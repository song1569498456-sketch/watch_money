"""Synthetic arrival/observer timing; not evidence of live exchange clock skew."""
import asyncio

import pytest
from watch_money.scanner import funding_field_state
from test_cex_light import EVENT, TickerClient as BaseTickerClient, adapter_for, instrument


class TickerClient(BaseTickerClient):
    async def close(self):
        pass


@pytest.mark.parametrize('path', ['bulk', 'single', 'detailed'])
@pytest.mark.parametrize('offset,rankable', [(10, False), (-10, True)])
async def test_observer_delay_never_moves_original_receipt(monkeypatch, path, offset, rankable):
    clock = [EVENT]
    monkeypatch.setattr('watch_money.adapters.cex.now_ms', lambda: clock[0])
    monkeypatch.setattr('watch_money.adapters.bulk_tickers.now_ms', lambda: clock[0])
    item = instrument(venue='okx')
    client = TickerClient(multiple=path != 'single')
    client.ticker['timestamp'] = EVENT + offset
    adapter, _ = adapter_for([item], clients={'okx': client})
    async def observer(*args):
        clock[0] += 40
        await asyncio.sleep(0)
    adapter.on_payload = observer
    try:
        rows = await adapter.fetch([item]) if path == 'detailed' else await adapter.fetch_light([item])
        patch = rows[0]
        assert patch.received_at == EVENT
        assert patch.field_received_at.get('bid', patch.received_at) == EVENT
        assert funding_field_state(patch, item, 'bid', EVENT + 100, 60)['rankable'] is rankable
    finally:
        await adapter.close()


async def test_detailed_later_funding_call_cannot_retime_earlier_ticker(monkeypatch):
    clock = [EVENT]
    monkeypatch.setattr('watch_money.adapters.cex.now_ms', lambda: clock[0])
    item = instrument(venue='okx', kind='perpetual', settle='USDT')
    client = TickerClient()
    client.ticker['timestamp'] = EVENT + 10
    client.has['fetchOpenInterest'] = False
    async def funding(*args):
        clock[0] += 20
        return {'timestamp': clock[0], 'fundingRate': .0001, 'interval': '8h'}
    client.fetch_funding_rate = funding
    adapter, _ = adapter_for([item], clients={'okx': client})
    async def observer(*args):
        clock[0] += 40
    adapter.on_payload = observer
    try:
        patch = (await adapter.fetch([item]))[0]
        assert patch.field_received_at['bid'] == EVENT
        assert patch.field_received_at['funding_rate'] == EVENT + 60
        assert not funding_field_state(patch, item, 'bid', EVENT + 100, 60)['rankable']
    finally:
        await adapter.close()


@pytest.mark.parametrize('path', ['bulk', 'single', 'detailed'])
async def test_cancel_during_observer_emits_nothing_and_next_call_keeps_new_receipt(monkeypatch, path):
    clock = [EVENT]
    monkeypatch.setattr('watch_money.adapters.cex.now_ms', lambda: clock[0])
    monkeypatch.setattr('watch_money.adapters.bulk_tickers.now_ms', lambda: clock[0])
    item = instrument()
    client = TickerClient(multiple=path != 'single')
    adapter, _ = adapter_for([item], clients={'okx': client})
    entered, exited = asyncio.Event(), asyncio.Event()
    async def observer(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()
    adapter.on_payload = observer
    call = adapter.fetch if path == 'detailed' else adapter.fetch_light
    task = asyncio.create_task(call([item]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
        adapter.on_payload = None
        clock[0] += 100
        patch = (await call([item]))[0]
        assert patch.received_at == EVENT + 100
    finally:
        await adapter.close()
