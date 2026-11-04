"""Reviewed public historical schemas; all transport is replaced locally."""
from copy import deepcopy

import pytest

from test_funding_history import EVENT, NOW, entry, setup_history as _setup_history
setup_history = _setup_history


def prepare(factory, client_id):
    venue = 'mexc' if client_id == 'mexc' else 'kucoin'
    service, item, client, adapter = factory(client_id, venue=venue)
    native = 'PEPE_USDT' if client_id == 'mexc' else 'PEPEUSDTM'
    market = {'id': native, 'symbol': item.native_symbol, 'base': 'PEPE', 'quote': 'USDT',
              'settle': 'USDT', 'swap': True, 'spot': False, 'contract': True, 'type': 'swap',
              'linear': True, 'inverse': False, 'contractSize': 10, 'info': {'fundingInterval': 8}}
    adapter._markets[item.id] = (client_id, market)
    raw = {'symbol': native, 'fundingRate': '-.0002',
           'settleTime' if client_id == 'mexc' else 'timepoint': EVENT}
    return service, item, client, adapter, market, raw


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
async def test_installed_public_history_routing_time_and_units(setup_history, client_id):
    import ccxt.async_support as ccxt
    service, item, _, adapter, market, raw = prepare(setup_history, client_id)
    client = getattr(ccxt, client_id)()
    client.set_markets([market])
    calls = []
    async def public(params):
        calls.append(deepcopy(params))
        if client_id == 'mexc':
            return {'success': True, 'code': 0, 'data': {'currentPage': 1, 'resultList': [raw | {'collectCycle': 4}]}}
        return {'code': '200000', 'data': [raw]}
    async def forbidden(*args, **kwargs):
        pytest.fail('unexpected network, private or UTA route')
    client.fetch = forbidden
    if client_id == 'mexc':
        client.contractPublicGetFundingRateHistory = public
    else:
        client.futuresPublicGetContractFundingRates = public
        client.utaGetMarketFundingRateHistory = forbidden
    adapter._clients[client_id] = client
    try:
        result = await service.get(item.id, refresh=True, since=EVENT)
        record = result['records'][0]
        assert record['native_market_id'] == market['id'] and record['native_settlement_time'] == EVENT
        assert record['funding_rate'] == -.0002 and record['long_cashflow_sign'] == 1
        assert record['funding_interval_hours'] == (4 if client_id == 'mexc' else None)
        assert result['summary']['next_since'] is None
        expected = {'symbol': market['id'], 'page_num': 1, 'page_size': 100} if client_id == 'mexc' else {
            'symbol': market['id'], 'from': EVENT, 'to': NOW}
        assert calls == [expected]
    finally:
        await adapter.close()


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
@pytest.mark.parametrize('case', ['symbol', 'seconds', 'future_time', 'missing_rate'])
async def test_native_fields_required_without_unified_fallback(setup_history, client_id, case):
    service, item, client, _, _, raw = prepare(setup_history, client_id)
    if case == 'symbol':
        raw['symbol'] = 'OTHER'
    elif case == 'missing_rate':
        del raw['fundingRate']
    else:
        raw['settleTime' if client_id == 'mexc' else 'timepoint'] = EVENT // 1000 if case == 'seconds' else NOW + 1
    client.response = [entry(info=raw)]
    result = await service.get(item.id, refresh=True)
    assert result['records'] == [] and result['summary']['rejected_count'] == 1


@pytest.mark.parametrize('period', [None, True, 0, -1, .5, 'NaN', 'Infinity'])
async def test_invalid_mexc_period_preserves_rate_but_period_is_unknown(setup_history, period):
    service, item, client, _, _, raw = prepare(setup_history, 'mexc')
    raw['collectCycle'] = period
    client.response = [entry(info=raw)]
    result = await service.get(item.id, refresh=True)
    record = result['records'][0]
    assert record['funding_rate'] == -.0002
    assert record['funding_interval_hours'] is None and record['period_source'] is None


async def test_kucoin_does_not_inherit_mexc_or_catalog_cycle(setup_history):
    service, item, client, _, _, raw = prepare(setup_history, 'kucoinfutures')
    client.response = [entry(info=raw | {'collectCycle': 8})]
    record = (await service.get(item.id, refresh=True))['records'][0]
    assert record['funding_interval_hours'] is None


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
async def test_conflicting_same_settlement_is_not_a_response_order_revision(setup_history, client_id):
    service, item, client, _, _, raw = prepare(setup_history, client_id)
    client.response = [entry(info=raw), entry(info=raw | {'fundingRate': '.1'})]
    report = await service.get(item.id, refresh=True)
    assert report['records'] == [] and report['summary']['rejected_count'] == 2
    assert service._read(item.id, None, 100) == ([], 0)


async def test_conflicting_mexc_period_cannot_rewrite_same_page_record(setup_history):
    service, item, client, _, _, raw = prepare(setup_history, 'mexc')
    client.response = [entry(info=raw | {'collectCycle': 4}), entry(info=raw | {'collectCycle': 8})]
    report = await service.get(item.id, refresh=True)
    assert report['records'] == [] and report['summary']['rejected_count'] == 2


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
@pytest.mark.parametrize('reverse', [False, True])
async def test_installed_parser_limit_one_cannot_hide_raw_conflict(setup_history, client_id, reverse):
    import ccxt.async_support as ccxt
    service, item, _, adapter, market, raw = prepare(setup_history, client_id)
    client = getattr(ccxt, client_id)()
    client.set_markets([market])
    rows = [raw, raw | {'fundingRate': '.1'}]
    if reverse:
        rows.reverse()
    async def public(params):
        if client_id == 'mexc':
            return {'success': True, 'code': 0, 'data': {'currentPage': 1, 'resultList': deepcopy(rows)}}
        return {'code': '200000', 'data': deepcopy(rows)}
    if client_id == 'mexc':
        client.contractPublicGetFundingRateHistory = public
    else:
        client.futuresPublicGetContractFundingRates = public
    adapter._clients[client_id] = client
    try:
        # Reproduce the actual SDK's pre-validation loss, not a mock parser.
        clipped = await client.fetch_funding_rate_history(item.native_symbol, EVENT, 1, {'uta': False} if client_id == 'kucoinfutures' else {})
        assert len(clipped) == 1
        for _ in range(2):
            result = await service.get(item.id, refresh=True, since=EVENT, limit=1)
            assert result['records'] == []
            assert result['summary']['fetched_count'] == result['summary']['rejected_count'] == 2
            assert result['summary']['accepted_count'] == 0 and result['summary']['next_since'] is None
            rows.reverse()
        import sqlite3
        with sqlite3.connect(service.path) as db:
            assert db.execute('SELECT COUNT(*) FROM funding_revisions').fetchone()[0] == 0
        assert service._read(item.id, None, 100) == ([], 0)
    finally:
        await adapter.close()


@pytest.mark.parametrize('client_id,cap', [('mexc', 100), ('kucoinfutures', 1000)])
async def test_oversized_raw_page_fails_before_any_save(setup_history, client_id, cap):
    from fastapi import HTTPException
    service, item, client, _, _, raw = prepare(setup_history, client_id)
    client.response = [entry(info=raw)] * (cap+1)
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True, limit=1)
    assert error.value.status_code == 502
    assert service._read(item.id, None, 100) == ([], 0)


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
async def test_output_limit_does_not_limit_validation_or_units(setup_history, client_id):
    service, item, client, _, _, raw = prepare(setup_history, client_id)
    time_key = 'settleTime' if client_id == 'mexc' else 'timepoint'
    client.response = [entry(info=raw | {time_key: EVENT+i*1000, 'collectCycle': 4}) for i in range(3)]
    result = await service.get(item.id, refresh=True, since=EVENT, limit=1)
    assert len(result['records']) == 1 and result['summary']['accepted_count'] == 3
    assert result['summary']['fetched_count'] == result['summary']['cached_matching_count'] == 3
    assert result['summary']['truncated'] and result['summary']['next_since'] is None
    stored, count = service._read(item.id, None, 100)
    assert count == 3 and all(r['funding_rate'] == -.0002 for r in stored)
    assert all(r['funding_interval_hours'] == (4 if client_id == 'mexc' else None) for r in stored)


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
@pytest.mark.parametrize('case', ['error', 'shape', 'page'])
async def test_bad_native_envelope_cannot_be_empty_success(setup_history, client_id, case):
    from fastapi import HTTPException
    service, item, client, _, _, _ = prepare(setup_history, client_id)
    async def public(params):
        if client_id == 'mexc':
            return {'success': case != 'error', 'code': 0, 'data': {'currentPage': 2 if case == 'page' else 1,
                    'resultList': {} if case == 'shape' else []}}
        return {'code': '400001' if case == 'error' else '200000',
                'data': {} if case in ('shape', 'page') else []}
    if client_id == 'mexc':
        client.contractPublicGetFundingRateHistory = public
    else:
        client.futuresPublicGetContractFundingRates = public
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True, limit=1)
    assert error.value.status_code == 502


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
async def test_native_public_transport_keeps_sdk_throttling(setup_history, client_id):
    from urllib.parse import parse_qs, urlsplit
    import ccxt.async_support as ccxt
    service, item, _, adapter, market, raw = prepare(setup_history, client_id)
    client = getattr(ccxt, client_id)()
    costs, calls = [], []
    async def throttle(cost):
        costs.append(cost)
    async def fetch(url, method='GET', headers=None, body=None):
        parsed = urlsplit(url)
        assert method == 'GET' and body is None
        assert not any('auth' in k.lower() or 'api-key' in k.lower() for k in (headers or {}))
        calls.append(parse_qs(parsed.query))
        if client_id == 'mexc':
            assert parsed.hostname == 'api.mexc.com' and parsed.path == '/api/v1/contract/funding_rate/history'
            return {'success': True, 'code': 0, 'data': {'currentPage': 1, 'resultList': [raw]}}
        assert parsed.hostname == 'api-futures.kucoin.com' and parsed.path == '/api/v1/contract/funding-rates'
        return {'code': '200000', 'data': [raw]}
    client.throttle, client.fetch = throttle, fetch
    adapter._clients[client_id] = client
    try:
        result = await service.get(item.id, refresh=True, since=EVENT, limit=1)
        assert len(result['records']) == 1 and len(costs) == 1 and costs[0] > 0
        assert calls[0]['symbol'] == [market['id']]
        assert ('page_size' in calls[0]) == (client_id == 'mexc')
    finally:
        await adapter.close()


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
@pytest.mark.parametrize('kind,status', [('timeout', 504), ('denied', 401), ('rate_limit', 502)])
async def test_native_errors_do_not_retry_or_save(setup_history, client_id, kind, status):
    from fastapi import HTTPException
    import ccxt
    service, item, client, _, _, _ = prepare(setup_history, client_id)
    client.error = {'timeout': TimeoutError(), 'denied': ccxt.PermissionDenied(),
                    'rate_limit': ccxt.RateLimitExceeded()}[kind]
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True, limit=1)
    assert error.value.status_code == status and len(client.calls) == 1
    assert service._read(item.id, None, 100) == ([], 0)


@pytest.mark.parametrize('client_id', ['mexc', 'kucoinfutures'])
async def test_raw_conflict_cannot_revise_existing_cache(setup_history, client_id):
    import sqlite3
    service, item, client, _, _, raw = prepare(setup_history, client_id)
    client.response = [entry(info=raw)]
    before = (await service.get(item.id, refresh=True, limit=1))['records'][0]
    for rows in ([raw, raw | {'fundingRate': '.1'}], [raw | {'fundingRate': '.1'}, raw]):
        client.response = [entry(info=r) for r in rows]
        result = await service.get(item.id, refresh=True, limit=1)
        assert result['records'] == [before]  # Explicit local_cache_query, not new acceptance.
        assert result['summary']['records_scope'] == 'local_cache_query'
        assert result['summary']['accepted_count'] == 0 and result['summary']['rejected_count'] == 2
    with sqlite3.connect(service.path) as db:
        assert db.execute('SELECT COUNT(*) FROM funding_revisions').fetchone()[0] == 0
