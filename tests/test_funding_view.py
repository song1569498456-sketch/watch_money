"""Funding tables retain all symbols while separating identity, time and rate type."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from watch_money import funding_view
from watch_money.funding_view import build_funding_view, router
from watch_money.models import Instrument, MarketSnapshot, Settings

NOW = 1800000000000


def market(identity, venue, base="BRETT", **values):
    return Instrument(id=identity, venue=venue, base=base, native_symbol=f"{base}/USDT:USDT",
                      quote=values.pop("quote", "USDT"), asset_id=values.pop("asset_id", base),
                      kind=values.pop("kind", "perpetual"),
                      mapping_verified=values.pop("mapping_verified", True), **values)


def snap(instrument, **values):
    return MarketSnapshot(instrument_id=instrument.id, venue=instrument.venue, source=f"fixture:{instrument.venue}",
                          source_at=values.pop("source_at", NOW), received_at=values.pop("received_at", NOW),
                          funding_rate=values.pop("funding_rate", .001),
                          funding_kind=values.pop("funding_kind", "current"),
                          funding_interval_hours=values.pop("funding_interval_hours", 8),
                          next_funding_at=values.pop("next_funding_at", NOW + 3600000), **values)


def run(markets, snapshots=None, **values):
    return build_funding_view(markets, snapshots if snapshots is not None else [snap(item) for item in markets],
                              NOW, 60, **values)


def test_all_perpetual_assets_are_ranked_without_watchlist_or_major_coin_filter():
    assets = ["BRETT", "POPCAT", "TURBO", "BONK", "MEW", "PYTH"]
    markets = [market(f"{base}:{venue}", venue, base) for base in assets for venue in ["alpha", "beta"]]
    result = run(markets)
    assert len(result["rows"]) == 12 and len(result["groups"]) == 6
    assert {row["base"] for row in result["rows"]} == set(assets)
    assert all(row["identity_status"] == "verified" for row in result["rows"])
    assert result["observation_type"] == "local_public_snapshots" and not result["is_settled_history"]


def test_unverified_same_named_tokens_are_listed_but_never_merged_into_matrix():
    markets = [market("a", "alpha", asset_id="alpha:BRETT", mapping_verified=False),
               market("b", "beta", asset_id="beta:BRETT", mapping_verified=False)]
    result = run(markets)
    assert len(result["rows"]) == 2 and result["groups"] == []
    assert len({row["identity_group_id"] for row in result["rows"]}) == 2
    assert all(row["identity_status"] == "unverified" for row in result["rows"])


def test_conflicting_native_addresses_remain_rows_without_matrix():
    markets = [market("a", "alpha", chain="base", native_asset_id="base:0x" + "11" * 20),
               market("b", "beta", chain="base", native_asset_id="base:0x" + "22" * 20)]
    result = run(markets)
    assert len(result["rows"]) == 2 and not result["groups"]
    assert all(row["identity_status"] == "conflict" for row in result["rows"])


def test_unknown_interval_keeps_raw_rate_without_inventing_normalization():
    item = market("a", "alpha")
    row = run([item], [snap(item, funding_interval_hours=None)])["rows"][0]
    assert row["raw_rate"] == .001 and row["normalized_8h_rate"] is None
    assert "funding_interval_hours_missing" in row["missing"]
    assert not row["field_validity"]["funding_interval_hours"]["valid"]


def test_market_without_snapshot_is_still_listed_with_explicit_missing_reasons():
    item = market("a", "alpha")
    result = run([item], [])
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["raw_rate"] is row["interval_hours"] is row["normalized_8h_rate"] is None
    assert row["source_at"] is row["received_at"] is None
    assert "snapshot_missing" in row["missing"]
    assert all(not validity["valid"] for validity in row["field_validity"].values())


def test_stale_dynamic_rate_is_not_refreshed_by_fresh_interval_or_envelope():
    item = market("a", "alpha")
    row = run([item], [snap(item, field_timestamps={"funding_rate": NOW - 61000})])["rows"][0]
    assert row["raw_rate"] is row["normalized_8h_rate"] is None
    assert row["interval_hours"] == 8
    assert row["field_validity"]["funding_rate"]["reason"] == "funding_rate_stale"
    assert row["field_validity"]["funding_rate"]["source_at"] == NOW - 61000
    assert row["field_validity"]["funding_rate"]["received_at"] == NOW


@pytest.mark.parametrize("suffix", ["catalog_metadata_observed_at", "public_metadata_observed_at"])
def test_period_metadata_uses_six_hour_observation_ttl_without_fake_source_time(suffix):
    item = market("a", "alpha")
    observation = snap(item, field_timestamps={"funding_interval_hours": None},
                       field_received_at={"funding_interval_hours": NOW - 3600000},
                       field_sources={"funding_interval_hours": f"fixture:{suffix}"})
    row = run([item], [observation])["rows"][0]
    assert row["normalized_8h_rate"] == .001
    validity = row["field_validity"]["funding_interval_hours"]
    assert validity["source_at"] is None and validity["received_at"] == NOW - 3600000
    assert validity["metadata_observation"] and validity["ttl_seconds"] == 21600
    assert "source_timestamp_missing" in row["warnings"]
    observation.field_received_at["funding_interval_hours"] = NOW - 6 * 3600000 - 1
    assert run([item], [observation])["rows"][0]["normalized_8h_rate"] is None


def test_matrix_preserves_raw_intervals_and_computes_crossvenue_normalized_difference():
    a, b = market("a", "alpha"), market("b", "beta")
    result = run([a, b], [snap(a, funding_rate=-.0001, funding_interval_hours=1),
                          snap(b, funding_rate=.0004, funding_interval_hours=8)])
    group = result["groups"][0]
    assert group["spread_8h_rate"] == pytest.approx(.0012)
    assert group["long_instrument_id"] == "a" and group["short_instrument_id"] == "b"
    assert {cell["interval_hours"] for cell in group["cells"]} == {1, 8}
    assert group["net_profit"] is None
    assert group["comparison_type"] == "normalized_rate_difference_not_cashflow"


def test_same_venue_extremes_do_not_masquerade_as_best_crossvenue_pair():
    a, b, c = market("a", "alpha"), market("b", "alpha"), market("c", "beta")
    result = run([a, b, c], [snap(a, funding_rate=-.01), snap(b, funding_rate=.01), snap(c, funding_rate=0)])
    group = result["groups"][0]
    assert group["spread_8h_rate"] == .01
    rows = {row["instrument_id"]: row for row in result["rows"]}
    assert rows[group["long_instrument_id"]]["venue"] != rows[group["short_instrument_id"]]["venue"]


def test_different_funding_types_do_not_silently_produce_comparable_spread():
    a, b = market("a", "alpha"), market("b", "beta")
    group = run([a, b], [snap(a), snap(b, funding_kind="predicted")])["groups"][0]
    assert group["spread_8h_rate"] is None and not group["comparison_ready"]
    assert "funding_kinds_differ" in group["missing"]


def test_different_quotes_and_settlement_times_warn_instead_of_claiming_cash_profit():
    a, b = market("a", "alpha"), market("b", "beta", quote="USDC")
    group = run([a, b], [snap(a), snap(b, next_funding_at=NOW + 7200000)])["groups"][0]
    assert "settlement_times_differ" in group["warnings"]
    assert "quotes_differ_equal_valuation_notionals_required" in group["warnings"]
    assert group["net_profit"] is None


def test_current_settled_snapshot_is_never_advertised_as_historical_series():
    item = market("a", "alpha")
    row = run([item], [snap(item, funding_kind="settled")])["rows"][0]
    assert row["kind"] == "settled" and row["is_settled_history"] is False
    assert "observed_settled_rate_is_not_a_settlement_history_series" in row["warnings"]


def test_rank_basis_and_limit_are_explicit_and_zero_rates_are_not_missing():
    a, b, c = market("a", "alpha"), market("b", "beta"), market("c", "gamma")
    observations = [snap(a, funding_rate=.001, funding_interval_hours=1),
                    snap(b, funding_rate=.002, funding_interval_hours=8), snap(c, funding_rate=0)]
    result = run([a, b, c], observations, limit=1)
    assert result["rows"][0]["instrument_id"] == "a" and result["stats"]["rows_truncated"]
    raw = run([a, b, c], observations, rate_basis="raw")
    assert raw["rows"][0]["instrument_id"] == "b"
    assert raw["stats"]["fresh_rates"] == raw["stats"]["normalized_rates"] == 3
    assert next(row for row in raw["rows"] if row["instrument_id"] == "c")["normalized_8h_rate"] == 0


def test_countdown_and_missing_next_time_are_explicit():
    a, b = market("a", "alpha"), market("b", "beta")
    result = run([a, b], [snap(a, next_funding_at=NOW + 90000), snap(b, next_funding_at=None)])
    rows = {row["instrument_id"]: row for row in result["rows"]}
    assert rows["a"]["countdown_seconds"] == 90
    assert rows["b"]["next_funding_at"] is rows["b"]["countdown_seconds"] is None


def test_snapshot_selection_is_asof_and_function_is_pure():
    item = market("a", "alpha")
    past = snap(item, funding_rate=.001)
    future = snap(item, funding_rate=.9, received_at=NOW + 1000, source_at=NOW + 1000)
    inputs = [item], [future, past]
    before = deepcopy(inputs)
    assert run(*inputs)["rows"][0]["raw_rate"] == .001
    assert inputs == before


def test_api_respects_platform_and_active_filters_and_ignores_watchlist(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    markets = [market("a", "alpha"), market("b", "beta"), market("c", "gamma"),
               market("d", "alpha", active=False), market("e", "alpha", kind="spot")]
    settings = Settings(enabled_venues=["alpha", "beta"], watch_assets=["BTC"], computation_enabled=False)
    app.state.store = SimpleNamespace(settings=lambda: settings)
    app.state.collector = SimpleNamespace(instruments={item.id: item for item in markets},
                                         snapshots={item.id: snap(item) for item in markets})
    monkeypatch.setattr(funding_view, "now_ms", lambda: NOW)
    with TestClient(app) as client:
        result = client.get("/api/funding?sort=spread").json()
        assert result["enabled"] and len(result["rows"]) == 2
        assert result["stats"]["excluded_by_settings"] == 1
        assert result["rows"][0]["base"] == "BRETT"
        assert client.get("/api/funding?sort=unknown").status_code == 422
        settings.demo_mode = True
        assert client.get("/api/funding").json()["rows"] == []


@pytest.mark.parametrize("values", [{"limit": 0}, {"limit": 5001}, {"sort": "bad"}, {"rate_basis": "bad"}])
def test_invalid_output_options_are_rejected(values):
    with pytest.raises(ValueError):
        run([], **values)
