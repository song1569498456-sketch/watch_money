"""Synthetic stream work bounds; these tests cannot diagnose a live disconnect."""
import asyncio
from types import SimpleNamespace

import pytest

from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot, Settings, now_ms
from watch_money.storage import Storage


def setup(tmp_path, count=347):
    store = Storage(tmp_path)
    store.set('settings', Settings(enabled_venues=['alpha'], watch_assets=['BTC'],
                                  history_enabled=False, poll_seconds=3600).model_dump())
    items = [Instrument(id=f'a:{index:04}', venue='alpha', native_symbol=f'SMALL{index}',
                        base=f'SMALL{index}', quote='USDC', asset_id=f'synthetic:{index}',
                        kind='perpetual') for index in range(count)]
    store.save_instruments(items)
    collector = Collector(store, {'alpha': SimpleNamespace()})
    collector.discovered.add('alpha')
    return collector, store, items


@pytest.mark.asyncio
async def test_ten_thousand_patches_bound_settings_and_full_catalog_signatures(tmp_path, monkeypatch):
    collector, store, items = setup(tmp_path)
    clock = [0.0]
    reads = serializations = 0
    original_settings, original_dump = store.settings, Instrument.model_dump_json

    def settings():
        nonlocal reads
        reads += 1
        return original_settings()

    def dump(item, *args, **kwargs):
        nonlocal serializations
        serializations += 1
        return original_dump(item, *args, **kwargs)

    monkeypatch.setattr('watch_money.collector.monotonic', lambda: clock[0])
    monkeypatch.setattr(store, 'settings', settings)
    monkeypatch.setattr(store, 'save_snapshots', lambda *_: None)
    monkeypatch.setattr(Instrument, 'model_dump_json', dump)
    # Isolate selection overhead. Correct merge/provenance has separate tests.
    monkeypatch.setattr(collector, 'merge_patch', lambda patch: patch)
    patch = MarketSnapshot(instrument_id=items[0].id, venue='alpha', bid=1, ask=2,
                           source='synthetic', source_at=now_ms())

    class Stream:
        catalog_stream = True

        async def stream(self, selected):
            assert len(selected) == 347
            for index in range(10_000):
                clock[0] = index / 5000  # Two synthetic seconds, regardless of CPU speed.
                if index == 9999:
                    collector.stopping.set()
                yield patch.model_copy(update={'instrument_id': items[index % len(items)].id})

        async def close(self):
            pass

    await asyncio.wait_for(collector.run_stream('alpha', Stream()), 5)
    assert reads == 2 and serializations == 2 * 347
    status = collector.statuses['alpha']
    assert status.stream_received_patches == 10_000
    assert len(collector.snapshots) == 347
    assert status.stream_reconnects == 0


@pytest.mark.parametrize('change', ['metadata', 'delist'])
@pytest.mark.asyncio
async def test_hot_feed_rebinds_on_metadata_or_delisting_at_check_deadline(tmp_path, monkeypatch, change):
    collector, store, items = setup(tmp_path, 2)
    clock = [0.0]
    monkeypatch.setattr('watch_money.collector.monotonic', lambda: clock[0])
    monkeypatch.setattr(store, 'save_snapshots', lambda *_: None)
    subscriptions = []
    stamp = now_ms()

    class Stream:
        catalog_stream = True
        closed = 0

        async def stream(self, selected):
            subscriptions.append([(item.id, item.contract_size) for item in selected])
            if len(subscriptions) == 1:
                yield MarketSnapshot(instrument_id=items[0].id, venue='alpha', last=1,
                                     source='synthetic', source_at=stamp)
                if change == 'metadata':
                    collector.instruments[items[0].id].contract_size = .01
                else:
                    collector.instruments[items[1].id].active = False
                clock[0] = 1.01
                # Do not apply an in-flight old subscription patch after rebinding.
                yield MarketSnapshot(instrument_id=items[0].id, venue='alpha', last=999,
                                     source='synthetic', source_at=stamp)
            else:
                collector.stopping.set()
                yield MarketSnapshot(instrument_id=items[0].id, venue='alpha', last=2,
                                     source='synthetic', source_at=stamp)

        async def close(self):
            self.closed += 1

    stream = Stream()
    await asyncio.wait_for(collector.run_stream('alpha', stream), 2)
    assert len(subscriptions) == 2 and stream.closed == 2
    assert subscriptions[1] == ([(items[0].id, .01), (items[1].id, 1)] if change == 'metadata'
                                else [(items[0].id, 1)])
    assert collector.snapshots[items[0].id].last == 2
    assert collector.statuses['alpha'].stream_received_patches == 2


@pytest.mark.parametrize('change', [
    {'collection_enabled': False}, {'prefer_websocket': False},
    {'enabled_venues': []}, {'demo_mode': True}, {'broad_collection_enabled': False},
])
@pytest.mark.asyncio
async def test_idle_stream_cancels_pending_receive_after_settings_disable(tmp_path, change):
    collector, store, items = setup(tmp_path, 1)
    pending, cancelled, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Stream:
        catalog_stream = True

        async def stream(self, _selected):
            try:
                pending.set()
                await asyncio.Event().wait()
                yield MarketSnapshot(instrument_id=items[0].id, venue='alpha', source='synthetic')
            finally:
                cancelled.set()

        async def close(self):
            closed.set()

    task = asyncio.create_task(collector.run_stream('alpha', Stream()))
    try:
        await asyncio.wait_for(pending.wait(), .5)
        store.set('settings', store.settings().model_copy(update=change).model_dump())
        await asyncio.wait_for(closed.wait(), 1.5)
        assert cancelled.is_set()
        assert not collector.snapshots
        assert collector.statuses['alpha'].stream_selected_markets == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stream_gap_and_error_diagnostics_are_local_and_sanitized(tmp_path, monkeypatch):
    collector, store, items = setup(tmp_path, 1)
    clock = [0.0]
    monkeypatch.setattr('watch_money.collector.monotonic', lambda: clock[0])
    monkeypatch.setattr(store, 'save_snapshots', lambda *_: None)

    class Stream:
        catalog_stream = True
        calls = 0

        async def stream(self, _selected):
            self.calls += 1
            if self.calls == 1:
                yield MarketSnapshot(instrument_id=items[0].id, venue='alpha', last=1, source='synthetic')
                raise ConnectionError('do-not-expose-private-url-or-token')
            clock[0] = 7
            collector.stopping.set()
            yield MarketSnapshot(instrument_id=items[0].id, venue='alpha', last=2, source='synthetic')

        async def close(self):
            pass

    async def pause(_delay):
        await asyncio.sleep(0)

    collector.pause = pause
    await asyncio.wait_for(collector.run_stream('alpha', Stream()), 2)
    status = collector.statuses['alpha']
    assert status.stream_received_patches == 2
    assert status.stream_last_gap_seconds == status.stream_max_gap_seconds == 7
    assert status.stream_last_patch_at is not None
    assert status.stream_last_error_type == 'ConnectionError' and status.stream_last_error_at is not None
    assert 'do-not-expose' not in status.model_dump_json()
