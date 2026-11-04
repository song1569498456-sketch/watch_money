"""Independent follow-up regressions; synthetic fixtures are not live evidence."""
import asyncio

import pytest

from watch_money.models import Instrument, MarketSnapshot, OrderBook, now_ms
from watch_money.strategies import CalculateRequest, LegCost, FundingEvent, calculate


def perpetual(identity):
    return Instrument(id=identity, venue=identity, native_symbol="BTC/USD", asset_id="bitcoin",
                      base="BTC", quote="USD", settle="USD", kind="perpetual", mapping_verified=True)


def funding_request(at):
    instruments = [perpetual("a"), perpetual("b")]
    return CalculateRequest(
        instruments=instruments, buy_instrument_id="a", sell_instrument_id="b", size_base=1,
        strategy="funding_spread", as_of=at, hold_hours=8, assume_exit_at_entry=True,
        snapshots=[MarketSnapshot(instrument_id=i.id, venue=i.venue, source="fixture", source_at=at,
                                  received_at=at, mark=100, funding_rate=0.001, funding_kind="current",
                                  funding_interval_hours=8, next_funding_at=at + 3_600_000)
                   for i in instruments],
        orderbooks=[OrderBook(instrument_id=i.id, bids=[(99, 10)], asks=[(100, 10)],
                             source="fixture", source_at=at, received_at=at) for i in instruments],
        costs=[LegCost(instrument_id=i.id, open_fee_rate=0, close_fee_rate=0) for i in instruments],
    )


def test_stale_explicit_override_cannot_replace_current_funding_estimate():
    """A partial override needs the same freshness gate as a complete schedule."""
    at = now_ms()
    request = funding_request(at)
    request.funding_events = [FundingEvent(instrument_id="b", settlement_at=at + 3_600_000,
                                         rate=0.5, kind="predicted", known_at=at - 3_600_000,
                                         source="old forecast")]
    result = calculate(request)["opportunities"][0]
    assert result["net_profit"] is None, "A stale 50% forecast must not produce an actionable net profit"
    assert any("stale" in reason.lower() for reason in result["missing_prerequisites"])


@pytest.mark.parametrize("kind,unit,step,size", [
    ("perpetual", "contracts", 1.0, 0.25),
    ("spot", "base", 0.1, 0.15),
])
def test_exact_execution_rejects_quantities_between_native_amount_steps(kind, unit, step, size):
    request = funding_request(now_ms())
    request.size_base = size
    for instrument in request.instruments:
        instrument.kind = kind
        instrument.amount_precision = step
    for book in request.orderbooks:
        book.quantity_unit = unit
    if kind == "spot":
        request.strategy = "spot_spread"
    result = calculate(request)["opportunities"][0]
    assert result["net_profit"] is None, "An unexecutable fractional quantity cannot have an actionable net"
    assert result["invalid_reasons"] or result["missing_prerequisites"]


@pytest.mark.asyncio
async def test_stream_future_timestamp_does_not_poison_recovery_watermark(monkeypatch):
    """A malformed future ticker must not suppress every corrected current ticker."""
    from watch_money.adapters.cex import CexAdapter
    from watch_money.adapters.streaming import CexStreamAdapter
    from watch_money.adapters import streaming

    at = now_ms()
    monkeypatch.setattr(streaming, "now_ms", lambda: at)
    instrument = Instrument(id="okx:BTC", venue="okx", native_symbol="BTC/USDT", asset_id="BTC",
                            base="BTC", quote="USDT", kind="spot")

    class Client:
        has = {"watchTickers": True}

        def __init__(self):
            self.messages = asyncio.Queue()

        def set_markets_from_exchange(self, _):
            pass

        async def watch_tickers(self, _):
            return await self.messages.get()

        async def close(self):
            pass

    client = Client()
    rest = CexAdapter("okx", client_factory=lambda _: object())
    rest._markets[instrument.id] = ("okx", {"symbol": instrument.native_symbol})
    stream = CexStreamAdapter(rest, client_factory=lambda _: client)
    generator = stream.stream([instrument])
    for timestamp, price in [(at + 86_400_000, 200), (at, 100)]:
        client.messages.put_nowait({instrument.native_symbol: {"last": price, "timestamp": timestamp}})
    try:
        first = await asyncio.wait_for(anext(generator), timeout=1)
        recovered = first if first.last == 100 else await asyncio.wait_for(anext(generator), timeout=0.1)
        assert recovered.last == 100
        assert recovered.source_at == at
    finally:
        await generator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_result", ["failure", "empty"])
async def test_light_catalog_rotation_survives_bad_batch_and_preserves_newer_field_times(tmp_path, first_result):
    from watch_money.collector import Collector, enrich
    from watch_money.models import Settings
    from watch_money.storage import Storage

    at = now_ms()
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["a"], watch_assets=["BTC"], broad_batch_size=2,
                                    broad_poll_seconds=10, history_enabled=False).model_dump())
    catalog = [Instrument(id=f"a:{index}", venue="a", native_symbol=f"ALT{index}/USD",
                          asset_id=f"ALT{index}", base=f"ALT{index}", quote="USD", kind="spot")
               for index in range(5)]
    store.save_instruments(catalog)
    recent = MarketSnapshot(instrument_id="a:2", venue="a", bid=99, ask=101, funding_rate=.001,
                            funding_interval_hours=1, source="detailed WS", source_at=at - 1000,
                            received_at=at - 1000, field_timestamps={"funding_rate": at - 120_000})
    store.save_snapshots([recent], archive=False)

    class Adapter:
        def __init__(self):
            self.calls = []

        async def fetch_light(self, selected):
            self.calls.append([item.id for item in selected])
            if len(self.calls) == 1:
                if first_result == "failure":
                    raise ConnectionError("fixture batch unavailable")
                return []
            return [MarketSnapshot(instrument_id=item.id, venue="a", bid=49, ask=51,
                                   source="light REST old source", source_at=at - 60_000,
                                   received_at=at) for item in selected]

    adapter = Adapter()
    collector = Collector(store, {"a": adapter})
    collector.discovered.add("a")
    delays = []

    async def pause(delay):
        delays.append(delay)
        if len(adapter.calls) >= 4:
            collector.stopping.set()

    collector.pause = pause
    await collector.run_light("a", adapter)
    assert adapter.calls == [["a:0", "a:1"], ["a:2", "a:3"], ["a:4"], ["a:0", "a:1"]]
    assert set(collector.snapshots) == {item.id for item in catalog}
    assert delays == [10, 10, 10, 10]
    merged = collector.snapshots["a:2"]
    assert (merged.bid, merged.ask) == (99, 101)
    assert merged.field_timestamps["bid"] == at - 1000
    assert merged.field_timestamps["funding_rate"] == at - 120_000
    assert enrich(merged, 60)["field_stale"]["funding_rate"] is True
    restored = Storage(tmp_path).latest()["a:2"]
    assert restored.bid == 99 and restored.field_timestamps == merged.field_timestamps


@pytest.mark.asyncio
async def test_light_catalog_waits_for_fresh_discovery_then_respects_disable(tmp_path):
    from watch_money.collector import Collector
    from watch_money.models import Settings
    from watch_money.storage import Storage

    store = Storage(tmp_path)
    settings = Settings(enabled_venues=["a"], broad_batch_size=1, history_enabled=False)
    store.set("settings", settings.model_dump())
    store.save_instruments([perpetual("a")])

    class Adapter:
        calls = 0

        async def fetch_light(self, selected):
            self.calls += 1
            settings.broad_collection_enabled = False
            store.set("settings", settings.model_dump())
            return []

    adapter = Adapter()
    collector = Collector(store, {"a": adapter})
    pauses = 0

    async def pause(_):
        nonlocal pauses
        pauses += 1
        if pauses == 1:
            assert adapter.calls == 0, "Persisted catalogs must not bypass this session's discovery"
            collector.discovered.add("a")
        elif pauses == 3:
            collector.stopping.set()

    collector.pause = pause
    await collector.run_light("a", adapter)
    assert adapter.calls == 1
    assert collector.statuses["a"].light_state == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["hyperliquid", "dydx"])
async def test_native_dex_stream_future_timestamp_allows_current_recovery(monkeypatch, venue):
    import json
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from watch_money.adapters import dex_streaming
    from watch_money.adapters.dex import HyperliquidAdapter, DydxAdapter

    at = now_ms()
    monkeypatch.setattr(dex_streaming, "now_ms", lambda: at)
    symbol = "BTC" if venue == "hyperliquid" else "BTC-USD"
    item = Instrument(id=f"{venue}:perpetual:{symbol}", venue=venue, native_symbol=symbol,
                      asset_id="BTC", base="BTC", quote="USD", settle="USDC", kind="perpetual")
    messages = asyncio.Queue()
    if venue == "dydx":
        messages.put_nowait({"type": "connected", "connection_id": "independent"})
        messages.put_nowait({"type": "subscribed", "channel": "v4_markets", "message_id": 1,
                            "connection_id": "independent", "contents": {"markets": {}}})
    for index, (timestamp, price) in enumerate(((at + 86_400_000, 200), (at, 100)), start=2):
        if venue == "hyperliquid":
            message = {"channel": "bbo", "data": {"coin": symbol, "time": timestamp,
                       "bbo": [{"px": str(price), "sz": "1"}, {"px": str(price + 1), "sz": "1"}]}}
        else:
            effective = datetime.fromtimestamp(timestamp / 1000, UTC).isoformat()
            message = {"type": "channel_data", "channel": "v4_markets", "message_id": index,
                       "connection_id": "independent", "contents": {"oraclePrices": {
                           symbol: {"oraclePrice": str(price), "effectiveAt": effective}}}}
        messages.put_nowait(message)

    class Socket:
        async def recv(self):
            return json.dumps(await messages.get())

        async def send(self, _):
            pass

        async def close(self):
            pass

    @asynccontextmanager
    async def connection(_):
        yield Socket()

    rest = HyperliquidAdapter() if venue == "hyperliquid" else DydxAdapter()
    stream = dex_streaming.DexStreamAdapter(rest, connection_factory=connection)
    generator = stream.stream([item])
    try:
        first = await asyncio.wait_for(anext(generator), 1)
        recovered = first if first.source_at == at else await asyncio.wait_for(anext(generator), .1)
        assert recovered.source_at == at
        assert (recovered.bid if venue == "hyperliquid" else recovered.index) == 100
    finally:
        await generator.aclose()
        await rest.close()


def test_delayed_old_light_envelope_does_not_stale_fresh_funding_calculation(tmp_path):
    from watch_money.collector import Collector
    from watch_money.storage import Storage

    at = now_ms()
    request = funding_request(at)
    store = Storage(tmp_path)
    store.save_snapshots(request.snapshots, archive=False)
    collector = Collector(store, {})
    delayed = MarketSnapshot(instrument_id="a", venue="a", last=90, source="delayed light REST",
                             source_at=at - 120_000, received_at=at)
    merged = collector.merge_patch(delayed)
    assert merged.field_timestamps["mark"] == at
    assert merged.field_timestamps["funding_rate"] == at
    assert merged.field_timestamps["last"] == at - 120_000
    request.snapshots = [merged, request.snapshots[1]]
    result = calculate(request)["opportunities"][0]
    assert result["net_profit"] == pytest.approx(0), result["invalid_reasons"]
    assert result["stale"] is False

    # The fix must continue to reject genuinely old funding, even if the
    # ticker envelope and executable books are current.
    merged.field_timestamps["funding_rate"] = at - 120_000
    merged.source_at = at
    old_funding = calculate(request)["opportunities"][0]
    assert old_funding["net_profit"] is None


def test_partial_field_times_do_not_hide_stale_legacy_funding():
    at = now_ms()
    request = funding_request(at)
    request.snapshots[0].source_at = at - 120_000
    request.snapshots[0].field_timestamps = {"mark": at}
    result = calculate(request)["opportunities"][0]
    assert result["net_profit"] is None, "Absent funding field timestamps must inherit the old envelope"


def test_manual_mapping_cannot_hide_conflicting_native_contracts_from_calculation():
    from watch_money.identity import identity_candidates

    request = funding_request(now_ms())
    for index, instrument in enumerate(request.instruments, start=1):
        instrument.chain = "base"
        instrument.native_asset_id = "base:0x" + str(index) * 40
    review = identity_candidates(request.instruments)
    assert len(review) == 1 and not review[0]["comparison_ready"]
    assert "different_native_chain_addresses" in review[0]["blockers"]
    result = calculate(request)["opportunities"][0]
    assert result["net_profit"] is None, "The calculator must enforce the same native identity conflict"
    assert result["status"] == "invalidated"


@pytest.mark.asyncio
async def test_non_watchlist_small_caps_are_visible_without_merging_same_ticker(tmp_path):
    from fastapi.testclient import TestClient
    from watch_money.adapters.cex import CexAdapter
    from watch_money.app import create_app
    from watch_money.models import Settings
    from watch_money.storage import Storage

    class Client:
        precisionMode = 4

        async def load_markets(self, reload):
            return {"PEPE/USD": {"id": "PEPEUSD", "symbol": "PEPE/USD", "base": "PEPE",
                                  "baseId": "PEPE", "quote": "USD", "spot": True, "active": True}}

    instruments = []
    for venue in ("okx", "bybit"):
        adapter = CexAdapter(venue, client_factory=lambda _: Client())
        discovered = await adapter.discover()
        assert len(discovered) == 1
        instruments.extend(discovered)
    assert instruments[0].asset_id != instruments[1].asset_id
    assert all(not item.mapping_verified for item in instruments)
    store = Storage(tmp_path)
    store.save_instruments(instruments)
    store.set("settings", Settings(enabled_venues=["okx", "bybit"], watch_assets=["BTC"],
                                    collection_enabled=False).model_dump())
    at = now_ms()
    store.save_snapshots([MarketSnapshot(instrument_id=item.id, venue=item.venue, last=.00001,
                                        source="synthetic light ticker", source_at=at, received_at=at)
                          for item in instruments], archive=False)
    with TestClient(create_app(tmp_path, start_collector=False, adapters={})) as client:
        markets = client.get("/api/markets").json()
        assert {item["id"] for item in markets["instruments"]} == {item.id for item in instruments}
        assert len(markets["snapshots"]) == 2
        assets = client.get("/api/assets").json()["assets"]
        assert len(assets) == 2
        assert all(not item["verified"] for item in assets)
        candidates = client.get("/api/assets/candidates").json()["candidates"]
        review = next(row for row in candidates if row["kind"] == "ticker_candidate")
        assert set(review["instrument_ids"]) == {item.id for item in instruments}
        assert not review["comparison_ready"] and not review["identity_compatible"]
        rejected = client.put("/api/assets/mappings/batch", json={
            "instrument_ids": [instruments[0].id, "missing-instrument"], "asset_id": "reviewed:PEPE"})
        assert rejected.status_code == 404
        assert client.get("/api/assets").json()["audit"] == []
        assert len(client.get("/api/assets").json()["assets"]) == 2
        confirmed = client.put("/api/assets/mappings/batch", json={
            "instrument_ids": [item.id for item in instruments], "asset_id": "reviewed:PEPE"})
        assert confirmed.status_code == 200
        assert len(confirmed.json()["audit"]) == 2
        assert all(item["mapping_verified"] for item in confirmed.json()["instruments"])
        assert len({item["native_asset_id"] for item in confirmed.json()["instruments"]}) == 2
    restored = Storage(tmp_path).instruments()
    assert {item.asset_id for item in restored.values()} == {"reviewed:PEPE"}
    assert len({item.native_asset_id for item in restored.values()}) == 2


def test_native_pool_api_requires_rpc_and_keeps_estimates_non_executable(tmp_path, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from watch_money.adapters import onchain
    from watch_money.app import create_app
    from watch_money.models import Settings
    from watch_money.storage import Storage
    from test_onchain import FixtureRPC, POOL, T0, BLOCK_HASH

    # Reuse only the canned ABI node; the API and persistence assertions below
    # are independent of the reader owner's unit tests.
    node = FixtureRPC()
    original_reader = onchain.NativePoolReader

    class InjectedReader(original_reader):
        def __init__(self, urls):
            super().__init__(urls, httpx.AsyncClient(transport=httpx.MockTransport(node)))
            self._owns_client = True

    monkeypatch.setattr(onchain, "NativePoolReader", InjectedReader)
    store = Storage(tmp_path)
    store.set("settings", Settings(collection_enabled=False).model_dump())
    payload = {"protocol": "pancakeswap", "chain": "bsc", "version": "v2", "pool_address": POOL}
    app = create_app(tmp_path, start_collector=False, adapters={})
    with TestClient(app) as client:
        assert client.post("/api/pools/native/state", json=payload).status_code == 422
        assert node.calls == []
        settings = client.get("/api/settings").json()
        settings["rpc_urls"] = {"bsc": "https://mock.invalid"}
        assert client.put("/api/settings", json=settings).status_code == 200
        response = client.post("/api/pools/native/quote", json={**payload, "token_in": T0, "amount_in": "100"})
        assert response.status_code == 200, response.text
        quote = response.json()
        assert quote["executable"] is False
        assert quote["gas_included"] is False
        assert quote["amount_out_raw"] == "45351216185496703796"
        assert quote["pool"]["block_hash"] == BLOCK_HASH
        assert quote["pool"]["execution_verified"] is False
        assert quote["pool"]["factory_membership_verified"] is True
        assert client.get("/api/markets").json()["snapshots"] == []
    saved = Storage(tmp_path).get(f"native_pool:bsc:{POOL}")
    assert saved["block_hash"] == BLOCK_HASH and saved["execution_verified"] is False
    assert {call["method"] for call in node.calls} <= {
        "eth_chainId", "eth_getBlockByNumber", "eth_getCode", "eth_call"}


@pytest.mark.asyncio
async def test_bybit_eight_hour_catalog_metadata_reaches_calculator_without_fake_event_time(monkeypatch):
    import ccxt.async_support as ccxt
    from watch_money.adapters import cex
    from test_cex import FakeClient, market

    at = now_ms()
    monkeypatch.setattr(cex, "now_ms", lambda: at)
    native_market = market(swap=True, raw={"fundingInterval": "480"})
    native_market["id"] = "BTCUSDT"
    parser = ccxt.bybit()
    try:
        # CCXT injects the public response's `time` into each ticker before
        # parse_funding_rate. This exercises the installed Bybit parser too.
        funding = parser.parse_funding_rate({"symbol": native_market["id"], "timestamp": at,
                                            "fundingRate": "0.001", "markPrice": "100",
                                            "indexPrice": "100", "nextFundingTime": str(at + 3_600_000)},
                                           native_market)
    finally:
        await parser.close()
    client = FakeClient([native_market])
    client.ticker = {"timestamp": at, "bid": 99, "ask": 100, "last": 100}
    client.funding = funding
    client.interest = {"timestamp": at, "openInterestAmount": 10}
    adapter = cex.CexAdapter("bybit", client_factory=lambda _: client)
    item, = await adapter.discover()
    snap, = await adapter.fetch([item])
    assert snap.funding_interval_hours == 8
    assert snap.field_timestamps["funding_interval_hours"] is None
    assert snap.field_sources["funding_interval_hours"].endswith(":catalog_metadata_observed_at")
    assert snap.field_received_at["funding_interval_hours"] == at
    request = funding_request(at)
    other = request.instruments[1]
    other.asset_id, other.quote, other.settle = item.asset_id, item.quote, item.settle
    request.instruments = [item, other]
    request.buy_instrument_id = item.id
    request.valuation_currency = "USDT"
    request.snapshots[0] = snap
    request.orderbooks[0].instrument_id = item.id
    request.costs[0].instrument_id = item.id
    result = calculate(request)["opportunities"][0]
    assert result["net_profit"] == pytest.approx(0), result["missing_prerequisites"]
    assert len(result["funding_events"]) == 2
    snap.field_received_at["funding_interval_hours"] = at - 6 * 3_600_000 - 1
    expired = calculate(request)["opportunities"][0]
    assert expired["net_profit"] is None
    assert any("funding_interval_hours" in reason for reason in expired["missing_prerequisites"])


@pytest.mark.asyncio
async def test_catalog_funding_rotation_includes_small_caps_and_does_not_refresh_old_prices(tmp_path, monkeypatch):
    from watch_money.adapters import cex
    from watch_money.collector import Collector, enrich
    from watch_money.models import Settings
    from watch_money.storage import Storage
    from test_cex import FakeClient, market

    at = now_ms()
    monkeypatch.setattr(cex, "now_ms", lambda: at)

    class FundingOnlyClient(FakeClient):
        async def fetch_ticker(self, _):
            raise AssertionError("Broad funding must not issue detailed ticker calls")

        async def fetch_open_interest(self, _):
            raise AssertionError("Broad funding must not issue OI calls")

    rows = [market(base=f"ALT{index}", swap=True, raw={"fundingInterval": "480"}) for index in range(3)]
    rows.append(market(base="ALTSPOT"))
    client = FundingOnlyClient(rows)
    client.funding = {"timestamp": at, "fundingRate": .001, "fundingTimestamp": at + 3_600_000,
                      "markPrice": 100, "info": {}}
    adapter = cex.CexAdapter("bybit", client_factory=lambda _: client)
    catalog = await adapter.discover()
    store = Storage(tmp_path)
    store.save_instruments(catalog)
    store.set("settings", Settings(enabled_venues=["bybit"], watch_assets=["BTC"], broad_batch_size=2,
                                    history_enabled=False, funding_poll_seconds=60).model_dump())
    oldest = next(item for item in catalog if item.base == "ALT0")
    store.save_snapshots([MarketSnapshot(instrument_id=oldest.id, venue="bybit", bid=99, ask=101,
                                        source="old ticker", source_at=at - 120_000,
                                        received_at=at - 120_000)], archive=False)
    collector = Collector(store, {"bybit": adapter})
    collector.discovered.add("bybit")
    pauses = []

    async def pause(delay):
        pauses.append(delay)
        # Planned bulk/fallback workers yield independently. Stop after the
        # complete funding catalog is observed, rather than counting idle ticks.
        await asyncio.sleep(0)
        if all(item.id in collector.snapshots and collector.snapshots[item.id].funding_rate is not None
               for item in catalog if item.kind == 'perpetual'):
            collector.stopping.set()

    collector.pause = pause
    await collector.run_light("bybit", adapter, funding=True)
    assert any(delay < 1 for delay in pauses), 'Symbol batches must not each sleep the full 60s cadence'
    assert set(collector.snapshots) == {item.id for item in catalog if item.kind == "perpetual"}
    assert collector.statuses["bybit"].funding_subscriptions == 3
    assert collector.statuses["bybit"].funding_state == "online"
    assert all(snapshot.funding_rate == .001 and snapshot.funding_interval_hours == 8
               for snapshot in collector.snapshots.values())
    merged = collector.snapshots[oldest.id]
    assert merged.bid == 99 and merged.ask == 101
    assert merged.field_timestamps["bid"] == at - 120_000
    assert merged.field_timestamps["funding_rate"] == at
    assert enrich(merged, 60)["stale"] is True


def test_scanner_compares_fresh_funding_with_recently_observed_catalog_interval():
    from watch_money.scanner import scan_markets

    at = now_ms()
    request = funding_request(at)
    for snapshot in request.snapshots:
        snapshot.field_timestamps = {"funding_rate": at, "funding_interval_hours": None}
        snapshot.field_received_at = {"funding_rate": at, "funding_interval_hours": at - 3_600_000}
        snapshot.field_sources = {"funding_interval_hours": "fixture:catalog_metadata_observed_at"}
    result = scan_markets(request.instruments, request.snapshots, at, 60)
    assert len(result["candidates"]) == 2
    for candidate in result["candidates"]:
        assert candidate["funding_8h_rate"] == pytest.approx(0)
        assert candidate["status"] == "indicative"
        assert candidate["net_profit"] is None and candidate["executable"] is False
        assert candidate["funding_rankable"], "Explicitly observed catalog metadata is a valid interval source"


@pytest.mark.parametrize("unknown_field", ["funding_interval_hours", "funding_kind"])
def test_scanner_funding_best_requires_provenance_of_normalization_and_kind(unknown_field):
    from watch_money.scanner import scan_markets

    at = now_ms()
    request = funding_request(at)
    for snapshot in request.snapshots:
        snapshot.field_timestamps = {unknown_field: None}
        snapshot.field_sources = {unknown_field: "unclassified public value"}
    result = scan_markets(request.instruments, request.snapshots, at, 60, sort="funding")
    assert all(candidate["funding_8h_rate"] is not None for candidate in result["candidates"])
    assert all(not candidate["funding_rankable"] for candidate in result["candidates"]), (
        "Unknown interval/kind provenance may support an indicative value, never a reliable best")
    assert result["groups"][0]["best_funding_candidate"] is None


def test_scanner_groups_preserve_field_age_filters_direction_and_scan_scope():
    from watch_money.scanner import scan_markets

    at = now_ms()
    request = funding_request(at)
    instruments = request.instruments + [perpetual("c"), perpetual("d")]
    for item in instruments:
        item.base, item.asset_id, item.native_symbol = "PEPE", "reviewed:PEPE", "PEPE/USD"
    snapshots = [request.snapshots[0].model_copy(deep=True, update={
        "instrument_id": item.id, "venue": item.venue,
        "bid": bid, "ask": ask})
        for item, bid, ask in zip(instruments, [99, 109, 999, 9999], [100, 110, 1000, 10000])]
    snapshots[0].field_timestamps = {"ask": at - 5000}
    snapshots[0].field_received_at = {"ask": at - 2000}
    snapshots[0].field_sources = {"ask": "a:public_ask"}
    snapshots[2].field_timestamps = {"bid": None, "ask": None}
    snapshots[3].field_timestamps = {"bid": at - 120000, "ask": at - 120000}
    snapshots[1].next_funding_at += 3600000
    result = scan_markets(instruments, snapshots, at, 60, q="PEPE/USD", limit=100)
    group = result["groups"][0]
    assert group["market_count"] == 4 and group["scan_complete"]
    assert group["possible_directions"] == group["scanned_directions"] == 12
    best = group["best_spread_candidate"]
    assert (best["buy_instrument_id"], best["sell_instrument_id"]) == ("a", "b")
    assert best["spread_rate"] == pytest.approx(.09)
    assert best["reverse_spread_rate"] == pytest.approx((99 - 110) / 110)
    assert best["settlement_alignment"] == "different"
    assert "settlement_times_differ" in best["warnings"]
    assert "normalized_rates_are_not_actual_settlement_cashflows" in best["warnings"]
    assert best["net_profit"] is None and best["executable"] is False
    detail = next(market for market in group["markets"] if market["instrument_id"] == "a")
    assert detail["fields"]["ask"]["source_at"] == at - 5000
    assert detail["fields"]["ask"]["received_at"] == at - 2000
    assert detail["fields"]["ask"]["age_seconds"] == 5
    assert detail["fields"]["ask"]["source"] == "a:public_ask"
    assert any(row["spread_rate"] is not None and row["spread_rate"] > .09
               and not row["price_rankable"] for row in group["directions"])
    filtered = scan_markets(instruments, snapshots, at, 60, q="PEPE", platforms=["a", "b"])
    assert filtered["groups"][0]["market_count"] == 2
    assert filtered["stats"]["possible_candidates"] == 2
    assert filtered["available_platforms"] == ["a", "b", "c", "d"]
    limited = scan_markets(instruments, snapshots, at, 60, max_candidates=1)
    assert limited["stats"]["enumeration_truncated"]
    assert limited["groups"][0]["best_within_scan"] and not limited["groups"][0]["scan_complete"]
    assert limited["groups"][0]["scanned_directions"] == 1


def test_full_catalog_scanner_api_is_indicative_and_respects_fx_and_switch(tmp_path):
    from fastapi.testclient import TestClient
    from watch_money.app import create_app
    from watch_money.models import Settings
    from watch_money.storage import Storage

    at = now_ms()
    request = funding_request(at)
    for item in request.instruments:
        item.base, item.asset_id = "PEPE", "reviewed:PEPE"
    request.snapshots[0].bid, request.snapshots[0].ask = 99, 100
    request.snapshots[0].funding_interval_hours = 1
    request.snapshots[1].bid, request.snapshots[1].ask = 110, 111
    request.snapshots[1].funding_rate = .016
    store = Storage(tmp_path)
    store.save_instruments(request.instruments)
    store.save_snapshots(request.snapshots, archive=False)
    store.set("settings", Settings(enabled_venues=["a", "b"], watch_assets=["BTC"],
                                    collection_enabled=False).model_dump())
    app = create_app(tmp_path, start_collector=False, adapters={})
    with TestClient(app) as client:
        result = client.get("/api/scanner?sort=funding").json()
        assert result["enabled"] and result["stats"]["catalog_markets"] == 2
        assert len(result["candidates"]) == 2
        leading = result["candidates"][0]
        assert leading["asset_symbols"] == ["PEPE"]
        assert leading["funding_8h_rate"] == pytest.approx(.008)
        assert leading["spread_rate"] == pytest.approx(.1)
        assert leading["net_profit"] is None and not leading["executable"]
        assert "fees_not_configured" in leading["requirements"]
        assert client.get("/api/notifications/history").json()["notifications"] == []
        app.state.collector.instruments["b"].quote = "KRW"
        unmatched_quotes = client.get("/api/scanner").json()["candidates"]
        assert all(candidate["spread_rate"] is None and "quote_fx_not_provided" in candidate["missing"]
                   for candidate in unmatched_quotes)
        settings = client.get("/api/settings").json()
        settings["computation_enabled"] = False
        client.put("/api/settings", json=settings)
        disabled = client.get("/api/scanner").json()
        assert disabled["enabled"] is False and disabled["candidates"] == []


def test_actual_websocket_initial_delta_stale_removal_and_mode_reset(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from watch_money import collector as module
    from watch_money.app import create_app
    from watch_money.models import Settings
    from watch_money.storage import Storage

    clock = [now_ms()]
    monkeypatch.setattr(module, "now_ms", lambda: clock[0])
    store = Storage(tmp_path)
    store.set("settings", Settings(enabled_venues=["a", "b"], collection_enabled=False,
                                    refresh_ms=500, stale_seconds=5).model_dump())
    store.save_instruments([perpetual("a"), perpetual("b")])
    store.save_snapshots([MarketSnapshot(instrument_id=identity, venue=identity, last=100,
                                        source="independent WS fixture", source_at=clock[0], received_at=clock[0])
                          for identity in ("a", "b")], archive=False)
    app = create_app(tmp_path, start_collector=False, adapters={})
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as socket:
            initial = socket.receive_json()
            assert initial["type"] == "markets" and len(initial["snapshots"]) == 2
            unchanged = socket.receive_json()
            assert unchanged["type"] == "markets_delta"
            assert unchanged["instruments"] == unchanged["snapshots"] == []
            app.state.collector.snapshots["a"].last = 102
            changed = socket.receive_json()
            assert [row["instrument_id"] for row in changed["snapshots"]] == ["a"]
            assert changed["snapshots"][0]["last"] == 102
            clock[0] += 6000
            expired = socket.receive_json()
            assert len(expired["snapshots"]) == 2
            assert all(row["stale"] for row in expired["snapshots"])
            del app.state.collector.instruments["b"]
            del app.state.collector.snapshots["b"]
            removed = socket.receive_json()
            assert removed["removed_instruments"] == removed["removed_snapshots"] == ["b"]
            settings = client.get("/api/settings").json()
            settings["demo_mode"] = True
            client.put("/api/settings", json=settings)
            reset = socket.receive_json()
            assert reset["type"] == "markets" and reset["mode"] == "demo"
            assert reset["snapshots"] and all(row["is_demo"] for row in reset["snapshots"])
        with client.websocket_connect("/ws") as reconnected:
            assert reconnected.receive_json()["type"] == "markets"


def test_both_perpetual_legs_configured_holding_costs_reduce_every_horizon_net():
    request = funding_request(now_ms())
    request.costs[0].holding_rate_per_hour = .001
    request.costs[1].holding_rate_per_hour = .002
    result = calculate(request)["opportunities"][0]
    per_hour = 100 * .001 + 99 * .002
    assert result["known_costs"] == pytest.approx(per_hour * 8)
    assert result["net_profit"] == pytest.approx(-per_hour * 8)
    assert result["breakeven_hours"] is None, "A carry path with no positive cash balance never breaks even"
    for row in result["scenarios"]:
        assert row["known_costs"] == pytest.approx(per_hour * row["hold_hours"])
        assert row["net_profit"] == pytest.approx(-per_hour * row["hold_hours"])


def test_holding_costs_apply_native_notional_and_exactly_one_fx_conversion():
    from watch_money.strategies import FXRate

    request = funding_request(now_ms())
    for instrument in request.instruments:
        instrument.quote = instrument.settle = "EUR"
    request.fx_rates = [FXRate(base="EUR", quote="USD", rate=2, source="synthetic FX", at=request.as_of)]
    request.costs[0].holding_rate_per_hour = .001
    request.costs[1].holding_rate_per_hour = .002
    result = calculate(request)["opportunities"][0]
    assert result["known_costs"] == pytest.approx((100 * .001 + 99 * .002) * 8 * 2)
    assert result["net_profit"] == pytest.approx(-4.768)
    assert sum(row["amount"] for row in result["holding_costs"]) == pytest.approx(2.384)
    assert sum(row["valuation_amount"] for row in result["holding_costs"]) == pytest.approx(4.768)


@pytest.mark.parametrize("kind,unit,contract_size,size,minimum", [
    ("perpetual", "contracts", .1, .4, 5),
    ("perpetual", "base", .1, .4, 5),
    ("spot", "base", 1, 4, 5),
])
def test_native_minimum_quantity_is_enforced_in_correct_units(kind, unit, contract_size, size, minimum):
    request = funding_request(now_ms())
    request.size_base = size
    for instrument in request.instruments:
        instrument.kind, instrument.contract_size, instrument.min_amount = kind, contract_size, minimum
    for book in request.orderbooks:
        book.quantity_unit = unit
    if kind == "spot":
        request.strategy = "spot_spread"
    too_small = calculate(request)["opportunities"][0]
    assert too_small["net_profit"] is None
    assert any("minimum order amount" in reason for reason in too_small["invalid_reasons"])
    request.size_base = minimum * contract_size
    exact_minimum = calculate(request)["opportunities"][0]
    assert exact_minimum["net_profit"] is not None, exact_minimum["invalid_reasons"]


def test_funding_view_keeps_unknown_identity_rows_but_excludes_stale_rates_from_matrix():
    from watch_money.funding_view import build_funding_view

    at = now_ms()
    request = funding_request(at)
    request.snapshots[0].funding_interval_hours = 1
    request.snapshots[1].funding_rate = .016
    third = perpetual("c")
    old = MarketSnapshot(instrument_id="c", venue="c", source="old funding merged into new ticker",
                         source_at=at, received_at=at, funding_rate=.99, funding_kind="current",
                         funding_interval_hours=8, field_timestamps={"funding_rate": at - 120_000})
    unknown = perpetual("d").model_copy(update={"asset_id": "d:PEPE", "base": "PEPE", "mapping_verified": False})
    fresh = old.model_copy(update={"instrument_id": "d", "venue": "d", "funding_rate": .02,
                                   "field_timestamps": {"funding_rate": at}})
    result = build_funding_view(request.instruments + [third, unknown], request.snapshots + [old, fresh], at, 60)
    assert result["is_settled_history"] is False and len(result["rows"]) == 4
    by_id = {row["instrument_id"]: row for row in result["rows"]}
    assert by_id["c"]["raw_rate"] is None and by_id["c"]["normalized_8h_rate"] is None
    assert by_id["d"]["raw_rate"] == .02 and by_id["d"]["identity_status"] == "unverified"
    group, = result["groups"]
    assert group["long_instrument_id"] == "a" and group["short_instrument_id"] == "b"
    assert group["spread_8h_rate"] == pytest.approx(.008)
    assert group["net_profit"] is None


@pytest.mark.asyncio
async def test_settled_history_one_page_is_separate_persistent_and_correctable(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from watch_money.adapters.cex import CexAdapter
    from watch_money import funding_history
    from test_cex import FakeClient, market

    at = now_ms()
    monkeypatch.setattr(funding_history, "now_ms", lambda: at)
    native = market(swap=True)

    class HistoryClient(FakeClient):
        calls = 0
        rows = []

        async def fetch_funding_rate_history(self, symbol, since, limit, params):
            self.calls += 1
            assert symbol == native["symbol"] and params["paginate"] is False and limit == 100
            return self.rows

    client = HistoryClient([native])
    client.has["fetchFundingRateHistory"] = True
    adapter = CexAdapter("okx", client_factory=lambda _: client)
    instrument, = await adapter.discover()
    collector = SimpleNamespace(instruments={instrument.id: instrument}, adapters={"okx": adapter})
    service = funding_history.FundingHistoryService(tmp_path, collector)
    empty = await service.get(instrument.id)
    assert empty["records"] == [] and client.calls == 0
    client.rows = [
        {"symbol": native["symbol"], "timestamp": at - 3_600_000, "fundingRate": .8,
         "info": {"instId": native["id"], "fundingTime": str(at - 3_600_000), "fundingRate": ".8", "realizedRate": "-.0002"}},
        {"symbol": native["symbol"], "timestamp": at + 3_600_000, "fundingRate": .1,
         "info": {"instId": native["id"], "fundingTime": str(at + 3_600_000), "realizedRate": ".1"}},
        {"symbol": native["symbol"], "timestamp": at - 2 * 3_600_000, "fundingRate": .1,
         "info": {"instId": native["id"], "fundingTime": str(at - 2 * 3_600_000), "fundingRate": ".1"}},
    ]
    first = await service.get(instrument.id, refresh=True)
    row, = first["records"]
    assert client.calls == 1 and first["summary"]["pages"] == 1
    assert first["summary"]["rejected_count"] == 2
    assert row["funding_rate"] == -.0002 and row["funding_kind"] == "settled"
    assert row["funding_interval_hours"] is None and row["revision"] == 1
    assert first["coverage"]["all_history"] is False
    assert first["summary"]["next_since"] is None  # rejected rows cannot justify skipping ahead
    restored = funding_history.FundingHistoryService(tmp_path, collector)
    assert (await restored.get(instrument.id))["records"] == [row]
    assert client.calls == 1
    client.rows = [dict(client.rows[0], info={**client.rows[0]["info"], "realizedRate": "-.0003"})]
    monkeypatch.setattr(funding_history, "now_ms", lambda: at + 2000)
    corrected = await restored.get(instrument.id, refresh=True)
    revised, = corrected["records"]
    assert revised["funding_rate"] == -.0003 and revised["revision"] == 2
    assert revised["first_received_at"] == row["first_received_at"]
    assert revised["received_at"] == at + 2000
    monkeypatch.setattr(funding_history, "now_ms", lambda: at + 3000)
    unchanged = await restored.get(instrument.id, refresh=True)
    assert len(unchanged["records"]) == 1 and unchanged["records"][0]["revision"] == 2
    assert unchanged["records"][0]["last_seen_at"] == at + 3000
    assert unchanged["records"][0]["received_at"] == at + 2000


@pytest.mark.asyncio
async def test_public_funding_history_api_cache_survives_restart_without_snapshot_substitution(tmp_path):
    from fastapi.testclient import TestClient
    from watch_money.adapters.cex import CexAdapter
    from watch_money.app import create_app
    from watch_money.models import Settings
    from watch_money.storage import Storage
    from test_cex import FakeClient, market

    at = now_ms()
    native = market(swap=True)

    class HistoryClient(FakeClient):
        calls = 0

        async def fetch_funding_rate_history(self, symbol, since, limit, params):
            self.calls += 1
            return [{"symbol": symbol, "timestamp": at - 3_600_000, "fundingRate": .0001,
                     "info": {"symbol": native["id"], "fundingRateTimestamp": str(at - 3_600_000), "fundingRate": ".0001"}}]

    source = HistoryClient([native])
    source.has["fetchFundingRateHistory"] = True
    adapter = CexAdapter("bybit", client_factory=lambda _: source)
    instrument, = await adapter.discover()
    store = Storage(tmp_path)
    store.save_instruments([instrument])
    store.set("settings", Settings(enabled_venues=["bybit"], collection_enabled=False).model_dump())
    path = f"/api/funding/history/{instrument.id}"
    with TestClient(create_app(tmp_path, start_collector=False, adapters={"bybit": adapter})) as client:
        assert client.get(path).json()["records"] == []
        assert source.calls == 0
        refreshed = client.get(path, params={"refresh": True})
        assert refreshed.status_code == 200, refreshed.text
        row, = refreshed.json()["records"]
        assert row["funding_kind"] == "settled" and row["settlement_at"] == at - 3_600_000
        assert source.calls == 1
        assert client.get("/api/history", params={"instrument_id": instrument.id}).json()["snapshots"] == []
        assert client.get("/api/markets").json()["snapshots"] == []
        source.apiKey = "synthetic-credential-must-not-be-used"
        blocked = client.get(path, params={"refresh": True})
        assert blocked.status_code == 409 and source.calls == 1
        source.apiKey = None
    with TestClient(create_app(tmp_path, start_collector=False, adapters={})) as restarted:
        cached = restarted.get(path)
        assert cached.status_code == 200 and cached.json()["records"] == [row]
        assert source.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("queued_state", ["pending", "retry", "sending"])
async def test_restart_never_delivers_expired_opportunity_from_queue(tmp_path, queued_state):
    from watch_money.notifications import NotificationRule, NotificationService

    clock = [now_ms() / 1000]
    service = NotificationService(tmp_path, clock=lambda: clock[0])
    service.save_rule(NotificationRule(id="age-bound", name="Fresh local opportunity", channels=["local"],
                                        min_net_profit=0, max_age_seconds=60))
    created = await service.evaluate({"id": "local-fixture:opportunity", "category": "opportunity",
                                      "type": "opportunity", "source_at": round(clock[0] * 1000),
                                      "valid": True, "status": "threshold_met", "net_profit": 10})
    assert len(created) == 1
    if queued_state == "retry":
        async def failed_mock_send(_channel, _message):
            return True, "synthetic_transport_failure", 2
        service._send = failed_mock_send
        await service.process_pending()
    elif queued_state == "sending":
        assert service._claim(service._due_rows(1)[0])
    assert service.history()["deliveries"][0]["status"] == queued_state
    await service.stop()
    clock[0] += 3600
    restored = NotificationService(tmp_path, clock=lambda: clock[0])
    attempts = []

    async def tracked_mock_send(channel, message):
        attempts.append(channel)
        return False, None, None

    restored._send = tracked_mock_send
    try:
        await restored.start()
        await restored.process_pending()
        delivery = restored.history()["deliveries"][0]
        assert attempts == [], "Send/retry must recheck the persisted event's age after restart"
        assert delivery["status"] not in {"pending", "retry", "sending", "sent"}
        assert delivery["last_error"], "Discarded stale opportunity must remain auditable"
    finally:
        await restored.stop()


@pytest.mark.asyncio
async def test_unsent_expired_notification_allows_new_fresh_same_opportunity(tmp_path):
    from watch_money.notifications import NotificationRule, NotificationService

    clock = [now_ms() / 1000]
    service = NotificationService(tmp_path, clock=lambda: clock[0])
    service.save_rule(NotificationRule(id="fresh-rearm", name="Local fixture", channels=["local"],
                                        max_age_seconds=60, cooldown_seconds=300))
    event = {"id": "unchanged-economics", "category": "opportunity", "type": "opportunity",
             "source_at": round(clock[0] * 1000), "net_profit": 10, "valid": True}
    try:
        assert len(await service.evaluate(event)) == 1
        clock[0] += 3600
        await service.process_pending()
        assert service.history()["deliveries"][0]["status"] == "expired"
        fresh = {**event, "source_at": round(clock[0] * 1000)}
        assert len(await service.evaluate(fresh)) == 1, "Unsent expiry must not permanently suppress fresh observations"
        await service.process_pending()
        assert {row["status"] for row in service.history()["deliveries"]} == {"expired", "sent"}
        # Once delivered, identical observations should still deduplicate.
        clock[0] += 1
        assert await service.evaluate({**fresh, "source_at": round(clock[0] * 1000)}) == []
    finally:
        await service.stop()


def test_fee_api_scopes_public_defaults_and_persists_unknown_override_without_refresh(tmp_path):
    from fastapi.testclient import TestClient
    from watch_money.app import create_app
    from watch_money.storage import Storage

    at = now_ms()
    base = perpetual("normal")
    base.venue, base.native_symbol, base.quote, base.settle = "okx", "BTCUSDT", "USDT", "USDT"
    inverse = base.model_copy(deep=True, update={
        "id": "inverse", "native_symbol": "BTCUSD", "quote": "USD", "settle": "BTC", "linear": False})
    Storage(tmp_path).save_instruments([base, inverse])
    original = None
    with TestClient(create_app(tmp_path, start_collector=False, adapters={})) as client:
        all_profiles = client.get("/api/fees").json()
        assert len(all_profiles["profiles"]) == 112 and not all_profiles["account_api_used"]
        rows = client.get("/api/fees", params={"instrument_id": "normal"}).json()["profiles"]
        original = next(row for row in rows if row["liquidity"] == "taker")["effective"]
        assert original["rate"] is not None and original["source_verified"]
        unsupported = client.get("/api/fees", params={"instrument_id": "inverse"}).json()["profiles"]
        assert all(row["effective"]["rate"] is None and row["effective"]["evidence_status"] == "out_of_scope"
                   for row in unsupported), "USDT linear fees must not bleed into coin-settled inverse contracts"
        invalid = client.put("/api/fees", json={"venue": "okx", "kind": "perpetual",
                            "liquidity": "taker", "rate": .001, "source_verified": True})
        assert invalid.status_code == 422, "Client must not promote its own fee assumption to verified evidence"
        result = client.put("/api/fees", json={"venue": "okx", "kind": "perpetual", "liquidity": "taker",
                             "rate": None, "source": "local independent fixture", "updated_at": at - 10000})
        assert result.status_code == 200
        assert result.json()["profile"]["effective"]["rate"] is None
        assert not result.json()["profile"]["effective"]["source_verified"]
    with TestClient(create_app(tmp_path, start_collector=False, adapters={})) as restored:
        rows = restored.get("/api/fees", params={"instrument_id": "normal"}).json()["profiles"]
        unknown = next(row for row in rows if row["liquidity"] == "taker")
        assert unknown["effective"]["rate"] is None
        assert unknown["effective"]["updated_at"] == at - 10000
        assert unknown["default"] == original
        assert next(row for row in rows if row["liquidity"] == "maker")["override"] is None
        reset = restored.put("/api/fees", json={"venue": "okx", "kind": "perpetual",
                             "liquidity": "taker", "reset": True})
        assert reset.status_code == 200 and reset.json()["profile"]["effective"] == original
        assert {row["action"] for row in reset.json()["audit"]} == {"fee_profile_override", "fee_profile_reset"}
