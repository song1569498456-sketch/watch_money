"""Public bulk/rotation simulations, not live performance certification."""
import asyncio

import pytest

from watch_money.adapters.cex import CexAdapter
from watch_money.collector import Collector, funding_response_source_ages
from watch_money.models import Instrument, MarketSnapshot, Settings, now_ms
from watch_money.storage import Storage

NOW = now_ms()


class PublicClient:
    rateLimit = 110

    def __init__(self, items, *, bulk_funding=True):
        self.items = items
        self.has = {"fetchTickers": True, "fetchTicker": True, "fetchFundingRates": bulk_funding,
                    "fetchFundingRate": True}
        self.calls = []
        self.failure = None
        self.single_gate = None
        self.started = asyncio.Event()
        self.on_tickers = None

    def rate(self):
        return {"timestamp": NOW, "fundingRate": .0001, "interval": "1h", "fundingTimestamp": NOW + 3600_000,
                "info": {"ts": str(NOW), "fundingTime": str(NOW + 3600_000), "nextFundingTime": str(NOW + 7200_000)}}

    async def fetch_tickers(self, symbols, params):
        self.calls.append(("tickers", symbols, params))
        if self.on_tickers:
            self.on_tickers()
        if self.failure:
            raise self.failure
        return {item.native_symbol: {"timestamp": NOW, "bid": 100, "ask": 101} for item in self.items
                if symbols is None or item.native_symbol in symbols}

    async def fetch_funding_rates(self, symbols, params):
        self.calls.append(("funding", symbols, params))
        if self.failure:
            raise self.failure
        return {item.native_symbol: self.rate() for item in self.items if symbols is None or item.native_symbol in symbols}

    async def fetch_funding_rate(self, symbol):
        self.calls.append(("single_funding", symbol))
        self.started.set()
        if self.single_gate:
            await self.single_gate.wait()
        return self.rate()

    async def fetch_ticker(self, symbol):
        self.calls.append(("single_ticker", symbol))
        return {"timestamp": NOW, "bid": 100, "ask": 101}


def adapter_for(count=1000, *, venue="okx", kind="perpetual", bulk_funding=True):
    items = [Instrument(id=f"{venue}:{kind}:{index}", venue=venue, native_symbol=f"SMALL{index}/USDT" +
                        (":USDT" if kind == "perpetual" else ""), asset_id=f"SMALL{index}", base=f"SMALL{index}",
                        quote="USDT", settle="USDT", kind=kind, mapping_verified=True) for index in range(count)]
    client = PublicClient(items, bulk_funding=bulk_funding)
    adapter = CexAdapter(venue, client_factory=lambda _: client)
    adapter._markets = {item.id: (venue, {"symbol": item.native_symbol}) for item in items}
    return adapter, client, items


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("watch_money.adapters.cex.now_ms", lambda: NOW)
    monkeypatch.setattr("watch_money.adapters.bulk_tickers.now_ms", lambda: NOW)
    monkeypatch.setattr("watch_money.collector.now_ms", lambda: NOW)


@pytest.mark.asyncio
async def test_thousand_product_markets_need_one_public_method_call_per_lane():
    adapter, client, items = adapter_for()
    for funding in (False, True):
        plan = adapter.plan_light(items, funding=funding, batch_size=20)
        assert len(plan) == 1 and plan[0].full_product and len(plan[0].instruments) == 1000
        result = await adapter.fetch_light_batch(plan[0], funding=funding)
        assert len(result.snapshots) == 1000 and not result.errors
    assert len(client.calls) == 2 and all(call[1] is None for call in client.calls)


def test_known_fallback_rate_limits_expose_unachievable_sixty_second_coverage():
    adapter, _client, items = adapter_for(1000, venue="mexc", bulk_funding=False)
    plan = adapter.plan_light(items, funding=True, batch_size=20)
    assert len(plan) == 50 and not any(batch.full_product for batch in plan)
    # MEXC has 50ms base limiter, cost=2; fake client uses 110ms, so
    # the conservative estimate may be higher than the official 100s minimum.
    assert adapter.minimum_light_cycle_seconds(plan, funding=True) >= 100
    adapter, _client, items = adapter_for(1000, bulk_funding=False)
    assert adapter.minimum_light_cycle_seconds(adapter.plan_light(items, funding=True), funding=True) >= 220


@pytest.mark.parametrize(('ages', 'expected', 'reason'), [
    ([0] * 10, 30, 'configured'),
    ([40] * 10, 15, 'source_near_expiry'),
    ([55] * 10, 5, 'source_near_expiry'),
    ([61] * 10, 5, 'upstream_source_stale'),
    ([], 30, 'source_age_unknown'),
    ([-10, float('nan')], 30, 'source_age_unknown'),
])
def test_okx_adaptive_policy_is_a_bounded_public_bulk_target(ages, expected, reason):
    adapter, _client, items = adapter_for(10)
    batch = adapter.plan_light(items, funding=True)[0]
    assert adapter.funding_poll_policy(batch, source_ages=ages, period=30, stale_seconds=60) == (expected, reason)
    # The faster policy must never fan out the reviewed ANY request into symbols.
    fallback = batch.__class__(batch.key, batch.instruments, False)
    assert adapter.funding_poll_policy(fallback, source_ages=ages, period=30, stale_seconds=60) == (30, 'configured')


def test_adaptive_response_ages_exclude_future_unknown_invalid_and_demo():
    adapter, _client, items = adapter_for(6)
    def row(item, **extra):
        return MarketSnapshot(instrument_id=item.id, venue='okx', funding_rate=0,
                              source='public:synthetic:funding', source_at=NOW - 55_000,
                              received_at=NOW, **extra)
    rows = [row(items[0]), row(items[1]).model_copy(update={'source_at': NOW - 61_000}),
            row(items[2]).model_copy(update={'field_timestamps': {'funding_rate': NOW + 1}}),
            row(items[3]).model_copy(update={'field_timestamps': {'funding_rate': None}}),
            row(items[4], is_demo=True), row(items[5], valid=False)]
    assert funding_response_source_ages(rows, {item.id: item for item in items}, NOW, 60) == [55, 61]
    adapter, _client, items = adapter_for(1, venue='bybit')
    assert adapter.funding_poll_policy(adapter.plan_light(items, funding=True)[0],
                                      source_ages=[55], period=30, stale_seconds=60) == (30, 'configured')


@pytest.mark.asyncio
async def test_okx_source_age_refresh_does_not_mask_stale_source_or_fetch_failure(tmp_path, monkeypatch):
    """Synthetic sequence: old-on-arrival -> expired -> request error -> recovery."""
    adapter, client, items = adapter_for(3)
    clock = [NOW]
    monkeypatch.setattr('watch_money.adapters.cex.now_ms', lambda: clock[0])
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: clock[0])
    source = [NOW - 55_000]
    client.rate = lambda: {'timestamp': source[0], 'fundingRate': 0, 'interval': '1h',
                           'fundingTimestamp': NOW + 3600_000}
    store = Storage(tmp_path)
    store.save_instruments(items)
    store.set('settings', Settings(enabled_venues=['okx'], history_enabled=False, broad_poll_seconds=30, funding_poll_seconds=30).model_dump())
    collector = Collector(store, {'okx': adapter})
    collector.discovered.add('okx')
    # A recent price envelope is no evidence that the separate rate is recent.
    for item in items:
        collector.snapshots[item.id] = MarketSnapshot(instrument_id=item.id, venue='okx', bid=100, ask=101,
            source='public:synthetic:ticker', source_at=NOW, received_at=NOW)
    checkpoints = []

    async def pause(delay):
        if asyncio.current_task().get_name() == 'funding:okx:bulk':
            status = collector.statuses['okx']
            checkpoints.append(status.model_dump())
            if len(checkpoints) == 1:
                assert 4 < delay <= 5
                clock[0] += 6_000
            elif len(checkpoints) == 2:
                assert 4 < delay <= 5
                client.failure = TimeoutError('synthetic public timeout')
            elif len(checkpoints) == 3:
                assert delay >= 30
                client.failure = None
                source[0] = clock[0]
            else:
                collector.stopping.set()
        await asyncio.sleep(.001)

    collector.pause = pause
    await asyncio.wait_for(collector.run_light('okx', adapter, funding=True), 5)
    near, stale, failed, recovered = checkpoints
    assert near['funding_fresh_markets'] == 3 and near['funding_arrival_source_age_p90_seconds'] == 55
    assert near['funding_schedule_reason'] == 'source_near_expiry' and not near['funding_errors']
    assert stale['funding_fresh_markets'] == 0 and stale['funding_source_stale_markets'] == 3
    assert stale['funding_arrival_source_age_max_seconds'] == 61
    assert stale['funding_schedule_reason'] == 'upstream_source_stale' and not stale['funding_errors']
    assert failed['funding_schedule_reason'] == 'request_backoff'
    assert set(failed['funding_errors'].values()) == {'TimeoutError'}
    assert failed['funding_arrival_checked_at'] == stale['funding_arrival_checked_at']
    assert failed['funding_arrival_source_age_max_seconds'] == 61
    assert recovered['funding_fresh_markets'] == 3 and not recovered['funding_errors']
    assert recovered['funding_arrival_source_age_p90_seconds'] == 0
    assert recovered['funding_effective_poll_seconds'] == 30 and recovered['funding_schedule_reason'] == 'configured'
    assert all(call[1] is None for call in client.calls) and len(client.calls) == 4
    assert Settings().stale_seconds == 60
    assert all(row.field_timestamps['bid'] == NOW for row in collector.snapshots.values())


@pytest.mark.asyncio
async def test_bulk_runtime_rejection_replans_without_unbounded_single_fanout():
    class NotSupported(Exception):
        pass

    adapter, client, items = adapter_for()
    client.failure = NotSupported("synthetic bulk rejection")
    result = await adapter.fetch_light_batch(adapter.plan_light(items)[0])
    assert result.error_type == "NotSupported" and len(client.calls) == 1
    assert all(call[0] != "single_ticker" for call in client.calls)
    fallback = adapter.plan_light(items, batch_size=20)
    assert len(fallback) == 50 and all(not batch.full_product for batch in fallback)


@pytest.mark.asyncio
async def test_slow_symbol_funding_does_not_hold_the_product_price_slot():
    adapter, client, perps = adapter_for(20, venue="mexc", bulk_funding=False)
    spots = [item.model_copy(update={"id": item.id + ":spot", "kind": "spot", "native_symbol": item.native_symbol.split(":")[0]}) for item in perps]
    client.items = perps + spots
    adapter._markets.update({item.id: ("mexc", {"symbol": item.native_symbol}) for item in spots})
    client.single_gate = asyncio.Event()
    task = asyncio.create_task(adapter.fetch_light_batch(adapter.plan_light(perps, funding=True)[0], funding=True))
    try:
        await asyncio.wait_for(client.started.wait(), .5)
        price = await asyncio.wait_for(adapter.fetch_light_batch(adapter.plan_light(spots)[0]), .5)
        assert len(price.snapshots) == 20
    finally:
        client.single_gate.set()
        await task


def test_read_time_coverage_rejects_new_last_price_over_old_quotes_and_ages_when_stopped(tmp_path, monkeypatch):
    adapter, _client, items = adapter_for(2)
    store = Storage(tmp_path)
    store.save_instruments(items)
    store.set("settings", Settings(enabled_venues=["okx"], collection_enabled=False).model_dump())
    collector = Collector(store, {"okx": adapter})
    collector.snapshots[items[0].id] = MarketSnapshot(instrument_id=items[0].id, venue="okx", bid=100, ask=101,
        last=100.5, funding_rate=.001, funding_kind="current", funding_interval_hours=8, source="synthetic",
        source_at=NOW, received_at=NOW,
        field_timestamps={"bid": NOW - 120_000, "ask": NOW - 120_000, "last": NOW})
    collector.snapshots[items[1].id] = collector.snapshots[items[0].id].model_copy(update={"instrument_id": items[1].id, "is_demo": True})
    collector.refresh_status_coverage()
    status = collector.statuses["okx"]
    assert status.light_observed_markets == 1 and status.light_fresh_markets == 0
    assert status.funding_fresh_markets == 1 and status.funding_coverage_ratio == .5
    monkeypatch.setattr("watch_money.collector.now_ms", lambda: NOW + 61_000)
    collector.refresh_status_coverage()
    assert status.funding_fresh_markets == 0 and status.funding_coverage_ratio == 0


def test_read_time_source_diagnostics_separate_stale_missing_future_without_rewriting_arrival_age(tmp_path):
    adapter, _client, items = adapter_for(6)
    store = Storage(tmp_path)
    store.save_instruments(items)
    collector = Collector(store, {'okx': adapter})
    base = MarketSnapshot(instrument_id=items[0].id, venue='okx', funding_rate=.001, funding_kind='current',
        funding_interval_hours=8, source='public:synthetic', source_at=NOW - 61_000, received_at=NOW)
    collector.snapshots = {
        items[0].id: base,
        items[1].id: base.model_copy(update={'instrument_id': items[1].id, 'field_timestamps': {'funding_rate': None}}),
        items[2].id: base.model_copy(update={'instrument_id': items[2].id, 'field_timestamps': {'funding_rate': NOW + 1}}),
        items[3].id: base.model_copy(update={'instrument_id': items[3].id, 'source': ''}),
        items[4].id: base.model_copy(update={'instrument_id': items[4].id, 'is_demo': True}),
        items[5].id: base.model_copy(update={'instrument_id': items[5].id, 'valid': False,
                                           'field_timestamps': {'funding_rate': NOW + 31_000}}),
    }
    status = collector.statuses['okx']
    status.funding_arrival_source_age_p90_seconds = 55
    status.funding_arrival_checked_at = NOW - 6_000
    collector.refresh_status_coverage()
    assert status.funding_observed_markets == 5 and status.funding_fresh_markets == 0
    assert status.funding_source_stale_markets == 1
    assert status.funding_source_missing_markets == 2
    assert status.funding_source_future_markets == 2
    assert status.funding_arrival_source_age_p90_seconds == 55
    assert status.funding_arrival_checked_at == NOW - 6_000


@pytest.mark.asyncio
async def test_catalog_plan_change_clears_old_completed_cycle_before_new_request(tmp_path):
    adapter, client, items = adapter_for(2, kind="spot")
    store = Storage(tmp_path)
    store.save_instruments(items)
    store.set("settings", Settings(enabled_venues=["okx"], history_enabled=False, broad_poll_seconds=30, funding_poll_seconds=30).model_dump())
    collector = Collector(store, {"okx": adapter})
    collector.discovered.add("okx")
    added = items[0].model_copy(update={"id": "new-market", "native_symbol": "NEW/USDT", "base": "NEW"})
    updated = False

    def before_request():
        if len(client.calls) == 2:
            assert collector.statuses["okx"].light_last_cycle_at is None
            assert collector.statuses["okx"].light_cycle_seconds is None
            collector.stopping.set()

    client.on_tickers = before_request

    async def pause(_delay):
        nonlocal updated
        if collector.statuses["okx"].light_last_cycle_at is not None and not updated:
            updated = True
            client.items.append(added)
            adapter._markets[added.id] = ("okx", {"symbol": added.native_symbol})
            collector.instruments[added.id] = added
        await asyncio.sleep(.001)

    collector.pause = pause
    await asyncio.wait_for(collector.run_light("okx", adapter), 3)
    assert updated and len(client.calls) == 2 and added.id in collector.snapshots
    assert collector.statuses["okx"].light_fresh_markets == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("period", [5, 30])
async def test_full_catalog_price_and_funding_sweeps_ignore_old_hundred_market_boundary(tmp_path, period):
    adapter, client, items = adapter_for(1000)
    store = Storage(tmp_path)
    store.save_instruments(items)
    store.set("settings", Settings(enabled_venues=["okx"], history_enabled=False, broad_poll_seconds=period, funding_poll_seconds=period).model_dump())
    collector = Collector(store, {"okx": adapter})
    collector.discovered.add("okx")

    async def pause(delay):
        status = collector.statuses["okx"]
        if status.light_last_cycle_at is not None and status.funding_last_cycle_at is not None:
            collector.stopping.set()
        # A completed sweep waits for its peer, regardless of how long parsing
        # took on this runner. Inferring completion from period-.5 races CPU load.
        if asyncio.current_task().get_name().endswith(':bulk'):
            await collector.stopping.wait()
        else:
            await asyncio.sleep(.001)

    collector.pause = pause
    await asyncio.wait_for(asyncio.gather(collector.run_light("okx", adapter),
                                        collector.run_light("okx", adapter, funding=True)), 5)
    status = collector.statuses["okx"]
    assert len(client.calls) == 2 and all(call[1] is None for call in client.calls)
    assert status.light_bulk_markets == status.funding_bulk_markets == 1000
    assert status.light_fresh_markets == status.funding_fresh_markets == 1000
    assert status.light_coverage_ratio == status.funding_coverage_ratio == 1
    assert status.light_estimated_cycle_seconds == status.funding_estimated_cycle_seconds == period
    assert Settings().stale_seconds == 60


def test_only_reviewed_all_payload_dex_paths_get_product_bulk_plans():
    from watch_money.adapters.dex import HyperliquidAdapter, LighterAdapter, DydxAdapter, GMXAdapter, EdgeXAdapter

    for adapter in (LighterAdapter(), DydxAdapter(), GMXAdapter()):
        _cex, _client, items = adapter_for(1000, venue="okx")
        items = [item.model_copy(update={"venue": adapter.venue}) for item in items]
        plan = adapter.plan_light(items, batch_size=20)
        assert len(plan) == 1 and plan[0].full_product and len(plan[0].instruments) == 1000
    adapter = HyperliquidAdapter()
    _cex, _client, items = adapter_for(1000)
    items = [item.model_copy(update={"venue": adapter.venue, "native_symbol":
             ("xyz:" if index % 2 else "") + item.base}) for index, item in enumerate(items)]
    assert len(adapter.plan_light(items, batch_size=20)) == 2
    assert not EdgeXAdapter().layered_light
    assert Settings().broad_batch_size == 100 and Settings().broad_symbol_batch_size == 20
