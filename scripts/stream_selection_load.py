"""Compare the old/local stream loop with fixed synthetic catalogue/patches.

No sockets or exchange requests. Decode, merge and snapshot disk writes are
excluded so this isolates collector selection/settings/signature overhead.
The baseline function is extracted from a named, local Git revision, not fetched.
This benchmark cannot establish the cause of a real exchange disconnect.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import math
import platform
import subprocess
import tempfile
from pathlib import Path
from time import perf_counter, process_time
from types import SimpleNamespace

import watch_money.collector as collector_module
from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot, Settings, now_ms
from watch_money.storage import Storage


def baseline_loop(revision):
    source = subprocess.run(['git', 'show', f'{revision}:backend/watch_money/collector.py'],
                            check=True, capture_output=True, text=True).stdout
    tree = ast.parse(source)
    collector = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Collector')
    function = next(node for node in collector.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'run_stream')
    namespace = dict(vars(collector_module))
    exec(compile(ast.Module(body=[function], type_ignores=[]), f'local-git:{revision}:run_stream', 'exec'), namespace)
    return namespace['run_stream']


def quantile(values, p):
    return round(sorted(values)[max(0, math.ceil(len(values) * p) - 1)], 3) if values else None


async def measure(method, markets, patches, rate):
    with tempfile.TemporaryDirectory(prefix='stream-load-') as directory:
        store = Storage(directory)
        store.set('settings', Settings(enabled_venues=['synthetic'], history_enabled=False,
                                      poll_seconds=3600).model_dump())
        items = [Instrument(id=f'load:{n:04}', venue='synthetic', native_symbol=f'SMALL{n}',
                            base=f'SMALL{n}', quote='USDC', asset_id=f'synthetic:{n}', kind='perpetual')
                 for n in range(markets)]
        store.save_instruments(items)
        collector = Collector(store, {'synthetic': SimpleNamespace()})
        collector.discovered.add('synthetic')
        collector.merge_patch = lambda patch: patch
        store.save_snapshots = lambda *_args: None
        settings_reads = signature_rows = 0
        settings, dump = store.settings, Instrument.model_dump_json

        def counted_settings():
            nonlocal settings_reads
            settings_reads += 1
            return settings()

        def counted_dump(item, *args, **kwargs):
            nonlocal signature_rows
            signature_rows += 1
            return dump(item, *args, **kwargs)

        store.settings = counted_settings
        Instrument.model_dump_json = counted_dump
        patch = MarketSnapshot(instrument_id=items[0].id, venue='synthetic', bid=1, ask=2,
                               source='synthetic:load', source_at=now_ms())
        seen = 0
        gaps = []
        done = asyncio.Event()

        class Stream:
            catalog_stream = True

            async def stream(self, selected):
                nonlocal seen
                assert len(selected) == markets
                origin = perf_counter()
                for index in range(patches):
                    if rate:
                        await asyncio.sleep(max(0, origin + index / rate - perf_counter()))
                    seen += 1
                    if index == patches - 1:
                        collector.stopping.set()
                    yield patch.model_copy(update={'instrument_id': items[index % markets].id})

            async def close(self):
                pass

        async def timer():
            while not done.is_set():
                deadline = perf_counter() + .01
                await asyncio.sleep(.01)
                gaps.append(max(0, (perf_counter() - deadline) * 1000))

        timer_task = asyncio.create_task(timer())
        started, cpu_started = perf_counter(), process_time()
        try:
            await method(collector, 'synthetic', Stream())
            wall, cpu = perf_counter() - started, process_time() - cpu_started
        finally:
            done.set()
            await timer_task
            Instrument.model_dump_json = dump
        assert seen == patches and len(collector.snapshots) == min(markets, patches)
        return {'markets': markets, 'patches': patches, 'target_patches_per_second': rate or None,
                'settings_reads': settings_reads,
                'signature_checks': signature_rows // markets, 'signature_rows': signature_rows,
                'wall_seconds': round(wall, 6), 'cpu_seconds': round(cpu, 6),
                'cpu_us_per_patch': round(cpu * 1e6 / patches, 3),
                'timer_period_ms': 10, 'timer_samples': len(gaps),
                'timer_lag_p50_ms': quantile(gaps, .5), 'timer_lag_p90_ms': quantile(gaps, .9),
                'timer_lag_max_ms': round(max(gaps, default=0), 3),
                'selected_market_snapshots': len(collector.snapshots)}


async def main(args):
    before = await measure(baseline_loop(args.baseline), args.markets, args.patches, args.rate)
    after = await measure(Collector.run_stream, args.markets, args.patches, args.rate)
    result = {'kind': 'synthetic_local_cpu_benchmark', 'checked_at': now_ms(),
              'baseline_revision': args.baseline, 'python': platform.python_version(),
              'excluded': ['exchange/network', 'websocket decode', 'merge_patch', 'snapshot disk writes'],
              'settings_reads': 'real local SQLite + Settings validation in both versions',
              'limitations': 'Fixed patch count and target arrival rate, not a real transport. Timer lag is local scheduling lag, '
                             'not measured network latency or a proven live-disconnect cause.',
              'before': before, 'after': after}
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='eaeb822')
    parser.add_argument('--markets', type=int, default=347)
    parser.add_argument('--patches', type=int, default=3000)
    parser.add_argument('--rate', type=float, default=300, help='Synthetic patches/second, 0 for unpaced')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.markets < 1 or args.patches < 1 or args.rate < 0:
        parser.error('markets/patches must be positive and rate nonnegative')
    asyncio.run(main(args))
