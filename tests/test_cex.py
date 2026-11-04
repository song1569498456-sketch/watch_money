"""Synthetic public API fixtures; these are parser tests, not live certification."""
from copy import deepcopy

import pytest
from watch_money.adapters.cex import (
    SPECS,
    CexAdapter,
    _open_interest,
    build_cex_adapters,
    coverage,
    funding_interval,
)

EVENT_MS = 1_800_000_000_000


def market(*, base="BTC", quote="USDT", swap=False, inverse=False, size=1, raw=None):
    return {"id": f"{base}{quote}" + ("-PERP" if swap else ""),
            "symbol": f"{base}/{quote}" + (f":{base if inverse else quote}" if swap else ""),
            "base": base, "baseId": base, "quote": quote, "spot": not swap, "swap": swap,
            "settle": (base if inverse else quote) if swap else None,
            "linear": not inverse if swap else None, "inverse": inverse if swap else None,
            "contractSize": size if swap else None, "active": True, "info": raw or {},
            "precision": {"price": 0.1, "amount": 0.001}, "limits": {"cost": {"min": 5}}}


class FakeClient:
    precisionMode = 4

    def __init__(self, markets):
        self.markets = {m["symbol"]: m for m in markets}
        self.has = {"fetchFundingRate": True, "fetchOpenInterest": True}
        self.ticker = {"timestamp": EVENT_MS, "bid": 100, "ask": 101, "last": 100.5,
                       "baseVolume": 0, "quoteVolume": 123}
        self.funding = {"timestamp": EVENT_MS + 1000, "fundingRate": -0.0002,
                        "fundingTimestamp": EVENT_MS + 3_600_000, "markPrice": 100.4,
                        "indexPrice": 100.1, "info": {}}
        self.interest = {"timestamp": EVENT_MS + 2000, "openInterestAmount": 1500}
        self.book = {"timestamp": EVENT_MS, "bids": [[100, 20], [99, 10]], "asks": [[101, 30]]}
        self.closed = False
        self.fail_funding = None

    async def load_markets(self, reload=False):
        assert reload
        return deepcopy(self.markets)

    async def fetch_ticker(self, symbol):
        assert symbol in self.markets
        return deepcopy(self.ticker)

    async def fetch_funding_rate(self, symbol):
        if self.fail_funding:
            raise self.fail_funding
        return deepcopy(self.funding)

    async def fetch_open_interest(self, symbol):
        return deepcopy(self.interest)

    async def fetch_funding_intervals(self, symbols):
        self.interval_symbols = symbols
        return {symbols[0]: {"interval": "4h", "info": {"fundingIntervalHours": 4}}}

    async def fetch_order_book(self, symbol):
        return deepcopy(self.book)

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("client,raw,expected", [
    ("bybit", {"fundingInterval": "60"}, 1),
    ("bybit", {"fundingInterval": "90"}, 1.5),
    ("bitget", {"fundingRateInterval": "4"}, 4),
    ("gate", {"funding_interval": "14400"}, 4),
    ("mexc", {"collectCycle": 2}, 2),
    ("kucoinfutures", {"granularity": 3_600_000}, 1),
    ("binanceusdm", {"fundingIntervalHours": "1"}, 1),
    ("okx", {"fundingTime": EVENT_MS, "nextFundingTime": EVENT_MS + 14_400_000}, 4),
    ("htx", {"funding_time": EVENT_MS, "next_funding_time": EVENT_MS + 3_600_000}, 1),
])
def test_actual_funding_intervals(client, raw, expected):
    assert funding_interval(client, {}, {"info": raw})[0] == pytest.approx(expected)


@pytest.mark.parametrize("data", [
    {}, {"interval": 8}, {"interval": "8"}, {"interval": "0h"},
    {"timestamp": EVENT_MS, "fundingTimestamp": EVENT_MS + 1000},
])
def test_never_infer_funding_cycle_from_countdown_or_default(data):
    assert funding_interval("binanceusdm", {}, data)[0] is None


def test_explicit_market_metadata_precedes_rounded_ccxt_interval():
    assert funding_interval("bybit", {"info": {"fundingInterval": 90}}, {"interval": "1h"})[0] == 1.5
    assert funding_interval("deribit", {}, {"interval": "8h"})[0] == 8


@pytest.mark.asyncio
async def test_spot_preserves_fiat_quote_and_real_zero_volume():
    client = FakeClient([market(quote="KRW")])
    adapter = CexAdapter("upbit", client_factory=lambda _: client)
    instruments = await adapter.discover()
    result = (await adapter.fetch(instruments))[0]
    assert instruments[0].quote == "KRW"
    assert instruments[0].asset_id == "BTC"
    assert result.volume == 0 and result.volume_unit == "BTC"
    assert result.funding_rate is None and result.open_interest is None
    assert result.is_demo is False
    assert result.field_timestamps["bid"] == EVENT_MS
    await adapter.close()
    assert client.closed


@pytest.mark.asyncio
async def test_unknown_or_unverified_identity_is_scoped_and_dated_futures_excluded():
    unknown = market(base="SAME")
    unverified = market(base="BTC", quote="USD")
    unverified["baseId"] = "WRAPPED_BTC"
    dated = market(base="ETH")
    dated.update(spot=False, swap=False, future=True)
    client = FakeClient([unknown, unverified, dated])
    items = await CexAdapter("okx", client_factory=lambda _: client).discover()
    assert len(items) == 2
    assert all(x.asset_id.startswith("cex:okx:okx:") and not x.mapping_verified for x in items)


@pytest.mark.asyncio
async def test_perpetual_unit_multiplier_missing_interval_and_field_times():
    client = FakeClient([market(swap=True, size=0.01)])
    adapter = CexAdapter("okx", client_factory=lambda _: client)
    items = await adapter.discover()
    result = (await adapter.fetch(items))[0]
    assert items[0].contract_size == 0.01
    assert result.funding_rate == -0.0002 and result.funding_kind == "current"
    assert result.funding_interval_hours is None
    assert result.next_funding_at == EVENT_MS + 3_600_000
    assert result.open_interest == 1500 and result.oi_unit == "contracts"
    assert result.field_timestamps["mark"] == EVENT_MS + 1000
    assert result.field_timestamps["open_interest"] == EVENT_MS + 2000
    assert result.source_at == EVENT_MS
    assert any("8h normalization unavailable" in x for x in result.issues)
    book = await adapter.orderbook(items[0])
    assert book.quantity_unit == "contracts" and book.bids[0] == (100, 20)


@pytest.mark.asyncio
async def test_unsupported_fields_and_unknown_source_times_are_visible():
    client = FakeClient([market(swap=True)])
    client.has = {}
    client.ticker = {"last": 100}
    adapter = CexAdapter("bybit", client_factory=lambda _: client)
    result = (await adapter.fetch(await adapter.discover()))[0]
    assert result.source_at is None and result.field_timestamps["last"] is None
    assert result.funding_rate is None
    assert any("fetchFundingRate" in x for x in result.issues)
    assert any("receipt time is not source time" in x for x in result.issues)


@pytest.mark.asyncio
async def test_network_failure_reaches_collector_no_fabricated_snapshot():
    client = FakeClient([market(swap=True)])
    client.fail_funding = TimeoutError("secret proxy URL must never become snapshot data")
    adapter = CexAdapter("bybit", client_factory=lambda _: client)
    items = await adapter.discover()
    with pytest.raises(TimeoutError):
        await adapter.fetch(items)


@pytest.mark.asyncio
async def test_deribit_notional_orderbook_becomes_contracts_and_rate_is_realized():
    client = FakeClient([market(quote="USD", swap=True, inverse=True, size=10)])
    client.funding["interval"] = "8h"
    adapter = CexAdapter("deribit", client_factory=lambda _: client)
    items = await adapter.discover()
    book = await adapter.orderbook(items[0])
    assert book.quantity_unit == "contracts" and book.bids[0] == (100, 2)
    snapshot = (await adapter.fetch(items))[0]
    assert snapshot.funding_kind == "settled"
    assert snapshot.funding_interval_hours == 8
    assert any("continuous accrual" in x for x in snapshot.issues)


@pytest.mark.parametrize("client,inverse,amount,unit", [
    ("binanceusdm", False, 12, "BTC"), ("binancecoinm", True, 12, "contracts"),
    ("bybit", False, 12, "BTC"), ("bitget", False, 12, "BTC"),
    ("okx", False, 12, "contracts"), ("htx", False, 12, "contracts"),
    ("kucoinfutures", False, 12, "contracts"), ("gate", False, 12, "contracts"),
    ("unreviewed", False, None, None),
])
def test_open_interest_native_unit_table(client, inverse, amount, unit):
    result = _open_interest(client, market(swap=True, inverse=inverse), {"openInterestAmount": 12})
    assert result[:2] == (amount, unit)


@pytest.mark.asyncio
async def test_nonfinite_prices_do_not_poison_json_and_crossed_quotes_invalid():
    client = FakeClient([market()])
    client.ticker.update(last="NaN", bid=102, ask=101, baseVolume="Infinity", quoteVolume=None)
    adapter = CexAdapter("upbit", client_factory=lambda _: client)
    result = (await adapter.fetch(await adapter.discover()))[0]
    assert result.last is None and result.volume is None and not result.valid
    assert "crossed orderbook: bid exceeds ask" in result.issues


@pytest.mark.asyncio
async def test_separate_clients_keep_native_identity_and_derivative_units():
    clients = {"binance": FakeClient([market()]),
               "binanceusdm": FakeClient([market(swap=True)]),
               "binancecoinm": FakeClient([market(quote="USD", swap=True, inverse=True, size=100)])}
    adapter = CexAdapter("binance", client_factory=clients.__getitem__)
    items = await adapter.discover()
    assert len({x.id for x in items}) == 3
    assert any("binancecoinm" in x.id and not x.linear for x in items)
    await adapter.close()
    assert all(x.closed for x in clients.values())


@pytest.mark.asyncio
async def test_binance_adjustment_metadata_is_fetched_without_default_for_missing_symbols():
    clients = {"binance": FakeClient([]),
               "binanceusdm": FakeClient([market(swap=True), market(base="ETH", swap=True)]),
               "binancecoinm": FakeClient([])}
    clients["binanceusdm"].has["fetchFundingIntervals"] = True
    adapter = CexAdapter("binance", client_factory=clients.__getitem__)
    snapshots = await adapter.fetch(await adapter.discover())
    assert snapshots[0].funding_interval_hours == 4
    assert snapshots[1].funding_interval_hours is None
    assert "fetch_funding_intervals" in snapshots[0].field_sources["funding_interval_hours"]
    assert len(clients["binanceusdm"].interval_symbols) == 2


@pytest.mark.asyncio
async def test_failed_multi_service_catalog_preserves_previous_discovery():
    clients = {"kraken": FakeClient([market()]), "krakenfutures": FakeClient([market(swap=True)])}
    adapter = CexAdapter("kraken", client_factory=clients.__getitem__)
    await adapter.discover()
    previous = deepcopy(adapter._markets)

    async def fail_catalog(reload=False):
        raise TimeoutError("catalog unavailable")

    clients["krakenfutures"].load_markets = fail_catalog
    with pytest.raises(TimeoutError):
        await adapter.discover()
    assert adapter._markets == previous


def test_all_twelve_venues_have_honest_coverage_and_lazy_constructors():
    assert len(build_cex_adapters()) == len(coverage()) == len(SPECS) == 12
    assert all(not x._clients for x in build_cex_adapters().values())
    assert all(x.status == "implemented_unverified_live" and x.last_verified_at is None for x in coverage())


@pytest.mark.asyncio
async def test_installed_ccxt_public_client_construction_no_network():
    pytest.importorskip("ccxt.async_support")
    adapters = build_cex_adapters()
    for adapter in adapters.values():
        for client_id in adapter.spec.clients:
            client = adapter._client(client_id)
            assert client.enableRateLimit
            assert client.aiohttp_trust_env
            assert client.ssl_context.verify_mode != 0
            assert not client.apiKey and not client.secret
            assert client.has["fetchCurrencies"] is False
        await adapter.close()
