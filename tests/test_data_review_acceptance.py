"""Independent data-review regressions using synthetic fixtures, never live API evidence."""
import pytest

from watch_money.funding_view import build_funding_view
from watch_money.models import Instrument, MarketSnapshot
from watch_money.scanner import scan_markets
from watch_money.strategies import CalculateRequest, FXRate, calculate


AS_OF = 1_793_820_000_000


def funding_market(venue, rate, kind="current"):
    instrument = Instrument(id=f"review:{venue}", venue=venue, native_symbol="SMALL/USDT",
                            asset_id="reviewed:small", native_asset_id=f"{venue}:small",
                            base="SMALL", quote="USDT", settle="USDT", kind="perpetual",
                            mapping_verified=True)
    snapshot = MarketSnapshot(instrument_id=instrument.id, venue=venue, funding_rate=rate,
                              funding_interval_hours=8, funding_kind=kind,
                              next_funding_at=AS_OF + 3600000, received_at=AS_OF, source_at=AS_OF,
                              source="synthetic:data-review")
    return instrument, snapshot


@pytest.mark.parametrize("field", ["funding_rate", "funding_interval_hours", "funding_kind"])
@pytest.mark.parametrize("missing", ["event_time", "source"])
def test_funding_matrix_and_scanner_agree_unknown_provenance_cannot_be_reliable_best(field, missing):
    markets, snapshots = map(list, zip(funding_market("a", .001), funding_market("b", .003)))
    for snapshot in snapshots:
        if missing == "event_time":
            snapshot.field_timestamps[field] = None
            snapshot.field_sources[field] = "synthetic:unclassified-public-field"
        else:
            snapshot.field_sources[field] = ""
    scanner = scan_markets(markets, snapshots, AS_OF, 60, sort="funding")
    assert all(not row["funding_rankable"] for row in scanner["candidates"])
    matrix = build_funding_view(markets, snapshots, AS_OF, 60)
    assert all(not row["comparison_ready"] and row["spread_8h_rate"] is None
               for row in matrix["matrix_groups"]), (
        "The same unknown provenance rejected by scanner must not become a reliable matrix comparison")


def test_recent_observed_interval_metadata_remains_usable_without_inventing_event_time():
    markets, snapshots = map(list, zip(funding_market("a", .001), funding_market("b", .003)))
    for snapshot in snapshots:
        snapshot.field_timestamps["funding_interval_hours"] = None
        snapshot.field_received_at["funding_interval_hours"] = AS_OF - 3600000
        snapshot.field_sources["funding_interval_hours"] = "synthetic:catalog_metadata_observed_at"
    scanner = scan_markets(markets, snapshots, AS_OF, 60, sort="funding")
    matrix = build_funding_view(markets, snapshots, AS_OF, 60)
    assert scanner["groups"][0]["best_funding_candidate"]["funding_8h_rate"] == pytest.approx(.002)
    assert matrix["matrix_groups"][0]["spread_8h_rate"] == pytest.approx(.002)
    assert all(row["field_validity"]["funding_interval_hours"]["source_at"] is None for row in matrix["rows"])
    # Legitimate interval metadata must not refresh an older dynamic funding rate.
    for snapshot in snapshots:
        snapshot.field_timestamps["funding_rate"] = AS_OF - 61000
    assert all(not row["funding_rankable"] for row in scan_markets(markets, snapshots, AS_OF, 60)["candidates"])
    assert all(not row["comparison_ready"] for row in build_funding_view(markets, snapshots, AS_OF, 60)["matrix_groups"])


@pytest.mark.parametrize("outlier_kind", ["predicted", "settled"])
def test_other_funding_kind_does_not_erase_compatible_current_pair(outlier_kind):
    markets, snapshots = map(list, zip(funding_market("a", .001), funding_market("b", .003),
                                       funding_market("c", .9, outlier_kind)))
    scanner = scan_markets(markets, snapshots, AS_OF, 60, sort="funding")
    best = scanner["groups"][0]["best_funding_candidate"]
    assert (best["buy_instrument_id"], best["sell_instrument_id"]) == ("review:a", "review:b")
    matrix = build_funding_view(markets, snapshots, AS_OF, 60)
    compatible = [row for row in matrix["matrix_groups"] if row["comparison_ready"]]
    assert compatible, "A third venue's incompatible kind must not poison the two valid current-rate venues"
    assert any(row["long_instrument_id"] == "review:a" and row["short_instrument_id"] == "review:b"
               and row["spread_8h_rate"] == pytest.approx(.002) for row in compatible)


@pytest.mark.parametrize("native_ts", [AS_OF - 1000, None, AS_OF - 61000, AS_OF + 1000, 1793820000])
def test_real_ccxt_okx_parser_preserves_native_return_time_without_settlement_or_receipt_substitution(native_ts):
    import ccxt
    from watch_money.adapters.cex import CexAdapter

    instrument, _ = funding_market("okx", .003)
    market = {"id": "SMALL-USDT-SWAP", "symbol": "SMALL/USDT:USDT", "base": "SMALL",
              "quote": "USDT", "settle": "USDT", "swap": True, "spot": False, "linear": True,
              "inverse": False, "contractSize": 1, "info": {}}
    instrument.native_symbol = market["symbol"]
    raw = {"instId": market["id"], "fundingRate": "0.003", "fundingTime": str(AS_OF + 3600000),
           "nextFundingTime": str(AS_OF + 9 * 3600000)}
    if native_ts is not None:
        raw["ts"] = str(native_ts)
    # This invokes the installed CCXT parser on a synthetic response; no API is contacted.
    parsed = ccxt.okx().parse_funding_rate(raw, market)
    assert parsed["timestamp"] is None
    adapter = CexAdapter("okx")
    adapter._markets[instrument.id] = ("okx", market)
    patch = adapter._funding_patch(instrument, parsed, "fetch_funding_rate", AS_OF)
    expected_at = native_ts if native_ts is not None and native_ts > 1_000_000_000_000 else None
    assert patch.field_timestamps["funding_rate"] == expected_at
    assert patch.field_received_at["funding_rate"] == AS_OF
    assert patch.funding_interval_hours == 8
    if expected_at is not None:
        assert "info.ts" in patch.field_sources["funding_rate"]
    other, other_snapshot = funding_market("b", .001)
    scanner = scan_markets([instrument, other], [patch, other_snapshot], AS_OF, 60, sort="funding")
    matrix = build_funding_view([instrument, other], [patch, other_snapshot], AS_OF, 60)
    eligible = native_ts == AS_OF - 1000
    assert bool(scanner["groups"][0]["best_funding_candidate"]) is eligible
    assert matrix["matrix_groups"][0]["comparison_ready"] is eligible


def test_funding_compatibility_does_not_automatically_merge_same_ticker_identities():
    markets, snapshots = map(list, zip(funding_market("a", .001), funding_market("b", .003)))
    for item in markets:
        item.asset_id = f"unreviewed:{item.venue}:SMALL"
        item.mapping_verified = False
    matrix = build_funding_view(markets, snapshots, AS_OF, 60)
    assert len(matrix["matrix_groups"]) == 2
    assert all(not row["comparison_ready"] for row in matrix["matrix_groups"])
    assert scan_markets(markets, snapshots, AS_OF, 60)["candidates"] == []


def test_scanner_dex_quotes_with_unknown_token_identity_cannot_be_reliable_best():
    identity = "base:0x" + "11" * 20
    markets = [Instrument(id=venue, venue=venue, native_symbol="SMALL/USDT", asset_id=identity,
                          native_asset_id=identity, base="SMALL", quote="USDT", kind="pool",
                          mechanism="amm", chain="base", mapping_verified=True)
               for venue in ("uniswap", "pancakeswap")]
    snapshots = [MarketSnapshot(instrument_id=item.id, venue=item.venue,
                                bid=99 if index == 0 else 105, ask=100 if index == 0 else 106,
                                source="synthetic:pool", source_at=AS_OF, received_at=AS_OF,
                                issues=["quote_token_contract_identity_unavailable"])
                 for index, item in enumerate(markets)]
    result = scan_markets(markets, snapshots, AS_OF, 60)
    assert all(not row["price_rankable"] for row in result["candidates"])
    assert result["groups"][0]["best_spread_candidate"] is None


def dex_calculation_request(strategy="dex_pool_spread", reported_missing=False):
    base_identity = "base:0x" + "11" * 20
    instruments = [Instrument(id=f"review:{venue}", venue=venue, native_symbol="SMALL/USDT",
                              asset_id=base_identity, native_asset_id=base_identity,
                              base="SMALL", quote="USDT", kind="pool", mechanism="amm", chain="base",
                              mapping_verified=True) for venue in ("uniswap", "pancakeswap")]
    snapshots = [MarketSnapshot(instrument_id=item.id, venue=item.venue, source="synthetic:pool",
                                source_at=AS_OF, received_at=AS_OF, valid=True,
                                issues=["quote_token_contract_identity_unavailable"] if reported_missing else [])
                 for item in instruments]
    routes = [{"instrument_id": instruments[0].id, "side": "buy", "input_asset": "USDT", "output_asset": "SMALL",
               "input_amount": 100, "output_amount": 1, "route": ["synthetic:pool-a"], "block_number": 123,
               "source": "synthetic:route", "source_at": AS_OF, "received_at": AS_OF,
               "gas_amount": 0, "gas_currency": "USDT"},
              {"instrument_id": instruments[1].id, "side": "sell", "input_asset": "SMALL", "output_asset": "USDT",
               "input_amount": 1, "output_amount": 105, "route": ["synthetic:pool-b"], "block_number": 123,
               "source": "synthetic:route", "source_at": AS_OF, "received_at": AS_OF,
               "gas_amount": 0, "gas_currency": "USDT"}]
    books = []
    if strategy == "cex_dex_spot":
        instruments[0].chain, instruments[0].kind, instruments[0].mechanism = None, "spot", "orderbook"
        instruments[0].id, instruments[0].venue = "review:okx", "okx"
        snapshots[0].instrument_id, snapshots[0].venue = instruments[0].id, instruments[0].venue
        snapshots[0].issues = []
        instruments[0].native_asset_id = "cex:reviewed-small"
        routes = routes[1:]
        books = [{"instrument_id": instruments[0].id, "bids": [[99, 10]], "asks": [[100, 10]],
                  "source": "synthetic:book", "source_at": AS_OF, "received_at": AS_OF}]
    return CalculateRequest(instruments=instruments, snapshots=snapshots, route_quotes=routes,
                               orderbooks=books, buy_instrument_id=instruments[0].id,
                               sell_instrument_id=instruments[1].id, size_base=1, strategy=strategy,
                               valuation_currency="USDT", as_of=AS_OF,
                               costs=[{"instrument_id": item.id, "open_fee_rate": 0, "close_fee_rate": 0}
                                      for item in instruments])


@pytest.mark.parametrize("strategy", ["dex_pool_spread", "cex_dex_spot"])
@pytest.mark.parametrize("reported_missing", [True, False])
def test_same_quote_symbol_without_dex_token_identity_never_establishes_net_profit(strategy, reported_missing):
    result = calculate(dex_calculation_request(strategy, reported_missing))["opportunities"][0]
    assert result["net_profit"] is None, (
        "Identical USDT symbols and exact-size routes cannot prove quote tokens have identical value")
    assert result["status"] != "threshold_met"
    assert any("quote" in reason.lower() for reason in result["missing_prerequisites"] + result["invalid_reasons"])


def confirmed_pool_request():
    request = dex_calculation_request()
    quote_id = "base:0x" + "22" * 20
    for item in request.instruments:
        item.quote_asset_id = item.native_quote_asset_id = quote_id
        item.quote_identity_source = "https://synthetic.invalid/public-token-proof"
        item.quote_identity_at = AS_OF
    for route in request.route_quotes:
        route.quote_asset_id = quote_id
    return request


def test_confirmed_same_quote_token_allows_size_specific_pool_profit():
    request = confirmed_pool_request()
    result = calculate(request)["opportunities"][0]
    assert result["net_profit"] == pytest.approx(5)
    assert result["status"] == "threshold_met"


@pytest.mark.parametrize("change", ["quote_mismatch", "route_mismatch", "stale", "future", "untraceable"])
def test_quote_proof_must_match_route_and_remain_current_and_traceable(change):
    request = confirmed_pool_request()
    quote_b = "base:0x" + "33" * 20
    if change == "quote_mismatch":
        request.instruments[1].quote_asset_id = request.instruments[1].native_quote_asset_id = quote_b
        request.route_quotes[1].quote_asset_id = quote_b
    elif change == "route_mismatch":
        request.route_quotes[1].quote_asset_id = quote_b
    elif change == "stale":
        request.instruments[1].quote_identity_at = AS_OF - 61000
    elif change == "future":
        request.instruments[1].quote_identity_at = AS_OF + 1
    else:
        request.instruments[1].quote_identity_source = "verified"
    result = calculate(request)["opportunities"][0]
    assert result["net_profit"] is None
    assert result["status"] != "threshold_met"


@pytest.mark.parametrize("binding", ["symbols_only", "wrong_token", "exact_assets"])
def test_cex_dex_same_symbol_conversion_requires_both_actual_quote_asset_endpoints(binding):
    request = dex_calculation_request("cex_dex_spot")
    quote_id = "base:0x" + "22" * 20
    dex = request.instruments[1]
    dex.quote_asset_id = dex.native_quote_asset_id = quote_id
    dex.quote_identity_source = "https://synthetic.invalid/public-token-proof"
    dex.quote_identity_at = AS_OF
    request.route_quotes[0].quote_asset_id = quote_id
    request.fx_rates = [FXRate(base="USDT", quote="USDT", rate=.98,
                              source="https://synthetic.invalid/asset-specific-conversion", at=AS_OF,
                              base_asset_id=quote_id if binding == "exact_assets" else
                              "base:0x" + "33" * 20 if binding == "wrong_token" else None,
                              quote_asset_id="cex:okx:USDT" if binding != "symbols_only" else None)]
    result = calculate(request)["opportunities"][0]
    if binding == "exact_assets":
        assert result["net_profit"] == pytest.approx(2.9)
    else:
        assert result["net_profit"] is None
