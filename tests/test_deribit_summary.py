"""Official summary schema fixtures; no live traffic or identity approvals."""
import pytest
from watch_money.adapters.cex import _deribit_summary_patch
from watch_money.scanner import funding_field_state
from test_cex_light import EVENT, instrument, adapter_for


def sample(base='PEPE', settle='USDC'):
    item = instrument(base, venue='deribit', kind='perpetual', settle=settle)
    market = {'id': f'{base}_{settle}-PERPETUAL', 'symbol': item.native_symbol,
              'swap': True, 'settle': settle, 'base': base, 'baseId': base,
              'quote': item.quote, 'quoteId': item.quote, 'type': 'swap', 'spot': False,
              'contract': True, 'linear': True, 'inverse': False, 'option': False}
    raw = {'instrument_name': market['id'], 'base_currency': base, 'quote_currency': item.quote,
           'creation_timestamp': EVENT, 'bid_price': 100, 'ask_price': 101, 'volume': 123,
           'funding_8h': .01, 'current_funding': .02}
    return item, market, {'symbol': item.native_symbol, 'timestamp': EVENT, 'info': raw}


@pytest.mark.parametrize('settle', ['BTC', 'ETH', 'USDC'])
def test_plan_bulk_groups_by_settle_instead_of_base(settle):
    rows = [sample(f'COIN{i}', settle) for i in range(150)]
    adapter, _ = adapter_for([row[0] for row in rows], venue='deribit')
    for item, market, _ in rows:
        adapter._markets[item.id] = ('deribit', market)
    batches = adapter.plan_light([row[0] for row in rows])
    assert len(batches) == 1 and batches[0].full_product and len(batches[0].instruments) == 150
    assert all(not batch.full_product for batch in adapter.plan_light([row[0] for row in rows], funding=True))


@pytest.mark.parametrize('bid,ask,clears', [(None, 101, ['bid']), (100, None, ['ask']),
                                         (None, None, ['bid', 'ask']), (100, 101, [])])
def test_native_empty_sides_and_volume_never_invent_funding(bid, ask, clears):
    item, market, ticker = sample()
    ticker['info'].update(bid_price=bid, ask_price=ask)
    patch = _deribit_summary_patch(item, ticker, market, EVENT + 1)
    assert patch.cleared_fields == clears and patch.bid == bid and patch.ask == ask
    assert patch.source_at == EVENT and patch.field_timestamps['bid'] == EVENT
    assert patch.volume == 123 and patch.volume_unit == 'PEPE'
    assert patch.funding_rate is None and patch.funding_interval_hours is None
    assert 'summary_creation_timestamp' in patch.field_sources['bid']


@pytest.mark.parametrize('change', [{'instrument_name': 'OTHER'}, {'base_currency': 'OTHER'},
                                   {'quote_currency': 'OTHER'}, {'bid_price': -1}, {'ask_price': 'NaN'}])
def test_wrong_identity_or_invalid_price_rejected(change):
    item, market, ticker = sample()
    ticker['info'].update(change)
    assert _deribit_summary_patch(item, ticker, market, EVENT + 1) is None


@pytest.mark.parametrize('stamp', [None, EVENT - 61000, EVENT + 1000])
def test_no_receipt_or_unified_time_substitution(stamp):
    item, market, ticker = sample()
    ticker['info']['creation_timestamp'] = stamp
    patch = _deribit_summary_patch(item, ticker, market, EVENT)
    assert patch.source_at == stamp
    assert not funding_field_state(patch, item, 'bid', EVENT, 60)['rankable']


async def test_installed_parser_uses_public_settlement_request_and_filters_extra_markets():
    import ccxt.async_support as ccxt
    rows = [sample('PEPE'), sample('ETH')]
    client = ccxt.deribit()
    client.markets = {item.native_symbol: market for item, market, _ in rows}
    client.markets_by_id = {market['id']: [market] for _, market, _ in rows}
    client.currencies = {'USDC': {'id': 'USDC', 'code': 'USDC'}}
    calls = []
    async def public(params):
        calls.append(params)
        return {'result': [ticker['info'] for _, _, ticker in rows]}
    async def forbidden(*args, **kwargs):
        pytest.fail('unexpected transport')
    client.fetch = forbidden
    client.publicGetGetBookSummaryByCurrency = public
    adapter, _ = adapter_for([row[0] for row in rows], clients={'deribit': client}, venue='deribit')
    for item, market, _ in rows:
        adapter._markets[item.id] = ('deribit', market)
    try:
        plan = adapter.plan_light([rows[0][0]])
        result = await adapter.fetch_light_batch(plan[0])
        assert not result.errors and len(result.snapshots) == 1
        assert result.snapshots[0].instrument_id == rows[0][0].id
        assert calls == [{'currency': 'USDC', 'kind': 'future'}]
        assert result.snapshots[0].source_at == EVENT
    finally:
        await adapter.close()


def test_summary_clear_barrier_old_frames_and_recovery(tmp_path):
    from watch_money.collector import Collector
    from watch_money.storage import Storage
    from test_gate_perp_integration import put
    item, market, ticker = sample()
    collector = Collector(Storage(tmp_path), {})
    from watch_money.models import now_ms
    base_time = now_ms() - 1000
    def patch(at, bid, ask):
        raw = dict(ticker['info'], creation_timestamp=at, bid_price=bid, ask_price=ask)
        return _deribit_summary_patch(item, dict(ticker, info=raw), market, at + 1)
    assert put(collector, patch(base_time, 100, 101)).bid == 100
    empty = put(collector, patch(base_time + 10, None, None))
    assert empty.bid is None and empty.ask is None
    for at in [base_time, base_time + 10]:
        old = put(collector, patch(at, 100, 101))
        assert old.bid is None and old.ask is None
    recovered = put(collector, patch(base_time + 20, 102, 103))
    assert recovered.bid == 102 and recovered.ask == 103


def test_spot_and_unknown_settlement_do_not_claim_full_product():
    items = [instrument(venue='deribit'), sample()[0].model_copy(update={'settle': None})]
    adapter, _ = adapter_for(items, venue='deribit')
    assert all(not batch.full_product for batch in adapter.plan_light(items))
