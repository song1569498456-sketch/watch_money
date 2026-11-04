"""Complete quote replacement and sparse publication updates are not equivalent."""
from types import SimpleNamespace
import pytest
from watch_money.adapters.dex_streaming import hyperliquid_patch, lighter_patches
from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot
from watch_money.scanner import scan_markets
from watch_money.storage import Storage

AT = 1_800_000_000_000
QUOTE = 'base:0x' + '22' * 20

@pytest.fixture
def env(tmp_path, monkeypatch):
    for module in ('watch_money.collector', 'watch_money.adapters.dex_streaming'):
        monkeypatch.setattr(module + '.now_ms', lambda: AT + 10000)
    items = [Instrument(id=venue, venue=venue, native_symbol='SMALL', asset_id='SMALL',
                        base='SMALL', quote='USDC', kind='perpetual', chain='base', mapping_verified=True,
                        quote_asset_id=QUOTE, native_quote_asset_id=QUOTE,
                        quote_identity_source='https://synthetic.invalid/quote', quote_identity_at=AT,
                        quote_identity_expires_at=AT + 3600000) for venue in ['hyperliquid', 'lighter']]
    store = Storage(tmp_path)
    store.save_instruments(items)
    collector = Collector(store, {})
    collector.snapshots['lighter'] = MarketSnapshot(instrument_id='lighter', venue='lighter',
        bid=105, ask=106, source='synthetic', source_at=AT, received_at=AT)
    return SimpleNamespace(collector=collector, items=items, store=store)


def book(at, bid=100, ask=101):
    return {'channel':'l2Book','data':{'coin':'SMALL','time':at,'levels':[
        [] if bid is None else [{'px':str(bid),'sz':'1'}],
        [] if ask is None else [{'px':str(ask),'sz':'1'}]]}}


def apply(env, message):
    patch = hyperliquid_patch(env.items[0], message)
    assert patch is not None
    result = env.collector.merge_patch(patch)
    env.collector.snapshots['hyperliquid'] = result
    return result


def scan(env, at=AT + 10000):
    return scan_markets(env.items, env.collector.snapshots, at, 60)['candidates']


@pytest.mark.parametrize('bid,ask', [(None,102),(99,None),(None,None)])
def test_empty_full_snapshot_removes_only_absent_sides_and_blocks_affected_direction(env, bid, ask):
    apply(env, book(AT))
    assert all(row['price_rankable'] for row in scan(env)), [row['missing'] for row in scan(env)]
    result = apply(env, book(AT + 1000, bid, ask))
    assert (result.bid,result.ask) == (bid,ask)
    for field, value in [('bid',bid),('ask',ask)]:
        assert result.field_timestamps[field] == AT + 1000
        if value is None:
            assert result.field_sources[field].endswith(':explicit_unavailable')
    for row in scan(env):
        uses_missing = (row['buy_instrument_id']=='hyperliquid' and ask is None or
                        row['sell_instrument_id']=='hyperliquid' and bid is None)
        assert row['price_rankable'] == (not uses_missing)
    restored = apply(env, book(AT + 2000,98,103))
    assert (restored.bid,restored.ask) == (98,103)
    assert all(row['price_rankable'] for row in scan(env)), [row['missing'] for row in scan(env)]


def test_tombstone_survives_persistence_old_and_equal_time_quotes_cannot_resurrect_it(env):
    apply(env, book(AT))
    result = apply(env, book(AT + 2000,None,None))
    env.store.save_snapshots([result],archive=False)
    env.collector = Collector(env.store,{})
    for stamp in [AT,AT+2000]:
        result = apply(env,book(stamp))
        assert result.bid is None and result.ask is None
    result = apply(env,book(AT+3000))
    assert result.bid == 100 and result.ask == 101
    # Delayed empty snapshots cannot delete a newer restored book either.
    result = apply(env,book(AT+1000,None,None))
    assert result.bid == 100 and result.ask == 101


def test_expired_full_replacement_cannot_keep_a_newer_receipt_fresh(env):
    apply(env,book(AT-120000))
    result = apply(env,book(AT-100000,None,102))
    assert result.bid is None and result.ask == 102
    assert not any(row['price_rankable'] for row in scan(env))


def test_lighter_omitted_fields_are_sparse_but_explicit_unavailable_quotes_clear(env):
    item = env.items[1]
    def patch(row,at):
        return lighter_patches({'SMALL':item},{'type':'update/market_stats','channel':'market_stats:all',
            'timestamp':at,'market_stats':{'SMALL':row}})[0]
    old = env.collector.snapshots['lighter']
    result = env.collector.merge_patch(patch({'last_trade_price':'110'},AT+1000))
    assert result.bid == old.bid and result.ask == old.ask
    assert result.field_timestamps.get('bid',result.source_at) == AT
    env.collector.snapshots['lighter'] = result
    cleared = env.collector.merge_patch(patch({'best_bid_price':None,'best_ask_price':'0'},AT+2000))
    assert cleared.bid is None and cleared.ask is None
    env.collector.snapshots['lighter'] = cleared
    restored = env.collector.merge_patch(patch({'best_bid_price':'105','best_ask_price':'106'},AT+3000))
    assert restored.bid == 105 and restored.ask == 106


def test_untimed_empty_snapshot_does_not_allow_older_source_quote_to_resurrect(env):
    apply(env, book(AT))
    result = apply(env, book(None,None,None))
    assert result.bid is None and result.ask is None
    result = apply(env, book(AT+9000))  # before the unavailable observation's receipt
    assert result.bid is None and result.ask is None
    result = apply(env, book(AT+10001))
    assert result.bid == 100 and result.ask == 101


def test_plain_null_without_explicit_clear_remains_sparse_compatible(env):
    apply(env,book(AT))
    result = env.collector.merge_patch(MarketSnapshot(instrument_id='hyperliquid',venue='hyperliquid',
        source='synthetic:sparse',source_at=AT+1000,received_at=AT+1000,bid=None,last=100))
    assert result.bid == 100 and result.ask == 101
    assert result.field_timestamps['bid'] == AT
