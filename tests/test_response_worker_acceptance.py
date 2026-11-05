"""Independent worker-response checks using local synthetic state only."""
import asyncio
import json
import threading

import httpx
import pytest
from watch_money.app import create_app
from watch_money.models import Instrument, MarketSnapshot, Settings

AT = 1_800_000_000_000


def seeded_app(tmp_path, monkeypatch):
    for name in ("watch_money.collector", "watch_money.scanner", "watch_money.funding_view"):
        monkeypatch.setattr(f"{name}.now_ms", lambda: AT)
    app = create_app(tmp_path, start_collector=False, adapters={})
    app.state.store.set("settings", Settings(enabled_venues=["alpha", "beta"], broad_collection_enabled=True,
                                             stale_seconds=60).model_dump())
    for venue in ("alpha", "beta"):
        item = Instrument(id=venue, venue=venue, native_symbol="BTC/USD", base="BTC", quote="USD", settle="USD",
                          kind="perpetual", asset_id="BTC", mapping_verified=True)
        snapshot = MarketSnapshot(instrument_id=venue, venue=venue, source="https://synthetic.invalid/公开行情",
            source_at=AT - 1000, received_at=AT, bid=100 if venue == "alpha" else 102,
            ask=101 if venue == "alpha" else 103, funding_rate=.001, funding_interval_hours=8,
            funding_kind="current", protocol_metrics={"原始值": '"quoted"\nline', "missing": None})
        app.state.collector.instruments[item.id] = item
        app.state.collector.snapshots[item.id] = snapshot
    return app


@pytest.mark.parametrize("path,module", [
    ("/api/markets", "watch_money.app"), ("/api/scanner", "watch_money.scanner"),
    ("/api/funding", "watch_money.funding_view"),
])
async def test_worker_json_matches_previous_framework_encoding_for_nonempty_public_responses(tmp_path, monkeypatch, path, module):
    app = seeded_app(tmp_path, monkeypatch)
    before = {key: value.model_dump() for key, value in app.state.collector.snapshots.items()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local-test") as client:
        optimized = await client.get(path)
        # Returning the dict restores the prior FastAPI encoding path without changing business logic.
        monkeypatch.setattr("watch_money.query_work.JSONResponse", lambda content: content)
        prior_encoding = await client.get(path)
    assert optimized.status_code == prior_encoding.status_code == 200
    assert optimized.headers["content-type"] == prior_encoding.headers["content-type"] == "application/json"
    optimized_data, prior_data = optimized.json(), prior_encoding.json()
    if path == '/api/markets':
        # View revisions intentionally advance per build; payload encoding and
        # quote clocks must still be identical across the two encoders.
        assert prior_data.pop('view_seq') > optimized_data.pop('view_seq')
    assert optimized_data == prior_data
    data = optimized.json()
    if path == "/api/markets":
        assert len(data["instruments"]) == len(data["snapshots"]) == 2
        assert data["snapshots"][0]["protocol_metrics"]["原始值"] == '"quoted"\nline'
    elif path == "/api/scanner":
        assert data["candidates"] and data["groups"]
    else:
        assert len(data["rows"]) == 2 and data["matrix_groups"]
    assert {key: value.model_dump() for key, value in app.state.collector.snapshots.items()} == before
    await app.state.query_work.close()
    await app.state.asset_verification.close()


async def test_cancelling_websocket_during_worker_build_never_sends_late_frame_or_starts_another_build(tmp_path, monkeypatch):
    app = seeded_app(tmp_path, monkeypatch)
    started, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    builds, frames = [], []

    def blocked_build():
        builds.append(threading.get_ident())
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(3), "The test must release its blocked worker"
            return {"mode": "live", "instruments": [], "snapshots": []}
        finally:
            loop.call_soon_threadsafe(finished.set)

    class Socket:
        async def receive(self):
            await asyncio.Event().wait()

        async def accept(self):
            pass

        async def send_text(self, payload):
            frames.append(json.loads(payload))

    monkeypatch.setattr(app.state.collector, "markets", blocked_build)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/ws")
    task = asyncio.create_task(endpoint(Socket()))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert builds != [threading.get_ident()]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert not frames and len(builds) == 1
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 1)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await app.state.query_work.close()
    await app.state.asset_verification.close()
    assert not frames and len(builds) == 1


def test_market_view_keeps_captured_members_and_original_times_during_concurrent_catalog_replacement(tmp_path, monkeypatch):
    app = seeded_app(tmp_path, monkeypatch)
    collector = app.state.collector
    for identity, snapshot in collector.snapshots.items():
        collector.snapshots[identity] = snapshot.model_copy(update={"source_at": AT - 120000, "received_at": AT - 119000})
    rendering, mutated = threading.Event(), threading.Event()
    original_dump = Instrument.model_dump
    rendered = []

    def gated_dump(self, *args, **kwargs):
        if not rendered:
            rendered.append(self.id)
            rendering.set()
            assert mutated.wait(2)
        return original_dump(self, *args, **kwargs)

    def replace_catalog():
        assert rendering.wait(2)
        collector.instruments.pop("beta")
        collector.snapshots.pop("beta")
        collector.instruments["alpha"] = collector.instruments["alpha"].model_copy(update={"base": "NEW"})
        collector.snapshots["alpha"] = collector.snapshots["alpha"].model_copy(
            update={"bid": 200, "ask": 201, "source_at": AT, "received_at": AT})
        mutated.set()

    monkeypatch.setattr(Instrument, "model_dump", gated_dump)
    writer = threading.Thread(target=replace_catalog)
    writer.start()
    try:
        result = collector.markets()
    finally:
        mutated.set()
        writer.join(timeout=3)
    assert not writer.is_alive()
    assert {item["id"] for item in result["instruments"]} == {"alpha", "beta"}
    assert all(item["base"] == "BTC" for item in result["instruments"])
    assert {snapshot["instrument_id"] for snapshot in result["snapshots"]} == {"alpha", "beta"}
    assert all(snapshot["stale"] and snapshot["source_at"] == AT - 120000
               and snapshot["received_at"] == AT - 119000 for snapshot in result["snapshots"])
