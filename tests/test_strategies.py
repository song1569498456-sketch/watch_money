"""Financial boundary checks using explicitly timestamped synthetic inputs."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from watch_money.models import Instrument, OrderBook, Settings
from watch_money.strategies import CalculateRequest, _fill_book, calculate, router

NOW = 1_800_000_000_000
HOUR = 3_600_000


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


def test_dex_exact_size_route_gas_and_impact_not_double_counted():
    data = request_data(strategy="cex_dex_spot")
    data["instruments"][1].update({"kind": "pool", "mechanism": "amm", "chain": "base"})
    data["route_quotes"] = [{"instrument_id": "b", "side": "sell", "input_asset": "BTC",
                              "output_asset": "USDT", "input_amount": 1, "output_amount": 108,
                              "route": ["pool:0x123"], "block_number": 123, "source": "test:route",
                              "source_at": NOW, "received_at": NOW,
                              "gas_amount": 0.25, "gas_currency": "USDT"}]
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
    assert result(data)["net_profit"] == pytest.approx(4.8)
    data["route_quotes"][1]["block_number"] = 124
    assert result(data)["net_profit"] is None
    data["route_quotes"][1]["route"] = ["pool:a"]
    assert result(data)["status"] == "invalidated"


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
