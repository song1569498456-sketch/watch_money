"""Deterministic public API schema fixtures; these do not claim live API verification."""

import base64
import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from watch_money.adapters.dex import (
    AggregatedSpotAdapter,
    CCXTPerpetualAdapter,
    DriftAdapter,
    DydxAdapter,
    EdgeXAdapter,
    GMXAdapter,
    HyperliquidAdapter,
    JupiterPerpsAdapter,
    LighterAdapter,
    UnsupportedMarketData,
    VariationalAdapter,
    base58_encode,
    build_dex_adapters,
    coverage,
    deployments,
    interval_hours,
    number,
    timestamp,
)


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def response(data):
    return httpx.Response(200, json=data)


@pytest.mark.parametrize("value", [None, "", "nan", "inf", True, "garbage"])
def test_missing_or_invalid_numbers_never_become_zero(value):
    assert number(value) is None


def test_zero_funding_and_sign_are_preserved():
    assert number("0") == 0
    assert number("-0.0001") == -0.0001
    assert number("0", positive=True) is None
    assert number("0", nonnegative=True) == 0


def test_timestamp_and_interval_units():
    assert timestamp(1700000000) == 1700000000000
    assert timestamp("1700000000000") == 1700000000000
    assert timestamp("2023-11-14T22:13:20Z") == 1700000000000
    assert timestamp("2023-11-14T22:13:20") is None
    assert interval_hours("240m") == 4
    assert interval_hours("0h") is None
    assert interval_hours(None) is None


def test_factory_registry_covers_exact_planned_venues_without_connecting(monkeypatch):
    def no_client(*args, **kwargs):
        raise AssertionError("Construction must not create transport clients")

    monkeypatch.setattr(httpx, "AsyncClient", no_client)
    adapters = build_dex_adapters()
    assert set(adapters) == {
        "hyperliquid",
        "lighter",
        "edgex",
        "dydx",
        "drift",
        "gmx",
        "jupiter-perps",
        "aster",
        "paradex",
        "variational",
        "uniswap",
        "pancakeswap",
        "aerodrome",
        "curve",
        "raydium",
        "orca",
    }
    entries = coverage()
    assert {r.venue for r in entries} == set(adapters)
    assert all(r.last_verified_at is None for r in entries)
    assert not adapters["uniswap"].discovery_complete


def test_deployments_are_actual_scoped_source_verified_records():
    rows = deployments()
    assert {d["protocol"] for d in rows} == {
        "uniswap",
        "pancakeswap",
        "aerodrome",
        "curve",
        "raydium",
        "orca",
    }
    assert {d["chain"] for d in rows} == {"ethereum", "arbitrum", "base", "bsc", "solana"}
    assert len({(d["protocol"], d["chain"], d["version"]) for d in rows}) == len(rows)
    assert all(len(d["source_sha256"]) == 64 and not d["onchain_verified"] for d in rows)
    # A common but unsafe assumption: Uniswap Base does not use the Ethereum V3 factory.
    factories = {d["chain"]: d["address"] for d in rows if d["protocol"] == "uniswap"}
    assert factories["base"] != factories["ethereum"]


@pytest.mark.asyncio
async def test_hyperliquid_builder_namespace_collateral_decimals_and_hourly_fraction():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        kind, dex = body["type"], body.get("dex", "")
        if kind == "perpDexs":
            return response([None, {"name": "xyz"}])
        if kind == "spotMeta":
            return response({"tokens": [{"index": 0, "name": "USDC"}, {"index": 7, "name": "USDH"}]})
        meta = {
            "collateralToken": 7 if dex else 0,
            "universe": [{"name": "xyz:BTC" if dex else "BTC", "szDecimals": 3}],
        }
        if kind == "meta":
            return response(meta)
        if kind == "metaAndAssetCtxs":
            return response(
                [
                    meta,
                    [
                        {
                            "markPx": "100",
                            "oraclePx": "99",
                            "funding": "-0.0001",
                            "openInterest": "2",
                            "dayNtlVlm": "1000",
                            "impactPxs": ["98", "102"],
                        }
                    ],
                ]
            )
        if kind == "l2Book":
            assert body["coin"] == "xyz:BTC"
            return response(
                {
                    "time": 1700000000000,
                    "levels": [[{"px": "99", "sz": "0.5"}], [{"px": "101", "sz": "0.25"}]],
                }
            )
        raise AssertionError(body)

    async with mock_client(handler) as client:
        adapter = HyperliquidAdapter(client)
        instruments = await adapter.discover()
        assert [i.quote for i in instruments] == ["USDC", "USDH"]
        assert instruments[1].native_symbol == "xyz:BTC"
        assert instruments[0].asset_id != instruments[1].asset_id
        assert instruments[1].amount_precision == 0.001
        assert instruments[1].price_precision is None
        snapshots = await adapter.fetch(instruments)
        assert snapshots[1].funding_rate == -0.0001
        assert snapshots[1].funding_interval_hours == 1
        assert snapshots[1].volume_unit == "USDH"
        assert snapshots[1].source_at is None
        assert snapshots[1].last is None and snapshots[1].bid is None  # impactPx is NOT best bid.
        book = await adapter.orderbook(instruments[1])
        assert book.bids == [(99.0, 0.5)] and book.quantity_unit == "base"
        assert book.source_at == 1700000000000
    assert any(c == {"type": "metaAndAssetCtxs", "dex": "xyz"} for c in calls)


@pytest.mark.asyncio
async def test_hyperliquid_mismatched_contexts_fail_not_partial_fake_data():
    async with mock_client(lambda r: response([{"universe": [{"name": "BTC"}]}, []])) as client:
        adapter = HyperliquidAdapter(client)
        from watch_money.models import Instrument

        instrument = Instrument(
            id="h:BTC",
            venue="hyperliquid",
            native_symbol="BTC",
            asset_id="h:BTC",
            base="BTC",
            quote="USDC",
            kind="perpetual",
        )
        with pytest.raises(ValueError, match="mismatched"):
            await adapter.fetch([instrument])


LIGHTER_MARKET = {
    "market_id": 0,
    "market_type": "perp",
    "symbol": "ETH",
    "status": "active",
    "supported_size_decimals": 4,
    "supported_price_decimals": 2,
    "min_quote_amount": "5",
    "last_trade_price": 2000.5,
    "daily_quote_token_volume": 10000,
    "open_interest": 3,
    "mark_price": "2001",
    "index_price": "2000",
}


@pytest.mark.asyncio
async def test_lighter_decimals_funding_exchange_filter_and_unknown_period():
    def handler(request):
        if request.url.path.endswith("orderBookDetails"):
            return response(
                {
                    "code": 200,
                    "order_book_details": [
                        LIGHTER_MARKET,
                        {**LIGHTER_MARKET, "market_id": 1, "market_type": "binary_options"},
                    ],
                }
            )
        return response(
            {
                "code": 200,
                "funding_rates": [
                    {"market_id": 0, "exchange": "lighter", "rate": 0.000096},
                    {"market_id": 0, "exchange": "binance", "rate": 0.5},
                ],
            }
        )

    async with mock_client(handler) as client:
        adapter = LighterAdapter(client)
        instruments = await adapter.discover()
        assert len(instruments) == 1
        assert instruments[0].amount_precision == 0.0001
        assert instruments[0].price_precision == 0.01
        snapshots = await adapter.fetch(instruments)
        assert snapshots[0].last == 2000.5
        assert snapshots[0].funding_rate == 0.000096
        assert snapshots[0].funding_interval_hours is None
        assert snapshots[0].next_funding_at is None
        assert "funding_period_unverified" in snapshots[0].issues
        assert snapshots[0].open_interest == 3 and snapshots[0].oi_unit == "ETH"


@pytest.mark.asyncio
async def test_lighter_book_aggregates_unfilled_human_decimal_sizes():
    def handler(request):
        if request.url.path.endswith("orderBookDetails"):
            return response({"order_book_details": [LIGHTER_MARKET]})
        return response(
            {
                "code": 200,
                "bids": [
                    {"price": "1999.50", "remaining_base_amount": "0.1"},
                    {"price": "1999.50", "remaining_base_amount": "0.2"},
                ],
                "asks": [{"price": "2001.00", "remaining_base_amount": "0.01"}],
            }
        )

    async with mock_client(handler) as client:
        adapter = LighterAdapter(client)
        instrument = (await adapter.discover())[0]
        book = await adapter.orderbook(instrument)
        assert book.bids == [(1999.5, 0.3)]
        assert book.asks == [(2001.0, 0.01)]
        assert book.source_at is None


@pytest.mark.asyncio
async def test_api_error_is_propagated():
    async with mock_client(lambda request: httpx.Response(429, json={"error": "limit"})) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await LighterAdapter(client).discover()
    async with mock_client(lambda request: response({"code": 500, "order_book_details": []})) as client:
        with pytest.raises(RuntimeError, match="code 500"):
            await LighterAdapter(client).discover()


def ccxt_fixture(venue="paradex", multiplier=1):
    market = {
        "symbol": "BTC/USD:USDC",
        "id": "BTC-USD-PERP",
        "swap": True,
        "linear": True,
        "base": "BTC",
        "quote": "USD",
        "settle": "USDC",
        "active": True,
        "contractSize": multiplier,
        "info": {"funding_period_hours": "24"},
    }
    client = type("FixtureClient", (), {})()
    client.has = {"fetchTickers": True, "fetchFundingRates": True}
    client.load_markets = AsyncMock(return_value={market["symbol"]: market})
    client.fetch_tickers = AsyncMock(
        return_value={
            market["symbol"]: {"last": 100, "timestamp": 1700000000000, "quoteVolume": 50, "info": {}}
        }
    )
    client.fetch_funding_rates = AsyncMock(
        return_value={market["symbol"]: {"fundingRate": -0.002, "timestamp": 1699990000000}}
    )
    client.close = AsyncMock()
    return CCXTPerpetualAdapter(venue, client)


@pytest.mark.asyncio
async def test_paradex_market_specific_period_and_separate_funding_time():
    adapter = ccxt_fixture()
    instruments = await adapter.discover()
    snapshot = (await adapter.fetch(instruments))[0]
    assert snapshot.funding_interval_hours == 24
    assert snapshot.field_timestamps["funding_rate"] == 1699990000000
    assert snapshot.source_at == 1700000000000
    assert snapshot.volume_unit == "USD"


@pytest.mark.asyncio
async def test_unknown_contract_multiplier_is_not_assumed():
    assert await ccxt_fixture(multiplier=None).discover() == []
    assert await ccxt_fixture(multiplier=0).discover() == []


@pytest.mark.asyncio
async def test_aster_fetches_market_funding_interval_instead_of_eight_hour_default():
    adapter = ccxt_fixture("aster")
    adapter.client.has["fetchFundingIntervals"] = True
    adapter.client.fetch_funding_intervals = AsyncMock(return_value={"BTC/USD:USDC": {"interval": "4h"}})
    snapshot = (await adapter.fetch(await adapter.discover()))[0]
    assert snapshot.funding_interval_hours == 4


@pytest.mark.asyncio
async def test_dydx_next_rate_is_predicted_hourly_not_last_settlement():
    adapter = ccxt_fixture("dydx")
    adapter.client.has["fetchFundingRates"] = False
    adapter.client.fetch_tickers.return_value["BTC/USD:USDC"]["info"] = {
        "oraclePrice": "99",
        "openInterest": "7",
        "nextFundingRate": "0.0001",
    }
    snapshot = (await adapter.fetch(await adapter.discover()))[0]
    assert snapshot.funding_kind == "predicted" and snapshot.funding_interval_hours == 1
    assert snapshot.index == 99 and snapshot.open_interest == 7


def spot_pair(**changes):
    return {
        "chainId": "ethereum",
        "dexId": "uniswap",
        "labels": ["v3"],
        "pairAddress": "0xABC",
        "priceNative": "2000",
        "priceUsd": "2100",
        "baseToken": {"address": "0xETH", "symbol": "WETH"},
        "quoteToken": {"address": "0xUSDC", "symbol": "USDC"},
        "liquidity": {"usd": 12345},
        "volume": {"h24": 999},
        **changes,
    }


@pytest.mark.asyncio
async def test_spot_keeps_underlying_route_native_quote_and_no_execution_liquidity():
    pair = spot_pair()
    async with mock_client(lambda request: response({"pairs": [pair]})) as client:
        adapter = AggregatedSpotAdapter("uniswap", client, search_queries=["ETH"])
        instruments = await adapter.discover()
        assert instruments[0].id == "uniswap:ethereum:v3:0xabc"
        assert instruments[0].asset_id == "ethereum:0xeth"
        snapshot = (await adapter.fetch(instruments))[0]
        assert snapshot.last == 2000  # native quote, not priceUsd
        assert snapshot.volume_unit == "USD"
        assert snapshot.bid is None and snapshot.ask is None
        assert snapshot.source.startswith("aggregated:")
        assert snapshot.field_sources["route"] == "ethereum/uniswap/v3/0xabc"
        assert not adapter.pools[instruments[0].id].verified
        assert adapter.pools[instruments[0].id].liquidity_usd == 12345
        with pytest.raises(UnsupportedMarketData, match="indicative"):
            await adapter.orderbook(instruments[0])


@pytest.mark.parametrize(
    "changes",
    [{"chainId": "solana"}, {"labels": ["v4"]}, {"labels": []}, {"labels": None}, {"dexId": "pancakeswap"}],
)
def test_spot_rejects_unverified_deployment_version_instead_of_guessing(changes):
    assert AggregatedSpotAdapter("uniswap").register(spot_pair(**changes)) is None


def test_solana_asset_addresses_remain_case_sensitive():
    adapter = AggregatedSpotAdapter("orca")
    pair = spot_pair(
        chainId="solana",
        dexId="orca",
        labels=["whirlpools"],
        pairAddress="AbCd",
        baseToken={"address": "SoLAbC", "symbol": "SOL"},
    )
    instrument = adapter.register(pair)
    assert instrument.native_symbol == "AbCd" and instrument.asset_id == "solana:SoLAbC"


@pytest.mark.asyncio
async def test_edgex_v2_latest_funding_period_and_timestamp_are_separate_from_ticker():
    def handler(request):
        if request.url.path.endswith("getMetaData"):
            data = {
                "coinList": [{"coinId": "1", "coinName": "BTC"}, {"coinId": "2", "coinName": "USDC"}],
                "contractList": [
                    {
                        "contractId": "10",
                        "baseCoinId": "1",
                        "quoteCoinId": "2",
                        "enableTrade": True,
                        "tickSize": "0.1",
                        "stepSize": ".001",
                        "fundingRateIntervalMin": "240",
                    }
                ],
            }
        elif request.url.path.endswith("getTicker"):
            data = [
                {
                    "contractId": "10",
                    "lastPrice": "100",
                    "markPrice": "101",
                    "indexPrice": "99",
                    "fundingRate": ".9",
                    "value": "300",
                    "endTime": "1700000000000",
                }
            ]
        else:
            data = [
                {
                    "contractId": "10",
                    "fundingRate": "-0.0001",
                    "fundingRateIntervalMin": "240",
                    "fundingTimestamp": "1699999000000",
                    "isSettlement": False,
                }
            ]
        return response({"code": "SUCCESS", "data": data})

    async with mock_client(handler) as client:
        adapter = EdgeXAdapter(client)
        snapshot = (await adapter.fetch(await adapter.discover()))[0]
        assert snapshot.funding_rate == -0.0001 and snapshot.funding_interval_hours == 4
        assert snapshot.field_timestamps["funding_rate"] == 1699999000000
        assert snapshot.field_timestamps["mark"] is None
        assert snapshot.source_at is None


@pytest.mark.asyncio
async def test_variational_v2_interval_not_annualized_and_swaps_are_excluded():
    body = {
        "listings": [
            {
                "ticker": "BTC-PERP",
                "contract_type": "perpetual",
                "mark_price": "100",
                "volume_24h": "5",
                "open_interest": {"long_open_interest": "10", "short_open_interest": "20"},
                "funding": {
                    "last": {"annualized_rate": ".1", "interval_rate": "-.0003"},
                    "interval_seconds": 14400,
                    "next_timestamp": 1700000000,
                },
                "quotes": {"updated_at": "2023-11-14T22:13:20Z", "size_1k": {"bid": "99", "ask": "101"}},
            },
            {"ticker": "XAU-SWAP", "contract_type": "swap", "funding": None},
        ]
    }
    async with mock_client(lambda request: response(body)) as client:
        adapter = VariationalAdapter(client)
        instruments = await adapter.discover()
        assert len(instruments) == 1 and instruments[0].mechanism == "rfq"
        snapshot = (await adapter.fetch(instruments))[0]
        assert snapshot.funding_rate == -0.0003 and snapshot.funding_interval_hours == 4
        assert snapshot.open_interest == 30 and snapshot.bid is None
        assert snapshot.next_funding_at == 1700000000000
        assert json.loads(snapshot.protocol_metrics["indicative_rfq_quotes_json"])["size_1k"]["bid"] == "99"
        assert snapshot.source_at is None  # cached quote time cannot freshen mark/funding


@pytest.mark.asyncio
async def test_gmx_v2_fixed30_mark_and_usd_oi_are_not_executable_book():
    index = "0xindex"

    def handler(request):
        if request.url.path == "/markets":
            return response({"markets": [{"marketToken": "0xmarket", "indexToken": index}]})
        if request.url.path == "/tokens":
            return response({"tokens": [{"address": index, "symbol": "ETH", "decimals": 18}]})
        assert request.url.path == "/v1/markets/tickers"
        return response(
            [
                {
                    "marketTokenAddress": "0xmarket",
                    "markPrice": str(2000 * 10**30),
                    "longInterestUsd": str(10 * 10**30),
                    "shortInterestUsd": str(20 * 10**30),
                    "fundingRateLong": "5",
                }
            ]
        )

    async with mock_client(handler) as client:
        adapter = GMXAdapter(client)
        instruments = await adapter.discover()
        snapshot = (await adapter.fetch(instruments))[0]
        assert snapshot.mark == 2000 and snapshot.open_interest == 30
        assert snapshot.funding_rate is None and snapshot.bid is None
        assert snapshot.protocol_metrics["raw_fundingRateLong"] == "5"
        assert instruments[0].settle is None
        with pytest.raises(UnsupportedMarketData):
            await adapter.orderbook(instruments[0])


@pytest.mark.asyncio
async def test_metadata_only_drift_without_rpc_fails_explicitly(monkeypatch):
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    adapter = DriftAdapter()
    with pytest.raises(UnsupportedMarketData, match="metadata-only adapter requires SOLANA_RPC_URL"):
        await adapter.discover()


@pytest.mark.asyncio
async def test_native_dydx_preserves_usd_quote_and_predicted_rate():
    market = {
        "ticker": "BTC-USD",
        "status": "ACTIVE",
        "oraclePrice": "100",
        "volume24H": "300",
        "openInterest": "2",
        "nextFundingRate": "0.0001",
        "tickSize": ".1",
        "stepSize": ".001",
    }

    def handler(request):
        if request.url.path.endswith("perpetualMarkets"):
            return response({"markets": {"BTC-USD": market}})
        assert request.url.path.endswith("/orderbooks/perpetualMarket/BTC-USD")
        return response({"bids": [{"price": "99", "size": ".5"}], "asks": [{"price": "101", "size": ".1"}]})

    async with mock_client(handler) as client:
        adapter = DydxAdapter(client)
        instruments = await adapter.discover()
        assert instruments[0].quote == "USD" and instruments[0].settle == "USDC"
        snapshot = (await adapter.fetch(instruments))[0]
        assert snapshot.index == 100 and snapshot.last is None
        assert snapshot.funding_kind == "predicted" and snapshot.funding_interval_hours == 1
        book = await adapter.orderbook(instruments[0])
        assert book.quantity_unit == "base" and book.bids == [(99, 0.5)]


@pytest.mark.asyncio
async def test_jupiter_public_state_preserves_borrow_semantics_without_fake_funding_or_mark():
    mint = "So11111111111111111111111111111111111111112"
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("jlp-info"):
            return response(
                {
                    "custodies": [
                        {
                            "mint": mint,
                            "pubkey": "CustodyPubkey",
                            "symbol": "SOL",
                            "aumUsd": "123456789",
                            "guaranteedUsd": "3000000",
                            "globalShortSizes": "2000000",
                            "owned": "9007199254740993",
                            "locked": "100",
                        },
                        {"mint": "StableMint", "symbol": "USDC"},
                    ]
                }
            )
        assert request.url.params["mint"] == mint
        if request.url.path.endswith("market-stats"):
            return response({"price": "123.45", "volume": "9000"})
        assert request.url.path.endswith("pool-info")
        return response(
            {
                "longBorrowRatePercent": "0.0026",
                "shortBorrowRatePercent": "0",
                "longUtilizationPercent": "26",
                "shortUtilizationPercent": "10",
                "longAvailableLiquidity": "2500000",
            }
        )

    async with mock_client(handler) as client:
        adapter = JupiterPerpsAdapter(client)
        instruments = await adapter.discover()
        assert len(instruments) == 1 and instruments[0].asset_id == f"solana:{mint}"
        assert instruments[0].quote == "USD" and instruments[0].settle is None
        snapshot = (await adapter.fetch(instruments))[0]
        assert snapshot.funding_rate is None and snapshot.funding_interval_hours is None
        assert snapshot.mark is None and snapshot.last is None and snapshot.open_interest is None
        metrics = snapshot.protocol_metrics
        assert metrics["reference_price_usd"] == 123.45
        assert metrics["long_borrow_rate_fraction"] == pytest.approx(0.000026)
        assert metrics["short_borrow_rate_fraction"] == 0
        assert metrics["short_open_interest_usd"] == 2
        assert metrics["long_position_size_minus_collateral_usd"] == 3
        assert metrics["custody_owned_token_raw"] == "9007199254740993"
        assert metrics["long_available_liquidity_raw"] == "2500000"
        assert snapshot.source_at is None
        assert snapshot.field_timestamps["protocol_metrics.reference_price_usd"] is None
        with pytest.raises(UnsupportedMarketData, match="pool based"):
            await adapter.orderbook(instruments[0])
    assert all(method == "GET" for method, _ in calls)
    assert {path.rsplit("/", 1)[-1] for _, path in calls} == {"jlp-info", "market-stats", "pool-info"}


def drift_rpc_fixture(
    *,
    wrong_owner=False,
    wrong_discriminator=False,
    truncated=False,
    growth=False,
    exchange_status_bits=255,
    market_status=4,
):
    """Original SDK IDL + Rust layout fixture; explicitly independent account byte positions."""
    pubkey = "11111111111111111111111111111111"
    state = bytearray(992)
    state[:8] = hashlib.sha256(b"account:State").digest()[:8]
    state[966:968] = (1).to_bytes(2, "little")
    state[974] = exchange_status_bits
    market = bytearray(1216 + (64 if growth else 0))
    market[:8] = hashlib.sha256(b"account:PerpMarket").digest()[:8]
    market[1000:1008] = b"SOL-PERP"
    market[1160:1162] = (0).to_bytes(2, "little")
    market[1162] = market_status  # Original Drift MarketStatus enum (Velocity ordering differs).
    market[1163] = 0  # ContractType.Perpetual.
    market[1165] = 255
    if wrong_discriminator:
        market[:8] = b"bad-data"
    if truncated:
        market = market[:1009]

    def handler(request):
        payload = json.loads(request.content)
        assert payload["method"] == "getProgramAccounts"
        assert payload["params"][0] == DriftAdapter.program_id
        params = payload["params"][1]
        assert params["withContext"] and "dataSize" not in json.dumps(params["filters"])
        wanted = params["filters"][0]["memcmp"]["bytes"]
        data = state if wanted == base58_encode(state[:8]) else market
        owner = "vELoC1audYbSYVRXn1vPaV8Axoa9oU6BYmNGZZBDZ1P" if wrong_owner else DriftAdapter.program_id
        return response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "context": {"slot": 300000000},
                    "value": [
                        {
                            "pubkey": pubkey,
                            "account": {
                                "owner": owner,
                                "executable": False,
                                "data": [base64.b64encode(data).decode(), "base64"],
                            },
                        }
                    ],
                },
            }
        )

    return handler


@pytest.mark.asyncio
async def test_original_drift_rpc_metadata_is_inactive_without_inheriting_successor_prices():
    async with mock_client(drift_rpc_fixture(growth=True)) as client:
        adapter = DriftAdapter(client, rpc_url="https://configured-rpc.invalid/private-token")
        instruments = await adapter.discover()
        assert len(instruments) == 1 and not instruments[0].active
        assert instruments[0].base == "SOL" and instruments[0].asset_id == "drift:market-index:0"
        snapshot = (await adapter.fetch(instruments))[0]
        assert not snapshot.valid and snapshot.mark is None and snapshot.index is None
        assert snapshot.funding_rate is None
        assert snapshot.protocol_metrics["market_status"] == "FillPaused"
        assert snapshot.protocol_metrics["exchange_status_bits"] == 255
        assert snapshot.protocol_metrics["rpc_context_slot"] == "300000000"
        assert "private-token" not in snapshot.model_dump_json()
        assert snapshot.source_at is None
        with pytest.raises(UnsupportedMarketData, match="metadata-only adapter"):
            await adapter.orderbook(instruments[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["wrong_owner", "wrong_discriminator", "truncated"])
async def test_original_drift_rpc_rejects_wrong_program_layout_or_truncated_accounts(failure):
    async with mock_client(drift_rpc_fixture(**{failure: True})) as client:
        adapter = DriftAdapter(client, rpc_url="https://configured-rpc.invalid")
        with pytest.raises(ValueError):
            await adapter.discover()


@pytest.mark.asyncio
async def test_drift_rpc_errors_are_not_market_data_and_do_not_leak_rpc_credentials():
    async with mock_client(
        lambda request: response(
            {"jsonrpc": "2.0", "error": {"code": -32005, "message": "sensitive upstream context"}}
        )
    ) as client:
        adapter = DriftAdapter(client, rpc_url="https://configured-rpc.invalid/private-token")
        with pytest.raises(RuntimeError, match="-32005") as error:
            await adapter.discover()
        assert "private-token" not in str(error.value) and "sensitive" not in str(error.value)
    async with mock_client(lambda request: httpx.Response(403)) as client:
        adapter = DriftAdapter(client, rpc_url="https://configured-rpc.invalid/private-token")
        with pytest.raises(RuntimeError) as error:
            await adapter.discover()
        assert "private-token" not in str(error.value)


def test_original_drift_source_registry_and_base58_vectors():
    adapter = DriftAdapter(rpc_url="https://configured-rpc.invalid")
    assert adapter.layout["PerpMarket"]["source_account_bytes"] == 1216
    assert adapter.layout["State"]["source_account_bytes"] == 992
    assert len(adapter.layout["source_sha256"]) == 64
    assert base58_encode(bytes(32)) == "1" * 32
    assert base58_encode(bytes([1])) == "2"
    assert base58_encode(bytes([255])) == "5Q"


@pytest.mark.asyncio
async def test_drift_local_execution_unavailability_is_not_a_claim_of_onchain_pause():
    async with mock_client(drift_rpc_fixture(exchange_status_bits=0, market_status=1)) as client:
        adapter = DriftAdapter(client, rpc_url="https://configured-rpc.invalid")
        instruments = await adapter.discover()
        snapshot = (await adapter.fetch(instruments))[0]
        assert not instruments[0].active and not snapshot.valid
        assert snapshot.protocol_metrics["deployment_state"] == "unverified-lifecycle"
        assert snapshot.protocol_metrics["adapter_execution_state"] == "metadata-only-non-executable"
        assert snapshot.protocol_metrics["market_status"] == "Active"
        assert snapshot.protocol_metrics["exchange_status_bits"] == 0
        assert "original_drift_current_lifecycle_unverified" in snapshot.issues
        row = next(c for c in coverage() if c.venue == "drift")
        assert row.status == "metadata-only-unverified-lifecycle"
