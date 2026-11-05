"""Matrix scope filtering and complete cell evidence; all rates are synthetic."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from watch_money.funding_view import build_funding_view, router
from watch_money.models import Instrument, MarketSnapshot, Settings

NOW = 1_800_000_000_000


def instrument(base, venue, *, verified=True):
    return Instrument(id=f"{base}:{venue}", venue=venue, native_symbol=f"{base}/USDT:USDT",
                      asset_id=base if verified else f"{venue}:{base}", base=base, quote="USDT", kind="perpetual",
                      mapping_verified=verified)


def snapshot(item, rate=.001, **updates):
    return MarketSnapshot(instrument_id=item.id, venue=item.venue, funding_rate=rate, funding_kind="current",
                          funding_interval_hours=8, next_funding_at=NOW + 3600_000, source="synthetic",
                          source_at=NOW, received_at=NOW, **updates)


def test_matrix_includes_isolated_and_unverified_groups_without_unsafe_spread():
    items = [instrument("SMALL", "alpha"), instrument("SAME", "alpha", verified=False),
             instrument("SAME", "beta", verified=False)]
    result = build_funding_view(items, [snapshot(item) for item in items], NOW, 300)
    assert not result["groups"]
    assert len(result["matrix_groups"]) == 3
    assert all(group["spread_8h_rate"] is None for group in result["matrix_groups"])
    assert len({group["id"] for group in result["matrix_groups"]}) == 3


def test_matrix_cells_keep_full_evidence_when_ranking_rows_are_truncated():
    items = [instrument("SMALL", venue) for venue in ("alpha", "beta")]
    result = build_funding_view(items, [snapshot(item) for item in items], NOW, 300, limit=1)
    assert len(result["rows"]) == 1
    cells = result["matrix_groups"][0]["cells"]
    assert len(cells) == 2
    assert all(cell["field_validity"]["funding_rate"]["ttl_seconds"] == 300 for cell in cells)
    assert all(cell["source"] == "synthetic" and cell["observed"]["funding_rate"] == .001 for cell in cells)


def test_stale_cells_retain_last_observed_rate_without_normalizing_it():
    item = instrument("SMALL", "alpha")
    observed = snapshot(item, .004, field_timestamps={"funding_rate": NOW - 301_000})
    row = build_funding_view([item], [observed], NOW, 300)["matrix_groups"][0]["cells"][0]
    assert row["raw_rate"] is None and row["normalized_8h_rate"] is None
    assert row["observed"]["funding_rate"] == .004
    assert row["field_validity"]["funding_rate"]["source_at"] == NOW - 301_000


def test_platform_and_search_filters_precede_group_sort_and_budget(monkeypatch):
    from watch_money.query_work import QueryWork
    monkeypatch.setattr("watch_money.funding_view.now_ms", lambda: NOW)
    items = [instrument(base, venue) for base, venue in (("X", "alpha"), ("X", "beta"), ("X", "gamma"),
                                                       ("Y", "alpha"), ("Y", "beta"))]
    values = [snapshot(item, rate) for item, rate in zip(items, [.01, .010001, .9, -.02, 0])]
    app = FastAPI()
    app.state.query_work = QueryWork()
    app.router.add_event_handler('shutdown', app.state.query_work.close)
    app.include_router(router)
    app.state.collector = SimpleNamespace(instruments={item.id: item for item in items}, snapshots={row.instrument_id: row for row in values})
    app.state.store = SimpleNamespace(settings=lambda: Settings(enabled_venues=["alpha", "beta", "gamma"], stale_seconds=300))
    with TestClient(app) as client:
        global_result = client.get("/api/funding?sort=spread&limit=1").json()
        assert global_result["matrix_groups"][0]["symbols"] == ["X"]
        selected = client.get("/api/funding?sort=spread&limit=1&platforms=alpha,beta").json()
        assert selected["matrix_groups"][0]["symbols"] == ["Y"]
        assert selected["venues"] == ["alpha", "beta", "gamma"]
        assert all(cell["venue"] != "gamma" for group in selected["matrix_groups"] for cell in group["cells"])
        search = client.get("/api/funding?sort=spread&limit=1&search=Y").json()
        assert search["matrix_groups"][0]["symbols"] == ["Y"]
        single = client.get("/api/funding?venue=beta&limit=1").json()
        assert single["stats"]["rows_total"] == 2 and all(row["venue"] == "beta" for row in single["rows"])
        assert single["venues"] == ["alpha", "beta", "gamma"]
        assert client.get("/api/funding?platforms=").json()["matrix_groups"] == []
