"""Actual SDK preprocessing adversarial samples; transport is always local."""
from copy import deepcopy
import sqlite3

import ccxt.async_support as ccxt
import pytest
from fastapi import HTTPException

from test_funding_history import EVENT, NOW, setup_history as _setup_history, reviewed_native_history, configure_reviewed_history, htx_inverse_history, htx_raw, entry
from test_binance_funding_history import prepare as binance_prepare
from watch_money.funding_history import SETTLED_ENDPOINTS

setup_history = _setup_history
CLIENTS = ['binanceusdm', 'binancecoinm', 'okx', 'bybit', 'gate', 'bitget', 'htx']
METHODS = dict(binanceusdm='fapiPublicGetFundingRate', binancecoinm='dapiPublicGetFundingRate',
    okx='publicGetPublicFundingRateHistory', bybit='publicGetV5MarketFundingHistory',
    gate='publicFuturesGetSettleFundingRate', bitget='publicMixGetV2MixMarketHistoryFundRate',
    htx='contractPublicGetSwapApiV1SwapHistoricalFundingRate')


def prepare(factory, key):
    if key.startswith('binance'):
        return binance_prepare(factory, key)
    if key in ('gate', 'bitget'):
        service, item, client, adapter, market = configure_reviewed_history(factory, key)
        raw = {'t': EVENT//1000, 'r': '-.0002'} if key == 'gate' else {
            'symbol': market['id'], 'fundingTime': EVENT, 'fundingRate': '-.0002'}
    elif key == 'htx':
        service, item, client, adapter, market = htx_inverse_history(factory)
        raw = htx_raw(realized_rate='-.0002')
    else:
        service, item, client, adapter = factory(key, venue=key)
        market = adapter._markets[item.id][1]
        raw = reviewed_native_history(key, **({'realizedRate': '-.0002'} if key == 'okx' else {'fundingRate': '-.0002'}))
    market.update(base=item.base, quote=item.quote, type='swap', spot=False, contract=True,
                  linear=item.linear, inverse=not item.linear, contractSize=100,
                  info={'fundingInterval': '480'})
    return service, item, client, adapter, market, raw


def envelope(key, rows):
    if key in ('binanceusdm', 'binancecoinm', 'gate'):
        return deepcopy(rows)
    if key == 'okx':
        return {'code': '0', 'data': deepcopy(rows)}
    if key == 'bybit':
        return {'retCode': 0, 'result': {'category': 'linear', 'list': deepcopy(rows)}}
    if key == 'bitget':
        return {'code': '00000', 'data': deepcopy(rows)}
    return {'status': 'ok', 'data': {'current_page': 1, 'data': deepcopy(rows)}}


def rate_key(key):
    return {'okx': 'realizedRate', 'htx': 'realized_rate', 'gate': 'r'}.get(key, 'fundingRate')


@pytest.mark.parametrize('key', CLIENTS)
@pytest.mark.parametrize('reverse', [False, True])
async def test_actual_sdk_limit_one_hides_conflict_native_validation_does_not(setup_history, key, reverse):
    service, item, _, adapter, market, raw = prepare(setup_history, key)
    client = getattr(ccxt, key)()
    client.set_markets([market])
    rows = [raw, raw | {rate_key(key): '.1'}]
    if reverse:
        rows.reverse()
    calls = []
    async def endpoint(params):
        calls.append(deepcopy(params))
        return envelope(key, rows)
    setattr(client, METHODS[key], endpoint)
    adapter._clients[key] = client
    try:
        params = {'paginate': False}
        if key == 'bybit':
            params['until'] = NOW
        if key == 'bitget':
            params.update(uta=False, pageNo=1)
        if key == 'htx':
            params['page_index'] = 1
        clipped = await client.fetch_funding_rate_history(item.native_symbol, EVENT, 1, params)
        assert len(clipped) == 1
        for _ in range(2):
            result = await service.get(item.id, refresh=True, since=EVENT, limit=1)
            assert result['records'] == []
            assert result['summary']['rejected_count'] == result['summary']['fetched_count'] == 2
            assert result['summary']['accepted_count'] == 0 and result['summary']['next_since'] is None
            rows.reverse()
        with sqlite3.connect(service.path) as db:
            assert db.execute('SELECT COUNT(*) FROM funding_revisions').fetchone()[0] == 0
        assert len(calls) == 3
    finally:
        await adapter.close()


@pytest.mark.parametrize('key', CLIENTS)
async def test_native_oversize_cannot_be_hidden_by_output_limit(setup_history, key):
    service, item, client, _, _, raw = prepare(setup_history, key)
    client.response = [entry(info=raw)] * (SETTLED_ENDPOINTS[key][0] + 1)
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True, limit=1)
    assert error.value.status_code == 502 and service._read(item.id, None, 100) == ([], 0)


@pytest.mark.parametrize('key', CLIENTS)
async def test_native_page_units_and_same_page_cache_protection(setup_history, key):
    service, item, client, _, _, raw = prepare(setup_history, key)
    client.response = [entry(info=raw)]
    before = (await service.get(item.id, refresh=True, since=EVENT, limit=1))['records'][0]
    assert before['funding_rate'] == -.0002 and before['settlement_at'] == EVENT
    assert before['funding_interval_hours'] is None
    for rows in ([raw, raw | {rate_key(key): '.2'}], [raw | {rate_key(key): '.2'}, raw]):
        client.response = [entry(info=r) for r in rows]
        result = await service.get(item.id, refresh=True, limit=1)
        assert result['records'] == [before] and result['summary']['accepted_count'] == 0
        assert result['summary']['rejected_count'] == 2
    with sqlite3.connect(service.path) as db:
        assert db.execute('SELECT COUNT(*) FROM funding_revisions').fetchone()[0] == 0


@pytest.mark.parametrize('key', CLIENTS)
async def test_native_public_http_routes_keep_sdk_limits_and_query_semantics(setup_history, key):
    from urllib.parse import urlsplit, parse_qs
    service, item, _, adapter, market, raw = prepare(setup_history, key)
    client = getattr(ccxt, key)()
    calls, costs = [], []
    paths = {'binanceusdm': '/fapi/v1/fundingRate', 'binancecoinm': '/dapi/v1/fundingRate',
             'okx': '/api/v5/public/funding-rate-history', 'bybit': '/v5/market/funding/history',
             'gate': '/api/v4/futures/usdt/funding_rate', 'bitget': '/api/v2/mix/market/history-fund-rate',
             'htx': '/swap-api/v1/swap_historical_funding_rate'}
    async def throttle(cost):
        costs.append(cost)
    async def fetch(url, method='GET', headers=None, body=None):
        parts = urlsplit(url)
        assert parts.path == paths[key] and method == 'GET' and body is None
        assert not any('auth' in k.lower() or 'api-key' in k.lower() for k in (headers or {}))
        calls.append(parse_qs(parts.query))
        return envelope(key, [raw])
    client.throttle, client.fetch = throttle, fetch
    adapter._clients[key] = client
    try:
        result = await service.get(item.id, refresh=True, since=EVENT, limit=1)
        assert len(result['records']) == len(costs) == len(calls) == 1 and costs[0] > 0
        query = calls[0]
        native_key = {'okx': 'instId', 'htx': 'contract_code', 'gate': 'contract'}.get(key, 'symbol')
        assert query[native_key] == [market['id']]
        if key in ('binanceusdm', 'binancecoinm', 'bybit'):
            assert query['startTime'] == [str(EVENT)]
        if key == 'bybit':
            assert query['endTime'] == [str(NOW)] and query['category'] == ['linear']
        if key == 'gate':
            assert query['from'] == [str(EVENT//1000)]
        if key == 'okx':
            assert query['before'] == [str(EVENT-1)]
        if key == 'bitget':
            assert query['productType'] == ['USDT-FUTURES'] and query['pageNo'] == ['1']
        if key == 'htx':
            assert query['page_index'] == ['1']
    finally:
        await adapter.close()


@pytest.mark.parametrize('key', CLIENTS)
async def test_native_bad_envelope_is_not_silent_empty_success(setup_history, key):
    service, item, client, _, _, _ = prepare(setup_history, key)
    async def invalid(params):
        return {}
    setattr(client, METHODS[key], invalid)
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True)
    assert error.value.status_code == 502 and service._read(item.id, None, 100) == ([], 0)


@pytest.mark.parametrize('key', CLIENTS)
async def test_access_denied_is_not_retried(setup_history, key):
    service, item, client, _, _, _ = prepare(setup_history, key)
    client.error = ccxt.PermissionDenied()
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True)
    assert error.value.status_code == 401 and len(client.calls) == 1
    assert service._read(item.id, None, 100) == ([], 0)


@pytest.mark.parametrize('key,field,value', [('gate', 't', EVENT/1000+.5), ('htx', 'funding_time', EVENT+.5)])
async def test_native_fractional_timestamp_is_not_rounded_to_valid(setup_history, key, field, value):
    service, item, client, _, _, raw = prepare(setup_history, key)
    client.response = [entry(info=raw | {field: value})]
    result = await service.get(item.id, refresh=True)
    assert result['summary']['rejected_count'] == 1 and result['records'] == []


@pytest.mark.parametrize('reverse', [False, True])
async def test_same_realized_rate_with_conflicting_htx_advertised_metadata_is_not_revision(setup_history, reverse):
    service, item, client, _, _, raw = prepare(setup_history, 'htx')
    rows = [raw, raw | {'funding_rate': '.8'}]
    client.response = [entry(info=r) for r in (rows[::-1] if reverse else rows)]
    result = await service.get(item.id, refresh=True, limit=1)
    assert result['records'] == [] and result['summary']['rejected_count'] == 2


async def test_mexc_same_unknown_period_with_conflicting_raw_metadata_is_rejected(setup_history):
    from test_mexc_kucoin_history import prepare as mexc_prepare
    service, item, client, _, _, raw = mexc_prepare(setup_history, 'mexc')
    client.response = [entry(info=raw | {'collectCycle': value}) for value in (None, True)]
    result = await service.get(item.id, refresh=True, limit=1)
    assert result['records'] == [] and result['summary']['rejected_count'] == 2
