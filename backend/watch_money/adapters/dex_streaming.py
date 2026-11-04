"""Native public DEX WebSocket patches; the collector owns retries and REST fallback."""
from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from watch_money.adapters.dex import DydxAdapter, HyperliquidAdapter, LighterAdapter, number, timestamp
from watch_money.adapters.streaming import StreamUnavailable
from watch_money.models import Instrument, MarketSnapshot, now_ms

ENDPOINTS = {
    "hyperliquid": "wss://api.hyperliquid.xyz/ws",
    "dydx": "wss://indexer.dydx.trade/v4/ws",
    "lighter": "wss://mainnet.zklighter.elliot.ai/stream",
}


class ConnectionOpenTimeout(TimeoutError):
    pass


class SubscriptionSendTimeout(TimeoutError):
    pass


class HeartbeatSendTimeout(TimeoutError):
    pass


class ApplicationPongTimeout(TimeoutError):
    pass


class ConnectedHandshakeTimeout(TimeoutError):
    pass


class InitialSnapshotTimeout(TimeoutError):
    pass


class SourceReadTimeout(TimeoutError):
    pass


class _HyperliquidInbox:
    """Bounded snapshot backlog; compact only on pressure, never block pong reads."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.pending = deque()
        self.changed = asyncio.Event()
        self.error = None
        self.coalesced = 0

    @staticmethod
    def key(message):
        data = message["data"]
        # Context fields may be sparse; never overwrite a different field set.
        shape = tuple(sorted(data.get("ctx", {}))) if message["channel"] == "activeAssetCtx" else ()
        return message["channel"], data["coin"], shape

    @staticmethod
    def newer(previous, current):
        old_message, old_received = previous
        new_message, new_received = current
        if new_message["channel"] == "activeAssetCtx":
            return current  # Untimed contexts remain untimed, including after coalescing.
        old, new = number(old_message["data"].get("time")), number(new_message["data"].get("time"))

        def trustworthy(value, received):
            return value is not None and 100_000_000_000 <= value <= received

        if trustworthy(old, old_received) and (not trustworthy(new, new_received) or new < old):
            return previous
        return current

    def put(self, message, received_at=None):
        entry = (message, now_ms() if received_at is None else received_at)
        if len(self.pending) >= self.capacity:
            latest = {}
            for pending in (*self.pending, entry):
                key = self.key(pending[0])
                latest[key] = self.newer(latest[key], pending) if key in latest else pending
            if len(latest) > self.capacity:
                raise StreamUnavailable("hyperliquid: snapshot_buffer capacity exceeded; field shapes changed")
            self.coalesced += len(self.pending) + 1 - len(latest)
            self.pending = deque(latest.values())
        else:
            self.pending.append(entry)
        self.changed.set()

    async def get(self):
        while not self.pending:
            if self.error is not None:
                raise self.error
            self.changed.clear()
            await self.changed.wait()
        if self.error is not None:
            raise self.error
        return self.pending.popleft()

    def fail(self, error):
        self.error = error
        self.changed.set()


class _SubscriptionWindow:
    """Persistent conservative batch budget, sealed after the last possible send."""

    def __init__(self, *, limit=1500, seconds=60, clock=time.monotonic, wait=asyncio.sleep):
        self.limit, self.seconds, self.clock, self.wait = limit, seconds, clock, wait
        self.entries: list[dict] = []
        self.changed = asyncio.Event()
        self.lock = asyncio.Lock()

    async def reserve(self, count: int) -> dict:
        if count > self.limit:
            raise StreamUnavailable("Subscription batch exceeds the persistent message budget")
        while True:
            async with self.lock:
                now = self.clock()
                self.entries = [entry for entry in self.entries
                                if entry["at"] is None or entry["at"] + self.seconds > now]
                if sum(entry["count"] for entry in self.entries) + count <= self.limit:
                    token = {"count": count, "at": None}
                    self.entries.append(token)
                    return token
                completed = [entry["at"] for entry in self.entries if entry["at"] is not None]
                delay = max(0, min(completed) + self.seconds - now) if completed else None
                changed = self.changed
                changed.clear()
            if delay is None:
                await changed.wait()
            else:
                await self.wait(delay)

    def seal(self, token: dict):
        if token["at"] is None:
            token["at"] = self.clock()
            self.changed.set()


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
           *, source_at: int | None = None, funding_kind: str = "current",
           protocol_metrics: dict | None = None, cleared_fields: tuple = (),
           cleared_protocol_metrics: tuple = (), received_at: int | None = None) -> MarketSnapshot | None:
    values, sources, times = {}, {}, {}
    source = f"{ENDPOINTS[instrument.venue]}#{channel}:{instrument.native_symbol}"
    for field, native, mode in mappings:
        value = number(row.get(native), positive=mode == "positive", nonnegative=mode == "nonnegative")
        if value is None:
            continue
        values[field] = value
        sources[field] = f"{source}.{native}"
        times[field] = source_at
    for field in cleared_fields:
        values[field] = None
        sources[field] = f"{source}.{field}:explicit_unavailable"
        times[field] = source_at
    if not values and not protocol_metrics and not cleared_protocol_metrics:
        return None
    if "volume" in values:
        values["volume_unit"] = instrument.quote
    if "open_interest" in values:
        values["oi_unit"] = instrument.base
    if "funding_rate" in values:
        values.update(funding_kind=funding_kind, funding_interval_hours=1)
        for field in ("funding_kind", "funding_interval_hours"):
            sources[field] = f"{source}:{field}:documented_hourly_perpetual"
            times[field] = source_at
    crossed = values.get("bid") is not None and values.get("ask") is not None and values["bid"] > values["ask"]
    issues = ["source_event_time_unavailable; receipt time is not source time"] if source_at is None else []
    if crossed:
        issues.append("crossed orderbook: bid exceeds ask")
    received = now_ms() if received_at is None else received_at
    return MarketSnapshot(instrument_id=instrument.id, venue=instrument.venue, source=source,
                          source_at=source_at, received_at=received, field_sources=sources,
                          field_timestamps=times, field_received_at={field: received for field in times},
                          valid=not crossed, issues=issues, is_demo=False, cleared_fields=list(cleared_fields),
                          cleared_protocol_metrics=list(cleared_protocol_metrics),
                          **({"protocol_metrics": protocol_metrics} if protocol_metrics else {}), **values)


def hyperliquid_patch(instrument: Instrument, message: dict, *, received_at: int | None = None) -> MarketSnapshot | None:
    """BBO has block time; asset context has no documented source timestamp."""
    row = message.get("data")
    if not isinstance(row, dict) or row.get("coin") != instrument.native_symbol:
        return None
    channel = message.get("channel")
    if channel in ("bbo", "l2Book"):
        levels = row.get("bbo") if channel == "bbo" else row.get("levels")
        if not isinstance(levels, list) or len(levels) != 2:
            raise ValueError(f"hyperliquid: malformed {channel}")
        if channel == "l2Book":
            if any(not isinstance(side, list) for side in levels):
                raise TypeError("hyperliquid: malformed book sides")
            # Native l2Book messages are complete snapshots, never book deltas.
            levels = [next((level for level in side if isinstance(level, dict)
                            and number(level.get("px"), positive=True) is not None
                            and number(level.get("sz"), positive=True) is not None), None) for side in levels]
        prices = {field: level.get("px") for field, level in zip(("bid", "ask"), levels, strict=True)
                  if isinstance(level, dict) and number(level.get("sz"), positive=True) is not None
                  and number(level.get("px"), positive=True) is not None}
        # This protocol defines milliseconds. Reject apparent seconds instead of guessing.
        event_time = number(row.get("time"), positive=True)
        source_at = int(event_time) if event_time is not None and event_time >= 100_000_000_000 else None
        return _patch(instrument, channel, prices,
                      (("bid", "bid", "positive"), ("ask", "ask", "positive")), source_at=source_at,
                      cleared_fields=tuple(field for field in ("bid", "ask") if field not in prices), received_at=received_at)
    if channel == "activeAssetCtx":
        ctx = row.get("ctx")
        if not isinstance(ctx, dict):
            raise ValueError("hyperliquid: malformed activeAssetCtx")
        return _patch(instrument, channel, ctx, (
            ("mark", "markPx", "positive"), ("index", "oraclePx", "positive"),
            ("volume", "dayNtlVlm", "nonnegative"), ("open_interest", "openInterest", "nonnegative"),
            ("funding_rate", "funding", "finite"),
        ), received_at=received_at)
    return None


def lighter_patches(instruments: dict[str, Instrument], message: dict) -> list[MarketSnapshot]:
    """All-market publication snapshot; unverified funding units remain raw metrics."""
    if message.get("type") not in ("subscribed/market_stats", "update/market_stats"):
        return []
    channel = message.get("channel", "")
    if not isinstance(channel, str) or not channel.startswith("market_stats:"):
        return []
    payload = message.get("market_stats")
    if not isinstance(payload, dict):
        raise TypeError("lighter: malformed market_stats")
    rows = payload if channel == "market_stats:all" else {channel.split(":", 1)[1]: payload}
    event = number(message.get("timestamp"), positive=True)
    source_at = int(event) if event is not None and 100_000_000_000 <= event < 10_000_000_000_000 else None
    received = now_ms()
    result = []
    for market_id, row in rows.items():
        item = instruments.get(str(market_id))
        if item is None:
            continue
        if not isinstance(row, dict) or str(row.get("market_id", market_id)) != item.native_symbol:
            raise ValueError("lighter: market_stats identity mismatch")
        metrics = {}
        metric_clears = []
        for key, label in (("current_funding_rate", "raw_upcoming_funding_rate"),
                           ("funding_rate", "raw_last_settled_funding_rate"),
                           ("funding_timestamp", "raw_last_settled_funding_timestamp"),
                           ("open_interest", "raw_open_interest")):
            if number(row.get(key)) is not None:
                metrics[label] = str(row[key])
            elif key in row:
                metric_clears.append(label)
        if any(key.startswith("raw_") and "funding" in key for key in metrics):
            metrics.update(funding_unit="unverified", funding_rate_basis="unverified_not_normalized",
                           funding_observed_at_ms=str(received),
                           funding_source_publication_at_ms=str(source_at) if source_at is not None else None)
        if "funding_timestamp" in row:
            settled = number(row.get("funding_timestamp"), positive=True)
            if settled is not None and 100_000_000_000 <= settled < 10_000_000_000_000:
                metrics["last_settled_funding_at_ms"] = str(int(settled))
            else:
                metric_clears.append("last_settled_funding_at_ms")
        if "raw_open_interest" in metrics:
            metrics["open_interest_unit"] = "unverified"
        patch = _patch(item, "market_stats", row, (
            ("bid", "best_bid_price", "positive"), ("ask", "best_ask_price", "positive"),
            ("mark", "mark_price", "positive"), ("index", "index_price", "positive"),
            ("last", "last_trade_price", "positive"), ("volume", "daily_quote_token_volume", "nonnegative"),
        ), source_at=source_at, protocol_metrics=metrics, cleared_protocol_metrics=tuple(metric_clears),
            cleared_fields=tuple(field for field, native in (("bid", "best_bid_price"), ("ask", "best_ask_price"))
                                 if native in row and number(row[native], positive=True) is None))
        if patch is not None:
            patch.issues.append("Source timestamps describe upstream market_stats publication, not individual trade time")
            if "funding_unit" in metrics:
                patch.issues.append("Lighter WebSocket funding unit/basis unverified; raw metrics are not normalized rates")
            result.append(patch)
    return result


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

    def __init__(self, rest_adapter: HyperliquidAdapter | DydxAdapter | LighterAdapter, *,
                 connection_factory: Callable | None = None, heartbeat_seconds: float = 20,
                 heartbeat_timeout: float = 10, handshake_timeout: float = 15,
                 hyperliquid_book_channel: str = "bbo"):
        if not isinstance(rest_adapter, (HyperliquidAdapter, DydxAdapter, LighterAdapter)):
            raise TypeError("Unsupported native DEX stream adapter")
        if hyperliquid_book_channel not in {"bbo", "l2Book"}:
            raise ValueError("Unsupported Hyperliquid public book channel")
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
        self._book_channel = hyperliquid_book_channel
        self.subscription_count = 0
        self._running = False
        self._socket: Any = None
        self._tasks: list[asyncio.Task] = []
        self._subscriptions_sent = asyncio.Event()
        self._inbox: _HyperliquidInbox | None = None
        self._payload_observer_dropped = 0
        self._observer_queue: asyncio.Queue | None = None

    async def _bounded(self, awaitable, seconds, stage):
        try:
            return await asyncio.wait_for(awaitable, seconds)
        except TimeoutError as exc:
            error_class = {"subscription_send": SubscriptionSendTimeout, "heartbeat_send": HeartbeatSendTimeout,
                           "application_pong": ApplicationPongTimeout, "connected_handshake": ConnectedHandshakeTimeout,
                           "initial_snapshot": InitialSnapshotTimeout}[stage]
            raise error_class(f"{self.venue}: {stage} timed out after {seconds}s") from exc

    async def _observe_payloads(self):
        while True:
            payload = await self._observer_queue.get()
            try:
                with suppress(Exception):
                    await self.on_payload(self.venue, f"websocket:{self.venue}", payload)
            except asyncio.CancelledError:
                # The callback may already have written before cancellation;
                # conservatively report a potentially skipped diagnostic frame.
                self._payload_observer_dropped += 1
                raise

    @property
    def payload_observer_dropped(self):
        """Skipped/potentially skipped callbacks, including queue overflow and close."""
        return self._payload_observer_dropped

    async def _read_hyperliquid(self, socket, selected, pong, inbox):
        try:
            frames = 0
            while True:
                try:
                    message = await self._receive(socket, observe=False)
                except TimeoutError as exc:
                    raise SourceReadTimeout("hyperliquid: source_read timed out") from exc
                received_at = now_ms()
                if self._observer_queue is not None:
                    try:
                        self._observer_queue.put_nowait(message)
                    except asyncio.QueueFull:
                        self._payload_observer_dropped += 1
                channel = message.get("channel")
                if channel == "pong":
                    pong.set()
                elif channel == "error" or message.get("type") == "error":
                    raise ConnectionError("hyperliquid: source rejected public subscription")
                elif channel in {"bbo", "l2Book", "activeAssetCtx"}:
                    data = message.get("data")
                    if isinstance(data, dict) and data.get("coin") in selected:
                        inbox.put(message, received_at)
                frames += 1
                if frames % 64 == 0:
                    # A full library receive buffer may resolve recv immediately;
                    # still let heartbeat and downstream tasks make progress.
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - propagate reader failure to the owning consumer
            inbox.fail(exc)

    @property
    def catalog_stream(self):
        return self.venue in {"hyperliquid", "lighter"}

    @property
    def max_catalog_markets(self):
        # Lighter uses one all-market topic, not one subscription per market.
        return 5000 if self.venue == "lighter" else 100 if self.venue == "hyperliquid" else None

    async def _receive(self, socket, *, observe=True) -> dict:
        raw = await socket.recv()
        # The official HL SDK also accepts this initial non-JSON greeting.
        if raw == "Websocket connection established." and self.venue == "hyperliquid":
            return {"channel": "connected"}
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise TypeError(f"{self.venue}: expected WebSocket object")
        if observe and self.on_payload is not None:
            with suppress(Exception):
                await self.on_payload(self.venue, f"websocket:{self.venue}", message)
        return message

    async def _heartbeat(self, socket, pong: asyncio.Event):
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            pong.clear()
            await self._bounded(socket.send(json.dumps({"method": "ping"})), self._heartbeat_timeout, "heartbeat_send")
            await self._bounded(pong.wait(), self._heartbeat_timeout, "application_pong")

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
        limit = self.max_catalog_markets
        if limit is not None and len(selected) > limit:
            raise StreamUnavailable(f"{self.venue} local limit is {limit} markets; use REST")
        return selected

    async def stream(self, instruments: list[Instrument]) -> AsyncIterator[MarketSnapshot]:
        if self._running:
            raise RuntimeError("Cancel and close current DEX stream before replacing subscriptions")
        selected = self._selection(instruments)
        self._running = True
        self._subscriptions_sent.clear()
        # Deduplication is session-local. A fresh subscription can emit its initial snapshot.
        previous: dict[tuple, tuple] = {}
        last_times: dict[tuple, int] = {}
        opened = False
        try:
            async with self._factory(ENDPOINTS[self.venue]) as socket:
                opened = True
                self._socket = socket
                pong = asyncio.Event()
                heartbeat = None
                connection_id, last_sequence, subscribed = None, -1, False
                if self.venue == "hyperliquid":
                    self._inbox = _HyperliquidInbox(max(16, len(selected) * 4))
                    self._observer_queue = None
                    if self.on_payload is not None:
                        self._observer_queue = asyncio.Queue(maxsize=64)
                        self._tasks.append(asyncio.create_task(self._observe_payloads()))
                    # Drain source frames before the first subscription send. A
                    # slow downstream consumer must never hold application pongs.
                    self._tasks.append(asyncio.create_task(self._read_hyperliquid(socket, selected, pong, self._inbox)))
                    for symbol in selected:
                        for channel in (self._book_channel, "activeAssetCtx"):
                            options = {"fast": True} if channel == "l2Book" else {}
                            await self._bounded(socket.send(json.dumps({"method": "subscribe",
                                                          "subscription": {"type": channel, "coin": symbol, **options}})),
                                                self._handshake_timeout, "subscription_send")
                    self.subscription_count = len(selected) * 2
                    heartbeat = asyncio.create_task(self._heartbeat(socket, pong))
                    self._tasks.append(heartbeat)
                elif self.venue == "lighter":
                    message = await self._bounded(self._receive(socket), self._handshake_timeout, "connected_handshake")
                    if message.get("type") != "connected":
                        raise ConnectionError("lighter: expected connected handshake")
                    await socket.send(json.dumps({"type": "subscribe", "channel": "market_stats/all"}))
                    self.subscription_count = 1
                else:
                    message = await self._bounded(self._receive(socket), self._handshake_timeout, "connected_handshake")
                    if message.get("type") != "connected":
                        raise ConnectionError("dydx: expected connected handshake")
                    connection_id = message.get("connection_id")
                    await socket.send(json.dumps({"type": "subscribe", "channel": "v4_markets", "batched": False}))
                    self.subscription_count = 1
                self._subscriptions_sent.set()
                while True:
                    receive = asyncio.create_task(self._inbox.get() if self.venue == "hyperliquid" else self._receive(socket))
                    self._tasks.append(receive)
                    if heartbeat is not None:
                        done, _ = await asyncio.wait((receive, heartbeat), return_when=asyncio.FIRST_COMPLETED)
                        if heartbeat in done:
                            heartbeat.result()
                    received_at = None
                    if self.venue in {"dydx", "lighter"} and not subscribed:
                        message = await self._bounded(receive, self._handshake_timeout, "initial_snapshot")
                    else:
                        message = await receive
                        if self.venue == "hyperliquid":
                            message, received_at = message
                    self._tasks.remove(receive)
                    if message.get("type") == "error" or message.get("channel") == "error":
                        raise ConnectionError(f"{self.venue}: source rejected public subscription")
                    if self.venue == "lighter":
                        if message.get("type") == "ping":
                            await socket.send(json.dumps({"type": "pong"}))
                            continue
                        if message.get("type") == "shutdown":
                            raise ConnectionError("lighter: source announced shutdown; reconnect required")
                    patches = []
                    if self.venue == "hyperliquid":
                        if message.get("channel") == "pong":
                            pong.set()
                            continue
                        data = message.get("data")
                        item = selected.get(data.get("coin")) if isinstance(data, dict) else None
                        if item is not None:
                            patch = hyperliquid_patch(item, message, received_at=received_at)
                            patches = [patch] if patch is not None else []
                    elif self.venue == "lighter":
                        patches = lighter_patches(selected, message)
                        if message.get("type") in {"subscribed/market_stats", "update/market_stats"}:
                            subscribed = True
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
                        if patch.protocol_metrics:
                            metrics = {name: value for name, value in patch.protocol_metrics.items()
                                       if not name.endswith("observed_at_ms")}
                            fingerprint += (("protocol_metrics", patch.source_at, json.dumps(metrics, sort_keys=True)),)
                        if patch.cleared_protocol_metrics:
                            fingerprint += (("cleared_protocol_metrics", tuple(sorted(patch.cleared_protocol_metrics)), patch.source_at),)
                        if previous.get(key) == fingerprint:
                            continue
                        previous[key] = fingerprint
                        # The collector rejects future timestamps. They must not
                        # poison the session watermark and suppress later valid data.
                        if patch.source_at is not None and patch.source_at <= min(now_ms(), patch.received_at):
                            last_times[key] = patch.source_at
                        yield patch
        except TimeoutError as exc:
            if not opened:
                raise ConnectionOpenTimeout(f"{self.venue}: connection_open timed out") from exc
            raise
        finally:
            await self.close()
            self._running = False

    async def close(self):
        observer_queue, self._observer_queue = self._observer_queue, None
        if observer_queue is not None:
            self._payload_observer_dropped += observer_queue.qsize()
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
    if not isinstance(rest_adapter, (HyperliquidAdapter, DydxAdapter, LighterAdapter)):
        return None
    try:
        from websockets.asyncio.client import connect  # noqa: F401
    except ImportError:
        return None
    return HyperliquidCatalogStream(rest_adapter) if isinstance(rest_adapter, HyperliquidAdapter) else DexStreamAdapter(rest_adapter)


class HyperliquidCatalogStream(DexStreamAdapter):
    """At most five 100-market sessions and 1000 topics across the whole wrapper."""

    def __init__(self, rest_adapter, *, subscription_clock=time.monotonic,
                 subscription_wait=asyncio.sleep, subscription_limit=1500, **kwargs):
        super().__init__(rest_adapter, **kwargs)
        self._streams: list[DexStreamAdapter] = []
        self._subscription_window = _SubscriptionWindow(limit=subscription_limit,
                                                       clock=subscription_clock, wait=subscription_wait)

    @property
    def max_catalog_markets(self):
        return 500

    @property
    def payload_observer_dropped(self):
        return self._payload_observer_dropped + sum(child.payload_observer_dropped for child in self._streams)

    async def stream(self, instruments: list[Instrument]) -> AsyncIterator[MarketSnapshot]:
        if self._running:
            raise RuntimeError("Cancel and close current DEX stream before replacing subscriptions")
        selected = list(self._selection(instruments).values())
        self._running = True
        events: asyncio.Queue = asyncio.Queue(maxsize=max(2, len(selected) * 2))
        token = None

        async def relay(stream, batch):
            try:
                async for patch in stream.stream(batch):
                    await events.put(patch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - relay every worker failure; never leave the consumer hanging
                await events.put(exc)

        try:
            # Reserve before opening sockets. The window persists across source
            # errors, close() and collector retries. Failed partial sends consume
            # their entire reservation, never granting accidental retry bursts.
            token = await self._subscription_window.reserve(len(selected) * 2)
            for offset in range(0, len(selected), 100):
                child = DexStreamAdapter(self.rest_adapter, connection_factory=self._factory,
                                         heartbeat_seconds=self._heartbeat_seconds, heartbeat_timeout=self._heartbeat_timeout,
                                         handshake_timeout=self._handshake_timeout, hyperliquid_book_channel="l2Book")
                child.on_payload = self.on_payload
                self._streams.append(child)
                self._tasks.append(asyncio.create_task(relay(child, selected[offset:offset + 100])))
            async def seal_after_subscriptions():
                await asyncio.gather(*(child._subscriptions_sent.wait() for child in self._streams))
                self._subscription_window.seal(token)

            self._tasks.append(asyncio.create_task(seal_after_subscriptions()))
            self.subscription_count = len(selected) * 2
            while True:
                event = await events.get()
                if isinstance(event, BaseException):
                    raise event
                yield event
        finally:
            await self.close()
            if token is not None:
                self._subscription_window.seal(token)
            self._running = False

    async def close(self):
        await super().close()
        streams, self._streams = self._streams, []
        await asyncio.gather(*(stream.close() for stream in streams), return_exceptions=True)
        self._payload_observer_dropped += sum(stream.payload_observer_dropped for stream in streams)
