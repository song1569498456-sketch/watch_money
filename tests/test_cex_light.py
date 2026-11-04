"""Synthetic public ticker fixtures only; no exchange traffic/live certification."""
import asyncio
from copy import deepcopy

import pytest

from watch_money.adapters.cex import CexAdapter
from watch_money.models import Instrument

EVENT = 1_800_000_000_000


def instrument(base="BTC", *, venue="okx", client=None, kind="spot", settle=None, linear=True):
    client = client or venue
    quote = "USD" if not linear else "USDT"
    symbol = f"{base}/{quote}" + (f":{settle}" if kind == "perpetual" else "")
    return Instrument(id=f"cex:{venue}:{client}:{kind}:{symbol}", venue=venue, native_symbol=symbol,
                      asset_id=base, base=base, quote=quote, kind=kind, settle=settle, linear=linear)


class TickerClient:
    def __init__(self, *, multiple=True, single=True):
        self.has = {"fetchTickers": multiple, "fetchTicker": single,
                    "fetchFundingRate": True, "fetchOpenInterest": True, "fetchFundingIntervals": True}
        self.calls = []
        self.ticker = {"timestamp": EVENT, "bid": 100, "ask": 101, "last": 100.5, "baseVolume": 0}
        self.payloads = None
        self.error = None
        self.active = self.max_active = 0

    async def fetch_tickers(self, symbols, params):
        self.calls.append(("multiple", list(symbols), deepcopy(params)))
        if self.error:
            raise self.error
        if self.payloads is not None:
            return deepcopy(self.payloads)
        return {symbol: {**self.ticker, "symbol": symbol} for symbol in symbols}

    async def fetch_ticker(self, symbol):
        self.calls.append(("single", symbol))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if self.error:
                raise self.error
            return deepcopy(self.ticker)
        finally:
            self.active -= 1

    async def fetch_funding_rate(self, *_args):
        raise AssertionError("light collection must not fetch funding")

    async def fetch_open_interest(self, *_args):
        raise AssertionError("light collection must not fetch open interest")

    async def fetch_funding_intervals(self, *_args):
        raise AssertionError("light collection must not fetch funding intervals")


def adapter_for(items, clients=None, *, venue="okx"):
    clients = clients or {venue: TickerClient()}
    adapter = CexAdapter(venue, client_factory=clients.__getitem__)
    for item in items:
        client_id = item.id.split(":")[2]
        adapter._markets[item.id] = (client_id, {"symbol": item.native_symbol})
    return adapter, clients


@pytest.mark.asyncio
async def test_batch_is_capped_deduplicated_ordered_and_sparse():
    items = [instrument(f"COIN{i}", kind="perpetual", settle="USDT") for i in range(100)]
    adapter, clients = adapter_for(items)
    clients["okx"].ticker["info"] = {"volCcy24h": "0"}
    patches = await adapter.fetch_light(items + [items[0]])
    assert [patch.instrument_id for patch in patches] == [item.id for item in items]
    assert [len(call[1]) for call in clients["okx"].calls] == [50, 50]
    patch = patches[0]
    supplied = patch.model_dump(exclude_unset=True)
    assert not {"funding_rate", "funding_kind", "funding_interval_hours", "open_interest", "next_funding_at"} & supplied.keys()
    assert patch.volume == 0 and patch.volume_unit == items[0].base
    assert patch.field_timestamps["last"] == EVENT and patch.source_at == EVENT
    assert patch.source.startswith("ccxt:okx:fetch_tickers:") and not patch.is_demo
    assert "unified_ticker_event_time" in patch.field_sources["last"]


@pytest.mark.asyncio
async def test_hard_limit_and_invalid_identity_fail_before_requests():
    items = [instrument(f"COIN{i}") for i in range(101)]
    adapter, clients = adapter_for(items)
    with pytest.raises(ValueError, match="at most 100"):
        await adapter.fetch_light(items)
    with pytest.raises(ValueError, match="discovered first"):
        await adapter.fetch_light([items[0], instrument("NEW")])
    assert clients["okx"].calls == []
    assert await adapter.fetch_light([]) == []


@pytest.mark.parametrize("venue,expected", [
    ("okx", {"type": "swap"}),
    ("bybit", {"type": "swap", "subType": "linear"}),
    ("bitget", {"type": "swap", "subType": "linear"}),
    ("gate", {"type": "swap", "settle": "usdt"}),
    ("mexc", {"type": "swap"}),
    ("htx", {"type": "swap", "subType": "linear"}),
])
@pytest.mark.asyncio
async def test_product_and_settlement_groups_have_explicit_request_family(venue, expected):
    items = [instrument(venue=venue),
             instrument(venue=venue, kind="perpetual", settle="USDT"),
             instrument(venue=venue, kind="perpetual", settle="USDC"),
             instrument(venue=venue, kind="perpetual", settle="BTC", linear=False)]
    adapter, clients = adapter_for(items, venue=venue)
    patches = await adapter.fetch_light(items)
    calls = clients[venue].calls
    assert len(patches) == len(calls) == 4
    assert calls[0][2] == {"type": "spot"}
    assert calls[1][2] == expected
    assert all(len(call[1]) == 1 for call in calls)
    if "subType" in expected:
        assert calls[3][2]["subType"] == "inverse"
    if venue == "gate":
        assert calls[2][2]["settle"] == "usdc" and calls[3][2]["settle"] == "btc"


@pytest.mark.asyncio
async def test_binance_service_clients_and_deribit_bases_never_mix():
    items = [instrument(venue="binance", client="binance"),
             instrument(venue="binance", client="binanceusdm", kind="perpetual", settle="USDT"),
             instrument(venue="binance", client="binancecoinm", kind="perpetual", settle="BTC", linear=False)]
    clients = {name: TickerClient() for name in ("binance", "binanceusdm", "binancecoinm")}
    adapter, _ = adapter_for(items, clients, venue="binance")
    assert len(await adapter.fetch_light(items)) == 3
    assert all(len(client.calls) == 1 for client in clients.values())
    bases = [instrument(base, venue="deribit", kind="perpetual", settle="USDC") for base in ("BTC", "ETH")]
    adapter, clients = adapter_for(bases, venue="deribit")
    assert len(await adapter.fetch_light(bases)) == 2
    assert len(clients["deribit"].calls) == 2


@pytest.mark.asyncio
async def test_coinbase_forces_public_tickers():
    items = [instrument(venue="coinbase")]
    adapter, clients = adapter_for(items, venue="coinbase")
    await adapter.fetch_light(items)
    assert clients["coinbase"].calls[0][2] == {"usePrivate": False}


@pytest.mark.parametrize("multiple", [False, None, "emulated"])
@pytest.mark.asyncio
async def test_single_fallback_is_bounded_and_does_not_use_emulated_bulk(multiple):
    items = [instrument(f"COIN{i}") for i in range(20)]
    client = TickerClient(multiple=multiple)
    adapter, _ = adapter_for(items, {"okx": client})
    assert len(await adapter.fetch_light(items)) == 20
    assert len(client.calls) == 20 and all(call[0] == "single" for call in client.calls)
    assert 1 < client.max_active <= 4


@pytest.mark.asyncio
async def test_runtime_not_supported_falls_back_but_network_errors_reach_backoff():
    class NotSupported(Exception):
        pass

    items = [instrument()]
    client = TickerClient()
    adapter, _ = adapter_for(items, {"okx": client})
    client.error = NotSupported("public batch unavailable")
    original_fetch_ticker = client.fetch_ticker

    async def supported_single(symbol):
        client.error = None
        return await original_fetch_ticker(symbol)

    client.fetch_ticker = supported_single
    assert len(await adapter.fetch_light(items)) == 1
    assert [call[0] for call in client.calls] == ["multiple", "single"]
    client.calls.clear()
    client.error = TimeoutError("proxy credential must not appear in fabricated snapshot")
    with pytest.raises(TimeoutError):
        await adapter.fetch_light(items)
    assert [call[0] for call in client.calls] == ["multiple"]


@pytest.mark.asyncio
async def test_missing_capabilities_are_explicit_and_reset_on_next_call():
    items = [instrument()]
    client = TickerClient(multiple=False, single=False)
    adapter, _ = adapter_for(items, {"okx": client})
    assert await adapter.fetch_light(items) == []
    assert adapter.light_unsupported == ["okx:spot:None:native public ticker unavailable"]
    assert client.calls == []
    client.has["fetchTicker"] = True
    assert len(await adapter.fetch_light(items)) == 1
    assert adapter.light_unsupported == []


@pytest.mark.asyncio
async def test_unreturned_empty_wrong_symbol_and_nonfinite_entries_do_not_refresh():
    items = [instrument(base) for base in ("BTC", "ETH", "SOL", "MISSING", "BAD")]
    client = TickerClient()
    client.payloads = {
        items[0].native_symbol: {"last": 100, "timestamp": EVENT - 3_600_000},
        items[1].native_symbol: {"symbol": "OTHER/USDT", "last": 999},
        items[2].native_symbol: {"baseVolume": 1},
        items[4].native_symbol: {"last": "NaN", "bid": -1, "ask": "Infinity"},
        "EXTRA/USDT": {"last": 999},
    }
    adapter, _ = adapter_for(items, {"okx": client})
    patches = await adapter.fetch_light(items)
    assert len(patches) == 1 and patches[0].instrument_id == items[0].id
    assert patches[0].source_at == EVENT - 3_600_000
    assert "volume" not in patches[0].model_dump(exclude_unset=True)


@pytest.mark.asyncio
async def test_unknown_source_time_quote_volume_and_crossed_quotes_are_preserved():
    items = [instrument()]
    client = TickerClient()
    client.ticker = {"bid": 102, "ask": 101, "quoteVolume": 0}
    adapter, _ = adapter_for(items, {"okx": client})
    patch = (await adapter.fetch_light(items))[0]
    assert not patch.valid and "crossed orderbook: bid exceeds ask" in patch.issues
    assert patch.source_at is None and patch.field_timestamps["bid"] is None
    assert patch.volume == 0 and patch.volume_unit == "USDT"
    assert any("receipt time is not source time" in issue for issue in patch.issues)


@pytest.mark.asyncio
async def test_payload_callback_observes_public_objects_and_failure_does_not_stop_collection():
    items = [instrument()]
    adapter, clients = adapter_for(items)
    calls = []

    async def record(venue, transport, payload):
        calls.append((venue, transport, deepcopy(payload)))
        raise OSError("optional diagnostic sink unavailable")

    adapter.on_payload = record
    assert len(await adapter.fetch_light(items)) == 1
    assert calls[0][0:2] == ("okx", "rest:ccxt:fetch_tickers")
    assert calls[0][2][items[0].native_symbol]["last"] == 100.5
    clients["okx"].has["fetchTickers"] = False
    assert len(await adapter.fetch_light(items)) == 1
    assert calls[-1][1] == "rest:ccxt:fetch_ticker"
    assert len(await adapter.fetch(items)) == 1
    assert calls[-1][1] == "rest:ccxt:fetch_ticker"


@pytest.mark.asyncio
async def test_malformed_batch_raises_without_fabrication():
    items = [instrument()]
    client = TickerClient()
    client.payloads = []
    adapter, _ = adapter_for(items, {"okx": client})
    with pytest.raises(ValueError, match="non-mapping"):
        await adapter.fetch_light(items)


@pytest.mark.asyncio
async def test_partial_single_failure_keeps_successes_and_all_failure_raises():
    items = [instrument("BTC"), instrument("OTHER")]
    client = TickerClient(multiple=False)
    original = client.fetch_ticker

    async def partial(symbol):
        if symbol == items[1].native_symbol:
            raise TimeoutError("secret proxy URL must never enter status")
        return await original(symbol)

    client.fetch_ticker = partial
    adapter, _ = adapter_for(items, {"okx": client})
    assert [patch.instrument_id for patch in await adapter.fetch_light(items)] == [items[0].id]
    assert adapter.light_errors == {items[1].id: "TimeoutError"}
    client.fetch_ticker = original
    client.error = TimeoutError("private URL")
    with pytest.raises(TimeoutError):
        await adapter.fetch_light(items)
    assert adapter.light_errors == {item.id: "TimeoutError" for item in items}
    assert client.active == 0


@pytest.mark.asyncio
async def test_partial_bulk_failure_keeps_other_product_groups():
    items = [instrument(), instrument("OTHER", kind="perpetual", settle="USDT")]
    client = TickerClient()
    original = client.fetch_tickers

    async def partial(symbols, params):
        if params["type"] == "swap":
            raise TimeoutError("private URL")
        return await original(symbols, params)

    client.fetch_tickers = partial
    adapter, _ = adapter_for(items, {"okx": client})
    assert [patch.instrument_id for patch in await adapter.fetch_light(items)] == [items[0].id]
    assert adapter.light_errors == {items[1].id: "TimeoutError"}


def native_market(base, base_id, quote="USDT"):
    return {"id": f"{base_id}{quote}", "symbol": f"{base}/{quote}", "base": base,
            "baseId": base_id, "quote": quote, "spot": True, "active": True}


class CatalogClient(TickerClient):
    def __init__(self, markets):
        super().__init__()
        self.markets = markets

    async def load_markets(self, reload=False):
        assert reload
        if self.error:
            raise self.error
        return {market["id"]: deepcopy(market) for market in self.markets}


@pytest.mark.asyncio
async def test_catalog_keeps_all_nonmajor_pairs_and_separates_same_ticker_native_ids():
    markets = [native_market("PEPE", "PEPE", "USDT"), native_market("PEPE", "PEPE", "USDC"),
               native_market("SAME", "TOKEN_A"), native_market("SAME", "TOKEN_B", "BTC"),
               native_market("BTC", "WRAPPED_BTC")]
    client = CatalogClient(markets)
    adapter = CexAdapter("okx", client_factory=lambda _: client)
    items = await adapter.discover()
    assert len(items) == 5 and adapter.discovery_complete
    assert not adapter.discovery_errors
    assert all(not item.mapping_verified for item in items)
    pepe = [item for item in items if item.base == "PEPE"]
    assert len(pepe) == 2 and len({item.asset_id for item in pepe}) == 1
    same = [item for item in items if item.base == "SAME"]
    assert len({item.asset_id for item in same}) == 2
    assert {item.quote for item in same} == {"USDT", "BTC"}
    assert all(item.asset_id.startswith("cex:okx:okx:") for item in items)
    assert len(await adapter.fetch_light(items)) == 5


@pytest.mark.asyncio
async def test_partial_discovery_retains_failed_identity_as_unavailable_and_recovers():
    clients = {"kraken": CatalogClient([native_market("NEW", "NEW")]),
               "krakenfutures": CatalogClient([native_market("OLD", "OLD")])}
    adapter = CexAdapter("kraken", client_factory=clients.__getitem__)
    before = await adapter.discover()
    old_id = next(item.id for item in before if item.base == "OLD")
    clients["krakenfutures"].error = TimeoutError("secret URL")
    clients["kraken"].markets.append(native_market("PEPE", "PEPE"))
    partial = await adapter.discover()
    assert {item.base for item in partial} == {"NEW", "OLD", "PEPE"}
    assert next(item for item in partial if item.id == old_id).active is False
    assert adapter.discovery_complete is False
    assert adapter.discovery_errors == {"krakenfutures": "TimeoutError"}
    clients["krakenfutures"].error = None
    recovered = await adapter.discover()
    assert adapter.discovery_complete and not adapter.discovery_errors
    assert next(item for item in recovered if item.id == old_id).active is True


@pytest.mark.asyncio
async def test_first_partial_catalog_is_returned_and_total_initial_failure_raises():
    clients = {"kraken": CatalogClient([native_market("PEPE", "PEPE")]),
               "krakenfutures": CatalogClient([])}
    clients["krakenfutures"].error = TimeoutError("secret URL")
    adapter = CexAdapter("kraken", client_factory=clients.__getitem__)
    assert len(await adapter.discover()) == 1
    assert adapter.discovery_errors == {"krakenfutures": "TimeoutError"}
    clients["kraken"].error = TimeoutError("secret URL")
    cached = await adapter.discover()
    assert len(cached) == 1 and not cached[0].active
    fresh = CexAdapter("kraken", client_factory=clients.__getitem__)
    with pytest.raises(TimeoutError):
        await fresh.discover()
    assert fresh.discovery_errors == {name: "TimeoutError" for name in clients}


class FundingClient(CatalogClient):
    def __init__(self, markets=(), *, multiple=True):
        super().__init__(markets)
        self.has.update(fetchFundingRates=multiple, fetchFundingRate=True, fetchFundingIntervals=False)
        self.rate = {"timestamp": EVENT, "fundingRate": 0.0003,
                     "fundingTimestamp": EVENT + 3_600_000, "interval": "1h"}
        self.funding_payloads = None
        self.funding_error = None
        self.single_errors = {}

    async def fetch_funding_rates(self, symbols, params):
        self.calls.append(("funding_multiple", list(symbols), deepcopy(params)))
        if self.funding_error:
            raise self.funding_error
        if self.funding_payloads is not None:
            return deepcopy(self.funding_payloads)
        return {symbol: {**self.rate, "symbol": symbol} for symbol in symbols}

    async def fetch_funding_rate(self, symbol):
        self.calls.append(("funding_single", symbol))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if symbol in self.single_errors:
                raise self.single_errors[symbol]
            return deepcopy(self.rate)
        finally:
            self.active -= 1

    async def fetch_open_interest(self, *_args):
        return {"timestamp": EVENT, "openInterestAmount": 1}


def funding_market(base="PEPE", *, raw=None):
    item = native_market(base, base)
    item.update(spot=False, swap=True, linear=True, inverse=False, settle="USDT", contractSize=1,
                symbol=f"{base}/USDT:USDT", info=raw or {})
    return item


@pytest.mark.asyncio
async def test_funding_light_covers_small_coins_batched_without_ticker_or_oi():
    markets = [funding_market(f"SMALL{i}") for i in range(100)]
    client = FundingClient(markets)
    adapter = CexAdapter("okx", client_factory=lambda _: client)
    items = await adapter.discover()
    patches = await adapter.fetch_funding_light(items + [items[0]])
    assert len(patches) == 100 and all(patch.valid for patch in patches)
    assert [len(call[1]) for call in client.calls] == [50, 50]
    assert all(call[0] == "funding_multiple" and call[2] == {} for call in client.calls)
    patch = patches[0]
    assert patch.funding_rate == 0.0003 and patch.funding_kind == "current"
    assert patch.funding_interval_hours == 1 and patch.next_funding_at == EVENT + 3_600_000
    assert patch.field_timestamps["funding_rate"] == EVENT
    assert not {"bid", "ask", "last", "open_interest"} & patch.model_dump(exclude_unset=True).keys()
    with pytest.raises(ValueError, match="at most 100"):
        await adapter.fetch_funding_light(items + [instrument("ADDITIONAL")])


@pytest.mark.asyncio
async def test_funding_single_is_bounded_partial_failure_isolated_and_all_failure_raises():
    items = [instrument(f"SMALL{i}", kind="perpetual", settle="USDT") for i in range(20)]
    client = FundingClient(multiple=False)
    adapter, _ = adapter_for(items, {"okx": client})
    client.single_errors[items[0].native_symbol] = TimeoutError("secret URL")
    patches = await adapter.fetch_funding_light(items)
    assert len(patches) == 19 and 1 < client.max_active <= 4
    assert adapter.funding_errors == {items[0].id: "TimeoutError"}
    client.single_errors = {item.native_symbol: TimeoutError("secret URL") for item in items}
    with pytest.raises(TimeoutError):
        await adapter.fetch_funding_light(items)
    assert len(adapter.funding_errors) == 20 and client.active == 0


@pytest.mark.asyncio
async def test_funding_missing_nonfinite_zero_and_prediction_remain_explicit():
    items = [instrument(base, kind="perpetual", settle="USDT") for base in ("MISSING", "BAD", "ZERO", "PREDICTED")]
    client = FundingClient()
    client.funding_payloads = {
        items[1].native_symbol: {"timestamp": EVENT, "fundingRate": "NaN"},
        items[2].native_symbol: {"timestamp": EVENT, "fundingRate": 0, "interval": "4h"},
        items[3].native_symbol: {"timestamp": EVENT, "nextFundingRate": -0.0001, "interval": "2h",
                               "nextFundingTimestamp": EVENT + 7_200_000},
    }
    adapter, _ = adapter_for(items, {"okx": client})
    patches = {patch.instrument_id: patch for patch in await adapter.fetch_funding_light(items)}
    assert adapter.funding_errors == {items[0].id: "MissingFundingRate"}
    assert not patches[items[1].id].valid and patches[items[1].id].funding_rate is None
    assert patches[items[2].id].funding_rate == 0 and patches[items[2].id].valid
    predicted = patches[items[3].id]
    assert predicted.funding_kind == "predicted" and predicted.funding_rate == -0.0001
    assert predicted.next_funding_at == EVENT + 7_200_000


@pytest.mark.asyncio
async def test_bybit_catalog_interval_has_observation_age_without_fake_source_time(monkeypatch):
    from watch_money.strategies import CalculateRequest, _field_fresh

    monkeypatch.setattr("watch_money.adapters.cex.now_ms", lambda: EVENT + 1000)
    client = FundingClient([funding_market(raw={"fundingInterval": "480"})])
    adapter = CexAdapter("bybit", client_factory=lambda _: client)
    items = await adapter.discover()
    monkeypatch.setattr("watch_money.adapters.cex.now_ms", lambda: EVENT + 2000)
    patch = (await adapter.fetch_funding_light(items))[0]
    assert patch.funding_interval_hours == 8
    assert patch.field_timestamps["funding_interval_hours"] is None
    assert patch.field_received_at["funding_interval_hours"] == EVENT + 1000
    assert patch.field_sources["funding_interval_hours"].endswith(":catalog_metadata_observed_at")
    assert patch.field_timestamps["funding_rate"] == EVENT
    assert patch.field_received_at["funding_rate"] == EVENT + 2000
    assert any("not exchange event time" in issue for issue in patch.issues)
    # A new funding poll must not rejuvenate the cached catalog evidence.
    monkeypatch.setattr("watch_money.adapters.cex.now_ms", lambda: EVENT + 3000)
    client.rate["timestamp"] = EVENT + 2500
    patch = (await adapter.fetch_funding_light(items))[0]
    assert patch.field_received_at["funding_interval_hours"] == EVENT + 1000
    assert patch.field_timestamps["funding_rate"] == EVENT + 2500
    detailed = (await adapter.fetch(items))[0]
    assert detailed.field_received_at["funding_interval_hours"] == EVENT + 1000
    assert detailed.field_timestamps["funding_interval_hours"] is None
    req = CalculateRequest(instruments=items, snapshots=[patch], buy_instrument_id=items[0].id,
                           sell_instrument_id="another:venue:instrument", size_base=1, as_of=EVENT + 3000)
    assert _field_fresh(patch, "funding_interval_hours", req)
    expired = req.model_copy(update={"as_of": EVENT + 1001 + 6 * 3600_000})
    assert not _field_fresh(patch, "funding_interval_hours", expired)
    assert not _field_fresh(patch, "funding_rate", expired)
    no_observation = patch.model_copy(update={"field_received_at": {"funding_rate": EVENT + 3000}})
    assert not _field_fresh(no_observation, "funding_interval_hours", req)


@pytest.mark.asyncio
async def test_binance_public_interval_observation_is_explicit_and_missing_is_not_default(monkeypatch):
    monkeypatch.setattr("watch_money.adapters.cex.now_ms", lambda: EVENT + 1000)
    items = [instrument(base, venue="binance", client="binancecoinm", kind="perpetual", settle="BTC", linear=False)
             for base in ("PEPE", "SMALL")]
    client = FundingClient()
    client.has["fetchFundingIntervals"] = True

    async def intervals(symbols):
        return {symbols[0]: {"interval": "4h", "info": {"fundingIntervalHours": "4"}}}

    client.fetch_funding_intervals = intervals
    client.rate.pop("interval")
    adapter, _ = adapter_for(items, {"binancecoinm": client}, venue="binance")
    patches = await adapter.fetch_funding_light(items)
    assert patches[0].funding_interval_hours == 4 and patches[1].funding_interval_hours is None
    assert patches[0].field_timestamps["funding_interval_hours"] is None
    assert patches[0].field_received_at["funding_interval_hours"] == EVENT + 1000
    assert patches[0].field_sources["funding_interval_hours"].endswith(":public_metadata_observed_at")
    assert client.calls[0][2] == {"subType": "inverse"}


@pytest.mark.asyncio
async def test_funding_capability_spot_filtering_and_callback():
    items = [instrument(), instrument("PEPE", kind="perpetual", settle="USDT")]
    client = FundingClient()
    adapter, _ = adapter_for(items, {"okx": client})
    calls = []

    async def record(venue, transport, payload):
        calls.append((venue, transport, deepcopy(payload)))

    adapter.on_payload = record
    patches = await adapter.fetch_funding_light(items)
    assert [patch.instrument_id for patch in patches] == [items[1].id]
    assert calls[0][0:2] == ("okx", "rest:ccxt:fetch_funding_rates")
    client.has.update(fetchFundingRates=False, fetchFundingRate=False)
    assert await adapter.fetch_funding_light(items) == []
    assert adapter.funding_unsupported == ["okx:USDT:native public funding unavailable"]


@pytest.mark.asyncio
async def test_detailed_fetch_preserves_successful_markets_when_one_funding_request_fails():
    client = FundingClient([funding_market("PEPE"), funding_market("OTHER")])
    adapter = CexAdapter("okx", client_factory=lambda _: client)
    items = await adapter.discover()
    client.single_errors[items[0].native_symbol] = TimeoutError("secret URL")
    results = await adapter.fetch(items)
    assert [snapshot.instrument_id for snapshot in results] == [items[1].id]
    assert adapter.fetch_errors == {items[0].id: "TimeoutError"}
    client.single_errors[items[1].native_symbol] = TimeoutError("secret URL")
    with pytest.raises(TimeoutError):
        await adapter.fetch(items)
    assert adapter.fetch_errors == {item.id: "TimeoutError" for item in items}


@pytest.mark.asyncio
async def test_funding_bulk_partial_groups_and_native_single_fallback():
    class NotSupported(Exception):
        pass

    items = [instrument("PEPE", kind="perpetual", settle="USDT"),
             instrument("OTHER", kind="perpetual", settle="USDC")]
    client = FundingClient()
    original = client.fetch_funding_rates

    async def partial(symbols, params):
        if symbols == [items[1].native_symbol]:
            raise TimeoutError("secret URL")
        return await original(symbols, params)

    client.fetch_funding_rates = partial
    adapter, _ = adapter_for(items, {"okx": client})
    assert [patch.instrument_id for patch in await adapter.fetch_funding_light(items)] == [items[0].id]
    assert adapter.funding_errors == {items[1].id: "TimeoutError"}
    client.fetch_funding_rates = original
    client.funding_error = NotSupported("native batch unavailable")
    assert len(await adapter.fetch_funding_light(items)) == 2
    assert not adapter.funding_errors
    assert any(call[0] == "funding_single" for call in client.calls)


@pytest.mark.asyncio
async def test_okx_native_volume_units_are_shared_by_detailed_and_light_rest():
    market = funding_market("BTC")
    market.update(id="BTC-USDT-SWAP", contractSize=0.01, info={"ctValCcy": "BTC"})
    client = FundingClient([market])
    client.ticker.update(baseVolume=10000, info={"instId": "BTC-USDT-SWAP", "instType": "SWAP",
                                               "vol24h": "10000", "volCcy24h": "100"})
    adapter = CexAdapter("okx", client_factory=lambda _: client)
    items = await adapter.discover()
    detailed = (await adapter.fetch(items))[0]
    light = (await adapter.fetch_light(items))[0]
    for snapshot in (detailed, light):
        assert snapshot.volume == 100 and snapshot.volume_unit == "BTC"
        assert ".info.volCcy24h:rolling_24h" in snapshot.field_sources["volume"]
    # Unknown derivative raw schemas do not inherit a possibly mislabeled unified unit.
    other = CexAdapter("bybit", client_factory=lambda _: client)
    other_items = await other.discover()
    unknown = (await other.fetch_light(other_items))[0]
    assert unknown.volume is None and "volume" not in unknown.model_dump(exclude_unset=True)
