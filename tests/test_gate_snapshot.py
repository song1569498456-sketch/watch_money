"""Official-schema simulated depth snapshots; no real network acceptance."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from watch_money.adapters.gate_snapshot import GateSnapshotService, SnapshotBusy, parse_snapshot
from watch_money.app import create_app
from watch_money.collector import Collector, enrich
from watch_money.models import Instrument, now_ms
from watch_money.storage import Storage


def item():
    return Instrument(
        id="gate:small",
        venue="gate",
        native_symbol="SMALL/USDT",
        asset_id="SMALL",
        base="SMALL",
        quote="USDT",
        kind="spot",
    )


def raw(at):
    return {"current": at, "update": at - 3600_000, "id": 12, "bids": [["100", "2"]], "asks": [["101", "3"]]}


def test_native_snapshot_generation_is_distinct_from_last_change():
    at = now_ms()
    book, patch, obs = parse_snapshot(item(), raw(at), at + 1)
    assert book.source_at == at == patch.source_at
    assert obs["book_last_changed_at"] == at - 3600_000
    assert obs["source_time_kind"] == "exchange_snapshot_generation"
    assert "exchange_snapshot_generation" in patch.field_sources["bid"]
    assert patch.field_received_at["bid"] == at + 1


@pytest.mark.parametrize("key,value", [("current", None), ("current", True), ("current", "bad")])
def test_generation_unavailable_uses_only_old_native_change_time(key, value):
    at = now_ms()
    body = raw(at)
    body[key] = value
    book, patch, obs = parse_snapshot(item(), body, at)
    assert book.source_at == at - 3600_000
    assert not enrich(patch, 60)["price_fresh"]
    assert obs["source_time_kind"] == "book_last_change"


def test_no_native_clocks_never_substitutes_receipt():
    body = raw(now_ms())
    body.pop("current")
    body.pop("update")
    book, patch, obs = parse_snapshot(item(), body, now_ms())
    assert book.source_at is None and patch.source_at is None
    assert not obs["timestamp_available"] and not enrich(patch, 60)["price_fresh"]


@pytest.mark.parametrize(
    "change",
    [
        lambda r, t: r.update(current=t + 1),
        lambda r, t: r.update(update=t + 1),
        lambda r, t: r.pop("asks"),
        lambda r, t: r.update(bids=[["nan", "1"]]),
        lambda r, t: r.update(bids=[["102", "1"]]),
    ],
)
def test_future_inconsistent_missing_malformed_crossed_rejected(change):
    at = now_ms()
    body = raw(at)
    change(body, at)
    with pytest.raises(ValueError):
        parse_snapshot(item(), body, at)


def test_empty_snapshot_sides_explicitly_clear():
    at = now_ms()
    body = raw(at)
    body.update(bids=[], asks=[])
    _, patch, _ = parse_snapshot(item(), body, at)
    assert set(patch.cleared_fields) == {"bid", "ask"}
    assert patch.field_received_at == {"bid": at, "ask": at}


class Adapter:
    def __init__(self, response, callback=None):
        self.response, self.callback, self.calls = response, callback, []

    def _resolve(self, instrument):
        return "gate", {"id": "SMALL_USDT"}

    def _client(self, name):
        assert name == "gate"
        return self

    async def publicSpotGetOrderBook(self, params):
        self.calls.append(params)
        if self.callback:
            await self.callback()
        return self.response

    async def close(self):
        pass


def setup(tmp_path, response, callback=None):
    store = Storage(tmp_path)
    store.save_instruments([item()])
    adapter = Adapter(response, callback)
    collector = Collector(store, {"gate": adapter})
    return collector, adapter, collector.instruments[item().id]


async def test_request_scope_merges_and_budget_does_not_repeat(tmp_path):
    collector, adapter, inst = setup(tmp_path, raw(now_ms()))
    service = GateSnapshotService(collector)
    result = await service.fetch(inst)
    assert result["observation"]["merged"]
    assert collector.snapshots[inst.id].bid == 100
    assert adapter.calls == [{"currency_pair": "SMALL_USDT", "limit": 20, "with_id": True}]
    with pytest.raises(SnapshotBusy):
        await service.fetch(inst)
    assert len(adapter.calls) == 1


async def test_unknown_time_returns_depth_without_overwriting_quotes(tmp_path):
    response = raw(now_ms())
    response.pop("current")
    response.pop("update")
    collector, _, inst = setup(tmp_path, response)
    result = await GateSnapshotService(collector).fetch(inst)
    assert not result["observation"]["merged"] and not collector.snapshots


async def test_catalog_change_during_request_does_not_merge(tmp_path):
    async def change():
        collector.instruments[inst.id] = inst.model_copy(update={"asset_id": "changed"})

    collector, _, inst = setup(tmp_path, raw(now_ms()), change)
    result = await GateSnapshotService(collector).fetch(inst)
    assert not result["observation"]["merged"] and not collector.snapshots


async def test_cancel_releases_request_capacity_but_preserves_cooldown(tmp_path):
    entered, blocker = asyncio.Event(), asyncio.Event()

    async def block():
        entered.set()
        await blocker.wait()

    collector, _, inst = setup(tmp_path, raw(now_ms()), block)
    service = GateSnapshotService(collector)
    pending = asyncio.create_task(service.fetch(inst))
    await entered.wait()
    assert service.inflight == 1
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert service.inflight == 0 and not collector.snapshots
    with pytest.raises(SnapshotBusy):
        await service.fetch(inst)


def test_existing_depth_route_exposes_native_semantics_and_rate_budget(tmp_path):
    adapter = Adapter(raw(now_ms()))
    app = create_app(tmp_path, start_collector=False, adapters={"gate": adapter})
    app.state.collector.instruments = {item().id: item()}
    with TestClient(app) as client:
        result = client.get("/api/orderbook/gate:small")
        assert result.status_code == 200 and result.json()["observation"]["merged"]
        assert client.get("/api/orderbook/gate:small").status_code == 429
        diagnostics = client.get("/api/query-work").json()
        assert diagnostics["gate_on_demand_snapshots"]["requests"] == 1


async def test_rest_ticker_does_not_renew_snapshot_and_older_ws_cannot_replace_it(tmp_path):
    from test_gate_quote_integration import rest, book

    at = now_ms() - 1000
    collector, _, inst = setup(tmp_path, raw(at))
    await GateSnapshotService(collector).fetch(inst)
    late = rest(at + 500).model_copy(update={"instrument_id": inst.id})
    collector.snapshots[inst.id] = collector.merge_patch(late)
    assert collector.snapshots[inst.id].bid == 100
    assert collector.snapshots[inst.id].field_timestamps["bid"] == at
    older = book(at - 100).model_copy(update={"instrument_id": inst.id, "bid": 50})
    merged = collector.merge_patch(older)
    assert merged.bid == 100 and merged.field_timestamps["bid"] == at


def test_bbo_coverage_separates_unobserved_unknown_stale_and_future(tmp_path, monkeypatch):
    from watch_money.models import MarketSnapshot

    at = now_ms()
    inst = item()

    def counts(snapshot):
        return Collector._coverage_counts(inst, snapshot, at, 60)

    assert counts(None)["light_book_missing_markets"] == 1
    base = MarketSnapshot(
        instrument_id=inst.id,
        venue="gate",
        source="fixture",
        bid=100,
        ask=101,
        source_at=None,
        received_at=at,
    )
    assert counts(base)["light_source_missing_markets"] == 1
    assert counts(base)["light_fresh_markets"] == 0
    stale = base.model_copy(update={"source_at": at - 61_000})
    assert counts(stale)["light_source_stale_markets"] == 1
    future = base.model_copy(update={"source_at": at + 1})
    assert counts(future)["light_source_future_markets"] == 1
    fresh = base.model_copy(update={"source_at": at})
    assert counts(fresh)["light_fresh_markets"] == 1
    collector, _, inst = setup(tmp_path, raw(at))
    collector.snapshots[inst.id] = fresh
    collector.refresh_status_coverage()
    assert collector.statuses["gate"].light_fresh_markets == 1
    monkeypatch.setattr("watch_money.collector.now_ms", lambda: at + 61_000)
    collector.refresh_status_coverage()
    assert collector.statuses["gate"].light_fresh_markets == 0
    assert collector.statuses["gate"].light_source_stale_markets == 1
    assert collector.statuses["gate"].light_observation_checked_at == at + 61_000


async def test_global_spacing_and_two_request_limit(tmp_path):
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def block():
        await entered.put(True)
        await release.wait()

    collector, adapter, original = setup(tmp_path, raw(now_ms()), block)
    items = [original.model_copy(update={"id": f"gate:{i}"}) for i in range(3)]
    collector.instruments = {value.id: value for value in items}
    clock = [10.0]
    service = GateSnapshotService(collector, clock=lambda: clock[0])
    first = asyncio.create_task(service.fetch(items[0]))
    await entered.get()
    with pytest.raises(SnapshotBusy):
        await service.fetch(items[1])
    clock[0] += 0.25
    second = asyncio.create_task(service.fetch(items[1]))
    await entered.get()
    clock[0] += 0.25
    with pytest.raises(SnapshotBusy):
        await service.fetch(items[2])
    assert service.inflight == 2 and len(adapter.calls) == 2
    release.set()
    await asyncio.gather(first, second)
    assert service.inflight == 0


def test_invalid_structure_does_not_hide_future_source_diagnostic():
    from watch_money.models import MarketSnapshot

    at = now_ms()
    inst = item()
    value = MarketSnapshot(
        instrument_id=inst.id,
        venue="gate",
        source="fixture",
        bid=101,
        ask=100,
        source_at=at + 60_000,
        received_at=at,
        valid=False,
    )
    count = Collector._coverage_counts(inst, value, at, 60)
    assert count["light_source_future_markets"] == 1 and count["light_fresh_markets"] == 0


async def test_older_snapshot_does_not_claim_it_replaced_newer_ws(tmp_path):
    from test_gate_quote_integration import book

    at = now_ms() - 1000
    collector, _, inst = setup(tmp_path, raw(at - 1000))
    collector.snapshots[inst.id] = book(at).model_copy(update={"instrument_id": inst.id})
    service = GateSnapshotService(collector)
    result = await service.fetch(inst)
    assert not result["observation"]["merged"] and result["observation"]["merged_fields"] == []
    assert collector.snapshots[inst.id].field_timestamps["bid"] == at
    assert service.applied == 0
