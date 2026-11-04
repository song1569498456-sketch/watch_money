
import pytest
from watch_money.collector import Collector, enrich
from watch_money.models import MarketSnapshot, VenueStatus, now_ms
from watch_money.storage import Storage


def test_partition_retention_preserves_current_history(tmp_path):
    store = Storage(tmp_path)
    current = MarketSnapshot(instrument_id='x', venue='x', source='fixture', last=100,
                             source_at=now_ms(), received_at=now_ms())
    old = current.model_copy(update={'received_at': now_ms() - 40 * 86400_000})
    store.save_snapshots([old, current])
    assert len(store.history('x')) == 2
    assert store.prune(30) == 1
    assert len(store.history('x')) == 1
    assert store.latest()['x'].received_at == current.received_at


@pytest.mark.asyncio
async def test_health_rules_emit_stale_and_disconnected_events(tmp_path):
    store = Storage(tmp_path)
    store.set('settings', {'enabled_venues': ['fixture'], 'stale_seconds': 10})
    events = []

    class Notifications:
        async def evaluate(self, event):
            events.append(event)

    collector = Collector(store, {}, Notifications())
    collector.snapshots = {'x': MarketSnapshot(instrument_id='x', venue='fixture', source='fixture',
                                               source_at=now_ms() - 20_000, received_at=now_ms())}
    collector.statuses = {'fixture': VenueStatus(venue='fixture', state='error', last_received_at=now_ms())}

    async def stop_after_cycle(seconds):
        collector.stopping.set()

    collector.pause = stop_after_cycle
    await collector.watch_health()
    assert {e['type'] for e in events} == {'data_stale', 'disconnected'}
    assert all(e['category'] == 'system' for e in events)
    assert all(abs(e['source_at'] - now_ms()) < 1000 for e in events)


def test_future_source_cannot_hide_old_receive_time():
    snapshot = MarketSnapshot(instrument_id='x', venue='x', source='fixture',
                              source_at=now_ms() + 1000, received_at=now_ms() - 120_000)
    assert enrich(snapshot, 60)['stale'] is True


def test_stream_patch_preserves_funding_age_and_rejects_older_price(tmp_path):
    collector = Collector(Storage(tmp_path), {})
    stamp = now_ms()
    original = MarketSnapshot(instrument_id='x', venue='x', source='REST', last=100,
                              funding_rate=.001, funding_interval_hours=4, source_at=stamp,
                              received_at=stamp, field_timestamps={'last': stamp, 'funding_rate': stamp-120000},
                              field_sources={'last': 'REST ticker', 'funding_rate': 'REST funding'})
    collector.snapshots['x'] = original
    patch = MarketSnapshot(instrument_id='x', venue='x', source='WS', last=90, source_at=stamp-1000,
                           received_at=stamp+1, field_timestamps={'last': stamp-1000},
                           field_sources={'last': 'WS ticker'})
    merged = collector.merge_patch(patch)
    assert merged.last == 100
    assert merged.field_timestamps['last'] == stamp
    assert merged.funding_rate == .001
    assert merged.field_timestamps['funding_rate'] == stamp-120000
