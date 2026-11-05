"""Synthetic scheduling and exact-expiry regressions; no live exchange claims."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot, Settings
from watch_money.storage import Storage

AT = 1_800_000_000_000


def setup(tmp_path, monkeypatch, count=1):
    clock = [AT]
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: clock[0])
    store = Storage(tmp_path)
    store.set('settings', Settings(enabled_venues=['x'], notifications_enabled=False).model_dump())
    collector = Collector(store, {'x': SimpleNamespace()})
    for n in range(count):
        key = str(n)
        collector.instruments[key] = Instrument(id=key, venue='x', native_symbol=key, base=key,
            quote='USD', asset_id=key, kind='perpetual')
        collector.snapshots[key] = MarketSnapshot(instrument_id=key, venue='x', source='synthetic',
            bid=10, ask=11, funding_rate=.01, funding_kind='current', funding_interval_hours=8,
            source_at=AT, received_at=AT)
    return collector, clock


def test_cache_exact_expiry_settings_replacement_and_pruning(tmp_path, monkeypatch):
    collector, clock = setup(tmp_path, monkeypatch, 2)
    calls = []
    original = collector._coverage_counts
    monkeypatch.setattr(collector, '_coverage_counts', lambda *args: (calls.append(args[0].id), original(*args))[1])
    collector.refresh_status_coverage()
    collector.refresh_status_coverage()
    assert len(calls) == 2
    clock[0] = AT + 60000
    collector.refresh_status_coverage()
    assert len(calls) == 2 and collector.statuses['x'].funding_fresh_markets == 2
    clock[0] += 1
    collector.refresh_status_coverage()
    assert len(calls) == 4 and collector.statuses['x'].funding_fresh_markets == 0
    # New field data and a stricter setting invalidate the memo immediately.
    collector.snapshots['0'] = collector.snapshots['0'].model_copy(update={'source_at': clock[0], 'received_at': clock[0]})
    collector.refresh_status_coverage()
    assert len(calls) == 5 and collector.statuses['x'].funding_fresh_markets == 1
    collector.store.set('settings', Settings(enabled_venues=['x'], stale_seconds=5).model_dump())
    collector.refresh_status_coverage()
    assert len(calls) == 7
    del collector.instruments['1']
    collector.refresh_status_coverage()
    assert set(collector._coverage_cache) == {'0'}
    # Time moving backwards must not reuse an expired classification.
    clock[0] = AT
    collector.refresh_status_coverage()
    assert len(calls) == 8 and collector.statuses['x'].funding_fresh_markets == 0


def test_future_and_metadata_boundaries_are_exact(tmp_path, monkeypatch):
    collector, clock = setup(tmp_path, monkeypatch)
    collector.snapshots['0'] = collector.snapshots['0'].model_copy(update={'source_at': AT+1000, 'received_at': AT+1000})
    collector.refresh_status_coverage()
    assert collector.statuses['x'].light_fresh_markets == 0
    clock[0] += 1000
    collector.refresh_status_coverage()
    assert collector.statuses['x'].light_fresh_markets == 1
    row = collector.snapshots['0'].model_copy(update={
        'field_timestamps': {'funding_interval_hours': None},
        'field_received_at': {'funding_interval_hours': clock[0]-6*3600000},
        'field_sources': {'funding_interval_hours': 'public:catalog_metadata_observed_at'}})
    collector.snapshots['0'] = row
    collector.refresh_status_coverage()
    assert collector.statuses['x'].funding_fresh_markets == 1
    clock[0] += 1
    collector.refresh_status_coverage()
    assert collector.statuses['x'].funding_fresh_markets == 0


@pytest.mark.asyncio
async def test_disabled_health_skips_serialization_and_scan(tmp_path, monkeypatch):
    collector, _ = setup(tmp_path, monkeypatch, 1000)
    collector.notifications = SimpleNamespace()
    def forbidden(*args):
        raise AssertionError('disabled health must not scan stale fields or serialize snapshots')
    monkeypatch.setattr('watch_money.collector.enrich', forbidden)
    monkeypatch.setattr('watch_money.collector.field_stale', forbidden)
    async def stop(_):
        collector.stopping.set()
    collector.pause = stop
    await collector.watch_health()
    assert collector.statuses['x'].light_fresh_markets == 1000


@pytest.mark.asyncio
async def test_background_coverage_keeps_loop_responsive_and_cancellation_drains(tmp_path, monkeypatch):
    collector, _ = setup(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(_venue=None):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(collector, 'refresh_status_coverage', slow)
    task = asyncio.create_task(collector._refresh_status_coverage_async())
    await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
    ticks = 0
    for _ in range(10):
        await asyncio.sleep(.001)
        ticks += 1
    task.cancel()
    await asyncio.sleep(.01)
    assert not task.done() and ticks == 10
    other = asyncio.create_task(collector._refresh_status_coverage_async())
    await asyncio.sleep(.01)
    assert len(calls) == 1  # Admission stays held until the old worker really ends.
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await other
    assert len(calls) == 2 and all(ident != threading.get_ident() for ident in calls)


@pytest.mark.asyncio
async def test_health_setting_change_stops_old_scope_events_without_enrich(tmp_path, monkeypatch):
    collector, clock = setup(tmp_path, monkeypatch, 3)
    collector.store.set('settings', Settings(enabled_venues=['x'], notifications_enabled=True).model_dump())
    clock[0] += 61000
    collector.statuses['x'].state = 'error'
    collector.statuses['x'].last_received_at = AT
    events = []
    class Notifications:
        async def evaluate(self, event):
            events.append(event)
            collector.store.set('settings', Settings(enabled_venues=[], notifications_enabled=True).model_dump())
    collector.notifications = Notifications()
    def forbidden(*args):
        raise AssertionError('health must not serialize full snapshots')
    monkeypatch.setattr('watch_money.collector.enrich', forbidden)
    async def stop(_):
        collector.stopping.set()
    collector.pause = stop
    await collector.watch_health()
    assert len(events) == 1 and events[0]['type'] == 'data_stale'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['refresh', 'remove_snapshot', 'retire', 'remove_instrument', 'still_stale'])
async def test_health_reloads_after_yield_before_stale_alert(tmp_path, monkeypatch, change):
    collector, clock = setup(tmp_path, monkeypatch)
    collector.store.set('settings', Settings(enabled_venues=['x'], notifications_enabled=True).model_dump())
    clock[0] += 61000
    events = []
    async def evaluate(event):
        events.append(event)
    collector.notifications = SimpleNamespace(evaluate=evaluate)
    async def coverage():
        pass
    async def stop(_):
        collector.stopping.set()
    collector._refresh_status_coverage_async = coverage
    collector.pause = stop
    async def replace_during_yield():
        if change == 'refresh':
            collector.snapshots['0'] = collector.snapshots['0'].model_copy(update={
                'source_at': clock[0], 'received_at': clock[0]})
        elif change == 'remove_snapshot':
            del collector.snapshots['0']
        elif change == 'remove_instrument':
            del collector.instruments['0']
        elif change == 'retire':
            collector.instruments['0'] = collector.instruments['0'].model_copy(update={'active': False})
        else:
            collector.snapshots['0'] = collector.snapshots['0'].model_copy(update={'bid': 9})
    # health reaches its explicit yield first; replacement then precedes resume.
    await asyncio.gather(collector.watch_health(), replace_during_yield())
    assert len(events) == (1 if change == 'still_stale' else 0)


@pytest.mark.asyncio
async def test_final_stale_guard_rejects_replaced_version_and_real_notifications_dedupe(tmp_path, monkeypatch):
    from watch_money.notifications import NotificationRule, NotificationService
    collector, clock = setup(tmp_path, monkeypatch)
    collector.store.set('settings', Settings(enabled_venues=['x'], notifications_enabled=True).model_dump())
    clock[0] += 61000
    service = NotificationService(tmp_path, clock=lambda: clock[0] / 1000)
    service.save_rule(NotificationRule(name='stale', category='system', event_types=['data_stale'], cooldown_seconds=0))
    collector.notifications = service
    old = collector.snapshots['0']
    def event():
        return {'id': 'stale:0', 'category': 'system', 'type': 'data_stale', 'platforms': ['x'],
                'source_at': clock[0], 'message': 'Stale market data: 0'}
    fresh = old.model_copy(update={'source_at': clock[0], 'received_at': clock[0]})
    collector.snapshots['0'] = fresh
    await collector.event(event(), expected_snapshot=old)
    await collector.event(event(), expected_snapshot=fresh)
    assert not service.history()['notifications']
    # Genuine later expiry still alerts; checking again does not duplicate it.
    clock[0] += 61000
    await collector.event(event(), expected_snapshot=fresh)
    clock[0] += 5000
    await collector.event(event(), expected_snapshot=fresh)
    assert len(service.history()['notifications']) == 1
    del collector.snapshots['0']
    await collector.event(event(), expected_snapshot=fresh)
    assert len(service.history()['notifications']) == 1
    await service.stop()


@pytest.mark.asyncio
async def test_cancel_health_during_yield_does_not_enqueue(tmp_path, monkeypatch):
    collector, clock = setup(tmp_path, monkeypatch)
    collector.store.set('settings', Settings(enabled_venues=['x'], notifications_enabled=True).model_dump())
    clock[0] += 61000
    events = []
    async def evaluate(event):
        events.append(event)
    async def coverage():
        pass
    collector.notifications = SimpleNamespace(evaluate=evaluate)
    collector._refresh_status_coverage_async = coverage
    task = asyncio.create_task(collector.watch_health())
    # Parent resumes after health has yielded but before its scan resumes.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == []
