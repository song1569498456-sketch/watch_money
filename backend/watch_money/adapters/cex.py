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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

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
    def __init__(self, venue: str, *, client_factory: Callable[[str], Any] | None = None):
        self.venue = venue
        self.spec = SPECS[venue]
        self._factory = client_factory or _client_factory
        self._clients: dict[str, Any] = {}
        self._markets: dict[str, tuple[str, dict]] = {}
        self._semaphore = asyncio.Semaphore(4)

    def _client(self, client_id: str) -> Any:
        if client_id not in self._clients:
            self._clients[client_id] = self._factory(client_id)
        return self._clients[client_id]

    async def discover(self) -> list[Instrument]:
        # Sequential clients avoid simultaneous exchange-wide catalog bursts.
        discovered: dict[str, tuple[str, dict]] = {}
        instruments = []
        for client_id in self.spec.clients:
            client = self._client(client_id)
            markets = await client.load_markets(reload=True)
            for market in markets.values():
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
                instrument = Instrument(
                    id=identity, venue=self.venue, native_symbol=market["symbol"], asset_id=asset_id,
                    base=base, quote=market["quote"], settle=market.get("settle"), kind=kind,
                    contract_size=size, linear=not bool(market.get("inverse")), mapping_verified=verified,
                    # CCXT precisionMode may be decimal places or significant digits;
                    # emit ticks only when its mode explicitly says TICK_SIZE (4).
                    price_precision=_number(precision.get("price")) if getattr(client, "precisionMode", 4) == 4
                    else None,
                    amount_precision=_number(precision.get("amount")) if getattr(client, "precisionMode", 4) == 4
                    else None,
                    min_notional=_number(limits.get("min"), nonnegative=True),
                )
                discovered[identity] = (client_id, market)
                instruments.append(instrument)
        self._markets = discovered
        return instruments

    def _resolve(self, instrument: Instrument) -> tuple[str, dict]:
        if instrument.venue != self.venue or instrument.id not in self._markets:
            raise ValueError("Instrument must belong to this adapter and be discovered first")
        return self._markets[instrument.id]

    async def fetch(self, instruments: list[Instrument]) -> list[MarketSnapshot]:
        interval_metadata: dict[tuple[str, str], dict] = {}
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
                values = await client.fetch_funding_intervals([item.native_symbol for item in selected])
                for symbol, payload in values.items():
                    interval_metadata[(client_id, symbol)] = payload
        # A bounded per-symbol path avoids CCXT bulk endpoints mixing product
        # families and preserves distinct bid/ask across native market identities.
        async def fetch_one(instrument: Instrument) -> MarketSnapshot:
            async with self._semaphore:
                client_id, _market = self._resolve(instrument)
                return await self._fetch_one(instrument, interval_metadata.get((client_id,
                                                                               instrument.native_symbol)))
        return list(await asyncio.gather(*(fetch_one(instrument) for instrument in instruments)))

    async def _fetch_one(self, instrument: Instrument, interval_metadata: dict | None = None) -> MarketSnapshot:
        client_id, market = self._resolve(instrument)
        if interval_metadata:
            market = {**market, "_funding_interval_metadata": interval_metadata}
        client = self._client(client_id)
        symbol = instrument.native_symbol
        ticker = await client.fetch_ticker(symbol)
        received = now_ms()
        ticker_time = _timestamp(ticker.get("timestamp"))
        source = f"ccxt:{client_id}:fetch_ticker:{symbol}"
        fields: dict[str, Any] = {}
        sources: dict[str, str] = {}
        times: dict[str, int | None] = {}
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
        # CCXT unified ticker volumes use base and quote currencies. No contract
        # volume field is silently interpreted as base currency.
        if _number(ticker.get("baseVolume"), nonnegative=True) is not None:
            put("volume", ticker["baseVolume"], f"{source}.baseVolume:rolling_24h", ticker_time,
                nonnegative=True)
            fields["volume_unit"] = instrument.base
        elif _number(ticker.get("quoteVolume"), nonnegative=True) is not None:
            put("volume", ticker["quoteVolume"], f"{source}.quoteVolume:rolling_24h", ticker_time,
                nonnegative=True)
            fields["volume_unit"] = instrument.quote

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
                    rate = payload.get("fundingRate")
                    # Some providers publish only an explicitly predicted field.
                    predicted = rate is None and payload.get("nextFundingRate") is not None
                    if predicted:
                        rate = payload["nextFundingRate"]
                    put("funding_rate", rate, f"{endpoint}.{'nextFundingRate' if predicted else 'fundingRate'}",
                        event_time)
                    if "funding_rate" in fields:
                        fields["funding_kind"] = "predicted" if predicted else "current"
                        raw_funding = payload.get("info") or {}
                        if client_id == "kucoinfutures" and isinstance(raw_funding, dict) and \
                                raw_funding.get("nextFundingRate") is not None:
                            fields["funding_kind"] = "predicted"
                        # Deribit's public rate is a realized trailing 8h rate;
                        # never portray it as a future payment prediction.
                        if client_id == "deribit":
                            fields["funding_kind"] = "settled"
                            issues.append("Deribit funding: realized trailing window; continuous accrual, "
                                          "not a discrete upcoming 8h payment")
                        hours, interval_source = funding_interval(client_id, market, payload)
                        if hours is not None:
                            fields["funding_interval_hours"] = hours
                            if interval_source.startswith("market."):
                                sources["funding_interval_hours"] = (
                                    f"ccxt:{client_id}:load_markets:{symbol}:{interval_source}")
                                times["funding_interval_hours"] = None
                            elif interval_source.startswith("fetch_funding_intervals"):
                                sources["funding_interval_hours"] = f"ccxt:{client_id}:{interval_source}:{symbol}"
                                times["funding_interval_hours"] = _timestamp(interval_metadata.get("timestamp"))
                            else:
                                sources["funding_interval_hours"] = f"{endpoint}:{interval_source}"
                                times["funding_interval_hours"] = event_time
                        else:
                            issues.append("funding_interval_hours: not provided; 8h normalization unavailable")
                        next_key = "nextFundingTimestamp" if predicted else "fundingTimestamp"
                        next_at = _timestamp(payload.get(next_key))
                        if next_at is not None and next_at > (event_time or received) and client_id != "deribit":
                            fields["next_funding_at"] = next_at
                            sources["next_funding_at"] = f"{endpoint}.{next_key}"
                            times["next_funding_at"] = event_time
                    for field, key in (("mark", "markPrice"), ("index", "indexPrice")):
                        put(field, payload.get(key), f"{endpoint}.{key}", event_time, positive=True)
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
                              field_timestamps=times, issues=issues, valid=valid and
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
        capabilities=["market_catalog", "ticker", "orderbook_on_demand"] +
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
