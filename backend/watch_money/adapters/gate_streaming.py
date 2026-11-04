"""Gate public spot BBO; result.t is book update time, never envelope/receipt time."""
from __future__ import annotations

import asyncio
import json
import math
from collections import OrderedDict
from contextlib import suppress

from watch_money.async_work import finish_owned
from watch_money.models import MarketSnapshot, now_ms
from .streaming import StreamUnavailable

ENDPOINT = 'wss://api.gateio.ws/ws/v4/'
CHANNEL = 'spot.book_ticker'


def _connection_factory(endpoint):
    # Shared public transport preserves inherited proxy/CA and control ping/pong.
    from .dex_streaming import _connection_factory as connect
    return connect(endpoint)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def gate_spot_patch(instrument, message, received_at):
    """Only complete BBO states with native book time; zero side means unavailable."""
    if instrument is None or instrument.venue != 'gate' or instrument.kind != 'spot':
        return None
    if message.get('channel') != CHANNEL or message.get('event') != 'update':
        return None
    row = message.get('result')
    if not isinstance(row, dict):
        return None
    stamp = row.get('t')
    if type(stamp) is not int or not 100_000_000_000 <= stamp <= received_at:
        return None
    sequence = row.get('u')
    if type(sequence) is not int or sequence < 0:
        return None
    values, sources, clears = {}, {}, []
    source = f'gate:spot.book_ticker:{row.get("s")}'
    for field, price_key, amount_key in (('bid', 'b', 'B'), ('ask', 'a', 'A')):
        price, amount = _number(row.get(price_key)), _number(row.get(amount_key))
        if price is None or amount is None:
            return None  # malformed/omitted is not an explicitly empty book side
        available = price > 0 and amount > 0
        values[field] = price if available else None
        sources[field] = f'{source}.result.{price_key}:book_update_time' + ('' if available else ':explicit_unavailable')
        if not available:
            clears.append(field)
    crossed = values['bid'] is not None and values['ask'] is not None and values['bid'] > values['ask']
    return MarketSnapshot(instrument_id=instrument.id, venue='gate', source=source,
                          source_at=stamp, received_at=received_at,
                          field_timestamps={field: stamp for field in values},
                          field_received_at={field: received_at for field in values},
                          field_sources=sources, cleared_fields=clears, valid=not crossed,
                          issues=['crossed Gate BBO'] if crossed else [], is_demo=False, **values)


def _combine(old, new):
    """Keep equal-time clears even if buffering hides the intermediate frame."""
    if old.source_at > new.source_at:
        return old
    if old.source_at != new.source_at:
        return new
    clears = set(old.cleared_fields) | set(new.cleared_fields)
    if not clears:
        return new
    sources, receipts = dict(new.field_sources), dict(new.field_received_at)
    for field in old.cleared_fields:
        sources[field], receipts[field] = old.field_sources[field], old.field_received_at[field]
    return new.model_copy(update={**{field: None for field in clears}, 'cleared_fields': sorted(clears),
                                  'field_sources': sources, 'field_received_at': receipts})


class _Inbox:
    """One latest BBO per selected market; producer never waits on consumer."""
    def __init__(self, capacity):
        self.capacity = capacity
        self.pending = OrderedDict()
        self.changed = asyncio.Event()
        self.error = None

    def put(self, patch):
        key = patch.instrument_id
        if key in self.pending:
            patch = _combine(self.pending[key], patch)
        elif len(self.pending) >= self.capacity:
            raise StreamUnavailable('Gate public BBO buffer capacity exceeded')
        self.pending[key] = patch
        self.changed.set()

    def fail(self, error):
        self.error = error
        self.changed.set()

    async def get(self):
        while not self.pending:
            if self.error is not None:
                raise self.error
            self.changed.clear()
            await self.changed.wait()
        if self.error is not None:
            raise self.error
        return self.pending.popitem(last=False)[1]


class GateSpotStreamAdapter:
    """Single public connection, bounded application budget; collector owns retry."""
    catalog_stream = True
    max_catalog_markets = 5000
    venue = 'gate'

    def __init__(self, rest_adapter, *, connection_factory=None, subscription_batch=50,
                 subscription_delay=.25, heartbeat_seconds=20, heartbeat_timeout=10):
        if not 1 <= subscription_batch <= 100 or subscription_delay < 0:
            raise ValueError('Invalid Gate subscription budget')
        self.rest_adapter = rest_adapter
        self._factory = connection_factory or _connection_factory
        self._batch = subscription_batch
        self._delay = subscription_delay
        self._heartbeat_seconds = heartbeat_seconds
        self._heartbeat_timeout = heartbeat_timeout
        self._running = False
        self._tasks = []
        self._close_task = None
        self._socket = None
        self._inbox = None
        self._observer_queue = None
        self.payload_observer_dropped = 0
        self.on_payload = None

    def supports_instrument(self, item):
        if item.venue != 'gate' or item.kind != 'spot' or not item.active:
            return False
        try:
            client, market = self.rest_adapter._resolve(item)
        except (ValueError, KeyError):
            return False
        return client == 'gate' and isinstance(market.get('id'), str) and bool(market['id'])

    async def _observe(self):
        while True:
            message = await self._observer_queue.get()
            try:
                with suppress(Exception):
                    await self.on_payload('gate', 'websocket:gate:spot.book_ticker', message)
            except asyncio.CancelledError:
                self.payload_observer_dropped += 1
                raise

    async def _reader(self, socket, selected, inbox, pong):
        previous = {}
        sequences = {}
        frames = 0
        while True:
            raw = await socket.recv()
            received = now_ms()
            # recv() may drain an already buffered burst without suspending.
            # Duplicate/rejected frames must also yield, not bypass the budget.
            frames += 1
            if frames % 64 == 0:
                await asyncio.sleep(0)
            if not isinstance(raw, (str, bytes)) or len(raw) > 64 * 1024:
                raise ValueError('Gate public frame exceeds BBO size budget')
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError('Gate public frame must be an object')
            if message.get('error'):
                raise StreamUnavailable('Gate rejected public subscription')
            if message.get('channel') == 'spot.pong':
                pong.set()
            elif message.get('channel') == CHANNEL and message.get('event') == 'update':
                row = message.get('result')
                item = selected.get(row.get('s')) if isinstance(row, dict) else None
                patch = gate_spot_patch(item, message, received) if item else None
                if patch is not None:
                    sequence = row['u']
                    if sequence < sequences.get(item.id, -1):
                        continue
                    old = previous.get(item.id)
                    if old is not None:
                        if sequence == sequences.get(item.id) and patch.source_at > old.source_at:
                            continue  # same book ID cannot renew its event clock
                        patch = _combine(old, patch)
                        if patch is old:
                            continue
                        if (patch.source_at, patch.bid, patch.ask, patch.cleared_fields) == (old.source_at, old.bid, old.ask, old.cleared_fields):
                            sequences[item.id] = sequence
                            continue
                    previous[item.id] = patch
                    sequences[item.id] = sequence
                    inbox.put(patch)
                if item is not None and self._observer_queue is not None:
                    try:
                        self._observer_queue.put_nowait(message)
                    except asyncio.QueueFull:
                        self.payload_observer_dropped += 1

    async def _heartbeat(self, socket, pong):
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            pong.clear()
            await asyncio.wait_for(socket.send(json.dumps({'time': now_ms() // 1000, 'channel': 'spot.ping'})), self._heartbeat_timeout)
            await asyncio.wait_for(pong.wait(), self._heartbeat_timeout)

    async def _subscribe(self, socket, symbols):
        for offset in range(0, len(symbols), self._batch):
            if offset:
                await asyncio.sleep(self._delay)
            await asyncio.wait_for(socket.send(json.dumps({'time': now_ms() // 1000, 'channel': CHANNEL,
                                                          'event': 'subscribe', 'payload': symbols[offset:offset + self._batch]})), 10)

    async def _connection(self, selected, inbox):
        children = []
        try:
            async with self._factory(ENDPOINT) as socket:
                self._socket = socket
                pong = asyncio.Event()
                children = [asyncio.create_task(self._reader(socket, selected, inbox, pong)),
                            asyncio.create_task(self._heartbeat(socket, pong)),
                            asyncio.create_task(self._subscribe(socket, list(selected)))]
                await asyncio.gather(*children)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            inbox.fail(exc)
        finally:
            for child in children:
                child.cancel()
            if children:
                await asyncio.gather(*children, return_exceptions=True)
            self._socket = None

    async def stream(self, instruments):
        if self._running or self._close_task is not None and not self._close_task.done():
            raise RuntimeError('Close current Gate stream before replacing subscriptions')
        selected = {}
        for item in instruments:
            if self.supports_instrument(item):
                native = self.rest_adapter._resolve(item)[1]['id']
                if native in selected and selected[native].id != item.id:
                    raise ValueError('Ambiguous Gate native market identity')
                selected[native] = item
        if not selected or len(selected) > self.max_catalog_markets:
            raise StreamUnavailable('Gate spot stream requires 1..5000 explicitly selected spot markets')
        self._running = True
        self._close_task = None
        inbox = _Inbox(len(selected))
        self._inbox = inbox
        try:
            if self.on_payload is not None:
                self._observer_queue = asyncio.Queue(maxsize=16)
                self._tasks.append(asyncio.create_task(self._observe()))
            self._tasks.append(asyncio.create_task(self._connection(selected, inbox)))
            while True:
                yield await inbox.get()
        finally:
            try:
                await self.close()
            finally:
                self._running = False

    async def _close_owned(self):
        inbox, self._inbox = self._inbox, None
        if inbox is not None:
            inbox.fail(StreamUnavailable('Gate public stream closed'))
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        queue, self._observer_queue = self._observer_queue, None
        if queue is not None:
            self.payload_observer_dropped += queue.qsize()

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned())
        await finish_owned(self._close_task)
