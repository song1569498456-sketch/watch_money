"""Bounded, shared public-query CPU work; timestamps remain owned by builders."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from time import perf_counter, process_time, thread_time

from fastapi.responses import JSONResponse

from .models import now_ms


class QueryOverloaded(Exception):
    pass


class QueryWork:
    def __init__(self, *, max_queued=2, queue_timeout=1.0, sample_interval=.5, stall_probe=False):
        self.max_queued = max_queued
        self.queue_timeout = queue_timeout
        self.sample_interval = sample_interval
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='public-query')
        self.slot = asyncio.Semaphore(1)
        self.active = self.queued = 0
        self.routes = {}
        self.jobs = set()
        self.closed = False
        from .stall_probe import StallProbe
        self.stall_probe = StallProbe() if stall_probe else None
        self.sampler = None
        self.loop_lag = {'sample_interval_seconds': sample_interval, 'samples': 0,
                         'last_delay_ms': None, 'max_delay_ms': None, 'last_sample_at': None}

    def start(self):
        if self.sampler is None and not self.closed:
            if self.stall_probe is not None:
                self.stall_probe.start()
            self.sampler = asyncio.create_task(self._sample_loop(), name='public-query-loop-lag')

    async def _sample_loop(self):
        while True:
            deadline = perf_counter() + self.sample_interval
            await asyncio.sleep(self.sample_interval)
            if self.stall_probe is not None:
                self.stall_probe.beat()
            delay = round(max(0, perf_counter() - deadline) * 1000, 3)
            self.loop_lag.update(samples=self.loop_lag['samples'] + 1, last_delay_ms=delay,
                                 max_delay_ms=max(self.loop_lag['max_delay_ms'] or 0, delay),
                                 last_sample_at=now_ms())

    def _stats(self, route):
        method, path = {'markets': ('GET', '/api/markets'), 'scanner': ('GET', '/api/scanner'),
                        'funding': ('GET', '/api/funding'), 'ws': ('WEBSOCKET', '/ws')}.get(route, ('INTERNAL', None))
        return self.routes.setdefault(route, {'requested': 0, 'completed': 0, 'failed': 0,
            'method': method, 'path': path, 'rejected': 0, 'cancelled_clients': 0,
            'last_execution': None, 'slowest_execution': None, 'last_rejection': None})

    def _reject(self, stats, reason, started):
        stats['rejected'] += 1
        stats['last_rejection'] = {'reason': reason, 'queue_ms': round((perf_counter() - started) * 1000, 3),
                                   'at': now_ms()}
        raise QueryOverloaded(reason)

    @staticmethod
    def _build_render(build, render):
        started, cpu, worker_cpu = perf_counter(), process_time(), thread_time()
        build_end = None
        value = error = None
        try:
            built = build()
            build_end = perf_counter()
            value = render(built)
        except Exception as exc:
            error = exc
            build_end = build_end if build_end is not None else perf_counter()
        body = getattr(value, 'body', None)
        response_bytes = len(body) if body is not None else None
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], str):
            response_bytes = len(value[1].encode('utf-8'))
        ended = perf_counter()
        return value, error, {'build_ms': round((build_end - started) * 1000, 3),
            'render_ms': round((ended - build_end) * 1000, 3),
            'response_bytes': response_bytes,
            'worker_cpu_ms': round((thread_time() - worker_cpu) * 1000, 3),
            'process_cpu_ms_during_job': round((process_time() - cpu) * 1000, 3)}

    async def _execute(self, stats, build, render, started, admitted):
        try:
            value, error, metrics = await asyncio.get_running_loop().run_in_executor(
                self.executor, self._build_render, build, render)
            metrics.update(queue_ms=round((admitted - started) * 1000, 3),
                wall_ms=round((perf_counter() - started) * 1000, 3),
                outcome='error' if error else 'ok', error_type=type(error).__name__ if error else None,
                finished_at=now_ms())
            stats['last_execution'] = metrics
            if stats['slowest_execution'] is None or metrics['wall_ms'] > stats['slowest_execution']['wall_ms']:
                stats['slowest_execution'] = dict(metrics)
            stats['failed' if error else 'completed'] += 1
            if error:
                raise error
            return value
        finally:
            self.active -= 1
            self.slot.release()

    async def run(self, route, build, render, *, wait=True):
        started = perf_counter()
        stats = self._stats(route)
        stats['requested'] += 1
        if self.closed:
            self._reject(stats, 'closed', started)
        if not wait and (self.active or self.queued):
            self._reject(stats, 'busy_frame_skipped', started)
        if not self.slot.locked() and not self.queued:
            await self.slot.acquire()
        else:
            if self.queued >= self.max_queued:
                self._reject(stats, 'queue_full', started)
            self.queued += 1
            try:
                try:
                    await asyncio.wait_for(self.slot.acquire(), timeout=self.queue_timeout)
                except asyncio.CancelledError:
                    stats['cancelled_clients'] += 1
                    raise
                except TimeoutError:
                    self._reject(stats, 'queue_timeout', started)
                except asyncio.CancelledError:
                    stats['cancelled_clients'] += 1
                    raise
            finally:
                self.queued -= 1
        if self.closed:
            self.slot.release()
            self._reject(stats, 'closed', started)
        self.active += 1
        job = asyncio.create_task(self._execute(stats, build, render, started, perf_counter()))
        self.jobs.add(job)

        def finished(task):
            self.jobs.discard(task)
            # A disconnected caller cannot cancel a running Python thread.
            # Retrieve its eventual error, while keeping the slot until it ends.
            if not task.cancelled():
                task.exception()

        job.add_done_callback(finished)
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            stats['cancelled_clients'] += 1
            raise

    async def json_response(self, route, build):
        try:
            return await self.run(route, build, JSONResponse)
        except QueryOverloaded:
            return JSONResponse({'detail': 'Public query capacity is busy; retry later.',
                'code': 'heavy_query_overloaded', 'retry_after_seconds': 2}, status_code=503,
                headers={'Retry-After': '2'})

    def diagnostics(self):
        return {'stall_probe': self.stall_probe.snapshot() if self.stall_probe else {'enabled': False},
                'capacity': 1, 'active': self.active, 'queued': self.queued,
                'max_queued': self.max_queued, 'queue_timeout_seconds': self.queue_timeout,
                'closed': self.closed, 'routes': deepcopy(self.routes),
                'event_loop': {**self.loop_lag,
                    'meaning': 'Local timer scheduling delay; not network latency or source freshness.'},
                'cpu_meaning': 'worker_cpu_ms is this worker thread; process_cpu_ms_during_job includes other threads.'}

    async def close(self):
        self.closed = True
        if self.stall_probe is not None:
            self.stall_probe.close()
        if self.sampler is not None:
            self.sampler.cancel()
            await asyncio.gather(self.sampler, return_exceptions=True)
        if self.jobs:
            await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
        self.executor.shutdown(wait=True)
