"""Independent acceptance checks. Fixtures do not certify live API or delivery."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from watch_money.models import Instrument, MarketSnapshot, OrderBook


def instrument(id: str, venue: str, quote: str = "USD", **overrides):
    values = dict(id=id, venue=venue, native_symbol="BTC/" + quote,
                  asset_id="bitcoin", base="BTC", quote=quote, kind="spot",
                  mapping_verified=True)
    values.update(overrides)
    if values["kind"] == "perpetual" and "settle" not in overrides:
        values["settle"] = quote
    return Instrument(**values)


def snapshot(id: str, venue: str, at: int, **overrides):
    values = dict(instrument_id=id, venue=venue, received_at=at,
                  source_at=at, source="independent recorded fixture")
    values.update(overrides)
    return MarketSnapshot(**values)


def book(id: str, at: int, bids=None, asks=None, **overrides):
    values = dict(instrument_id=id, received_at=at, source_at=at,
                  bids=bids or [(99.0, 10.0)], asks=asks or [(100.0, 10.0)],
                  source="independent recorded fixture")
    values.update(overrides)
    return OrderBook(**values)


def test_missing_market_values_remain_unknown_and_provenance_roundtrips():
    value = snapshot("venue:BTC", "venue", 1_700_000_000_000,
                     field_sources={"last": "direct ticker"}, is_demo=True)
    restored = MarketSnapshot.model_validate_json(value.model_dump_json())
    assert restored.last is None
    assert restored.bid is None
    assert restored.funding_rate is None
    assert restored.funding_interval_hours is None
    assert restored.is_demo is True
    assert restored.field_sources == {"last": "direct ticker"}
    assert restored.source_at == 1_700_000_000_000


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_market_prices_rejected(value):
    with pytest.raises(ValidationError):
        snapshot("venue:BTC", "venue", 1_700_000_000_000, last=value)


def test_staleness_uses_source_age_and_unknown_interval_is_not_guessed(monkeypatch):
    from watch_money import collector
    at = 1_700_000_000_000
    monkeypatch.setattr(collector, "now_ms", lambda: at)
    old = snapshot("a", "alpha", at, source_at=at - 120_000, funding_rate=0.001)
    enriched = collector.enrich(collector.quality(old), stale_seconds=60)
    assert enriched["stale"] is True
    assert enriched["age_seconds"] == 120
    assert enriched["funding_8h"] is None
    assert "funding_interval_not_provided" in enriched["issues"]
    short_cycle = old.model_copy(update={"funding_interval_hours": 1})
    assert collector.enrich(short_cycle, 60)["funding_8h"] == pytest.approx(0.008)


def test_crossed_and_future_market_sources_are_explicitly_invalid(monkeypatch):
    from watch_money import collector
    at = 1_700_000_000_000
    monkeypatch.setattr(collector, "now_ms", lambda: at)
    crossed = collector.quality(snapshot("a", "alpha", at, bid=102, ask=100))
    future = collector.quality(snapshot("a", "alpha", at, source_at=at + 60_000))
    assert not crossed.valid and "crossed_orderbook" in crossed.issues
    assert not future.valid and "source_timestamp_in_future" in future.issues


def test_storage_restart_preserves_mapping_audit_settings_and_asof_history(tmp_path):
    from watch_money.storage import Storage
    from watch_money.models import Settings, now_ms
    store = Storage(tmp_path)
    at = now_ms() - 10_000
    store.save_instruments([instrument("a", "alpha", asset_id="alpha:BTC", mapping_verified=False)])
    store.set("settings", Settings(enabled_venues=["alpha"], poll_seconds=30).model_dump())
    store.map_asset("a", "bitcoin")
    store.map_asset("a", "reviewed:bitcoin")
    first = snapshot("a", "alpha", at, last=100, is_demo=True, field_sources={"last": "fixture"})
    second = snapshot("a", "alpha", at + 1000, last=102)
    store.save_snapshots([first, second])
    restored = Storage(tmp_path)
    assert restored.instruments()["a"].asset_id == "reviewed:bitcoin"
    assert restored.instruments()["a"].mapping_verified
    assert restored.audit()[0]["payload"]["previous"] == "bitcoin"
    assert restored.settings().poll_seconds == 30
    assert restored.latest()["a"].last == 102
    history = restored.history("a", before=at)
    assert len(history) == 1
    assert history[0]["last"] == 100
    assert history[0]["is_demo"] is True
    assert history[0]["field_sources"] == {"last": "fixture"}
    assert len(list((tmp_path / "history").glob("date=*/venue=*/*.parquet"))) == 1


@pytest.mark.asyncio
async def test_collector_rate_limit_retries_exponentially_and_recovers(tmp_path, monkeypatch):
    import httpx
    from watch_money.collector import Collector
    from watch_money.storage import Storage
    from watch_money.models import Settings, now_ms
    from watch_money import collector as module
    monkeypatch.setattr(module.random, "uniform", lambda *_: 0)
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["alpha"], poll_seconds=5).model_dump())

    class Adapter:
        calls = 0
        async def discover(self):
            return [instrument("a", "alpha")]
        async def fetch(self, _):
            self.calls += 1
            if self.calls <= 2:
                response = httpx.Response(429, request=httpx.Request("GET", "https://fixture.invalid"))
                raise httpx.HTTPStatusError("credential=not-for-display", request=response.request, response=response)
            return [snapshot("a", "alpha", now_ms(), bid=99, ask=100)]
        async def close(self):
            pass

    adapter = Adapter()
    collector = Collector(store, {"alpha": adapter})
    delays = []
    async def pause(seconds):
        delays.append(seconds)
        if adapter.calls == 3:
            collector.stopping.set()
    collector.pause = pause
    await collector.run("alpha", adapter)
    assert delays == [5, 10, 5]
    status = collector.statuses["alpha"]
    assert status.state == "online"
    assert status.retries == 2 and status.rate_limited == 2
    assert status.message is None
    assert collector.snapshots["a"].last is None


@pytest.mark.asyncio
async def test_notification_retry_after_survives_restart_and_dedup_cooldown(tmp_path, monkeypatch):
    import httpx
    from watch_money.notifications import NotificationRule, NotificationService
    clock = [1_700_000_000.0]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fixture-chat")
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"ok": False, "error_code": 429,
                                             "parameters": {"retry_after": 30}})
        return httpx.Response(200, json={"ok": True})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = NotificationService(tmp_path, client=client, clock=lambda: clock[0])
    rule = NotificationRule(id="independent", name="Independent threshold", min_net_profit=10,
                            channels=["telegram"], cooldown_seconds=60)
    service.save_rule(rule)
    event = {"id": "stable-opportunity", "category": "opportunity", "asset": "BTC", "valid": True,
             "net_profit": 20, "source_at": int(clock[0] * 1000)}
    assert len(await service.evaluate(event)) == 1
    await service.process_pending()
    delivery = next(x for x in service.history()["deliveries"] if x["channel"] == "telegram")
    assert delivery["status"] == "retry"
    assert delivery["next_attempt_at"] >= int((clock[0] + 30) * 1000)
    await service.stop()
    restarted = NotificationService(tmp_path, client=client, clock=lambda: clock[0])
    assert restarted.rules()[0]["id"] == "independent"
    assert await restarted.evaluate(event) == []
    clock[0] += 15
    await restarted.process_pending()
    assert len(calls) == 1
    assert await restarted.evaluate({**event, "net_profit": 30, "source_at": int(clock[0] * 1000)}) == []
    clock[0] += 16
    await restarted.process_pending()
    assert len(calls) == 2
    delivery = next(x for x in restarted.history()["deliveries"] if x["channel"] == "telegram")
    assert delivery["status"] == "sent" and delivery["attempts"] == 2
    clock[0] += 31
    assert len(await restarted.evaluate({**event, "net_profit": 40,
                                         "source_at": int(clock[0] * 1000)})) == 1
    await restarted.stop()
    await client.aclose()


@pytest.mark.asyncio
async def test_notification_missing_stale_invalid_data_cannot_trigger(tmp_path):
    from watch_money.notifications import NotificationRule, NotificationService
    at = 1_700_000_000
    service = NotificationService(tmp_path, clock=lambda: at)
    service.save_rule(NotificationRule(name="Only actionable", min_net_profit=1))
    event = {"id": "one", "category": "opportunity", "net_profit": 20, "source_at": at * 1000}
    for override in ({"net_profit": None}, {"source_at": (at - 61) * 1000}, {"valid": False}):
        assert await service.evaluate({**event, **override}) == []
    assert service.history() == {"notifications": [], "deliveries": []}
    await service.stop()


def calculation_request(**overrides):
    from watch_money.strategies import CalculateRequest
    at = 1_700_000_000_000
    values = dict(instruments=[instrument("a", "alpha"), instrument("b", "beta")],
                  buy_instrument_id="a", sell_instrument_id="b", size_base=1, as_of=at,
                  snapshots=[snapshot("a", "alpha", at), snapshot("b", "beta", at)],
                  orderbooks=[book("a", at, asks=[(100, 1), (110, 1)]),
                              book("b", at, bids=[(120, 2)], asks=[(121, 2)])],
                  costs=[dict(instrument_id="a", open_fee_rate=.001, close_fee_rate=.001,
                              source="explicit fee scenario", at=at),
                         dict(instrument_id="b", open_fee_rate=.001, close_fee_rate=.001,
                              source="explicit fee scenario", at=at)])
    values.update(overrides)
    return CalculateRequest(**values)


def test_calculator_executes_depth_and_scale_changes_net_return():
    from watch_money.strategies import calculate
    req = calculation_request(sizes_base=[1, 2, 3])
    small, large, excessive = calculate(req)["opportunities"]
    assert small["gross_profit"] == pytest.approx(20)
    assert small["net_profit"] == pytest.approx(19.78)
    assert large["legs"][0]["average_price"] == pytest.approx(105)
    assert large["net_profit"] == pytest.approx(29.55)
    assert excessive["net_profit"] is None
    assert excessive["capacity_base"] == 2
    assert excessive["status"] == "data_incomplete"


def test_calculator_requires_exact_asset_identity_and_explicit_fx():
    from watch_money.strategies import calculate, FXRate
    req = calculation_request(instruments=[instrument("a", "alpha", asset_id="alpha:BTC", mapping_verified=False),
                                          instrument("b", "beta", asset_id="beta:BTC", mapping_verified=False)])
    assert calculate(req)["opportunities"][0]["status"] == "invalidated"
    req = calculation_request(instruments=[instrument("a", "alpha"), instrument("b", "beta", quote="USDT")])
    result = calculate(req)["opportunities"][0]
    assert result["net_profit"] is None
    assert any("FX" in issue for issue in result["missing_prerequisites"])
    req.fx_rates = [FXRate(base="USDT", quote="USD", rate=.99, at=req.as_of, source="explicit FX fixture")]
    assert calculate(req)["opportunities"][0]["net_profit"] == pytest.approx(18.5812)


def test_calculator_funding_actual_settlement_direction_and_four_fees():
    from watch_money.strategies import calculate
    at = 1_700_000_000_000
    req = calculation_request(strategy="funding_spread", hold_hours=8, assume_exit_at_entry=True,
        instruments=[instrument("a", "alpha", kind="perpetual"), instrument("b", "beta", kind="perpetual")],
        orderbooks=[book("a", at), book("b", at, bids=[(100, 10)], asks=[(101, 10)])],
        snapshots=[snapshot("a", "alpha", at, funding_rate=.001, funding_kind="current", mark=100,
                            funding_interval_hours=1, next_funding_at=at + 3_600_000),
                   snapshot("b", "beta", at, funding_rate=.002, funding_kind="predicted", mark=100,
                            funding_interval_hours=8, next_funding_at=at + 2 * 3_600_000)])
    result = calculate(req)["opportunities"][0]
    assert len(result["funding_events"]) == 9
    assert sum(e["cashflow"] for e in result["funding_events"] if e["position"] == "long") == pytest.approx(-.8)
    assert sum(e["cashflow"] for e in result["funding_events"] if e["position"] == "short") == pytest.approx(.2)
    assert result["funding_profit"] == pytest.approx(-.6)
    assert result["known_costs"] == pytest.approx(.4)
    assert result["net_profit"] == pytest.approx(-1)
    req.snapshots[1].funding_interval_hours = None
    assert calculate(req)["opportunities"][0]["net_profit"] is None


@pytest.mark.parametrize("broken", ["crossed", "negative", "stale", "costs"])
def test_calculator_rejects_invalid_or_incomplete_inputs(broken):
    from watch_money.strategies import calculate
    req = calculation_request()
    if broken == "crossed":
        req.orderbooks[0].bids = [(200, 10)]
    elif broken == "negative":
        req.orderbooks[0].asks = [(100, -1)]
    elif broken == "stale":
        req.orderbooks[0].source_at = req.as_of - 61_000
    else:
        req.costs = []
    result = calculate(req)["opportunities"][0]
    assert result["net_profit"] is None
    assert result["status"] in {"invalidated", "cost_pending", "data_incomplete"}


def test_api_settings_websocket_history_mapping_and_calculation_notifications(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from watch_money.app import create_app
    from watch_money.models import now_ms
    monkeypatch.setenv("WATCH_MONEY_COLLECT", "0")
    app = create_app(tmp_path, start_collector=False, adapters={})
    at = now_ms()
    inst = instrument("a", "alpha", asset_id="alpha:BTC", mapping_verified=False)
    app.state.store.save_instruments([inst])
    app.state.store.save_snapshots([snapshot("a", "alpha", at, last=100)])
    app.state.collector.instruments = app.state.store.instruments()
    app.state.collector.snapshots = app.state.store.latest()
    with TestClient(app) as client:
        assert client.get("/api/health").json()["mode"] == "live"
        settings = client.get("/api/settings").json()
        settings["enabled_venues"] = ["alpha", "beta"]
        assert client.put("/api/settings", json=settings).status_code == 200
        assert client.get("/api/markets").json()["snapshots"][0]["last"] == 100
        with client.websocket_connect("/ws") as websocket:
            update = websocket.receive_json()
            assert update["type"] == "markets" and update["snapshots"][0]["source"].startswith("independent")
        mapped = client.put("/api/assets/mappings", json={"instrument_id": "a", "asset_id": "bitcoin"})
        assert mapped.status_code == 200
        assert mapped.json()["instrument"]["mapping_verified"] is True
        assert client.get("/api/history", params={"instrument_id": "a"}).json()["snapshots"][0]["last"] == 100
        assert client.put("/api/favorites", json={"instrument_ids": ["a", "a"]}).json() == {"instrument_ids": ["a"]}
        rule = {"id": "api-rule", "name": "API integration", "min_net_profit": 1, "channels": ["local"]}
        assert client.post("/api/notifications/rules", json=rule).status_code == 200
        req = calculation_request(as_of=at,
              snapshots=[snapshot("a", "alpha", at), snapshot("b", "beta", at)],
              orderbooks=[book("a", at), book("b", at, bids=[(120, 2)], asks=[(121, 2)])])
        result = client.post("/api/opportunities/calculate", json=req.model_dump(mode="json"))
        assert result.status_code == 200
        assert result.json()["opportunities"][0]["net_profit"] == pytest.approx(19.78)
        notifications = client.get("/api/notifications/history").json()["notifications"]
        assert len(notifications) == 1, "A calculated opportunity must reach a matching local rule"
        settings["computation_enabled"] = False
        client.put("/api/settings", json=settings)
        assert client.post("/api/opportunities/calculate", json=req.model_dump(mode="json")).status_code == 409
    restored = create_app(tmp_path, start_collector=False, adapters={})
    with TestClient(restored) as client:
        assert client.get("/api/favorites").json() == {"instrument_ids": ["a"]}
        assert client.get("/api/settings").json()["computation_enabled"] is False


def test_calculator_stale_funding_field_is_not_refreshed_by_fresh_ticker():
    from watch_money.strategies import calculate
    at = 1_700_000_000_000
    req = calculation_request(strategy="funding_spread", hold_hours=8, assume_exit_at_entry=True,
        instruments=[instrument("a", "alpha", kind="perpetual"), instrument("b", "beta", kind="perpetual")],
        snapshots=[snapshot("a", "alpha", at, funding_rate=.001, funding_kind="current", mark=100,
                            funding_interval_hours=1, next_funding_at=at + 3_600_000,
                            field_timestamps={"funding_rate": at - 120_000}),
                   snapshot("b", "beta", at, funding_rate=.002, funding_kind="predicted", mark=100,
                            funding_interval_hours=8, next_funding_at=at + 2 * 3_600_000)])
    result = calculate(req)["opportunities"][0]
    assert result["net_profit"] is None, "Old funding may not inherit the fresh ticker timestamp"


def test_calculator_contract_multiplier_changes_executable_base_capacity():
    from watch_money.strategies import calculate
    at = 1_700_000_000_000
    req = calculation_request(strategy="perp_spread", size_base=.03, assume_exit_at_entry=True,
        instruments=[instrument("a", "alpha", kind="perpetual", contract_size=.01),
                     instrument("b", "beta", kind="perpetual", contract_size=.01)],
        orderbooks=[book("a", at, asks=[(100, 2)], quantity_unit="contracts"),
                    book("b", at, bids=[(120, 4)], asks=[(121, 4)], quantity_unit="contracts")])
    result = calculate(req)["opportunities"][0]
    assert result["capacity_base"] == pytest.approx(.02)
    assert result["net_profit"] is None
    assert result["legs"][0]["filled_base"] == pytest.approx(.02)
    assert result["legs"][0]["contracts"] == 2


def test_calculator_exact_dex_quote_fee_and_price_impact_are_not_charged_twice():
    from watch_money.strategies import calculate
    at = 1_700_000_000_000
    req = calculation_request(strategy="cex_dex_spot",
        instruments=[instrument("a", "alpha"),
                     instrument("b", "beta", kind="pool", mechanism="amm", chain="ethereum")],
        route_quotes=[dict(instrument_id="b", side="sell", input_asset="BTC", output_asset="USD",
                           input_amount=1, output_amount=120, route=["ethereum/pool-address"], block_number=100,
                           source="explicit quote fixture", source_at=at, received_at=at,
                           fees_included=True, gas_amount=2, gas_currency="USD")])
    result = calculate(req)["opportunities"][0]
    assert result["net_profit"] == pytest.approx(17.9)
    assert result["known_costs"] == pytest.approx(2.1)
    req.size_base = 2
    assert calculate(req)["opportunities"][0]["net_profit"] is None


def test_api_saved_sample_uses_app_directory_and_replay_filters_unobserved_book(tmp_path):
    from fastapi.testclient import TestClient
    from watch_money.app import create_app
    req = calculation_request()
    app = create_app(tmp_path / "isolated", start_collector=False, adapters={})
    with TestClient(app) as client:
        response = client.post("/api/opportunities/saved", json={"name": "independent sample",
                                                              "calculation": req.model_dump(mode="json")})
        assert response.status_code == 200
        saved = response.json()
        assert (tmp_path / "isolated" / "opportunities.sqlite3").exists()
        exact = client.post("/api/opportunities/replay", json={"sample_id": saved["id"], "as_of": req.as_of})
        assert exact.json()["opportunities"] == saved["result"]["opportunities"]
        delayed = client.post("/api/opportunities/replay", json={"sample_id": saved["id"], "as_of": req.as_of,
                                                               "receive_delay_ms": 1})
        assert delayed.json()["opportunities"][0]["net_profit"] is None
        assert delayed.json()["replay"]["information_cutoff"] == req.as_of - 1
    reopened = create_app(tmp_path / "isolated", start_collector=False, adapters={})
    with TestClient(reopened) as client:
        assert client.get("/api/opportunities/saved").json()["samples"][0]["id"] == saved["id"]


@pytest.mark.asyncio
async def test_collector_rejects_cross_venue_contamination_and_retires_removed_markets(tmp_path):
    from watch_money.collector import Collector
    from watch_money.storage import Storage
    from watch_money.models import Settings, now_ms
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["alpha"]).model_dump())
    store.save_instruments([instrument("retired", "alpha"), instrument("b", "beta")])
    store.save_snapshots([snapshot("b", "beta", now_ms(), last=50)], archive=False)
    class Adapter:
        async def discover(self):
            return [instrument("a", "alpha")]
        async def fetch(self, _):
            return [snapshot("b", "alpha", now_ms(), last=999)]
    adapter = Adapter()
    collector = Collector(store, {"alpha": adapter})
    async def pause(_):
        collector.stopping.set()
    collector.pause = pause
    await collector.run("alpha", adapter)
    assert collector.statuses["alpha"].state == "error"
    assert collector.statuses["alpha"].parse_errors == 1
    assert store.latest()["b"].last == 50
    assert store.instruments()["retired"].active is False


@pytest.mark.asyncio
async def test_monitor_refreshes_execution_inputs_and_reports_invalidation(tmp_path):
    from watch_money.collector import Collector
    from watch_money.monitors import Monitor, MonitorService
    from watch_money.notifications import NotificationRule, NotificationService
    from watch_money.storage import Storage
    from watch_money.models import Settings, now_ms
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["alpha", "beta"]).model_dump())
    store.save_instruments([instrument("a", "alpha"), instrument("b", "beta")])
    at = now_ms()
    store.save_snapshots([snapshot("a", "alpha", at), snapshot("b", "beta", at)], archive=False)
    class Adapter:
        fail = False
        calls = 0
        async def orderbook(self, inst):
            self.calls += 1
            if self.fail:
                raise RuntimeError("fixture outage")
            return book(inst.id, now_ms(), bids=[(120, 2)], asks=[(121, 2)]) if inst.id == "b" else book(inst.id, now_ms())
    alpha, beta = Adapter(), Adapter()
    collector = Collector(store, {"alpha": alpha, "beta": beta})
    notifications = NotificationService(tmp_path)
    notifications.save_rule(NotificationRule(id="threshold", name="Threshold", min_net_profit=1,
                                             event_types=["opportunity"]))
    notifications.save_rule(NotificationRule(id="invalidated", name="Lifecycle",
                                             event_types=["opportunity_invalidated"]))
    service = MonitorService(store, collector, notifications)
    service.save(Monitor(id="watch", name="Independent monitor", calculation=calculation_request()))
    await service.tick()
    first = service.list()["results"][0]
    assert first["opportunities"][0]["net_profit"] == pytest.approx(19.78)
    assert alpha.calls == beta.calls == 1
    assert len(notifications.history()["notifications"]) == 1
    alpha.fail = True
    service.next_run.clear()
    await service.tick()
    second = service.list()["results"][0]
    assert second["opportunities"][0]["net_profit"] is None
    assert second["errors"] == ["a: RuntimeError"]
    assert {x["event"]["type"] for x in notifications.history()["notifications"]} == {"opportunity", "opportunity_invalidated"}
    restored = MonitorService(Storage(tmp_path), collector, notifications)
    assert restored.list()["monitors"][0]["id"] == "watch"
    assert restored.list()["results"][0]["opportunities"][0]["net_profit"] is None
    await notifications.stop()


def test_funding_default_uses_mark_not_entry_and_break_even_nets_same_settlement():
    from watch_money.strategies import calculate, opportunity_event
    at = 1_700_000_000_000
    req = calculation_request(strategy="funding_spread", hold_hours=8, assume_exit_at_entry=True,
        instruments=[instrument("a", "alpha", kind="perpetual"), instrument("b", "beta", kind="perpetual")],
        orderbooks=[book("a", at), book("b", at, bids=[(100, 10)], asks=[(101, 10)])],
        snapshots=[snapshot("a", "alpha", at, funding_rate=.001, funding_kind="current", mark=200,
                            funding_interval_hours=1, next_funding_at=at + 3_600_000),
                   snapshot("b", "beta", at, funding_rate=.004, funding_kind="current", mark=100,
                            funding_interval_hours=1, next_funding_at=at + 3_600_000)])
    result = calculate(req)["opportunities"][0]
    assert result["funding_profit"] == pytest.approx(1.6)
    assert result["known_costs"] == pytest.approx(.4)
    assert result["net_profit"] == pytest.approx(1.2)
    assert result["breakeven_hours"] == 2
    assert {e["notional"] for e in result["funding_events"] if e["position"] == "long"} == {200}
    event = opportunity_event(result, req)
    assert event["funding_spread"] == pytest.approx(.024)
    assert event["funding_spread_unit"] == "fraction_per_8h"
    assert event["asset"] == "BTC"
    req.snapshots[0].mark = None
    assert calculate(req)["opportunities"][0]["net_profit"] is None
    req.funding_notional_basis = "entry_vwap"
    assert calculate(req)["opportunities"][0]["funding_profit"] == pytest.approx(2.4)


@pytest.mark.parametrize("known_source_time", [True, False])
def test_stream_last_update_cannot_refresh_retained_stale_displayed_mid(tmp_path, monkeypatch, known_source_time):
    from watch_money.collector import Collector, enrich
    from watch_money.storage import Storage
    from watch_money import collector as module
    at = 1_700_000_000_000
    monkeypatch.setattr(module, "now_ms", lambda: at)
    store = Storage(tmp_path)
    old_source = at - 120_000 if known_source_time else None
    old = snapshot("a", "alpha", at - 120_000, source_at=old_source, bid=99, ask=101,
                   field_timestamps={"bid": old_source, "ask": old_source})
    store.save_snapshots([old], archive=False)
    collector = Collector(store, {})
    patch = snapshot("a", "alpha", at, last=105, field_timestamps={"last": at})
    merged = collector.merge_patch(patch)
    result = enrich(merged, 60)
    assert result["mid"] == 100
    assert result["stale"] is True, "A fresh last trade must not freshen the retained displayed bid/ask midpoint"
    assert result["age_seconds"] >= 120
    assert result["field_timestamps"]["bid"] == old_source
    store.save_snapshots([merged], archive=False)
    restored = enrich(Storage(tmp_path).latest()["a"], 60)
    assert restored["stale"] is True
    assert restored["age_seconds"] >= 120


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_field_times", [True, False])
async def test_slower_rest_response_does_not_replace_newer_stream_prices(tmp_path, explicit_field_times):
    from watch_money.collector import Collector
    from watch_money.storage import Storage
    from watch_money.models import Settings, now_ms
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["alpha"], history_enabled=False).model_dump())
    at = now_ms()
    current = snapshot("a", "alpha", at, bid=109, ask=111,
                       field_timestamps={"bid": at, "ask": at} if explicit_field_times else {},
                       field_sources={"bid": "fixture:ws", "ask": "fixture:ws"})
    store.save_snapshots([current], archive=False)
    class Adapter:
        async def discover(self):
            return [instrument("a", "alpha")]
        async def fetch(self, _):
            return [snapshot("a", "alpha", at + 1, source_at=at - 10_000, bid=99, ask=101,
                             funding_rate=.001, funding_kind="current", funding_interval_hours=8,
                             field_timestamps={"bid": at - 10_000, "ask": at - 10_000,
                                               "funding_rate": at},
                             field_sources={"bid": "fixture:rest", "ask": "fixture:rest",
                                            "funding_rate": "fixture:rest-funding"})]
    adapter = Adapter()
    collector = Collector(store, {"alpha": adapter})
    async def pause(_):
        collector.stopping.set()
    collector.pause = pause
    await collector.run("alpha", adapter)
    merged = collector.snapshots["a"]
    assert merged.bid == 109 and merged.ask == 111
    assert merged.field_sources["bid"] == "fixture:ws"
    assert merged.funding_rate == .001
    assert merged.field_timestamps["funding_rate"] == at
    restored = Storage(tmp_path).latest()["a"]
    assert restored.bid == 109 and restored.ask == 111
    assert restored.funding_rate == .001


@pytest.mark.asyncio
async def test_source_stream_reconnects_resubscribes_and_cancels_pending_receive(tmp_path):
    import asyncio
    from watch_money.collector import Collector
    from watch_money.storage import Storage
    from watch_money.models import Settings, now_ms
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["alpha"], watch_assets=["BTC"],
                                   prefer_websocket=True, history_enabled=False).model_dump())
    store.save_instruments([instrument("a", "alpha"),
                            instrument("b", "alpha", base="ETH", native_symbol="ETH/USD")])
    class Adapter:
        _markets = {"a": {}, "b": {}}
        closed = False
        async def close(self):
            self.closed = True
    class Stream:
        calls = []
        cancelled = []
        closed = 0
        first_pending = asyncio.Event()
        second_pending = asyncio.Event()
        async def stream(self, selected):
            ids = tuple(i.id for i in selected)
            self.calls.append(ids)
            if len(self.calls) <= 2:
                raise ConnectionError("independent connection outage")
            try:
                yield snapshot(ids[0], "alpha", now_ms(), last=100)
                (self.first_pending if ids == ("a",) else self.second_pending).set()
                await asyncio.Event().wait()
            finally:
                self.cancelled.append(ids)
        async def close(self):
            self.closed += 1
    adapter, stream = Adapter(), Stream()
    collector = Collector(store, {"alpha": adapter})
    delays = []
    async def pause(seconds):
        delays.append(seconds)
        await asyncio.sleep(0)
    collector.pause = pause
    task = asyncio.create_task(collector.run_stream("alpha", stream))
    collector.tasks = [task]
    try:
        await asyncio.wait_for(stream.first_pending.wait(), timeout=3)
        assert delays[:2] == [5, 10]
        assert collector.statuses["alpha"].stream_reconnects == 2
        settings = store.settings()
        settings.watch_assets = ["ETH"]
        store.set("settings", settings.model_dump())
        await asyncio.wait_for(stream.second_pending.wait(), timeout=3)
        assert stream.calls == [("a",), ("a",), ("a",), ("b",)]
        assert ("a",) in stream.cancelled
        assert collector.snapshots["b"].last == 100
    finally:
        await asyncio.wait_for(collector.stop(), timeout=2)
    assert task.done()
    assert ("b",) in stream.cancelled
    assert adapter.closed is True
    assert stream.closed >= 4


def test_fresh_stream_trade_does_not_revalidate_retained_future_quotes(tmp_path, monkeypatch):
    from watch_money.collector import Collector, quality
    from watch_money.storage import Storage
    from watch_money import collector as module
    at = 1_700_000_000_000
    monkeypatch.setattr(module, "now_ms", lambda: at)
    store = Storage(tmp_path)
    future = quality(snapshot("a", "alpha", at, source_at=at + 120_000, bid=99, ask=101,
                              field_timestamps={"bid": at + 120_000, "ask": at + 120_000}))
    assert future.valid is False
    store.save_snapshots([future], archive=False)
    collector = Collector(store, {})
    merged = collector.merge_patch(snapshot("a", "alpha", at + 1, source_at=at, last=100,
                                            field_timestamps={"last": at}))
    assert merged.valid is False, "Unrelated fresh trade cannot rehabilitate retained future-dated quotes"
    collector.snapshots["a"] = merged
    corrected = collector.merge_patch(snapshot("a", "alpha", at + 2, source_at=at, bid=98, ask=102,
                                               field_timestamps={"bid": at, "ask": at}))
    assert corrected.valid is True, "A valid current quote must recover from an invalid future timestamp"
    assert corrected.bid == 98 and corrected.ask == 102
    assert corrected.field_timestamps["bid"] == at
