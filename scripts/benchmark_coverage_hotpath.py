"""Local-only 7000-market coverage workload; never an exchange acceptance test."""
import asyncio
import json
from pathlib import Path
import tempfile
from time import perf_counter
from types import SimpleNamespace

import watch_money.collector as module
from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot, Settings
from watch_money.storage import Storage


async def run():
    with tempfile.TemporaryDirectory(prefix='coverage-workload-') as directory:
        store = Storage(directory)
        store.set('settings', Settings(enabled_venues=['synthetic'], notifications_enabled=False).model_dump())
        collector = Collector(store, {'synthetic': SimpleNamespace()})
        at = module.now_ms()
        # Stable time isolates cached computation; expiry correctness is tested separately.
        original_clock = module.now_ms
        module.now_ms = lambda: at
        try:
            for n in range(7000):
                key = str(n)
                collector.instruments[key] = Instrument(id=key, venue='synthetic', native_symbol=key,
                    base=key, quote='USD', asset_id=key, kind='perpetual')
                collector.snapshots[key] = MarketSnapshot(instrument_id=key, venue='synthetic', source='synthetic',
                    bid=10, ask=11, funding_rate=.001, funding_kind='current', funding_interval_hours=8,
                    source_at=at, received_at=at)
            count = 0
            original = collector._coverage_counts
            def counted(*args):
                nonlocal count
                count += 1
                return original(*args)
            collector._coverage_counts = counted
            samples = []
            for phase in ['cold', 'unchanged', 'unchanged', 'unchanged']:
                before = count
                started = perf_counter()
                await collector._refresh_status_coverage_async()
                samples.append({'phase': phase, 'milliseconds': round((perf_counter()-started)*1000, 3),
                                'reclassified_markets': count-before})
            cached_status = collector.statuses['synthetic'].model_dump()
            collector._coverage_cache.clear()
            delays = []
            stopping = asyncio.Event()
            async def ticker():
                while not stopping.is_set():
                    deadline = perf_counter()+.005
                    await asyncio.sleep(.005)
                    delays.append(max(0, perf_counter()-deadline)*1000)
            ticker_task = asyncio.create_task(ticker())
            await asyncio.sleep(.005)
            await collector._refresh_status_coverage_async()
            stopping.set()
            await ticker_task
            assert collector.statuses['synthetic'].model_dump() == cached_status
            evidence = {'fixture': '7000 local synthetic perpetual markets; no network/account access; '
                        'fixed event clock for compute comparison; not live stability acceptance',
                        'samples': samples, 'cache_entries': len(collector._coverage_cache),
                        'cold_recompute_same_status': True, 'cold_background_loop_samples': len(delays),
                        'cold_background_loop_max_delay_ms': round(max(delays, default=0), 3),
                        'price_and_funding_fresh_markets': collector.statuses['synthetic'].funding_fresh_markets}
            target = Path('docs/evidence/coverage-hotpath-synthetic.json')
            target.write_text(json.dumps(evidence, indent=2)+'\n')
            print(json.dumps(evidence))
        finally:
            module.now_ms = original_clock


if __name__ == '__main__':
    asyncio.run(run())
