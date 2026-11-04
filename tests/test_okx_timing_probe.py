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
