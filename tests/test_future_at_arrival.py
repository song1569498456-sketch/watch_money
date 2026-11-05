"""Waiting cannot legitimize a source timestamp ahead of its original receipt."""
from types import SimpleNamespace

import pytest

from watch_money.collector import Collector, field_stale
from watch_money.models import Instrument, MarketSnapshot
from watch_money.scanner import funding_field_state
from watch_money.storage import Storage
from watch_money.strategies import _field_fresh, _fresh

AT = 1_800_000_000_000


@pytest.mark.parametrize('field,value', [('bid', 100), ('funding_rate', .001),
                                        ('funding_interval_hours', 8), ('funding_kind', 'current')])
def test_future_at_receipt_stays_unrankable_after_clock_catches_up(tmp_path, monkeypatch, field, value):
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: AT + 1000)
    item = Instrument(id='x', venue='hyperliquid', native_symbol='BTC', base='BTC', quote='USDC',
                      asset_id='BTC', kind='perpetual')
    snapshot = MarketSnapshot(instrument_id='x', venue='hyperliquid', source='fixture:source',
                              source_at=AT + 100, received_at=AT, **{field: value})
    request = SimpleNamespace(as_of=AT + 1000, stale_seconds=60)
    state = funding_field_state(snapshot, item, field, request.as_of, 60)
    assert not state['rankable'] and state['issue'] == f'{field}_timestamp_in_future'
    assert not _field_fresh(snapshot, field, request)
    assert field_stale(snapshot, field, 60)
    collector = Collector(Storage(tmp_path), {})
    collector.snapshots['x'] = snapshot
    # A later normally timed observation restores the field without borrowing envelope time.
    normal = snapshot.model_copy(update={'source_at': AT + 50, 'received_at': AT + 60})
    restored = collector.merge_patch(normal)
    assert funding_field_state(restored, item, field, request.as_of, 60)['rankable']
    assert _field_fresh(restored, field, request)


def test_book_freshness_also_rejects_future_at_receipt():
    request = SimpleNamespace(as_of=AT + 1000, stale_seconds=60)
    assert not _fresh(AT + 100, AT, request)
    assert _fresh(AT, AT + 1, request)
