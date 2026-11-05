"""Saved-plan monitoring tests; all market inputs and external sends are simulated."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from watch_money import monitors as module
from watch_money.models import Instrument, MarketSnapshot, OrderBook, Settings
from watch_money.monitors import Monitor, MonitorService, router
from watch_money.notifications import NotificationRule, NotificationService
from watch_money.storage import Storage
from watch_money.strategies import CalculateRequest, calculate

NOW = 1_800_000_000_000


class Clock:
    def __init__(self):
        self.at = NOW

    def advance(self, seconds):
        self.at += int(seconds * 1000)


class Store:
    def __init__(self):
        self.lock = RLock()
        self.values = {"settings": Settings(enabled_venues=["alpha", "beta"]).model_dump()}

    def get(self, key, default=None):
        return deepcopy(self.values.get(key, default))

    def set(self, key, value):
        self.values[key] = deepcopy(value)

    def settings(self):
        return Settings.model_validate(self.get("settings"))

    def configure(self, **changes):
        self.set("settings", {**self.get("settings"), **changes})


def instrument(identity, venue):
    return Instrument(id=identity, venue=venue, native_symbol="BTC/USD", asset_id="bitcoin", base="BTC",
                      quote="USD", kind="spot", mapping_verified=True)


def snapshot(inst, at):
    return MarketSnapshot(instrument_id=inst.id, venue=inst.venue, bid=999, ask=1000,
                          source="fixture:collector", source_at=at, received_at=at)


class Adapter:
    def __init__(self, clock, identity, bid, ask):
        self.clock, self.identity, self.bid, self.ask = clock, identity, bid, ask
        self.calls = 0
        self.failure = False
        self.stale = False
        self.wrong_identity = False
        self.entered = None
        self.release = None

    async def orderbook(self, inst):
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
            await self.release.wait()
        if self.failure:
            raise httpx.ConnectError("fixture private endpoint must not enter monitor errors")
        at = self.clock.at - (120_000 if self.stale else 0)
        return OrderBook(instrument_id="unexpected" if self.wrong_identity else inst.id,
                         bids=[(self.bid, 10)], asks=[(self.ask, 10)], source="fixture:live-depth",
                         source_at=at, received_at=self.clock.at)


def template():
    old = NOW - 1_000_000
    instruments = [instrument("a", "alpha"), instrument("b", "beta")]
    return CalculateRequest(instruments=instruments, snapshots=[snapshot(i, old) for i in instruments],
        buy_instrument_id="a", sell_instrument_id="b", size_base=1, valuation_currency="USD", as_of=old,
        orderbooks=[OrderBook(instrument_id="a", bids=[(9, 10)], asks=[(10, 10)], source="saved:old-depth",
                              source_at=old, received_at=old),
                    OrderBook(instrument_id="b", bids=[(1000, 10)], asks=[(1001, 10)], source="saved:old-depth",
                              source_at=old, received_at=old)],
        route_quotes=[{"instrument_id": "b", "side": "sell", "input_asset": "BTC", "output_asset": "USD",
                       "input_amount": 1, "output_amount": 1000, "route": ["saved:old-pool"],
                       "block_number": 10, "source": "saved:old-route", "source_at": old, "received_at": old}],
        costs=[{"instrument_id": "a", "open_fee_rate": .001, "close_fee_rate": .001},
               {"instrument_id": "b", "open_fee_rate": .001, "close_fee_rate": .001}],
        funding_events=[{"instrument_id": "a", "settlement_at": old, "rate": .9,
                         "kind": "settled", "known_at": old, "source": "saved:old-funding"}],
        funding_schedule_complete_until={"a": old}, funding_schedule_known_at={"a": old})


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(module, "now_ms", lambda: clock.at)
    store = Store()
    req = template()
    collector = SimpleNamespace(instruments={i.id: i.model_copy(deep=True) for i in req.instruments},
                                snapshots={i.id: snapshot(i, clock.at) for i in req.instruments})
    collector.adapters = {"alpha": Adapter(clock, "a", 99, 100), "beta": Adapter(clock, "b", 120, 121)}
    notifications = NotificationService(tmp_path, clock=lambda: clock.at / 1000)
    service = MonitorService(store, collector, notifications)
    return SimpleNamespace(clock=clock, store=store, req=req, collector=collector,
                           notifications=notifications, service=service, tmp_path=tmp_path)


def refresh(env):
    env.collector.snapshots = {i.id: snapshot(i, env.clock.at) for i in env.collector.instruments.values()}


def save(env, **changes):
    monitor = Monitor(id="watch-btc", name="BTC depth spread", calculation=env.req, **changes)
    env.service.save(monitor)
    return monitor


async def test_current_depth_replaces_saved_quotes_without_mutating_template(env, monkeypatch):
    observed = []

    def capture(req):
        observed.append(req.model_copy(deep=True))
        return calculate(req)

    monkeypatch.setattr(module, "calculate", capture)
    before = env.req.model_dump()
    save(env)
    await env.service.tick()
    opportunity = env.service.list()["results"][0]["opportunities"][0]
    assert opportunity["net_profit"] == pytest.approx(19.78)
    assert opportunity["legs"][0]["source"] == "fixture:live-depth"
    assert opportunity["legs"][0]["average_price"] == 100
    assert opportunity["legs"][1]["average_price"] == 120
    assert observed[0].as_of == env.clock.at
    assert {b.source for b in observed[0].orderbooks} == {"fixture:live-depth"}
    assert observed[0].funding_events == []
    assert observed[0].funding_schedule_complete_until == {}
    assert observed[0].route_quotes == []
    assert env.req.model_dump() == before
    assert env.service.list()["monitors"][0]["calculation"] == before
    await env.notifications.stop()


async def test_calculator_rule_queue_and_restart_persist_local_only(env):
    env.notifications.save_rule(NotificationRule(name="Executable profit", event_types=["opportunity"],
                                                min_net_profit=10, cooldown_seconds=0))
    save(env)
    await env.service.tick()
    history = env.notifications.history()
    assert len(history["notifications"]) == 1
    notification = history["notifications"][0]
    assert notification["event"]["net_profit"] == pytest.approx(19.78)
    assert notification["event"]["valuation_currency"] == "USD"
    assert notification["event"]["capacity_unit"] == "base"
    assert {d["channel"] for d in history["deliveries"]} == {"local"}
    assert history["deliveries"][0]["status"] == "pending"
    await env.notifications.stop()
    restored = NotificationService(env.tmp_path, clock=lambda: env.clock.at / 1000)
    assert restored.history()["notifications"][0]["id"] == notification["id"]
    await restored.process_pending()
    assert restored.history()["deliveries"][0]["status"] == "sent"
    await restored.stop()


async def test_interval_refresh_changes_net_and_deduplicates_unchanged_update(env):
    env.notifications.save_rule(NotificationRule(name="Changes", min_net_profit=1, cooldown_seconds=0))
    save(env)
    await env.service.tick()
    calls = sum(a.calls for a in env.collector.adapters.values())
    await env.service.tick()
    assert sum(a.calls for a in env.collector.adapters.values()) == calls
    env.clock.advance(31)
    refresh(env)
    env.collector.adapters["beta"].bid = 125
    env.collector.adapters["beta"].ask = 126
    await env.service.tick()
    opportunities = env.service.list()["results"][0]["opportunities"]
    assert opportunities[0]["net_profit"] == pytest.approx(24.775)
    assert len(env.notifications.history()["notifications"]) == 2
    env.clock.advance(31)
    refresh(env)
    await env.service.tick()
    assert len(env.notifications.history()["notifications"]) == 2
    await env.notifications.stop()


@pytest.mark.parametrize("failure", ["stale", "failure", "wrong_identity"])
async def test_stale_or_failed_books_invalidate_and_trigger_lifecycle_rule(env, failure):
    env.notifications.save_rule(NotificationRule(name="Profit", event_types=["opportunity"], min_net_profit=1))
    env.notifications.save_rule(NotificationRule(name="Lost opportunity", event_types=["opportunity_invalidated"]))
    save(env)
    await env.service.tick()
    assert len(env.notifications.history()["notifications"]) == 1
    env.clock.advance(31)
    refresh(env)
    setattr(env.collector.adapters["alpha"], failure, True)
    await env.service.tick()
    record = env.service.list()["results"][0]
    opportunity = record["opportunities"][0]
    assert opportunity["net_profit"] is None
    assert opportunity["status"] in {"invalidated", "data_incomplete"}
    if failure != "stale":
        assert record["errors"]
        assert "private endpoint" not in str(record["errors"])
    notifications = env.notifications.history()["notifications"]
    assert len(notifications) == 2
    assert notifications[0]["event"]["type"] == "opportunity_invalidated"
    assert notifications[0]["event"]["source_at"] == env.clock.at
    await env.notifications.stop()


@pytest.mark.parametrize("changes", [{"computation_enabled": False}, {"demo_mode": True}])
async def test_disabled_computation_and_demo_make_no_adapter_calls(env, changes):
    save(env)
    env.store.configure(**changes)
    await env.service.tick()
    assert all(adapter.calls == 0 for adapter in env.collector.adapters.values())
    assert env.service.list()["results"] == []
    assert env.notifications.history()["notifications"] == []
    await env.notifications.stop()


async def test_disabled_monitor_and_notifications_are_independent(env):
    save(env, enabled=False)
    await env.service.tick()
    assert all(adapter.calls == 0 for adapter in env.collector.adapters.values())
    save(env, enabled=True)
    env.store.configure(notifications_enabled=False)
    env.notifications.save_rule(NotificationRule(name="Would match", min_net_profit=1))
    await env.service.tick()
    assert env.service.list()["results"][0]["opportunities"][0]["net_profit"] is not None
    assert env.notifications.history()["notifications"] == []
    await env.notifications.stop()


async def test_disabled_venue_does_not_reuse_old_saved_executable_book(env):
    save(env)
    env.store.configure(enabled_venues=["beta"])
    await env.service.tick()
    record = env.service.list()["results"][0]
    assert env.collector.adapters["alpha"].calls == 0
    assert env.collector.adapters["beta"].calls == 1
    assert record["opportunities"][0]["net_profit"] is None
    assert any("alpha: disabled" in e for e in record["errors"])
    await env.notifications.stop()


async def test_saved_amm_route_is_not_reused_without_live_route_provider(env):
    for instruments in (env.req.instruments, list(env.collector.instruments.values())):
        sell = next(i for i in instruments if i.id == "b")
        sell.kind, sell.mechanism, sell.chain = "pool", "amm", "ethereum"
    env.req.strategy = "cex_dex_spot"
    save(env)
    await env.service.tick()
    record = env.service.list()["results"][0]
    assert env.collector.adapters["beta"].calls == 0
    assert record["opportunities"][0]["net_profit"] is None
    assert any("live size-specific route provider not configured" in error for error in record["errors"])
    await env.notifications.stop()


async def test_restart_restores_monitor_config_and_last_result(env, tmp_path):
    first = Storage(tmp_path / "store")
    first.set("settings", env.store.settings().model_dump())
    service = MonitorService(first, env.collector, env.notifications)
    monitor = Monitor(id="persistent", name="Restart", calculation=env.req)
    service.save(monitor)
    await service.tick()
    restored = MonitorService(Storage(tmp_path / "store"), env.collector, env.notifications)
    records = restored.list()
    assert records["monitors"][0]["id"] == "persistent"
    assert records["monitors"][0]["calculation"] == env.req.model_dump(mode="json")
    assert records["results"][0]["opportunities"][0]["net_profit"] == pytest.approx(19.78)
    await restored.tick()
    assert len(restored.list()["results"]) == 1
    restored.delete("persistent")
    assert restored.list() == {"monitors": [], "results": []}
    await env.notifications.stop()


async def test_monitor_limit_allows_update_and_slot_reuse_and_api_reports_limit(env):
    for index in range(100):
        env.service.save(Monitor(id=f"monitor-{index}", name=f"Monitor {index}", calculation=env.req))
    assert len(env.service.list()["monitors"]) == 100
    env.service.save(Monitor(id="monitor-0", name="Updated", calculation=env.req))
    with pytest.raises(ValueError, match="100"):
        env.service.save(Monitor(id="overflow", name="Overflow", calculation=env.req))
    app = FastAPI()
    app.state.monitors = env.service
    app.state.store = env.store
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = Monitor(id="overflow", name="Overflow", calculation=env.req).model_dump(mode="json")
        assert (await client.post("/api/monitors", json=payload)).status_code == 422
        assert (await client.delete("/api/monitors/monitor-0")).status_code == 200
        assert (await client.post("/api/monitors", json=payload)).status_code == 200
        response = await client.get("/api/monitors")
        assert response.status_code == 200
        assert len(response.json()["monitors"]) == 100
    await env.notifications.stop()


async def test_concurrent_saves_preserve_accepted_plans_and_enforce_cap(env):
    plans = [Monitor(id=f"parallel-{i}", name=f"Parallel {i}", calculation=env.req) for i in range(120)]

    def attempt(plan):
        try:
            env.service.save(plan)
        except ValueError:
            return None
        return plan.id

    def save_all():
        with ThreadPoolExecutor(max_workers=16) as executor:
            return list(executor.map(attempt, plans))

    accepted = {identity for identity in await asyncio.to_thread(save_all) if identity is not None}
    persisted = {monitor["id"] for monitor in env.service.list()["monitors"]}
    assert len(accepted) == 100
    assert persisted == accepted
    await env.notifications.stop()


@pytest.mark.parametrize("mutation", ["delete", "update"])
async def test_inflight_deleted_or_replaced_plan_cannot_publish_old_results(env, mutation):
    env.notifications.save_rule(NotificationRule(name="Potential stale-plan alert", min_net_profit=1))
    save(env)
    adapter = env.collector.adapters["alpha"]
    adapter.entered, adapter.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(env.service.tick())
    await asyncio.wait_for(adapter.entered.wait(), timeout=2)
    if mutation == "delete":
        env.service.delete("watch-btc")
    else:
        updated = env.req.model_copy(deep=True)
        updated.size_base = 2
        env.service.save(Monitor(id="watch-btc", name="Replaced plan", calculation=updated))
    adapter.release.set()
    await asyncio.wait_for(task, timeout=2)
    assert env.service.list()["results"] == [], "Discard results computed for a deleted or replaced plan"
    assert env.notifications.history()["notifications"] == []
    await env.notifications.stop()
