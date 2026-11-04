"""Official public Exchange shapes; offline only, independent of Advanced Trade."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

import ccxt.async_support as ccxt
import pytest

from watch_money.adapters.cex import CexAdapter, _coinbase_book_patch
from watch_money.asset_verification import public_source_scope

NOW = 1_800_000_000_000


def product(base='PEPE', **changes):
    return dict(id=f'{base}-USD', base_currency=base, quote_currency='USD',
                base_increment='0.01', quote_increment='0.001', status='online',
                trading_disabled=False, cancel_only=False, auction_mode=False) | changes


def book(**changes):
    return dict(time=datetime.fromtimestamp(NOW / 1000, timezone.utc).isoformat(), sequence=123,
                bids=[['100', '2', 5]], asks=[['101', '3', 7]], auction_mode=False) | changes


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setattr('watch_money.adapters.cex.now_ms', lambda: NOW)
    client = ccxt.coinbaseexchange()
    products = [product(), product('SMALL'), product('ALIAS', base_currency='OTHER')]
    calls, current = [], [book()]
    async def fetch(url, method='GET', headers=None, body=None):
        parts = urlsplit(url)
        assert parts.hostname == 'api.exchange.coinbase.com' and method == 'GET' and body is None
        assert not any('auth' in key.lower() or 'access' in key.lower() for key in (headers or {}))
        calls.append((parts.path, parse_qs(parts.query)))
        if parts.path == '/products':
            return deepcopy(products)
        assert parts.path.startswith('/products/') and parts.path.endswith('/book')
        assert parse_qs(parts.query).get('level') in (['1'], ['2'])
        return deepcopy(current[0])
    client.fetch = fetch
    async def load_markets(reload=False):
        markets = await client.fetch_markets()
        client.set_markets(markets)
        return client.markets
    client.load_markets = load_markets
    class Advanced:
        async def load_markets(self, reload=False):
            return {'PEPE/USD': dict(id='PEPE-USD', symbol='PEPE/USD', base='PEPE', baseId='PEPE',
                quote='USD', quoteId='USD', spot=True, swap=False, active=True)}
        async def close(self):
            pass
    adapter = CexAdapter('coinbase', client_factory=lambda key: client if key == 'coinbaseexchange' else Advanced())
    items = await adapter.discover()
    item = next(item for item in items if 'coinbaseexchange' in item.id and item.base == 'PEPE')
    yield adapter, item, items, current, calls
    await adapter.close()


async def test_native_catalog_identity_is_scoped_and_not_ticker_merged(env):
    adapter, item, items, _, calls = env
    assert adapter.discovery_errors == {"coinbaseexchange:native_currency_scope": "NativeCurrencyMismatch"}
    assert not adapter.discovery_complete
    assert len(items) == 3  # two Exchange products, one distinct Advanced Trade product
    advanced = next(i for i in items if ':coinbase:spot:' in i.id)
    assert item.asset_id != advanced.asset_id and not item.mapping_verified
    assert public_source_scope(item, 'coinbase') == ('PEPE', None)
    assert public_source_scope(advanced, 'coinbase')[1] == 'native_client_out_of_source_scope'
    assert all(i.base != 'ALIAS' for i in items)
    assert calls == [('/products', {})]
    assert all(not batch.full_product for batch in adapter.plan_light([item]))


async def test_native_l1_and_l2_ignore_ticker_candles_and_order_count(env):
    adapter, item, _, _, calls = env
    patch = (await adapter.fetch_light([item]))[0]
    assert patch.bid == 100 and patch.ask == 101 and patch.source_at == NOW
    assert patch.field_timestamps == {'bid': NOW, 'ask': NOW}
    detailed = (await adapter.fetch([item]))[0]
    assert detailed.source_at == NOW and detailed.last is None
    depth = await adapter.orderbook(item)
    assert depth.bids == [(100, 2)] and depth.asks == [(101, 3)] and depth.quantity_unit == 'base'
    assert depth.source_at == NOW
    assert calls[1:] == [('/products/PEPE-USD/book', {'level': ['1']}),
                        ('/products/PEPE-USD/book', {'level': ['1']}),
                        ('/products/PEPE-USD/book', {'level': ['2']})]


@pytest.mark.parametrize('changes,clears', [({'bids': []}, ['bid']), ({'asks': []}, ['ask']),
    ({'bids': [], 'asks': []}, ['bid', 'ask']), ({'auction_mode': True}, ['bid', 'ask'])])
async def test_empty_and_auction_book_explicitly_clear(env, changes, clears):
    adapter, item, _, current, _ = env
    current[0] = book(**changes)
    patch = (await adapter.fetch_light([item]))[0]
    assert patch.cleared_fields == clears
    if changes.get('auction_mode'):
        assert not patch.valid
        depth = await adapter.orderbook(item)
        assert depth.bids == depth.asks == []


@pytest.mark.parametrize('time', [None, '2026-11-04T00:00:00', 'bad', NOW])
async def test_missing_or_non_native_time_never_uses_receipt(env, time):
    adapter, item, _, current, _ = env
    current[0] = book(time=time)
    patch = (await adapter.fetch_light([item]))[0]
    assert patch.source_at is None and patch.field_timestamps['bid'] is None
    assert patch.received_at == NOW


async def test_observer_delay_and_cancellation_preserve_response_boundary(env, monkeypatch):
    adapter, item, _, _, _ = env
    clock = [NOW]
    monkeypatch.setattr('watch_money.adapters.cex.now_ms', lambda: clock[0])
    async def delay(*args):
        clock[0] += 40
    adapter.on_payload = delay
    patch = (await adapter.fetch_light([item]))[0]
    assert patch.received_at == NOW
    entered = asyncio.Event()
    async def block(*args):
        entered.set()
        await asyncio.Event().wait()
    adapter.on_payload = block
    task = asyncio.create_task(adapter.fetch_light([item]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    adapter.on_payload = None
    assert (await adapter.fetch_light([item]))[0].received_at == NOW + 40


@pytest.mark.parametrize('changes', [{'bids': None}, {'asks': [['bad', '2']]}, {'auction_mode': 'false'}])
async def test_bad_shapes_rejected_without_fabricated_prices(env, changes):
    _, item, _, _, _ = env
    with pytest.raises(ValueError):
        _coinbase_book_patch(item, book(**changes), NOW)


@pytest.mark.parametrize('offset', [-61000, 10])
async def test_stale_and_future_native_book_cannot_rank(env, offset):
    from watch_money.scanner import funding_field_state
    _, item, _, _, _ = env
    stamp = datetime.fromtimestamp((NOW + offset) / 1000, timezone.utc).isoformat()
    patch = _coinbase_book_patch(item, book(time=stamp), NOW)
    assert not funding_field_state(patch, item, 'bid', NOW + 40, 60)['rankable']


async def test_clear_barrier_old_same_time_and_recovery(env, tmp_path):
    from watch_money.collector import Collector
    from watch_money.storage import Storage
    from test_gate_perp_integration import put
    from watch_money.models import now_ms
    _, item, _, _, _ = env
    collector = Collector(Storage(tmp_path), {})
    base = now_ms() - 1000
    def patch(at, **changes):
        stamp = datetime.fromtimestamp(at / 1000, timezone.utc).isoformat()
        return _coinbase_book_patch(item, book(time=stamp, **changes), at + 1)
    assert put(collector, patch(base)).bid == 100
    assert put(collector, patch(base + 10, bids=[])).bid is None
    for at in [base, base + 10]:
        assert put(collector, patch(at)).bid is None
    assert put(collector, patch(base + 20)).bid == 100
