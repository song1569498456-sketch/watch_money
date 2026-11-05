"""Synthetic perpetual merge/identity/field-clock integration, not live acceptance."""

from types import SimpleNamespace

import pytest

from watch_money.adapters.cex import CexAdapter, _light_ticker_patch
from watch_money.adapters.gate_futures import gate_futures_book_patch
from watch_money.collector import Collector, enrich
from watch_money.models import Instrument, MarketSnapshot, now_ms
from watch_money.scanner import funding_field_state
from watch_money.storage import Storage


def instrument(settle="USDT", size=0.001, linear=True):
    return Instrument(
        id=f"gate:perp:{settle}",
        venue="gate",
        native_symbol=f"BTC/USDT:{settle}",
        asset_id="BTC",
        native_asset_id="BTC",
        base="BTC",
        quote="USDT",
        kind="perpetual",
        settle=settle,
        contract_size=size,
        linear=linear,
        mapping_verified=True,
    )


def bbo(item, at, *, bid="100", ask="101", bid_size=".5", ask_size=".25", sequence="5"):
    symbol = f"BTC_{item.settle}"
    message = {
        "channel": "futures.book_ticker",
        "event": "update",
        "time_ms": at + 1000,
        "result": {"s": symbol, "t": at, "u": sequence, "b": bid, "B": bid_size, "a": ask, "A": ask_size},
    }
    return gate_futures_book_patch(item, message, at + 1, native_contract=symbol, settle=item.settle.lower())


def put(collector, patch):
    merged = collector.merge_patch(patch)
    collector.snapshots[patch.instrument_id] = merged
    return merged


@pytest.mark.parametrize(
    "settle,size,linear", [("USDT", 0.001, True), ("BTC", 100, False), ("USD1", 10, True)]
)
def test_native_prices_never_scaled_by_contract_multiplier(tmp_path, settle, size, linear):
    item = instrument(settle, size, linear)
    at = now_ms() - 1000
    patch = bbo(item, at)
    assert patch.bid == 100 and patch.ask == 101
    assert patch.source_at == at and patch.field_received_at["bid"] == at + 1
    assert not {"volume", "open_interest", "funding_rate"} & patch.model_fields_set
    assert enrich(put(Collector(Storage(tmp_path), {}), patch), 60)["price_fresh"]


def test_late_untimed_perp_rest_cannot_resurrect_side_or_renew_age(tmp_path, monkeypatch):
    item = instrument()
    at = now_ms() - 1000
    collector = Collector(Storage(tmp_path), {})
    put(collector, bbo(item, at))
    put(collector, bbo(item, at + 1, bid="", bid_size=0, sequence="6"))
    rest = _light_ticker_patch(
        item,
        "gate",
        {"timestamp": None, "bid": 200, "ask": 201, "last": 200.5},
        "fetch_tickers",
        received_at=at + 2,
    )
    result = put(collector, rest)
    assert result.bid is None and result.ask == 101 and result.last == 200.5
    assert result.field_timestamps["bid"] == at + 1
    # A delayed reconnect frame cannot cross persisted clear time, even with a new sequence epoch.
    collector.store.save_snapshots([result])
    collector = Collector(collector.store, {})
    result = put(collector, bbo(item, at, sequence="1"))
    assert result.bid is None
    result = put(collector, bbo(item, at + 1, sequence="2"))
    assert result.bid is None
    result = put(collector, bbo(item, at + 2, sequence="3"))
    assert result.bid == 100
    monkeypatch.setattr("watch_money.collector.now_ms", lambda: at + 61_000)
    assert not enrich(put(collector, rest.model_copy(update={"received_at": at + 61_000})), 60)["price_fresh"]


def test_price_time_never_fills_missing_rate_event_time(tmp_path):
    item = instrument()
    at = now_ms() - 1000
    collector = Collector(Storage(tmp_path), {})
    adapter = CexAdapter("gate")
    adapter._markets[item.id] = ("gate", {"info": {"funding_interval": 28800}, "_catalog_observed_at": at})
    rate = adapter._funding_patch(
        item,
        {
            "timestamp": None,
            "fundingRate": 0.001,
            "info": {"funding_interval": 28800, "config_change_time": at // 1000},
        },
        "fetch_funding_rate",
        at,
    )
    put(collector, rate)
    result = put(collector, bbo(item, at + 1))
    assert result.funding_rate == 0.001
    assert result.field_timestamps["funding_rate"] is None
    assert not funding_field_state(result, item, "funding_rate", at + 2, 60)["rankable"]
    assert funding_field_state(result, item, "bid", at + 2, 60)["rankable"]


def test_same_native_symbol_spot_and_perp_do_not_share_cache(tmp_path):
    perp = instrument()
    spot = perp.model_copy(update={"id": "gate:spot", "kind": "spot", "settle": None, "contract_size": 1})
    collector = Collector(Storage(tmp_path), {})
    at = now_ms() - 1000
    put(
        collector,
        MarketSnapshot(
            instrument_id=spot.id,
            venue="gate",
            source="fixture:spot",
            bid=200,
            ask=201,
            source_at=at,
            received_at=at,
        ),
    )
    put(collector, bbo(perp, at))
    assert collector.snapshots[spot.id].bid == 200
    assert collector.snapshots[perp.id].bid == 100


async def test_collector_budget_hook_and_partial_health_visible(tmp_path):
    import asyncio
    from watch_money.models import Settings

    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["gate"], broad_collection_enabled=True).model_dump())
    items = [instrument("USDT"), instrument("BTC")]
    store.save_instruments(items)
    collector = Collector(store, {"gate": SimpleNamespace()})
    collector.discovered.add("gate")
    seen = []

    class Stream:
        catalog_stream = True
        max_catalog_markets = 8000

        def select_instruments(self, selected):
            return selected[:1]

        def health_state(self):
            return "partial"

        async def stream(self, selected):
            seen.extend(i.id for i in selected)
            collector.stopping.set()
            yield bbo(selected[0], now_ms() - 1)

        async def close(self):
            pass

    await asyncio.wait_for(collector.run_stream("gate", Stream()), 2)
    status = collector.statuses["gate"]
    assert len(seen) == 1 and status.stream_requested_markets == 2
    assert status.stream_selected_markets == 1 and status.stream_truncated
    assert status.stream_state == "partial"


def test_futures_ack_without_optional_error_field_still_needs_exact_id():
    from watch_money.adapters.gate_streaming import GateFuturesStreamAdapter

    stream = GateFuturesStreamAdapter(SimpleNamespace(), settle="usdt")
    stream._subscriptions[1] = {"market_count": 3, "acknowledged": False}
    stream._acknowledge({"id": 1, "result": {"status": "success"}})
    assert stream.diagnostics()["acknowledged_markets"] == 3
    stream._acknowledge({"result": {"status": "success"}})
    assert stream.diagnostics()["acknowledged_markets"] == 3
    assert stream.diagnostics()["uncorrelated_acknowledgements"] == 1


async def test_ambiguous_native_contract_rejected_before_connection():
    from watch_money.adapters.gate_streaming import GateStreamAdapter

    rest = SimpleNamespace(_resolve=lambda item: ("gate", {"id": "BTC_USDT", "settle": "USDT", "swap": True}))
    opened = []

    def connect(endpoint):
        opened.append(endpoint)
        raise AssertionError("Ambiguous namespace must not connect")

    stream = GateStreamAdapter(rest, connection_factory=connect)
    item = instrument()
    iterator = stream.stream([item, item.model_copy(update={"id": "second-catalog-id"})])
    with pytest.raises(ValueError, match="Ambiguous"):
        await anext(iterator)
    await iterator.aclose()
    await stream.close()
    assert not opened
