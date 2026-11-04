import asyncio
import json
import threading
import time

import pytest

from watch_money.query_work import QueryWork
from watch_money.stall_probe import StallProbe, resources, stacks


def test_capture_has_no_locals_source_or_absolute_paths():
    secret_marker = 'test-private-value-must-not-appear'
    captured = stacks(threading.get_ident())
    assert secret_marker not in json.dumps(captured)
    assert captured[0]['role'] == 'event_loop'
    assert all(set(frame) == {'file', 'function', 'line'} and '/' not in frame['file']
               for thread in captured for frame in thread['stack'])
    assert len(captured) <= 16
    assert all(len(thread['stack']) <= 24 for thread in captured)
    assert 'ru_utime' in resources()


def test_shared_gap_is_not_claimed_as_loop_only_block_and_storage_is_bounded():
    probe = StallProbe(interval=.5, threshold=2, cooldown=10, capacity=2)
    probe.heartbeat = 100
    probe.observe(100, 90)  # Loop recovered before observer; observer still sees its own gap.
    probe.observe(101, 90)  # Cooldown suppresses repeated capture.
    assert probe.snapshot()['total_captures'] == 1
    assert probe.snapshot()['events'][0]['kind'] == 'shared_scheduling_gap'
    probe.heartbeat = 110
    probe.observe(113, 112.5)
    probe.heartbeat = 120
    probe.observe(124, 123.5)
    snapshot = probe.snapshot()
    assert snapshot['total_captures'] == 3 and len(snapshot['events']) == 2
    assert all(event['kind'] == 'loop_heartbeat_stall' for event in snapshot['events'])
    snapshot['events'].clear()
    assert len(probe.snapshot()['events']) == 2


@pytest.mark.asyncio
async def test_independent_thread_captures_actual_loop_block_and_stops():
    work = QueryWork(sample_interval=.01, stall_probe=True)
    work.stall_probe = StallProbe(interval=.01, threshold=.04, cooldown=.1)
    work.start()
    await asyncio.sleep(.03)
    # Intentional local-only block, not an exchange/network test.
    time.sleep(.16)
    await asyncio.sleep(.03)
    events = work.diagnostics()['stall_probe']['events']
    assert events
    assert any(event['kind'] == 'loop_heartbeat_stall' for event in events)
    assert any(frame['function'] == 'test_independent_thread_captures_actual_loop_block_and_stops'
               for event in events for thread in event['threads'] if thread['role'] == 'event_loop'
               for frame in thread['stack'])
    await work.close()
    assert not work.stall_probe.thread.is_alive()


@pytest.mark.asyncio
async def test_disabled_by_default_and_normal_heartbeat_does_not_capture():
    work = QueryWork(sample_interval=.01)
    work.start()
    await asyncio.sleep(.03)
    assert work.diagnostics()['stall_probe'] == {'enabled': False}
    await work.close()
    probe = StallProbe()
    probe.heartbeat = 100
    probe.observe(100.5, 100)
    assert probe.snapshot()['events'] == []
