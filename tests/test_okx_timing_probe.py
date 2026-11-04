import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest

from watch_money.funding_status import observation_state
from watch_money.models import VenueStatus

spec = importlib.util.spec_from_file_location('okx_probe', Path(__file__).parents[1]/'scripts/probe_okx_funding_timing.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.asyncio
async def test_paired_public_probe_retains_old_ts_and_reports_batch_difference(tmp_path):
    now = [1800000000000]
    requested = []
    async def handle(request):
        requested.append(str(request.url))
        assert request.method == 'GET' and request.url.host == 'www.okx.com'
        assert not any('access' in name.lower() or 'authorization' in name.lower() for name in request.headers)
        now[0] += 10
        if request.url.path.endswith('/time'):
            return httpx.Response(200, json={'code': '0', 'data': [{'ts': str(now[0])}]})
        age = 71000 if request.url.params['instId'] == 'ANY' else 1000
        return httpx.Response(200, json={'code': '0', 'data': [{'instId': 'BTC-USDT-SWAP',
            'ts': str(now[0]-age), 'fundingRate': '.001', 'fundingTime': str(now[0]+3600000)}]},
            headers={'Age': '60', 'Set-Cookie': 'must-not-export'})
    async def pause(_):
        now[0] += 10000
        await asyncio.sleep(0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        output = await probe.probe(client, symbols=['BTC-USDT-SWAP'], samples=2, interval=10,
                                   wall_ms=lambda: now[0], pause=pause)
    assert len(requested) == 6 and len(output['cycles']) == 2
    for cycle in output['cycles']:
        clock, batch, direct = cycle['requests']
        assert batch['source_older_than_60s'] == 1 and direct['source_older_than_60s'] == 0
        assert cycle['pairs'][0]['same_ts'] is False
        assert clock['clock_offset_bounds_ms'] is not None
        assert batch['http_cache_age_seconds'] == 60
        assert batch['decoded_at'] >= batch['received_at']
    assert output['cycles'][1]['requests'][1]['selected']['BTC-USDT-SWAP']['ts_advance_ms'] > 0
    assert 'must-not-export' not in str(output)


@pytest.mark.asyncio
async def test_probe_stops_after_first_failed_cycle_without_error_body_or_retry():
    calls = []
    async def handle(request):
        calls.append(request.url.path)
        return httpx.Response(403, text='secret-response-body-not-exported')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        output = await probe.probe(client, symbols=['BTC-USDT-SWAP'], samples=60, interval=5)
    assert len(output['cycles']) == 1 and len(calls) == 3
    assert output['stopped_on_error'] and 'secret-response-body-not-exported' not in str(output)


def test_observation_quality_does_not_confuse_transport_success_and_stale_rates():
    status = VenueStatus(venue='okx', funding_state='online', funding_subscriptions=540,
                         funding_observed_markets=540, funding_source_stale_markets=540)
    assert observation_state(status) == 'source_stale' and status.funding_state == 'online'
    status.funding_fresh_markets = 6
    assert observation_state(status) == 'partially_ready'
    status.funding_fresh_markets = 540
    assert observation_state(status) == 'fields_ready'
    status.funding_fresh_markets = status.funding_source_stale_markets = 0
    assert observation_state(status) == 'incomplete_or_mixed'
    status.funding_source_missing_markets = 540
    assert observation_state(status) == 'source_time_unknown'
    status.funding_source_missing_markets = 0
    status.funding_source_future_markets = 540
    assert observation_state(status) == 'source_time_future'
    status.funding_observed_markets = 0
    assert observation_state(status) == 'not_observed'


@pytest.mark.asyncio
async def test_probe_validates_bounds_before_network():
    async with httpx.AsyncClient() as client:
        for kwargs in [dict(symbols=['https://example.com'], samples=1, interval=5),
                       dict(symbols=['BTC-USDT-SWAP'], samples=61, interval=5),
                       dict(symbols=['BTC-USDT-SWAP'], samples=1, interval=0)]:
            with pytest.raises(ValueError):
                await probe.probe(client, **kwargs)


@pytest.mark.asyncio
async def test_single_timeout_keeps_other_results_and_later_cycles_with_checkpoint(monkeypatch):
    import copy
    calls, active, max_active = {}, 0, 0
    snapshots, pauses = [], []
    async def unpaced(_):
        pass
    monkeypatch.setattr(probe.RequestPacer, 'wait', unpaced)
    async def handle(request):
        nonlocal active, max_active
        key = request.url.params.get('instId', 'clock')
        calls[key] = calls.get(key, 0)+1
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(.01)
            if key == 'ETH-USDT-SWAP' and calls[key] == 1:
                raise httpx.ReadTimeout('must-not-export-timeout-details')
            data = [{'ts': '1800000000000'}] if key == 'clock' else [
                {'instId': symbol, 'ts': '1799999935000', 'fundingRate': '.001'}
                for symbol in (['BTC-USDT-SWAP', 'ETH-USDT-SWAP'] if key == 'ANY' else [key])]
            return httpx.Response(200, json={'code': '0', 'data': data})
        finally:
            active -= 1
    async def pause(delay):
        pauses.append(delay)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report = await probe.probe(client, symbols=['BTC-USDT-SWAP', 'ETH-USDT-SWAP'], samples=2,
            interval=5, wall_ms=lambda: 1800000000000, pause=pause,
            on_cycle=lambda value: snapshots.append(copy.deepcopy(value)))
    assert max_active == 2 and all(count == 2 for count in calls.values())
    assert report['finished'] and report['completed_requested_cycles'] and not report['stopped_on_error']
    assert report['summary']['outcomes'] == {'success': 7, 'timeout': 1}
    assert report['summary']['pairs_unavailable'] == 1 and report['summary']['pairs_with_same_ts'] == 3
    failure = report['cycles'][0]['requests'][3]
    assert failure['error'] == 'ReadTimeout' and failure['elapsed_ms'] >= 0 and 'finished_at' in failure
    assert 'received_at' not in failure and 'selected' not in failure
    assert report['cycles'][1]['requests'][2]['selected']['BTC-USDT-SWAP']['ts_advance_ms'] == 0
    assert report['cycles'][1]['requests'][2]['source_older_than_60s'] == 1
    assert pauses == [5] and [row['finished'] for row in snapshots] == [False, False, True]
    assert 'must-not-export' not in str(report)


@pytest.mark.asyncio
async def test_persistent_failures_have_bounded_cycles_and_backoff(monkeypatch):
    calls, pauses = [], []
    async def unpaced(_):
        pass
    monkeypatch.setattr(probe.RequestPacer, 'wait', unpaced)
    async def handle(request):
        calls.append(request.url.path)
        raise httpx.ConnectTimeout('private-error-details')
    async def pause(delay):
        pauses.append(delay)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report = await probe.probe(client, symbols=['BTC-USDT-SWAP'], samples=60, interval=40, pause=pause)
    assert len(calls) == 9 and len(report['cycles']) == 3 and pauses == [40, 60]
    assert report['stop_reason'] == 'three_all_failed_cycles'
    assert report['summary']['outcomes'] == {'timeout': 9}
    assert not report['completed_requested_cycles'] and report['finished']


@pytest.mark.asyncio
async def test_total_deadline_and_partial_read_record_failure_without_fake_receipt():
    async def slow(request):
        await asyncio.sleep(10)
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        row = await probe.request(client, asyncio.Semaphore(2), 'direct', 'BTC-USDT-SWAP',
            ['BTC-USDT-SWAP'], lambda: 1800000000000, timeout_seconds=.01)
    assert row['outcome'] == 'timeout' and row['error'] == 'TimeoutError' and row['elapsed_ms'] >= 10
    assert 'received_at' not in row
    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"code":'
            raise httpx.ReadTimeout('private-error-details')
    async def partial(request):
        return httpx.Response(200, stream=BrokenBody())
    async with httpx.AsyncClient(transport=httpx.MockTransport(partial)) as client:
        row = await probe.request(client, asyncio.Semaphore(2), 'batch', 'ANY', ['BTC-USDT-SWAP'],
                                  lambda: 1800000000000)
    assert row['http_status'] == 200 and row['response_bytes'] == 8 and row['error_stage'] == 'body'
    assert row['outcome'] == 'timeout' and row['error'] == 'ReadTimeout' and 'received_at' not in row


@pytest.mark.asyncio
async def test_request_start_pacer_enforces_spacing():
    import time
    pacer = probe.RequestPacer(gap=.01)
    starts = []
    async def start():
        await pacer.wait()
        starts.append(time.monotonic())
    await asyncio.gather(*(start() for _ in range(4)))
    assert all(b-a >= .009 for a, b in zip(starts, starts[1:]))
