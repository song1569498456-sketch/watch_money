"""Local synthetic API/loop/storage timing; no external network or collectors.

This identifies executable blocking paths, not the cause of a reported live
outage. ASGI requests have no TCP/browser overhead. Samples share this machine
with other work; timings are observations, never acceptance thresholds.
"""
import argparse
import asyncio
import inspect
import json
import tempfile
import threading
from pathlib import Path
from time import perf_counter, process_time
from types import SimpleNamespace

import httpx
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

from watch_money.app import create_app
from watch_money.market_stream import market_delta
from watch_money.models import Instrument, MarketSnapshot, Settings, now_ms
from watch_money.scanner import scan_markets


async def timed(operation):
    lags = []
    done = asyncio.Event()

    async def timer():
        while not done.is_set():
            deadline = perf_counter() + .01
            await asyncio.sleep(.01)
            lags.append(max(0, (perf_counter() - deadline) * 1000))

    task = asyncio.create_task(timer())
    await asyncio.sleep(.025)
    start, cpu = perf_counter(), process_time()
    try:
        value = operation()
        if inspect.isawaitable(value):
            value = await value
        metrics = {'wall_seconds': round(perf_counter() - start, 6),
                   'process_cpu_seconds': round(process_time() - cpu, 6)}
        await asyncio.sleep(.025)
    finally:
        done.set()
        await task
    metrics.update(timer_samples=len(lags), timer_lag_max_ms=round(max(lags, default=0), 3))
    return value, metrics


class MeasuredLock:
    def __init__(self, lock):
        self.lock = lock
        self.waits = []

    def __enter__(self):
        start = perf_counter()
        self.lock.acquire()
        self.waits.append({'thread': threading.get_ident(), 'wait_seconds': round(perf_counter() - start, 6)})
        return self

    def __exit__(self, *_):
        self.lock.release()


async def measure(size):
    with tempfile.TemporaryDirectory(prefix='api-diagnostic-') as directory:
        venues = [f'synthetic{n}' for n in range(4)]
        app = create_app(directory, start_collector=False, adapters={name: SimpleNamespace() for name in venues})
        collector, store = app.state.collector, app.state.store
        store.set('settings', Settings(enabled_venues=venues, history_enabled=True).model_dump())
        stamp = now_ms()
        for index in range(size):
            group, venue = index // 4, venues[index % 4]
            item = Instrument(id=f'{venue}:{group}', venue=venue, native_symbol=f'SYNTH{group}/USDT:USDT',
                              asset_id=f'SYNTH{group}', base=f'SYNTH{group}', quote='USDT', settle='USDT',
                              kind='perpetual', mapping_verified=True)
            collector.instruments[item.id] = item
            collector.snapshots[item.id] = MarketSnapshot(instrument_id=item.id, venue=venue,
                bid=100 + index % 4, ask=101 + index % 4, last=100.5, funding_rate=.0001,
                funding_kind='current', funding_interval_hours=8, next_funding_at=stamp + 3600000,
                source='synthetic:public', source_at=stamp, received_at=stamp,
                protocol_metrics={'fixture': 'not_real_market_data'})
        stages = {}
        markets, stages['markets_direct_build'] = await timed(collector.markets)
        encoded, stages['markets_jsonable_encoder_on_loop'] = await timed(lambda: jsonable_encoder(markets))
        response, stages['markets_json_render_on_loop'] = await timed(lambda: JSONResponse(encoded))
        stages['markets_json_render_on_loop']['response_bytes'] = len(response.body)
        delta, stages['ws_unchanged_delta_on_loop'] = await timed(lambda: market_delta(markets, markets))
        stages['ws_unchanged_delta_on_loop']['delta_bytes'] = len(JSONResponse(delta).body)
        _, stages['coverage_all_venues_on_loop'] = await timed(collector.refresh_status_coverage)
        scan, stages['scanner_worker_build'] = await timed(lambda: asyncio.to_thread(scan_markets,
            list(collector.instruments.values()), dict(collector.snapshots), stamp, 60, 100))
        scan_encoded, stages['scanner_jsonable_encoder_on_loop'] = await timed(lambda: jsonable_encoder(scan))
        scan_response, stages['scanner_json_render_on_loop'] = await timed(lambda: JSONResponse(scan_encoded))
        stages['scanner_json_render_on_loop']['response_bytes'] = len(scan_response.body)
        stages['scanner_worker_build']['scanned_candidates'] = scan['stats']['scanned']
        stages['scanner_worker_build']['groups_total'] = scan['stats']['groups_total']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic.local') as client:
            for path, key in [('/api/markets', 'markets_actual_asgi_http'), ('/api/scanner', 'scanner_actual_asgi_http')]:
                actual, stages[key] = await timed(lambda path=path: client.get(path))
                actual.raise_for_status()
                stages[key]['response_bytes'] = len(actual.content)
            actual, stages['parallel_markets_scanner_asgi_http'] = await timed(lambda: asyncio.gather(
                client.get('/api/markets'), client.get('/api/scanner')))
            assert all(row.status_code == 200 for row in actual)
        lock = MeasuredLock(store.lock)
        store.lock = lock
        write_rows = list(collector.snapshots.values())[:min(size, 1000)]
        _, stages['single_snapshot_history_write_worker'] = await timed(lambda: asyncio.to_thread(
            store.save_snapshots, write_rows, True))
        lock.waits.clear()
        _, stages['three_concurrent_snapshot_history_writes_workers'] = await timed(lambda: asyncio.gather(*(
            asyncio.to_thread(store.save_snapshots, write_rows, True) for _ in range(3))))
        stages['three_concurrent_snapshot_history_writes_workers'].update(
            rows_per_write=len(write_rows), lock_acquisition_waits=lock.waits)
        await app.state.asset_verification.close()
        return {'markets': size, 'venues': len(venues), 'stages': stages}


async def main(args):
    results = [await measure(size) for size in args.sizes]
    evidence = {'kind': 'synthetic_local_api_blocking_diagnostic', 'at': now_ms(),
                'production_edits_by_script': False, 'network_requests': False,
                'included': ['actual in-process ASGI routing/serialization', 'synthetic confirmed four-venue identity groups',
                             'actual SQLite and Parquet writes', '10ms event-loop timer'],
                'excluded': ['exchange and TCP/browser traffic', 'active collectors', 'other process isolation'],
                'limitations': 'Synthetic shape/sizes, one sample per stage, shared machine. Cannot establish live outage cause. '
                               'Direct on-loop stages intentionally measure synchronous work paths; worker stages still contend for GIL.',
                'results': results}
    text = json.dumps(evidence, indent=2) + '\n'
    if args.output:
        Path(args.output).write_text(text)
    print(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[1000, 10000])
    parser.add_argument('--output', default='docs/evidence/api-blocking-diagnostic.json')
    args = parser.parse_args()
    if any(size < 4 for size in args.sizes):
        parser.error('sizes must be >=4')
    asyncio.run(main(args))
