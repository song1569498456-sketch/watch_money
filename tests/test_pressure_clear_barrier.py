"""Pressure compaction must agree with the collector's same-event clear barrier."""
from copy import deepcopy

import pytest
from test_dex_streaming import EVENT, instrument
from watch_money.adapters.dex_streaming import _HyperliquidInbox, hyperliquid_patch
from watch_money.collector import Collector
from watch_money.storage import Storage


def frame(channel, *, bid=100, ask=101, at=EVENT):
    sides = [None if price is None else {'px': str(price), 'sz': '1'} for price in (bid, ask)]
    key = 'bbo' if channel == 'bbo' else 'levels'
    levels = sides if channel == 'bbo' else [[side] if side else [] for side in sides]
    return {'channel': channel, 'data': {'coin': 'BTC', 'time': at, key: levels}}


def apply(collector, rows):
    for message, receipt in rows:
        patch = hyperliquid_patch(instrument(), message, received_at=receipt)
        collector.snapshots[patch.instrument_id] = collector.merge_patch(patch)
    return collector.snapshots[instrument().id]


@pytest.mark.parametrize('channel', ['l2Book', 'bbo'])
@pytest.mark.parametrize('empty', ['both', 'bid', 'ask'])
def test_equal_time_clear_survives_pressure_per_side_and_recovers(tmp_path, monkeypatch, channel, empty):
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: EVENT + 1000)
    monkeypatch.setattr('watch_money.adapters.dex_streaming.now_ms', lambda: EVENT + 1000)
    clear = frame(channel, bid=None if empty in ('both', 'bid') else 100,
                  ask=None if empty in ('both', 'ask') else 101)
    rows = [(deepcopy(clear), EVENT + i) for i in range(15)]
    rows += [(frame(channel, bid=102, ask=103), EVENT + i) for i in (15, 16)]
    original = deepcopy(rows)
    inbox = _HyperliquidInbox(16)
    for message, receipt in rows:
        inbox.put(message, receipt)
        assert len(inbox.pending) <= 16
    assert inbox.coalesced > 0
    direct = apply(Collector(Storage(tmp_path/'direct'), {}), rows)
    collector = Collector(Storage(tmp_path/'compact'), {})
    compact = apply(collector, inbox.pending)
    assert (compact.bid, compact.ask) == (direct.bid, direct.ask)
    assert compact.field_received_at == direct.field_received_at
    assert compact.field_timestamps == direct.field_timestamps
    assert compact.field_sources == direct.field_sources
    assert rows == original  # Coalescing must not mutate recorder/raw fixtures.
    # Earlier positive snapshots cannot restore, while a later event can.
    older = apply(collector, [(frame(channel, at=EVENT-1), EVENT+20)])
    assert (older.bid, older.ask) == (compact.bid, compact.ask)
    restored = apply(collector, [(frame(channel, bid=104, ask=105, at=EVENT+1), EVENT+21)])
    assert (restored.bid, restored.ask) == (104, 105)


@pytest.mark.parametrize('channel', ['l2Book', 'bbo'])
def test_pressure_combines_opposite_side_clears_without_reviving_either(tmp_path, monkeypatch, channel):
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: EVENT + 1000)
    inbox = _HyperliquidInbox(16)
    rows = [(frame(channel, bid=None), EVENT+i) for i in range(15)]
    rows += [(frame(channel, ask=None), EVENT+15), (frame(channel), EVENT+16)]
    for row in rows:
        inbox.put(*row)
    direct = apply(Collector(Storage(tmp_path/'direct'), {}), rows)
    compact = apply(Collector(Storage(tmp_path/'compact'), {}), inbox.pending)
    assert compact.bid is None and compact.ask is None
    assert compact.field_received_at == direct.field_received_at


@pytest.mark.parametrize('channel', ['l2Book', 'bbo'])
def test_newer_event_restores_during_pressure_not_just_after_drain(tmp_path, monkeypatch, channel):
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: EVENT + 1000)
    inbox = _HyperliquidInbox(16)
    for index in range(15):
        inbox.put(frame(channel, bid=None, ask=None), EVENT+index)
    inbox.put(frame(channel, at=EVENT+1), EVENT+16)
    inbox.put(frame(channel, bid=102, ask=103, at=EVENT+2), EVENT+17)
    result = apply(Collector(Storage(tmp_path), {}), inbox.pending)
    assert (result.bid, result.ask) == (102, 103)
    assert result.field_timestamps == {'bid': EVENT+2, 'ask': EVENT+2}
    assert len(inbox.pending) <= 16


@pytest.mark.parametrize('channel', ['l2Book', 'bbo'])
def test_repeated_compaction_matches_sequential_side_state(tmp_path, monkeypatch, channel):
    import random
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: EVENT + 10000)
    rng = random.Random(27)
    inbox = _HyperliquidInbox(16)
    rows = []
    for index in range(300):
        row = (frame(channel, bid=None if rng.randrange(4) == 0 else 100,
                     ask=None if rng.randrange(4) == 0 else 101,
                     at=EVENT + index // 35), EVENT + index + 100)
        rows.append(row)
        inbox.put(*row)
        assert len(inbox.pending) <= 16
    direct = apply(Collector(Storage(tmp_path/'direct'), {}), rows)
    compact = apply(Collector(Storage(tmp_path/'compact'), {}), inbox.pending)
    assert (compact.bid, compact.ask) == (direct.bid, direct.ask)
    assert compact.field_received_at == direct.field_received_at
    assert compact.field_timestamps == direct.field_timestamps
    assert compact.field_sources == direct.field_sources
    assert inbox.coalesced > 250
