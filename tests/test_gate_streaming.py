"""Synthetic Gate wire schemas; no live connectivity or production approval proof."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from watch_money.adapters.gate_streaming import CHANNEL, ENDPOINT, GateSpotStreamAdapter, _Inbox, gate_spot_patch
from watch_money.models import Instrument

AT = 1_800_000_000_000


def item(symbol='PEPE'):
    return Instrument(id=f'gate:{symbol}', venue='gate', native_symbol=f'{symbol}/USDT',
                      asset_id=symbol, base=symbol, quote='USDT', kind='spot')


def frame(symbol='PEPE', **updates):
    row = {'s': f'{symbol}_USDT', 't': AT, 'u': 5, 'b': '100', 'B': '2', 'a': '101', 'A': '3'}
    row.update(updates)
    return {'channel': CHANNEL, 'event': 'update', 'time_ms': AT + 999, 'result': row}


def rest():
    return SimpleNamespace(_resolve=lambda instrument: ('gate', {'id': instrument.native_symbol.replace('/', '_')}))


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr('watch_money.adapters.gate_streaming.now_ms', lambda: AT + 1000)


def test_native_book_time_not_envelope_receipt():
    patch = gate_spot_patch(item(), frame(), AT + 1000)
    assert patch.source_at == AT
    assert patch.field_timestamps == {'bid': AT, 'ask': AT}
    assert patch.received_at == AT + 1000
    assert patch.bid == 100 and patch.ask == 101
    assert patch.field_sources['bid'].startswith('gate:spot.book_ticker:PEPE_USDT')


@pytest.mark.parametrize('updates', [{'t': None}, {'t': AT / 1000}, {'t': AT + 1001}, {'t': True},
                                     {'b': None}, {'B': 'nan'}, {'a': '-1'}, {'A': None}, {'u': None}])
def test_missing_malformed_or_future_book_fields_rejected(updates):
    assert gate_spot_patch(item(), frame(**updates), AT + 1000) is None


@pytest.mark.parametrize('updates,clears', [({'b': '0'}, ['bid']), ({'A': '0'}, ['ask']),
                                          ({'B': '0', 'A': '0'}, ['bid', 'ask'])])
def test_explicit_zero_side_clear(updates, clears):
    patch = gate_spot_patch(item(), frame(**updates), AT + 1000)
    assert patch.cleared_fields == clears
    for field in clears:
        assert getattr(patch, field) is None
        assert patch.field_sources[field].endswith(':explicit_unavailable')


def test_crossed_book_retains_invalid_status():
    assert not gate_spot_patch(item(), frame(b='102'), AT + 1000).valid


@pytest.mark.asyncio
async def test_bounded_inbox_equal_time_clears_and_later_recovery():
    inbox = _Inbox(1)
    empty = gate_spot_patch(item(), frame(B='0'), AT + 1)
    positive = gate_spot_patch(item(), frame(a='102'), AT + 2)
    for _ in range(100):
        inbox.put(empty)
        inbox.put(positive)
    assert len(inbox.pending) == 1
    merged = await inbox.get()
    assert merged.bid is None and merged.ask == 102
    assert merged.field_received_at['bid'] == AT + 1
    inbox.put(merged)
    inbox.put(gate_spot_patch(item(), frame(t=AT + 1), AT + 3))
    assert (await inbox.get()).bid == 100


class Socket:
    def __init__(self, frames=(), auto_pong=True):
        self.incoming = asyncio.Queue()
        for value in frames:
            self.incoming.put_nowait(value)
        self.sent = []
        self.closed = False
        self.auto_pong = auto_pong

    async def recv(self):
        value = await self.incoming.get()
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)

    async def send(self, raw):
        value = json.loads(raw)
        self.sent.append(value)
        if value['channel'] == 'spot.ping' and self.auto_pong:
            self.incoming.put_nowait({'channel': 'spot.pong'})


def factory(socket):
    @asynccontextmanager
    async def connect(endpoint):
        assert endpoint == ENDPOINT
        try:
            yield socket
        finally:
            socket.closed = True
    return connect


@pytest.mark.asyncio
async def test_exact_native_identity_catalog_subscription_and_close():
    socket = Socket([frame('WRONG'), frame('SMALL')])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket), subscription_batch=1, subscription_delay=0)
    items = [item('PEPE'), item('SMALL')]
    stream = adapter.stream(items)
    patch = await asyncio.wait_for(anext(stream), 1)
    assert patch.instrument_id == 'gate:SMALL'
    await asyncio.sleep(.01)
    assert [request['payload'] for request in socket.sent] == [['PEPE_USDT'], ['SMALL_USDT']]
    assert all('auth' not in request for request in socket.sent)
    await stream.aclose()
    assert socket.closed and not adapter._tasks and not adapter._running
    assert not adapter.supports_instrument(item().model_copy(update={'kind': 'perpetual'}))


@pytest.mark.asyncio
async def test_future_and_older_frames_cannot_pollute_watermark():
    socket = Socket([frame(t=AT + 9999), frame(t=AT, b='100'), frame(t=AT - 1, b='99'), frame(t=AT + 1, u=6, b='101')])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    stream = adapter.stream([item()])
    patch = await asyncio.wait_for(anext(stream), 1)
    assert patch.source_at == AT + 1 and patch.bid == 101
    await stream.aclose()


@pytest.mark.asyncio
async def test_reader_keeps_clear_barrier_after_consumption():
    socket = Socket([frame(B='0')])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    stream = adapter.stream([item()])
    assert (await anext(stream)).bid is None
    socket.incoming.put_nowait(frame(a='102'))
    assert (await anext(stream)).bid is None
    socket.incoming.put_nowait(frame(t=AT + 1, u=6))
    assert (await anext(stream)).bid == 100
    await stream.aclose()


@pytest.mark.asyncio
async def test_observer_backpressure_does_not_block_heartbeat_or_original_receipt():
    socket = Socket([frame(t=AT + n, u=5 + n, b=str(90 + n)) for n in range(100)])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket), heartbeat_seconds=.01, heartbeat_timeout=.1)
    blocked = asyncio.Event()
    async def observer(*args):
        await blocked.wait()
    adapter.on_payload = observer
    stream = adapter.stream([item()])
    patch = await asyncio.wait_for(anext(stream), 1)
    await asyncio.sleep(.05)
    assert adapter.payload_observer_dropped > 0
    assert any(value['channel'] == 'spot.ping' for value in socket.sent)
    assert patch.received_at == AT + 1000
    assert len(adapter._observer_queue._queue) <= 16
    await stream.aclose()
    assert socket.closed


@pytest.mark.asyncio
async def test_heartbeat_failure_reaches_consumer_and_cancellation_closes():
    socket = Socket(auto_pong=False)
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket), heartbeat_seconds=.001, heartbeat_timeout=.01)
    stream = adapter.stream([item()])
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(stream), 1)
    assert socket.closed
    socket = Socket()
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    stream = adapter.stream([item()])
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert socket.closed and not adapter._tasks


@pytest.mark.asyncio
async def test_error_ack_propagates_without_error_payload_leak():
    socket = Socket([{'channel': CHANNEL, 'event': 'subscribe', 'error': {'message': 'private-ish server content'}}])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    with pytest.raises(RuntimeError, match='rejected public subscription'):
        await anext(adapter.stream([item()]))
    assert socket.closed


@pytest.mark.asyncio
async def test_lower_sequence_rejected_and_future_does_not_advance_sequence():
    socket = Socket([frame(u=8), frame(u=999, t=AT + 9999), frame(u=7, b='99'), frame(u=9, a='102')])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    stream = adapter.stream([item()])
    patch = await anext(stream)
    assert patch.bid == 100 and patch.ask == 102
    await stream.aclose()


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_connection_teardown():
    entered, release = asyncio.Event(), asyncio.Event()
    socket = Socket()
    @asynccontextmanager
    async def connect(endpoint):
        try:
            yield socket
        finally:
            entered.set()
            await release.wait()
            socket.closed = True
    adapter = GateSpotStreamAdapter(rest(), connection_factory=connect)
    stream = adapter.stream([item()])
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(.01)
    pending.cancel()
    await entered.wait()
    pending.cancel()
    await asyncio.sleep(0)
    assert not pending.done() and not socket.closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert socket.closed and not adapter._running


@pytest.mark.asyncio
async def test_external_close_unblocks_waiting_consumer():
    socket = Socket()
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    stream = adapter.stream([item()])
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(.01)
    await adapter.close()
    with pytest.raises(RuntimeError, match='closed'):
        await asyncio.wait_for(pending, 1)
    assert socket.closed


@pytest.mark.asyncio
async def test_duplicate_advances_sequence_without_refreshing_receipt():
    socket = Socket([frame(u=5), frame(u=10), frame(u=9, b='99')])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    stream = adapter.stream([item()])
    patch = await anext(stream)
    assert patch.bid == 100
    await stream.aclose()


@pytest.mark.asyncio
async def test_same_sequence_cannot_renew_book_clock():
    socket = Socket([frame(u=5), frame(t=AT + 10, u=5, b='99')])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    stream = adapter.stream([item()])
    patch = await asyncio.wait_for(anext(stream), 1)
    assert patch.source_at == AT and patch.bid == 100
    await stream.aclose()


@pytest.mark.asyncio
async def test_duplicate_burst_yields_to_event_loop():
    socket = Socket([frame() for _ in range(1000)])
    adapter = GateSpotStreamAdapter(rest(), connection_factory=factory(socket))
    inbox, pong = _Inbox(1), asyncio.Event()
    reader = asyncio.create_task(adapter._reader(socket, {'PEPE_USDT': item()}, inbox, pong))
    await asyncio.sleep(0)
    # Reader yielded before exhausting buffered duplicate frames.
    assert socket.incoming.qsize() > 0
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
