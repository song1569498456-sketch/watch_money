from copy import deepcopy
import json

import pytest

from watch_money.market_stream import market_delta


def data(count=1):
    return {'mode': 'live', 'instruments': [{'id': str(n), 'base': f'ALT{n}'} for n in range(count)],
            'snapshots': [{'instrument_id': str(n), 'bid': 1, 'age_seconds': 1,
                           'field_ages_seconds': {'bid': 1}, 'stale': False, 'field_stale': {'bid': False}}
                          for n in range(count)]}


def test_initial_reconnect_mode_change_send_complete_state():
    current = data()
    assert market_delta(None, current) == {'type': 'markets', **current}
    demo = {**current, 'mode': 'demo'}
    assert market_delta(current, demo) == {'type': 'markets', **demo}


def test_unchanged_catalog_sends_no_repeated_rows_but_updates_staleness():
    old = data(10000)
    current = deepcopy(old)
    for snapshot in current['snapshots']:
        snapshot['age_seconds'] = 2
        snapshot['field_ages_seconds']['bid'] = 2
    delta = market_delta(old, current)
    assert not delta['instruments'] and not delta['snapshots']
    assert len(json.dumps(delta)) < 200
    current['snapshots'][500]['field_stale']['bid'] = True
    assert market_delta(old, current)['snapshots'] == [current['snapshots'][500]]


def test_upserts_deletions_and_field_updates_reconstruct_full_state():
    old = data(3)
    current = deepcopy(old)
    current['instruments'][0]['base'] = 'renamed'
    current['snapshots'][0]['bid'] = 2
    current['instruments'].pop()
    current['snapshots'].pop()
    current['instruments'].append({'id': 'new', 'base': 'NEW'})
    delta = market_delta(old, current)
    assert delta['removed_instruments'] == delta['removed_snapshots'] == ['2']
    rebuilt = deepcopy(old)
    for key, identity in [('instruments', 'id'), ('snapshots', 'instrument_id')]:
        rows = {row[identity]: row for row in rebuilt[key]}
        for removed in delta['removed_' + key]:
            rows.pop(removed)
        rows.update({row[identity]: row for row in delta[key]})
        assert list(rows.values()) == current[key]


def test_legacy_derivative_volume_is_not_relabelled_or_revived_after_restart(tmp_path):
    from watch_money.collector import Collector
    from watch_money.models import Instrument, MarketSnapshot
    from watch_money.storage import Storage

    store = Storage(tmp_path)
    item = Instrument(id='old', venue='okx', asset_id='BTC', base='BTC', quote='USDT',
                      kind='perpetual', native_symbol='BTC/USDT:USDT', contract_size=.01)
    old = MarketSnapshot(instrument_id='old', venue='okx', last=100, volume=10000, volume_unit='BTC',
                         source='ccxt:okx:fetch_ticker', field_sources={'volume': 'ccxt:okx:fetch_ticker.baseVolume'})
    store.save_instruments([item])
    store.save_snapshots([old])
    collector = Collector(store, {})
    assert collector.snapshots['old'].volume is None
    assert 'legacy_volume_unit_unverified' in collector.snapshots['old'].issues
    updated = collector.merge_patch(MarketSnapshot(instrument_id='old', venue='okx', last=101,
                                                   source='ccxt:okx:fetch_ticker'))
    assert updated.volume is None  # Missing fresh volume cannot revive an incorrectly labelled cached amount.


def versioned(seq=1, instance='server-a'):
    return {**data(), 'server_instance': instance, 'view_seq': seq,
            'view_context': {'enabled_venues': ['alpha'], 'demo_mode': False,
                             'broad_collection_enabled': True, 'watch_assets': ['BTC'], 'stale_seconds': 60}}


def test_versioned_delta_names_actual_ws_base_despite_intervening_http_builds():
    old, current = versioned(2), versioned(5)
    current['snapshots'][0].update(bid=2, source_at=123, received_at=456)
    delta = market_delta(old, current)
    assert delta['type'] == 'markets_delta'
    assert delta['base_view_seq'] == 2 and delta['view_seq'] == 5
    assert delta['server_instance'] == 'server-a' and delta['view_context'] == current['view_context']
    assert delta['snapshots'][0]['source_at'] == 123 and delta['snapshots'][0]['received_at'] == 456


@pytest.mark.parametrize('change', [
    {'server_instance': 'server-b'}, {'view_seq': 2}, {'view_seq': 1},
    {'view_seq': True}, {'view_seq': None}, {'view_context': None},
])
def test_incompatible_or_non_increasing_version_requires_full_frame(change):
    old, current = versioned(2), versioned(3)
    current.update(change)
    frame = market_delta(old, current)
    assert frame == {'type': 'markets', **current, 'base_view_seq': None}


@pytest.mark.parametrize(('field', 'value'), [
    ('enabled_venues', ['beta']), ('demo_mode', True), ('broad_collection_enabled', False),
    ('watch_assets', ['ETH']), ('stale_seconds', 30),
])
def test_each_settings_context_change_requires_full_frame(field, value):
    old, current = versioned(1), versioned(2)
    current['view_context'][field] = value
    frame = market_delta(old, current)
    assert frame['type'] == 'markets' and frame['base_view_seq'] is None
    assert frame['view_context'][field] == value


def test_legacy_to_versioned_and_versioned_to_legacy_require_full_frames():
    assert market_delta(data(), versioned()) == {'type': 'markets', **versioned(), 'base_view_seq': None}
    assert market_delta(versioned(), data()) == {'type': 'markets', **data()}
