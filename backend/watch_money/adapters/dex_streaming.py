"""Native public DEX WebSocket patches; the collector owns retries and REST fallback."""
from __future__ import annotations

import asyncio
import json
import os
import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from watch_money.adapters.dex import DydxAdapter, HyperliquidAdapter, number, timestamp
from watch_money.adapters.streaming import StreamUnavailable
from watch_money.models import Instrument, MarketSnapshot, now_ms

ENDPOINTS = {
    "hyperliquid": "wss://api.hyperliquid.xyz/ws",
    "dydx": "wss://indexer.dydx.trade/v4/ws",
}


def _connection_factory(endpoint: str):
    """Use inherited proxies and verified TLS; no account credentials or alternate hosts."""
    from websockets.asyncio.client import connect

    ca_file = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    proxy = (os.environ.get("WSS_PROXY") or os.environ.get("wss_proxy")
             or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
             or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or True)
    return connect(endpoint, ssl=ssl.create_default_context(cafile=ca_file), proxy=proxy,
                   open_timeout=15, close_timeout=5, ping_interval=20, ping_timeout=10,
                   max_size=2 * 1024 * 1024, max_queue=16)


def _patch(instrument: Instrument, channel: str, row: dict, mappings: tuple,
           *, source_at: int | None = None, funding_kind: str = "current") -> MarketSnapshot | None:
    values, sources, times = {}, {}, {}
    source = f"{ENDPOINTS[instrument.venue]}#{channel}:{instrument.native_symbol}"
    for field, native, mode in mappings:
        value = number(row.get(native), positive=mode == "positive", nonnegative=mode == "nonnegative")
        if value is None:
            continue
        values[field] = value
        sources[field] = f"{source}.{native}"
        times[field] = source_at
    if not values:
        return None
    if "volume" in values:
        values["volume_unit"] = instrument.quote
    if "open_interest" in values:
        values["oi_unit"] = instrument.base
    if "funding_rate" in values:
        values.update(funding_kind=funding_kind, funding_interval_hours=1)
    crossed = "bid" in values and "ask" in values and values["bid"] > values["ask"]
    issues = ["source_event_time_unavailable; receipt time is not source time"] if source_at is None else []
    if crossed:
        issues.append("crossed orderbook: bid exceeds ask")
    received = now_ms()
    return MarketSnapshot(instrument_id=instrument.id, venue=instrument.venue, source=source,
                          source_at=source_at, received_at=received, field_sources=sources,
                          field_timestamps=times, field_received_at={field: received for field in times},
                          valid=not crossed, issues=issues, is_demo=False, **values)


def hyperliquid_patch(instrument: Instrument, message: dict) -> MarketSnapshot | None:
    """BBO has block time; asset context has no documented source timestamp."""
    row = message.get("data")
    if not isinstance(row, dict) or row.get("coin") != instrument.native_symbol:
        return None
    channel = message.get("channel")
    if channel == "bbo":
        levels = row.get("bbo")
        if not isinstance(levels, list) or len(levels) != 2:
            raise ValueError("hyperliquid: malformed bbo")
        prices = {field: level.get("px") for field, level in zip(("bid", "ask"), levels, strict=True)
                  if isinstance(level, dict) and number(level.get("sz"), positive=True) is not None}
        # This protocol defines milliseconds. Reject apparent seconds instead of guessing.
        event_time = number(row.get("time"), positive=True)
        source_at = int(event_time) if event_time is not None and event_time >= 100_000_000_000 else None
        return _patch(instrument, channel, prices,
                      (("bid", "bid", "positive"), ("ask", "ask", "positive")), source_at=source_at)
    if channel == "activeAssetCtx":
        ctx = row.get("ctx")
        if not isinstance(ctx, dict):
            raise ValueError("hyperliquid: malformed activeAssetCtx")
        return _patch(instrument, channel, ctx, (
            ("mark", "markPx", "positive"), ("index", "oraclePx", "positive"),
            ("volume", "dayNtlVlm", "nonnegative"), ("open_interest", "openInterest", "nonnegative"),
            ("funding_rate", "funding", "finite"),
        ))
    return None


def dydx_patches(instruments: dict[str, Instrument], message: dict) -> list[MarketSnapshot]:
    """Parse only supplied market deltas, never carry cached fields into a new event."""
    contents = message.get("contents")
    if not isinstance(contents, dict):
        raise TypeError("dydx: malformed markets contents")
    patches = []
    initial = message.get("type") == "subscribed"
    for category in (("markets",) if initial else ("trading", "oraclePrices")):
        rows = contents.get(category, {})
        if not isinstance(rows, dict):
            raise TypeError(f"dydx: malformed {category}")
        for symbol, row in rows.items():
            item = instruments.get(symbol)
            if item is None:
                continue
            if not isinstance(row, dict):
                raise TypeError("dydx: malformed market row")
            if category == "oraclePrices":
                # effectiveAt belongs to the oracle price, never to trading/funding fields.
                effective = row.get("effectiveAt")
                event_time = timestamp(effective) if isinstance(effective, str) and "T" in effective else None
                patch = _patch(item, "v4_markets.oraclePrices", row,
                               (("index", "oraclePrice", "positive"),), source_at=event_time)
            else:
                mappings = (("volume", "volume24H", "nonnegative"),
                            ("open_interest", "openInterest", "nonnegative"),
                            ("funding_rate", "nextFundingRate", "finite"))
                if initial:
                    mappings += (("index", "oraclePrice", "positive"),)
                patch = _patch(item, f"v4_markets.{category}", row, mappings, funding_kind="predicted")
            if patch is not None:
                patches.append(patch)
    return patches


class DexStreamAdapter:
    """A cancellable source session; restart with discovered instruments after failure.

    connection_factory(endpoint) is an async context manager yielding a socket with
    send(str), recv(), close(). websockets handles control ping/pong automatically.
    on_payload is an optional async diagnostic hook; its failures never break data.
    """

    def __init__(self, rest_adapter: HyperliquidAdapter | DydxAdapter, *,
                 connection_factory: Callable | None = None, heartbeat_seconds: float = 20,
                 heartbeat_timeout: float = 10, handshake_timeout: float = 15):
        if not isinstance(rest_adapter, (HyperliquidAdapter, DydxAdapter)):
            raise TypeError("Unsupported native DEX stream adapter")
        if min(heartbeat_seconds, heartbeat_timeout, handshake_timeout) <= 0:
            raise ValueError("Stream timeouts must be positive")
        self.venue = rest_adapter.venue
        self.rest_adapter = rest_adapter
        self.unsupported: list[str] = []
        self.on_payload: Callable | None = None
        self._factory = connection_factory or _connection_factory
        self._heartbeat_seconds = heartbeat_seconds
        self._heartbeat_timeout = heartbeat_timeout
        self._handshake_timeout = handshake_timeout
        self._running = False
        self._socket: Any = None
        self._tasks: list[asyncio.Task] = []

    async def _receive(self, socket) -> dict:
        raw = await socket.recv()
        # The official HL SDK also accepts this initial non-JSON greeting.
        if raw == "Websocket connection established." and self.venue == "hyperliquid":
            return {"channel": "connected"}
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise TypeError(f"{self.venue}: expected WebSocket object")
        if self.on_payload is not None:
            with suppress(Exception):
                await self.on_payload(self.venue, f"websocket:{self.venue}", message)
        return message

    async def _heartbeat(self, socket, pong: asyncio.Event):
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            pong.clear()
            await socket.send(json.dumps({"method": "ping"}))
            await asyncio.wait_for(pong.wait(), self._heartbeat_timeout)

    def _selection(self, instruments: list[Instrument]) -> dict[str, Instrument]:
        selected = {}
        self.unsupported = []
        for item in instruments:
            if (item.venue != self.venue or item.kind != "perpetual"
                    or item.id != f"{self.venue}:perpetual:{item.native_symbol}"):
                raise ValueError("DEX stream requires a discovered instrument of its own venue")
            if not item.active:
                self.unsupported.append(f"{item.id}:inactive")
                continue
            if item.native_symbol in selected and selected[item.native_symbol] != item:
                raise ValueError("Conflicting native market identities")
            selected[item.native_symbol] = item
        if not selected:
            raise StreamUnavailable("No active public DEX subscriptions; use REST")
        # One connection, two topics per HL market. Leave ample IP-wide budget
        # for other applications; never silently truncate selected instruments.
        if self.venue == "hyperliquid" and len(selected) > 100:
            raise StreamUnavailable("Hyperliquid local limit is 100 markets / 200 topics; use REST")
        return selected

    async def stream(self, instruments: list[Instrument]) -> AsyncIterator[MarketSnapshot]:
        if self._running:
            raise RuntimeError("Cancel and close current DEX stream before replacing subscriptions")
        selected = self._selection(instruments)
        self._running = True
        # Deduplication is session-local. A fresh subscription can emit its initial snapshot.
        previous: dict[tuple, tuple] = {}
        last_times: dict[tuple, int] = {}
        try:
            async with self._factory(ENDPOINTS[self.venue]) as socket:
                self._socket = socket
                pong = asyncio.Event()
                heartbeat = None
                connection_id, last_sequence, subscribed = None, -1, False
                if self.venue == "hyperliquid":
                    for symbol in selected:
                        for channel in ("bbo", "activeAssetCtx"):
                            await socket.send(json.dumps({"method": "subscribe",
                                                          "subscription": {"type": channel, "coin": symbol}}))
                    heartbeat = asyncio.create_task(self._heartbeat(socket, pong))
                    self._tasks.append(heartbeat)
                else:
                    message = await asyncio.wait_for(self._receive(socket), self._handshake_timeout)
                    if message.get("type") != "connected":
                        raise ConnectionError("dydx: expected connected handshake")
                    connection_id = message.get("connection_id")
                    await socket.send(json.dumps({"type": "subscribe", "channel": "v4_markets", "batched": False}))
                while True:
                    receive = asyncio.create_task(self._receive(socket))
                    self._tasks.append(receive)
                    if heartbeat is not None:
                        done, _ = await asyncio.wait((receive, heartbeat), return_when=asyncio.FIRST_COMPLETED)
                        if heartbeat in done:
                            heartbeat.result()
                    if self.venue == "dydx" and not subscribed:
                        message = await asyncio.wait_for(receive, self._handshake_timeout)
                    else:
                        message = await receive
                    self._tasks.remove(receive)
                    if message.get("type") == "error" or message.get("channel") == "error":
                        raise ConnectionError(f"{self.venue}: source rejected public subscription")
                    patches = []
                    if self.venue == "hyperliquid":
                        if message.get("channel") == "pong":
                            pong.set()
                            continue
                        data = message.get("data")
                        item = selected.get(data.get("coin")) if isinstance(data, dict) else None
                        if item is not None:
                            patch = hyperliquid_patch(item, message)
                            patches = [patch] if patch is not None else []
                    elif message.get("channel") == "v4_markets":
                        kind = message.get("type")
                        if kind == "unsubscribed":
                            raise ConnectionError("dydx: public markets subscription ended")
                        if kind not in ("subscribed", "channel_data"):
                            continue
                        if kind == "channel_data" and not subscribed:
                            raise ConnectionError("dydx: update received before initial snapshot")
                        if message.get("connection_id", connection_id) != connection_id:
                            raise ConnectionError("dydx: connection identity changed")
                        sequence = message.get("message_id")
                        if isinstance(sequence, int) and not isinstance(sequence, bool):
                            if sequence <= last_sequence:
                                continue
                            last_sequence = sequence
                        subscribed = True
                        patches = dydx_patches(selected, message)
                    for patch in patches:
                        key = (patch.instrument_id, patch.source)
                        if patch.source_at is not None and patch.source_at < last_times.get(key, 0):
                            continue
                        fingerprint = tuple((field, getattr(patch, field), patch.field_timestamps[field])
                                            for field in sorted(patch.field_sources))
                        if previous.get(key) == fingerprint:
                            continue
                        previous[key] = fingerprint
                        # The collector rejects future timestamps. They must not
                        # poison the session watermark and suppress later valid data.
                        if patch.source_at is not None and patch.source_at <= now_ms() + 30_000:
                            last_times[key] = patch.source_at
                        yield patch
        finally:
            await self.close()
            self._running = False

    async def close(self):
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        socket, self._socket = self._socket, None
        if socket is not None:
            with suppress(Exception):
                await socket.close()


def create_dex_stream(rest_adapter: Any) -> DexStreamAdapter | None:
    if not isinstance(rest_adapter, (HyperliquidAdapter, DydxAdapter)):
        return None
    try:
        from websockets.asyncio.client import connect  # noqa: F401
    except ImportError:
        return None
    return DexStreamAdapter(rest_adapter)
