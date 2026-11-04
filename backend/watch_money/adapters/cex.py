"""Public, credential-free CEX adapters backed by CCXT's asynchronous clients.

No request is made at import/construction time. The collector owns retries and
backoff; optional funding/OI failures remain visible on individual snapshots.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import ssl
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from watch_money.adapters.volume_units import volume_fields
from watch_money.models import CoverageEntry, Instrument, MarketSnapshot, OrderBook, now_ms


@dataclass(frozen=True)
class VenueSpec:
    clients: tuple[str, ...]
    docs: str
    products: tuple[str, ...] = ("spot", "perpetual")
    note: str = "Global service; regional products are separate and are not silently substituted."


SPECS: dict[str, VenueSpec] = {
    "binance": VenueSpec(("binance", "binanceusdm", "binancecoinm"),
                         "https://developers.binance.com/docs/derivatives/Introduction"),
    "okx": VenueSpec(("okx",), "https://www.okx.com/docs-v5/en/"),
    "bybit": VenueSpec(("bybit",), "https://bybit-exchange.github.io/docs/v5/intro"),
    "bitget": VenueSpec(("bitget",), "https://www.bitget.com/api-doc/common/intro"),
    "gate": VenueSpec(("gate",), "https://www.gate.com/docs/developers/apiv4/en/"),
    "mexc": VenueSpec(("mexc",), "https://www.mexc.com/api-docs/spot-v3/introduction"),
    "kucoin": VenueSpec(("kucoin", "kucoinfutures"), "https://www.kucoin.com/docs-new"),
    "coinbase": VenueSpec(("coinbase",), "https://docs.cdp.coinbase.com/advanced-trade/docs/welcome",
                          ("spot",), "Advanced Trade public spot service. International/US derivatives "
                          "are separate services and are not claimed as supported."),
    "kraken": VenueSpec(("kraken", "krakenfutures"), "https://docs.kraken.com/api/"),
    "htx": VenueSpec(("htx",), "https://huobiapi.github.io/docs/spot/v1/en/"),
    "deribit": VenueSpec(("deribit",), "https://docs.deribit.com/", ("spot", "perpetual"),
                         "Deribit service; dated futures/options excluded. Funding is continuously "
                         "accrued: the 8h rate window is not a discrete settlement schedule."),
    "upbit": VenueSpec(("upbit",), "https://docs.upbit.com/", ("spot",),
                       "Korea API only; KRW/BTC/USDT quotes preserved. Singapore, Indonesia and "
                       "Thailand APIs are separate regional services, not substituted."),
}

# Deliberately small reviewed identity map. A unified ticker alone is insufficient:
# the exchange's underlying base identifier must independently match this map.
_BASE_IDS = {"BTC": {"BTC", "XBT", "XXBT"}, "ETH": {"ETH", "XETH"}, "SOL": {"SOL"}}


def _number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        return None
    return result


def _timestamp(value: Any) -> int | None:
    value = _number(value, positive=True)
    # CCXT unified timestamps are milliseconds. Reject seconds rather than guess.
    return int(value) if value is not None and value >= 100_000_000_000 else None


def _interval(value: Any) -> float | None:
    """Only accept an explicit duration with units, never an unlabelled number."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)\s*", value)
    if not match:
        return None
    return _number(float(match[1]) * {"ms": 1 / 3_600_000, "s": 1 / 3600,
                                    "m": 1 / 60, "h": 1, "d": 24}[match[2]], positive=True)


def funding_interval(client_id: str, market: dict, funding: dict) -> tuple[float | None, str | None]:
    """Use explicit native metadata before CCXT's documented unified interval."""
    extra = market.get("_funding_interval_metadata") or {}
    if isinstance(extra, dict):
        hours = _interval(extra.get("interval"))
        if hours is not None:
            return hours, "fetch_funding_intervals.interval"
    raw = funding.get("info") or {}
    metadata = market.get("info") or {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(metadata, dict):
        metadata = {}
    keys = {
        "binance": (("fundingIntervalHours", 1),),
        "binanceusdm": (("fundingIntervalHours", 1),),
        "binancecoinm": (("fundingIntervalHours", 1),),
        "bybit": (("fundingInterval", 1 / 60),),
        "bitget": (("fundingRateInterval", 1), ("ratePeriod", 1), ("fundInterval", 1)),
        "gate": (("funding_interval", 1 / 3600),),
        "mexc": (("collectCycle", 1),),
        "kucoinfutures": (("granularity", 1 / 3_600_000),
                           ("currentGranularity", 1 / 3_600_000),
                           ("fundingRateGranularity", 1 / 3_600_000)),
    }.get(client_id, ())
    for container, name in ((raw, "funding.info"), (metadata, "market.info")):
        for key, multiplier in keys:
            value = _number(container.get(key), positive=True)
            if value is not None:
                return value * multiplier, f"{name}.{key}"
    # These official fields are adjacent scheduled settlement times, not a
    # receipt timestamp and next settlement (which would produce a countdown).
    adjacent = {"okx": ("fundingTime", "nextFundingTime"),
                "htx": ("funding_time", "next_funding_time")}.get(client_id)
    if adjacent:
        a, b = (_timestamp(raw.get(key)) for key in adjacent)
        if a is not None and b is not None and b > a:
            return (b - a) / 3_600_000, f"funding.info.{adjacent[1]}-{adjacent[0]}"
    value = _interval(funding.get("interval"))
    return (value, "funding.interval") if value is not None else (None, None)


def _client_factory(client_id: str) -> Any:
    import ccxt.async_support as ccxt
    actual = client_id if hasattr(ccxt, client_id) else {"gate": "gateio"}.get(client_id, client_id)
    options: dict[str, Any] = {}
    if client_id == "binance":
        options = {"defaultType": "spot", "fetchMarkets": {"types": ["spot"]}}
    elif client_id in {"okx", "bitget", "gate"}:
        options = {"fetchMarkets": {"types": ["spot", "swap"]}}
    elif client_id == "bybit":
        options = {"fetchMarkets": {"types": ["spot", "linear", "inverse"]}}
    ca_file = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    client = getattr(ccxt, actual)({"enableRateLimit": True, "timeout": 15000, "options": options,
                                   "aiohttp_trust_env": True,
                                   "ssl_context": ssl.create_default_context(cafile=ca_file)})
    # Currency catalogs can hit account endpoints; market metadata is sufficient.
    client.has["fetchCurrencies"] = False
    return client


class CexAdapter:
    light_max_instruments = 100
    light_batch_size = 50

    def __init__(self, venue: str, *, client_factory: Callable[[str], Any] | None = None):
        self.venue = venue
        self.spec = SPECS[venue]
        self._factory = client_factory or _client_factory
        self._clients: dict[str, Any] = {}
        self._markets: dict[str, tuple[str, dict]] = {}
        self._instruments: dict[str, Instrument] = {}
        self._catalog_observed_at: dict[str, int] = {}
        self._semaphore = asyncio.Semaphore(4)
        self.light_unsupported: list[str] = []
        self.light_errors: dict[str, str] = {}
        self.funding_errors: dict[str, str] = {}
        self.funding_unsupported: list[str] = []
        self.fetch_errors: dict[str, str] = {}
        self.discovery_complete = False
        self.discovery_errors: dict[str, str] = {}
        self.on_payload: Callable[[str, str, Any], Awaitable[None]] | None = None

    async def _record_payload(self, method: str, payload: Any) -> None:
        # Diagnostic recording is optional and must not interrupt collection.
        if self.on_payload is not None:
            with suppress(Exception):
                await self.on_payload(self.venue, f"rest:ccxt:{method}", payload)

    def _client(self, client_id: str) -> Any:
        if client_id not in self._clients:
            self._clients[client_id] = self._factory(client_id)
        return self._clients[client_id]

    async def discover(self) -> list[Instrument]:
        # Sequential clients avoid simultaneous exchange-wide catalog bursts.
        discovered: dict[str, tuple[str, dict]] = {}
        instruments = []
        self.discovery_errors = {}
        self.discovery_complete = False
        successful_clients = 0
        first_error: Exception | None = None
        for client_id in self.spec.clients:
            try:
                client = self._client(client_id)
                markets = await client.load_markets(reload=True)
            except Exception as exc:
                self.discovery_errors[client_id] = type(exc).__name__
                first_error = first_error or exc
                # A failed service is neither a successful empty catalog nor
                # evidence of delisting. Keep its last known identities visible,
                # but do not claim their current availability is established.
                for identity, (previous_client, market) in self._markets.items():
                    if previous_client == client_id:
                        discovered[identity] = (previous_client, market)
                        if identity in self._instruments:
                            instruments.append(self._instruments[identity].model_copy(update={"active": False}))
                continue
            successful_clients += 1
            self._catalog_observed_at[client_id] = now_ms()
            for market in markets.values():
                market = {**market, "_catalog_observed_at": self._catalog_observed_at[client_id]}
                kind = "spot" if market.get("spot") else "perpetual" if market.get("swap") else None
                if kind is None or kind not in self.spec.products or market.get("active") is False:
                    continue
                if not all(market.get(key) for key in ("id", "symbol", "base", "quote")):
                    continue
                size = _number(market.get("contractSize"), positive=True) if kind == "perpetual" else 1.0
                # An unknown multiplier or inverse/linear type is unsafe for depth calculations.
                if size is None or (kind == "perpetual" and market.get("linear") is None
                                    and market.get("inverse") is None):
                    continue
                base = market["base"]
                base_id = str(market.get("baseId") or "").upper()
                verified = base in _BASE_IDS and base_id in _BASE_IDS[base]
                asset_id = base if verified else f"cex:{self.venue}:{client_id}:{base_id or base}"
                identity = f"cex:{self.venue}:{client_id}:{kind}:{quote(str(market['id']), safe='')}"
                precision = market.get("precision") or {}
                limits = (market.get("limits") or {}).get("cost") or {}
                # Deribit CCXT amounts (precision/minimum included) are native
                # base for linear and quote for inverse, like its order books.
                amount_divisor = size if client_id == "deribit" and kind == "perpetual" else 1
                amount_step = _number(precision.get("amount"))
                minimum_amount = _number(((market.get("limits") or {}).get("amount") or {}).get("min"),
                                         nonnegative=True)
                instrument = Instrument(
                    id=identity, venue=self.venue, native_symbol=market["symbol"], asset_id=asset_id,
                    base=base, quote=market["quote"], settle=market.get("settle"), kind=kind,
                    contract_size=size, linear=not bool(market.get("inverse")), mapping_verified=verified,
                    # CCXT precisionMode may be decimal places or significant digits;
                    # emit ticks only when its mode explicitly says TICK_SIZE (4).
                    price_precision=_number(precision.get("price")) if getattr(client, "precisionMode", 4) == 4
                    else None,
                    amount_precision=amount_step / amount_divisor if amount_step is not None and getattr(client, "precisionMode", 4) == 4
                    else None,
                    min_notional=_number(limits.get("min"), nonnegative=True),
                    min_amount=minimum_amount / amount_divisor if minimum_amount is not None else None,
                )
                discovered[identity] = (client_id, market)
                instruments.append(instrument)
        if not successful_clients and not discovered and first_error is not None:
            raise first_error
        self._markets = discovered
        self._instruments = {item.id: item for item in instruments}
        self.discovery_complete = not self.discovery_errors
        return instruments

    def _resolve(self, instrument: Instrument) -> tuple[str, dict]:
        if instrument.venue != self.venue or instrument.id not in self._markets:
            raise ValueError("Instrument must belong to this adapter and be discovered first")
        return self._markets[instrument.id]

    async def fetch(self, instruments: list[Instrument]) -> list[MarketSnapshot]:
        self.fetch_errors = {}
        interval_metadata: dict[tuple[str, str], dict] = {}
        for item in instruments:
            self._resolve(item)
        # Binance premiumIndex omits interval metadata. Query its separate public
        # adjustment list once per client/poll, without inventing defaults for
        # symbols omitted by that list.
        for client_id in self.spec.clients:
            selected = [item for item in instruments if item.kind == "perpetual"
                        and self._resolve(item)[0] == client_id]
            if not selected or not client_id.startswith("binance"):
                continue
            client = self._client(client_id)
            if client.has.get("fetchFundingIntervals"):
                try:
                    async with self._semaphore:
                        values = await client.fetch_funding_intervals([item.native_symbol for item in selected])
                except Exception as exc:
                    self.fetch_errors.update((item.id, f"funding_interval:{type(exc).__name__}") for item in selected)
                    continue
                observed_at = now_ms()
                await self._record_payload("fetch_funding_intervals", values)
                for symbol, payload in values.items():
                    interval_metadata[(client_id, symbol)] = {**payload, "_observed_at": observed_at}
        # A bounded per-symbol path avoids CCXT bulk endpoints mixing product
        # families and preserves distinct bid/ask across native market identities.
        async def fetch_one(instrument: Instrument) -> MarketSnapshot:
            async with self._semaphore:
                client_id, _market = self._resolve(instrument)
                return await self._fetch_one(instrument, interval_metadata.get((client_id,
                                                                               instrument.native_symbol)))
        tasks = [asyncio.create_task(fetch_one(instrument)) for instrument in instruments]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        snapshots = []
        first_error: Exception | None = None
        for item, result in zip(instruments, results):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                first_error = first_error or result
                self.fetch_errors[item.id] = type(result).__name__
            else:
                snapshots.append(result)
        if not snapshots and first_error is not None:
            raise first_error
        return snapshots

    async def fetch_light(self, instruments: list[Instrument]) -> list[MarketSnapshot]:
        """Collect ticker-only sparse patches for a collector-selected slice.

        The caller owns full-catalog rotation, cadence and retry/backoff. Never
        use the detailed fetch path here: it adds per-market funding/OI calls.
        Limits apply after identity deduplication, before any network request.
        """
        self.light_unsupported = []
        self.light_errors = {}
        selected = {item.id: item for item in instruments}
        if len(selected) > self.light_max_instruments:
            raise ValueError(f"fetch_light accepts at most {self.light_max_instruments} unique instruments")
        groups: dict[tuple, list[Instrument]] = defaultdict(list)
        for item in selected.values():
            client_id, _market = self._resolve(item)
            # Deribit's summary endpoint additionally requires one base currency.
            groups[(client_id, item.kind, item.settle, item.linear,
                    item.base if client_id == "deribit" else None)].append(item)
        patches: dict[str, MarketSnapshot] = {}
        first_error: Exception | None = None
        successful_requests = 0

        def record_error(batch: list[Instrument], exc: Exception) -> None:
            nonlocal first_error
            first_error = first_error or exc
            self.light_errors.update((item.id, type(exc).__name__) for item in batch)

        for group, items in groups.items():
            client_id = group[0]
            client = self._client(client_id)
            params = _light_params(client_id, items[0])
            # CCXT's "emulated" capability can hide per-market request fanout.
            # Keep those calls on our explicitly bounded single-ticker path.
            multiple = client.has.get("fetchTickers") is True
            single = client.has.get("fetchTicker") is True
            for offset in range(0, len(items), self.light_batch_size):
                batch = items[offset:offset + self.light_batch_size]
                if multiple:
                    try:
                        async with self._semaphore:
                            payloads = await client.fetch_tickers([item.native_symbol for item in batch], params)
                        await self._record_payload("fetch_tickers", payloads)
                        if not isinstance(payloads, dict):
                            raise ValueError("Public fetch_tickers returned a non-mapping payload")
                    except Exception as exc:
                        if type(exc).__name__ != "NotSupported":
                            record_error(batch, exc)
                            continue
                        multiple = False
                    else:
                        successful_requests += 1
                        for item in batch:
                            ticker = payloads.get(item.native_symbol)
                            if not isinstance(ticker, dict):
                                continue
                            # A mismatched explicit symbol must never refresh a
                            # different market; extra response entries are ignored.
                            if ticker.get("symbol") not in (None, item.native_symbol):
                                continue
                            patch = _light_ticker_patch(item, client_id, ticker, "fetch_tickers",
                                                        self._resolve(item)[1])
                            if patch is not None:
                                patches[item.id] = patch
                        continue
                if not single:
                    self.light_unsupported.append(
                        f"{client_id}:{group[1]}:{group[2]}:native public ticker unavailable")
                    break

                async def fetch_single(item: Instrument) -> MarketSnapshot | None:
                    async with self._semaphore:
                        try:
                            ticker = await client.fetch_ticker(item.native_symbol)
                        except Exception as exc:
                            if type(exc).__name__ != "NotSupported":
                                raise
                            self.light_unsupported.append(
                                f"{client_id}:{item.kind}:{item.native_symbol}:public ticker unavailable")
                            return None
                    await self._record_payload("fetch_ticker", ticker)
                    if not isinstance(ticker, dict):
                        raise ValueError("Public fetch_ticker returned a non-mapping payload")
                    if ticker.get("symbol") not in (None, item.native_symbol):
                        return None
                    return _light_ticker_patch(item, client_id, ticker, "fetch_ticker", self._resolve(item)[1])

                tasks = [asyncio.create_task(fetch_single(item)) for item in batch]
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                except BaseException:
                    # Do not leave queued requests running after the caller
                    # enters backoff or cancels this collection cycle.
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
                for item, result in zip(batch, results):
                    if isinstance(result, BaseException):
                        if not isinstance(result, Exception):
                            raise result
                        record_error([item], result)
                    else:
                        successful_requests += 1
                        if result is not None:
                            patches[result.instrument_id] = result
        if first_error is not None and not successful_requests:
            raise first_error
        return [patches[item_id] for item_id in selected if item_id in patches]

    async def fetch_funding_light(self, instruments: list[Instrument]) -> list[MarketSnapshot]:
        """Funding-only catalog slice; collector owns its independent slow rotation."""
        self.funding_errors = {}
        self.funding_unsupported = []
        selected = {item.id: item for item in instruments}
        if len(selected) > self.light_max_instruments:
            raise ValueError(f"fetch_funding_light accepts at most {self.light_max_instruments} unique instruments")
        groups: dict[tuple, list[Instrument]] = defaultdict(list)
        for item in selected.values():
            client_id, _market = self._resolve(item)
            if item.kind == "perpetual":
                groups[(client_id, item.settle, item.linear)].append(item)
        patches: dict[str, MarketSnapshot] = {}
        first_error: Exception | None = None
        successful_requests = 0

        def record_error(batch: list[Instrument], exc: Exception) -> None:
            nonlocal first_error
            first_error = first_error or exc
            self.funding_errors.update((item.id, type(exc).__name__) for item in batch)

        for group, items in groups.items():
            client_id = group[0]
            client = self._client(client_id)
            interval_metadata: dict[str, dict] = {}
            if client_id.startswith("binance") and client.has.get("fetchFundingIntervals") is True:
                try:
                    async with self._semaphore:
                        values = await client.fetch_funding_intervals([item.native_symbol for item in items])
                    observed = now_ms()
                    await self._record_payload("fetch_funding_intervals", values)
                    if not isinstance(values, dict):
                        raise ValueError("Public funding interval payload must be a mapping")
                    interval_metadata = {symbol: {**payload, "_observed_at": observed}
                                         for symbol, payload in values.items() if isinstance(payload, dict)}
                except Exception as exc:
                    # Keep rates from their independent endpoint; interval stays
                    # absent rather than inventing a default after metadata failure.
                    self.funding_errors.update((item.id, f"funding_interval:{type(exc).__name__}") for item in items)
            multiple = client.has.get("fetchFundingRates") is True
            # Emulated single funding usually calls the batch method again; do
            # not introduce hidden fanout when the native batch is unavailable.
            single = client.has.get("fetchFundingRate") is True
            params = _funding_params(client_id, items[0])
            for offset in range(0, len(items), self.light_batch_size):
                batch = items[offset:offset + self.light_batch_size]
                if multiple:
                    try:
                        async with self._semaphore:
                            payloads = await client.fetch_funding_rates([item.native_symbol for item in batch], params)
                        observed = now_ms()
                        await self._record_payload("fetch_funding_rates", payloads)
                        if not isinstance(payloads, dict):
                            raise ValueError("Public funding rates payload must be a mapping")
                    except Exception as exc:
                        if type(exc).__name__ != "NotSupported":
                            record_error(batch, exc)
                            continue
                        multiple = False
                    else:
                        successful_requests += 1
                        for item in batch:
                            payload = payloads.get(item.native_symbol)
                            if not isinstance(payload, dict) or payload.get("symbol") not in (None, item.native_symbol):
                                self.funding_errors[item.id] = "MissingFundingRate"
                                continue
                            patches[item.id] = self._funding_patch(
                                item, payload, "fetch_funding_rates", observed,
                                interval_metadata.get(item.native_symbol))
                        continue
                if not single:
                    self.funding_unsupported.append(
                        f"{client_id}:{group[1]}:native public funding unavailable")
                    break

                async def fetch_single(item: Instrument) -> MarketSnapshot | None:
                    async with self._semaphore:
                        try:
                            payload = await client.fetch_funding_rate(item.native_symbol)
                        except Exception as exc:
                            if type(exc).__name__ not in {"NotSupported", "BadSymbol"}:
                                raise
                            self.funding_unsupported.append(
                                f"{client_id}:{item.native_symbol}:public funding {type(exc).__name__}")
                            return None
                        observed = now_ms()
                    await self._record_payload("fetch_funding_rate", payload)
                    if not isinstance(payload, dict):
                        raise ValueError("Public funding rate payload must be a mapping")
                    if payload.get("symbol") not in (None, item.native_symbol):
                        return None
                    return self._funding_patch(item, payload, "fetch_funding_rate", observed,
                                               interval_metadata.get(item.native_symbol))

                tasks = [asyncio.create_task(fetch_single(item)) for item in batch]
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                except BaseException:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
                for item, result in zip(batch, results):
                    if isinstance(result, BaseException):
                        if not isinstance(result, Exception):
                            raise result
                        record_error([item], result)
                    else:
                        successful_requests += 1
                        if result is not None:
                            patches[item.id] = result
        if first_error is not None and not successful_requests:
            raise first_error
        return [patches[item_id] for item_id in selected if item_id in patches]

    def _funding_patch(self, instrument: Instrument, payload: dict, method: str, received: int,
                       interval_metadata: dict | None = None) -> MarketSnapshot:
        client_id, market = self._resolve(instrument)
        if interval_metadata:
            market = {**market, "_funding_interval_metadata": interval_metadata}
        source = f"ccxt:{client_id}:{method}:{instrument.native_symbol}"
        event_time = _timestamp(payload.get("timestamp"))
        fields: dict[str, Any] = {}
        sources: dict[str, str] = {}
        times: dict[str, int | None] = {}
        receipts: dict[str, int] = {}
        issues = []
        predicted = payload.get("fundingRate") is None and payload.get("nextFundingRate") is not None
        rate_key = "nextFundingRate" if predicted else "fundingRate"
        rate = _number(payload.get(rate_key))
        if rate is not None:
            fields["funding_rate"] = rate
            fields["funding_kind"] = "predicted" if predicted else "current"
            raw = payload.get("info") or {}
            if client_id == "kucoinfutures" and isinstance(raw, dict) and raw.get("nextFundingRate") is not None:
                fields["funding_kind"] = "predicted"
            if client_id == "deribit":
                fields["funding_kind"] = "settled"
                issues.append("Deribit funding: realized trailing window; continuous accrual, not a discrete upcoming 8h payment")
            sources["funding_rate"] = f"{source}.{rate_key}"
            times["funding_rate"] = event_time
            receipts["funding_rate"] = received
            hours, interval_source = funding_interval(client_id, market, payload)
            if hours is not None:
                fields["funding_interval_hours"] = hours
                interval_time = event_time
                interval_observed = received
                if interval_source.startswith("market."):
                    interval_source = f"ccxt:{client_id}:load_markets:{instrument.native_symbol}:{interval_source}"
                    interval_time = None
                    interval_observed = market.get("_catalog_observed_at")
                    interval_source += ":catalog_metadata_observed_at"
                elif interval_source.startswith("fetch_funding_intervals"):
                    interval_source = f"ccxt:{client_id}:{interval_source}:{instrument.native_symbol}"
                    interval_time = _timestamp((interval_metadata or {}).get("timestamp"))
                    interval_observed = (interval_metadata or {}).get("_observed_at")
                    if interval_time is None:
                        interval_source += ":public_metadata_observed_at"
                else:
                    interval_source = f"{source}:{interval_source}"
                    if interval_time is None:
                        interval_source += ":public_metadata_observed_at"
                sources["funding_interval_hours"] = interval_source
                times["funding_interval_hours"] = interval_time
                if interval_observed is not None:
                    receipts["funding_interval_hours"] = interval_observed
                if interval_time is None:
                    issues.append("Funding interval uses metadata observation time; it is not exchange event time")
            else:
                issues.append("funding_interval_hours: not provided; 8h normalization unavailable")
            next_key = "nextFundingTimestamp" if predicted else "fundingTimestamp"
            next_at = _timestamp(payload.get(next_key))
            if next_at is not None and next_at > (event_time or received) and client_id != "deribit":
                fields["next_funding_at"] = next_at
                sources["next_funding_at"] = f"{source}.{next_key}"
                times["next_funding_at"] = event_time
                receipts["next_funding_at"] = received
        else:
            issues.append("funding_rate: not provided or nonfinite")
        for field, key in (("mark", "markPrice"), ("index", "indexPrice")):
            value = _number(payload.get(key), positive=True)
            if value is not None:
                fields[field] = value
                sources[field] = f"{source}.{key}"
                times[field] = event_time
                receipts[field] = received
        if event_time is None:
            issues.append("Funding source event time unavailable; receipt time is not source time")
        return MarketSnapshot(instrument_id=instrument.id, venue=self.venue, source=source,
                              source_at=event_time, received_at=received, field_sources=sources,
                              field_timestamps=times, field_received_at=receipts, issues=issues,
                              valid=rate is not None, is_demo=False, **fields)

    async def _fetch_one(self, instrument: Instrument, interval_metadata: dict | None = None) -> MarketSnapshot:
        client_id, market = self._resolve(instrument)
        if interval_metadata:
            market = {**market, "_funding_interval_metadata": interval_metadata}
        client = self._client(client_id)
        symbol = instrument.native_symbol
        ticker = await client.fetch_ticker(symbol)
        await self._record_payload("fetch_ticker", ticker)
        received = now_ms()
        ticker_time = _timestamp(ticker.get("timestamp"))
        source = f"ccxt:{client_id}:fetch_ticker:{symbol}"
        fields: dict[str, Any] = {}
        sources: dict[str, str] = {}
        times: dict[str, int | None] = {}
        receipts: dict[str, int] = {}
        issues = []

        def put(field: str, value: Any, endpoint: str, timestamp: int | None,
                *, positive: bool = False, nonnegative: bool = False) -> None:
            numeric = _number(value, positive=positive, nonnegative=nonnegative)
            if numeric is not None:
                fields[field] = numeric
                sources[field] = endpoint
                times[field] = timestamp

        for field in ("bid", "ask", "last"):
            put(field, ticker.get(field), f"{source}.{field}", ticker_time, positive=True)
        for field, key in (("mark", "markPrice"), ("index", "indexPrice")):
            put(field, ticker.get(key), f"{source}.{key}", ticker_time, positive=True)
        # Unified derivative baseVolume can contain contracts. Use the same
        # audited native-unit helper as lightweight and streaming collection.
        volume, volume_unit, volume_suffix = volume_fields(client_id, instrument, ticker, market)
        if volume is not None:
            put("volume", volume, f"{source}.{volume_suffix}", ticker_time, nonnegative=True)
            fields["volume_unit"] = volume_unit

        if instrument.kind == "perpetual":
            for capability, method in (("fetchFundingRate", "fetch_funding_rate"),
                                       ("fetchOpenInterest", "fetch_open_interest")):
                if not client.has.get(capability):
                    issues.append(f"{capability}: not provided by public CCXT adapter")
                    continue
                try:
                    payload = await getattr(client, method)(symbol)
                except Exception as exc:
                    if type(exc).__name__ not in {"NotSupported", "BadSymbol"}:
                        # In particular, throttling/network failures must reach
                        # the collector's bounded retry/backoff state machine.
                        raise
                    # Do not expose raw exception text: proxy URLs may include credentials.
                    issues.append(f"{capability}: {type(exc).__name__}")
                    continue
                received = now_ms()
                endpoint = f"ccxt:{client_id}:{method}:{symbol}"
                event_time = _timestamp(payload.get("timestamp"))
                if capability == "fetchFundingRate":
                    patch = self._funding_patch(instrument, payload, method, received, interval_metadata)
                    for field in ("funding_rate", "funding_kind", "funding_interval_hours", "next_funding_at", "mark", "index"):
                        if field in patch.model_fields_set:
                            fields[field] = getattr(patch, field)
                    sources.update(patch.field_sources)
                    times.update(patch.field_timestamps)
                    receipts.update(patch.field_received_at)
                    issues.extend(patch.issues)
                else:
                    oi, unit, key = _open_interest(client_id, market, payload)
                    if oi is not None:
                        put("open_interest", oi, f"{endpoint}.{key}", event_time, nonnegative=True)
                        fields["oi_unit"] = unit
                    else:
                        issues.append("open_interest: quantity unit unverified or not provided")
        if fields.get("bid") and fields.get("ask") and fields["bid"] > fields["ask"]:
            issues.append("crossed orderbook: bid exceeds ask")
        for field in ("bid", "ask", "last", "volume"):
            if field not in fields:
                issues.append(f"{field}: not provided")
        if instrument.kind == "perpetual":
            for field in ("funding_rate", "mark", "index", "open_interest", "next_funding_at"):
                if field not in fields:
                    issues.append(f"{field}: not provided")
        if any(timestamp is None for timestamp in times.values()):
            issues.append("One or more field event timestamps unavailable; receipt time is not source time")
        valid = any(fields.get(key) for key in ("bid", "ask", "last", "mark", "index"))
        return MarketSnapshot(instrument_id=instrument.id, venue=self.venue, source=source,
                              source_at=ticker_time, received_at=received, field_sources=sources,
                              field_timestamps=times, field_received_at=receipts, issues=issues, valid=valid and
                              "crossed orderbook: bid exceeds ask" not in issues, **fields)

    async def orderbook(self, instrument: Instrument) -> OrderBook:
        client_id, _market = self._resolve(instrument)
        payload = await self._client(client_id).fetch_order_book(instrument.native_symbol)
        # Deribit quantities are base units (linear) or quote notional (inverse),
        # whereas its market contract_size denotes one exchange contract.
        divisor = instrument.contract_size if client_id == "deribit" and instrument.kind == "perpetual" else 1

        def levels(side: str) -> list[tuple[float, float]]:
            values = []
            for level in payload.get(side) or []:
                if len(level) < 2:
                    continue
                price, amount = _number(level[0], positive=True), _number(level[1], positive=True)
                if price is not None and amount is not None:
                    values.append((price, amount / divisor))
            return sorted(values, reverse=side == "bids")

        return OrderBook(instrument_id=instrument.id, bids=levels("bids"), asks=levels("asks"),
                         quantity_unit="contracts" if instrument.kind == "perpetual" else "base",
                         source_at=_timestamp(payload.get("timestamp")),
                         source=f"ccxt:{client_id}:fetch_order_book:{instrument.native_symbol}"
                         + (f":native_quantity_divided_by_contract_size={divisor}" if divisor != 1 else ""))

    async def close(self) -> None:
        results = await asyncio.gather(*(client.close() for client in self._clients.values()),
                                       return_exceptions=True)
        self._clients.clear()
        for result in results:
            if isinstance(result, Exception):
                raise result


def _funding_params(client_id: str, instrument: Instrument) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if client_id.startswith("binance") or client_id in {"bybit", "htx"}:
        params["subType"] = "linear" if instrument.linear else "inverse"
    if client_id == "gate" and instrument.settle:
        params["settle"] = instrument.settle.lower()
    # These unified params are consumed by the CCXT funding method; do not send
    # generic ticker `type` params to endpoints such as OKX that forward them.
    if client_id == "bitget":
        params["productType"] = ({"USDT": "USDT-FUTURES", "USDC": "USDC-FUTURES"}
                                 .get(instrument.settle, "COIN-FUTURES"))
    return params


def _light_params(client_id: str, instrument: Instrument) -> dict[str, Any]:
    """Select endpoint families without mutating shared client default options."""
    params: dict[str, Any] = {}
    if client_id in {"okx", "bybit", "bitget", "gate", "mexc", "htx"}:
        params["type"] = "spot" if instrument.kind == "spot" else "swap"
        if instrument.kind == "perpetual":
            if client_id in {"bybit", "bitget", "htx"}:
                params["subType"] = "linear" if instrument.linear else "inverse"
            if client_id == "gate" and instrument.settle:
                params["settle"] = instrument.settle.lower()
    if client_id == "coinbase":
        params["usePrivate"] = False
    return params


def _light_ticker_patch(instrument: Instrument, client_id: str, ticker: dict,
                        method: str, market: dict | None = None) -> MarketSnapshot | None:
    """REST equivalent of streaming.ticker_patch, with REST provenance.

    Omitted fields must stay unset so merges preserve detailed funding/OI and
    their ages. A ticker's event time does not establish each constituent's age.
    """
    source = f"ccxt:{client_id}:{method}:{instrument.native_symbol}"
    event_time = _timestamp(ticker.get("timestamp"))
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
    if not values:
        return None
    volume, volume_unit, volume_suffix = volume_fields(client_id, instrument, ticker, market)
    if volume is not None:
        values.update(volume=volume, volume_unit=volume_unit)
        sources["volume"] = f"{source}.{volume_suffix}:unified_ticker_event_time"
        timestamps["volume"] = event_time
    crossed = values.get("bid") is not None and values.get("ask") is not None and values["bid"] > values["ask"]
    issues = ["REST timestamps describe unified ticker events; constituent field timing may differ"]
    if event_time is None:
        issues.append("REST ticker event time unavailable; receipt time is not source time")
    if crossed:
        issues.append("crossed orderbook: bid exceeds ask")
    return MarketSnapshot(
        instrument_id=instrument.id, venue=instrument.venue, source=source,
        source_at=event_time, received_at=now_ms(), field_sources=sources,
        field_timestamps=timestamps, valid=not crossed, issues=issues, is_demo=False, **values,
    )


def _open_interest(client_id: str, market: dict, payload: dict) -> tuple[float | None, str | None, str]:
    """CCXT amount is *not* a universal unit: native APIs differ."""
    amount = _number(payload.get("openInterestAmount"), nonnegative=True)
    if amount is not None:
        if client_id in {"okx", "gate", "kucoinfutures", "htx"}:
            return amount, "contracts", "openInterestAmount"
        if client_id in {"binance", "binanceusdm", "binancecoinm"}:
            return amount, "contracts" if market.get("inverse") else market["base"], "openInterestAmount"
        if client_id in {"bybit", "bitget"} and not market.get("inverse"):
            return amount, market["base"], "openInterestAmount"
    value = _number(payload.get("openInterestValue"), nonnegative=True)
    # Only verified currency cases. Others retain missing OI rather than infer USD.
    if value is not None:
        if client_id in {"okx", "gate"}:
            return value, "USD", "openInterestValue"
        if client_id in {"binance", "binanceusdm", "binancecoinm", "bybit", "deribit"}:
            return value, market["quote"], "openInterestValue"
    return None, None, ""


def build_cex_adapters() -> dict[str, CexAdapter]:
    return {venue: CexAdapter(venue) for venue in SPECS}


def coverage() -> list[CoverageEntry]:
    return [CoverageEntry(
        venue=venue, category="cex", products=list(spec.products), method="CCXT public REST + supported source WebSocket",
        capabilities=["market_catalog", "full_catalog_rotation", "ticker", "orderbook_on_demand", "per_market_failure_isolation"] +
                     (["funding_when_provided", "open_interest_when_unit_verified"]
                      if "perpetual" in spec.products else []),
        requirements=["Public API reachable from runtime region; no account/API key required"],
        limitations=[spec.note, "REST polling; exchange rate limiter enabled; collector owns backoff.",
                     "Catalog/history endpoint behavior and live availability not verified in this environment.",
                     "Funding interval remains missing unless explicit metadata is available.",
                     "Public API access can be regionally blocked; missing fields remain null.",
                     "Only BTC/ETH/SOL with reviewed native base identifiers map automatically."],
        status="implemented_unverified_live", docs_url=spec.docs, last_verified_at=None,
    ) for venue, spec in SPECS.items()]
