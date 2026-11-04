import asyncio

import pytest
from watch_money.adapters.cex import SPECS, CexAdapter
from watch_money.adapters.streaming import (
    CexStreamAdapter,
    StreamUnavailable,
    _stream_client_factory,
    create_stream,
    ticker_patch,
)
from watch_money.models import Instrument

EVENT = 1_800_000_000_000


def instrument(symbol="BTC/USDT", *, client="okx", kind="spot", settle=None, linear=True):
    base, quote = symbol.split(":")[0].split("/")
    return Instrument(id=f"cex:okx:{client}:{kind}:{symbol}", venue="okx", native_symbol=symbol,
                      asset_id=base, base=base, quote=quote, kind=kind, settle=settle, linear=linear)


class RestClient:
    def __init__(self, client_id):
        self.id = client_id


def rest_adapter(instruments, *, venue="okx"):
    adapter = CexAdapter(venue, client_factory=RestClient)
    for item, client in instruments:
        adapter._markets[item.id] = (client, {"symbol": item.native_symbol})
    return adapter


class MockStreamClient:
    def __init__(self, *, multiple=True, single=True):
        self.has = {"watchTickers": multiple, "watchTicker": single}
        self.events = asyncio.Queue()
        self.calls = []
        self.closed = False
        self.shared_from = None

    def set_markets_from_exchange(self, client):
        self.shared_from = client.id

    async def watch_tickers(self, symbols):
        self.calls.append(("multiple", list(symbols)))
        while True:
            result = await self.events.get()
            if isinstance(result, BaseException):
                raise result
            if any(symbol in result for symbol in symbols):
                return result
            await self.events.put(result)
            await asyncio.sleep(0)

    async def watch_ticker(self, symbol):
        self.calls.append(("single", symbol))
        result = await self.events.get()
        if isinstance(result, BaseException):
            raise result
        return result.get(symbol, result)

    async def close(self):
        self.closed = True


def test_sparse_patch_never_supplies_unavailable_funding_or_prices():
    item = instrument()
    result = ticker_patch(item, "okx", {"last": 100, "bid": None, "fundingRate": .001,
                                        "baseVolume": 0, "timestamp": EVENT}, "watch_tickers")
    values = result.model_dump(exclude_unset=True)
    assert result.instrument_id == item.id and result.received_at > 0
    assert values["last"] == 100 and values["volume"] == 0 and values["volume_unit"] == "BTC"
    assert "funding_rate" not in values and "bid" not in values and "ask" not in values
    assert result.field_timestamps["last"] == EVENT
    assert "ccxt.pro:okx" in result.field_sources["last"]


def test_no_fabricated_snapshot_and_unknown_timestamp_is_explicit():
    item = instrument()
    assert ticker_patch(item, "okx", {"last": "NaN", "bid": 0, "ask": "Infinity"}, "watch_ticker") is None
    patch = ticker_patch(item, "okx", {"bid": 101, "ask": 100}, "watch_ticker")
    assert not patch.valid and patch.source_at is None
    assert patch.field_timestamps == {"bid": None, "ask": None}
    assert any("receipt time is not source time" in issue for issue in patch.issues)


@pytest.mark.asyncio
async def test_public_stream_uses_discovered_identity_and_cancel_closes_clients():
    item = instrument()
    client = MockStreamClient()
    rest = rest_adapter([(item, "okx")])
    stream = CexStreamAdapter(rest, client_factory=lambda _: client)
    await client.events.put({item.native_symbol: {"bid": 100, "ask": 101, "timestamp": EVENT}})
    generator = stream.stream([item, item])
    patch = await anext(generator)
    assert patch.instrument_id == item.id
    assert client.shared_from == "okx"
    assert client.calls[0] == ("multiple", [item.native_symbol])
    await generator.aclose()
    assert client.closed and not stream._tasks and not stream._clients and not stream._running


@pytest.mark.asyncio
async def test_grouping_separates_products_settlement_and_service_clients():
    spot = instrument()
    linear = instrument("BTC/USDT:USDT", kind="perpetual", settle="USDT")
    inverse = instrument("BTC/USD:BTC", kind="perpetual", settle="BTC", linear=False)
    items = [spot, linear, inverse]
    client = MockStreamClient()
    stream = CexStreamAdapter(rest_adapter([(item, "okx") for item in items]), client_factory=lambda _: client)
    for item in items:
        await client.events.put({item.native_symbol: {"last": 100, "timestamp": EVENT}})
    generator = stream.stream(items)
    patches = [await anext(generator) for _ in items]
    assert {patch.instrument_id for patch in patches} == {item.id for item in items}
    assert all(len(symbols) == 1 for method, symbols in client.calls if method == "multiple")
    await generator.aclose()


@pytest.mark.asyncio
async def test_single_symbol_fallback_and_runtime_unsupported_bulk_fallback():
    item = instrument()
    client = MockStreamClient(multiple=False)
    stream = CexStreamAdapter(rest_adapter([(item, "okx")]), client_factory=lambda _: client)
    await client.events.put({item.native_symbol: {"last": 100}})
    generator = stream.stream([item])
    assert (await anext(generator)).last == 100
    assert client.calls[0] == ("single", item.native_symbol)
    await generator.aclose()

    class NotSupported(Exception):
        pass

    client = MockStreamClient()
    stream = CexStreamAdapter(rest_adapter([(item, "okx")]), client_factory=lambda _: client)
    await client.events.put(NotSupported())
    await client.events.put({item.native_symbol: {"last": 101}})
    generator = stream.stream([item])
    assert (await anext(generator)).last == 101
    assert client.calls[:2] == [("multiple", [item.native_symbol]), ("single", item.native_symbol)]
    await generator.aclose()


@pytest.mark.asyncio
async def test_failure_reaches_root_and_restart_creates_fresh_subscription():
    item = instrument()
    clients = []

    def factory(_):
        client = MockStreamClient()
        clients.append(client)
        if len(clients) == 1:
            client.events.put_nowait(ConnectionError("source disconnected"))
        else:
            client.events.put_nowait({item.native_symbol: {"last": 102, "timestamp": EVENT + 1000}})
        return client

    stream = CexStreamAdapter(rest_adapter([(item, "okx")]), client_factory=factory)
    with pytest.raises(ConnectionError):
        await anext(stream.stream([item]))
    assert clients[0].closed
    generator = stream.stream([item])
    assert (await anext(generator)).last == 102
    assert len(clients) == 2
    await generator.aclose()


@pytest.mark.asyncio
async def test_settings_change_replaces_subscriptions_and_cancellation_cleans_up():
    btc, eth = instrument(), instrument("ETH/USDT")
    clients = []

    def factory(_):
        client = MockStreamClient()
        clients.append(client)
        return client

    stream = CexStreamAdapter(rest_adapter([(btc, "okx"), (eth, "okx")]), client_factory=factory)
    generator = stream.stream([btc])
    task = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clients[0].closed and not stream._running
    generator = stream.stream([eth])
    task = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    await clients[-1].events.put({eth.native_symbol: {"last": 200, "timestamp": EVENT}})
    assert (await task).instrument_id == eth.id
    assert clients[-1].calls[0] == ("multiple", [eth.native_symbol])
    await generator.aclose()


@pytest.mark.asyncio
async def test_rest_only_is_explicit_no_null_stream_values():
    item = instrument()
    client = MockStreamClient(multiple=False, single=False)
    stream = CexStreamAdapter(rest_adapter([(item, "okx")]), client_factory=lambda _: client)
    with pytest.raises(StreamUnavailable, match="use REST"):
        await anext(stream.stream([item]))
    assert stream.unsupported and client.closed


@pytest.mark.asyncio
async def test_duplicate_and_older_events_do_not_undo_newer_price():
    item = instrument()
    client = MockStreamClient()
    for time, price in [(EVENT, 100), (EVENT, 100), (EVENT - 1, 99), (EVENT + 1, 101)]:
        await client.events.put({item.native_symbol: {"last": price, "timestamp": time}})
    stream = CexStreamAdapter(rest_adapter([(item, "okx")]), client_factory=lambda _: client)
    generator = stream.stream([item])
    assert (await anext(generator)).last == 100
    assert (await anext(generator)).last == 101
    await generator.aclose()


def test_factory_excludes_non_cex_and_imports_installed_pro_without_network():
    assert create_stream(object()) is None
    pytest.importorskip("ccxt.pro")
    stream = create_stream(CexAdapter("okx"))
    assert isinstance(stream, CexStreamAdapter) and stream.venue == "okx" and not stream._clients


@pytest.mark.asyncio
async def test_installed_pro_clients_use_verified_tls_inherited_proxy_and_no_account_keys(monkeypatch):
    pytest.importorskip("ccxt.pro")
    monkeypatch.setenv("WSS_PROXY", "http://test-proxy.invalid:8080")
    for spec in SPECS.values():
        for client_id in spec.clients:
            client = _stream_client_factory(client_id)
            assert client.aiohttp_trust_env and client.enableRateLimit and client.newUpdates
            assert client.wssProxy == "http://test-proxy.invalid:8080"
            assert client.ssl_context.verify_mode != 0
            assert not client.apiKey and not client.secret
            assert client.has.get("watchTicker") or client.has.get("watchTickers")
            await client.close()
