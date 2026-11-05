import asyncio
import json
import subprocess
import sys
import threading

import pytest

from watch_money.collector import Collector
from watch_money.storage import Storage


def test_settings_reads_no_sql_and_copies_are_isolated(tmp_path, monkeypatch):
    store = Storage(tmp_path)
    sibling = Storage(tmp_path)
    store.set('settings', {'recording_enabled': True, 'enabled_venues': ['okx']})
    with monkeypatch.context() as patch:
        patch.setattr(store, 'connect', lambda: pytest.fail('hot settings read touched SQLite'))
        for _ in range(1000):
            value = store.settings()
            value.enabled_venues.append('bad')
            assert sibling.settings().enabled_venues == ['okx']
    sibling.set('settings', {'recording_enabled': False})
    assert not store.settings().recording_enabled
    result = subprocess.check_output([sys.executable, '-c',
        'import sys; from watch_money.storage import Storage; print(Storage(sys.argv[1]).settings().recording_enabled)',
        str(tmp_path)], text=True)
    assert result.strip() == 'False'


def test_failed_commit_does_not_publish_settings(tmp_path, monkeypatch):
    store = Storage(tmp_path)
    def failed(*_):
        raise OSError('synthetic write failure')
    monkeypatch.setattr(store, '_write_value', failed)
    with pytest.raises(OSError):
        store.set('settings', {'recording_enabled': True})
    assert not store.settings().recording_enabled


@pytest.mark.asyncio
async def test_blocked_writer_does_not_block_loop_readers_and_commit_publishes(tmp_path, monkeypatch):
    store = Storage(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = store._write_value
    def slow(*args):
        entered.set()
        assert release.wait(3)
        original(*args)
    monkeypatch.setattr(store, '_write_value', slow)
    writing = asyncio.create_task(asyncio.to_thread(store.set, 'settings', {'recording_enabled': True}))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # These synchronous reads must finish while writer's commit is held.
        for _ in range(100):
            assert not store.settings().recording_enabled
            await asyncio.sleep(0)
    finally:
        release.set()
        await writing
    assert store.settings().recording_enabled


@pytest.mark.asyncio
async def test_disabled_payload_and_notifications_do_not_connect_sqlite(tmp_path, monkeypatch):
    store = Storage(tmp_path)
    store.set('settings', {'recording_enabled': False, 'notifications_enabled': False})
    collector = Collector(store, {}, notifications=object())
    monkeypatch.setattr(store, 'connect', lambda: pytest.fail('disabled callback touched SQLite'))
    monkeypatch.setattr(collector.recorder, 'configure', lambda **_: pytest.fail('disabled recorder configured'))
    for _ in range(1000):
        await collector.record_payload('okx', 'ws', {'price': 1})
        await collector.event({'type': 'data_stale'})
    assert not collector._record_jobs


@pytest.mark.asyncio
async def test_recording_queue_cancel_disable_and_persistence(tmp_path, monkeypatch):
    store = Storage(tmp_path)
    store.set('settings', {'recording_enabled': True})
    collector = Collector(store, {})
    entered, release = threading.Event(), threading.Event()
    original = collector.recorder.record
    calls = []
    def slow(*args):
        entered.set()
        assert release.wait(3)
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(collector.recorder, 'record', slow)
    first = asyncio.create_task(collector.record_payload('okx', 'ws', {'price': 1}))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        waiting = [asyncio.create_task(collector.record_payload('okx', 'ws', {'price': i})) for i in range(30)]
        await asyncio.sleep(.02)
        assert len(collector._record_jobs) == 16
        assert collector.recording_queue_dropped == 15
        store.set('settings', {'recording_enabled': False})
        stopping = asyncio.create_task(collector.stop())
        await asyncio.sleep(.02)
        assert not stopping.done()  # Running write retains ownership through cancellation.
    finally:
        release.set()
    await asyncio.gather(*waiting)
    await stopping
    assert len(calls) == 1
    assert not collector._record_jobs
    files = list((tmp_path / 'recordings').glob('*.jsonl'))
    assert len(files) == 1 and len(files[0].read_text().splitlines()) == 1
    assert json.loads(files[0].read_text())


@pytest.mark.asyncio
async def test_recording_changes_and_restart_use_committed_settings(tmp_path, monkeypatch):
    store = Storage(tmp_path)
    collector = Collector(store, {})
    configured = []
    configure = collector.recorder.configure
    def tracked(**kw):
        configured.append(kw)
        return configure(**kw)
    monkeypatch.setattr(collector.recorder, 'configure', tracked)
    store.set('settings', {'recording_enabled': True, 'recording_venues': ['okx']})
    await collector.record_payload('bybit', 'ws', {'price': 0})
    await collector.record_payload('okx', 'ws', {'price': 1})
    await collector.record_payload('okx', 'ws', {'price': 2})
    assert len(configured) == 1
    store.set('settings', {'recording_enabled': True, 'recording_max_mb': 20})
    await collector.record_payload('okx', 'ws', {'price': 3})
    assert len(configured) == 2 and configured[-1]['max_total_bytes'] == 20 * 1024 * 1024
    store.set('settings', {'recording_enabled': False})
    await collector.record_payload('okx', 'ws', {'price': 4})
    await collector.stop()
    restored = Collector(Storage(tmp_path), {})
    await restored.record_payload('okx', 'ws', {'price': 5})
    store.set('settings', {'recording_enabled': True})
    await restored.record_payload('okx', 'ws', {'price': 6})
    await restored.stop()
    lines = [line for path in (tmp_path / 'recordings').glob('*.jsonl') for line in path.read_text().splitlines()]
    assert len(lines) == 4
