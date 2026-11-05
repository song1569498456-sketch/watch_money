"""Independent field clocks: catalog observations must not inherit price time."""
import pytest

from watch_money.collector import Collector
from watch_money.models import MarketSnapshot
from watch_money.storage import Storage

AT = 1_800_000_000_000


@pytest.fixture
def collector(tmp_path, monkeypatch):
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: AT + 100_000)
    return Collector(Storage(tmp_path), {})


def patch(**values):
    return MarketSnapshot(instrument_id='bitget:test', venue='bitget', source='fixture',
                          received_at=AT + 3000, source_at=AT + 2000, **values)


def metadata(hours=8, observed=AT):
    return patch(funding_interval_hours=hours,
                 field_timestamps={'funding_interval_hours': None},
                 field_received_at={'funding_interval_hours': observed},
                 field_sources={'funding_interval_hours': 'bitget:catalog_metadata_observed_at'})


def apply(collector, value):
    merged = collector.merge_patch(value)
    collector.snapshots[value.instrument_id] = merged
    return merged


@pytest.mark.parametrize('price_first', [True, False])
def test_cold_start_catalog_and_price_arrival_order(collector, price_first):
    price = patch(last=100)
    rows = [price, metadata()] if price_first else [metadata(), price]
    for row in rows:
        merged = apply(collector, row)
    assert merged.last == 100
    assert merged.funding_interval_hours == 8
    assert merged.field_timestamps['funding_interval_hours'] is None
    assert merged.field_received_at['funding_interval_hours'] == AT
    assert merged.field_sources['funding_interval_hours'] == 'bitget:catalog_metadata_observed_at'
    assert merged.field_timestamps['last'] == AT + 2000


def test_metadata_cannot_overwrite_newer_metadata(collector):
    apply(collector, metadata(4, AT + 1000))
    result = apply(collector, metadata(8, AT))
    assert result.funding_interval_hours == 4
    assert result.field_received_at['funding_interval_hours'] == AT + 1000


def test_metadata_order_does_not_follow_price_envelope(collector):
    apply(collector, metadata(8, AT))
    apply(collector, patch(last=100))
    result = apply(collector, metadata(4, AT + 1000))
    assert result.funding_interval_hours == 4
    assert result.field_received_at['funding_interval_hours'] == AT + 1000
    assert result.last == 100


def test_independent_event_time_new_field_and_existing_field(collector):
    apply(collector, patch(last=100))
    result = apply(collector, patch(last=90, funding_rate=.001,
                                  field_timestamps={'last': AT, 'funding_rate': AT},
                                  field_received_at={'last': AT + 1000, 'funding_rate': AT + 1000},
                                  field_sources={'funding_rate': 'fixture:funding'}))
    assert result.last == 100
    assert result.funding_rate == .001
    assert result.field_timestamps['funding_rate'] == AT
    assert result.field_received_at['funding_rate'] == AT + 1000


def test_persisted_missing_field_can_receive_older_metadata(collector):
    value = apply(collector, patch(last=100))
    collector.store.save_snapshots([value])
    restored = Collector(collector.store, {})
    result = apply(restored, metadata())
    assert result.funding_interval_hours == 8
    assert result.field_received_at['funding_interval_hours'] == AT


def test_missing_side_with_explicit_clear_retains_barrier(collector):
    apply(collector, patch(bid=100, ask=101))
    cleared = patch(bid=None, cleared_fields=['bid'],
                    field_sources={'bid': 'fixture:explicit_unavailable'})
    apply(collector, cleared)
    result = apply(collector, patch(bid=99, field_timestamps={'bid': AT}))
    assert result.bid is None


@pytest.mark.asyncio
async def test_bitget_catalog_interval_survives_price_first_and_is_comparable(collector, monkeypatch):
    from test_cex_light import FundingClient, funding_market
    from watch_money.adapters.cex import CexAdapter
    from watch_money.scanner import funding_field_state

    clock = [AT]
    monkeypatch.setattr('watch_money.adapters.cex.now_ms', lambda: clock[0])
    client = FundingClient([funding_market(raw={'fundingRateInterval': '8'})])
    client.rate = {'timestamp': AT + 2000, 'fundingRate': .0003,
                   'fundingTimestamp': AT + 3_600_000}
    adapter = CexAdapter('bitget', client_factory=lambda _: client)
    item, = await adapter.discover()
    apply(collector, MarketSnapshot(instrument_id=item.id, venue='bitget', source='fixture:price',
                                    last=100, source_at=AT + 1000, received_at=AT + 1000))
    clock[0] = AT + 3000
    incoming, = await adapter.fetch_funding_light([item])
    assert incoming.funding_interval_hours == 8
    merged = apply(collector, incoming)
    for field in ('funding_rate', 'funding_interval_hours'):
        assert funding_field_state(merged, item, field, clock[0], 60)['rankable']
    assert merged.field_timestamps['funding_interval_hours'] is None
    assert merged.field_received_at['funding_interval_hours'] == AT
    assert merged.field_timestamps['funding_rate'] == AT + 2000
