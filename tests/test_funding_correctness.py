"""Public source-time bridge and compatible funding cohorts, without exchange IO."""
import pytest
from watch_money.adapters.cex import CexAdapter
from watch_money.funding_view import build_funding_view
from watch_money.models import Instrument, MarketSnapshot
from watch_money.scanner import scan_markets

NOW = 1_800_000_000_000


def item(identity, venue):
    return Instrument(id=identity, venue=venue, native_symbol="SMALL/USDT:USDT", base="SMALL", quote="USDT",
                      settle="USDT", asset_id="asset:SMALL", kind="perpetual", mapping_verified=True)


def observation(instrument, rate=.001, kind="current", **values):
    return MarketSnapshot(instrument_id=instrument.id, venue=instrument.venue, source=f"fixture:{instrument.venue}",
                          source_at=NOW, received_at=NOW, funding_rate=rate, funding_kind=kind,
                          funding_interval_hours=8, bid=100, ask=101, **values)


def okx_patch(raw, *, unified_timestamp=None):
    ccxt = pytest.importorskip("ccxt")
    market = {"id": "BTC-USD-SWAP", "symbol": "BTC/USD:BTC", "base": "BTC", "quote": "USD", "settle": "BTC"}
    # The official SDK parser preserves raw info, including ts. CCXT 4.5.91
    # leaves timestamp empty; force that case even after a future upstream fix.
    payload = ccxt.okx().parse_funding_rate(raw, market)
    payload["timestamp"] = unified_timestamp
    instrument = Instrument(id="okx:fixture", venue="okx", native_symbol=market["symbol"],
                            asset_id="okx:BTC", base="BTC", quote="USD", settle="BTC", linear=False,
                            kind="perpetual")
    adapter = CexAdapter("okx")
    adapter._markets[instrument.id] = ("okx", market)
    return adapter._funding_patch(instrument, payload, "fetch_funding_rate", 1700724675502)


def test_official_okx_sample_preserves_raw_data_time_without_using_settlement_as_event_time():
    # Official public funding-rate WebSocket sample and documented ts semantics:
    # https://www.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel
    # Parser: https://github.com/ccxt/ccxt/blob/v4.5.91/python/ccxt/okx.py
    raw = {"fundingRate": "0.0001875391284828", "fundingTime": "1700726400000", "instId": "BTC-USD-SWAP",
           "instType": "SWAP", "nextFundingRate": "", "nextFundingTime": "1700755200000",
           "settFundingRate": "0.0001699799259033", "settState": "settled", "ts": "1700724675402"}
    patch = okx_patch(raw)
    assert patch.source_at == 1700724675402
    assert patch.next_funding_at == 1700726400000
    assert patch.source_at != patch.next_funding_at
    assert patch.funding_interval_hours == 8 and patch.funding_rate == pytest.approx(.0001875391284828)
    assert patch.field_timestamps["funding_rate"] == patch.field_timestamps["funding_kind"] == patch.source_at
    assert "info.ts" in patch.field_sources["funding_rate"]
    assert patch.field_received_at["funding_kind"] == 1700724675502
    # Settled raw info describes the separate settFundingRate, never this live estimate.
    assert patch.funding_kind == "current"
    assert okx_patch(raw, unified_timestamp=1700724675302).source_at == 1700724675302


@pytest.mark.parametrize("data_time", [None, "", "invalid", 1700724675])
def test_okx_missing_or_invalid_data_time_never_uses_future_funding_time(data_time):
    patch = okx_patch({"instId": "BTC-USD-SWAP", "fundingRate": "0", "fundingTime": "1700726400000",
                       "nextFundingTime": "1700755200000", "ts": data_time})
    assert patch.source_at is None and patch.field_timestamps["funding_rate"] is None
    assert patch.funding_rate == 0 and patch.next_funding_at == 1700726400000


@pytest.mark.parametrize("field", ["funding_rate", "funding_kind", "funding_interval_hours"])
@pytest.mark.parametrize("provenance", ["unknown_time", "empty_source"])
def test_scanner_and_funding_matrix_share_identical_provenance_gate(field, provenance):
    a, b = item("a", "alpha"), item("b", "beta")
    first, second = observation(a), observation(b, .9)
    if provenance == "unknown_time":
        second.field_timestamps[field] = None
    else:
        second.field_sources[field] = ""
    scan = scan_markets([a, b], [first, second], NOW, 60)
    view = build_funding_view([a, b], [first, second], NOW, 60)
    row = next(row for row in view["rows"] if row["instrument_id"] == "b")
    assert not row["field_validity"][field]["rankable"]
    assert row["raw_rate"] == .9  # preserve labelled observations for diagnostics
    assert view["groups"][0]["spread_8h_rate"] is None
    assert all(not direction["funding_rankable"] for direction in scan["candidates"])
    assert view["rows"][0]["instrument_id"] == "a"


@pytest.mark.parametrize("other_kind", ["predicted", "settled"])
def test_incompatible_third_venue_does_not_poison_same_type_pair(other_kind):
    markets = [item("a", "alpha"), item("b", "beta"), item("c", "gamma")]
    snapshots = [observation(markets[0], .001), observation(markets[1], .002),
                 observation(markets[2], .9, other_kind)]
    group = build_funding_view(markets, snapshots, NOW, 60)["groups"][0]
    scan = scan_markets(markets, snapshots, NOW, 60, sort="funding")
    assert group["spread_8h_rate"] == pytest.approx(.001) and group["comparison_ready"]
    assert group["long_instrument_id"] == "a" and group["short_instrument_id"] == "b"
    assert group["funding_kind"] == "current" and "funding_kinds_differ" in group["warnings"]
    assert "funding_kinds_differ" not in group["missing"]
    best = scan["groups"][0]["best_funding_candidate"]
    assert best["buy_instrument_id"] == "a" and best["sell_instrument_id"] == "b"
    cohorts = {cohort["funding_kind"]: cohort for cohort in group["compatible_comparisons"]}
    assert cohorts["current"]["comparison_ready"] and not cohorts[other_kind]["comparison_ready"]


def test_multiple_type_cohorts_compare_inside_type_without_cross_type_extremes():
    markets = [item(identity, venue) for identity, venue in zip("abcd", ["alpha", "beta", "gamma", "delta"], strict=True)]
    snapshots = [observation(markets[0], .001), observation(markets[1], .002),
                 observation(markets[2], .1, "predicted"), observation(markets[3], .105, "predicted")]
    group = build_funding_view(markets, snapshots, NOW, 60)["groups"][0]
    assert group["spread_8h_rate"] == pytest.approx(.005) and group["funding_kind"] == "predicted"
    assert {row["funding_kind"] for row in group["compatible_comparisons"] if row["comparison_ready"]} == {"current", "predicted"}


@pytest.mark.parametrize("offset,ready", [(-1, False), (0, True), (1, True)])
def test_replay_uses_mapping_approval_cutoff_in_both_rankings(offset, ready):
    from watch_money.models import now_ms
    approved = now_ms() - 10000
    markets = [item("a", "alpha"), item("b", "beta")]
    # One leg was already known; the other was approved after the historical
    # quote. Today's valid mapping must not retroactively authorize that pair.
    markets = [market.model_copy(update={"mapping_verified_at": approved - 1000 if index == 0 else approved,
               "mapping_evidence_status": "verified", "mapping_expires_at": approved + 86400000})
               for index, market in enumerate(markets)]
    snapshots = [observation(market, .001 * (index + 1)).model_copy(update={
                 "source_at": approved - 100, "received_at": approved - 100})
                 for index, market in enumerate(markets)]
    scan = scan_markets(markets, snapshots, approved + offset, 60)
    view = build_funding_view(markets, snapshots, approved + offset, 60)
    assert bool(scan["groups"][0]["best_spread_candidate"]) == ready
    assert bool(scan["groups"][0]["best_funding_candidate"]) == ready
    assert bool(view["groups"]) == ready
    assert view["matrix_groups"][0]["comparison_ready"] == ready
    if not ready:
        assert "mapping_not_known_as_of" in scan["groups"][0]["identity_blockers"]


@pytest.mark.parametrize('age_ms,rankable', [(60000, True), (61000, False), (72000, False)])
def test_okx_successful_response_does_not_renew_old_source_time(age_ms, rankable):
    from watch_money.collector import funding_response_source_ages
    from watch_money.scanner import funding_field_state
    receipt = 1700724675502
    raw = {'instId': 'BTC-USD-SWAP', 'fundingRate': '0.001', 'ts': str(receipt-age_ms),
           'fundingTime': '1700726400000', 'nextFundingTime': '1700755200000'}
    patch = okx_patch(raw)
    instrument = Instrument(id='okx:fixture', venue='okx', native_symbol='BTC/USD:BTC', base='BTC',
                            quote='USD', settle='BTC', asset_id='okx:BTC', kind='perpetual', linear=False)
    state = funding_field_state(patch, instrument, 'funding_rate', receipt, 60)
    assert patch.source_at == receipt-age_ms and patch.received_at == receipt
    assert state['rankable'] is rankable
    assert funding_response_source_ages([patch], {instrument.id: instrument}, receipt, 60) == [age_ms/1000]
    # A later successful HTTP observation cannot renew the source timestamp.
    again = patch.model_copy(update={'received_at': receipt+1000,
                                    'field_received_at': {key: receipt+1000 for key in patch.field_received_at}})
    assert not funding_field_state(again, instrument, 'funding_rate', receipt+1000, 60)['rankable']
