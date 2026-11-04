"""dYdX v4 official schema fixtures; no live or wallet requests."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from watch_money.adapters.dex import DydxAdapter
from watch_money.funding_history import FundingHistoryService
from watch_money.models import Instrument

NOW = 1_800_000_000_000


def row(**changes):
    return {'ticker': 'PEPE-USD', 'rate': '-.0001', 'price': '100',
            'effectiveAt': '2026-01-01T00:00:00.000Z', 'effectiveAtHeight': '1234'} | changes


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr('watch_money.funding_history.now_ms', lambda: NOW)
    monkeypatch.setattr('watch_money.adapters.dex.now_ms', lambda: NOW)
    def make(payload=None, status=200):
        calls = []
        def handler(request):
            calls.append(request)
            assert str(request.url).startswith('https://indexer.dydx.trade/v4/historicalFunding/PEPE-USD?')
            assert request.method == 'GET' and set(request.url.params) == {'limit'}
            assert 'authorization' not in request.headers
            return httpx.Response(status, json=payload if payload is not None else {'historicalFunding': [row()]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = DydxAdapter(client)
        item = Instrument(id='dydx:perpetual:PEPE-USD', venue='dydx', native_symbol='PEPE-USD',
                          base='PEPE', quote='USD', settle='USDC', asset_id='dydx:PEPE',
                          chain='dydx', kind='perpetual')
        collector = SimpleNamespace(instruments={item.id: item}, adapters={'dydx': adapter})
        return FundingHistoryService(tmp_path, collector), item, client, adapter, calls
    return make


async def test_native_history_and_cache_preserve_units_unknown_period(env):
    service, item, client, _, calls = env()
    async with client:
        assert (await service.get(item.id))['records'] == [] and not calls
        result = await service.get(item.id, refresh=True, limit=1000)
        record = result['records'][0]
        assert record['funding_rate'] == -.0001 and record['raw_funding_rate'] == '-.0001'
        assert record['funding_interval_hours'] is None and record['long_cashflow_sign'] == 1
        assert record['settlement_at'] == 1767225600000 and record['native_block_height'] == '1234'
        assert result['summary']['next_since'] is None and result['summary']['truncated']
        assert calls[0].url.params['limit'] == '100'
        await service.get(item.id, refresh=True)
        cached = await service.get(item.id)
        assert len(cached['records']) == 1 and cached['records'][0]['revision'] == 1


@pytest.mark.parametrize('changes', [{'ticker': 'OTHER-USD'}, {'rate': 'NaN'},
    {'effectiveAt': '2026-01-01T00:00:00'}, {'effectiveAt': '2099-01-01T00:00:00Z'},
    {'effectiveAt': 1767225600000}, {'effectiveAtHeight': None}, {'effectiveAtHeight': '-1'}])
async def test_invalid_native_fields_rejected(env, changes):
    service, item, client, _, _ = env({'historicalFunding': [row(**changes)]})
    async with client:
        result = await service.get(item.id, refresh=True)
    assert result['records'] == [] and result['summary']['rejected_count'] == 1


async def test_timezone_and_since_do_not_change_rate_or_fetch_older_page(env):
    service, item, client, _, calls = env({'historicalFunding': [
        row(effectiveAt='2026-01-01T08:00:00+08:00', rate='0'),
        row(effectiveAt='2026-01-01T01:00:00Z', rate='.0002')]})
    async with client:
        result = await service.get(item.id, refresh=True, since=1767225600001)
    assert len(result['records']) == 1 and result['records'][0]['funding_rate'] == .0002
    assert result['records'][0]['long_cashflow_sign'] == -1 and len(calls) == 1
    assert result['summary']['rejected_count'] == 1 and result['summary']['next_since'] is None


@pytest.mark.parametrize('status', [403, 451])
async def test_access_denial_no_retry(env, status):
    service, item, client, _, calls = env(status=status)
    async with client:
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
    assert error.value.status_code == 403 and len(calls) == 1


async def test_wrong_scope_and_changed_catalog_never_persist(env):
    service, item, client, adapter, calls = env()
    async def request(*args, **kwargs):
        service.collector.instruments[item.id] = item.model_copy(update={'settle': 'OTHER'})
        return {'historicalFunding': [row()]}
    adapter.request = request
    async with client:
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
        assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
        assert error.value.status_code == 409 and not calls
        assert (await service.get(item.id))['records'] == []


@pytest.mark.parametrize('payload', [{}, {'historicalFunding': [row()] * 101}])
async def test_malformed_oversized_response(env, payload):
    service, item, client, _, _ = env(payload)
    async with client:
        with pytest.raises(HTTPException) as error:
            await service.get(item.id, refresh=True)
    assert error.value.status_code == 502


@pytest.mark.parametrize('offset,accepted', [(10, False), (-10, True)])
async def test_observer_delay_does_not_move_receipt(env, monkeypatch, offset, accepted):
    from datetime import datetime, timezone
    clock = [NOW]
    monkeypatch.setattr('watch_money.adapters.dex.now_ms', lambda: clock[0])
    monkeypatch.setattr('watch_money.funding_history.now_ms', lambda: clock[0])
    stamp = datetime.fromtimestamp((NOW + offset) / 1000, timezone.utc).isoformat(timespec='milliseconds')
    service, item, client, adapter, _ = env({'historicalFunding': [row(effectiveAt=stamp)]})
    async def observer(*args):
        clock[0] += 40
    adapter.on_payload = observer
    async with client:
        result = await service.get(item.id, refresh=True)
    assert bool(result['records']) is accepted
    if accepted:
        assert result['records'][0]['received_at'] == NOW
    else:
        assert result['summary']['rejected_count'] == 1


async def test_cancelled_observer_does_not_save_or_reuse_receipt(env, monkeypatch):
    import asyncio
    clock = [NOW]
    monkeypatch.setattr('watch_money.adapters.dex.now_ms', lambda: clock[0])
    monkeypatch.setattr('watch_money.funding_history.now_ms', lambda: clock[0])
    service, item, client, adapter, _ = env()
    entered = asyncio.Event()
    async def observer(*args):
        entered.set()
        await asyncio.Event().wait()
    adapter.on_payload = observer
    async with client:
        task = asyncio.create_task(service.get(item.id, refresh=True))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service._read(item.id, None, 100) == ([], 0)
        clock[0] += 100
        adapter.on_payload = None
        result = await service.get(item.id, refresh=True)
        assert result['records'][0]['received_at'] == NOW + 100


async def test_changed_native_height_is_audited_even_with_same_rate(env, monkeypatch):
    import json
    import sqlite3
    clock = [NOW]
    monkeypatch.setattr('watch_money.adapters.dex.now_ms', lambda: clock[0])
    payload = {'historicalFunding': [row()]}
    service, item, client, _, _ = env(payload)
    async with client:
        await service.get(item.id, refresh=True)
        payload['historicalFunding'][0]['effectiveAtHeight'] = '1235'
        clock[0] += 100
        result = await service.get(item.id, refresh=True)
        record = result['records'][0]
        assert record['native_block_height'] == '1235' and record['revision'] == 2
        assert record['first_received_at'] == NOW and record['received_at'] == NOW + 100
        clock[0] += 100
        again = (await service.get(item.id, refresh=True))['records'][0]
        assert again['revision'] == 2 and again['last_seen_at'] == NOW + 200
    with sqlite3.connect(service.path) as db:
        revisions = db.execute('SELECT previous_record,revised_at FROM funding_revisions').fetchall()
    assert len(revisions) == 1 and revisions[0][1] == NOW + 100
    old = json.loads(revisions[0][0])
    assert old['native_block_height'] == '1234' and old['revision'] == 1
    assert old['funding_rate'] == record['funding_rate']
