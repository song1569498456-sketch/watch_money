"""Financial boundary checks using explicitly timestamped synthetic inputs."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from watch_money.models import Instrument, OrderBook, Settings
from watch_money.strategies import CalculateRequest, _fill_book, calculate, router

NOW = 1_800_000_000_000
HOUR = 3_600_000
QUOTE_ASSET = "base:0x" + "22" * 20
OTHER_QUOTE_ASSET = "base:0x" + "33" * 20


def confirm_quote_assets(data):
    """Synthetic token evidence, not live RPC/API certification."""
    identities = {}
    for item in data["instruments"]:
        if item.get("chain"):
            identities[item["id"]] = QUOTE_ASSET
            item.update(quote_asset_id=QUOTE_ASSET, native_quote_asset_id=QUOTE_ASSET,
                        quote_identity_source="https://synthetic.example/pool-token-metadata", quote_identity_at=NOW)
    for route in data.get("route_quotes", []):
        route["quote_asset_id"] = identities[route["instrument_id"]]


def confirmed_dex_pair():
    data = request_data(strategy="dex_pool_spread")
    for item in data["instruments"]:
        item.update(kind="pool", mechanism="amm", chain="base")
    data["route_quotes"] = [
        {"instrument_id": identity, "side": side, "input_asset": input_asset, "output_asset": output_asset,
         "input_amount": input_amount, "output_amount": output_amount, "route": [f"pool:{identity}"],
         "block_number": 123, "source": "synthetic manual route", "source_at": NOW, "received_at": NOW,
         "gas_amount": .1, "gas_currency": "USDT"}
        for identity, side, input_asset, output_asset, input_amount, output_amount in (
            ("a", "buy", "USDT", "BTC", 100, 1), ("b", "sell", "BTC", "USDT", 1, 105))
    ]
    confirm_quote_assets(data)
    return data


def request_data(kind="spot", strategy="spot_spread"):
    return {
        "instruments": [
            {"id": "a", "venue": "a", "native_symbol": "BTC/USDT", "asset_id": "bitcoin",
             "base": "BTC", "quote": "USDT", "kind": kind, "settle": "USDT", "mapping_verified": True},
            {"id": "b", "venue": "b", "native_symbol": "BTC/USDT", "asset_id": "bitcoin",
             "base": "BTC", "quote": "USDT", "kind": kind, "settle": "USDT", "mapping_verified": True},
        ],
        "snapshots": [
            {"instrument_id": "a", "venue": "a", "source": "test", "source_at": NOW,
             "received_at": NOW, "funding_rate": 0.001, "funding_kind": "current",
             "funding_interval_hours": 8, "next_funding_at": NOW + HOUR, "mark": 100},
            {"instrument_id": "b", "venue": "b", "source": "test", "source_at": NOW,
             "received_at": NOW, "funding_rate": 0.002, "funding_kind": "predicted",
             "funding_interval_hours": 4, "next_funding_at": NOW + HOUR, "mark": 105},
        ],
        "orderbooks": [
            {"instrument_id": "a", "bids": [[99, 10]], "asks": [[100, 1], [102, 9]],
             "source": "test:depth", "source_at": NOW, "received_at": NOW},
            {"instrument_id": "b", "bids": [[105, 1], [103, 9]], "asks": [[106, 10]],
             "source": "test:depth", "source_at": NOW, "received_at": NOW},
        ],
        "buy_instrument_id": "a", "sell_instrument_id": "b", "size_base": 1,
        "strategy": strategy, "valuation_currency": "USDT", "as_of": NOW,
        "costs": [
            {"instrument_id": "a", "open_fee_rate": 0.001, "close_fee_rate": 0.002},
            {"instrument_id": "b", "open_fee_rate": 0.001, "close_fee_rate": 0.002},
        ],
    }


def result(data):
    return calculate(CalculateRequest.model_validate(data))["opportunities"][0]


def test_depth_changes_executable_price_and_cost_without_last_trade():
    data = request_data()
    small = result(data)
    assert small["gross_profit"] == 5
    assert small["known_costs"] == pytest.approx(0.205)
    assert small["net_profit"] == pytest.approx(4.795)
    assert small["legs"][0]["capacity_base"] == 10
    data["size_base"] = 2
    larger = result(data)
    assert larger["legs"][0]["average_price"] == 101
    assert larger["legs"][1]["average_price"] == 104
    assert larger["gross_profit"] == 6
    data["size_base"] = 11
    partial = result(data)
    assert partial["capacity_base"] == 10
    assert partial["net_profit"] is None
    assert partial["gross_profit"] is None
    assert any("insufficient depth" in text for text in partial["missing_prerequisites"])
    data["size_base"] = 1e-15
    data["orderbooks"][0]["asks"] = []
    assert result(data)["gross_profit"] is None


def test_ticker_is_not_executable_and_missing_fee_not_zero():
    data = request_data()
    data["orderbooks"] = []
    for snap in data["snapshots"]:
        snap.update({"bid": 110, "ask": 111, "last": 110})
    assert result(data)["gross_profit"] is None
    data = request_data()
    data["costs"] = data["costs"][:1]
    out = result(data)
    assert out["gross_profit"] == 5
    assert out["known_costs"] == pytest.approx(0.1)
    assert out["net_profit"] is None
    assert out["status"] == "cost_pending"


def test_roundtrip_fees_and_actual_settlement_intervals_signs():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    out = result(data)
    # One long debit 100*.001, two short credits 105*.002, no 8h-normalized shortcut.
    assert out["funding_profit"] == pytest.approx(-0.1 + 0.42)
    assert out["known_costs"] == pytest.approx(0.615)  # open .205 + close .410
    assert out["net_profit"] == pytest.approx(0.32 - 0.615)
    assert len(out["funding_events"]) == 3
    assert {e["position"] for e in out["funding_events"]} == {"long", "short"}
    assert {s["hold_hours"] for s in out["scenarios"]} == {8, 24, 72}
    rows = {s["scenario"]: s for s in out["scenarios"] if s["hold_hours"] == 8}
    assert rows["half"]["funding_profit"] == pytest.approx(0.16)
    assert rows["zero"]["net_profit"] == pytest.approx(-0.615)
    assert rows["reverse"]["funding_profit"] == pytest.approx(-0.32)
    # Negative funding credits the long and debits the short.
    data["snapshots"][0]["funding_rate"] = -0.001
    data["snapshots"][1]["funding_rate"] = -0.002
    assert result(data)["funding_profit"] == pytest.approx(-0.32)


def test_unknown_closing_cost_exit_and_funding_schedule_block_net():
    data = request_data("perpetual", "funding_basis")
    out = result(data)
    assert out["net_profit"] is None
    assert out["basis_profit"] is None
    data["assume_exit_at_entry"] = True
    data["costs"][0]["close_fee_rate"] = None
    out = result(data)
    assert out["net_profit"] is None
    assert out["known_costs"] == pytest.approx(0.415)
    data["costs"][0]["close_fee_rate"] = 0.002
    data["snapshots"][0]["funding_interval_hours"] = None
    out = result(data)
    assert out["funding_profit"] is None
    assert out["net_profit"] is None
    data["snapshots"][0]["funding_kind"] = "settled"
    assert result(data)["funding_profit"] is None


def test_explicit_irregular_funding_events_and_zero_events_complete_horizon():
    data = request_data("perpetual", "funding_basis")
    data.update({"assume_exit_at_entry": True, "hold_hours": 3.5,
                 "funding_schedule_complete_until": {"a": NOW + 72 * HOUR, "b": NOW + 72 * HOUR},
                 "funding_events": [
                     {"instrument_id": "a", "settlement_at": NOW + HOUR, "rate": -0.01,
                      "kind": "predicted", "known_at": NOW, "source": "schedule"},
                     {"instrument_id": "b", "settlement_at": NOW + 3 * HOUR, "rate": 0.02,
                      "kind": "current", "known_at": NOW, "source": "schedule"},
                     {"instrument_id": "b", "settlement_at": NOW + 4 * HOUR, "rate": -0.5,
                      "kind": "predicted", "known_at": NOW, "source": "schedule"},
                 ]})
    out = result(data)
    assert out["funding_profit"] == pytest.approx(3.1)
    assert [e["settlement_at"] for e in out["funding_events"]] == [NOW + HOUR, NOW + 3 * HOUR]
    assert out["net_profit"] == pytest.approx(3.1 - 0.615)
    assert 3.5 in {s["hold_hours"] for s in out["scenarios"]}


@pytest.mark.parametrize("complete_schedule", [False, True])
@pytest.mark.parametrize("age_ms", [60_000, 60_001, HOUR])
def test_explicit_funding_estimates_share_freshness_boundary(complete_schedule, age_ms):
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    if complete_schedule:
        data["funding_schedule_complete_until"] = {"b": NOW + 72 * HOUR}
    data["funding_events"] = [
        {"instrument_id": "b", "settlement_at": NOW + HOUR, "rate": 0.5,
         "kind": "predicted", "known_at": NOW - age_ms, "source": "forecast"},
    ]
    out = result(data)
    if age_ms <= 60_000:
        assert out["net_profit"] is not None
        assert any(event["rate"] == 0.5 for event in out["funding_events"])
    else:
        assert out["funding_profit"] is None
        assert out["net_profit"] is None
        assert any("stale" in reason for reason in out["missing_prerequisites"])
        assert all(row["net_profit"] is None for row in out["scenarios"])


@pytest.mark.parametrize("complete_schedule", [False, True])
def test_explicit_funding_uses_latest_known_estimate_before_freshness_check(complete_schedule):
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    if complete_schedule:
        data["funding_schedule_complete_until"] = {"b": NOW + 72 * HOUR}
    # Deliberately unsorted: an obsolete forecast and a future revision must
    # neither override nor invalidate the fresh revision available now.
    data["funding_events"] = [
        {"instrument_id": "b", "settlement_at": NOW + HOUR, "rate": rate,
         "kind": "predicted", "known_at": known_at, "source": source}
        for rate, known_at, source in [(0.003, NOW, "latest known"),
                                      (0.5, NOW - HOUR, "obsolete"),
                                      (0.9, NOW + 1, "future revision")]
    ]
    out = result(data)
    assert out["net_profit"] is not None
    selected = [event for event in out["funding_events"] if event["instrument_id"] == "b"
                and event["settlement_at"] == NOW + HOUR]
    assert len(selected) == 1
    assert selected[0]["rate"] == 0.003
    assert selected[0]["known_at"] == NOW
    assert selected[0]["source"] == "latest known"


def test_linear_contract_multiplier_and_inverse_depth_conversion():
    inst = Instrument(id="a", venue="a", native_symbol="X", asset_id="btc", base="BTC", quote="USD",
                      kind="perpetual", contract_size=0.01, settle="USDT")
    book = OrderBook(instrument_id="a", asks=[(100, 50), (102, 50)], bids=[(99, 100)],
                     quantity_unit="contracts", source="test", received_at=NOW)
    fill = _fill_book(inst, book, "buy", 0.75)
    assert fill["capacity_base"] == 1
    assert fill["contracts"] == 75
    assert fill["quote_amount"] == pytest.approx(75.5)
    inverse = inst.model_copy(update={"contract_size": 100, "linear": False, "settle": "BTC"})
    inverse_book = book.model_copy(update={"asks": [(10_000, 100)], "bids": [(9999, 100)]})
    fill = _fill_book(inverse, inverse_book, "buy", 0.5)
    assert fill["contracts"] == 50
    assert fill["capacity_base"] == 1
    assert fill["quote_amount"] == 5000
    with pytest.raises(ValueError, match="base settlement"):
        _fill_book(inverse.model_copy(update={"settle": "USD"}), inverse_book, "buy", 0.5)


@pytest.mark.parametrize("quantity_unit", ["base", "contracts"])
@pytest.mark.parametrize("size,contract_size,step,executable", [
    (0.25, 1, 1, False), (0.25, 0.01, 1, True),
    (0.25, 0.01, 2, False), (0.24, 0.01, 2, True), (0.3, 0.1, 1, True),
])
def test_contract_amount_step_applies_after_base_conversion(quantity_unit, size, contract_size, step, executable):
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8, "size_base": size})
    for instrument in data["instruments"]:
        instrument.update({"contract_size": contract_size, "amount_precision": step})
    for book in data["orderbooks"]:
        book["quantity_unit"] = quantity_unit
        book["asks"] = [[100, 100]]
        book["bids"] = [[99, 100]]
    out = result(data)
    if executable:
        assert out["net_profit"] is not None
        assert all(leg["contracts"] == pytest.approx(size / contract_size) for leg in out["legs"])
    else:
        assert out["net_profit"] is None
        assert out["status"] == "invalidated"
        assert any("amount precision step" in reason for reason in out["invalid_reasons"])


def test_spot_amount_step_and_inverse_contract_step_do_not_round_requested_size():
    data = request_data()
    data["instruments"][0]["amount_precision"] = 0.1
    data["size_base"] = 0.25
    assert result(data)["net_profit"] is None
    data["size_base"] = 0.3
    assert result(data)["net_profit"] is not None
    inverse = Instrument(id="a", venue="a", native_symbol="X", asset_id="btc", base="BTC", quote="USD",
                         kind="perpetual", contract_size=100, linear=False, settle="BTC", amount_precision=1)
    for quantity_unit in ("base", "contracts"):
        book = OrderBook(instrument_id="a", asks=[(10_000, 100)], bids=[(9999, 100)],
                         quantity_unit=quantity_unit, source="test", received_at=NOW)
        assert _fill_book(inverse, book, "buy", 0.5)["contracts"] == 50
        with pytest.raises(ValueError, match="amount precision step"):
            _fill_book(inverse, book, "buy", 0.505)


def test_stale_source_not_hidden_by_recent_receive_and_invalid_book():
    data = request_data()
    data["orderbooks"][0]["source_at"] = NOW - 120_000
    out = result(data)
    assert out["status"] == "invalidated"
    assert out["stale"] is True
    assert out["net_profit"] is None
    data = request_data()
    data["orderbooks"][0]["asks"] = [[0, 1]]
    with pytest.raises(ValueError, match="positive"):
        result(data)
    data["orderbooks"][0]["asks"] = [[98, 1]]
    with pytest.raises(ValueError, match="Crossed"):
        result(data)


def test_no_stablecoin_peg_assumption_and_fx_provenance():
    data = request_data()
    data["valuation_currency"] = "USD"
    assert result(data)["net_profit"] is None
    data["fx_rates"] = [{"base": "USDT", "quote": "USD", "rate": 0.99,
                          "source": "explicit:USDTUSD", "at": NOW - 1000}]
    out = result(data)
    assert out["gross_profit"] == pytest.approx(4.95)
    assert out["net_profit"] == pytest.approx(4.795 * 0.99)
    assert out["fx_rates"][0]["source"] == "explicit:USDTUSD"
    assert out["fx_rates"][0]["at"] == NOW - 1000
    data["fx_rates"][0]["at"] = NOW - 3_600_001
    assert result(data)["net_profit"] is None


def test_spot_perpetual_basis_exit_and_short_spot_borrowing():
    data = request_data("perpetual", "spot_perp_basis")
    data["instruments"][0]["kind"] = "spot"
    data["exit_prices"] = {"a": 102, "b": 103}
    out = result(data)
    assert out["basis_profit"] == 4  # +2 spot, +2 short perp
    assert out["net_profit"] is not None
    data["instruments"][0]["kind"] = "perpetual"
    data["instruments"][1]["kind"] = "spot"
    assert result(data)["net_profit"] is None
    data["costs"][1]["holding_rate_per_hour"] = 0.001
    out = result(data)
    assert out["net_profit"] is not None
    assert out["known_costs"] == pytest.approx(0.205 + 0.41 + 105 * 0.001 * 24)


def test_both_perpetual_holding_charges_scale_with_duration_and_use_each_quote_fx_once():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    data["instruments"][1].update({"quote": "USD", "settle": "USD"})
    data["fx_rates"] = [{"base": "USD", "quote": "USDT", "rate": 0.5, "source": "test:FX", "at": NOW}]
    for index, cost in enumerate(data["costs"]):
        cost.update({"holding_rate_per_hour": (index + 1) * 0.001, "source": f"account:{index}",
                     "at": NOW - 1000, "conditions": "explicit account assumption"})
    out = result(data)
    hourly = 100 * 0.001 + 105 * 0.5 * 0.002
    assert out["holding_cost"] == pytest.approx(hourly * 8)
    assert out["known_costs"] == pytest.approx((100 + 105 * 0.5) * 0.003 + hourly * 8)
    assert out["net_profit"] == pytest.approx(out["gross_profit"] - out["known_costs"])
    assert sum(cost["valuation_amount"] for cost in out["costs"]) == pytest.approx(out["known_costs"])
    assert len(out["holding_costs"]) == 2 and out["borrowing_cost"] == 0
    assert out["holding_costs"][1]["amount"] == pytest.approx(105 * 0.002 * 8)
    assert out["holding_costs"][1]["currency"] == "USD"
    assert out["holding_costs"][1]["valuation_amount"] == pytest.approx(105 * 0.5 * 0.002 * 8)
    assert out["holding_costs"][1]["source"] == "account:1"
    assert out["holding_costs"][1]["at"] == NOW - 1000
    for scenario in out["scenarios"]:
        assert scenario["holding_cost"] == pytest.approx(hourly * scenario["hold_hours"])
        assert scenario["net_profit"] == pytest.approx(scenario["gross_profit"] - scenario["known_costs"])


def test_long_spot_explicit_holding_charge_is_applied_and_short_spot_borrowing_not_double_counted():
    data = request_data("perpetual", "spot_perp_basis")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    data["instruments"][0]["kind"] = "spot"
    data["costs"][0]["holding_rate_per_hour"] = 0.001
    out = result(data)
    assert out["net_profit"] is not None and out["holding_cost"] == pytest.approx(0.8)
    assert out["borrowing_cost"] == 0
    assert any("user assumption without an observation timestamp" in text for text in out["assumptions"])
    data["instruments"][0]["kind"] = "perpetual"
    data["instruments"][1]["kind"] = "spot"
    data["costs"][1]["holding_rate_per_hour"] = 0.001
    out = result(data)
    assert out["holding_cost"] == pytest.approx((100 + 105) * 0.001 * 8)
    assert out["borrowing_cost"] == pytest.approx(105 * 0.001 * 8)
    assert out["known_costs"] == pytest.approx(0.615 + out["holding_cost"])
    assert sum(cost["valuation_amount"] for cost in out["costs"]) == pytest.approx(out["known_costs"])
    data["costs"][1]["holding_rate_per_hour"] = None
    assert result(data)["holding_cost"] is None and result(data)["net_profit"] is None


def test_optional_holding_default_future_configuration_and_instant_strategy_applicability():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    assert result(data)["holding_cost"] == 0 and result(data)["net_profit"] is not None
    data["costs"][0].update({"holding_rate_per_hour": 0.001, "at": NOW + 1})
    out = result(data)
    assert out["holding_cost"] is None and out["net_profit"] is None
    assert any("holding cost configuration was not yet known" in reason for reason in out["missing_prerequisites"])
    assert out["holding_costs"][0]["at"] == NOW + 1 and out["holding_costs"][0]["amount"] is None
    data = request_data()
    data["costs"][0]["holding_rate_per_hour"] = 0.001
    out = result(data)
    assert out["net_profit"] is None
    assert any("requires a held strategy" in reason for reason in out["missing_prerequisites"])
    data["costs"][0]["holding_rate_per_hour"] = 0
    assert result(data)["net_profit"] == pytest.approx(4.795)


def test_breakeven_includes_continuously_accruing_perpetual_holding_cost():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 72})
    data["snapshots"][1]["funding_interval_hours"] = 8
    assert result(data)["breakeven_hours"] == 41
    data["costs"][0]["holding_rate_per_hour"] = 0.00002
    out = result(data)
    assert out["breakeven_hours"] == 49
    assert 6 * 0.11 - 0.615 - 100 * 0.00002 * 41 < 0
    assert 7 * 0.11 - 0.615 - 100 * 0.00002 * 49 > 0


def test_zero_entry_cost_does_not_imply_breakeven_for_an_always_losing_holding_path():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    for cost in data["costs"]:
        cost.update({"open_fee_rate": 0, "close_fee_rate": 0, "holding_rate_per_hour": 0.001})
    for snapshot in data["snapshots"]:
        snapshot.update({"mark": 100, "funding_rate": 0.001, "funding_interval_hours": 8})
    out = result(data)
    assert out["funding_profit"] == 0 and out["net_profit"] < 0
    assert out["breakeven_hours"] is None
    for cost in data["costs"]:
        cost["holding_rate_per_hour"] = 0
    assert result(data)["net_profit"] == 0 and result(data)["breakeven_hours"] is None


def test_immediate_positive_rebate_can_breakeven_at_zero_but_later_carrying_loss_is_explicit():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    for cost in data["costs"]:
        cost.update({"open_fee_rate": -0.001, "close_fee_rate": 0, "holding_rate_per_hour": 0.001})
    for snapshot in data["snapshots"]:
        snapshot.update({"mark": 100, "funding_rate": 0.001, "funding_interval_hours": 8})
    out = result(data)
    assert out["net_profit"] < 0 and out["breakeven_hours"] == 0
    assert any("balance negative again" in text for text in out["assumptions"])


@pytest.mark.parametrize("kind,quantity_unit,contract_size,minimum,size,executable", [
    ("spot", "base", 1, 0.5, 0.25, False), ("spot", "base", 1, 0.5, 0.5, True),
    ("perpetual", "contracts", 0.01, 10, 0.05, False),
    ("perpetual", "contracts", 0.01, 10, 0.1, True),
    ("perpetual", "base", 0.01, 10, 0.05, False),
    ("perpetual", "base", 0.01, 10, 0.1, True),
])
def test_minimum_order_amount_uses_exchange_contract_or_spot_base_units(kind, quantity_unit, contract_size,
                                                                      minimum, size, executable):
    data = request_data(kind, "funding_spread" if kind == "perpetual" else "spot_spread")
    data.update({"assume_exit_at_entry": True, "size_base": size})
    for item in data["instruments"]:
        item.update({"contract_size": contract_size, "min_amount": minimum})
    for book in data["orderbooks"]:
        book["quantity_unit"] = quantity_unit
        book["asks"] = [[100, 100]]
        book["bids"] = [[99, 100]]
    out = result(data)
    if executable:
        assert out["net_profit"] is not None
    else:
        assert out["status"] == "invalidated" and out["net_profit"] is None
        assert any("minimum order amount" in reason for reason in out["invalid_reasons"])


def test_inverse_minimum_order_amount_uses_contracts_at_execution_price():
    inst = Instrument(id="a", venue="a", native_symbol="X", asset_id="BTC", base="BTC", quote="USD",
                      settle="BTC", kind="perpetual", contract_size=100, linear=False, min_amount=50)
    for unit in ("base", "contracts"):
        book = OrderBook(instrument_id="a", asks=[(10000, 100)], bids=[(9999, 100)], quantity_unit=unit,
                         received_at=NOW, source="test")
        with pytest.raises(ValueError, match="minimum order amount"):
            _fill_book(inst, book, "buy", 0.49)
        assert _fill_book(inst, book, "buy", 0.5)["contracts"] == 50


@pytest.mark.parametrize("venue,kind,linear,contract_size,native_min,expected_min", [
    ("okx", "spot", True, 1, 0.01, 0.01),
    ("okx", "perpetual", True, 0.01, 10, 10),
    ("deribit", "perpetual", True, 0.001, 0.001, 1),
    ("deribit", "perpetual", False, 10, 10, 1),
])
@pytest.mark.asyncio
async def test_cex_discovery_preserves_minimum_order_units(venue, kind, linear, contract_size,
                                                        native_min, expected_min):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from watch_money.adapters.cex import CexAdapter
    symbol = "BTC/USD:USD"
    market = {"id": "BTCUSD", "symbol": symbol, "base": "BTC", "baseId": "BTC", "quote": "USD",
              "settle": "USD" if linear else "BTC", "active": True, "spot": kind == "spot",
              "swap": kind == "perpetual", "linear": linear, "inverse": not linear, "contractSize": contract_size,
              "precision": {"amount": native_min}, "limits": {"amount": {"min": native_min}}}
    client = SimpleNamespace(precisionMode=4, load_markets=AsyncMock(return_value={symbol: market}))
    adapter = CexAdapter(venue, client_factory=lambda _: client)
    [item] = await adapter.discover()
    assert item.min_amount == expected_min
    assert item.amount_precision == expected_min


def test_dex_exact_size_route_gas_and_impact_not_double_counted():
    data = request_data(strategy="cex_dex_spot")
    data["instruments"][1].update({"kind": "pool", "mechanism": "amm", "chain": "base"})
    data["route_quotes"] = [{"instrument_id": "b", "side": "sell", "input_asset": "BTC",
                              "output_asset": "USDT", "input_amount": 1, "output_amount": 108,
                              "route": ["pool:0x123"], "block_number": 123, "source": "test:route",
                              "source_at": NOW, "received_at": NOW,
                              "gas_amount": 0.25, "gas_currency": "USDT"}]
    confirm_quote_assets(data)
    data["fx_rates"] = [{"base": "USDT", "quote": "USDT", "rate": 1,
                         "base_asset_id": QUOTE_ASSET, "quote_asset_id": "cex:a:USDT",
                         "source": "https://synthetic.example/scoped-conversion", "at": NOW}]
    out = result(data)
    assert out["gross_profit"] == 8
    assert out["known_costs"] == pytest.approx(0.35)  # CEX .1 + gas .25, route fee embedded
    assert out["net_profit"] == pytest.approx(7.65)
    assert out["legs"][1]["route"] == ["pool:0x123"]
    data["route_quotes"][0]["gas_amount"] = None
    assert result(data)["net_profit"] is None
    data["route_quotes"][0]["input_amount"] = 2
    assert result(data)["status"] == "invalidated"


def test_asset_mapping_and_strategy_validation():
    data = request_data()
    data["instruments"][1]["asset_id"] = "unrelated-btc"
    assert result(data)["status"] == "invalidated"
    data = request_data()
    data["instruments"][1]["mapping_verified"] = False
    assert result(data)["status"] == "invalidated"
    data = request_data(strategy="funding_spread")
    assert result(data)["status"] == "invalidated"


@pytest.mark.parametrize("second_chain,second_address,reason", [
    ("base", "0x" + "b2" * 20, "different_native_chain_addresses"),
    ("arbitrum", "0x" + "a1" * 20, "different_chains"),
])
def test_calculator_rejects_forced_mapping_over_native_token_conflicts(second_chain, second_address, reason):
    data = request_data()
    for instrument, chain, address in zip(data["instruments"], ["base", second_chain],
                                           ["0x" + "a1" * 20, second_address], strict=True):
        instrument.update({"chain": chain, "native_asset_id": f"{chain}:{address}",
                           "asset_id": "reviewed:shared-token", "mapping_verified": True})
    out = result(data)
    assert out["status"] == "invalidated"
    assert out["net_profit"] is None and out["gross_profit"] is None
    assert any(reason in text for text in out["invalid_reasons"])


@pytest.mark.parametrize("bases", [("PEPE", "1000PEPE"), ("ETH", "WETH"), ("USDC", "USDC.e")])
def test_calculator_rejects_forced_mapping_over_incomparable_base_units(bases):
    data = request_data()
    for instrument, base in zip(data["instruments"], bases, strict=True):
        instrument.update({"base": base, "asset_id": "reviewed:shared-token", "mapping_verified": True})
    # Contract quantity conversion is independent of a base token unit ratio.
    data["instruments"][1]["contract_size"] = 1000
    out = result(data)
    assert out["status"] == "invalidated"
    assert out["net_profit"] is None
    assert any("different_base_symbols_require_unit_review" in reason for reason in out["invalid_reasons"])


def test_calculator_keeps_reviewed_bitcoin_alias_and_explicit_quote_contract_conversions():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    data["instruments"][1].update({"base": "XBT", "quote": "USD", "settle": "USD", "contract_size": 0.01})
    data["orderbooks"][1]["quantity_unit"] = "contracts"
    data["orderbooks"][1]["bids"] = [[105, 100]]
    data["orderbooks"][1]["asks"] = [[106, 100]]
    data["fx_rates"] = [{"base": "USD", "quote": "USDT", "rate": 0.99,
                         "source": "reviewed:USD/USDT", "at": NOW}]
    out = result(data)
    assert out["net_profit"] is not None
    assert out["legs"][1]["contracts"] == 100
    assert out["legs"][1]["valuation_amount"] == pytest.approx(105 * 0.99)


def test_saved_sample_is_repeatable_and_replay_excludes_delayed_data(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCH_MONEY_DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        data = request_data()
        original = client.post("/api/opportunities/calculate", json=data)
        assert original.status_code == 200
        saved = client.post("/api/opportunities/saved", json={"name": "deterministic", "calculation": data}).json()
        assert saved["result"] == original.json()
        assert len(client.get("/api/opportunities/saved").json()["samples"]) == 1
        replay = client.post("/api/opportunities/replay", json={"sample_id": saved["id"], "as_of": NOW}).json()
        assert replay["opportunities"] == original.json()["opportunities"]
        delayed = client.post("/api/opportunities/replay", json={"sample_id": saved["id"], "as_of": NOW,
                                                                "operation_delay_ms": 1}).json()
        assert delayed["opportunities"][0]["net_profit"] is None
        assert delayed["replay"]["information_cutoff"] == NOW - 1
        assert client.post("/api/opportunities/replay", json={"sample_id": "missing", "as_of": NOW}).status_code == 404


def test_mark_notional_is_distinct_from_execution_price_and_requires_fresh_provenance():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    data["snapshots"][0]["mark"] = 150
    assert result(data)["funding_profit"] == pytest.approx(-0.15 + 0.42)
    data["snapshots"][0]["mark"] = None
    assert result(data)["funding_profit"] is None
    data["funding_notional_basis"] = "entry_vwap"
    assert result(data)["funding_profit"] == pytest.approx(-0.1 + 0.42)
    data["funding_notional_basis"] = "mark"
    data["snapshots"][0]["mark"] = 100
    data["snapshots"][0]["field_timestamps"] = {"mark": NOW - 120_000}
    assert result(data)["net_profit"] is None


def test_breakeven_groups_simultaneous_long_debits_and_short_credits():
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 72})
    data["snapshots"][1]["funding_interval_hours"] = 8
    out = result(data)
    # Each simultaneous settlement nets +.11, six settlements cover .615 roundtrip fees.
    assert out["breakeven_hours"] == 41
    data["assume_exit_at_entry"] = False
    data["exit_prices"] = {"a": 101, "b": 103}
    assert result(data)["breakeven_hours"] is None


def test_batch_sizes_and_transfer_unknown_cost():
    data = request_data()
    data["sizes_base"] = [1, 2, 11]
    assert [o["gross_profit"] for o in calculate(CalculateRequest.model_validate(data))["opportunities"]] == [5, 6, None]
    data["sizes_base"] = []
    data["inventory_path"] = "transfer"
    assert result(data)["net_profit"] is None
    data["transfer_cost"] = {"name": "withdrawal", "amount": 1, "currency": "USDT"}
    assert result(data)["net_profit"] == pytest.approx(3.795)


def test_same_chain_routes_require_distinct_liquidity_and_coherent_blocks():
    data = request_data(strategy="dex_pool_spread")
    for instrument in data["instruments"]:
        instrument.update({"kind": "pool", "mechanism": "amm", "chain": "base"})
    data["route_quotes"] = [
        {"instrument_id": "a", "side": "buy", "input_asset": "USDT", "output_asset": "BTC",
         "input_amount": 100, "output_amount": 1, "route": ["pool:a"], "block_number": 123,
         "source": "test", "source_at": NOW, "received_at": NOW, "gas_amount": 0.1, "gas_currency": "USDT"},
        {"instrument_id": "b", "side": "sell", "input_asset": "BTC", "output_asset": "USDT",
         "input_amount": 1, "output_amount": 105, "route": ["pool:b"], "block_number": 123,
         "source": "test", "source_at": NOW, "received_at": NOW, "gas_amount": 0.1, "gas_currency": "USDT"},
    ]
    confirm_quote_assets(data)
    assert result(data)["net_profit"] == pytest.approx(4.8)
    data["route_quotes"][1]["block_number"] = 124
    assert result(data)["net_profit"] is None
    data["route_quotes"][1]["route"] = ["pool:a"]
    assert result(data)["status"] == "invalidated"


def test_manual_dex_same_symbol_without_quote_contract_evidence_cannot_produce_net():
    data = request_data(strategy="dex_pool_spread")
    for item in data["instruments"]:
        item.update({"kind": "pool", "mechanism": "amm", "chain": "base"})
    data["route_quotes"] = [
        {"instrument_id": identity, "side": side, "input_asset": input_asset, "output_asset": output_asset,
         "input_amount": input_amount, "output_amount": output_amount, "route": [f"pool:{identity}"],
         "block_number": 123, "source": "synthetic manual route", "source_at": NOW, "received_at": NOW,
         "gas_amount": .1, "gas_currency": "USDT"}
        for identity, side, input_asset, output_asset, input_amount, output_amount in (
            ("a", "buy", "USDT", "BTC", 100, 1), ("b", "sell", "BTC", "USDT", 1, 105))
    ]
    out = result(data)
    assert out["net_profit"] is None
    assert any("quote" in reason.lower() for reason in out["invalid_reasons"] + out["missing_prerequisites"])


@pytest.mark.parametrize("field,value,reason", [
    ("quote_asset_id", "USDT", "invalid chain/address"),
    ("native_quote_asset_id", OTHER_QUOTE_ASSET, "conflicts with native"),
    ("quote_identity_source", "verified", "traceable source"),
    ("quote_identity_source", "https://[", "traceable source"),
    ("quote_identity_at", NOW + 1, "future"),
    ("quote_identity_at", NOW - 60_001, "stale"),
    ("chain", "arbitrum", "quote token chain"),
])
def test_dex_quote_evidence_conflict_time_and_source_cannot_be_replaced_by_manual_flags(field, value, reason):
    data = confirmed_dex_pair()
    data["instruments"][0][field] = value
    for snapshot in data["snapshots"]:
        snapshot.update(valid=True, is_demo=True, issues=[])
    out = result(data)
    assert out["net_profit"] is None and out["status"] != "threshold_met"
    assert any(reason in text for text in out["invalid_reasons"])


def test_same_symbol_different_quote_contracts_require_fresh_exact_bound_conversion():
    data = confirmed_dex_pair()
    data["instruments"][1].update(quote_asset_id=OTHER_QUOTE_ASSET, native_quote_asset_id=OTHER_QUOTE_ASSET)
    data["route_quotes"][1]["quote_asset_id"] = OTHER_QUOTE_ASSET
    assert result(data)["net_profit"] is None
    conversion = {"base": "USDT", "quote": "USDT", "rate": 0.5,
                  "source": "https://synthetic.example/specific-token-conversion", "at": NOW}
    data["fx_rates"] = [conversion]
    assert result(data)["net_profit"] is None  # A symbol-only conversion is insufficient.
    conversion.update(base_asset_id=OTHER_QUOTE_ASSET, quote_asset_id=QUOTE_ASSET)
    out = result(data)
    assert out["gross_profit"] == pytest.approx(105 * 0.5 - 100)
    assert out["known_costs"] == pytest.approx(.1 + .1 * 0.5)  # Quote-denominated gas follows each token.
    assert out["net_profit"] == pytest.approx(105 * 0.5 - 100 - .15)
    assert any(row.get("from_asset_id") == OTHER_QUOTE_ASSET for row in out["fx_rates"])
    conversion["at"] = NOW - 3_600_001
    assert result(data)["net_profit"] is None
    conversion["at"] = NOW + 1
    assert result(data)["net_profit"] is None
    conversion["at"], conversion["source"] = NOW, "user guessed parity"
    assert result(data)["net_profit"] is None


def test_route_token_labels_cannot_replace_or_conflict_with_confirmed_instrument_evidence():
    data = confirmed_dex_pair()
    data["route_quotes"][1]["quote_asset_id"] = OTHER_QUOTE_ASSET
    assert result(data)["net_profit"] is None
    assert any("Route quote token identity" in text for text in result(data)["invalid_reasons"])
    data["instruments"][1]["quote_asset_id"] = None
    data["instruments"][1]["native_quote_asset_id"] = None
    assert result(data)["net_profit"] is None


def test_quote_address_canonicalization_retains_solana_case_and_allows_same_evm_token():
    from watch_money.quote_identity import canonical_asset_id

    data = confirmed_dex_pair()
    data["instruments"][1]["quote_asset_id"] = "base:0x" + "22" * 20
    assert result(data)["net_profit"] == pytest.approx(4.8)
    assert canonical_asset_id("base:0x" + "AA" * 20) == "base:0x" + "aa" * 20
    mint = "So11111111111111111111111111111111111111112"
    assert canonical_asset_id(f"solana:{mint}") == f"solana:{mint}"
    assert canonical_asset_id(f"solana:{mint.lower()}") != f"solana:{mint}"


def test_third_valuation_token_cannot_be_invented_from_an_fx_symbol():
    data = confirmed_dex_pair()
    data["valuation_currency"] = "USDC"
    data["fx_rates"] = [{"base": "USDT", "quote": "USDC", "rate": 1,
                         "base_asset_id": QUOTE_ASSET, "quote_asset_id": "valuation:USDC",
                         "source": "https://synthetic.example/scoped-conversion", "at": NOW}]
    assert result(data)["net_profit"] is None
    # A fiat unit is explicitly distinct from a chain token; its conversion is
    # still bound to the specific source token and is not a stablecoin peg.
    data["valuation_currency"] = "USD"
    data["fx_rates"][0].update(quote="USD", quote_asset_id="fiat:USD", rate=.99)
    assert result(data)["net_profit"] == pytest.approx(4.8 * .99)


def test_bounded_static_quote_metadata_is_separate_from_dynamic_price_freshness():
    data = confirmed_dex_pair()
    for item in data["instruments"]:
        item.update(quote_identity_at=NOW - HOUR, quote_identity_expires_at=NOW + HOUR)
    assert result(data)["net_profit"] == pytest.approx(4.8)
    data["instruments"][0]["quote_identity_expires_at"] = NOW
    out = result(data)
    assert out["net_profit"] is None
    assert any("metadata review has expired" in reason for reason in out["invalid_reasons"])
    # New executable route/market timestamps cannot renew old identity evidence.
    for snapshot in data["snapshots"]:
        snapshot.update(source_at=NOW, received_at=NOW)
    for route in data["route_quotes"]:
        route.update(source_at=NOW, received_at=NOW)
    assert result(data)["net_profit"] is None
    data["instruments"][0]["quote_identity_expires_at"] = NOW + 6 * HOUR
    assert result(data)["net_profit"] is None  # Seven hours from observation exceeds the policy limit.


def test_saved_dex_quote_identity_cannot_cross_replay_information_cutoff(tmp_path):
    data = confirmed_dex_pair()
    for snapshot in data["snapshots"]:
        snapshot.update(source_at=NOW - 1000, received_at=NOW - 1000)
    for route in data["route_quotes"]:
        route.update(source_at=NOW - 1000, received_at=NOW - 1000)
    app = FastAPI()
    app.include_router(router)
    app.state.data_dir = tmp_path
    with TestClient(app) as client:
        saved = client.post("/api/opportunities/saved", json={"name": "quote evidence cutoff", "calculation": data}).json()
        assert saved["result"]["opportunities"][0]["net_profit"] == pytest.approx(4.8)
        replay = client.post("/api/opportunities/replay", json={"sample_id": saved["id"], "as_of": NOW,
                                                                "operation_delay_ms": 1}).json()
        out = replay["opportunities"][0]
        assert out["net_profit"] is None
        assert any("quote token identity observation time" in text for text in out["invalid_reasons"])


def test_held_inverse_and_future_fee_configuration_do_not_invent_net():
    data = request_data("perpetual", "perp_spread")
    data["assume_exit_at_entry"] = True
    data["instruments"][0].update({"linear": False, "settle": "BTC", "contract_size": 100})
    assert result(data)["net_profit"] is None
    assert any("inverse" in text for text in result(data)["missing_prerequisites"])
    data = request_data()
    data["costs"][0]["at"] = NOW + 1
    assert result(data)["net_profit"] is None
    data = request_data("perpetual", "perp_spread")
    data["assume_exit_at_entry"] = True
    data["instruments"][0]["settle"] = "BTC"
    assert result(data)["net_profit"] is None


def test_notification_event_handles_invalid_books_and_has_normalized_funding_metric():
    from watch_money.strategies import opportunity_event
    data = request_data("perpetual", "funding_spread")
    data.update({"assume_exit_at_entry": True, "hold_hours": 8})
    req = CalculateRequest.model_validate(data)
    event = opportunity_event(calculate(req)["opportunities"][0], req)
    assert event["funding_spread"] == pytest.approx(-0.001 + 0.004)
    assert event["funding_spread_unit"] == "fraction_per_8h"
    assert event["asset"] == "BTC"
    req.orderbooks[0].source_at = NOW - 120_000
    event = opportunity_event(calculate(req)["opportunities"][0], req)
    assert event["valid"] is False
    assert event["spread"] is None


def test_computation_disabled_blocks_new_calculations_but_saved_samples_remain_viewable(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.data_dir = tmp_path
    app.state.settings = Settings(computation_enabled=False)
    with TestClient(app) as client:
        data = request_data()
        assert client.post("/api/opportunities/calculate", json=data).status_code == 409
        assert client.post("/api/opportunities/saved", json={"name": "paused", "calculation": data}).status_code == 409
        assert client.post("/api/opportunities/replay", json={"sample_id": "any", "as_of": NOW}).status_code == 409
        assert client.get("/api/opportunities/saved").json() == {"samples": []}
