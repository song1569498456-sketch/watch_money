"""Official-schema synthetic perpetual frames, not live Gate subscription proof."""
from copy import deepcopy

import pytest

from watch_money.adapters.gate_futures import ENDPOINTS, gate_futures_book_patch, gate_update_id
from watch_money.models import Instrument

AT = 1_800_000_000_000


def item(**updates):
    data = dict(id='gate:perp:SMALL_USDT', venue='gate', native_symbol='SMALL/USDT:USDT',
                asset_id='cex:gate:gate:SMALL', base='SMALL', quote='USDT', kind='perpetual',
                settle='USDT', contract_size=.001)
    return Instrument(**(data | updates))


def frame(**updates):
    return {'channel': 'futures.book_ticker', 'event': 'update', 'time_ms': AT + 700,
            'result': {'t': AT, 'u': '2517661076', 's': 'SMALL_USDT',
                       'b': '100', 'B': '37000.5', 'a': '101', 'A': 47061} | updates}


def parse(message, instrument=None, **kwargs):
    return gate_futures_book_patch(instrument or item(), message, AT + 500,
                                   **({'native_contract': 'SMALL_USDT', 'settle': 'usdt'} | kwargs))


def test_exact_native_book_clock_and_no_quantity_or_funding_inference():
    result = parse(frame())
    assert result.source_at == AT and result.received_at == AT + 500
    assert result.bid == 100 and result.ask == 101
    assert result.field_timestamps == {'bid': AT, 'ask': AT}
    assert result.field_received_at == {'bid': AT + 500, 'ask': AT + 500}
    assert 'book_generation_time' in result.field_sources['bid']
    assert not {'funding_rate', 'volume', 'open_interest'} & result.model_fields_set


@pytest.mark.parametrize('updates', [{'t': None}, {'t': AT // 1000}, {'t': AT + 501},
                                    {'t': float(AT)}, {'u': True}, {'u': 1.5},
                                    {'s': 'OTHER_USDT'}, {'b': ''}, {'B': None},
                                    {'a': 'NaN'}, {'A': -1}, {'b': '0', 'B': 1}])
def test_missing_future_or_invalid_native_state_rejected(updates):
    assert parse(frame(**updates)) is None


@pytest.mark.parametrize('updates,clears', [({'b': '', 'B': 0}, ['bid']),
                                           ({'a': '', 'A': '0'}, ['ask']),
                                           ({'b': '', 'B': '0', 'a': '', 'A': 0}, ['bid', 'ask'])])
def test_documented_empty_price_with_zero_size_is_explicit_clear(updates, clears):
    result = parse(frame(**updates))
    assert result.cleared_fields == clears
    for field in clears:
        assert getattr(result, field) is None
        assert result.field_sources[field].endswith(':explicit_unavailable')
        assert result.field_timestamps[field] == AT
        assert result.field_received_at[field] == AT + 500


def test_missing_side_is_not_equivalent_to_empty():
    message = deepcopy(frame())
    del message['result']['a']
    assert parse(message) is None


@pytest.mark.parametrize('instrument,scope', [(item(kind='spot'), 'usdt'), (item(venue='bitget'), 'usdt'),
                                             (item(active=False), 'usdt'), (item(settle='BTC'), 'usdt'),
                                             (item(), 'btc'), (item(settle='USDC'), 'usdc')])
def test_product_and_settlement_namespace_is_exact(instrument, scope):
    assert parse(frame(), instrument, settle=scope) is None


def test_reviewed_settlements_and_update_id_types():
    assert set(ENDPOINTS) == {'btc', 'usdt', 'usd1'}
    assert gate_update_id(2517661076) == gate_update_id('2517661076') == 2517661076
    assert gate_update_id('1e5') is None and gate_update_id('-1') is None
    assert parse(frame(u=2517661076)) is not None


def test_crossed_book_is_not_reliable():
    assert not parse(frame(b='102')).valid
    assert not parse(frame(b='101')).valid


def test_current_funding_does_not_borrow_configuration_settlement_or_receipt_time():
    from watch_money.adapters.cex import CexAdapter
    adapter = CexAdapter('gate')
    instrument = item()
    adapter._markets[instrument.id] = ('gate', {'info': {'funding_interval': 28800},
                                               '_catalog_observed_at': AT})
    response = {'timestamp': None, 'fundingRate': .0003, 'fundingTimestamp': AT + 28_800_000,
                'info': {'funding_next_apply': (AT + 28_800_000) // 1000,
                         'config_change_time': AT // 1000, 'create_time': AT // 1000,
                         'funding_interval': 28800}}
    patch = adapter._funding_patch(instrument, response, 'fetch_funding_rate', AT + 500)
    assert patch.funding_rate == .0003
    assert patch.source_at is None and patch.field_timestamps['funding_rate'] is None
    assert patch.next_funding_at == AT + 28_800_000
    assert patch.field_timestamps['next_funding_at'] is None
    assert any('Funding source event time unavailable' in value for value in patch.issues)
