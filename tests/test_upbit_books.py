"""Korea public book fixtures; no live exchange requests."""
import asyncio
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import ccxt.async_support as ccxt
import pytest

from watch_money.adapters.cex import CexAdapter, _upbit_book_patch, _upbit_book_values
from watch_money.models import Instrument

NOW = 1_800_000_000_000


def row(native, **changes):
    return dict(market=native, timestamp=NOW, level=0, orderbook_units=[
        dict(bid_price=100, ask_price=101, bid_size=2, ask_size=3)]) | changes


@pytest.fixture
async def env(monkeypatch):
    clock = [NOW]
    monkeypatch.setattr('watch_money.adapters.cex.now_ms', lambda: clock[0])
    monkeypatch.setattr('watch_money.adapters.bulk_tickers.now_ms', lambda: clock[0])
    client = ccxt.upbit({'enableRateLimit': False})
    adapter = CexAdapter('upbit', client_factory=lambda _: client)
    items = []
    for i in range(120):
        quote = ('KRW', 'BTC', 'USDT')[i % 3]
        base = f'SMALL{i}'
        item = Instrument(id=f'cex:upbit:upbit:spot:{base}/{quote}', venue='upbit',
            native_symbol=f'{base}/{quote}', base=base, quote=quote, asset_id=f'upbit:{base}', kind='spot')
        adapter._markets[item.id] = ('upbit', dict(id=f'{quote}-{base}', spot=True, symbol=item.native_symbol))
        items.append(item)
    calls, response = [], [None]
    async def fetch(url, method='GET', headers=None, body=None):
        parsed = urlsplit(url)
        assert parsed.hostname == 'api.upbit.com' and parsed.path == '/v1/orderbook'
        assert method == 'GET' and body is None
        assert not any('auth' in key.lower() for key in (headers or {}))
        params = parse_qs(parsed.query)
        calls.append(params)
        assert params['level'] == ['0']
        if isinstance(response[0], Exception):
            raise response[0]
        return deepcopy(response[0]) if response[0] is not None else [row(n) for n in params['markets'][0].split(',')]
    client.fetch = fetch
    async def tickers(symbols, params):
        selected = [i for i in items if symbols is None or i.native_symbol in symbols]
        return {i.native_symbol: dict(symbol=i.native_symbol, timestamp=NOW-1000, last=99, baseVolume=7) for i in selected}
    client.fetch_tickers = tickers
    yield adapter, items, calls, response, clock
    await adapter.close()


async def test_catalog_batches_and_independent_field_clocks(env):
    adapter, items, calls, _, _ = env
    batches = adapter.plan_light(items)
    assert len(batches) == 1 and batches[0].full_product
    assert adapter.minimum_light_cycle_seconds(batches) == pytest.approx(.3)
    result = await adapter.fetch_light_batch(batches[0])
    assert not result.errors and len(result.snapshots) == 120
    assert [len(c['markets'][0].split(',')) for c in calls] == [50, 50, 20]
    assert all(c['count'] == ['1'] for c in calls)
    for patch in result.snapshots:
        assert patch.bid == 100 and patch.ask == 101 and patch.last == 99 and patch.volume == 7
        assert patch.field_timestamps['last'] == NOW-1000
        assert patch.field_timestamps['bid'] == NOW
        assert patch.field_received_at['last'] == patch.field_received_at['bid'] == NOW
        assert not patch.is_demo
        from watch_money.scanner import funding_field_state
        assert funding_field_state(patch, next(i for i in items if i.id == patch.instrument_id), 'bid', NOW, 60)['rankable']


async def test_depth_base_units_and_native_time(env):
    adapter, items, calls, _, _ = env
    book = await adapter.orderbook(items[0])
    assert book.bids == [(100, 2)] and book.asks == [(101, 3)]
    assert book.quantity_unit == 'base' and book.source_at == book.received_at == NOW
    assert calls[0]['count'] == ['30']


@pytest.mark.parametrize('case', ['missing', 'duplicate', 'grouped', 'malformed', 'wrong_market'])
async def test_bad_market_does_not_discard_other_ticker_fields(env, case):
    adapter, items, _, response, _ = env
    native = adapter._markets[items[0].id][1]['id']
    bad = row(native)
    if case == 'missing':
        response[0] = []
    elif case == 'duplicate':
        response[0] = [bad, bad]
    elif case == 'grouped':
        response[0] = [bad | {'level': 100}]
    elif case == 'wrong_market':
        response[0] = [bad | {'market': 'KRW-OTHER'}]
    else:
        response[0] = [bad | {'orderbook_units': [{'bid_price': 100}]}]
    patches = await adapter.fetch_light(items[:1])
    assert len(patches) == 1 and patches[0].last == 99 and patches[0].bid is None
    assert items[0].id in adapter.light_errors


async def test_book_transport_failure_preserves_trade_data(env):
    adapter, items, _, response, _ = env
    response[0] = TimeoutError()
    patches = await adapter.fetch_light(items[:1])
    assert patches[0].last == 99 and patches[0].bid is None
    assert adapter.light_errors[items[0].id] == 'TimeoutError'


@pytest.mark.parametrize('timestamp', [None, NOW-70_000, NOW+10])
async def test_no_retiming_through_observer(env, timestamp):
    adapter, items, _, response, clock = env
    response[0] = [row(adapter._markets[items[0].id][1]['id'], timestamp=timestamp)]
    async def observer(*args):
        if args[1].endswith('publicGetOrderbook'):
            clock[0] += 40
    adapter.on_payload = observer
    patch = (await adapter.fetch_light(items[:1]))[0]
    assert patch.field_timestamps['bid'] == timestamp
    assert patch.field_received_at['bid'] == NOW and patch.received_at == NOW
    assert clock[0] == NOW+40
    from watch_money.scanner import funding_field_state
    assert not funding_field_state(patch, items[0], 'bid', NOW+40, 60)['rankable']


async def test_catalog_change_during_observer_rejects_book(env):
    adapter, items, _, _, _ = env
    async def observer(*args):
        if args[1].endswith('publicGetOrderbook'):
            adapter._markets[items[0].id] = ('upbit', dict(id='KRW-OTHER', symbol=items[0].native_symbol, spot=True))
    adapter.on_payload = observer
    patch = (await adapter.fetch_light(items[:1]))[0]
    assert patch.bid is None and adapter.light_errors[items[0].id] == 'ValueError'


async def test_observer_cancellation_and_next_poll(env):
    adapter, items, _, _, _ = env
    async def observer(*args):
        if args[1].endswith('publicGetOrderbook'):
            raise asyncio.CancelledError
    adapter.on_payload = observer
    with pytest.raises(asyncio.CancelledError):
        await adapter.fetch_light(items[:1])
    adapter.on_payload = None
    assert (await adapter.fetch_light(items[:1]))[0].bid == 100


@pytest.mark.parametrize('side', ['both', 'bid', 'ask'])
def test_complete_snapshot_empty_sides_are_explicit(side):
    item = Instrument(id='upbit:test', venue='upbit', native_symbol='TEST/KRW', base='TEST', quote='KRW', asset_id='test', kind='spot')
    payload = row('KRW-TEST')
    if side == 'both':
        payload['orderbook_units'] = []
    else:
        payload['orderbook_units'][0][side+'_size'] = 0
    bids, asks, stamp = _upbit_book_values(payload)
    patch = _upbit_book_patch(item, bids, asks, stamp, NOW)
    assert set(patch.cleared_fields) == ({'bid', 'ask'} if side == 'both' else {side})
    assert all(patch.model_dump(exclude_unset=True)[key] is None for key in patch.cleared_fields)
    recovered = _upbit_book_patch(item, [(100, 2)], [(101, 3)], NOW+1, NOW+1)
    assert not recovered.cleared_fields and recovered.bid == 100


async def test_clear_barrier_and_out_of_order_recovery(env, tmp_path):
    from watch_money.collector import Collector
    from watch_money.storage import Storage
    from test_gate_perp_integration import put
    from watch_money.models import now_ms
    _, items, _, _, _ = env
    item = items[0]
    collector = Collector(Storage(tmp_path), {})
    at = now_ms()-1000
    def patch(stamp, empty=False):
        return _upbit_book_patch(item, [] if empty else [(100, 2)], [(101, 3)], stamp, stamp+1)
    assert put(collector, patch(at)).bid == 100
    assert put(collector, patch(at+10, True)).bid is None
    assert put(collector, patch(at)).bid is None
    assert put(collector, patch(at+10)).bid is None
    assert put(collector, patch(at+20)).bid == 100


async def test_one_bad_record_keeps_other_native_book(env):
    adapter, items, _, response, _ = env
    response[0] = [row(adapter._markets[items[0].id][1]['id'], level=100),
                   row(adapter._markets[items[1].id][1]['id'])]
    patches = await adapter.fetch_light(items[:2])
    assert patches[0].bid is None and patches[1].bid == 100
    assert set(adapter.light_errors) == {items[0].id}
