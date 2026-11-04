"""Independent live-review regressions; every transport payload is synthetic."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
from watch_money.collector import Collector, enrich
from watch_money.models import Instrument, MarketSnapshot, Settings
from watch_money.storage import Storage

AT = 1_793_840_000_000


def registry_payload(venue, currency):
    """Synthetic public schemas only; no live verification is claimed."""
    address = "0x" + "12" * 20
    if venue == "gate":
        return [{"currency": currency, "chains": [{"name": "ETH", "addr": address}]}]
    if venue == "bitget":
        return {"code": "00000", "data": [{"coin": currency, "chains": [
            {"chain": "ERC20", "contractAddress": address}]}]}
    if venue == "kucoin":
        return {"code": "200000", "data": [{"currency": currency, "chains": [
            {"chainId": "eth", "chainName": "Ethereum", "contractAddress": address}]}]}
    return [{"id": currency, "supported_networks": [{"id": "ethereum", "contract_address": address}]}]


def small_market(identity, venue="alpha"):
    return Instrument(id=identity, venue=venue, native_symbol=f"{identity}/USD", base=identity.upper(),
                      quote="USD", asset_id=f"synthetic:{identity}", kind="perpetual", settle="USD")


def setup_collector(tmp_path, monkeypatch):
    import watch_money.collector as module

    monkeypatch.setattr(module, "now_ms", lambda: AT)
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["alpha"], watch_assets=["BTC"],
                                    broad_collection_enabled=True, stale_seconds=60).model_dump())
    store.save_instruments([small_market(identity) for identity in ("a", "b", "c")])
    old = MarketSnapshot(instrument_id="c", venue="alpha", bid=10, ask=11, funding_rate=.01,
                         funding_kind="current", funding_interval_hours=8,
                         source="synthetic:old-public-feed", source_at=AT - 120000, received_at=AT - 119000)
    store.save_snapshots([old], archive=False)
    collector = Collector(store, {"alpha": SimpleNamespace()})
    collector.discovered.add("alpha")
    return collector, store


@pytest.mark.asyncio
async def test_catalog_stream_retires_and_rebinds_pending_subscription_without_refreshing_capped_market(tmp_path, monkeypatch):
    collector, store = setup_collector(tmp_path, monkeypatch)
    pending = [asyncio.Event() for _ in range(3)]
    subscriptions, cancelled = [], []

    class Stream:
        catalog_stream = True
        max_catalog_markets = 1
        closed = 0

        async def stream(self, instruments):
            generation = len(subscriptions)
            subscriptions.append([(item.id, item.contract_size) for item in instruments])
            try:
                yield MarketSnapshot(instrument_id=instruments[0].id, venue="alpha", bid=20, ask=21,
                                     source="synthetic:stream-book", source_at=AT, received_at=AT)
                pending[generation].set()
                await asyncio.Event().wait()
            finally:
                cancelled.append(generation)

        async def close(self):
            self.closed += 1

    stream = Stream()
    task = asyncio.create_task(collector.run_stream("alpha", stream))
    try:
        await asyncio.wait_for(pending[0].wait(), 2)
        collector.instruments["a"].active = False
        collector.instruments["d"] = small_market("d")
        await asyncio.wait_for(pending[1].wait(), 3)
        # Same id with revised contract metadata must also replace the subscription.
        collector.instruments["b"].contract_size = .01
        await asyncio.wait_for(pending[2].wait(), 3)
        assert subscriptions == [[("a", 1)], [("b", 1)], [("b", .01)]]
        assert cancelled[:2] == [0, 1] and stream.closed == 2
        status = collector.statuses["alpha"]
        assert status.stream_requested_markets == 3 and status.stream_selected_markets == 1
        assert status.stream_truncated
        collector.refresh_status_coverage()
        assert status.light_fresh_markets == 1
        assert status.light_coverage_ratio == pytest.approx(1 / 3)
        untouched = collector.snapshots["c"]
        assert untouched.source_at == AT - 120000 and untouched.received_at == AT - 119000
        assert enrich(untouched, 60)["stale"] and enrich(untouched, 60)["field_stale"]["funding_rate"]
        assert store.latest()["c"].source_at == AT - 120000
        assert "d" not in collector.snapshots
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cancelled == [0, 1, 2] and stream.closed == 3


@pytest.mark.asyncio
async def test_catalog_cap_does_not_accept_unsolicited_market_or_make_old_cache_fresh(tmp_path, monkeypatch):
    collector, _ = setup_collector(tmp_path, monkeypatch)

    class Stream:
        catalog_stream = True
        max_catalog_markets = 1

        async def stream(self, instruments):
            assert [item.id for item in instruments] == ["a"]
            yield MarketSnapshot(instrument_id="c", venue="alpha", bid=999, ask=1000,
                                 source="synthetic:unsolicited", source_at=AT, received_at=AT)

        async def close(self):
            pass

    async def stop_after_error(_):
        collector.stopping.set()

    collector.pause = stop_after_error
    await asyncio.wait_for(collector.run_stream("alpha", Stream()), 2)
    assert collector.statuses["alpha"].stream_reconnects == 1
    assert collector.snapshots["c"].bid == 10
    assert collector.snapshots["c"].source_at == AT - 120000
    collector.refresh_status_coverage()
    assert collector.statuses["alpha"].light_fresh_markets == 0


@pytest.mark.asyncio
async def test_okx_faster_recheck_never_extends_ttl_or_uses_fresh_book_to_renew_expired_funding(tmp_path, monkeypatch):
    from watch_money.adapters.cex import CexAdapter
    from watch_money.collector import funding_response_source_ages
    from watch_money.scanner import funding_field_state

    clock = [AT]
    monkeypatch.setattr("watch_money.collector.now_ms", lambda: clock[0])
    item = small_market("okx:small", "okx")

    class PublicClient:
        has: ClassVar[dict] = {"fetchFundingRates": True}
        rateLimit = 0

        async def fetch_funding_rates(self, symbols, params):
            assert symbols is None
            return {item.native_symbol: {"timestamp": AT - 61000, "fundingRate": .001,
                    "fundingTimestamp": AT + 3600000, "interval": "8h", "info": {}}}

    adapter = CexAdapter("okx", client_factory=lambda _: PublicClient())
    adapter._markets[item.id] = ("okx", {"symbol": item.native_symbol})
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["okx"], stale_seconds=60,
                                    funding_poll_seconds=60).model_dump())
    store.save_instruments([item])
    collector = Collector(store, {"okx": adapter})
    collector.discovered.add("okx")
    status = collector.statuses["okx"]

    async def stop_after_completed_sweep(_):
        if status.funding_effective_poll_seconds is not None:
            collector.stopping.set()
        else:
            await collector.stopping.wait()

    collector.pause = stop_after_completed_sweep
    # Adapter receipt and collector clocks agree; event time is still 61s old.
    monkeypatch.setattr("watch_money.adapters.cex.now_ms", lambda: clock[0])
    await asyncio.wait_for(collector.run_planned_light("okx", adapter, funding=True), 3)
    assert status.funding_effective_poll_seconds == pytest.approx(5, abs=.1)
    assert status.funding_schedule_reason == "upstream_source_stale"
    assert status.funding_arrival_source_age_samples == 1
    assert status.funding_arrival_source_age_max_seconds == 61
    assert status.funding_errors == {} and status.funding_retries == 0
    assert status.funding_source_stale_markets == 1 and status.funding_fresh_markets == 0
    assert store.settings().stale_seconds == 60
    clock[0] += 1000
    book = MarketSnapshot(instrument_id=item.id, venue="okx", bid=99, ask=100,
                          source="synthetic:fresh-book", source_at=clock[0], received_at=clock[0])
    assert funding_response_source_ages([book], {item.id: item}, clock[0], 60) == []
    collector.snapshots[item.id] = collector.merge_patch(book)
    merged = collector.snapshots[item.id]
    assert merged.field_timestamps["funding_rate"] == AT - 61000
    assert merged.field_received_at["funding_rate"] == AT
    assert not funding_field_state(merged, item, "funding_rate", clock[0], 60)["rankable"]
    collector.refresh_status_coverage()
    assert status.light_fresh_markets == 1 and status.funding_fresh_markets == 0
    assert status.funding_source_stale_markets == 1 and status.funding_errors == {}


@pytest.mark.parametrize("problem", ["unknown", "future", "demo", "invalid"])
def test_untrusted_funding_arrivals_do_not_drive_faster_poll_policy(problem):
    from watch_money.adapters.cex import CexAdapter, LightPollBatch
    from watch_money.collector import funding_response_source_ages

    item = small_market("okx:small", "okx")
    snapshot = MarketSnapshot(instrument_id=item.id, venue="okx", funding_rate=.1,
                              source="synthetic:untrusted-funding", source_at=AT - 59000, received_at=AT)
    if problem == "unknown":
        snapshot.source_at = None
    elif problem == "future":
        snapshot.source_at = AT + 1
    elif problem == "demo":
        snapshot.is_demo = True
    else:
        snapshot.valid = False
    ages = funding_response_source_ages([snapshot], {item.id: item}, AT, 60)
    assert ages == []
    adapter = CexAdapter("okx")
    adapter._markets[item.id] = ("okx", {"symbol": item.native_symbol})
    period, reason = adapter.funding_poll_policy(LightPollBatch("synthetic", (item,), True),
                                                source_ages=ages, period=60, stale_seconds=60)
    assert period == 60 and reason == "source_age_unknown"


@pytest.mark.asyncio
async def test_lighter_successive_raw_metrics_updates_are_not_dropped_or_promoted_to_verified_units(monkeypatch):
    from watch_money.adapters.dex import LighterAdapter
    from watch_money.adapters.dex_streaming import DexStreamAdapter

    monkeypatch.setattr("watch_money.adapters.dex_streaming.now_ms", lambda: AT)
    item = small_market("lighter:perpetual:7", "lighter")
    item.native_symbol = "7"

    class Socket:
        messages: ClassVar[list] = [
            {"type": "connected"},
            {"type": "subscribed/market_stats", "channel": "market_stats:all", "timestamp": AT - 1,
             "market_stats": {"7": {"market_id": 7, "current_funding_rate": "0.01", "open_interest": "100"}}},
            {"type": "update/market_stats", "channel": "market_stats:all", "timestamp": AT,
             "market_stats": {"7": {"market_id": 7, "current_funding_rate": "0.02", "open_interest": "200"}}},
        ]
        sent: ClassVar[list] = []

        async def send(self, message):
            self.sent.append(json.loads(message))

        async def recv(self):
            if self.messages:
                return json.dumps(self.messages.pop(0))
            await asyncio.Event().wait()

        async def close(self):
            pass

    socket = Socket()

    @asynccontextmanager
    async def connection(_):
        yield socket

    adapter = DexStreamAdapter(LighterAdapter(), connection_factory=connection)
    iterator = adapter.stream([item])
    try:
        first = await asyncio.wait_for(anext(iterator), 1)
        second = await asyncio.wait_for(anext(iterator), 1)
        assert first.protocol_metrics["raw_upcoming_funding_rate"] == "0.01"
        assert second.protocol_metrics["raw_upcoming_funding_rate"] == "0.02"
        assert second.protocol_metrics["raw_open_interest"] == "200"
        assert second.protocol_metrics["funding_unit"] == second.protocol_metrics["open_interest_unit"] == "unverified"
        assert first.source_at == AT - 1 and second.source_at == AT
        assert all(row.funding_rate is None and row.funding_interval_hours is None
                   and row.open_interest is None and row.oi_unit is None for row in (first, second))
        assert socket.sent == [{"type": "subscribe", "channel": "market_stats/all"}]
    finally:
        await iterator.aclose()
        await adapter.close()


def test_new_lighter_publication_without_bid_ask_does_not_refresh_retained_book(tmp_path, monkeypatch):
    from watch_money.adapters.dex_streaming import lighter_patches

    monkeypatch.setattr("watch_money.adapters.dex_streaming.now_ms", lambda: AT)
    monkeypatch.setattr("watch_money.collector.now_ms", lambda: AT)
    item = small_market("lighter:perpetual:7", "lighter")
    item.native_symbol = "7"
    store = Storage(tmp_path)
    store.save_instruments([item])
    collector = Collector(store, {"lighter": SimpleNamespace()})
    old, = lighter_patches({"7": item}, {"type": "subscribed/market_stats", "channel": "market_stats:all",
        "timestamp": AT - 120000, "market_stats": {"7": {"market_id": 7, "best_bid_price": "99",
            "best_ask_price": "100", "current_funding_rate": "9", "open_interest": "1000"}}})
    collector.snapshots[item.id] = collector.merge_patch(old)
    latest, = lighter_patches({"7": item}, {"type": "update/market_stats", "channel": "market_stats:all",
        "timestamp": AT, "market_stats": {"7": {"market_id": 7, "last_trade_price": "100"}}})
    merged = collector.merge_patch(latest)
    assert merged.last == 100 and merged.field_timestamps["last"] == AT
    assert merged.bid == 99 and merged.ask == 100
    assert merged.field_timestamps["bid"] == merged.field_timestamps["ask"] == AT - 120000
    assert enrich(merged, 60)["stale"]
    assert merged.funding_rate is None and merged.open_interest is None


def test_hyperliquid_book_time_never_becomes_untimed_context_funding_time(tmp_path, monkeypatch):
    from watch_money.adapters.dex_streaming import hyperliquid_patch
    from watch_money.scanner import funding_field_state

    monkeypatch.setattr("watch_money.adapters.dex_streaming.now_ms", lambda: AT)
    monkeypatch.setattr("watch_money.collector.now_ms", lambda: AT)
    item = small_market("hyperliquid:perpetual:SMALL", "hyperliquid")
    item.native_symbol = "SMALL"
    store = Storage(tmp_path)
    store.save_instruments([item])
    collector = Collector(store, {"hyperliquid": SimpleNamespace()})
    context = hyperliquid_patch(item, {"channel": "activeAssetCtx", "data": {
        "coin": "SMALL", "ctx": {"funding": "0.001", "markPx": "100", "openInterest": "50"}}})
    collector.snapshots[item.id] = collector.merge_patch(context)
    book = hyperliquid_patch(item, {"channel": "l2Book", "data": {"coin": "SMALL", "time": AT,
        "levels": [[{"px": "99", "sz": "1"}], [{"px": "100", "sz": "1"}]]}})
    merged = collector.merge_patch(book)
    assert merged.bid == 99 and merged.ask == 100 and merged.field_timestamps["bid"] == AT
    assert merged.funding_rate == .001 and merged.field_timestamps["funding_rate"] is None
    assert merged.field_timestamps["funding_kind"] is None
    assert not funding_field_state(merged, item, "funding_rate", AT, 60)["rankable"]


@pytest.mark.asyncio
async def test_hyperliquid_catalog_shards_every_selected_market_and_closes_every_child(monkeypatch):
    from watch_money.adapters.dex import HyperliquidAdapter
    from watch_money.adapters.dex_streaming import HyperliquidCatalogStream
    from watch_money.adapters.streaming import StreamUnavailable

    monkeypatch.setattr("watch_money.adapters.dex_streaming.now_ms", lambda: AT)
    items = []
    for index in range(201):
        item = small_market(f"hyperliquid:perpetual:SMALL{index}", "hyperliquid")
        item.native_symbol = f"SMALL{index}"
        items.append(item)
    sockets = []
    subscribed = asyncio.Event()

    class Socket:
        def __init__(self):
            self.sent, self.messages, self.closed = [], asyncio.Queue(), False

        async def send(self, payload):
            self.sent.append(json.loads(payload))
            if sum(len(socket.sent) for socket in sockets) == 402:
                subscribed.set()

        async def recv(self):
            return json.dumps(await self.messages.get())

        async def close(self):
            self.closed = True

    @asynccontextmanager
    async def connection(_):
        socket = Socket()
        sockets.append(socket)
        yield socket

    adapter = HyperliquidCatalogStream(HyperliquidAdapter(), connection_factory=connection)
    iterator = adapter.stream(items)
    pending = asyncio.create_task(anext(iterator))
    try:
        await asyncio.wait_for(subscribed.wait(), 2)
        assert sorted(len(socket.sent) for socket in sockets) == [2, 200, 200]
        books = [message["subscription"] for socket in sockets for message in socket.sent
                 if message["subscription"]["type"] == "l2Book"]
        assert {message["coin"] for message in books} == {item.native_symbol for item in items}
        assert len(books) == 201 and all(message["fast"] for message in books)
        assert adapter.subscription_count == 402
        expected = set()
        for socket in sockets:
            symbol = socket.sent[-1]["subscription"]["coin"]
            expected.add(f"hyperliquid:perpetual:{symbol}")
            await socket.messages.put({"channel": "l2Book", "data": {"coin": symbol, "time": AT,
                "levels": [[{"px": "99", "sz": "1"}], [{"px": "100", "sz": "1"}]]}})
        received = [(await asyncio.wait_for(pending, 2)).instrument_id]
        for _ in range(2):
            received.append((await asyncio.wait_for(anext(iterator), 2)).instrument_id)
        assert set(received) == expected
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()
        await adapter.close()
    assert all(socket.closed for socket in sockets)
    before = len(sockets)
    excessive = items + [items[0].model_copy(update={"id": f"hyperliquid:perpetual:EXTRA{index}",
                                                    "native_symbol": f"EXTRA{index}"}) for index in range(300)]
    overflow = adapter.stream(excessive)
    with pytest.raises(StreamUnavailable, match="500"):
        await anext(overflow)
    assert len(sockets) == before, "Oversized selection must reject explicitly before opening any partial session"


@pytest.mark.parametrize("venue,client_id", [
    ("gate", "gate"), ("bitget", "bitget"), ("kucoin", "kucoin"), ("coinbase", "coinbaseexchange"),
])
async def test_public_registry_joins_only_exact_native_currency_and_reviewed_product_scope(tmp_path, venue, client_id):
    from watch_money.asset_verification import SOURCES, AssetVerificationService
    from watch_money.models import now_ms

    native_currency = "nativeSmall"

    def item(identity, *, currency=native_currency, suffix=native_currency, native_client=client_id, kind="spot"):
        native = f"cex:{venue}:{native_client}:{suffix}"
        return Instrument(id=identity, venue=venue, native_symbol="SMALL/USDT", base="SMALL", quote="USDT",
                          native_currency_id=currency, asset_id=native, native_asset_id=native, kind=kind)

    members = [item("valid"), item("no-original-id", currency=None),
               item("conflicting-original-id", currency="different"),
               item("case-differs", currency="NATIVESMALL", suffix="NATIVESMALL"),
               item("derivative", kind="perpetual"),
               item("wrong-business", native_client="coinbase" if venue == "coinbase" else "kucoinfutures")]
    store = Storage(tmp_path)
    store.save_instruments(members)
    collector = SimpleNamespace(instruments=store.instruments())
    requested = []

    def transport(request):
        assert str(request.url) == SOURCES[venue]["endpoint"]
        assert request.method == "GET" and not request.url.query
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        requested.append(str(request.url))
        return httpx.Response(200, json=registry_payload(venue, native_currency))

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service = AssetVerificationService(store, collector, client, clock=now_ms)
        result = await service.public_refresh([venue])
        assert len(requested) == 1 and result["imported"] == 1 and not result["mappings_changed"]
        attempt = result["sources"][0]
        assert attempt["state"] == "observed"
        assert attempt["scope_eligible_markets"] == 2 and attempt["scope_rejected_markets"] == 4
        assert attempt["matched_markets"] == 1 and attempt["unmatched_currency_markets"] == 1
        assert attempt["scope_reasons"]["original_currency_id_missing"] == 1
        assert attempt["scope_reasons"]["original_currency_id_conflicts_with_native_identity"] == 1
        assert attempt["scope_reasons"]["product_out_of_source_scope"] == 1
        assert attempt["scope_reasons"]["native_client_out_of_source_scope"] == 1
        assert not any(item.mapping_verified for item in collector.instruments.values())
        proof = service.status()["evidence"]
        assert len(proof) == 1 and proof[0]["instrument_id"] == "valid"
        assert proof[0]["native_currency_id"] == native_currency
        preview = service.preview(result["evidence_ids"])
        assert preview["ready"] == 1 and preview["blocked"] == 0
        applied = service.apply(result["evidence_ids"], preview["preview_token"])
        assert applied["applied_markets"] == 1
        assert {identity for identity, market in collector.instruments.items() if market.mapping_verified} == {"valid"}
        coverage = next(row for row in service.status()["platform_coverage"] if row["venue"] == venue)
        assert coverage["active_markets"] == 6 and coverage["source_scope_markets"] == 2
        assert coverage["source_covered_markets"] == coverage["traceable_verified_markets"] == 1


@pytest.mark.parametrize("base_id", [None, "mIxEd-Native42"])
async def test_ccxt_discovery_preserves_original_id_and_missing_id_cannot_use_ticker_registry_match(tmp_path, base_id):
    from watch_money.adapters.cex import CexAdapter
    from watch_money.asset_verification import AssetVerificationService

    class PublicClient:
        precisionMode = 4

        async def load_markets(self, reload=False):
            assert reload
            return {"SMALL/USDT": {"id": "SMALL_USDT", "symbol": "SMALL/USDT", "base": "SMALL",
                    "baseId": base_id, "quote": "USDT", "spot": True, "active": True}}

    adapter = CexAdapter("gate", client_factory=lambda _: PublicClient())
    markets = await adapter.discover()
    assert len(markets) == 1 and markets[0].native_currency_id == base_id
    assert markets[0].asset_id.endswith(":" + (base_id or "SMALL"))
    store = Storage(tmp_path)
    store.save_instruments(markets)
    collector = SimpleNamespace(instruments=store.instruments())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=registry_payload("gate", base_id or "SMALL")))) as client:
        service = AssetVerificationService(store, collector, client)
        imported = await service.public_refresh(["gate"])
        assert imported["imported"] == (0 if base_id is None else 1)
        assert service.preview()["ready"] == (0 if base_id is None else 1)
        assert not any(item.mapping_verified for item in collector.instruments.values())
        if base_id is None:
            assert imported["sources"][0]["scope_reasons"] == {"original_currency_id_missing": 1}


async def test_legacy_public_proof_without_original_currency_id_is_downgraded_on_restart(tmp_path):
    from watch_money.asset_verification import AssetVerificationService

    native = "cex:kucoin:kucoin:SMALL"
    market = Instrument(id="small", venue="kucoin", native_symbol="SMALL/USDT", base="SMALL", quote="USDT",
                        kind="spot", asset_id=native, native_asset_id=native, native_currency_id="SMALL")
    store = Storage(tmp_path)
    store.save_instruments([market])
    collector = SimpleNamespace(instruments=store.instruments())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=registry_payload("kucoin", "SMALL")))) as client:
        service = AssetVerificationService(store, collector, client)
        imported = await service.public_refresh(["kucoin"])
        preview = service.preview(imported["evidence_ids"])
        service.apply(imported["evidence_ids"], preview["preview_token"])
        assert collector.instruments["small"].mapping_verified
        # Model an old persisted proof, not a new import with an explicit native ID.
        with store.connect() as db:
            identity, encoded = db.execute("SELECT id,record FROM asset_identity_evidence").fetchone()
            legacy = json.loads(encoded)
            del legacy["native_currency_id"]
            db.execute("UPDATE asset_identity_evidence SET record=? WHERE id=?", (json.dumps(legacy), identity))
        restarted = AssetVerificationService(store, collector, client)
        status = restarted.status()
        assert not collector.instruments["small"].mapping_verified
        assert status["evidence"][0]["status"] == "scope_unverified"
        assert status["coverage"]["traceable_verified_markets"] == 0
        assert status["coverage"]["source_covered_markets"] == 0
        assert status["coverage"]["ready_proposals"] == 0
        assert any(row["action"] == "asset_public_scope_downgrade" for row in status["audit"])
