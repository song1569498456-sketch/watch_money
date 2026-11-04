"""Public bulk/rotation simulations, not live performance certification."""
import asyncio

import pytest

from watch_money.adapters.cex import CexAdapter
from watch_money.collector import Collector
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


@pytest.mark.asyncio
async def test_catalog_plan_change_clears_old_completed_cycle_before_new_request(tmp_path):
    adapter, client, items = adapter_for(2, kind="spot")
    store = Storage(tmp_path)
    store.save_instruments(items)
    store.set("settings", Settings(enabled_venues=["okx"], history_enabled=False).model_dump())
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
async def test_full_catalog_price_and_funding_sweeps_ignore_old_hundred_market_boundary(tmp_path):
    adapter, client, items = adapter_for(1000)
    store = Storage(tmp_path)
    store.save_instruments(items)
    store.set("settings", Settings(enabled_venues=["okx"], history_enabled=False).model_dump())
    collector = Collector(store, {"okx": adapter})
    collector.discovered.add("okx")

    async def pause(delay):
        status = collector.statuses["okx"]
        if status.light_last_cycle_at is not None and status.funding_last_cycle_at is not None:
            collector.stopping.set()
        if delay >= 10:
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
    assert status.light_estimated_cycle_seconds == status.funding_estimated_cycle_seconds == 30
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
