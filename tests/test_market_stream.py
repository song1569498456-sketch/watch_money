from copy import deepcopy
import json

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
