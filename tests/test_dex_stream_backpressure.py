"""Offline native source lifecycle regressions; never contact an exchange."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from test_dex_catalog_streaming import book
from test_dex_streaming import EVENT, Socket, connector, instrument
from watch_money.adapters.dex import HyperliquidAdapter
from watch_money.adapters.dex_streaming import (
    ApplicationPongTimeout,
    ConnectionOpenTimeout,
    DexStreamAdapter,
    SourceReadTimeout,
    SubscriptionSendTimeout,
    _HyperliquidInbox,
)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("watch_money.adapters.dex_streaming.now_ms", lambda: EVENT + 1000)


@pytest.mark.asyncio
async def test_stalled_consumer_keeps_reading_pongs_and_bounds_snapshot_backlog():
    socket = Socket([book()])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]),
                              heartbeat_seconds=.005, heartbeat_timeout=.02, hyperliquid_book_channel="l2Book")
    generator = stream.stream([instrument()])
    assert (await anext(generator)).source_at == EVENT
    # Do not request another patch while source frames and application pongs flow.
    for index in range(1, 301):
        socket.events.put_nowait(book(at=EVENT + index))
    await asyncio.sleep(.06)
    assert len(stream._inbox.pending) <= stream._inbox.capacity == 16
    assert stream._inbox.coalesced > 250
    assert sum(message == {"method": "ping"} for message in socket.sent) >= 2
    assert all(not task.done() for task in stream._tasks)
    # The retained source stamp remains upstream time, never receipt time.
    patch = await asyncio.wait_for(anext(generator), .2)
    assert EVENT < patch.source_at <= EVENT + 300
    await generator.aclose()
    assert socket.closed and not stream._tasks


def test_pressure_preserves_newer_trustworthy_book_and_separate_sparse_contexts():
    inbox = _HyperliquidInbox(3)
    current = book(at=EVENT + 20)
    def context(values):
        return {"channel": "activeAssetCtx", "data": {"coin": "BTC", "ctx": values}}
    inbox.put(current)
    inbox.put(context({"funding": ".01"}))
    inbox.put(context({"markPx": "100"}))
    inbox.put(book(at=EVENT + 10))
    assert len(inbox.pending) == 3
    inbox.put(book(at=EVENT + 10_000))
    assert len(inbox.pending) == 3
    retained = next(message for message, _ in inbox.pending if message["channel"] == "l2Book")
    assert retained["data"]["time"] == EVENT + 20
    assert {tuple(message["data"]["ctx"]) for message, _ in inbox.pending if message["channel"] == "activeAssetCtx"} == {
        ("funding",), ("markPx",)}
    cleared = book(at=EVENT + 21)
    cleared["data"]["levels"] = [[], []]
    inbox.put(cleared)
    retained = next(message for message, _ in inbox.pending if message["channel"] == "l2Book")
    assert retained["data"]["levels"] == [[], []]


@pytest.mark.asyncio
async def test_source_read_runs_before_subscription_sends_complete():
    consumed = asyncio.Event()

    class InterlockedSocket(Socket):
        async def recv(self):
            result = await super().recv()
            consumed.set()
            return result

        async def send(self, text):
            await asyncio.wait_for(consumed.wait(), .2)
            await super().send(text)

    socket = InterlockedSocket([book()])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]))
    generator = stream.stream([instrument()])
    assert (await asyncio.wait_for(anext(generator), .5)).bid == 100
    assert len(socket.sent) == 2
    await generator.aclose()


@pytest.mark.asyncio
async def test_missing_application_pong_has_stage_and_closes_reader():
    socket = Socket(auto_pong=False)
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]),
                              heartbeat_seconds=.005, heartbeat_timeout=.005)
    with pytest.raises(ApplicationPongTimeout, match="hyperliquid: application_pong timed out"):
        await anext(stream.stream([instrument()]))
    assert socket.closed and not stream._tasks and not stream._running


@pytest.mark.asyncio
async def test_subscription_send_timeout_has_stage_and_closes_reader():
    class StalledSocket(Socket):
        async def send(self, text):
            await asyncio.Event().wait()

    socket = StalledSocket()
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]), handshake_timeout=.005)
    with pytest.raises(SubscriptionSendTimeout, match="hyperliquid: subscription_send timed out"):
        await anext(stream.stream([instrument()]))
    assert socket.closed and not stream._tasks and not stream._running


@pytest.mark.asyncio
async def test_connection_open_timeout_is_distinct():
    @asynccontextmanager
    async def failed_open(endpoint):
        raise TimeoutError("fixture transport open timeout")
        yield  # pragma: no cover

    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=failed_open)
    with pytest.raises(ConnectionOpenTimeout, match="hyperliquid: connection_open timed out"):
        await anext(stream.stream([instrument()]))
    assert not stream._tasks and not stream._running


@pytest.mark.asyncio
async def test_transport_read_timeout_is_distinct():
    socket = Socket([TimeoutError("fixture source read timeout")])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]))
    with pytest.raises(SourceReadTimeout, match="hyperliquid: source_read timed out"):
        await anext(stream.stream([instrument()]))
    assert socket.closed and not stream._tasks


@pytest.mark.asyncio
async def test_queued_snapshot_retains_arrival_receipt_and_future_at_arrival_is_not_renewed(monkeypatch):
    current_time = [EVENT]
    monkeypatch.setattr("watch_money.adapters.dex_streaming.now_ms", lambda: current_time[0])
    socket = Socket([book()])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]))
    generator = stream.stream([instrument()])
    assert (await anext(generator)).received_at == EVENT
    socket.events.put_nowait(book(at=EVENT + 100))
    await asyncio.sleep(0)
    assert stream._inbox.pending
    current_time[0] = EVENT + 1000
    patch = await anext(generator)
    assert patch.source_at == EVENT + 100
    assert patch.received_at == EVENT and patch.field_received_at == {"bid": EVENT, "ask": EVENT}
    assert patch.source_at > patch.received_at
    await generator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_frame", [False, True])
async def test_slow_observer_is_bounded_drops_explicitly_and_never_blocks_pongs(extra_frame):
    entered = asyncio.Event()

    async def observer(*args):
        entered.set()
        await asyncio.Event().wait()

    fence = object()
    class FencedSocket(Socket):
        def __init__(self):
            super().__init__([book()])
            self.ping_sent = asyncio.Event()
            self.paused = asyncio.Event()
            self.pongs_received = 0
        async def send(self, text):
            await super().send(text)
            if json.loads(text) == {"method": "ping"}:
                self.ping_sent.set()
        async def recv(self):
            value = await self.events.get()
            if value is fence:
                # All earlier frames were handled; later heartbeat pongs may
                # queue but cannot change overflow between baseline and close.
                self.paused.set()
                await asyncio.Event().wait()
            if value.get("channel") == "pong":
                self.pongs_received += 1
            return json.dumps(value)
    socket = FencedSocket()
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]),
                              heartbeat_seconds=.005, heartbeat_timeout=5)
    stream.on_payload = observer
    generator = stream.stream([instrument()])
    assert (await anext(generator)).bid == 100
    await entered.wait()
    for index in range(200):
        socket.events.put_nowait(book(at=EVENT + index))
    await asyncio.wait_for(socket.ping_sent.wait(), 1)
    if extra_frame:
        socket.events.put_nowait({"channel": "pong"})
    socket.events.put_nowait(fence)
    await asyncio.wait_for(socket.paused.wait(), 1)
    assert socket.pongs_received >= 1  # processed before reaching the fence
    assert stream._observer_queue.qsize() == 64
    assert stream.payload_observer_dropped >= 136
    assert all(not task.done() for task in stream._tasks)
    skipped_before_close = stream.payload_observer_dropped
    await generator.aclose()
    # Closing discards 64 queued callbacks and conservatively counts the one
    # in-flight callback, which may have performed a partial write already.
    assert stream.payload_observer_dropped == skipped_before_close + 65
    assert stream._observer_queue is None and not stream._tasks and socket.closed
    await stream.close()
    assert stream.payload_observer_dropped == skipped_before_close + 65
