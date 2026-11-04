"""Public DEX market data. No authentication, network access or clients at import.

A deployment registry validates protocol/chain/version scope, not individual pools.
Aggregated pool observations are indicative and never exposed as an order book.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import httpx

from watch_money.models import CoverageEntry, Instrument, MarketSnapshot, OrderBook, Pool, now_ms
from watch_money.quote_identity import MAX_QUOTE_IDENTITY_TTL_MS


class UnsupportedMarketData(RuntimeError):
    pass


def number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        return None
    return result


def timestamp(value: Any) -> int | None:
    """Accept explicit UTC ISO or numeric epoch seconds/milliseconds, never invent time."""
    if value is None:
        return None
    try:
        if isinstance(value, str) and "T" in value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return None
            return int(parsed.timestamp() * 1000)
        n = number(value, positive=True)
        return None if n is None else int(n * 1000 if n < 100_000_000_000 else n)
    except (ValueError, TypeError, OverflowError):
        return None


def step(decimals: Any) -> float | None:
    return 10.0 ** -int(decimals) if decimals is not None else None


def interval_hours(value: Any) -> float | None:
    if not isinstance(value, str) or len(value) < 2:
        return None
    units = {"h": 1, "m": 1 / 60, "d": 24, "s": 1 / 3600}
    count = number(value[:-1], positive=True)
    return count * units[value[-1]] if count is not None and value[-1] in units else None


def field_sources(source: str, values: dict) -> dict[str, str]:
    return {key: source for key, value in values.items() if value is not None}


class BroadPublicData:
    """Catalog scheduling interface; reuses fetch, not a cheaper endpoint."""

    light_fetch_mode = "reuse-public-fetch"
    light_fetch_cost = "Same public API calls as fetch; no order book requests"
    on_payload: Callable[[str, str, Any], Awaitable[None]] | None = None

    @property
    def layered_light(self):
        # Only these reviewed fetch paths are bounded all-market payloads.
        # Search/RFQ/pool readers and 1+N funding methods retain bounded rotation.
        return self.venue in {"hyperliquid", "lighter", "dydx", "gmx"}

    @property
    def light_min_bulk_interval(self):
        # Hyperliquid info weights are shared with discovery/detailed requests.
        return 2.0 if self.venue == "hyperliquid" else 0.0

    def plan_light(self, instruments, *, funding=False, batch_size=100):
        from .cex import LightPollBatch

        if funding or not self.layered_light:
            return []
        groups = defaultdict(list)
        for item in instruments:
            if item.venue != self.venue:
                raise ValueError("Foreign market in public bulk plan")
            namespace = item.native_symbol.split(":", 1)[0] if self.venue == "hyperliquid" and ":" in item.native_symbol else ""
            groups[namespace].append(item)
        return [LightPollBatch(f"{self.venue}:{namespace}", tuple(items), True) for namespace, items in groups.items()]

    async def fetch_light_batch(self, batch, *, funding=False):
        from .cex import LightPollResult

        try:
            return LightPollResult(await self.fetch_light(list(batch.instruments)), {}, [])
        except Exception as exc:
            return LightPollResult([], {item.id: type(exc).__name__ for item in batch.instruments}, [], type(exc).__name__)

    async def _record_payload(self, transport: str, payload: Any):
        if self.on_payload is not None:
            # Optional diagnostics must not stop collection. Transport labels
            # contain no request URLs, query strings, headers or credentials.
            with suppress(Exception):
                await self.on_payload(self.venue, transport, payload)

    async def fetch_light(self, instruments):
        """Fetch every supplied catalog market with the normal public fetch path."""
        instruments = list(instruments)
        return await self.fetch(instruments) if instruments else []


class PublicREST(BroadPublicData):
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None

    async def request(self, method: str, url: str, **kwargs):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20, headers={"User-Agent": "watch-money/0.1"})
        response = await self._client.request(method, url, **kwargs)
        response.raise_for_status()
        body = response.json()
        await self._record_payload(f"rest:public:{method.upper()}", body)
        if isinstance(body, dict) and body.get("code") not in (None, 0, 200, "SUCCESS"):
            raise RuntimeError(f"{self.venue}: public API error code {body['code']}")
        return body

    async def close(self):
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


class HyperliquidAdapter(PublicREST):
    venue = "hyperliquid"
    light_fetch_cost = "One metaAndAssetCtxs POST per distinct selected namespace"
    endpoint = "https://api.hyperliquid.xyz/info"

    def __init__(self, client=None):
        super().__init__(client)
        self._namespaces: list[str] = []
        self._quotes: dict[str, str] = {}

    async def info(self, kind: str, **kwargs):
        return await self.request("POST", self.endpoint, json={"type": kind, **kwargs})

    async def discover(self):
        dexes, spot_meta = await asyncio.gather(self.info("perpDexs"), self.info("spotMeta"))
        if not isinstance(dexes, list) or not isinstance(spot_meta.get("tokens"), list):
            raise TypeError("hyperliquid: invalid perpDexs/spotMeta response")
        self._namespaces = [""] + list(dict.fromkeys(d["name"] for d in dexes if d and d.get("name")))
        tokens = {t["index"]: t["name"] for t in spot_meta["tokens"]}
        instruments = []
        for dex in self._namespaces:
            meta = await self.info("meta", dex=dex)
            collateral = meta.get("collateralToken", 0 if not dex else None)
            quote = tokens.get(collateral)
            if quote is None:
                raise ValueError(f"hyperliquid: unresolved collateral token {collateral} for {dex!r}")
            self._quotes[dex] = quote
            for row in meta["universe"]:
                native = row["name"]
                # Native HIP-3 symbols are normally already qualified. Never double-prefix.
                native = native if not dex or ":" in native else f"{dex}:{native}"
                instruments.append(
                    Instrument(
                        id=f"hyperliquid:perpetual:{native}",
                        venue=self.venue,
                        native_symbol=native,
                        asset_id=f"hyperliquid:{native}",
                        base=native.split(":", 1)[-1],
                        quote=quote,
                        settle=quote,
                        kind="perpetual",
                        chain="hyperliquid",
                        amount_precision=step(row.get("szDecimals")),
                        # HL price constraints combine significant figures and szDecimals; no fixed tick invented.
                        active=not row.get("isDelisted", False),
                        mapping_verified=False,
                    )
                )
        return instruments

    async def fetch(self, instruments):
        wanted = {i.native_symbol: i for i in instruments}
        namespaces = list(dict.fromkeys(s.split(":", 1)[0] if ":" in s else "" for s in wanted))
        result = []
        for dex in namespaces:
            data = await self.info("metaAndAssetCtxs", dex=dex)
            if not isinstance(data, list) or len(data) != 2 or len(data[0]["universe"]) != len(data[1]):
                raise ValueError("hyperliquid: mismatched metadata/context lengths")
            for row, ctx in zip(data[0]["universe"], data[1], strict=True):
                native = row["name"]
                native = native if not dex or ":" in native else f"{dex}:{native}"
                instrument = wanted.get(native)
                if instrument is None:
                    continue
                values = {
                    "mark": number(ctx.get("markPx"), positive=True),
                    "index": number(ctx.get("oraclePx"), positive=True),
                    "volume": number(ctx.get("dayNtlVlm"), nonnegative=True),
                    "open_interest": number(ctx.get("openInterest"), nonnegative=True),
                    "funding_rate": number(ctx.get("funding")),
                }
                source = f"{self.endpoint}#metaAndAssetCtxs:dex={dex}"
                result.append(
                    MarketSnapshot(
                        instrument_id=instrument.id,
                        venue=self.venue,
                        **values,
                        volume_unit=instrument.quote,
                        oi_unit=instrument.base,
                        funding_kind="current" if values["funding_rate"] is not None else None,
                        funding_interval_hours=1 if values["funding_rate"] is not None else None,
                        source=source,
                        field_sources=field_sources(source, values),
                        issues=["source_event_time_unavailable", "best_bid_ask_require_orderbook"],
                    )
                )
        return result

    async def orderbook(self, instrument):
        data = await self.info("l2Book", coin=instrument.native_symbol)
        levels = data["levels"]
        if len(levels) != 2:
            raise ValueError("hyperliquid: malformed order book")

        def parse(side):
            return [
                (float(p["px"]), float(p["sz"]))
                for p in side
                if number(p.get("px"), positive=True) is not None
                and number(p.get("sz"), positive=True) is not None
            ]

        return OrderBook(
            instrument_id=instrument.id,
            bids=sorted(parse(levels[0]), reverse=True),
            asks=sorted(parse(levels[1])),
            source_at=timestamp(data.get("time")),
            source=f"{self.endpoint}#l2Book:{instrument.native_symbol}",
        )


class LighterAdapter(PublicREST):
    venue = "lighter"
    light_fetch_cost = "Two GETs: all-market orderBookDetails and funding-rates"
    endpoint = "https://mainnet.zklighter.elliot.ai/api/v1"

    async def details(self):
        data = await self.request("GET", f"{self.endpoint}/orderBookDetails")
        if not isinstance(data.get("order_book_details"), list):
            raise TypeError("lighter: missing order_book_details")
        return data["order_book_details"]

    async def discover(self):
        result = []
        for row in await self.details():
            if row.get("market_type", "perp") != "perp":
                continue
            # Perpetual API uses human decimal prices/sizes and USDC collateral.
            result.append(
                Instrument(
                    id=f"lighter:perpetual:{row['market_id']}",
                    venue=self.venue,
                    native_symbol=str(row["market_id"]),
                    asset_id=f"lighter:{row['symbol']}",
                    base=row["symbol"],
                    quote="USDC",
                    settle="USDC",
                    kind="perpetual",
                    chain="lighter",
                    active=row.get("status") == "active",
                    amount_precision=step(row.get("supported_size_decimals")),
                    price_precision=step(row.get("supported_price_decimals")),
                    min_notional=number(row.get("min_quote_amount"), positive=True),
                )
            )
        return result

    async def fetch(self, instruments):
        details, funding = await asyncio.gather(
            self.details(), self.request("GET", f"{self.endpoint}/funding-rates")
        )
        rates = {str(r["market_id"]): r for r in funding["funding_rates"] if r.get("exchange") == "lighter"}
        rows = {str(r["market_id"]): r for r in details if r.get("market_type", "perp") == "perp"}
        result = []
        for instrument in instruments:
            row = rows.get(instrument.native_symbol)
            if row is None:
                continue
            rate = rates.get(instrument.native_symbol, {})
            values = {
                "last": number(row.get("last_trade_price"), positive=True),
                "mark": number(row.get("mark_price"), positive=True),
                "index": number(row.get("index_price"), positive=True),
                "volume": number(row.get("daily_quote_token_volume"), nonnegative=True),
                "open_interest": number(row.get("open_interest"), nonnegative=True),
                "funding_rate": number(rate.get("rate")),
            }
            detail_source = f"{self.endpoint}/orderBookDetails"
            sources = field_sources(detail_source, values)
            if values["funding_rate"] is not None:
                sources["funding_rate"] = f"{self.endpoint}/funding-rates#exchange=lighter"
            # This comparative endpoint omits a period. It is unsafe to infer hourly from settlement mechanics.
            result.append(
                MarketSnapshot(
                    instrument_id=instrument.id,
                    venue=self.venue,
                    **values,
                    volume_unit="USDC",
                    oi_unit=instrument.base,
                    funding_kind="current" if values["funding_rate"] is not None else None,
                    source=detail_source,
                    field_sources=sources,
                    issues=[
                        "source_event_time_unavailable",
                        "funding_period_unverified",
                        "best_bid_ask_require_orderbook",
                    ],
                )
            )
        return result

    async def orderbook(self, instrument):
        data = await self.request(
            "GET",
            f"{self.endpoint}/orderBookOrders",
            params={"market_id": instrument.native_symbol, "limit": 100},
        )

        def levels(side, reverse=False):
            aggregate: dict[Decimal, Decimal] = defaultdict(Decimal)
            for row in side:
                if number(row.get("price"), positive=True) and number(
                    row.get("remaining_base_amount"), positive=True
                ):
                    aggregate[Decimal(row["price"])] += Decimal(row["remaining_base_amount"])
            return [(float(p), float(q)) for p, q in sorted(aggregate.items(), reverse=reverse)]

        return OrderBook(
            instrument_id=instrument.id,
            bids=levels(data["bids"], True),
            asks=levels(data["asks"]),
            source=f"{self.endpoint}/orderBookOrders?market_id={instrument.native_symbol}",
            source_at=timestamp(data.get("timestamp")),
        )


class DydxAdapter(PublicREST):
    venue = "dydx"
    light_fetch_cost = "One all-market perpetualMarkets GET"
    endpoint = "https://indexer.dydx.trade/v4"

    async def markets(self):
        return (await self.request("GET", f"{self.endpoint}/perpetualMarkets"))["markets"]

    async def discover(self):
        result = []
        for row in (await self.markets()).values():
            base, quote = row["ticker"].rsplit("-", 1)
            result.append(
                Instrument(
                    id=f"dydx:perpetual:{row['ticker']}",
                    venue=self.venue,
                    native_symbol=row["ticker"],
                    asset_id=f"dydx:{base}",
                    base=base,
                    quote=quote,
                    settle="USDC",
                    kind="perpetual",
                    chain="dydx",
                    active=row.get("status") == "ACTIVE",
                    amount_precision=number(row.get("stepSize"), positive=True),
                    price_precision=number(row.get("tickSize"), positive=True),
                )
            )
        return result

    async def fetch(self, instruments):
        rows = await self.markets()
        result = []
        for instrument in instruments:
            row = rows.get(instrument.native_symbol)
            if row is None:
                continue
            values = {
                "index": number(row.get("oraclePrice"), positive=True),
                "volume": number(row.get("volume24H"), nonnegative=True),
                "open_interest": number(row.get("openInterest"), nonnegative=True),
                "funding_rate": number(row.get("nextFundingRate")),
            }
            source = f"{self.endpoint}/perpetualMarkets"
            result.append(
                MarketSnapshot(
                    instrument_id=instrument.id,
                    venue=self.venue,
                    **values,
                    volume_unit=instrument.quote,
                    oi_unit=instrument.base,
                    funding_kind="predicted" if values["funding_rate"] is not None else None,
                    funding_interval_hours=1 if values["funding_rate"] is not None else None,
                    source=source,
                    field_sources=field_sources(source, values),
                    field_timestamps={key: None for key in values},
                    issues=["source_event_time_unavailable", "best_bid_ask_require_orderbook"],
                )
            )
        return result

    async def orderbook(self, instrument):
        source = f"{self.endpoint}/orderbooks/perpetualMarket/{instrument.native_symbol}"
        row = await self.request("GET", source)

        def levels(side, reverse=False):
            return sorted(
                [
                    (float(p["price"]), float(p["size"]))
                    for p in side
                    if number(p.get("price"), positive=True) and number(p.get("size"), positive=True)
                ],
                reverse=reverse,
            )

        return OrderBook(
            instrument_id=instrument.id,
            bids=levels(row["bids"], True),
            asks=levels(row["asks"]),
            source=source,
        )


class CCXTPerpetualAdapter(BroadPublicData):
    """CCXT supplies public transport and venue parsing; absent rate periods stay absent."""
    light_fetch_cost = (
        "Tickers and funding use batch methods when supported, otherwise up to N calls each; "
        "optional funding intervals add a public method call; CCXT may paginate internally"
    )

    def __init__(self, venue: str, client=None):
        self.venue = venue
        self._client = client
        self._markets: dict[str, dict] = {}

    @property
    def client(self):
        if self._client is None:
            import ccxt.async_support as ccxt

            factory = getattr(ccxt, self.venue, None)
            if factory is None:
                raise UnsupportedMarketData(f"Installed CCXT lacks {self.venue}; upgrade ccxt")
            self._client = factory({"enableRateLimit": True, "timeout": 20000, "aiohttp_trust_env": True})
        return self._client

    async def discover(self):
        markets = await self._public_call("load_markets", reload=True)
        self._markets = {
            m["symbol"]: m
            for m in markets.values()
            if m.get("swap") and m.get("linear") and number(m.get("contractSize"), positive=True) is not None
        }
        return [
            Instrument(
                id=f"{self.venue}:perpetual:{m['id']}",
                venue=self.venue,
                native_symbol=symbol,
                asset_id=f"{self.venue}:{m['base']}",
                base=m["base"],
                quote=m["quote"],
                settle=m.get("settle"),
                kind="perpetual",
                active=m.get("active") is not False,
                contract_size=m["contractSize"],
                # CCXT precisionMode can mean tick sizes or decimal places; don't conflate them.
            )
            for symbol, m in self._markets.items()
        ]

    async def fetch(self, instruments):
        symbols = [i.native_symbol for i in instruments]
        if not symbols:
            return []
        client = self.client
        if client.has.get("fetchTickers"):
            tickers = await self._public_call("fetch_tickers", symbols)
        else:
            tickers = {symbol: await self._public_call("fetch_ticker", symbol) for symbol in symbols}
        if client.has.get("fetchFundingRates"):
            funding = await self._public_call("fetch_funding_rates", symbols)
        elif client.has.get("fetchFundingRate"):
            funding = {symbol: await self._public_call("fetch_funding_rate", symbol) for symbol in symbols}
        else:
            funding = {}
        intervals = (
            await self._public_call("fetch_funding_intervals", symbols) if client.has.get("fetchFundingIntervals") else {}
        )
        result = []
        for instrument in instruments:
            row = tickers.get(instrument.native_symbol)
            if row is None:
                continue
            rate = funding.get(instrument.native_symbol, {})
            raw = row.get("info") or {}
            market = self._markets.get(instrument.native_symbol, {})
            market_info = market.get("info") or {}
            period = interval_hours(rate.get("interval"))
            if period is None:
                period = interval_hours(intervals.get(instrument.native_symbol, {}).get("interval"))
            if period is None and self.venue == "paradex":
                period = number(market_info.get("funding_period_hours"), positive=True)
            # dYdX nextFundingRate is explicitly the predicted next one-hour payment.
            funding_rate = number(rate.get("fundingRate"))
            funding_kind = "current" if funding_rate is not None else None
            if self.venue == "dydx" and funding_rate is None:
                funding_rate = number(raw.get("nextFundingRate"))
                if funding_rate is not None:
                    period, funding_kind = 1, "predicted"
            values = {
                "bid": number(row.get("bid"), positive=True),
                "ask": number(row.get("ask"), positive=True),
                "last": number(row.get("last"), positive=True),
                "mark": number(rate.get("markPrice"), positive=True),
                "index": number(rate.get("indexPrice"), positive=True),
                "volume": number(row.get("quoteVolume"), nonnegative=True),
                "funding_rate": funding_rate,
            }
            if self.venue == "dydx":
                values["index"] = number(raw.get("oraclePrice"), positive=True)
                values["open_interest"] = number(raw.get("openInterest"), nonnegative=True)
            elif self.venue == "paradex":
                values["open_interest"] = number(raw.get("open_interest"), nonnegative=True)
            source = f"direct:{self.venue}:public-rest:ccxt"
            issues = []
            source_at = timestamp(row.get("timestamp"))
            if source_at is None:
                issues.append("source_event_time_unavailable")
            if funding_rate is not None and period is None:
                issues.append("funding_period_unavailable")
            sources = field_sources(source, values)
            for field in ("mark", "index", "funding_rate"):
                if values.get(field) is not None:
                    sources[field] = f"{source}#funding-or-market-context"
            result.append(
                MarketSnapshot(
                    instrument_id=instrument.id,
                    venue=self.venue,
                    **values,
                    volume_unit=instrument.quote,
                    oi_unit=instrument.base if "open_interest" in values else None,
                    funding_kind=funding_kind,
                    funding_interval_hours=period,
                    next_funding_at=timestamp(rate.get("nextFundingTimestamp")),
                    source_at=source_at,
                    source=source,
                    field_sources=sources,
                    field_timestamps={
                        **{k: source_at for k in values if k not in ("funding_rate", "mark", "index")},
                        **{k: timestamp(rate.get("timestamp")) for k in ("funding_rate", "mark", "index")},
                    },
                    issues=issues,
                )
            )
        return result

    async def _public_call(self, method, *args, **kwargs):
        payload = await getattr(self.client, method)(*args, **kwargs)
        await self._record_payload(f"rest:ccxt:{method}", payload)
        return payload

    async def orderbook(self, instrument):
        book = await self._public_call("fetch_order_book", instrument.native_symbol, 100)

        def levels(side):
            return [
                (float(row[0]), float(row[1]))
                for row in side
                if number(row[0], positive=True) and number(row[1], positive=True)
            ]

        return OrderBook(
            instrument_id=instrument.id,
            bids=levels(book["bids"]),
            asks=levels(book["asks"]),
            quantity_unit="contracts",
            source_at=timestamp(book.get("timestamp")),
            source=f"direct:{self.venue}:public-rest:ccxt",
        )

    async def close(self):
        if self._client is not None:
            await self._client.close()
            self._client = None


def deployments() -> list[dict]:
    return json.loads(Path(__file__).with_name("deployments.json").read_text(encoding="utf-8"))["deployments"]


class AggregatedSpotAdapter(PublicREST):
    discovery_complete = False
    discovery_state = "search_limited"
    light_fetch_cost = "One pair-list GET per chain and each batch of at most 30 pools"
    endpoint = "https://api.dexscreener.com/latest/dex"

    def __init__(self, venue: str, client=None, search_queries: list[str] | None = None):
        super().__init__(client)
        self.venue = venue
        self.registry = [d for d in deployments() if d["protocol"] == venue]
        # Protocol/deployment searches have no base-token allowlist. The upstream
        # search remains bounded and is not a complete chain pool index.
        self.default_search_queries = list(dict.fromkeys(
            [venue] + [f"{dex_id} {deployment['chain']}"
                       for deployment in self.registry for dex_id in deployment["dex_ids"]]
        ))
        self.search_queries = list(self.default_search_queries if search_queries is None else search_queries)
        self._instruments: dict[str, Instrument] = {}
        self.pools: dict[str, Pool] = {}
        self._pairs: dict[str, dict] = {}

    def match_deployment(self, pair):
        labels = [str(label).lower() for label in (pair.get("labels") or [])]
        for deployment in self.registry:
            if pair.get("chainId") != deployment["chain"] or pair.get("dexId") not in deployment["dex_ids"]:
                continue
            # Require explicit version labels or a version-specific dex ID. No default v2 assumptions.
            if deployment["version_labels"] and not set(deployment["version_labels"]).intersection(labels):
                continue
            if not deployment["version_labels"] and pair.get("dexId") not in deployment.get(
                "version_specific_dex_ids", []
            ):
                continue
            return deployment
        return None

    def register(self, pair):
        deployment = self.match_deployment(pair)
        if deployment is None:
            return None
        base, quote = pair.get("baseToken", {}), pair.get("quoteToken", {})
        if not all(
            [
                pair.get("pairAddress"),
                base.get("address"),
                quote.get("address"),
                base.get("symbol"),
                quote.get("symbol"),
            ]
        ):
            return None
        chain, version, address = deployment["chain"], deployment["version"], pair["pairAddress"]
        if chain != "solana":
            address = address.lower()
        identity = f"{self.venue}:{chain}:{version}:{address}"
        base_address = base["address"] if chain == "solana" else base["address"].lower()
        observed_at = now_ms()
        instrument = Instrument(
            id=identity,
            venue=self.venue,
            native_symbol=address,
            asset_id=f"{chain}:{base_address}",
            base=base["symbol"],
            quote=quote["symbol"],
            kind="pool",
            mechanism="amm",
            chain=chain,
            quote_asset_id=f"{chain}:{quote['address'] if chain == 'solana' else quote['address'].lower()}",
            native_quote_asset_id=f"{chain}:{quote['address'] if chain == 'solana' else quote['address'].lower()}",
            quote_identity_source=f"{self.endpoint}/pairs/{chain}/{address}#quoteToken.address",
            quote_identity_at=observed_at,
            quote_identity_expires_at=observed_at + MAX_QUOTE_IDENTITY_TTL_MS,
        )
        self._instruments[identity] = instrument
        self._pairs[identity] = pair
        self.pools[identity] = Pool(
            id=identity,
            chain=chain,
            protocol=self.venue,
            version=version,
            address=address,
            asset0=base["address"],
            asset1=quote["address"],
            pool_type=deployment["pool_type"],
            liquidity_usd=number((pair.get("liquidity") or {}).get("usd"), nonnegative=True),
            verified=False,
        )
        return instrument

    async def discover(self):
        found = {}
        for query in dict.fromkeys(self.search_queries):
            if not isinstance(query, str) or not query.strip():
                raise ValueError("DEX discovery queries must be nonempty strings")
            if query.startswith("pool:"):
                parts = query.split(":", 2)
                chains = {deployment["chain"] for deployment in self.registry}
                if (len(parts) != 3 or parts[1] not in chains
                        or not parts[2].isascii() or not parts[2].isalnum()):
                    raise ValueError("Known pool source must be pool:<registered-chain>:<pool-address>")
                response = await self.request("GET", f"{self.endpoint}/pairs/{parts[1]}/{parts[2]}")
            else:
                response = await self.request("GET", f"{self.endpoint}/search", params={"q": query})
            if not isinstance(response.get("pairs"), list):
                raise TypeError("dexscreener: malformed search response")
            for pair in response["pairs"]:
                instrument = self.register(pair)
                if instrument is not None:
                    found[instrument.id] = instrument
        return list(found.values())

    async def fetch(self, instruments):
        wanted = {i.id: i for i in instruments}
        groups = defaultdict(list)
        for instrument in instruments:
            groups[instrument.chain].append(instrument.native_symbol)
        current = {}
        for chain, addresses in groups.items():
            for offset in range(0, len(addresses), 30):
                response = await self.request(
                    "GET", f"{self.endpoint}/pairs/{chain}/{','.join(addresses[offset : offset + 30])}"
                )
                pairs = response.get("pairs")
                if not isinstance(pairs, list):
                    raise TypeError("dexscreener: missing pair list")
                for pair in pairs:
                    instrument = self.register(pair)
                    if instrument is not None and instrument.id in wanted:
                        current[instrument.id] = pair
        result = []
        for identity, pair in current.items():
            instrument = wanted[identity]
            pool = self.pools[identity]
            # priceNative is quote-token per base token; priceUsd is NOT the original pair quote.
            values = {
                "last": number(pair.get("priceNative"), positive=True),
                "volume": number((pair.get("volume") or {}).get("h24"), nonnegative=True),
            }
            source = f"aggregated:dexscreener:{instrument.chain}:{pool.address}"
            sources = field_sources(source, values)
            sources.update(
                protocol=self.venue,
                version=pool.version,
                pool_address=pool.address,
                base_token_address=pool.asset0,
                quote_token_address=pool.asset1,
                route=f"{instrument.chain}/{self.venue}/{pool.version}/{pool.address}",
            )
            result.append(
                MarketSnapshot(
                    instrument_id=identity,
                    venue=self.venue,
                    **values,
                    volume_unit="USD",
                    source=source,
                    field_sources=sources,
                    issues=[
                        "aggregated_source",
                        "indicative_pool_price_not_executable",
                        "pool_membership_not_rpc_verified",
                        "source_event_time_unavailable",
                        "bounded_search_not_full_chain_discovery",
                        "search_limited",
                    ],
                )
            )
        return result

    async def orderbook(self, instrument):
        raise UnsupportedMarketData(
            "AMM pool price is indicative; executable size-dependent quotes require RPC/quoter"
        )


class EdgeXAdapter(PublicREST):
    venue = "edgex"
    light_fetch_cost = "One all-market ticker GET plus one funding GET per selected market (1+N)"
    endpoint = "https://edgex-prod-v2.edgex.exchange/api/v2/public"

    def __init__(self, client=None):
        super().__init__(client)
        self._metadata = {}

    async def get(self, path, **kwargs):
        return (await self.request("GET", f"{self.endpoint}/{path}", **kwargs))["data"]

    async def discover(self):
        data = await self.get("meta/getMetaData")
        coins = {str(c["coinId"]): c["coinName"] for c in data["coinList"]}
        self._metadata = {str(c["contractId"]): c for c in data["contractList"]}
        result = []
        for native, row in self._metadata.items():
            base, quote = coins[str(row["baseCoinId"])], coins[str(row["quoteCoinId"])]
            result.append(
                Instrument(
                    id=f"edgex:perpetual:{native}",
                    venue=self.venue,
                    native_symbol=native,
                    asset_id=f"edgex:{row['baseCoinId']}",
                    base=base,
                    quote=quote,
                    settle=quote,
                    kind="perpetual",
                    active=row.get("enableTrade") is True,
                    amount_precision=number(row.get("stepSize"), positive=True),
                    price_precision=number(row.get("tickSize"), positive=True),
                )
            )
        return result

    async def fetch(self, instruments):
        tickers = {str(r["contractId"]): r for r in await self.get("quote/getTicker")}
        result = []
        for instrument in instruments:
            row = tickers.get(instrument.native_symbol)
            if row is None:
                continue
            rates = await self.get(
                "funding/getLatestFundingRate", params={"contractId": instrument.native_symbol}
            )
            rate = next((r for r in rates if str(r["contractId"]) == instrument.native_symbol), {})
            minutes = number(rate.get("fundingRateIntervalMin"), positive=True)
            if minutes is None:
                minutes = number(
                    self._metadata.get(instrument.native_symbol, {}).get("fundingRateIntervalMin"),
                    positive=True,
                )
            values = {
                "last": number(row.get("lastPrice"), positive=True),
                "mark": number(row.get("markPrice"), positive=True),
                "index": number(row.get("indexPrice"), positive=True),
                "volume": number(row.get("value"), nonnegative=True),
                "open_interest": number(row.get("openInterest"), nonnegative=True),
                "funding_rate": number(rate.get("fundingRate")),
            }
            quote_source = f"{self.endpoint}/quote/getTicker"
            rate_source = f"{self.endpoint}/funding/getLatestFundingRate"
            sources = field_sources(quote_source, values)
            sources["funding_rate"] = rate_source
            times = {key: None for key in values}
            times["funding_rate"] = timestamp(rate.get("fundingTimestamp"))
            # Ticker endTime describes the rolling statistics window, not when the mark was observed.
            times["volume"] = timestamp(row.get("endTime"))
            result.append(
                MarketSnapshot(
                    instrument_id=instrument.id,
                    venue=self.venue,
                    **values,
                    volume_unit=instrument.quote,
                    oi_unit=instrument.base,
                    funding_kind=("settled" if rate.get("isSettlement") else "current")
                    if values["funding_rate"] is not None
                    else None,
                    funding_interval_hours=minutes / 60 if minutes is not None else None,
                    next_funding_at=timestamp(row.get("nextFundingTime")),
                    source=quote_source,
                    field_sources=sources,
                    field_timestamps=times,
                    issues=["ticker_event_time_unavailable", "best_bid_ask_require_orderbook"],
                )
            )
        return result

    async def orderbook(self, instrument):
        rows = await self.get("quote/getDepth", params={"contractId": instrument.native_symbol, "level": 200})
        row = next(r for r in rows if str(r["contractId"]) == instrument.native_symbol)

        def levels(side, reverse=False):
            return sorted(
                [
                    (float(p["price"]), float(p["size"]))
                    for p in side
                    if number(p.get("price"), positive=True) and number(p.get("size"), positive=True)
                ],
                reverse=reverse,
            )

        return OrderBook(
            instrument_id=instrument.id,
            bids=levels(row["bids"], True),
            asks=levels(row["asks"]),
            source=f"{self.endpoint}/quote/getDepth",
        )


class VariationalAdapter(PublicREST):
    discovery_complete = False
    venue = "variational"
    light_fetch_cost = "One all-listings metadata/v2/stats GET"
    endpoint = "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/v2/stats"

    async def listings(self):
        data = await self.request("GET", self.endpoint)
        return [r for r in data["listings"] if r.get("contract_type") == "perpetual"]

    async def discover(self):
        return [
            Instrument(
                id=f"variational:perpetual:{r['ticker']}",
                venue=self.venue,
                native_symbol=r["ticker"],
                asset_id=f"variational:{r['ticker']}",
                base=r["ticker"].removesuffix("-PERP"),
                quote="USDC",
                settle="USDC",
                kind="perpetual",
                mechanism="rfq",
            )
            for r in await self.listings()
        ]

    async def fetch(self, instruments):
        rows = {r["ticker"]: r for r in await self.listings()}
        result = []
        for instrument in instruments:
            row = rows.get(instrument.native_symbol)
            if row is None:
                continue
            funding = row.get("funding") or {}
            current = funding.get("last") or {}
            seconds = number(funding.get("interval_seconds"), positive=True)
            long_oi = number((row.get("open_interest") or {}).get("long_open_interest"), nonnegative=True)
            short_oi = number((row.get("open_interest") or {}).get("short_open_interest"), nonnegative=True)
            values = {
                "mark": number(row.get("mark_price"), positive=True),
                "volume": number(row.get("volume_24h"), nonnegative=True),
                "open_interest": long_oi + short_oi if long_oi is not None and short_oi is not None else None,
                "funding_rate": number(current.get("interval_rate")),
            }
            sources = field_sources(self.endpoint, values)
            quotes = row.get("quotes")
            metrics = {}
            times = {key: None for key in values}
            if quotes:
                # Preserve size/time explicitly; these cached RFQ samples are not executable bid/ask liquidity.
                metrics["indicative_rfq_quotes_json"] = json.dumps(quotes, separators=(",", ":"))
                sources["protocol_metrics.indicative_rfq_quotes_json"] = f"{self.endpoint}#quotes"
                times["protocol_metrics.indicative_rfq_quotes_json"] = timestamp(quotes.get("updated_at"))
            result.append(
                MarketSnapshot(
                    instrument_id=instrument.id,
                    venue=self.venue,
                    **values,
                    volume_unit="USDC",
                    oi_unit="USDC:trader-long-plus-short",
                    funding_kind="current" if values["funding_rate"] is not None else None,
                    funding_interval_hours=seconds / 3600 if seconds is not None else None,
                    next_funding_at=timestamp(funding.get("next_timestamp")),
                    source=self.endpoint,
                    field_sources=sources,
                    protocol_metrics=metrics,
                    field_timestamps=times,
                    issues=[
                        "source_event_time_unavailable",
                        "rfq_samples_indicative_cached_up_to_600_seconds",
                        "swap_products_not_supported",
                    ],
                )
            )
        return result

    async def orderbook(self, instrument):
        raise UnsupportedMarketData(
            "Variational uses RFQ; public size-specific cached samples are not an executable order book"
        )


class GMXAdapter(PublicREST):
    """Arbitrum V2 market discovery and indicative marks; no universal funding rate invented."""

    venue = "gmx"
    light_fetch_cost = "One Arbitrum V2 all-market ticker GET; no executable quotes"
    endpoint = "https://arbitrum-api.gmxinfra.io"
    market_endpoint = "https://arbitrum.gmxapi.io/v1/markets/tickers"

    def __init__(self, client=None):
        super().__init__(client)
        self._tokens = {}
        self._markets = {}

    async def discover(self):
        market_data, token_data = await asyncio.gather(
            self.request("GET", f"{self.endpoint}/markets"), self.request("GET", f"{self.endpoint}/tokens")
        )
        self._tokens = {t["address"].lower(): t for t in token_data["tokens"]}
        self._markets = {}
        result = []
        for row in market_data["markets"]:
            index_address = row["indexToken"].lower()
            token = self._tokens.get(index_address)
            if token is None or index_address == "0x" + "0" * 40:
                continue  # Swap-only pool, not a perpetual market.
            native = row["marketToken"].lower()
            self._markets[native] = row
            result.append(
                Instrument(
                    id=f"gmx:arbitrum:v2:{native}",
                    venue=self.venue,
                    native_symbol=native,
                    asset_id=f"arbitrum:{index_address}",
                    base=token["symbol"],
                    quote="USD",
                    kind="perpetual",
                    mechanism="amm",
                    chain="arbitrum",
                    index_source=f"{self.endpoint}/prices/tickers",
                    # GMX supports long/short collateral tokens; no single settlement token fabricated.
                )
            )
        return result

    async def fetch(self, instruments):
        data = await self.request("GET", self.market_endpoint)
        if not isinstance(data, list):
            raise TypeError("gmx: malformed market tickers")
        rows = {r["marketTokenAddress"].lower(): r for r in data}
        result = []
        for instrument in instruments:
            row = rows.get(instrument.native_symbol)
            if row is None:
                continue

            def fixed30(value):
                return float(Decimal(str(value)) / Decimal(10) ** 30) if value is not None else None

            mark = fixed30(row.get("markPrice"))
            long_oi, short_oi = fixed30(row.get("longInterestUsd")), fixed30(row.get("shortInterestUsd"))
            values = {
                "mark": mark,
                "open_interest": long_oi + short_oi if long_oi is not None and short_oi is not None else None,
            }
            sources = field_sources(self.market_endpoint, values)
            # Side-specific rates have different pay/receive/borrowing meanings; preserve raw fields for review.
            metrics = {}
            for key in ("fundingRateLong", "fundingRateShort", "borrowingRateLong", "borrowingRateShort"):
                if row.get(key) is not None:
                    metrics[f"raw_{key}"] = str(row[key])
                    sources[f"protocol_metrics.raw_{key}"] = f"{self.market_endpoint}#{key}"
            result.append(
                MarketSnapshot(
                    instrument_id=instrument.id,
                    venue=self.venue,
                    **values,
                    oi_unit="USD:long-plus-short",
                    source=self.market_endpoint,
                    source_at=timestamp(row.get("updatedAt")),
                    field_sources=sources,
                    protocol_metrics=metrics,
                    field_timestamps={key: timestamp(row.get("updatedAt")) for key in values},
                    issues=[
                        "indicative_mark_not_executable_price",
                        "funding_and_borrowing_side_specific_not_normalized",
                        "market_liquidity_requires_additional_state",
                        "arbitrum_v2_only",
                        "source_event_time_may_be_unavailable",
                    ],
                )
            )
        return result

    async def orderbook(self, instrument):
        raise UnsupportedMarketData(
            "GMX is pool based; executable price requires size/collateral/side and price-impact state"
        )


class JupiterPerpsAdapter(PublicREST):
    """Read-only published market/state APIs; their reference price is not an execution quote."""

    venue = "jupiter-perps"
    light_fetch_cost = "One jlp-info GET plus market-stats and pool-info GETs per selected mint (1+2N)"
    endpoint = "https://perps-api.jup.ag/v1"
    discovery_complete = False
    # Explicitly enumerated market mints in the published /market-stats and /pool-info schema.
    supported_markets: ClassVar[dict[str, str]] = {
        "So11111111111111111111111111111111111111112": "SOL",
        "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
        "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "BTC",
    }

    async def custodies(self):
        data = await self.request("GET", f"{self.endpoint}/jlp-info")
        if not isinstance(data.get("custodies"), list):
            raise TypeError("jupiter-perps: missing JLP custody list")
        return {c["mint"]: c for c in data["custodies"] if c.get("mint") in self.supported_markets}

    async def discover(self):
        return [
            Instrument(
                id=f"jupiter-perps:solana:perpetual:{mint}",
                venue=self.venue,
                native_symbol=mint,
                asset_id=f"solana:{mint}",
                base=row.get("symbol") or self.supported_markets[mint],
                quote="USD",
                kind="perpetual",
                mechanism="amm",
                chain="solana",
                # Collateral/settlement depends on side and custody; no universal USDC settlement inferred.
            )
            for mint, row in (await self.custodies()).items()
        ]

    async def fetch(self, instruments):
        custodies = await self.custodies()
        result = []
        for instrument in instruments:
            mint = instrument.native_symbol
            if mint not in self.supported_markets:
                raise UnsupportedMarketData("Jupiter mint is not in the published market API enum")
            custody = custodies.get(mint)
            if custody is None:
                continue
            stats, pool = await asyncio.gather(
                self.request("GET", f"{self.endpoint}/market-stats", params={"mint": mint}),
                self.request("GET", f"{self.endpoint}/pool-info", params={"mint": mint}),
            )
            metrics = {
                "reference_price_usd": number(stats.get("price"), positive=True),
                # The market-stats volume sample has no explicit unit/window contract.
                "market_stats_volume_raw": str(stats["volume"]) if stats.get("volume") is not None else None,
                "custody_address": custody.get("pubkey"),
                "token_mint": mint,
            }
            sources = {
                "protocol_metrics.reference_price_usd": f"{self.endpoint}/market-stats?mint={mint}",
                "protocol_metrics.market_stats_volume_raw": f"{self.endpoint}/market-stats?mint={mint}",
            }
            for side in ("long", "short"):
                for field, suffix in (
                    ("BorrowRatePercent", "borrow_rate_fraction"),
                    ("UtilizationPercent", "utilization_fraction"),
                ):
                    raw = number(pool.get(f"{side}{field}"), nonnegative=True)
                    key = f"{side}_{suffix}"
                    metrics[key] = raw / 100 if raw is not None else None
                    sources[f"protocol_metrics.{key}"] = (
                        f"{self.endpoint}/pool-info?mint={mint}#{side}{field}"
                    )
                # Availability is an indicative API quantity; its scale is not guessed from a sample.
                raw = pool.get(f"{side}AvailableLiquidity")
                key = f"{side}_available_liquidity_raw"
                metrics[key] = str(raw) if raw is not None else None
                sources[f"protocol_metrics.{key}"] = f"{self.endpoint}/pool-info?mint={mint}"
            # Account docs define these as USD6. guaranteedUsd excludes collateral and is NOT long OI.
            for field, key in (
                ("aumUsd", "custody_aum_usd"),
                ("guaranteedUsd", "long_position_size_minus_collateral_usd"),
                ("globalShortSizes", "short_open_interest_usd"),
            ):
                raw = number(custody.get(field), nonnegative=True)
                metrics[key] = raw / 1_000_000 if raw is not None else None
                sources[f"protocol_metrics.{key}"] = f"{self.endpoint}/jlp-info#{field}"
            for field in ("owned", "locked"):
                metrics[f"custody_{field}_token_raw"] = (
                    str(custody[field]) if custody.get(field) is not None else None
                )
                sources[f"protocol_metrics.custody_{field}_token_raw"] = f"{self.endpoint}/jlp-info#{field}"
            sources["protocol_metrics.custody_address"] = f"{self.endpoint}/jlp-info#pubkey"
            sources["protocol_metrics.token_mint"] = f"{self.endpoint}/jlp-info#mint"
            result.append(
                MarketSnapshot(
                    instrument_id=instrument.id,
                    venue=self.venue,
                    source=f"{self.endpoint}/market-stats?mint={mint}",
                    protocol_metrics=metrics,
                    field_sources=sources,
                    field_timestamps={key: None for key in sources},
                    issues=[
                        "reference_price_not_classified_as_trade_mark_or_index",
                        "borrow_fees_are_not_funding",
                        "borrow_rate_period_unverified_in_api_schema",
                        "side_specific_liquidity_not_execution_quote",
                        "source_event_time_unavailable",
                        "published_market_enum_bounded_discovery",
                    ],
                )
            )
        return result

    async def orderbook(self, instrument):
        raise UnsupportedMarketData(
            "Jupiter Perps is pool based; side/collateral/size-dependent quotes are not an order book"
        )


def base58_encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    value = int.from_bytes(data, "big")
    encoded = ""
    while value:
        value, digit = divmod(value, 58)
        encoded = alphabet[digit] + encoded
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + encoded


class DriftAdapter(PublicREST):
    """Optional metadata-only RPC audit of original Drift; current lifecycle is unverified."""

    light_fetch_cost = "Two getProgramAccounts RPC reads; metadata only, no market prices"
    venue = "drift"
    program_id = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"
    # A successor repository claim is not canonical Drift lifecycle verification.
    migration_source = (
        "https://github.com/velocity-exchange/velocity-v1/blob/master/docs/DRIFT-TO-VELOCITY.md"
    )

    def __init__(self, client=None, rpc_url: str | None = None):
        super().__init__(client)
        self.rpc_url = rpc_url or os.getenv("SOLANA_RPC_URL")
        registry = json.loads(Path(__file__).with_name("deployments.json").read_text(encoding="utf-8"))
        self.layout = registry["solana_readers"]["drift_v2_metadata"]
        self.protocol_state: dict = {}

    async def rpc(self, method, params):
        if not self.rpc_url:
            raise UnsupportedMarketData(
                "Drift metadata-only adapter requires SOLANA_RPC_URL; current deployment lifecycle is not independently verified"
            )
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            response = await self.request("POST", self.rpc_url, json=payload)
        except httpx.HTTPError as exc:
            # RPC URLs can embed credentials; never propagate the URL in stored collector errors.
            raise RuntimeError(f"drift: configured Solana RPC failed ({type(exc).__name__})") from None
        if response.get("error") is not None:
            error = response["error"]
            raise RuntimeError(f"drift: Solana RPC error code {error.get('code', 'unknown')}")
        if "result" not in response:
            raise ValueError("drift: missing Solana RPC result")
        return response["result"]

    async def accounts(self, name):
        discriminator = hashlib.sha256(f"account:{name}".encode()).digest()[:8]
        response = await self.rpc(
            "getProgramAccounts",
            [
                self.program_id,
                {
                    "encoding": "base64",
                    "commitment": "confirmed",
                    "withContext": True,
                    "filters": [{"memcmp": {"offset": 0, "bytes": base58_encode(discriminator)}}],
                },
            ],
        )
        if not isinstance(response, dict) or not isinstance(response.get("value"), list):
            raise TypeError("drift: RPC must return account data with context")
        slot = response["context"]["slot"]
        decoded = []
        fields = self.layout[name]["fields"]
        for row in response["value"]:
            account = row["account"]
            if account.get("owner") != self.program_id or account.get("executable") is not False:
                raise ValueError("drift: account is not original Drift-owned state")
            raw, encoding = account["data"]
            if encoding != "base64":
                raise ValueError("drift: unexpected RPC account encoding")
            data = base64.b64decode(raw, validate=True)
            if data[:8] != discriminator:
                raise ValueError("drift: wrong Anchor account discriminator")
            values = {"pubkey": row["pubkey"], "slot": slot}
            for key, field in fields.items():
                offset, size = field["offset"], field["size"]
                value = data[offset : offset + size]
                if len(value) != size:
                    raise ValueError("drift: truncated account metadata")
                if field["type"] == "publicKey":
                    values[key] = base58_encode(value)
                elif field["type"] == "name":
                    values[key] = value.rstrip(b"\0 ").decode("utf-8", errors="strict")
                else:
                    number_value = int.from_bytes(value, "little")
                    if field.get("variants"):
                        if number_value >= len(field["variants"]):
                            raise ValueError("drift: unsupported account enum/layout")
                        values[key] = field["variants"][number_value]
                    else:
                        values[key] = number_value
            if name == "PerpMarket" and values["selfPubkey"] != row["pubkey"]:
                raise ValueError("drift: account address disagrees with embedded market public key")
            decoded.append(values)
        return decoded

    async def read_metadata(self):
        states, markets = await asyncio.gather(self.accounts("State"), self.accounts("PerpMarket"))
        if len(states) != 1:
            raise ValueError("drift: expected exactly one original global State account")
        self.protocol_state = states[0]
        return markets

    async def discover(self):
        instruments = []
        for market in await self.read_metadata():
            if market["contractType"] != "Perpetual":
                continue
            symbol = market["name"]
            instruments.append(
                Instrument(
                    id=f"drift:solana:v2:{market['pubkey']}",
                    venue=self.venue,
                    native_symbol=market["pubkey"],
                    asset_id=f"drift:market-index:{market['marketIndex']}",
                    base=symbol.removesuffix("-PERP"),
                    quote="USD",
                    kind="perpetual",
                    mechanism="amm",
                    chain="solana",
                    active=False,
                    # This adapter has no validated execution capability; active=False is local, not a lifecycle claim.
                )
            )
        return instruments

    async def fetch(self, instruments):
        rows = {row["pubkey"]: row for row in await self.read_metadata()}
        result = []
        for instrument in instruments:
            market = rows.get(instrument.native_symbol)
            if market is None:
                continue
            metrics = {
                "program_id": self.program_id,
                "deployment_state": "unverified-lifecycle",
                "adapter_execution_state": "metadata-only-non-executable",
                "rpc_context_slot": str(market["slot"]),
                "global_state_context_slot": str(self.protocol_state["slot"]),
                "market_index": market["marketIndex"],
                "market_status": market["status"],
                "market_paused_operations_bits": market["pausedOperations"],
                "exchange_status_bits": self.protocol_state["exchangeStatus"],
                "contract_type": market["contractType"],
                "quote_spot_market_index": market["quoteSpotMarketIndex"],
            }
            source = f"solana-rpc:drift:{market['pubkey']}"
            sources = {f"protocol_metrics.{key}": source for key in metrics}
            sources["protocol_metrics.deployment_state"] = (
                "local:no-independent-current-lifecycle-verification"
            )
            sources["protocol_metrics.adapter_execution_state"] = "local:metadata-only-adapter"
            result.append(
                MarketSnapshot(
                    instrument_id=instrument.id,
                    venue=self.venue,
                    source=source,
                    valid=False,
                    protocol_metrics=metrics,
                    field_sources=sources,
                    field_timestamps={key: None for key in sources},
                    issues=[
                        "original_drift_current_lifecycle_unverified",
                        "metadata_only_not_live_trading",
                        "rpc_slot_is_not_a_price_event_timestamp",
                        "successor_velocity_is_a_separate_program",
                    ],
                )
            )
        return result

    async def orderbook(self, instrument):
        raise UnsupportedMarketData(
            "Drift metadata-only adapter has no validated executable order book; current lifecycle is unverified"
        )


def build_dex_adapters():
    adapters = {"hyperliquid": HyperliquidAdapter(), "lighter": LighterAdapter(), "dydx": DydxAdapter()}
    adapters.update({venue: CCXTPerpetualAdapter(venue) for venue in ("paradex", "aster")})
    adapters.update(
        edgex=EdgeXAdapter(),
        variational=VariationalAdapter(),
        gmx=GMXAdapter(),
        drift=DriftAdapter(),
        **{"jupiter-perps": JupiterPerpsAdapter()},
    )
    adapters.update(
        {
            venue: AggregatedSpotAdapter(venue)
            for venue in ("uniswap", "pancakeswap", "aerodrome", "curve", "raydium", "orca")
        }
    )
    return adapters


def coverage():
    blocked = "Development-time live verification unavailable under network policy (2026-11-04); see runtime status separately"
    rows = []
    docs = {
        "hyperliquid": "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint",
        "lighter": "https://apidocs.lighter.xyz/",
        "dydx": "https://docs.dydx.xyz/",
        "paradex": "https://docs.paradex.trade/",
        "aster": "https://github.com/asterdex/api-docs",
    }
    for venue in ("hyperliquid", "lighter", "dydx", "paradex", "aster"):
        limits = [blocked, ("Native public WebSocket plus REST; no complete historical/depth recovery"
                           if venue in {"hyperliquid", "dydx"} else "REST polling; no native source stream recovery yet")]
        if venue == "lighter":
            limits.append("Funding raw fraction retained; endpoint period unverified, normalization disabled")
        if venue == "aster":
            limits.append(
                "Funding normalization requires returned per-market interval; no fixed 8h assumption"
            )
        rows.append(
            CoverageEntry(
                venue=venue,
                category="perpetual-dex",
                products=["perpetual"],
                method="native public WebSocket + REST" if venue in {"hyperliquid", "dydx"} else "public REST",
                capabilities=["market-discovery", "price", "orderbook", "funding", "full-catalog-rotation"],
                requirements=["Public API network access"]
                + (["Compatible CCXT version"] if venue in ("paradex", "aster") else []),
                limitations=limits,
                status="implemented-unverified-live",
                docs_url=docs[venue],
            )
        )
    for venue, method, capabilities, limitations, url in [
        (
            "edgex",
            "public REST v2",
            ["market-discovery", "price", "orderbook", "funding", "open-interest", "volume"],
            ["No ticker event timestamps; funding timestamp preserved separately"],
            "https://github.com/edgex-org/edgex-gitbook/tree/main/api-v2",
        ),
        (
            "variational",
            "public REST v2 statistics / RFQ",
            ["market-discovery", "mark", "funding", "open-interest", "volume", "indicative-rfq-samples"],
            [
                "RFQ samples may be cached up to 600s and are not executable quotes",
                "Swap products have asymmetric funding and are excluded",
            ],
            "https://docs.variational.io/technical-documentation/api",
        ),
        (
            "gmx",
            "public REST Oracle API, Arbitrum V2",
            ["market-discovery", "indicative-mark", "open-interest-usd"],
            [
                "No executable prices, funding or borrowing normalization; fees require side/collateral-specific state",
                "Arbitrum V2 only",
            ],
            "https://docs.gmx.io/docs/api/rest-api/markets/",
        ),
    ]:
        rows.append(
            CoverageEntry(
                venue=venue,
                category="perpetual-dex",
                products=["perpetual"],
                method=method,
                chains=["arbitrum"] if venue == "gmx" else [],
                capabilities=capabilities,
                requirements=["Public API network access"],
                limitations=[blocked, *limitations],
                status="implemented-partial-unverified-live",
                docs_url=url,
            )
        )
    rows.append(
        CoverageEntry(
            venue="jupiter-perps",
            category="perpetual-dex",
            products=["perpetual"],
            chains=["solana"],
            method="public read-only Perps REST /jlp-info /pool-info /market-stats",
            capabilities=[
                "custody-discovery",
                "reference-price",
                "side-specific-borrow-metrics",
                "custody-state",
            ],
            requirements=["perps-api.jup.ag network access"],
            status="implemented-partial-unverified-live",
            limitations=[
                blocked,
                "Reference price has no documented last/mark/index identity; retained in protocol_metrics",
                "Borrow fees are not funding; endpoint does not explicitly declare borrow rate period",
                "Supported market mint enum bounds discovery; no executable quotes or full OI",
            ],
            docs_url="https://perps-api.jup.ag/v1/docs",
        )
    )
    rows.append(
        CoverageEntry(
            venue="drift",
            category="perpetual-dex",
            products=["perpetual"],
            chains=["solana"],
            method="optional Solana RPC original program metadata audit",
            capabilities=["inactive-market-discovery", "program-state", "market-status"],
            requirements=["SOLANA_RPC_URL for original-program metadata only"],
            status="metadata-only-unverified-lifecycle",
            limitations=[
                blocked,
                "Original identity/layout verified from drift-labs sources; current lifecycle lacks independent verification",
                "Inactive marks local adapter execution unavailability; decoded chain status flags remain separate",
                "Invalid metadata-only snapshots contain no prices/funding/executable books",
                "IDL and deployment metadata verified from official source, live RPC not verified",
            ],
            docs_url="https://github.com/drift-labs/protocol-v2/blob/master/sdk/src/config.ts",
        )
    )
    registry = deployments()
    for venue in ("uniswap", "pancakeswap", "aerodrome", "curve", "raydium", "orca"):
        deployments_for_venue = [d for d in registry if d["protocol"] == venue]
        rows.append(
            CoverageEntry(
                venue=venue,
                category="spot-dex",
                products=["pool"],
                chains=sorted({d["chain"] for d in deployments_for_venue}),
                method=("DEX Screener indicative discovery + optional addressed native RPC"
                        if venue in {"uniswap", "pancakeswap", "aerodrome"} else
                        "DEX Screener aggregated discovery/indicative pool prices"),
                capabilities=[
                    "bounded-pool-discovery",
                    "indicative-price",
                    "volume-usd",
                    "liquidity-usd",
                    "token-addresses",
                ],
                requirements=[
                    "api.dexscreener.com network access",
                    "RPC/quoter required for executable quotes",
                ],
                limitations=[
                    blocked,
                    "search_limited: configurable protocol/chain queries and known pool addresses; not a complete pool index",
                    "Strict version labels may omit valid pools",
                    "Aggregated observations lack native block/membership evidence; addressed RPC reader separately supports Uniswap V3, Pancake V2 and Aerodrome v1; no executable route provider",
                    "Deployment source documents verified; deployed bytecode not checked",
                ],
                status="aggregated-partial-unverified-live",
                docs_url=deployments_for_venue[0]["source_url"],
            )
        )
    return rows
