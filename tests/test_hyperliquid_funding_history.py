"""Native public history fixtures, not live Hyperliquid evidence."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from watch_money.adapters.dex import HyperliquidAdapter
from watch_money.funding_history import FundingHistoryService
from watch_money.models import Instrument

NOW = 1_800_000_000_000
EVENT = NOW - 3600000


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr('watch_money.funding_history.now_ms', lambda: NOW)
    monkeypatch.setattr('watch_money.adapters.dex.now_ms', lambda: NOW)
    def make(coin='PEPE', handler=None, timeout=15):
        requests = []
        def response(request):
            payload = json.loads(request.content)
            requests.append(payload)
            assert request.method == 'POST' and str(request.url) == 'https://api.hyperliquid.xyz/info'
            assert set(payload) == {'type', 'coin', 'startTime', 'endTime'}
            assert payload['type'] == 'fundingHistory'
            assert 'authorization' not in request.headers
            return handler(request) if handler else httpx.Response(200, json=[row(coin)])
        client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        adapter = HyperliquidAdapter(client)
        namespace = coin.split(':')[0] if ':' in coin else ''
        adapter._namespaces = [namespace]
        adapter._quotes = {namespace: 'USDC'}
        item = Instrument(id=f'hyperliquid:perpetual:{coin}', venue='hyperliquid', native_symbol=coin,
                          asset_id=f'hyperliquid:{coin}', base=coin.split(':')[-1], quote='USDC',
                          settle='USDC', kind='perpetual')
        collector = SimpleNamespace(instruments={item.id: item}, adapters={'hyperliquid': adapter})
        service = FundingHistoryService(tmp_path, collector, timeout_seconds=timeout)
        return service, item, client, adapter, requests
    return make


def row(coin='PEPE', time=EVENT, rate='-.0002'):
    return {'coin': coin, 'time': time, 'fundingRate': rate, 'premium': '.123'}


@pytest.mark.parametrize('coin', ['PEPE', 'xyz:PEPE'])
async def test_public_native_and_hip3_history_exact_identity_and_hourly_rate(environment, coin):
    service, item, client, _, requests = environment(coin)
    async with client:
        assert (await service.get(item.id))['records'] == [] and not requests
        result = await service.get(item.id, refresh=True)
        record = result['records'][0]
        assert record['funding_rate'] == -.0002 and record['funding_interval_hours'] == 1
        assert record['long_cashflow_sign'] == 1 and record['short_cashflow_sign'] == -1
        assert record['symbol'] == coin and record['native_settlement_time'] == EVENT
        assert requests[0]['coin'] == coin and requests[0]['startTime'] == NOW - 100 * 3600000
        assert result['summary']['next_since'] == EVENT + 1
        again = await service.get(item.id, refresh=True)
        assert again['records'][0]['revision'] == 1
        assert len((await service.get(item.id))['records']) == 1


async def test_bad_identity_times_and_rates_cannot_be_saved(environment):
    payload = [row('OTHER'), row(time=EVENT // 1000), row(time=NOW + 1), row(rate='NaN'),
               row(time=True), row(time=str(EVENT)), row(rate='0'), row(rate='.0001', time=EVENT + 1)]
    service, item, client, _, _ = environment(handler=lambda _: httpx.Response(200, json=payload))
    async with client:
        result = await service.get(item.id, refresh=True)
    assert result['summary']['rejected_count'] == 6
    assert [record['long_cashflow_sign'] for record in result['records']] == [0, -1]


async def test_sorted_bounded_page_keeps_cursor_before_unreturned_records(environment):
    payload = [row(time=EVENT + i) for i in reversed(range(5))]
    service, item, client, _, requests = environment(handler=lambda _: httpx.Response(200, json=payload))
    async with client:
        result = await service.get(item.id, refresh=True, since=EVENT, limit=2)
    assert [record['settlement_at'] for record in result['records']] == [EVENT, EVENT + 1]
    assert result['summary']['truncated'] and result['summary']['next_since'] == EVENT + 2
    assert requests[0]['endTime'] <= requests[0]['startTime'] + 500 * 3600000


@pytest.mark.parametrize('status', [403, 451, 500])
async def test_http_failure_is_sanitized_and_no_retry(environment, status):
    service, item, client, _, requests = environment(handler=lambda _: httpx.Response(status, text='private-marker'))
    async with client:
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
    assert error.value.status_code == (403 if status in {403, 451} else 502)
    assert 'private-marker' not in str(error.value.detail) and len(requests) == 1


@pytest.mark.parametrize('payload', [{}, [row()] * 501])
async def test_malformed_or_oversized_response_rejected(environment, payload):
    service, item, client, _, _ = environment(handler=lambda _: httpx.Response(200, json=payload))
    async with client:
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
    assert error.value.status_code == 502


async def test_scope_change_during_io_never_saves_wrong_namespace(environment):
    service, item, client, adapter, requests = environment()
    async def info(*args, **kwargs):
        adapter._quotes[''] = 'OTHER'
        return [row()]
    adapter.info = info
    async with client:
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
    assert error.value.status_code == 409 and not requests
    assert (await service.get(item.id))['records'] == []


async def test_unknown_namespace_and_future_start_rejected_before_network(environment):
    service, item, client, adapter, requests = environment()
    async with client:
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True, since=NOW + 1)
        assert error.value.status_code == 422
        adapter._namespaces = []
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
        assert error.value.status_code == 409 and not requests


async def test_timeout_cancels_public_call(environment):
    service, item, client, adapter, _ = environment(timeout=.01)
    ended = asyncio.Event()
    async def info(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()
    adapter.info = info
    async with client:
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
    assert error.value.status_code == 504 and ended.is_set()


async def test_observer_delay_keeps_native_receipt(environment, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr('watch_money.funding_history.now_ms', lambda: clock[0])
    monkeypatch.setattr('watch_money.adapters.dex.now_ms', lambda: clock[0])
    service, item, client, adapter, _ = environment()
    async def observer(*args):
        clock[0] += 40
    adapter.on_payload = observer
    async with client:
        result = await service.get(item.id, refresh=True)
    assert result['records'][0]['received_at'] == NOW
