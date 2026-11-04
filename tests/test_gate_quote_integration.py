"""Gate official-schema fixtures only; not live coverage acceptance."""
import asyncio
from types import SimpleNamespace

import pytest

from watch_money.adapters.cex import CexAdapter, _light_ticker_patch
from watch_money.adapters.streaming import create_stream
from watch_money.collector import Collector, enrich
from watch_money.models import Instrument, MarketSnapshot, Settings, now_ms
from watch_money.storage import Storage


def item(kind='spot'):
    return Instrument(id='gate:'+kind, venue='gate', native_symbol='SMALL/USDT',
                      base='SMALL', quote='USDT', asset_id='cex:gate:gate:SMALL', kind=kind)


def rest(at):
    return _light_ticker_patch(item(), 'gate', {'bid': 200, 'ask': 201, 'last': 200.5,
                              'timestamp': None, 'info': {'etf_pre_timestamp': at // 1000}},
                              'fetch_tickers', received_at=at)


def book(at, *, clear=False):
    return MarketSnapshot(instrument_id=item().id, venue='gate', source='gate:spot.book_ticker:SMALL_USDT',
                          bid=None if clear else 100, ask=101, source_at=at, received_at=at,
                          cleared_fields=['bid'] if clear else [],
                          field_timestamps={'bid': at, 'ask': at}, field_received_at={'bid': at, 'ask': at},
                          field_sources={'bid': 'gate:spot.book_ticker:SMALL_USDT.b' + (':explicit_unavailable' if clear else ''),
                                         'ask': 'gate:spot.book_ticker:SMALL_USDT.a'})


def put(collector, patch):
    value = collector.merge_patch(patch)
    collector.snapshots[patch.instrument_id] = value
    return value


def test_rest_no_timestamp_stays_visible_but_unrankable(tmp_path):
    at = now_ms()
    patch = rest(at)
    assert patch.source_at is None
    assert patch.field_timestamps['bid'] is None
    assert patch.bid == 200
    assert not enrich(patch, 60)['price_fresh']
    assert any('timed spot.book_ticker required' in issue for issue in patch.issues)


@pytest.mark.parametrize('clear', [False, True])
def test_untimed_rest_cannot_overwrite_book_or_clear_and_does_not_renew_age(tmp_path, monkeypatch, clear):
    at = now_ms() - 1000
    collector = Collector(Storage(tmp_path), {})
    put(collector, book(at, clear=clear))
    merged = put(collector, rest(at + 500))
    assert merged.bid == (None if clear else 100)
    assert merged.ask == 101 and merged.last == 200.5
    assert merged.field_timestamps['bid'] == at
    assert merged.field_received_at['bid'] == at
    if not clear:
        assert enrich(merged, 60)['price_fresh']
    monkeypatch.setattr('watch_money.collector.now_ms', lambda: at + 61_000)
    expired = put(collector, rest(at + 61_000))
    assert expired.field_timestamps['bid'] == at
    assert not enrich(expired, 60)['price_fresh']
    recovered = put(collector, book(at + 61_000))
    assert recovered.bid == 100 and enrich(recovered, 60)['price_fresh']


def test_timed_book_replaces_unknown_rest_and_survives_restart(tmp_path):
    at = now_ms() - 1000
    store = Storage(tmp_path)
    collector = Collector(store, {})
    put(collector, rest(at))
    merged = put(collector, book(at + 1))
    assert merged.bid == 100 and enrich(merged, 60)['price_fresh']
    store.save_snapshots([merged])
    restarted = Collector(store, {})
    again = put(restarted, rest(at + 2))
    assert again.bid == 100 and again.field_timestamps['bid'] == at + 1


async def test_gate_full_spot_catalog_selection_reports_perp_exclusion(tmp_path):
    store = Storage(tmp_path)
    store.set('settings', Settings(enabled_venues=['gate'], watch_assets=['BTC'], broad_collection_enabled=True).model_dump())
    spot, perp = item(), item('perpetual')
    store.save_instruments([spot, perp])
    collector = Collector(store, {'gate': SimpleNamespace()})
    collector.discovered.add('gate')
    selected = []
    class Stream:
        catalog_stream = True
        max_catalog_markets = 5000
        def supports_instrument(self, instrument):
            return instrument.kind == 'spot'
        async def stream(self, instruments):
            selected.extend(instruments)
            collector.stopping.set()
            yield book(now_ms())
        async def close(self):
            pass
    await asyncio.wait_for(collector.run_stream('gate', Stream()), 2)
    assert [row.id for row in selected] == [spot.id]
    status = collector.statuses['gate']
    assert status.stream_requested_markets == 2
    assert status.stream_selected_markets == 1 and status.stream_truncated


def test_gate_factory_uses_native_book_time_stream():
    from watch_money.adapters.gate_streaming import GateSpotStreamAdapter
    assert isinstance(create_stream(CexAdapter('gate')), GateSpotStreamAdapter)


def test_gate_book_time_unlocks_only_fresh_verified_price_directions(tmp_path):
    from watch_money.adapters.gate_streaming import gate_spot_patch
    from watch_money.scanner import scan_markets
    at = now_ms() - 1000
    gate = item().model_copy(update={'asset_id': 'confirmed:SMALL', 'mapping_verified': True})
    other = gate.model_copy(update={'id': 'bitget:spot', 'venue': 'bitget'})
    other_quote = MarketSnapshot(instrument_id=other.id, venue=other.venue, source='synthetic:bitget',
                                bid=102, ask=103, source_at=at, received_at=at)
    collector = Collector(Storage(tmp_path), {})
    def scan(quote, when=at):
        return scan_markets([gate, other], [quote, other_quote], when, 60)['stats']['reliable_price_candidates']
    assert scan(put(collector, rest(at))) == 0
    frame = {'channel': 'spot.book_ticker', 'event': 'update', 'time_ms': at + 500,
             'result': {'s': 'SMALL_USDT', 't': at, 'u': 1, 'b': '100', 'B': '2', 'a': '101', 'A': '3'}}
    patch = gate_spot_patch(gate, frame, at)
    assert patch.source_at == at
    assert scan(put(collector, patch)) == 2
    quote = put(collector, rest(at + 100))
    assert scan(quote, at + 100) == 2
    assert scan(quote, at + 61_000) == 0
    unverified = gate.model_copy(update={'mapping_verified': False})
    assert scan_markets([unverified, other], [quote, other_quote], at + 100, 60)['stats']['reliable_price_candidates'] == 0
