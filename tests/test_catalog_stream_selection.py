"""Collector-wide catalog selection, independent of transport fixtures."""
import asyncio
from types import SimpleNamespace
import pytest
from watch_money.collector import Collector
from watch_money.models import Instrument, MarketSnapshot, Settings, now_ms
from watch_money.storage import Storage


@pytest.mark.parametrize('catalog,broad,cap,expected,truncated', [
    (True, True, 2, ['a','b'], True), (True, True, 500, ['a','b','c'], False),
    (True, False, 500, ['a'], False), (False, True, 500, ['a'], False)])
async def test_reviewed_stream_selects_small_assets_and_reports_budget(tmp_path, catalog, broad, cap, expected, truncated):
    store = Storage(tmp_path)
    store.set('settings', Settings(enabled_venues=['alpha'], watch_assets=['BTC'], broad_collection_enabled=broad).model_dump())
    items = [Instrument(id=identity, venue='alpha', native_symbol=base, base=base, quote='USD',
                        asset_id=base, kind='perpetual') for identity, base in [('a','BTC'),('b','SMALL'),('c','OTHER')]]
    store.save_instruments(items)
    collector = Collector(store, {'alpha': SimpleNamespace()})
    collector.discovered.add('alpha')
    seen = []
    class Stream:
        catalog_stream = catalog
        max_catalog_markets = cap
        async def stream(self, instruments):
            seen.extend(item.id for item in instruments)
            collector.stopping.set()
            yield MarketSnapshot(instrument_id=instruments[0].id, venue='alpha', bid=1, ask=2,
                                 source='synthetic', source_at=now_ms())
        async def close(self):
            pass
    await asyncio.wait_for(collector.run_stream('alpha', Stream()), 2)
    assert seen == expected
    status = collector.statuses['alpha']
    assert status.stream_selected_markets == len(expected)
    assert status.stream_requested_markets == (3 if catalog and broad else 1)
    assert status.stream_truncated == truncated
