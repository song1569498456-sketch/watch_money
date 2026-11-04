"""Catalog breadth and request costs with synthetic public responses, not live evidence."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from watch_money.adapters.dex import (
    AggregatedSpotAdapter,
    CCXTPerpetualAdapter,
    DydxAdapter,
    EdgeXAdapter,
    HyperliquidAdapter,
    JupiterPerpsAdapter,
    LighterAdapter,
    PublicREST,
    VariationalAdapter,
    build_dex_adapters,
)
from watch_money.models import Instrument


@pytest.mark.asyncio
async def test_all_dex_catalog_interfaces_skip_network_for_empty_catalog():
    for adapter in build_dex_adapters().values():
        assert await adapter.fetch_light([]) == []
        assert adapter.light_fetch_mode == "reuse-public-fetch"
        assert adapter.light_fetch_cost
        assert adapter._client is None


@pytest.mark.asyncio
async def test_hyperliquid_broad_catalog_preserves_all_small_assets_and_namespaces():
    calls = []
    symbols = [f"SMALL{index}" for index in range(65)]

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        kind, dex = body["type"], body.get("dex", "")
        if kind == "perpDexs":
            return httpx.Response(200, json=[None, {"name": "builder"}])
        if kind == "spotMeta":
            return httpx.Response(200, json={"tokens": [{"index": 0, "name": "USDC"}]})
        meta = {"collateralToken": 0, "universe": [
            {"name": f"builder:{symbol}" if dex else symbol, "szDecimals": 2} for symbol in symbols
        ]}
        assert kind in {"meta", "metaAndAssetCtxs"}
        return httpx.Response(200, json=meta if kind == "meta" else [
            meta, [{"markPx": "2", "funding": "0"} for _ in symbols]
        ])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = HyperliquidAdapter(client)
        catalog = await adapter.discover()
        assert len(catalog) == 130
        calls.clear()
        snapshots = await adapter.fetch_light(catalog)
        assert {row.instrument_id for row in snapshots} == {item.id for item in catalog}
        assert len(calls) == 2
        assert {body["type"] for body in calls} == {"metaAndAssetCtxs"}
        assert {body["dex"] for body in calls} == {"", "builder"}


@pytest.mark.parametrize("adapter_type", [LighterAdapter, DydxAdapter, VariationalAdapter])
@pytest.mark.asyncio
async def test_native_public_batch_paths_cover_every_discovered_small_asset(adapter_type):
    calls = []
    count = 71

    def handler(request):
        calls.append(request.url.path)
        if adapter_type is LighterAdapter:
            body = {"funding_rates": []} if request.url.path.endswith("funding-rates") else {
                "order_book_details": [{"market_id": index, "symbol": f"SMALL{index}",
                                        "status": "active", "mark_price": "2"} for index in range(count)]
            }
        elif adapter_type is DydxAdapter:
            body = {"markets": {f"SMALL{index}-USD": {"ticker": f"SMALL{index}-USD",
                    "status": "ACTIVE", "oraclePrice": "2"} for index in range(count)}}
        else:
            body = {"listings": [{"ticker": f"SMALL{index}-PERP", "contract_type": "perpetual",
                                  "mark_price": "2"} for index in range(count)]}
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = adapter_type(client)
        catalog = await adapter.discover()
        calls.clear()
        snapshots = await adapter.fetch_light(catalog)
        assert len(catalog) == count
        assert {row.instrument_id for row in snapshots} == {item.id for item in catalog}
        assert len(calls) == (2 if adapter_type is LighterAdapter else 1)
        assert all("depth" not in path.lower() and "orderbookorders" not in path.lower() for path in calls)


@pytest.mark.parametrize("bulk", [True, False])
@pytest.mark.asyncio
async def test_ccxt_broad_fetch_cost_and_optional_recording(bulk):
    symbols = [f"SMALL{index}/USD:USD" for index in range(3)]
    markets = {symbol: {"id": symbol, "symbol": symbol, "base": symbol.split("/")[0], "quote": "USD",
                       "settle": "USD", "swap": True, "linear": True, "contractSize": 1} for symbol in symbols}
    tickers = {symbol: {"last": 2} for symbol in symbols}
    rates = {symbol: {"fundingRate": 0} for symbol in symbols}
    client = SimpleNamespace(
        has={"fetchTickers": bulk, "fetchFundingRates": bulk, "fetchFundingRate": True},
        load_markets=AsyncMock(return_value=markets), fetch_tickers=AsyncMock(return_value=tickers),
        fetch_funding_rates=AsyncMock(return_value=rates),
        fetch_ticker=AsyncMock(side_effect=lambda symbol: tickers[symbol]),
        fetch_funding_rate=AsyncMock(side_effect=lambda symbol: rates[symbol]),
    )
    adapter = CCXTPerpetualAdapter("aster", client)
    catalog = await adapter.discover()
    recorder = AsyncMock(side_effect=OSError("disk unavailable"))
    adapter.on_payload = recorder
    snapshots = await adapter.fetch_light(catalog)
    assert {row.instrument_id for row in snapshots} == {item.id for item in catalog}
    assert recorder.await_count == (2 if bulk else 6)
    assert client.fetch_tickers.await_count == int(bulk)
    assert client.fetch_ticker.await_count == (0 if bulk else 3)
    assert client.fetch_funding_rate.await_count == (0 if bulk else 3)


@pytest.mark.asyncio
async def test_edgex_broad_fetch_exposes_real_one_plus_n_request_cost():
    calls = []
    catalog = [Instrument(id=f"edgex:{index}", venue="edgex", native_symbol=str(index),
                          asset_id=f"small:{index}", base=f"SMALL{index}", quote="USD", kind="perpetual")
               for index in range(4)]

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("getTicker"):
            data = [{"contractId": item.native_symbol, "markPrice": "2"} for item in catalog]
        else:
            assert request.url.path.endswith("getLatestFundingRate")
            data = [{"contractId": request.url.params["contractId"], "fundingRate": "0"}]
        return httpx.Response(200, json={"code": "SUCCESS", "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = EdgeXAdapter(client)
        snapshots = await adapter.fetch_light(catalog)
        assert {row.instrument_id for row in snapshots} == {item.id for item in catalog}
        assert len(calls) == 1 + len(catalog)
        assert "1+N" in adapter.light_fetch_cost


@pytest.mark.asyncio
async def test_jupiter_broad_fetch_retains_published_market_boundary_and_one_plus_two_n_cost():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("jlp-info"):
            return httpx.Response(200, json={"custodies": [
                {"mint": mint, "symbol": symbol} for mint, symbol in JupiterPerpsAdapter.supported_markets.items()
            ] + [{"mint": "UnpublishedMint", "symbol": "SMALL"}]})
        assert request.url.path.endswith(("market-stats", "pool-info"))
        return httpx.Response(200, json={"price": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = JupiterPerpsAdapter(client)
        catalog = await adapter.discover()
        assert len(catalog) == len(adapter.supported_markets)
        assert adapter.discovery_complete is False
        calls.clear()
        snapshots = await adapter.fetch_light(catalog)
        assert {row.instrument_id for row in snapshots} == {item.id for item in catalog}
        assert len(calls) == 1 + 2 * len(catalog)
        assert "1+2N" in adapter.light_fetch_cost
        assert all(row.mark is None and row.funding_rate is None for row in snapshots)


@pytest.mark.asyncio
async def test_public_payload_callback_has_no_request_envelope_and_failure_is_isolated():
    body = {"result": {"context": {"slot": 123}, "value": []}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
        adapter = PublicREST(client)
        adapter.venue = "drift"
        recorder = AsyncMock(side_effect=OSError("disk unavailable"))
        adapter.on_payload = recorder
        result = await adapter.request("POST", "https://rpc.invalid/private-key?token=secret",
                                       headers={"Authorization": "Bearer secret"}, json={"method": "getProgramAccounts"})
        assert result == body
        recorder.assert_awaited_once_with("drift", "rest:public:POST", body)
        assert "secret" not in str(recorder.call_args)
        assert "rpc.invalid" not in str(recorder.call_args)


def pair(address="0xABC", **changes):
    return {"chainId": "ethereum", "dexId": "uniswap", "labels": ["v3"], "pairAddress": address,
            "baseToken": {"address": "0xSMALL", "symbol": "SMALL"},
            "quoteToken": {"address": "0xOTHER", "symbol": "OTHER"}, "priceNative": "2", **changes}


@pytest.mark.asyncio
async def test_spot_default_protocol_queries_include_small_assets_and_remain_search_limited():
    queries = []

    def handler(request):
        queries.append(request.url.params["q"])
        return httpx.Response(200, json={"pairs": [pair(), pair("0xDEF")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = AggregatedSpotAdapter("uniswap", client)
        catalog = await adapter.discover()
        assert len(catalog) == 2 and all(item.base == "SMALL" for item in catalog)
        assert queries == ["uniswap", "uniswap ethereum", "uniswap arbitrum", "uniswap base"]
        assert adapter.discovery_state == "search_limited" and adapter.discovery_complete is False


@pytest.mark.asyncio
async def test_spot_custom_queries_known_pool_sources_and_empty_config_are_respected():
    calls = []

    def handler(request):
        calls.append((request.url.path, request.url.params.get("q")))
        return httpx.Response(200, json={"pairs": [pair()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = AggregatedSpotAdapter("uniswap", client, search_queries=["pool:ethereum:0xABC", "SMALL", "SMALL"])
        assert len(await adapter.discover()) == 1
        assert calls == [("/latest/dex/pairs/ethereum/0xABC", None), ("/latest/dex/search", "SMALL")]
        adapter.search_queries = []
        calls.clear()
        assert await adapter.discover() == []
        assert calls == []
        assert AggregatedSpotAdapter("uniswap", search_queries=[]).search_queries == []


@pytest.mark.parametrize("query", ["", "pool:unknown:abc", "pool:ethereum:a/b", "pool:ethereum:"])
@pytest.mark.asyncio
async def test_invalid_known_pool_sources_do_not_make_requests(query):
    adapter = AggregatedSpotAdapter("uniswap", search_queries=[query])
    with pytest.raises(ValueError):
        await adapter.discover()
    assert adapter._client is None
