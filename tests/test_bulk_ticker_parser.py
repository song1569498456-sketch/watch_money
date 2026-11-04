"""Real CCXT parsers with local raw payloads; no network stability claims."""
import asyncio
import threading

import ccxt.async_support as ccxt
import pytest

from watch_money.adapters.bulk_tickers import BulkParseBusy, BulkTickerParser
from watch_money.adapters.cex import _light_ticker_patch
from watch_money.models import Instrument


def client_and_response(venue, kind='spot', settle='USDT'):
    client = getattr(ccxt, venue)({'enableRateLimit': True})
    spot = kind == 'spot'
    symbol = 'BTC/USDT' if spot else f'BTC/USDT:{settle}'
    market_id = ('BTC-USDT' if spot else 'BTC-USDT-SWAP') if venue == 'okx' else 'BTCUSDT'
    client.set_markets([{'id': market_id, 'symbol': symbol, 'base': 'BTC', 'quote': 'USDT',
        'baseId': 'BTC', 'quoteId': 'USDT', 'settle': None if spot else settle,
        'spot': spot, 'swap': not spot, 'future': False, 'option': False,
        'contract': not spot, 'linear': not spot and settle != 'BTC', 'inverse': not spot and settle == 'BTC',
        'active': True, 'type': kind, 'contractSize': 1, 'precision': {'price': .01, 'amount': .001}}])
    raw = ({'instId': market_id, 'instType': 'SPOT' if spot else 'SWAP', 'ts': '1800000000000',
        'bidPx': '99.01', 'askPx': '100.03', 'bidSz': '3.12', 'askSz': '4.51', 'last': '99.50',
        'open24h': '90', 'high24h': '101.25', 'low24h': '88', 'volCcy24h': '198000', 'vol24h': '2000'}
        if venue == 'okx' else {'symbol': market_id, 'ts': '1800000000000', 'bidPr': '99.01',
        'askPr': '100.03', 'bidSz': '3.12', 'askSz': '4.51', 'lastPr': '99.50', 'open': '90',
        'high24h': '101.25', 'low24h': '88', 'quoteVolume': '198000', 'baseVolume': '2000',
        'change24h': '.1055', **({'markPrice': '99.51', 'indexPrice': '99.49'} if not spot else {})})
    return client, symbol, {'data': [raw]}


@pytest.mark.parametrize('venue,kind,settle', [('okx', 'spot', 'USDT'), ('okx', 'swap', 'USDT'),
    ('bitget', 'spot', 'USDT'), ('bitget', 'swap', 'USDT'), ('bitget', 'swap', 'USDC'), ('bitget', 'swap', 'BTC')])
@pytest.mark.parametrize('selected', [True, False])
async def test_bulk_matches_upstream_requests_and_every_returned_metric(venue, kind, settle, selected, monkeypatch):
    client, symbol, response = client_and_response(venue, kind, settle)
    calls = []
    for method in ('publicGetMarketTickers', 'publicSpotGetV2SpotMarketTickers', 'publicMixGetV2MixMarketTickers'):
        async def reply(params, method=method):
            calls.append((method, params))
            return response
        monkeypatch.setattr(client, method, reply, raising=False)
    symbols = [symbol] if selected else None
    params = {'type': kind}
    if venue == 'bitget' and kind == 'swap':
        params.update(subType='inverse' if settle == 'BTC' else 'linear',
                      productType={'USDT': 'USDT-FUTURES', 'USDC': 'USDC-FUTURES', 'BTC': 'COIN-FUTURES'}[settle])
    original = await client.fetch_tickers(symbols, params)
    original_requests = list(calls)
    calls.clear()
    parser = BulkTickerParser()
    monkeypatch.setattr('watch_money.adapters.bulk_tickers.now_ms', lambda: 1800000000123)
    try:
        actual = await parser.fetch(client, symbols, params)
        assert actual == original
        assert calls == original_requests
        assert actual.received_at == 1800000000123
        assert actual[symbol]['info'] == response['data'][0]
        instrument = Instrument(id='fixture', venue=venue, native_symbol=symbol, base='BTC', quote='USDT',
                                asset_id='btc', kind='spot' if kind == 'spot' else 'perpetual')
        patch = _light_ticker_patch(instrument, venue, actual[symbol], 'fetch_tickers', client.market(symbol),
                                   received_at=actual.received_at)
        assert patch.source_at == 1800000000000 and patch.received_at == 1800000000123
    finally:
        await parser.close()
        await client.close()


async def test_slow_parser_loop_responsive_bounded_cancel_and_close(monkeypatch):
    client, symbol, response = client_and_response('okx')
    async def reply(_):
        return response
    monkeypatch.setattr(client, 'publicGetMarketTickers', reply)
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    worker_threads = []
    original_parse = BulkTickerParser._parse
    def slow(*args):
        worker_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return original_parse(*args)
    monkeypatch.setattr(BulkTickerParser, '_parse', staticmethod(slow))
    parser = BulkTickerParser()
    first = asyncio.create_task(parser.fetch(client, None, {'type': 'spot'}))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        ticked = 0
        for _ in range(10):
            await asyncio.sleep(.001)
            ticked += 1
        first.cancel()
        await asyncio.sleep(.01)
        assert not first.done() and parser.pending == 1 and ticked == 10
        second = asyncio.create_task(parser.fetch(client, None, {'type': 'spot'}))
        await asyncio.sleep(.01)
        with pytest.raises(BulkParseBusy):
            await parser.fetch(client, None, {'type': 'spot'})
        closing = asyncio.create_task(parser.close())
        await asyncio.sleep(.01)
        assert not closing.done() and len(worker_threads) == 1
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(BulkParseBusy):
        await second
    await closing
    assert parser.pending == 0 and all(ident != owner for ident in worker_threads)
    assert parser.closed
    await client.close()


async def test_unsupported_routing_keeps_upstream_and_parse_errors_surface(monkeypatch):
    client, symbol, response = client_and_response('bitget')
    calls = []
    async def fallback(symbols, params):
        calls.append((symbols, params))
        return {'upstream': True}
    monkeypatch.setattr(client, 'fetch_tickers', fallback)
    parser = BulkTickerParser()
    assert await parser.fetch(client, [symbol], {'type': 'spot', 'uta': True}) == {'upstream': True}
    assert calls == [([symbol], {'type': 'spot', 'uta': True})]
    async def reply(_):
        return response
    monkeypatch.setattr(client, 'publicSpotGetV2SpotMarketTickers', reply)
    def fail(*_):
        raise ValueError('synthetic parse failure')
    monkeypatch.setattr(BulkTickerParser, '_parse', staticmethod(fail))
    with pytest.raises(ValueError):
        await parser.fetch(client, None, {'type': 'spot'})
    assert parser.metrics['failed'] == 1 and parser.pending == 0
    await parser.close()
    await client.close()


async def test_unreviewed_ccxt_version_uses_upstream(monkeypatch):
    client, symbol, _ = client_and_response('okx')
    parser = BulkTickerParser()
    async def original(symbols, params):
        return {'fallback': True}
    monkeypatch.setattr(client, 'fetch_tickers', original)
    monkeypatch.setattr('watch_money.adapters.bulk_tickers.ccxt.__version__', 'unreviewed')
    assert await parser.fetch(client, None, {'type': 'spot'}) == {'fallback': True}
    assert parser.diagnostics()['fallbacks'] == 1
    assert parser.diagnostics()['completed'] == 0
    await parser.close()
    await client.close()


async def test_adapter_reopen_after_close_gets_new_bounded_parser():
    from watch_money.adapters.cex import CexAdapter
    adapter = CexAdapter('okx', client_factory=lambda _: client_and_response('okx')[0])
    first = adapter._client('okx')
    parser = adapter._ticker_parser
    await adapter.close()
    assert parser.closed
    second = adapter._client('okx')
    assert second is not first and adapter._ticker_parser is not parser
    assert not adapter._ticker_parser.closed
    await adapter.close()


async def test_catalog_replacement_during_http_cannot_mix_parser_metadata(monkeypatch):
    client, symbol, response = client_and_response('okx')
    expected = client.parse_tickers(response['data'], [symbol])
    async def replace_then_reply(_):
        market = dict(client.market(symbol))
        market['symbol'] = 'RENAMED/USDT'
        market['base'] = 'RENAMED'
        client.set_markets([market])
        return response
    monkeypatch.setattr(client, 'publicGetMarketTickers', replace_then_reply)
    parser = BulkTickerParser()
    try:
        actual = await parser.fetch(client, [symbol], {'type': 'spot'})
        assert actual == expected
        assert 'RENAMED/USDT' in client.markets and symbol not in client.markets
    finally:
        await parser.close()
        await client.close()


async def test_adapter_keeps_batch_contract_metadata_across_http_await(monkeypatch):
    from watch_money.adapters.cex import CexAdapter
    client, symbol, response = client_and_response('okx', 'swap')
    response['data'][0].pop('volCcy24h')
    item = Instrument(id='fixture', venue='okx', native_symbol=symbol, base='BTC', quote='USDT',
                      asset_id='btc', kind='perpetual', settle='USDT', linear=True, contract_size=1)
    adapter = CexAdapter('okx', client_factory=lambda _: client)
    adapter._markets[item.id] = ('okx', dict(client.market(symbol)))
    async def replace_then_reply(_):
        adapter._markets[item.id] = ('okx', {**adapter._markets[item.id][1], 'contractSize': 100})
        return response
    monkeypatch.setattr(client, 'publicGetMarketTickers', replace_then_reply)
    try:
        patches = await adapter.fetch_light([item])
        assert len(patches) == 1 and patches[0].volume == 2000 and patches[0].volume_unit == 'BTC'
        assert adapter._markets[item.id][1]['contractSize'] == 100
    finally:
        await adapter.close()


async def test_parse_delay_does_not_rejuvenate_receipt(monkeypatch):
    client, symbol, response = client_and_response('okx')
    clock = [1800000000100]
    monkeypatch.setattr('watch_money.adapters.bulk_tickers.now_ms', lambda: clock[0])
    async def reply(_):
        return response
    monkeypatch.setattr(client, 'publicGetMarketTickers', reply)
    original = BulkTickerParser._parse
    def delayed(*args):
        clock[0] += 5000
        return original(*args)
    monkeypatch.setattr(BulkTickerParser, '_parse', staticmethod(delayed))
    parser = BulkTickerParser()
    try:
        values = await parser.fetch(client, None, {'type': 'spot'})
        assert values.received_at == 1800000000100
        assert values[symbol]['timestamp'] == 1800000000000
        assert parser.diagnostics()['last']['finished_at'] == 1800000005100
    finally:
        await parser.close()
        await client.close()
