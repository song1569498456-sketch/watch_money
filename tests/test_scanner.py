"""Whole-catalog scanning is indicative, bounded and identity/time aware."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from watch_money import scanner
from watch_money.models import Instrument, MarketSnapshot, Settings
from watch_money.scanner import router, scan_markets

NOW = 1800000000000


def market(identity, venue, base="BRETT", **values):
    return Instrument(id=identity, venue=venue, native_symbol=f"{base}/USDT", base=base,
                      asset_id=values.pop("asset_id", f"confirmed:{base}"), quote=values.pop("quote", "USDT"),
                      kind=values.pop("kind", "spot"), mapping_verified=values.pop("mapping_verified", True), **values)


def snap(instrument, **values):
    return MarketSnapshot(instrument_id=instrument.id, venue=instrument.venue,
                          bid=values.pop("bid", 99), ask=values.pop("ask", 101),
                          received_at=values.pop("received_at", NOW), source_at=values.pop("source_at", NOW),
                          source=f"fixture:{instrument.venue}", **values)


def run(markets, snapshots=None, **values):
    return scan_markets(markets, snapshots if snapshots is not None else [snap(item) for item in markets],
                        NOW, 60, **values)


def direction(result, buy, sell):
    return next(row for row in result["candidates"]
                if row["buy_instrument_id"] == buy and row["sell_instrument_id"] == sell)


def test_full_catalog_scans_arbitrary_assets_without_major_coin_or_watchlist_filter():
    assets = ["BRETT", "TURBO", "BONK", "POPCAT", "WIF", "MEW", "JUP", "AERO", "ENA", "PYTH", "PENDLE"]
    markets = [market(f"{asset}:{venue}", venue, asset) for asset in assets for venue in ["alpha", "beta"]]
    result = run(markets)
    assert result["stats"]["scanned"] == len(assets) * 2
    assert {row["asset_id"] for row in result["candidates"]} == {f"confirmed:{asset}" for asset in assets}
    assert not result["stats"]["truncated"]


def test_all_crossvenue_spot_perpetual_combinations_include_both_directions():
    markets = [market(f"{venue}:{kind}", venue, kind=kind)
               for venue in ["alpha", "beta"] for kind in ["spot", "perpetual"]]
    result = run(markets)
    assert result["stats"]["possible_candidates"] == result["stats"]["scanned"] == 8
    assert {row["strategy"] for row in result["candidates"]} == {"spot_spread", "perp_spread", "spot_perp_basis"}
    assert all(row["buy_venue"] != row["sell_venue"] for row in result["candidates"])
    assert len({row["id"] for row in result["candidates"]}) == 8


@pytest.mark.parametrize("market_type,expected_count,expected_markets", [
    ("all", 8, 4), ("spot", 2, 2), ("perpetual", 2, 2), ("spot_perp", 4, 4),
])
def test_market_type_filters_before_enumeration_and_recomputes_exact_coverage(market_type, expected_count,
                                                                           expected_markets):
    markets = [market(f"{venue}:{kind}", venue, kind=kind)
               for venue in ["alpha", "beta"] for kind in ["spot", "perpetual"]]
    result = run(markets, market_type=market_type)
    assert result["filters"]["market_type"] == market_type
    assert result["stats"]["possible_candidates"] == result["stats"]["scanned"] == expected_count
    group = result["groups"][0]
    assert group["market_count"] == result["stats"]["active_markets"] == expected_markets
    assert group["possible_directions"] == group["scanned_directions"] == expected_count
    for row in result["candidates"]:
        if market_type == "spot":
            assert row["buy_kind"] == row["sell_kind"] == "spot"
        elif market_type == "perpetual":
            assert row["buy_kind"] == row["sell_kind"] == "perpetual"
        elif market_type == "spot_perp":
            assert {row["buy_kind"], row["sell_kind"]} == {"spot", "perpetual"}


def test_market_type_does_not_spend_scan_budget_on_filtered_directions_or_keep_old_best():
    a = market("a-spot", "alpha")
    b = market("b-spot", "beta")
    c = market("c-perp", "alpha", kind="perpetual")
    d = market("d-perp", "beta", kind="perpetual")
    snapshots = [snap(a, bid=99, ask=100), snap(b, bid=101, ask=102),
                 snap(c, bid=500, ask=501), snap(d, bid=600, ask=601)]
    spot = run([a, b, c, d], snapshots, market_type="spot")
    best = spot["groups"][0]["best_spread_candidate"]
    assert (best["buy_instrument_id"], best["sell_instrument_id"]) == ("a-spot", "b-spot")
    limited = run([a, b, c, d], snapshots, market_type="perpetual", max_candidates=1)
    assert limited["stats"]["scanned"] == 1 and limited["stats"]["possible_candidates"] == 2
    assert limited["candidates"][0]["buy_kind"] == limited["candidates"][0]["sell_kind"] == "perpetual"


def test_spot_filter_includes_pool_and_rfq_but_keeps_route_requirements():
    a = market("pool", "alpha", kind="pool", mechanism="amm")
    b = market("rfq", "beta", kind="rfq", mechanism="rfq")
    c = market("perp", "gamma", kind="perpetual")
    result = run([a, b, c], market_type="spot")
    assert result["stats"]["possible_candidates"] == 2 and result["groups"][0]["market_count"] == 2
    assert all("size_specific_route_quotes_not_provided" in row["requirements"] for row in result["candidates"])
    assert all(not row["executable"] and row["net_profit"] is None for row in result["candidates"])


def test_spot_perp_counter_excludes_same_venue_and_same_product_directions():
    markets = [market("a-spot", "alpha"), market("a-perp", "alpha", kind="perpetual"),
               market("b-spot", "beta"), market("b-perp", "beta", kind="perpetual"),
               market("c-perp", "gamma", kind="perpetual")]
    result = run(markets, market_type="spot_perp")
    assert result["stats"]["possible_candidates"] == result["stats"]["scanned"] == 8
    assert all(row["buy_venue"] != row["sell_venue"] and row["buy_kind"] != row["sell_kind"]
               for row in result["candidates"])


def test_same_ticker_unverified_assets_do_not_become_opportunities():
    markets = [market("a", "alpha", mapping_verified=False, asset_id="alpha:BRETT"),
               market("b", "beta", mapping_verified=False, asset_id="beta:BRETT")]
    result = run(markets)
    assert result["candidates"] == []
    assert result["stats"]["blocked_markets"] == 2
    assert result["stats"]["identity_blockers"]["unverified_asset_mapping"] == 2


def test_different_native_addresses_cannot_be_scanned_after_incorrect_manual_mapping():
    markets = [market("a", "alpha", chain="base", native_asset_id="base:0x" + "a1" * 20),
               market("b", "beta", chain="base", native_asset_id="base:0x" + "b2" * 20)]
    result = run(markets)
    assert result["candidates"] == []
    assert result["stats"]["identity_blockers"]["different_native_chain_addresses"] == 1


def test_stale_snapshots_produce_no_price_or_funding_numbers():
    markets = [market("a", "alpha", kind="perpetual"), market("b", "beta", kind="perpetual")]
    snapshots = [snap(item, source_at=NOW - 61000, funding_rate=.001,
                      funding_interval_hours=8, funding_kind="current") for item in markets]
    result = run(markets, snapshots)
    for row in result["candidates"]:
        assert row["buy_ask"] is row["sell_bid"] is row["spread_rate"] is row["funding_8h_rate"] is None
        assert row["funding"]["long"]["raw_rate"] is None
        assert row["status"] == "data_unavailable"
    assert result["stats"]["unavailable_candidates"] == 2


def test_field_timestamps_stop_fresh_price_patch_from_refreshing_old_funding():
    markets = [market("a", "alpha", kind="perpetual"), market("b", "beta", kind="perpetual")]
    snapshots = [snap(item, funding_rate=.001, funding_interval_hours=8, funding_kind="current",
                      field_timestamps={"funding_rate": NOW - 120000}) for item in markets]
    row = run(markets, snapshots)["candidates"][0]
    assert row["spread_rate"] is not None and row["funding_8h_rate"] is None
    assert "long:funding_rate_stale" in row["missing"]


def test_individual_bid_age_does_not_hide_fresh_ask_or_refresh_old_bid():
    a, b = market("a", "alpha"), market("b", "beta")
    result = run([a, b], [snap(a, field_received_at={"bid": NOW - 61000}), snap(b)])
    assert direction(result, "a", "b")["spread_rate"] is not None
    assert direction(result, "b", "a")["spread_rate"] is None
    assert "sell:bid_stale" in direction(result, "b", "a")["missing"]


def test_different_quotes_preserve_values_but_do_not_calculate_price_spread_without_fx():
    a, b = market("a", "alpha", quote="USDT"), market("b", "beta", quote="KRW")
    row = direction(run([a, b], [snap(a, ask=101), snap(b, bid=150000, ask=151000)]), "a", "b")
    assert row["buy_quote"] == "USDT" and row["sell_quote"] == "KRW"
    assert row["buy_ask"] == 101 and row["sell_bid"] == 150000
    assert row["spread_absolute"] is row["spread_rate"] is row["spread_quote"] is None
    assert "quote_fx_not_provided" in row["missing"]


def test_signed_funding_comparison_uses_raw_intervals_and_both_long_short_directions():
    a, b = market("a", "alpha", kind="perpetual"), market("b", "beta", kind="perpetual")
    result = run([a, b], [snap(a, funding_rate=-.0001, funding_interval_hours=1, funding_kind="current"),
                          snap(b, funding_rate=.0004, funding_interval_hours=8, funding_kind="predicted")],
                 sort="funding")
    positive, negative = direction(result, "a", "b"), direction(result, "b", "a")
    assert positive["funding"]["long"]["raw_rate"] == -.0001
    assert positive["funding"]["long"]["interval_hours"] == 1
    assert positive["funding"]["long"]["normalized_8h_rate"] == pytest.approx(-.0008)
    assert positive["funding_8h_rate"] == pytest.approx(.0012)
    assert negative["funding_8h_rate"] == pytest.approx(-.0012)
    assert result["candidates"][0]["id"] == positive["id"]
    assert "funding_kinds_differ" in positive["warnings"]


def test_spot_perp_funding_direction_and_true_zero_are_not_missing_values():
    a, b = market("a", "alpha"), market("b", "beta", kind="perpetual")
    snapshots = [snap(a), snap(b, funding_rate=.0002, funding_interval_hours=8, funding_kind="current")]
    result = run([a, b], snapshots)
    assert direction(result, "a", "b")["funding_8h_rate"] == .0002
    assert direction(result, "b", "a")["funding_8h_rate"] == -.0002
    assert direction(result, "a", "b")["funding"]["long"] is None
    snapshots[1].funding_rate = 0
    zero = direction(run([a, b], snapshots), "a", "b")
    assert zero["funding_8h_rate"] == zero["funding"]["short"]["raw_rate"] == 0


def test_missing_funding_period_is_not_assumed_to_be_eight_hours():
    a, b = market("a", "alpha"), market("b", "beta", kind="perpetual")
    row = direction(run([a, b], [snap(a), snap(b, funding_rate=.001, funding_kind="current")]), "a", "b")
    assert row["funding"]["short"]["raw_rate"] == .001
    assert row["funding"]["short"]["normalized_8h_rate"] is row["funding_8h_rate"] is None
    assert "short:funding_interval_hours_missing" in row["missing"]


@pytest.mark.parametrize("suffix", ["catalog_metadata_observed_at", "public_metadata_observed_at"])
def test_observed_interval_metadata_uses_six_hour_ttl_and_warns_no_exchange_timestamp(suffix):
    a, b = market("a", "alpha"), market("b", "beta", kind="perpetual")
    funding = snap(b, funding_rate=.001, funding_interval_hours=8, funding_kind="current",
                   field_timestamps={"funding_interval_hours": None},
                   field_received_at={"funding_interval_hours": NOW - 3600000},
                   field_sources={"funding_interval_hours": f"fixture:{suffix}"})
    row = direction(run([a, b], [snap(a), funding]), "a", "b")
    assert row["funding_8h_rate"] == .001
    assert row["funding_rankable"]
    assert "source_timestamp_missing" in row["warnings"]
    assert "short:source_timestamp_missing" in row["warnings"]
    funding.field_received_at["funding_interval_hours"] = NOW - 6 * 3600000
    assert direction(run([a, b], [snap(a), funding]), "a", "b")["funding_8h_rate"] == .001
    funding.field_received_at["funding_interval_hours"] -= 1
    expired = direction(run([a, b], [snap(a), funding]), "a", "b")
    assert expired["funding_8h_rate"] is None
    assert "short:funding_interval_hours_stale" in expired["missing"]


def test_metadata_interval_requires_explicit_observation_time_not_envelope_fallback():
    a, b = market("a", "alpha"), market("b", "beta", kind="perpetual")
    funding = snap(b, funding_rate=.001, funding_interval_hours=8, funding_kind="current",
                   field_timestamps={"funding_interval_hours": None},
                   field_sources={"funding_interval_hours": "fixture:catalog_metadata_observed_at"})
    row = direction(run([a, b], [snap(a), funding]), "a", "b")
    assert row["funding_8h_rate"] is None
    assert "short:funding_interval_observation_time_missing" in row["missing"]


def test_dynamic_interval_does_not_inherit_metadata_ttl():
    a, b = market("a", "alpha"), market("b", "beta", kind="perpetual")
    funding = snap(b, funding_rate=.001, funding_interval_hours=8, funding_kind="current",
                   field_timestamps={"funding_interval_hours": NOW - 61000},
                   field_received_at={"funding_interval_hours": NOW - 61000},
                   field_sources={"funding_interval_hours": "fixture:catalog_metadata_observed_at"})
    row = direction(run([a, b], [snap(a), funding]), "a", "b")
    assert row["funding_8h_rate"] is None
    assert "short:funding_interval_hours_stale" in row["missing"]


def test_missing_source_timestamp_can_be_indicative_but_is_explicitly_warned():
    a, b = market("a", "alpha"), market("b", "beta")
    row = direction(run([a, b], [snap(a, source_at=None), snap(b, source_at=None)]), "a", "b")
    assert row["spread_rate"] is not None
    assert "source_timestamp_missing" in row["warnings"]
    assert row["net_profit"] is None and row["executable"] is False


def test_positive_indication_never_claims_size_depth_net_profit_or_execution():
    a = market("a", "alpha")
    b = market("b", "beta", kind="pool", mechanism="amm", chain="base")
    row = direction(run([a, b], [snap(a, ask=100), snap(b, bid=110, ask=111)]), "a", "b")
    assert row["spread_absolute"] == 10 and row["spread_pct"] == pytest.approx(10)
    assert row["net_profit"] is None and row["executable"] is False
    assert {"trade_size_not_provided", "fees_not_configured", "executable_depth_not_checked",
            "size_specific_route_quotes_not_provided"} <= set(row["requirements"])


def test_scan_and_output_budgets_are_separate_and_truncation_is_explicit():
    markets = [market(str(number), f"venue{number}") for number in range(5)]
    result = run(markets, limit=3, max_candidates=5)
    stats = result["stats"]
    assert (stats["possible_candidates"], stats["scanned"], stats["returned"]) == (20, 5, 3)
    assert stats["unscanned"] == 15 and stats["omitted_by_limit"] == 2
    assert stats["enumeration_truncated"] and stats["result_truncated"] and stats["truncated"]
    assert stats["selection"] == "bounded_prefix"


def test_global_sort_ranks_both_directions_without_ignoring_negative_spreads():
    a, b = market("a", "alpha"), market("b", "beta")
    result = run([a, b], [snap(a, bid=99, ask=100), snap(b, bid=105, ask=106)])
    assert result["candidates"][0]["spread_rate"] == pytest.approx(.05)
    assert result["candidates"][1]["spread_rate"] < 0
    assert result["stats"]["selection"] == "global"


def test_verified_small_asset_many_venues_groups_best_path_and_complete_direction_coverage():
    markets = [market(f"{venue}:{kind}", venue, "TURBO", kind=kind)
               for venue in ["alpha", "beta", "gamma", "delta", "epsilon"]
               for kind in ["spot", "perpetual"]]
    snapshots = [snap(item, bid=105 if item.venue == "epsilon" else 99,
                      ask=100 if item.venue == "alpha" else 106) for item in markets]
    result = run(markets, snapshots, limit=1000)
    group = result["groups"][0]
    assert group["asset_symbols"] == ["TURBO"] and group["identity_confirmed"]
    assert group["venue_count"] == 5 and group["market_count"] == 10
    assert group["possible_directions"] == group["scanned_directions"] == group["directions_returned"] == 80
    best = group["best_spread_candidate"]
    assert best["buy_venue"] == "alpha" and best["sell_venue"] == "epsilon"
    assert best["spread_rate"] == pytest.approx(.05)
    assert group["best_within_scan"] and group["scan_complete"]


def test_missing_source_time_is_indicative_but_excluded_from_reliable_best_and_sorted_after_verified():
    a, b, c = market("a", "alpha"), market("b", "beta"), market("c", "gamma")
    result = run([a, b, c], [snap(a, bid=99, ask=100), snap(b, bid=105, ask=106),
                             snap(c, bid=999, ask=1000, source_at=None)])
    best = result["groups"][0]["best_spread_candidate"]
    assert best["buy_instrument_id"] == "a" and best["sell_instrument_id"] == "b"
    assert result["candidates"][0]["price_rankable"]
    unknown = direction(result, "a", "c")
    assert unknown["spread_rate"] > best["spread_rate"] and not unknown["price_rankable"]
    assert result["groups"][0]["excluded_from_price_ranking"] > 0


@pytest.mark.parametrize("values", [{"source_at": NOW - 61000}, {"source_at": NOW + 1000},
                                    {"ask": None}, {"source_at": None}])
def test_stale_future_missing_and_unknown_price_data_cannot_create_reliable_group_best(values):
    a, b = market("a", "alpha"), market("b", "beta")
    result = run([a, b], [snap(a, **values), snap(b)])
    group = result["groups"][0]
    if "ask" in values:  # The reverse direction can still have fresh sell bid.
        assert not direction(result, "a", "b")["price_rankable"]
    else:
        assert group["best_spread_candidate"] is None


def test_mixed_quote_pair_has_no_best_price_even_when_prices_are_fresh():
    a, b = market("a", "alpha", quote="USD"), market("b", "beta", quote="USDT")
    result = run([a, b])
    assert result["groups"][0]["best_spread_candidate"] is None
    assert all(not candidate["price_rankable"] for candidate in result["candidates"])


def test_reverse_direction_uses_opposite_bid_ask_and_retains_field_provenance():
    a, b = market("a", "alpha"), market("b", "beta")
    first = snap(a, bid=99, ask=100, field_timestamps={"bid": NOW - 1000, "ask": NOW - 2000},
                 field_received_at={"bid": NOW - 500, "ask": NOW - 1500},
                 field_sources={"bid": "fixture:bid-feed", "ask": "fixture:ask-feed"})
    row = direction(run([a, b], [first, snap(b, bid=105, ask=106)]), "a", "b")
    assert row["spread_rate"] == pytest.approx(.05)
    assert row["reverse_spread_rate"] == pytest.approx((99 - 106) / 106)
    assert row["buy_market"]["native_symbol"] == "BRETT/USDT"
    assert row["buy_market"]["fields"]["bid"] == {
        "valid": True, "issue": None, "source": "fixture:bid-feed", "source_at": NOW - 1000,
        "received_at": NOW - 500, "age_seconds": 1.0, "rankable": True}
    assert row["buy_market"]["fields"]["ask"]["source_at"] == NOW - 2000


def test_asset_search_selects_whole_group_and_platform_filter_restricts_coverage():
    a, b, c = market("a", "alpha"), market("b", "beta"), market("c", "gamma", "BONK")
    b.native_symbol = "BRETT/USDC:USDC"
    result = run([a, b, c], q="brett/usdc")
    assert len(result["groups"]) == 1 and result["groups"][0]["market_count"] == 2
    assert result["stats"]["possible_candidates"] == 2
    filtered = run([a, b, c], q="BRETT", platforms=["alpha"])
    assert filtered["groups"][0]["market_count"] == 1 and filtered["candidates"] == []
    assert filtered["available_platforms"] == ["alpha", "beta", "gamma"]


def test_search_cannot_hide_conflicting_identity_evidence_or_merge_unconfirmed_tickers():
    a = market("a", "alpha", native_asset_id="base:0x" + "11" * 20, chain="base")
    b = market("b", "beta", native_asset_id="base:0x" + "22" * 20, chain="base")
    result = run([a, b], q="a")
    assert result["groups"][0]["market_count"] == 2 and not result["groups"][0]["identity_confirmed"]
    assert result["groups"][0]["best_spread_candidate"] is None
    unknown = run([market("u", "alpha", asset_id="alpha:BRETT", mapping_verified=False),
                   market("v", "beta", asset_id="beta:BRETT", mapping_verified=False)], q="BRETT")
    assert len(unknown["groups"]) == 2 and all(not row["identity_confirmed"] for row in unknown["groups"])


def test_truncated_group_explicitly_limits_best_to_scanned_prefix():
    markets = [market(str(index), f"venue{index}") for index in range(4)]
    result = run(markets, max_candidates=2, limit=1)
    group = result["groups"][0]
    assert group["best_within_scan"] and not group["scan_complete"]
    assert group["possible_directions"] == 12 and group["scanned_directions"] == 2
    assert group["directions_truncated"] and group["directions_returned"] == 1


def test_funding_shows_leg_payment_direction_and_unknown_or_different_settlement():
    a, b = market("a", "alpha", kind="perpetual"), market("b", "beta", kind="perpetual")
    first = snap(a, funding_rate=.001, funding_kind="current", funding_interval_hours=1,
                 next_funding_at=NOW + 3600000)
    second = snap(b, funding_rate=.002, funding_kind="current", funding_interval_hours=8,
                  next_funding_at=NOW + 7200000)
    row = direction(run([a, b], [first, second]), "a", "b")
    assert row["funding"]["long"]["payment_direction"] == "pay"
    assert row["funding"]["short"]["payment_direction"] == "receive"
    assert row["funding"]["long"]["interval_hours"] == 1
    assert row["settlement_alignment"] == "different" and row["settlement_time_difference_seconds"] == 3600
    second.next_funding_at = None
    assert direction(run([a, b], [first, second]), "a", "b")["settlement_alignment"] == "unknown"


def test_unknown_funding_source_does_not_outrank_time_verified_rates():
    markets = [market(identity, venue, kind="perpetual") for identity, venue in
               [("a", "alpha"), ("b", "beta"), ("c", "gamma")]]
    snapshots = [snap(markets[0], funding_rate=.001, funding_kind="current", funding_interval_hours=8),
                 snap(markets[1], funding_rate=.002, funding_kind="current", funding_interval_hours=8),
                 snap(markets[2], funding_rate=.1, funding_kind="current", funding_interval_hours=8, source_at=None)]
    result = run(markets, snapshots, sort="funding")
    assert result["candidates"][0]["funding_rankable"]
    assert result["groups"][0]["directions"][0]["funding_rankable"]
    assert result["groups"][0]["best_funding_candidate"]["sell_instrument_id"] == "b"
    assert not direction(result, "a", "c")["funding_rankable"]


@pytest.mark.parametrize("field", ["funding_interval_hours", "funding_kind"])
def test_funding_reliable_best_requires_period_and_kind_provenance_not_just_rate(field):
    a, b = market("a", "alpha", kind="perpetual"), market("b", "beta", kind="perpetual")
    snapshots = [snap(item, funding_rate=.001, funding_kind="current", funding_interval_hours=8)
                 for item in [a, b]]
    snapshots[1].field_timestamps[field] = None
    snapshots[1].field_sources[field] = "unclassified public value"
    result = run([a, b], snapshots, sort="funding")
    assert all(row["funding_8h_rate"] is not None for row in result["candidates"])  # Indicative values survive.
    assert all(not row["funding_rankable"] for row in result["candidates"])
    assert result["groups"][0]["best_funding_candidate"] is None


def test_future_values_are_excluded_and_historical_inputs_only_use_known_snapshots():
    a, b = market("a", "alpha"), market("b", "beta")
    future = snap(a, received_at=NOW + 1000, source_at=NOW + 1000, bid=999, ask=1000)
    past = snap(a, bid=99, ask=100, received_at=NOW - 1000, source_at=NOW - 1000)
    result = run([a, b], [future, past, snap(b, bid=105, ask=106)])
    assert direction(result, "a", "b")["buy_ask"] == 100
    only_future = direction(run([a, b], [future, snap(b)]), "a", "b")
    assert only_future["buy_ask"] is only_future["spread_rate"] is None
    assert "buy:ask_timestamp_in_future" in only_future["missing"]


@pytest.mark.parametrize("change,reason", [({"valid": False}, "snapshot_invalid"),
                                          ({"is_demo": True}, "demo_snapshot_excluded"),
                                          ({"venue": "wrong"}, "snapshot_identity_mismatch"),
                                          ({"bid": 101, "ask": 100}, "crossed_orderbook")])
def test_invalid_demo_mismatched_or_crossed_data_never_produces_numbers(change, reason):
    a, b = market("a", "alpha"), market("b", "beta")
    bad = snap(a).model_copy(update=change)
    row = direction(run([a, b], [bad, snap(b)]), "a", "b")
    assert row["spread_rate"] is None and f"buy:{reason}" in row["missing"]


def test_inputs_are_not_mutated_and_mapping_inputs_are_supported():
    markets = [market("a", "alpha"), market("b", "beta", active=False)]
    snapshots = [snap(item) for item in markets]
    before = deepcopy((markets, snapshots))
    result = run({item.id: item for item in markets}, {item.instrument_id: item for item in snapshots})
    assert result["stats"]["catalog_markets"] == 2 and result["stats"]["active_markets"] == 1
    assert result["candidates"] == [] and (markets, snapshots) == before


def test_api_filters_disabled_venues_active_markets_and_honors_settings(monkeypatch):
    from watch_money.query_work import QueryWork
    app = FastAPI()
    app.state.query_work = QueryWork()
    app.router.add_event_handler('shutdown', app.state.query_work.close)
    app.include_router(router)
    markets = [market("a", "alpha"), market("b", "beta"), market("c", "gamma"),
               market("d", "alpha", active=False)]
    settings = Settings(enabled_venues=["alpha", "beta"], watch_assets=["BTC"])
    app.state.store = SimpleNamespace(settings=lambda: settings)
    app.state.collector = SimpleNamespace(instruments={item.id: item for item in markets},
                                         snapshots={item.id: snap(item) for item in markets})
    monkeypatch.setattr(scanner, "now_ms", lambda: NOW)
    with TestClient(app) as client:
        response = client.get("/api/scanner", params={"sort": "asset", "limit": 1})
        assert response.status_code == 200
        result = response.json()
        assert result["enabled"] and result["stats"]["possible_candidates"] == 2
        assert result["stats"]["active_markets"] == 2
        assert result["stats"]["excluded_by_settings"] == 1
        assert result["stats"]["result_truncated"]
        assert result["candidates"][0]["asset_symbols"] == ["BRETT"]
        filtered = client.get("/api/scanner", params={"q": "brett/usdt", "platforms": "alpha"}).json()
        assert filtered["groups"][0]["market_count"] == 1 and not filtered["candidates"]
        assert filtered["filters"] == {"q": "brett/usdt", "platforms": ["alpha"], "market_type": "all"}
        perpetuals = client.get("/api/scanner", params={"market_type": "perpetual"}).json()
        assert perpetuals["filters"]["market_type"] == "perpetual"
        assert perpetuals["stats"]["active_markets"] == 0 and perpetuals["candidates"] == []
        settings.computation_enabled = False
        assert client.get("/api/scanner").json()["candidates"] == []
        settings.computation_enabled, settings.demo_mode = True, True
        assert client.get("/api/scanner").json()["enabled"] is False
        assert client.get("/api/scanner", params={"sort": "unknown"}).status_code == 422
        assert client.get("/api/scanner", params={"market_type": "futures"}).status_code == 422
        assert client.get("/api/scanner", params={"max_candidates": 100001}).status_code == 422


@pytest.mark.parametrize("values", [{"limit": 0}, {"limit": 1001}, {"max_candidates": 0},
                                    {"max_candidates": 100001}, {"sort": "unknown"}, {"market_type": "futures"}])
def test_explicit_resource_bounds_are_validated(values):
    with pytest.raises(ValueError):
        run([], **values)


def test_asset_pagination_reaches_beyond_previous_thousand_group_cutoff():
    items = [market(f'{base}:{venue}', venue, base) for base in
             [f'SMALL{i:04}' for i in range(1005)] for venue in ['alpha', 'beta']]
    first = run(items, sort='asset', limit=10, group_limit=100)
    last = run(items, sort='asset', limit=10, group_limit=100, group_offset=1000)
    assert len(first['groups']) == 100 and len(last['groups']) == 5
    assert last['groups'][0]['asset_symbols'] == ['SMALL1000']
    assert last['stats']['group_offset'] == 1000 and last['stats']['next_group_offset'] is None
    assert first['stats']['next_group_offset'] == 100
    assert first['stats']['groups_total'] == last['stats']['groups_total'] == 1005
    assert first['stats']['scanned'] == last['stats']['scanned'] == 2010
    assert first['candidates'] == last['candidates']  # Independent candidate/output budgets.


def test_asset_pages_do_not_hide_enumeration_budget_or_identity_blockers():
    items = [market(f'{base}:{venue}', venue, base) for base in ['AAA', 'BBB', 'CCC'] for venue in ['alpha', 'beta']]
    report = run(items, sort='asset', max_candidates=1, group_limit=1, group_offset=2)
    assert report['stats']['enumeration_truncated'] and report['stats']['scanned'] == 1
    assert report['groups'][0]['scanned_directions'] == 0
    assert report['stats']['group_pagination'] == 'live_query_not_snapshot'
    unknown = [item.model_copy(update={'mapping_verified': False}) for item in items]
    blocked = run(unknown, group_limit=1, group_offset=1)
    assert not blocked['groups'][0]['identity_confirmed']


def test_group_page_out_of_range_is_empty_and_search_still_scopes_catalog():
    items = [market(f'{base}:{venue}', venue, base) for base in ['AAA', 'BBB'] for venue in ['alpha', 'beta']]
    report = run(items, q='BBB', group_limit=1, group_offset=5)
    assert report['groups'] == [] and report['stats']['groups_returned'] == 0
    assert report['stats']['groups_total'] == 1 and report['stats']['next_group_offset'] is None
    assert report['filters']['q'] == 'BBB'


@pytest.mark.parametrize('extra', [{'group_offset': -1}, {'group_offset': True}, {'group_offset': 1.5},
                                  {'group_offset': 100_000_001}, {'group_limit': 0}, {'group_limit': True}, {'group_limit': 1001}])
def test_group_pagination_bounds(extra):
    with pytest.raises(ValueError):
        run([], **extra)
