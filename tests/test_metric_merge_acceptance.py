"""Independent synthetic regressions; no real exchange or account requests."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from watch_money.adapters.dex import HyperliquidAdapter, LighterAdapter
from watch_money.adapters.dex_streaming import DexStreamAdapter, lighter_patches
from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot
from watch_money.scanner import funding_field_state
from watch_money.storage import Storage

AT = 1_800_000_000_000


@pytest.fixture
def clock(monkeypatch):
    current = [AT]
    for module in ("watch_money.collector", "watch_money.models", "watch_money.adapters.dex_streaming"):
        monkeypatch.setattr(f"{module}.now_ms", lambda: current[0])
    return current


def market(venue="lighter"):
    symbol = "7" if venue == "lighter" else "SMALL"
    return Instrument(id=f"{venue}:perpetual:{symbol}", venue=venue, native_symbol=symbol, base="SMALL",
                      quote="USDC", settle="USDC", kind="perpetual", asset_id=f"{venue}:SMALL")


def setup(tmp_path, item):
    store = Storage(tmp_path)
    store.save_instruments([item])
    return Collector(store, {item.venue: SimpleNamespace()}), store


def merge(collector, patch):
    merged = collector.merge_patch(patch)
    collector.snapshots[patch.instrument_id] = merged
    return merged


def metric_patch(item, values=None, *, at=AT, received=AT, source="synthetic:public-source", **fields):
    return MarketSnapshot(instrument_id=item.id, venue=item.venue, source=source, source_at=at,
                          received_at=received, protocol_metrics=values or {}, **fields)


def lighter_frame(item, at, **values):
    return lighter_patches({item.native_symbol: item}, {"type": "update/market_stats",
        "channel": "market_stats:all", "timestamp": at,
        "market_stats": {item.native_symbol: {"market_id": int(item.native_symbol), **values}}})[0]


async def test_rest_comparative_and_distinct_ws_funding_observations_survive_sparse_updates_and_restart(tmp_path, clock):
    item = market()
    collector, store = setup(tmp_path, item)
    requests = []

    def transport(request):
        assert request.method == "GET" and "authorization" not in request.headers
        requests.append(request.url.path)
        if request.url.path.endswith("/orderBookDetails"):
            return httpx.Response(200, json={"order_book_details": [{"market_id": 7, "market_type": "perp",
                "last_trade_price": "100", "mark_price": "100"}]})
        assert request.url.path.endswith("/funding-rates")
        return httpx.Response(200, json={"funding_rates": [
            {"market_id": 7, "exchange": "lighter", "rate": "0.0012"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = LighterAdapter(client)
        rest = (await adapter.fetch([item]))[0].model_copy(update={"received_at": clock[0]})
        original = merge(collector, rest)
        comparative = "raw_comparative_funding_rate"
        assert original.protocol_metric_sources[comparative].endswith("/funding-rates#exchange=lighter")
        assert original.protocol_metric_timestamps[comparative] is None
        clock[0] += 1000
        upcoming_at = clock[0]
        merge(collector, lighter_frame(item, upcoming_at, current_funding_rate="0.02"))
        clock[0] += 1000
        settled_at = clock[0]
        merge(collector, lighter_frame(item, settled_at, funding_rate="0.01", funding_timestamp=AT - 3600000))
        clock[0] += 1000
        merged = merge(collector, lighter_frame(item, clock[0], mark_price="101", best_bid_price="100",
                                                best_ask_price="102"))
        expected = {comparative: "0.0012", "raw_upcoming_funding_rate": "0.02",
                    "raw_last_settled_funding_rate": "0.01"}
        for key, value in expected.items():
            assert merged.protocol_metrics[key] == value
        assert merged.protocol_metric_received_at[comparative] == AT
        assert merged.protocol_metric_timestamps[comparative] is None
        assert merged.protocol_metric_timestamps["raw_upcoming_funding_rate"] == upcoming_at
        assert merged.protocol_metric_received_at["raw_upcoming_funding_rate"] == upcoming_at
        assert merged.protocol_metric_timestamps["raw_last_settled_funding_rate"] == settled_at
        assert merged.protocol_metrics["funding_observed_at_ms"] == str(settled_at)
        assert merged.funding_rate is None and merged.funding_kind is None and merged.funding_interval_hours is None
        assert not funding_field_state(merged, item, "funding_rate", clock[0], 60)["rankable"]
        store.save_snapshots([merged], archive=False)
        collector = Collector(store, {item.venue: SimpleNamespace()})
        clock[0] += 1000
        rest_again = (await adapter.fetch([item]))[0].model_copy(update={"received_at": clock[0]})
        restored = merge(collector, rest_again)
        for key, value in expected.items():
            assert restored.protocol_metrics[key] == value
        assert restored.protocol_metric_received_at[comparative] == clock[0]
        assert restored.protocol_metric_received_at["raw_upcoming_funding_rate"] == upcoming_at
        assert restored.protocol_metric_timestamps["raw_last_settled_funding_rate"] == settled_at
    assert len(requests) == 4


def test_each_metric_uses_its_own_source_order_instead_of_newer_envelope_time(tmp_path, clock):
    item = market()
    collector, _ = setup(tmp_path, item)
    merge(collector, metric_patch(item, {"a": "old-a", "b": "old-b"}, at=AT - 1000,
        protocol_metric_timestamps={"a": AT - 5000, "b": AT - 2000},
        protocol_metric_received_at={"a": AT - 4000, "b": AT - 1000}))
    clock[0] += 1000
    updated = merge(collector, metric_patch(item, {"a": "new-a", "b": "late-b"}, at=clock[0], received=clock[0],
        protocol_metric_timestamps={"a": AT - 3000, "b": AT - 3000},
        protocol_metric_received_at={"a": AT - 2000, "b": clock[0]}))
    assert updated.protocol_metrics == {"a": "new-a", "b": "old-b"}
    assert updated.protocol_metric_timestamps == {"a": AT - 3000, "b": AT - 2000}
    assert updated.protocol_metric_received_at == {"a": AT - 2000, "b": AT - 1000}


@pytest.mark.parametrize("known_time", [True, False], ids=["source-time", "receipt-only"])
def test_source_owned_clear_survives_restart_and_rejects_older_or_equal_observation(tmp_path, clock, known_time):
    item = market()
    collector, store = setup(tmp_path, item)
    def stamp(value):
        return value if known_time else None
    merge(collector, metric_patch(item, {"raw_rate": ".01", "unit": "unverified"}, at=stamp(AT)))
    clock[0] += 1000
    foreign = merge(collector, metric_patch(item, at=stamp(clock[0]), received=clock[0], source="synthetic:other-source",
                                          cleared_protocol_metrics=["raw_rate"]))
    assert foreign.protocol_metrics["raw_rate"] == ".01"
    cleared_at = clock[0] + 1000
    clock[0] = cleared_at
    cleared = merge(collector, metric_patch(item, at=stamp(cleared_at), received=cleared_at,
                                          cleared_protocol_metrics=["raw_rate"]))
    assert "raw_rate" not in cleared.protocol_metrics and cleared.protocol_metrics["unit"] == "unverified"
    assert cleared.protocol_metric_received_at["raw_rate"] == cleared_at
    assert cleared.protocol_metric_sources["raw_rate"] == "synthetic:public-source"
    store.save_snapshots([cleared], archive=False)
    collector = Collector(store, {item.venue: SimpleNamespace()})
    for observed_at in (AT, cleared_at):
        clock[0] += 1000
        late = merge(collector, metric_patch(item, {"raw_rate": "replayed"}, at=stamp(observed_at), received=clock[0],
            protocol_metric_received_at={"raw_rate": observed_at}))
        assert "raw_rate" not in late.protocol_metrics, "An old/equal observation must not undo a persisted clear"
        assert late.protocol_metric_received_at["raw_rate"] == cleared_at
    clock[0] += 1000
    recovered = merge(collector, metric_patch(item, {"raw_rate": ".02"}, at=stamp(clock[0]), received=clock[0]))
    assert recovered.protocol_metrics == {"raw_rate": ".02", "unit": "unverified"}
    assert recovered.protocol_metric_received_at["unit"] == AT


def test_legacy_metrics_with_no_field_provenance_cannot_borrow_recent_price_envelope(tmp_path, clock):
    item = market()
    collector, store = setup(tmp_path, item)
    # An old cache may already contain a newer book envelope around older raw metrics.
    old = metric_patch(item, {"raw_rate": ".01"}, at=AT - 1000, received=AT - 500,
                       source="synthetic:later-book-envelope", bid=99, ask=100)
    store.save_snapshots([old], archive=False)
    collector = Collector(store, {item.venue: SimpleNamespace()})
    fresh = merge(collector, metric_patch(item, {}, at=AT, source="synthetic:new-price-source", bid=100, ask=101))
    assert fresh.protocol_metrics == {"raw_rate": ".01"}
    assert fresh.protocol_metric_sources["raw_rate"] == "legacy_field_provenance_unavailable"
    assert fresh.protocol_metric_timestamps["raw_rate"] is None
    assert "raw_rate" not in fresh.protocol_metric_received_at


@pytest.mark.parametrize("invalid", [None, "unavailable"])
async def test_rest_missing_rate_preserves_observation_but_explicit_invalid_clears_only_comparative(tmp_path, clock, invalid):
    item = market()
    collector, store = setup(tmp_path, item)
    rate_fields = {"rate": ".001"}

    def transport(request):
        if request.url.path.endswith("/orderBookDetails"):
            return httpx.Response(200, json={"order_book_details": [{"market_id": 7, "market_type": "perp"}]})
        assert request.url.path.endswith("/funding-rates")
        return httpx.Response(200, json={"funding_rates": [{"market_id": 7, "exchange": "lighter", **rate_fields}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = LighterAdapter(client)

        async def refresh():
            row = (await adapter.fetch([item]))[0].model_copy(update={"received_at": clock[0]})
            return merge(collector, row)

        await refresh()
        merge(collector, lighter_frame(item, clock[0], current_funding_rate=".02", funding_rate=".01"))
        clock[0] += 1000
        rate_fields.clear()
        sparse = await refresh()
        assert sparse.protocol_metrics["raw_comparative_funding_rate"] == ".001"
        assert sparse.protocol_metric_received_at["raw_comparative_funding_rate"] == AT
        clock[0] += 1000
        rate_fields["rate"] = invalid
        cleared = await refresh()
        assert "raw_comparative_funding_rate" not in cleared.protocol_metrics
        assert cleared.protocol_metrics["raw_upcoming_funding_rate"] == ".02"
        assert cleared.protocol_metrics["raw_last_settled_funding_rate"] == ".01"
        store.save_snapshots([cleared], archive=False)
        collector = Collector(store, {item.venue: SimpleNamespace()})
        clock[0] += 1000
        rate_fields["rate"] = ".002"
        recovered = await refresh()
        assert recovered.protocol_metrics["raw_comparative_funding_rate"] == ".002"
        assert recovered.protocol_metric_timestamps["raw_comparative_funding_rate"] is None
        assert recovered.protocol_metric_received_at["raw_upcoming_funding_rate"] == AT


@pytest.mark.parametrize("settlement_time", [None, 123], ids=["explicit-null", "unit-unverified"])
async def test_real_lighter_stream_delivers_distinct_clear_only_frames_and_clears_settlement_alias(
        tmp_path, clock, settlement_time):
    item = market()
    collector, _ = setup(tmp_path, item)

    class Socket:
        def __init__(self):
            self.events = asyncio.Queue()
            self.events.put_nowait({"type": "connected"})
            rows = [
                {"current_funding_rate": ".02", "funding_rate": ".01", "funding_timestamp": AT - 3600000,
                 "open_interest": "123"},
                {"current_funding_rate": None},
                {"funding_rate": "unavailable"},
                {"funding_timestamp": settlement_time, "open_interest": "unavailable"},
            ]
            for index, row in enumerate(rows):
                self.events.put_nowait({"type": "subscribed/market_stats" if index == 0 else "update/market_stats",
                    "channel": "market_stats:all", "timestamp": AT - 1000 if index == 0 else AT,
                    "market_stats": {"7": {"market_id": 7, **row}}})

        async def send(self, message):
            assert json.loads(message)["type"] == "subscribe"

        async def recv(self):
            return json.dumps(await self.events.get())

        async def close(self):
            pass

    @asynccontextmanager
    async def connection(_):
        yield Socket()

    stream = DexStreamAdapter(LighterAdapter(), connection_factory=connection)
    iterator = stream.stream([item])
    try:
        initial = merge(collector, await asyncio.wait_for(anext(iterator), 1))
        assert initial.protocol_metrics["last_settled_funding_at_ms"] == str(AT - 3600000)
        upcoming_cleared = merge(collector, await asyncio.wait_for(anext(iterator), 1))
        assert "raw_upcoming_funding_rate" not in upcoming_cleared.protocol_metrics
        assert upcoming_cleared.protocol_metrics["raw_last_settled_funding_rate"] == ".01"
        settled_cleared = merge(collector, await asyncio.wait_for(anext(iterator), 1))
        assert "raw_last_settled_funding_rate" not in settled_cleared.protocol_metrics
        assert settled_cleared.protocol_metrics["raw_open_interest"] == "123"
        final = merge(collector, await asyncio.wait_for(anext(iterator), 1))
        if settlement_time is None:
            assert "raw_last_settled_funding_timestamp" not in final.protocol_metrics
        else:
            assert final.protocol_metrics["raw_last_settled_funding_timestamp"] == str(settlement_time)
        assert "last_settled_funding_at_ms" not in final.protocol_metrics
        assert "raw_open_interest" not in final.protocol_metrics
        assert final.funding_rate is None and final.open_interest is None
    finally:
        await iterator.aclose()


@pytest.mark.parametrize("future_clear", [False, True], ids=["future-value", "future-clear"])
def test_future_metric_or_tombstone_cannot_block_correctly_timed_recovery_after_restart(tmp_path, clock, future_clear):
    item = market()
    collector, store = setup(tmp_path, item)
    merge(collector, metric_patch(item, {"raw_rate": ".01"}, at=AT - 1000))
    future = merge(collector, metric_patch(item, {} if future_clear else {"raw_rate": ".99"}, at=AT + 20000,
        cleared_protocol_metrics=["raw_rate"] if future_clear else []))
    store.save_snapshots([future], archive=False)
    collector = Collector(store, {item.venue: SimpleNamespace()})
    clock[0] += 1000
    normal = merge(collector, metric_patch(item, {"raw_rate": ".02"}, at=AT, received=clock[0]))
    assert normal.protocol_metrics["raw_rate"] == ".02"
    assert normal.protocol_metric_timestamps["raw_rate"] == AT
    assert normal.protocol_metric_received_at["raw_rate"] == clock[0]


@pytest.mark.parametrize("offset", [1, 20000, 40000])
async def test_future_hyperliquid_frame_does_not_block_normal_book_after_cache_restart(tmp_path, clock, offset):
    item = market("hyperliquid")
    collector, store = setup(tmp_path, item)

    class Socket:
        def __init__(self):
            self.events = asyncio.Queue()
            for at, bid in ((AT + offset, 900), (AT, 100)):
                self.events.put_nowait({"channel": "bbo", "data": {"coin": "SMALL", "time": at,
                    "bbo": [{"px": str(bid), "sz": "1"}, {"px": str(bid + 1), "sz": "1"}]}})

        async def send(self, message):
            assert json.loads(message)["method"] in {"subscribe", "ping"}

        async def recv(self):
            return json.dumps(await self.events.get())

        async def close(self):
            pass

    @asynccontextmanager
    async def connection(_):
        yield Socket()

    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connection)
    iterator = stream.stream([item])
    try:
        future = merge(collector, await asyncio.wait_for(anext(iterator), 1))
        assert not funding_field_state(future, item, "bid", AT, 60)["rankable"]
        store.save_snapshots([future], archive=False)
        collector = Collector(store, {item.venue: SimpleNamespace()})
        normal = merge(collector, await asyncio.wait_for(anext(iterator), 1))
        assert normal.bid == 100 and normal.ask == 101
        for field in ("bid", "ask"):
            assert funding_field_state(normal, item, field, AT, 60)["rankable"]
            assert normal.field_timestamps[field] == AT
    finally:
        await iterator.aclose()


def test_originally_future_cache_cannot_poison_ordering_after_wall_clock_catches_up(tmp_path, clock):
    item = market('hyperliquid')
    collector, store = setup(tmp_path, item)
    merge(collector, metric_patch(item, {'raw_rate':'.9'}, at=AT+20000, received=AT, bid=900, ask=901,
                                 field_timestamps={'bid':AT+20000,'ask':AT+20000}))
    store.save_snapshots(list(collector.snapshots.values()), archive=False)
    clock[0] = AT+30000
    collector = Collector(store,{item.venue:SimpleNamespace()})
    result = merge(collector,metric_patch(item,{'raw_rate':'.01'},at=AT+10000,received=AT+30000,bid=100,ask=101))
    assert result.bid == 100 and result.ask == 101
    assert result.protocol_metrics['raw_rate'] == '.01'
    assert result.protocol_metric_timestamps['raw_rate'] == AT+10000
