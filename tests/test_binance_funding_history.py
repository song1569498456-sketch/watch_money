"""Public schema/installed-parser tests; no Binance network requests."""
from copy import deepcopy

import pytest

from test_funding_history import EVENT, entry, setup_history as _setup_history

setup_history = _setup_history


def prepare(setup_history, client_id):
    service, item, client, adapter = setup_history(client_id)
    inverse = client_id == 'binancecoinm'
    item = item.model_copy(update={'base': 'BTC', 'quote': 'USD' if inverse else 'USDT',
        'settle': 'BTC' if inverse else 'USDT', 'linear': not inverse,
        'native_symbol': 'BTC/USD:BTC' if inverse else 'BTC/USDT:USDT'})
    service.collector.instruments[item.id] = item
    market = {'id': 'BTCUSD_PERP' if inverse else 'BTCUSDT', 'symbol': item.native_symbol,
        'base': item.base, 'baseId': 'BTC', 'quote': item.quote, 'settle': item.settle,
        'swap': True, 'spot': False, 'type': 'swap', 'contract': True, 'linear': not inverse,
        'inverse': inverse, 'contractSize': 100 if inverse else 1}
    adapter._markets[item.id] = (client_id, market)
    raw = {'symbol': market['id'], 'fundingTime': EVENT, 'fundingRate': '-.0002'}
    return service, item, client, adapter, market, raw


@pytest.mark.parametrize('client_id', ['binanceusdm', 'binancecoinm'])
async def test_installed_public_routes_preserve_native_contract_rate_and_time(setup_history, client_id):
    import ccxt.async_support as ccxt
    service, item, _, adapter, market, raw = prepare(setup_history, client_id)
    client = getattr(ccxt, client_id)()
    client.set_markets([market])
    calls = []
    async def public(params):
        calls.append(deepcopy(params))
        return [raw]
    async def forbidden(*args, **kwargs):
        pytest.fail('unexpected endpoint or network')
    client.fetch = forbidden
    client.fapiPublicGetFundingRate = public if item.linear else forbidden
    client.dapiPublicGetFundingRate = forbidden if item.linear else public
    adapter._clients[client_id] = client
    try:
        result = await service.get(item.id, refresh=True, since=EVENT)
        record = result['records'][0]
        assert record['funding_rate'] == -.0002 and record['native_market_id'] == market['id']
        assert record['native_settlement_time'] == EVENT and record['settle'] == item.settle
        assert record['funding_interval_hours'] is None and record['long_cashflow_sign'] == 1
        assert calls == [{'symbol': market['id'], 'startTime': EVENT, 'limit': 100}]
        assert not client.apiKey and not client.secret
    finally:
        await adapter.close()


@pytest.mark.parametrize('client_id', ['binanceusdm', 'binancecoinm'])
@pytest.mark.parametrize('change', [{'symbol': 'OTHER'}, {'fundingTime': EVENT // 1000},
    {'fundingTime': EVENT + 1}, {'fundingRate': None}, {'rateType': 'Unknown'}])
async def test_native_mismatch_and_unknown_event_refuse_unified_fallback(setup_history, client_id, change):
    service, item, client, _, _, raw = prepare(setup_history, client_id)
    client.response = [entry(info=raw | change)]
    report = await service.get(item.id, refresh=True)
    assert not report['records'] and report['summary']['rejected_count'] == 1
    assert report['summary']['next_since'] is None


async def test_coinm_special_event_is_unreviewed(setup_history):
    service, item, client, _, _, raw = prepare(setup_history, 'binancecoinm')
    client.response = [entry(info=raw | {'rateType': 'Special'})]
    report = await service.get(item.id, refresh=True)
    assert not report['records']


@pytest.mark.parametrize('reverse', [False, True])
async def test_same_event_conflict_never_becomes_ordered_revision(setup_history, reverse):
    service, item, client, _, _, raw = prepare(setup_history, 'binanceusdm')
    rows = [entry(info=raw), entry(info=raw | {'fundingRate': '.5'})]
    client.response = rows[::-1] if reverse else rows
    report = await service.get(item.id, refresh=True)
    assert not report['records'] and report['summary']['rejected_count'] == 2
    assert service._read(item.id, None, 100) == ([], 0)


async def test_full_page_cannot_skip_special_event_at_same_time(setup_history):
    service, item, client, _, _, raw = prepare(setup_history, 'binanceusdm')
    client.response = [entry(info=raw | {'rateType': 'Regular'})]
    report = await service.get(item.id, refresh=True, limit=1)
    assert report['records'] and report['summary']['next_since'] is None
    assert 'same-time' in ' '.join(report['summary']['issues'])
