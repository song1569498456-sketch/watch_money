"""Public source WebSocket price patches alongside REST funding/OI collection."""
from __future__ import annotations

import asyncio
import os
import ssl
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from watch_money.adapters.cex import CexAdapter, _number, _timestamp
from watch_money.adapters.volume_units import volume_fields
from watch_money.models import Instrument, MarketSnapshot, now_ms


class StreamUnavailable(RuntimeError):
    """No requested public ticker subscription is supported; REST remains usable."""


def _stream_client_factory(client_id: str) -> Any:
    import ccxt.pro as ccxt

    actual = client_id if hasattr(ccxt, client_id) else {"gate": "gateio"}.get(client_id, client_id)
    ca_file = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    config: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": 15000,
        "aiohttp_trust_env": True,
        "ssl_context": ssl.create_default_context(cafile=ca_file),
        "newUpdates": True,
        "options": {"watchTickers": {"name": "ticker"}, "watchTicker": {"name": "ticker"}},
    }
    # aiohttp's WebSocket env lookup uses WSS_PROXY, not HTTPS_PROXY. Make the
    # inherited HTTPS proxy explicit for WSS while keeping TLS verification.
    proxy = (os.environ.get("WSS_PROXY") or os.environ.get("wss_proxy")
             or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
             or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))
    if proxy:
        config["wssProxy"] = proxy
    client = getattr(ccxt, actual)(config)
    client.has["fetchCurrencies"] = False
    return client


def ticker_patch(instrument: Instrument, client_id: str, ticker: dict, method: str) -> MarketSnapshot | None:
    """Emit supplied numeric fields only; absence is not a null replacement.

    A CCXT ticker timestamp describes its unified ticker event, not an independent
    exchange timestamp for every constituent price. Keep this uncertainty visible.
    """
    event_time = _timestamp(ticker.get("timestamp"))
    source = f"ccxt.pro:{client_id}:{method}:{instrument.native_symbol}"
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    timestamps: dict[str, int | None] = {}
    for field, key in (("bid", "bid"), ("ask", "ask"), ("last", "last"),
                       ("mark", "markPrice"), ("index", "indexPrice")):
        value = _number(ticker.get(key), positive=True)
        if value is not None:
            values[field] = value
            sources[field] = f"{source}.{key}:unified_ticker_event_time"
            timestamps[field] = event_time
    volume, unit, volume_source = volume_fields(client_id, instrument, ticker)
    if volume is not None:
        values["volume"] = volume
        values["volume_unit"] = unit
        sources["volume"] = f"{source}.{volume_source}:unified_ticker_event_time"
        timestamps["volume"] = event_time
    if not any(field in values for field in ("bid", "ask", "last", "mark", "index")):
        return None
    crossed = values.get("bid") is not None and values.get("ask") is not None and values["bid"] > values["ask"]
    issues = ["Source WebSocket timestamps describe unified ticker events; constituent field timing may differ"]
    if event_time is None:
        issues.append("Source WebSocket event time unavailable; receipt time is not source time")
    if crossed:
        issues.append("crossed orderbook: bid exceeds ask")
    return MarketSnapshot(
        instrument_id=instrument.id, venue=instrument.venue, source=source,
        source_at=event_time, received_at=now_ms(), field_sources=sources,
        field_timestamps=timestamps, valid=not crossed, issues=issues, is_demo=False, **values,
    )


class CexStreamAdapter:
    """One cancellable stream per logical venue; no internal retry loop.

    `stream` requires the REST adapter's completed discovery. The collector merges
    `patch.model_dump(exclude_unset=True)` with existing data, merging provenance
    dictionaries and retaining unrelated REST fields. Root owns retry/backoff and
    restarting this generator when the selected instruments change.
    """

    def __init__(self, rest_adapter: CexAdapter, *, client_factory: Callable[[str], Any] | None = None):
        self.venue = rest_adapter.venue
        self.rest_adapter = rest_adapter
        self._factory = client_factory or _stream_client_factory
        self._clients: dict[str, Any] = {}
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self.unsupported: list[str] = []
        self.on_payload = None

    def _client(self, client_id: str) -> Any:
        if client_id not in self._clients:
            client = self._factory(client_id)
            rest_client = self.rest_adapter._client(client_id)
            # Share the already-discovered native metadata, never refetch an
            # account/currency catalog while establishing public streams.
            client.set_markets_from_exchange(rest_client)
            self._clients[client_id] = client
        return self._clients[client_id]

    async def stream(self, instruments: list[Instrument]) -> AsyncIterator[MarketSnapshot]:
        if self._running:
            raise RuntimeError("Cancel and close the current stream before replacing subscriptions")
        self._running = True
        self.unsupported = []
        events: asyncio.Queue[MarketSnapshot | BaseException] = asyncio.Queue(maxsize=max(1, len(instruments) * 2))
        try:
            groups: dict[tuple, list[Instrument]] = defaultdict(list)
            seen = set()
            for instrument in instruments:
                if instrument.id in seen:
                    continue
                seen.add(instrument.id)
                client_id, _market = self.rest_adapter._resolve(instrument)
                group = (client_id, instrument.kind, instrument.settle, instrument.linear)
                groups[group].append(instrument)
            for group, selected in groups.items():
                client_id = group[0]
                client = self._client(client_id)
                if client.has.get("watchTickers") and not (client_id == "mexc" and group[1] == "spot"):
                    # Conservative request batches respect Bybit's small spot
                    # topic limit without claiming a venue-wide subscription cap.
                    for offset in range(0, len(selected), 10):
                        self._tasks.append(asyncio.create_task(self._watch(client_id, client,
                                                                           selected[offset:offset + 10], events,
                                                                           multiple=True)))
                elif client.has.get("watchTicker"):
                    for item in selected:
                        self._tasks.append(asyncio.create_task(self._watch(client_id, client, [item], events,
                                                                           multiple=False)))
                else:
                    self.unsupported.append(f"{client_id}:{group[1]}:{group[2]}:public ticker stream unavailable")
            if not self._tasks:
                raise StreamUnavailable("No public source ticker stream supported for requested markets; use REST")
            while True:
                event = await events.get()
                if isinstance(event, BaseException):
                    raise event
                yield event
        finally:
            await self.close()
            self._running = False

    async def _watch(self, client_id: str, client: Any, instruments: list[Instrument],
                     events: asyncio.Queue, *, multiple: bool) -> None:
        by_symbol = {item.native_symbol: item for item in instruments}
        symbols = list(by_symbol)
        method = "watch_tickers" if multiple else "watch_ticker"
        previous: dict[str, tuple] = {}
        last_source_times: dict[str, int] = {}
        try:
            while True:
                if multiple:
                    payloads = await client.watch_tickers(symbols)
                else:
                    payloads = {symbols[0]: await client.watch_ticker(symbols[0])}
                if self.on_payload is not None:
                    with suppress(Exception):
                        await self.on_payload(self.venue, f'websocket:ccxt:{method}', payloads)
                for symbol, ticker in payloads.items():
                    instrument = by_symbol.get(symbol)
                    if instrument is None or not isinstance(ticker, dict):
                        continue
                    patch = ticker_patch(instrument, client_id, ticker, method)
                    if patch is None:
                        continue
                    # Some implementations return cached entries together with
                    # updates. Do not emit a duplicate or move an event backwards.
                    fingerprint = tuple((key, getattr(patch, key)) for key in
                                        ("source_at", "bid", "ask", "last", "mark", "index", "volume", "volume_unit"))
                    prior = previous.get(symbol)
                    if prior == fingerprint:
                        continue
                    if patch.source_at is not None and patch.source_at < last_source_times.get(symbol, 0):
                        continue
                    if patch.source_at is not None and patch.source_at <= now_ms() + 30_000:
                        last_source_times[symbol] = patch.source_at
                    previous[symbol] = fingerprint
                    await events.put(patch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - relay worker failures to the generator's caller
            if type(exc).__name__ == "NotSupported" and multiple and client.has.get("watchTicker"):
                for instrument in instruments:
                    self._tasks.append(asyncio.create_task(self._watch(client_id, client, [instrument], events,
                                                                       multiple=False)))
                return
            if type(exc).__name__ == "NotSupported":
                self.unsupported.append(f"{client_id}:{instruments[0].kind}:public ticker stream unavailable")
                await events.put(StreamUnavailable("Requested public ticker method unavailable; use REST"))
                return
            await events.put(exc)

    async def close(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        clients, self._clients = self._clients, {}
        for client in clients.values():
            # Shutdown must not replace the original network/cancellation error.
            with suppress(Exception):
                await client.close()


def create_stream(rest_adapter: Any) -> CexStreamAdapter | None:
    if not isinstance(rest_adapter, CexAdapter):
        from .dex_streaming import create_dex_stream
        return create_dex_stream(rest_adapter)
    try:
        import ccxt.pro  # noqa: F401
    except ImportError:
        return None
    return CexStreamAdapter(rest_adapter)
