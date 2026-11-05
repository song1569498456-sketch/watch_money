"""Local admission/cancellation fixtures. No exchange traffic or performance SLA."""
import asyncio
import json
import threading
from time import sleep

import httpx
import pytest
from fastapi import WebSocketDisconnect
from fastapi.responses import JSONResponse

from watch_money.app import create_app
from watch_money.query_work import QueryOverloaded, QueryWork


async def until(predicate, timeout=1):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.001)


@pytest.mark.asyncio
async def test_one_worker_two_waiters_timeout_and_overload_are_bounded():
    work = QueryWork(queue_timeout=.08)
    started, release = threading.Event(), threading.Event()

    def blocked():
        started.set()
        release.wait(2)
        return {'source_at': 123, 'received_at': 456}

    first = asyncio.create_task(work.json_response('markets', blocked))
    try:
        await until(started.is_set)
        queued = [asyncio.create_task(work.json_response(route, lambda: {})) for route in ('scanner', 'funding')]
        await until(lambda: work.queued == 2)
        rejected = await work.json_response('markets', lambda: pytest.fail('Overloaded builder must not run'))
        assert rejected.status_code == 503 and rejected.headers['retry-after'] == '2'
        assert json.loads(rejected.body)['code'] == 'heavy_query_overloaded'
        responses = await asyncio.gather(*queued)
        assert all(row.status_code == 503 for row in responses)
        assert work.active == 1 and work.queued == 0
        assert work.diagnostics()['routes']['scanner']['last_rejection']['reason'] == 'queue_timeout'
    finally:
        release.set()
        response = await first
        await work.close()
    assert json.loads(response.body) == {'source_at': 123, 'received_at': 456}


@pytest.mark.asyncio
async def test_cancelled_client_keeps_slot_until_thread_finishes_and_queue_cancel_is_clean():
    work = QueryWork(queue_timeout=1)
    started, release = threading.Event(), threading.Event()

    def blocked():
        started.set()
        release.wait(2)
        return {}

    first = asyncio.create_task(work.json_response('markets', blocked))
    try:
        await until(started.is_set)
        queued = asyncio.create_task(work.json_response('scanner', lambda: {}))
        await until(lambda: work.queued == 1)
        queued.cancel()
        await asyncio.gather(queued, return_exceptions=True)
        assert work.queued == 0
        assert work.diagnostics()['routes']['scanner']['cancelled_clients'] == 1
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert work.active == 1
        with pytest.raises(QueryOverloaded):
            await work.run('ws', lambda: pytest.fail('Cancelled thread still owns slot'), JSONResponse, wait=False)
        assert work.diagnostics()['routes']['markets']['cancelled_clients'] == 1
    finally:
        release.set()
        await work.close()
    assert work.active == 0 and not work.jobs


@pytest.mark.asyncio
async def test_worker_timings_errors_and_loop_sampler_are_safe_and_closed():
    work = QueryWork(sample_interval=.01)
    work.start()

    def build():
        sleep(.01)
        return {'original_timestamp': 123}

    def render(value):
        sleep(.01)
        return JSONResponse(value)

    try:
        response = await work.run('markets', build, render)
        assert json.loads(response.body)['original_timestamp'] == 123
        execution = work.diagnostics()['routes']['markets']['last_execution']
        assert execution['build_ms'] >= 5 and execution['render_ms'] >= 5
        assert execution['wall_ms'] >= execution['build_ms'] + execution['render_ms']
        assert execution['worker_cpu_ms'] >= 0 and execution['process_cpu_ms_during_job'] >= 0
        assert execution['response_bytes'] == len(response.body)
        await work.run('markets', lambda: {}, JSONResponse)
        assert work.diagnostics()['routes']['markets']['slowest_execution'] == execution

        def fail():
            raise ValueError('private-url?credential=never-expose')

        with pytest.raises(ValueError):
            await work.run('funding', fail, JSONResponse)
        diagnostics = work.diagnostics()
        assert diagnostics['routes']['funding']['last_execution']['error_type'] == 'ValueError'
        assert 'never-expose' not in json.dumps(diagnostics)
        assert diagnostics['event_loop']['samples'] >= 1
        assert diagnostics['event_loop']['max_delay_ms'] >= 0
    finally:
        await work.close()
    assert work.sampler.done() and not work.jobs


@pytest.mark.asyncio
async def test_http_routes_share_admission_and_diagnostics_remain_available(tmp_path, monkeypatch):
    app = create_app(tmp_path, start_collector=False, adapters={})
    work = app.state.query_work
    work.queue_timeout = .08
    started, release = threading.Event(), threading.Event()

    def blocked():
        started.set()
        release.wait(2)
        return {'instruments': [], 'snapshots': [], 'mode': 'live'}

    monkeypatch.setattr(app.state.collector, 'markets', blocked)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
        first = asyncio.create_task(client.get('/api/markets'))
        try:
            await until(started.is_set)
            queued = [asyncio.create_task(client.get(path)) for path in ('/api/scanner', '/api/funding')]
            await until(lambda: work.queued == 2)
            diagnostics = (await client.get('/api/query-work')).json()
            assert diagnostics['capacity'] == diagnostics['active'] == 1 and diagnostics['queued'] == 2
            overload = await client.get('/api/markets')
            assert overload.status_code == 503 and overload.headers['retry-after'] == '2'
            assert all(row.status_code == 503 for row in await asyncio.gather(*queued))
        finally:
            release.set()
            assert (await first).status_code == 200
            await work.close()


@pytest.mark.asyncio
async def test_ws_skips_under_http_pressure_and_keeps_last_sent_baseline(tmp_path, monkeypatch):
    app = create_app(tmp_path, start_collector=False, adapters={})
    work = app.state.query_work
    source = {'instruments': [], 'snapshots': [{'instrument_id': 'old', 'last': 1,
               'source_at': 123, 'received_at': 456}], 'mode': 'live'}
    monkeypatch.setattr(app.state.collector, 'markets', lambda: json.loads(json.dumps(source)))
    started, release = threading.Event(), threading.Event()
    first_frame = asyncio.Event()
    heavy = []

    def blocked():
        started.set()
        release.wait(3)
        return {}

    class Socket:
        async def receive(self):
            await asyncio.Event().wait()

        frames = []

        async def accept(self):
            pass

        async def send_text(self, frame):
            self.frames.append(json.loads(frame))
            if len(self.frames) == 1:
                heavy.append(asyncio.create_task(work.json_response('scanner', blocked)))
                await until(started.is_set)
                first_frame.set()
            else:
                raise WebSocketDisconnect()

    socket = Socket()
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', None) == '/ws')
    task = asyncio.create_task(endpoint(socket))
    try:
        await asyncio.wait_for(first_frame.wait(), 1)
        await until(lambda: work.diagnostics()['routes']['ws']['rejected'] >= 1, timeout=1.5)
        assert len(socket.frames) == 1
        source['snapshots'][0]['last'] = 2
        release.set()
        await asyncio.wait_for(task, 2)
        assert socket.frames[0]['type'] == 'markets'
        assert socket.frames[1]['type'] == 'markets_delta'
        assert socket.frames[1]['snapshots'][0]['last'] == 2
        assert socket.frames[1]['snapshots'][0]['source_at'] == 123
        assert socket.frames[1]['snapshots'][0]['received_at'] == 456
        assert work.diagnostics()['routes']['ws']['last_execution']['response_bytes'] > 0
    finally:
        release.set()
        await asyncio.gather(*heavy, return_exceptions=True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await work.close()


@pytest.mark.asyncio
async def test_full_app_lifespan_closes_sampler_and_worker(tmp_path):
    app = create_app(tmp_path, start_collector=False, adapters={})
    work = app.state.query_work
    async with app.router.lifespan_context(app):
        assert work.sampler is not None and not work.sampler.done()
        await work.json_response('markets', lambda: {'source_at': 123})
    assert work.closed and work.sampler.done() and not work.jobs
    assert all(not thread.is_alive() for thread in work.executor._threads)


@pytest.mark.asyncio
async def test_disconnected_socket_stops_even_while_readonly_worker_is_still_running(tmp_path, monkeypatch):
    app = create_app(tmp_path, start_collector=False, adapters={})
    work = app.state.query_work
    started, release = threading.Event(), threading.Event()
    disconnected = asyncio.Event()
    calls = []

    def build():
        calls.append('build')
        started.set()
        release.wait(3)
        return {'instruments': [], 'snapshots': [], 'mode': 'live'}

    monkeypatch.setattr(app.state.collector, 'markets', build)

    class Socket:
        async def accept(self):
            pass

        async def receive(self):
            await disconnected.wait()
            return {'type': 'websocket.disconnect', 'code': 1000}

        async def send_text(self, frame):
            pytest.fail('Closed subscription must never publish a late frame')

    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', None) == '/ws')
    task = asyncio.create_task(endpoint(Socket()))
    try:
        await until(started.is_set)
        disconnected.set()
        await asyncio.wait_for(task, .5)
        assert work.active == 1  # CPU work cannot be killed; do not release its slot early.
    finally:
        release.set()
        await work.close()
        await app.state.asset_verification.close()
    assert calls == ['build'] and not work.jobs
