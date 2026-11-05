"""Synthetic producer/collector/storage exercise, not real WebSocket acceptance."""
import asyncio
import json
import math
import tempfile
from pathlib import Path
from time import perf_counter, process_time
from types import SimpleNamespace

from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot, Settings, now_ms
from watch_money.storage import Storage


async def main():
    with tempfile.TemporaryDirectory(prefix='stream-integration-') as directory:
        store = Storage(directory)
        store.set('settings', Settings(enabled_venues=['synthetic'], poll_seconds=5, history_enabled=True).model_dump())
        items = [Instrument(id=f'load:{index}', venue='synthetic', native_symbol=f'SMALL{index}',
                            base=f'SMALL{index}', quote='USD', asset_id=f'synthetic:{index}', kind='perpetual')
                 for index in range(347)]
        store.save_instruments(items)
        collector = Collector(store, {'synthetic': SimpleNamespace()})
        collector.discovered.add('synthetic')
        queue = asyncio.Queue(maxsize=6000)
        high_water = 0
        gaps = []
        done = asyncio.Event()

        async def producer():
            nonlocal high_water
            origin = perf_counter()
            for index in range(4500):
                await asyncio.sleep(max(0, origin + index / 300 - perf_counter()))
                if index == 1500:
                    await queue.put(ConnectionError('synthetic injected disconnect'))
                item = items[index % len(items)]
                patch = MarketSnapshot(instrument_id=item.id, venue='synthetic', bid=100, ask=101,
                                       source='synthetic:queued-public-feed', source_at=now_ms(),
                                       protocol_metrics={'raw_sequence': str(index)})
                await queue.put((index, patch))
                high_water = max(high_water, queue.qsize())

        class Stream:
            catalog_stream = True

            async def stream(self, selected):
                assert len(selected) == 347
                while True:
                    event = await queue.get()
                    if isinstance(event, Exception):
                        raise event
                    index, patch = event
                    if index == 4499:
                        collector.stopping.set()
                    yield patch

            async def close(self):
                pass

        async def timer():
            while not done.is_set():
                deadline = perf_counter() + .01
                await asyncio.sleep(.01)
                gaps.append(max(0, (perf_counter() - deadline) * 1000))

        started, cpu = perf_counter(), process_time()
        timer_task = asyncio.create_task(timer())
        producer_task = asyncio.create_task(producer())
        try:
            await asyncio.wait_for(collector.run_stream('synthetic', Stream()), 45)
            await producer_task
        finally:
            done.set()
            await timer_task
            if not producer_task.done():
                producer_task.cancel()
                await asyncio.gather(producer_task, return_exceptions=True)
        collector.refresh_status_coverage()
        status = collector.statuses['synthetic']
        def quantile(p):
            return round(sorted(gaps)[math.ceil(len(gaps) * p) - 1], 3)
        result = {'kind':'synthetic_collector_merge_storage_load', 'at':now_ms(),
                  'markets':347,'patches':4500,'target_producer_rate':300,
                  'injected_disconnects':1,'real_backoff_seconds':5,
                  'included':['actual merge_patch including raw metric provenance','actual SQLite snapshot/history writes every 5s',
                              'independent paced producer queue','catalog selection','real collector reconnect backoff'],
                  'excluded':['real exchange/network/WebSocket decoding','other venue collectors','browser push and HTTP workload'],
                  'wall_seconds':round(perf_counter()-started,3),'cpu_seconds':round(process_time()-cpu,3),
                  'queue_peak':high_water,'processed':status.stream_received_patches,
                  'reconnects':status.stream_reconnects,'last_error_type':status.stream_last_error_type,
                  'max_processed_patch_gap_seconds':status.stream_max_gap_seconds,
                  'final_fresh_book_markets':status.light_fresh_markets,
                  'timer_samples':len(gaps),'timer_lag_p90_ms':quantile(.9),'timer_lag_max_ms':round(max(gaps),3),
                  'measurement_environment':'shared workspace; no CPU/process isolation',
                  'limitations':'Synthetic recovery only; not a reproduction or causal explanation of the reported live disconnect.'}
        assert result['processed'] == 4500 and result['reconnects'] == 1
        assert result['final_fresh_book_markets'] == 347
        path = Path(__file__).resolve().parents[1] / 'docs/evidence/stream-integration-load.json'
        path.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
