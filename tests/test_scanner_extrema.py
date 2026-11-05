"""Price extrema must match exhaustive results without materializing all pairs."""
import random
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from watch_money import scanner
from watch_money.models import Instrument, MarketSnapshot, Settings
from watch_money.query_work import QueryWork

NOW = 1800000000000


def market(identity, venue, **values):
    return Instrument(id=identity, venue=venue, native_symbol=identity, base="TEST",
                      asset_id=values.pop("asset_id", "confirmed:TEST"),
                      quote=values.pop("quote", "USDT"), kind=values.pop("kind", "spot"),
                      mapping_verified=True, **values)


def snap(item, bid=99, ask=101, **values):
    return MarketSnapshot(instrument_id=item.id, venue=item.venue, bid=bid, ask=ask,
                          received_at=values.pop("received_at", NOW),
                          source_at=values.pop("source_at", NOW), source="fixture:public", **values)


def compare(markets, snapshots, **kwargs):
    full = scanner.scan_markets(markets, snapshots, NOW, 60, limit=1000,
                                max_candidates=100000, **kwargs)
    fast = scanner.scan_markets(markets, snapshots, NOW, 60, limit=1000,
                                comparison_mode="extrema", **kwargs)
    expected = {row["id"]: row["best_spread_candidate"] for row in full["groups"]}
    for group in fast["groups"]:
        want, got = expected[group["id"]], group["best_spread_candidate"]
        assert (got is None) == (want is None)
        if want:
            assert got["spread_rate"] == pytest.approx(want["spread_rate"])
            assert got["buy_venue"] != got["sell_venue"]
            assert got["buy_quote"] == got["sell_quote"]
            assert got["price_rankable"]
        assert group["best_price_complete"]
    assert fast["stats"]["possible_candidates"] == full["stats"]["possible_candidates"]
    assert not fast["stats"]["enumeration_truncated"]
    return fast


@pytest.mark.parametrize("market_type", ["all", "spot", "perpetual", "spot_perp"])
def test_extrema_matches_exhaustive_winner_across_random_markets_and_field_validity(market_type):
    rng = random.Random(479)
    for case in range(15):
        markets = [market(f"{case}:{i}", f"venue{i % 4}", quote=rng.choice(["USDT", "USD", "EUR"]),
                          kind=rng.choice(["spot", "perpetual"])) for i in range(18)]
        snapshots = []
        for item in markets:
            bid = rng.uniform(20, 200)
            values = rng.choice([{}, {}, {}, {"source_at": NOW - 61000},
                                 {"source_at": None}, {"source_at": NOW + 1000}])
            snapshots.append(snap(item, bid=bid if rng.random() > .1 else None,
                                  ask=bid + 1 if rng.random() > .1 else None, **values))
        compare(markets, snapshots, market_type=market_type)


def test_same_venue_extremes_use_the_best_other_venue_instead():
    markets = [market("cheap", "alpha"), market("high", "alpha"), market("other", "beta")]
    result = compare(markets, [snap(markets[0], 99, 100), snap(markets[1], 200, 201),
                               snap(markets[2], 149, 150)])
    best = result["groups"][0]["best_spread_candidate"]
    assert (best["buy_instrument_id"], best["sell_instrument_id"]) == ("cheap", "other")
    assert best["spread_rate"] == pytest.approx(.49)


def test_quote_buckets_keep_fx_and_execution_restrictions_out_of_extrema():
    markets = [market("a", "alpha"), market("b", "beta"),
               market("wrong-quote", "gamma", quote="EUR"),
               market("restricted", "delta", execution_restrictions=["market_not_tradable"])]
    result = compare(markets, [snap(markets[0], 99, 100), snap(markets[1], 105, 106),
                               snap(markets[2], 10000, 10001), snap(markets[3], 50000, 50001)])
    assert result["groups"][0]["best_spread_candidate"]["sell_instrument_id"] == "b"


def test_quote_contracts_with_matching_symbols_remain_separate_and_require_evidence():
    common = dict(chain="base", kind="pool", mechanism="amm", quote_identity_at=NOW,
                  quote_identity_source="https://example.org/official-token-list")
    q1, q2 = "base:0x" + "11" * 20, "base:0x" + "22" * 20
    markets = [market("a", "alpha", native_quote_asset_id=q1, **common),
               market("b", "beta", native_quote_asset_id=q1, **common),
               market("different-token", "gamma", native_quote_asset_id=q2, **common),
               market("unproven", "delta", chain="base", kind="pool", mechanism="amm")]
    result = compare(markets, [snap(markets[0], 99, 100), snap(markets[1], 105, 106),
                               snap(markets[2], 10000, 10001), snap(markets[3], 50000, 50001)])
    assert result["groups"][0]["best_spread_candidate"]["sell_instrument_id"] == "b"


def test_extrema_covers_all_assets_even_when_legacy_direction_budget_is_one():
    markets = [market(f"{asset}:{venue}", venue, asset_id=f"confirmed:{asset}")
               for asset in ["BTC", "ETH", "SOL", "SMALL"] for venue in ["alpha", "beta"]]
    result = scanner.scan_markets(markets, [snap(item) for item in markets], NOW, 60,
                                  max_candidates=1, comparison_mode="extrema")
    assert len(result["groups"]) == 4
    assert all(group["best_spread_candidate"] for group in result["groups"])
    assert result["stats"]["selection"] == "global_extrema"


def test_hundreds_of_markets_materialize_only_extreme_and_reverse_rows(monkeypatch):
    markets = [market(str(i), f"venue{i % 10}") for i in range(200)]
    calls = []
    original = scanner._candidate

    def observed(*args, **kwargs):
        calls.append((args[0].id, args[1].id))
        return original(*args, **kwargs)

    monkeypatch.setattr(scanner, "_candidate", observed)
    result = scanner.scan_markets(markets, [snap(item) for item in markets], NOW, 60,
                                  comparison_mode="extrema")
    assert len(calls) == 2
    assert result["stats"]["possible_candidates"] == 36000
    assert result["stats"]["pruned_directions"] == 35998


def test_overflowing_extreme_finds_the_best_representable_alternative():
    markets = [market("tiny", "alpha"), market("huge", "beta"), market("normal", "beta")]
    compare(markets, [snap(markets[0], 1e-300, 2e-300), snap(markets[1], 1e300, 2e300),
                      snap(markets[2], 1, 2)])


def test_http_defaults_to_extrema_and_direction_or_funding_requests_keep_explicit_enumeration(monkeypatch):
    markets = [market(str(i), f"venue{i % 3}") for i in range(12)]
    app = FastAPI()
    app.state.query_work = QueryWork()
    app.router.add_event_handler("shutdown", app.state.query_work.close)
    app.state.store = SimpleNamespace(settings=lambda: Settings(enabled_venues=["venue0", "venue1", "venue2"]))
    app.state.collector = SimpleNamespace(instruments={m.id: m for m in markets},
                                         snapshots={m.id: snap(m) for m in markets})
    app.include_router(scanner.router)
    monkeypatch.setattr(scanner, "now_ms", lambda: NOW)
    with TestClient(app) as client:
        fast = client.get("/api/scanner").json()
        assert fast["stats"]["selection"] == "global_extrema"
        assert fast["stats"]["scanned"] == 2
        detail = client.get("/api/scanner?comparison_mode=directions").json()
        assert detail["stats"]["scanned"] == detail["stats"]["possible_candidates"] == 96
        funding = client.get("/api/scanner?sort=funding").json()
        assert funding["stats"]["comparison_mode"] == "directions"
        assert funding["stats"]["scanned"] == 96
        assert client.get("/api/scanner?comparison_mode=invalid").status_code == 422
