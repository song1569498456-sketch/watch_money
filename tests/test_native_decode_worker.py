import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from watch_money.adapters.dex import HyperliquidAdapter
from watch_money.adapters.dex_streaming import DexStreamAdapter


@pytest.mark.asyncio
async def test_large_decode_off_loop_preserves_socket_receipt_and_all_fields(monkeypatch):
    stream = DexStreamAdapter(HyperliquidAdapter())
    body = {'channel': 'l2Book', 'data': {'coin': 'BTC', 'time': 1000,
            'levels': [[{'px': str(n), 'sz': '2', 'n': 3} for n in range(300)], []]}}
    raw = json.dumps(body)
    entered, release = threading.Event(), threading.Event()
    original = json.loads
    loop_thread = threading.get_ident()
    clock = [2000]
    def decode(value):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(3)
        return original(value)
    async def recv():
        return raw
    monkeypatch.setattr('watch_money.adapters.dex_streaming.json.loads', decode)
    monkeypatch.setattr('watch_money.adapters.dex_streaming.now_ms', lambda: clock[0])
    task = asyncio.create_task(stream._receive(SimpleNamespace(recv=recv), with_receipt=True))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        for _ in range(10):
            clock[0] += 100
            await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    message, receipt = await task
    assert message == body and receipt == 2000 and clock[0] == 3000
    await stream.close()
    assert stream._decode_executor is None


@pytest.mark.asyncio
async def test_cancelled_decode_keeps_worker_until_done_and_can_restart(monkeypatch):
    stream = DexStreamAdapter(HyperliquidAdapter())
    entered, release = threading.Event(), threading.Event()
    original = json.loads
    def decode(raw):
        entered.set()
        assert release.wait(3)
        return original(raw)
    monkeypatch.setattr('watch_money.adapters.dex_streaming.json.loads', decode)
    first = asyncio.create_task(stream._decode_hyperliquid('{"channel":"pong"}'))
    assert await asyncio.to_thread(entered.wait, 2)
    first.cancel()
    await asyncio.sleep(.01)
    assert not first.done() and stream._decode_lock.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await stream.close()
    assert await stream._decode_hyperliquid('{"channel":"pong"}') == {'channel': 'pong'}
    await stream.close()


@pytest.mark.asyncio
async def test_small_control_frames_do_not_start_worker_and_bad_json_still_fails():
    stream = DexStreamAdapter(HyperliquidAdapter())
    frames = iter(['{"channel":"pong"}', 'Websocket connection established.', '{', '[]', 'x' * (2*1024*1024+1)])
    async def recv():
        return next(frames)
    socket = SimpleNamespace(recv=recv)
    assert await stream._receive(socket) == {'channel': 'pong'}
    assert await stream._receive(socket) == {'channel': 'connected'}
    with pytest.raises(json.JSONDecodeError):
        await stream._receive(socket)
    with pytest.raises(TypeError):
        await stream._receive(socket)
    with pytest.raises(ValueError):
        await stream._receive(socket)
    assert stream._decode_executor is None
    await stream.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('catalog', [False, True])
async def test_repeated_cancel_close_and_waiting_decode_cannot_release_worker_early(monkeypatch, catalog):
    from watch_money.adapters.dex_streaming import HyperliquidCatalogStream
    stream = DexStreamAdapter(HyperliquidAdapter())
    parent = HyperliquidCatalogStream(stream.rest_adapter) if catalog else stream
    if catalog:
        parent._streams.append(stream)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def blocked(raw):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        finished.set()
        return {'channel': 'pong'}
    monkeypatch.setattr('watch_money.adapters.dex_streaming.json.loads', blocked)
    async def timed_decode():
        async with asyncio.timeout(.02):
            return await stream._decode_hyperliquid('{}')
    first = asyncio.create_task(timed_decode())
    stream._tasks.append(first)
    assert await asyncio.to_thread(entered.wait, 2)
    try:
        await asyncio.sleep(.04)
        for _ in range(3):
            first.cancel()
            await asyncio.sleep(.005)
        assert not first.done() and stream._decode_lock.locked()
        assert stream._decode_future is not None and not stream._decode_future.cancelled()
        waiting = asyncio.create_task(stream._decode_hyperliquid('{}'))
        closing = asyncio.create_task(parent.close())
        another_close = asyncio.create_task(parent.close())
        for _ in range(3):
            await asyncio.sleep(.005)
            closing.cancel()
        await asyncio.sleep(.005)
        assert not closing.done() and not another_close.done() and not finished.is_set()
        assert len(calls) == 1
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(RuntimeError, match='closing'):
        await waiting
    with pytest.raises(asyncio.CancelledError):
        await closing
    await another_close
    assert finished.is_set() and len(calls) == 1
    assert stream._decode_future is None and stream._decode_executor is None
    assert not stream._tasks
    # A fresh session can create a worker only after complete cleanup.
    assert await stream._decode_hyperliquid('{}') == {'channel': 'pong'}
    await stream.close()
