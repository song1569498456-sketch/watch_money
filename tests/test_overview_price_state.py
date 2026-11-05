"""Overview freshness uses primary price field clocks, not envelope age."""
import pytest
from watch_money.collector import enrich
from watch_money.models import MarketSnapshot

AT = 1_800_000_000_000


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: AT + 1000)


def snap(**values):
    return MarketSnapshot(instrument_id='x', venue='okx', source='fixture',
                          source_at=AT, received_at=AT, **values)


def test_future_at_arrival_never_looks_fresh_after_wall_clock_catches_up():
    original = snap(bid=100, ask=101, field_timestamps={'bid': AT+100, 'ask': AT+100})
    result = enrich(original, 60)
    assert result['field_stale'] == {'bid': True, 'ask': True}
    assert result['stale'] and not result['price_valid'] and not result['price_fresh']
    assert result['price_fields'] == ['bid', 'ask']
    assert original.valid  # Do not invalidate unrelated funding fields/persisted data.


def test_bad_funding_clock_does_not_poison_fresh_prices():
    result = enrich(snap(bid=100, ask=101, funding_rate=.001,
                         field_timestamps={'bid': AT, 'ask': AT, 'funding_rate': AT+100}), 60)
    assert result['field_stale']['funding_rate']
    assert result['price_valid'] and result['price_fresh'] and not result['stale']


def test_fresh_funding_or_last_does_not_mask_old_displayed_book():
    result = enrich(snap(bid=100, ask=101, last=100, funding_rate=.001,
                         field_timestamps={'bid': AT-61000, 'ask': AT-61000}), 60)
    assert result['stale'] and not result['price_fresh']
    assert not result['field_stale']['last'] and not result['field_stale']['funding_rate']


@pytest.mark.parametrize('values', [{}, {'bid': 100}, {'bid': 101, 'ask': 100},
                                   {'last': 100, 'field_timestamps': {'last': None}}])
def test_missing_crossed_or_untimed_price_cannot_count_as_fresh(values):
    assert not enrich(snap(**values), 60)['price_fresh']


def test_last_only_and_normal_source_recovery_are_fresh():
    result = enrich(snap(last=100, field_timestamps={'last': AT-1}), 60)
    assert result['price_fields'] == ['last']
    assert result['price_fresh'] and not result['stale']


def test_partial_metadata_cannot_borrow_unrelated_envelope_source():
    result = enrich(snap(last=100, funding_rate=.001,
                         field_timestamps={'funding_rate': AT}), 60)
    assert result['price_valid'] and not result['price_source_known'] and not result['price_fresh']
