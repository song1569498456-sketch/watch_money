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
@pytest.mark.parametrize('case', ['symbol', 'seconds', 'time_mismatch', 'missing_rate'])
async def test_raw_fields_required_independently_of_unified_values(setup_history, client_id, case):
    service, item, client, _, _, raw = prepare(setup_history, client_id)
    if case == 'symbol':
        raw['symbol'] = 'OTHER'
    elif case == 'missing_rate':
        del raw['fundingRate']
    else:
        raw['settleTime' if client_id == 'mexc' else 'timepoint'] = EVENT // 1000 if case == 'seconds' else EVENT + 1
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
