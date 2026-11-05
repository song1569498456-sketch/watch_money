"""View ordering is separate from market field source/receipt time."""
from concurrent.futures import ThreadPoolExecutor
import threading

from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot, Settings, now_ms
from watch_money.storage import Storage


def setup(tmp_path):
    store = Storage(tmp_path)
    store.set('settings', Settings(enabled_venues=['okx', 'bybit', 'okx'],
        watch_assets=['eth', 'BTC', 'btc'], stale_seconds=45).model_dump())
    item = Instrument(id='okx:btc', venue='okx', native_symbol='BTC/USDT', kind='spot',
                      base='BTC', quote='USDT', asset_id='BTC', mapping_verified=True)
    at = now_ms() - 1000
    row = MarketSnapshot(instrument_id=item.id, venue='okx', bid=100, ask=101, last=100.5,
        source='synthetic:public', source_at=at, received_at=at + 100,
        field_timestamps={'bid': at, 'ask': at, 'last': at - 1},
        field_received_at={'bid': at + 100, 'ask': at + 100, 'last': at + 50})
    store.save_instruments([item])
    store.save_snapshots([row], archive=False)
    return Collector(store, {}), store, row


def test_context_is_canonical_seq_advances_and_original_clocks_do_not(tmp_path):
    collector, _store, row = setup(tmp_path)
    before = collector.snapshots[row.instrument_id].model_dump()
    first, second = collector.markets(), collector.markets()
    assert first['view_seq'] == 1 and second['view_seq'] == 2
    assert first['server_instance'] == second['server_instance'] == collector.server_instance
    assert first['view_context'] == second['view_context'] == {
        'enabled_venues': ['bybit', 'okx'], 'watch_assets': ['BTC', 'ETH'], 'stale_seconds': 45,
        'demo_mode': False, 'broad_collection_enabled': True}
    for view in (first, second):
        result = view['snapshots'][0]
        assert result['source_at'] == row.source_at and result['received_at'] == row.received_at
        assert result['field_timestamps'] == row.field_timestamps
        assert result['field_received_at'] == row.field_received_at
    assert collector.snapshots[row.instrument_id].model_dump() == before


def test_view_seq_is_reserved_before_a_slow_settings_read_and_out_of_order_completion(tmp_path, monkeypatch):
    collector, store, _row = setup(tmp_path)
    original = store.settings
    entered, release = threading.Event(), threading.Event()

    def settings():
        if threading.current_thread().name.startswith('old-build'):
            entered.set()
            assert release.wait(2)
        return original()

    monkeypatch.setattr(store, 'settings', settings)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='old-build') as pool:
        future = pool.submit(collector.markets)
        try:
            assert entered.wait(1)
            newer = collector.markets()
            assert newer['view_seq'] == 2
        finally:
            release.set()
        older = future.result(timeout=1)
    assert older['view_seq'] == 1 and older['server_instance'] == newer['server_instance']


def test_parallel_view_builds_have_unique_order_and_restart_has_new_instance(tmp_path):
    collector, store, _row = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        views = list(pool.map(lambda _: collector.markets(), range(32)))
    assert sorted(view['view_seq'] for view in views) == list(range(1, 33))
    assert {view['server_instance'] for view in views} == {collector.server_instance}
    restarted = Collector(store, {})
    view = restarted.markets()
    assert view['view_seq'] == 1 and view['server_instance'] != collector.server_instance


def test_context_and_mode_use_one_settings_read_including_demo(tmp_path, monkeypatch):
    collector, store, _row = setup(tmp_path)
    calls = []
    live = store.settings()
    demo = live.model_copy(update={'demo_mode': True, 'enabled_venues': ['binance'],
                                   'watch_assets': ['sol'], 'stale_seconds': 120})

    def settings():
        calls.append(True)
        return demo if len(calls) == 1 else live

    monkeypatch.setattr(store, 'settings', settings)
    view = collector.markets()
    assert len(calls) == 1 and view['mode'] == 'demo' and view['view_context']['demo_mode']
    assert view['view_context']['enabled_venues'] == ['binance']
    assert view['view_context']['watch_assets'] == ['SOL']
    assert view['view_context']['stale_seconds'] == 120
