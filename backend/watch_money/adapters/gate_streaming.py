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
    from .gate_futures import ENDPOINTS
    if endpoint in ENDPOINTS.values():
        return connect(endpoint, additional_headers={'X-Gate-Size-Decimal': '1'})
    return connect(endpoint)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def gate_spot_rejection(instrument, message, received_at):
    """Fixed public diagnostic reasons; never return input content or substitute time."""
    if message.get('channel') != CHANNEL or message.get('event') != 'update':
        return 'malformed'
    row = message.get('result')
    if not isinstance(row, dict) or not isinstance(row.get('s'), str):
        return 'malformed'
    if instrument is None or instrument.venue != 'gate' or instrument.kind != 'spot':
        return 'unknown_symbol'
    stamp = row.get('t')
    if stamp is None:
        return 'missing_time'
    if type(stamp) is not int or stamp < 100_000_000_000:
        return 'invalid_time'
    if stamp > received_at:
        return 'future_time'
    if type(row.get('u')) is not int or row['u'] < 0:
        return 'malformed'
    if any(_number(row.get(key)) is None for key in ('b', 'B', 'a', 'A')):
        return 'malformed'
    return None


def gate_spot_patch(instrument, message, received_at):
    """Only complete BBO states with native book time; zero side means unavailable."""
    if gate_spot_rejection(instrument, message, received_at) is not None:
        return None
    return _build_gate_spot_patch(instrument, message, received_at)


def _build_gate_spot_patch(instrument, message, received_at):
    # Internal hot path: caller has already run gate_spot_rejection.
    row = message['result']
    stamp = row['t']
    values, sources, clears = {}, {}, []
    source = f'gate:spot.book_ticker:{row.get("s")}'
    for field, price_key, amount_key in (('bid', 'b', 'B'), ('ask', 'a', 'A')):
        price, amount = _number(row[price_key]), _number(row[amount_key])
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
        self.coalesced = 0

    def put(self, patch):
        key = patch.instrument_id
        if key in self.pending:
            self.coalesced += 1
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
    channel = CHANNEL
    endpoint = ENDPOINT
    ping_channel = 'spot.ping'
    pong_channel = 'spot.pong'
    stream_key = 'spot'

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
        self._generation = 0
        self._request_id = 0
        self._subscriptions = {}
        self._observed = set()
        self._accepted = set()
        self._diag = {}
        self._reset_diagnostics(0)

    def _reset_diagnostics(self, selected_count):
        self._subscriptions = {}
        self._observed = set()
        self._accepted = set()
        self._observer_start = self.payload_observer_dropped
        self._diag = {key: 0 for key in (
            'sent_batches', 'sent_markets', 'acknowledged_batches', 'acknowledged_markets',
            'uncorrelated_acknowledgements', 'duplicate_acknowledgements', 'rejected_subscriptions',
            'received_frames', 'bbo_frames', 'valid_bbo_frames', 'crossed_bbo_frames', 'accepted_patches',
            'rejected_missing_time', 'rejected_invalid_time', 'rejected_future_time', 'rejected_malformed',
            'rejected_unknown_symbol', 'rejected_stale_order', 'rejected_same_sequence_newer_time',
            'duplicate_bbo_frames')}
        self._diag.update(scope='current_or_last_connection', generation=self._generation,
                          selected_markets=selected_count,
                          planned_batches=(selected_count + self._batch - 1) // self._batch,
                          started_at=now_ms() if selected_count else None, connected_at=None,
                          closed_at=None, last_received_at=None, accepted_source_at_min=None,
                          accepted_source_at_max=None, accepted_receipt_at_min=None,
                          accepted_receipt_at_max=None, state='connecting' if selected_count else 'idle',
                          inbox_coalesced=0)

    def diagnostics(self):
        """Bounded O(1) snapshot; cumulative source extrema are NOT live freshness."""
        result = dict(self._diag)
        result.update(unique_observed_markets=len(self._observed), unique_accepted_markets=len(self._accepted),
                      observer_dropped=self.payload_observer_dropped - self._observer_start,
                      inbox_pending=len(self._inbox.pending) if self._inbox is not None else 0)
        if self._inbox is not None:
            result['inbox_coalesced'] = self._inbox.coalesced
        return result

    def _acknowledge(self, message):
        request_id = message.get('id')
        request = self._subscriptions.get(request_id) if type(request_id) is int else None
        if request is None:
            self._diag['uncorrelated_acknowledgements'] += 1
            return
        # error is optional on successful Gate spot AND futures responses.
        # A missing error never proves success by itself: require the exact
        # echoed request ID and success object, and reject any non-null error.
        if message.get('error') is not None or message.get('result') != {'status': 'success'}:
            self._diag['uncorrelated_acknowledgements'] += 1
            return
        if request['acknowledged']:
            self._diag['duplicate_acknowledgements'] += 1
            return
        request['acknowledged'] = True
        self._diag['acknowledged_batches'] += 1
        self._diag['acknowledged_markets'] += request['market_count']

    def supports_instrument(self, item):
        if item.venue != 'gate' or item.kind != 'spot' or not item.active:
            return False
        try:
            client, market = self.rest_adapter._resolve(item)
        except (ValueError, KeyError):
            return False
        return client == 'gate' and isinstance(market.get('id'), str) and bool(market['id'])

    def _parse(self, instrument, message, received):
        reason = gate_spot_rejection(instrument, message, received)
        return (_build_gate_spot_patch(instrument, message, received) if reason is None else None), reason

    @staticmethod
    def _sequence(value):
        return value

    async def _observe(self):
        while True:
            message = await self._observer_queue.get()
            try:
                with suppress(Exception):
                    await self.on_payload('gate', f'websocket:gate:{self.channel}' +
                                          (':' + self.settle if hasattr(self, 'settle') else ''), message)
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
            self._diag['received_frames'] += 1
            self._diag['last_received_at'] = received
            # recv() may drain an already buffered burst without suspending.
            # Duplicate/rejected frames must also yield, not bypass the budget.
            frames += 1
            if frames % 64 == 0:
                await asyncio.sleep(0)
            if not isinstance(raw, (str, bytes)) or len(raw) > 64 * 1024:
                self._diag['rejected_malformed'] += 1
                raise ValueError('Gate public frame exceeds BBO size budget')
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                self._diag['rejected_malformed'] += 1
                raise ValueError('Malformed Gate public JSON frame') from None
            if not isinstance(message, dict):
                self._diag['rejected_malformed'] += 1
                raise ValueError('Gate public frame must be an object')
            if message.get('error'):
                if message.get('channel') == self.channel and message.get('event') == 'subscribe':
                    self._diag['rejected_subscriptions'] += 1
                raise StreamUnavailable('Gate rejected public subscription')
            if message.get('channel') == self.pong_channel:
                pong.set()
            elif message.get('channel') == self.channel and message.get('event') == 'subscribe':
                self._acknowledge(message)
            elif message.get('channel') == self.channel and message.get('event') == 'update':
                self._diag['bbo_frames'] += 1
                row = message.get('result')
                symbol = row.get('s') if isinstance(row, dict) else None
                item = selected.get(symbol) if isinstance(symbol, str) else None
                if item is not None:
                    self._observed.add(item.id)
                patch, reason = self._parse(item, message, received)
                if reason is not None:
                    self._diag['rejected_' + reason] += 1
                if patch is not None:
                    self._diag['valid_bbo_frames' if patch.valid else 'crossed_bbo_frames'] += 1
                    sequence = self._sequence(row['u'])
                    if sequence < sequences.get(item.id, -1):
                        self._diag['rejected_stale_order'] += 1
                        continue
                    old = previous.get(item.id)
                    if old is not None:
                        if sequence == sequences.get(item.id) and patch.source_at > old.source_at:
                            self._diag['rejected_same_sequence_newer_time'] += 1
                            continue  # same book ID cannot renew its event clock
                        patch = _combine(old, patch)
                        if patch is old:
                            self._diag['rejected_stale_order'] += 1
                            continue
                        if (patch.source_at, patch.bid, patch.ask, patch.cleared_fields) == (old.source_at, old.bid, old.ask, old.cleared_fields):
                            sequences[item.id] = sequence
                            self._diag['duplicate_bbo_frames'] += 1
                            continue
                    previous[item.id] = patch
                    sequences[item.id] = sequence
                    inbox.put(patch)
                    self._accepted.add(item.id)
                    self._diag['accepted_patches'] += 1
                    for name, value in (('source', patch.source_at), ('receipt', patch.received_at)):
                        low, high = f'accepted_{name}_at_min', f'accepted_{name}_at_max'
                        self._diag[low] = value if self._diag[low] is None else min(value, self._diag[low])
                        self._diag[high] = value if self._diag[high] is None else max(value, self._diag[high])
                if item is not None and self._observer_queue is not None:
                    try:
                        self._observer_queue.put_nowait(message)
                    except asyncio.QueueFull:
                        self.payload_observer_dropped += 1

    async def _heartbeat(self, socket, pong):
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            pong.clear()
            await asyncio.wait_for(socket.send(json.dumps({'time': now_ms() // 1000, 'channel': self.ping_channel})), self._heartbeat_timeout)
            await asyncio.wait_for(pong.wait(), self._heartbeat_timeout)

    async def _subscribe(self, socket, symbols):
        for offset in range(0, len(symbols), self._batch):
            if offset:
                await asyncio.sleep(self._delay)
            batch = symbols[offset:offset + self._batch]
            self._request_id += 1
            request_id = self._request_id
            self._subscriptions[request_id] = {'market_count': len(batch), 'acknowledged': False}
            await asyncio.wait_for(socket.send(json.dumps({'time': now_ms() // 1000, 'channel': self.channel,
                                                          'id': request_id, 'event': 'subscribe', 'payload': batch})), 10)
            self._diag['sent_batches'] += 1
            self._diag['sent_markets'] += len(batch)

    async def _connection(self, selected, inbox):
        children = []
        try:
            async with self._factory(self.endpoint) as socket:
                self._socket = socket
                self._diag.update(state='connected', connected_at=now_ms())
                pong = asyncio.Event()
                children = [asyncio.create_task(self._reader(socket, selected, inbox, pong)),
                            asyncio.create_task(self._heartbeat(socket, pong)),
                            asyncio.create_task(self._subscribe(socket, list(selected)))]
                await asyncio.gather(*children)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diag['state'] = 'failed'
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
            raise StreamUnavailable('Gate namespace stream requires 1..5000 explicitly selected supported markets')
        self._generation += 1
        self._reset_diagnostics(len(selected))
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
            self._diag['inbox_coalesced'] = inbox.coalesced
            inbox.fail(StreamUnavailable('Gate public stream closed'))
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        queue, self._observer_queue = self._observer_queue, None
        if queue is not None:
            self.payload_observer_dropped += queue.qsize()
        self._diag['closed_at'] = now_ms()
        if self._diag['state'] != 'failed':
            self._diag['state'] = 'closed'

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned())
        await finish_owned(self._close_task)


class GateFuturesStreamAdapter(GateSpotStreamAdapter):
    """Reviewed connection-scoped perpetual BBO; no funding or size conversion."""
    channel = 'futures.book_ticker'
    ping_channel = 'futures.ping'
    pong_channel = 'futures.pong'

    def __init__(self, rest_adapter, *, settle, **kwargs):
        from .gate_futures import ENDPOINTS
        if settle not in ENDPOINTS:
            raise ValueError('Unreviewed Gate perpetual settlement')
        self.settle = settle
        self.endpoint = ENDPOINTS[settle]
        self.stream_key = 'futures:' + settle
        super().__init__(rest_adapter, **kwargs)

    def supports_instrument(self, item):
        if (item.venue != 'gate' or item.kind != 'perpetual' or not item.active
                or (item.settle or '').lower() != self.settle):
            return False
        try:
            client, market = self.rest_adapter._resolve(item)
        except (ValueError, KeyError):
            return False
        return (client == 'gate' and isinstance(market.get('id'), str) and bool(market['id'])
                and isinstance(market.get('settle'), str) and market['settle'].lower() == self.settle
                and market.get('swap') is True)

    @staticmethod
    def _sequence(value):
        from .gate_futures import gate_update_id
        return gate_update_id(value)

    def _parse(self, instrument, message, received):
        from .gate_futures import gate_futures_book_patch
        row = message.get('result')
        if not isinstance(row, dict) or not isinstance(row.get('s'), str):
            return None, 'malformed'
        if instrument is None:
            return None, 'unknown_symbol'
        stamp = row.get('t')
        if stamp is None:
            return None, 'missing_time'
        if type(stamp) is not int or stamp < 100_000_000_000:
            return None, 'invalid_time'
        if stamp > received:
            return None, 'future_time'
        # _reader already matched this native symbol in its startup catalog;
        # rediscovery must not mutate an in-flight connection's identity binding.
        native = row['s']
        patch = gate_futures_book_patch(instrument, message, received,
                                        native_contract=native, settle=self.settle)
        return patch, None if patch is not None else 'malformed'


class GateStreamAdapter:
    """At most four independent public namespaces, one shared bounded BBO inbox.

    Each failed namespace backs off without cancelling healthy siblings. The
    collector owns catalog replacement and final close. Namespace diagnostics
    describe current/last connections, not a claim that every market is fresh.
    """
    venue = 'gate'
    catalog_stream = True
    max_catalog_markets = 8000
    namespace_limit = 5000

    def __init__(self, rest_adapter, *, connection_factory=None, retry_initial=5,
                 retry_max=300, **child_options):
        if not 0 < retry_initial <= retry_max <= 300:
            raise ValueError('Gate retries require a positive bounded backoff')
        self.rest_adapter = rest_adapter
        self._options = dict(child_options, connection_factory=connection_factory)
        self._retry_initial, self._retry_max = retry_initial, retry_max
        self._children = {'spot': GateSpotStreamAdapter(rest_adapter, **self._options)}
        self._children.update({f'futures:{settle}': GateFuturesStreamAdapter(rest_adapter, settle=settle,
                              **self._options) for settle in ('btc', 'usdt', 'usd1')})
        self._tasks = []
        self._running = False
        self._close_task = None
        self._inbox = None
        self._relay_status = {}
        self._generation = 0
        self._selected = 0
        self._coalesced = 0
        self.on_payload = None

    def _key(self, item):
        key = 'spot' if item.kind == 'spot' else 'futures:' + (item.settle or '').lower()
        child = self._children.get(key)
        return key if child is not None and child.supports_instrument(item) else None

    def supports_instrument(self, item):
        return self._key(item) is not None

    def select_instruments(self, instruments):
        counts, selected, seen = {}, [], set()
        for item in sorted(instruments, key=lambda value: value.id):
            key = self._key(item)
            if key is None or item.id in seen or counts.get(key, 0) >= self.namespace_limit:
                continue
            if len(selected) >= self.max_catalog_markets:
                break
            seen.add(item.id)
            selected.append(item)
            counts[key] = counts.get(key, 0) + 1
        return selected

    @property
    def payload_observer_dropped(self):
        return sum(child.payload_observer_dropped for child in self._children.values())

    def health_state(self):
        states = [status['relay_state'] for status in self._relay_status.values()]
        if not self._running or states and all(state == 'closed' for state in states):
            return 'closed'
        receiving = sum(state == 'receiving' for state in states)
        if receiving == len(states) and states:
            return 'online'
        if receiving:
            return 'partial'
        if states and all(state == 'blocked' for state in states):
            return 'blocked'
        if 'blocked' in states or any(status['reconnects'] for status in self._relay_status.values()):
            return 'reconnecting'
        return 'connecting'

    def diagnostics(self):
        streams = {key: {**self._children[key].diagnostics(), **status}
                   for key, status in self._relay_status.items()}
        result = {'scope': 'current_or_last_catalog_session', 'generation': self._generation,
                  'state': self.health_state(), 'selected_markets': self._selected,
                  'namespace_count': len(streams), 'max_connections': 4, 'streams': streams,
                  'inbox_pending': len(self._inbox.pending) if self._inbox is not None else 0,
                  'inbox_coalesced': self._inbox.coalesced if self._inbox is not None else self._coalesced,
                  'observer_dropped': self.payload_observer_dropped}
        for field in ('sent_batches', 'sent_markets', 'acknowledged_batches', 'acknowledged_markets',
                      'unique_observed_markets', 'unique_accepted_markets', 'received_frames', 'accepted_patches'):
            result[field] = sum(row.get(field, 0) for row in streams.values())
        result['healthy_namespaces'] = sum(row['relay_state'] == 'receiving' for row in streams.values())
        result['backoff_namespaces'] = sum(row['relay_state'] == 'backoff' for row in streams.values())
        result['blocked_namespaces'] = sum(row['relay_state'] == 'blocked' for row in streams.values())
        return result

    async def _relay(self, key, instruments, inbox):
        child = self._children[key]
        status = self._relay_status[key]
        failures = 0
        while True:
            blocked = False
            frames = 0
            status.update(relay_state='connecting', next_retry_at=None)
            iterator = child.stream(instruments).__aiter__()
            try:
                async for patch in iterator:
                    status['relay_state'] = 'receiving'
                    failures = 0
                    inbox.put(patch)
                    frames += 1
                    if frames % 64 == 0:
                        await asyncio.sleep(0)
                raise StreamUnavailable('Gate namespace stream ended')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                status['last_error_type'] = type(exc).__name__
                code = getattr(getattr(exc, 'response', None), 'status_code', None)
                blocked = code in {401, 403, 451}
                if blocked:
                    status.update(relay_state='blocked', next_retry_at=None, retry_delay_seconds=None,
                                  blocked_http_status=code)
                else:
                    status['reconnects'] += 1
                    delay = min(self._retry_max, self._retry_initial * 2 ** min(failures - 1, 16))
                    status.update(relay_state='backoff', next_retry_at=now_ms() + int(delay * 1000),
                                  retry_delay_seconds=delay)
            finally:
                await iterator.aclose()
            if blocked:
                # No retries for explicit access/jurisdiction rejection during
                # this catalog session. Siblings continue until owner closes.
                await asyncio.Event().wait()
            await asyncio.sleep(delay)

    async def stream(self, instruments):
        if self._running or self._close_task is not None and not self._close_task.done():
            raise RuntimeError('Close current Gate catalog stream before replacement')
        groups, seen, native_seen = {}, set(), set()
        for item in instruments:
            if item.id in seen:
                continue
            key = self._key(item)
            if key is None:
                raise ValueError('Unsupported Gate market passed to catalog stream')
            native_key = (key, self.rest_adapter._resolve(item)[1]['id'])
            if native_key in native_seen:
                raise ValueError('Ambiguous Gate native market identity in namespace')
            native_seen.add(native_key)
            seen.add(item.id)
            groups.setdefault(key, []).append(item)
        if (not seen or len(seen) > self.max_catalog_markets
                or any(len(group) > self.namespace_limit for group in groups.values())):
            raise StreamUnavailable('Gate catalog selection exceeds total or namespace budget')
        self._generation += 1
        self._selected = len(seen)
        self._running = True
        self._close_task = None
        self._relay_status = {key: {'relay_state': 'connecting', 'reconnects': 0, 'last_error_type': None,
                                   'next_retry_at': None, 'retry_delay_seconds': None, 'blocked_http_status': None} for key in groups}
        self._inbox = inbox = _Inbox(len(seen))
        try:
            for key, group in groups.items():
                self._children[key].on_payload = self.on_payload
                self._tasks.append(asyncio.create_task(self._relay(key, group, inbox)))
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
            self._coalesced = inbox.coalesced
            inbox.fail(StreamUnavailable('Gate catalog stream closed'))
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(child.close() for child in self._children.values()))
        for status in self._relay_status.values():
            status['relay_state'] = 'closed'

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned())
        await finish_owned(self._close_task)
