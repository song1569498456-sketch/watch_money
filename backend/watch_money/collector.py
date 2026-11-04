"""Supervised, independently switchable public market collection."""
from __future__ import annotations

import asyncio
import random
from contextlib import suppress

from .models import MarketSnapshot, VenueStatus, now_ms

VALUE_FIELDS = ('bid', 'ask', 'last', 'mark', 'index', 'volume', 'open_interest',
                'funding_rate', 'funding_kind', 'funding_interval_hours', 'next_funding_at')


def field_time(snapshot, field):
    received = snapshot.field_received_at.get(field, snapshot.received_at)
    source = snapshot.field_timestamps.get(field)
    if field not in snapshot.field_timestamps:
        source = snapshot.source_at
    return min(received, source) if source is not None else received


def enrich(snapshot: MarketSnapshot, stale_seconds: float) -> dict:
    result = snapshot.model_dump()
    primary = ['bid', 'ask'] if snapshot.bid and snapshot.ask else [next(
        (f for f in ('last', 'mark', 'index', 'bid', 'ask') if getattr(snapshot, f) is not None), 'last')]
    timestamp = min(field_time(snapshot, f) for f in primary)
    age = max(0, (now_ms() - timestamp) / 1000)
    result.update(age_seconds=round(age, 3), stale=age > stale_seconds,
                  field_ages_seconds={f: max(0, (now_ms() - field_time(snapshot, f)) / 1000)
                                      for f in VALUE_FIELDS if getattr(snapshot, f) is not None},
                  field_stale={f: now_ms() - field_time(snapshot, f) > stale_seconds * 1000
                               for f in VALUE_FIELDS if getattr(snapshot, f) is not None},
                  mid=(snapshot.bid + snapshot.ask) / 2 if snapshot.bid and snapshot.ask else None,
                  funding_8h=(snapshot.funding_rate * 8 / snapshot.funding_interval_hours
                              if snapshot.funding_rate is not None and snapshot.funding_interval_hours else None))
    return result


def quality(snapshot: MarketSnapshot) -> MarketSnapshot:
    issues = [issue for issue in snapshot.issues
              if issue not in {'source_timestamp_in_future', 'field_timestamp_in_future'}]
    valid = snapshot.valid
    if snapshot.bid and snapshot.ask and snapshot.bid >= snapshot.ask:
        issues.append('crossed_orderbook')
        valid = False
    if snapshot.source_at and snapshot.source_at > now_ms() + 30_000:
        issues.append('source_timestamp_in_future')
        valid = False
    if any(stamp is not None and stamp > now_ms() + 30_000 for stamp in snapshot.field_timestamps.values()):
        issues.append('field_timestamp_in_future')
        valid = False
    if snapshot.source_at is None:
        issues.append('source_timestamp_not_provided')
    if snapshot.funding_rate is not None and not snapshot.funding_interval_hours:
        issues.append('funding_interval_not_provided')
    received = dict(snapshot.field_received_at)
    timestamps = dict(snapshot.field_timestamps)
    for field in VALUE_FIELDS:
        if getattr(snapshot, field) is not None:
            received.setdefault(field, snapshot.received_at)
            # Capture the original envelope fallback once; later unrelated patches must not move it.
            timestamps.setdefault(field, snapshot.source_at)
    return snapshot.model_copy(update={'valid': valid, 'issues': sorted(set(issues)),
                                       'field_received_at': received, 'field_timestamps': timestamps})


class Collector:
    def __init__(self, store, adapters, notifications=None):
        self.store = store
        self.adapters = adapters
        self.notifications = notifications
        self.instruments = store.instruments()
        self.snapshots = store.latest()
        self.statuses = {name: VenueStatus(venue=name) for name in adapters}
        self.tasks = []
        self.stopping = asyncio.Event()

    async def start(self):
        self.tasks = [asyncio.create_task(self.run(name, adapter), name=f'collector:{name}')
                      for name, adapter in self.adapters.items()]
        self.tasks.append(asyncio.create_task(self.watch_health(), name='market-health'))
        from .adapters.streaming import create_stream
        for name, adapter in self.adapters.items():
            streamer = create_stream(adapter)
            if streamer is not None:
                self.tasks.append(asyncio.create_task(self.run_stream(name, streamer), name=f'stream:{name}'))

    def merge_patch(self, snapshot):
        snapshot = quality(snapshot)
        old = self.snapshots.get(snapshot.instrument_id)
        if old is None:
            return quality(snapshot)
        patch = snapshot.model_dump(exclude_unset=True)
        old = quality(old)
        data = old.model_dump()
        timestamps = dict(old.field_timestamps)
        sources = dict(old.field_sources)
        received = dict(old.field_received_at)
        for field in VALUE_FIELDS:
            if field not in patch:
                continue
            stamp = snapshot.field_timestamps.get(field, snapshot.source_at)
            previous = timestamps.get(field)
            older = (stamp is not None and previous is not None and previous <= now_ms() + 30_000
                     and stamp < previous)
            if stamp is None or previous is None:
                older = snapshot.field_received_at.get(field, snapshot.received_at) < received.get(field, old.received_at)
            if patch[field] is None or (getattr(old, field) is not None and older):
                patch.pop(field, None)
                if field == 'volume':
                    patch.pop('volume_unit', None)
                if field == 'open_interest':
                    patch.pop('oi_unit', None)
                continue
            if field in snapshot.field_timestamps:
                timestamps[field] = stamp
            received[field] = snapshot.field_received_at.get(field, snapshot.received_at)
            if field in snapshot.field_sources:
                sources[field] = snapshot.field_sources[field]
        if snapshot.received_at < old.received_at:
            for field in ('source', 'source_at', 'received_at'):
                patch.pop(field, None)
        data.update(patch)
        issues = set(old.issues + snapshot.issues)
        issues = {issue for issue in issues if 'crossed' not in issue.lower()}
        data.update(field_timestamps=timestamps, field_sources=sources, field_received_at=received,
                    issues=sorted(issues))
        return quality(MarketSnapshot.model_validate(data))

    async def run_stream(self, name, streamer):
        status = self.statuses[name]
        failures = 0
        last_saved = 0

        def selection():
            settings = self.store.settings()
            if (not settings.collection_enabled or not settings.prefer_websocket or settings.demo_mode
                    or name not in settings.enabled_venues or not getattr(self.adapters[name], '_markets', None)):
                return []
            return sorted((i for i in self.instruments.values() if i.venue == name and i.active
                           and i.base.upper() in {a.upper() for a in settings.watch_assets}), key=lambda i: i.id)

        while not self.stopping.is_set():
            instruments = selection()
            if not instruments:
                status.stream_state = 'disabled'
                await self.pause(1)
                continue
            identity = [i.id for i in instruments]
            signature = [i.model_dump_json() for i in instruments]
            iterator = streamer.stream(instruments).__aiter__()
            pending = None
            try:
                status.stream_state = 'connecting'
                while not self.stopping.is_set():
                    if [i.model_dump_json() for i in selection()] != signature:
                        break
                    if pending is None:
                        pending = asyncio.create_task(anext(iterator))
                    done, _ = await asyncio.wait([pending], timeout=1)
                    if not done:
                        continue
                    incoming = pending.result()
                    pending = None
                    if incoming.instrument_id not in identity or incoming.venue != name:
                        raise ValueError('Stream returned unexpected market identity')
                    self.snapshots[incoming.instrument_id] = self.merge_patch(incoming)
                    status.last_received_at = incoming.received_at
                    status.stream_state = 'online'
                    status.transport = 'WebSocket + REST metadata/funding'
                    failures = 0
                    settings = self.store.settings()
                    if now_ms() - last_saved >= settings.poll_seconds * 1000:
                        snapshots = [self.snapshots[i] for i in identity if i in self.snapshots]
                        await asyncio.to_thread(self.store.save_snapshots, snapshots, settings.history_enabled)
                        last_saved = now_ms()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                status.stream_reconnects += 1
                status.stream_state = f'{type(exc).__name__}; REST fallback'
                status.transport = 'REST'
            finally:
                if pending:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                with suppress(Exception):
                    await iterator.aclose()
                with suppress(Exception):
                    await streamer.close()
            if failures:
                await self.pause(min(300, 5 * 2 ** min(failures - 1, 6)))

    async def stop(self):
        self.stopping.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.gather(*(a.close() for a in self.adapters.values()), return_exceptions=True)

    async def pause(self, seconds):
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.stopping.wait(), timeout=seconds)

    async def event(self, event):
        if self.notifications and self.store.settings().notifications_enabled:
            await self.notifications.evaluate(event)

    async def watch_health(self):
        while not self.stopping.is_set():
            settings = self.store.settings()
            if settings.collection_enabled and not settings.demo_mode:
                for snapshot in list(self.snapshots.values()):
                    if snapshot.venue not in settings.enabled_venues or snapshot.is_demo:
                        continue
                    if enrich(snapshot, settings.stale_seconds)['stale']:
                        await self.event({'id': f'stale:{snapshot.instrument_id}', 'category': 'system',
                                          'type': 'data_stale', 'platforms': [snapshot.venue],
                                          'source_at': now_ms(), 'message': f'Stale market data: {snapshot.instrument_id}'})
                for status in list(self.statuses.values()):
                    if status.venue in settings.enabled_venues and status.last_received_at and status.state in {'error', 'rate_limited'}:
                        await self.event({'id': f'disconnected:{status.venue}', 'category': 'system',
                                          'type': 'disconnected', 'platforms': [status.venue],
                                          'source_at': now_ms(), 'message': f'Collection interrupted: {status.venue}'})
            await self.pause(5)

    async def run(self, name, adapter):
        status = self.statuses[name]
        failures = 0
        discovered_at = 0
        default_rpc = getattr(adapter, 'rpc_url', None)
        while not self.stopping.is_set():
            settings = self.store.settings()
            if not settings.collection_enabled or name not in settings.enabled_venues or settings.demo_mode:
                status.state = 'disabled'
                await self.pause(1)
                continue
            status.last_attempt_at = now_ms()
            status.state = 'connecting' if not status.last_received_at else 'polling'
            try:
                if hasattr(adapter, 'rpc_url'):
                    rpc = settings.rpc_urls.get('solana') or default_rpc
                    if adapter.rpc_url != rpc:
                        adapter.rpc_url = rpc
                        discovered_at = 0
                if not discovered_at or now_ms() - discovered_at > 6 * 3600_000:
                    instruments = await asyncio.wait_for(adapter.discover(), timeout=90)
                    previous = {i.id for i in self.instruments.values() if i.venue == name}
                    if any(i.venue != name for i in instruments):
                        raise ValueError('Adapter returned foreign venue instruments')
                    discovered_ids = {i.id for i in instruments}
                    retired = ([self.instruments[key].model_copy(update={'active': False})
                                for key in previous - discovered_ids]
                               if getattr(adapter, 'discovery_complete', True) else [])
                    await asyncio.to_thread(self.store.save_instruments, retired)
                    await asyncio.to_thread(self.store.save_instruments, instruments)
                    pools = getattr(adapter, 'pools', None)
                    if isinstance(pools, dict):
                        self.store.set(f'pools:{name}', [p.model_dump() for p in pools.values()])
                    self.instruments.update(self.store.instruments())
                    if discovered_at:
                        for instrument in instruments:
                            if instrument.id not in previous:
                                await self.event({'id': f'new_market:{instrument.id}', 'type': 'new_market',
                                                  'category': 'market', 'asset': instrument.base,
                                                  'platforms': [name], 'source_at': now_ms(),
                                                  'message': f'New market {instrument.native_symbol} on {name}'})
                    discovered_at = now_ms()
                selected = [i for i in self.instruments.values() if i.venue == name and i.active
                            and i.base.upper() in {s.upper() for s in settings.watch_assets}]
                status.subscriptions = len(selected)
                if not selected:
                    status.state = 'no_markets'
                    status.message = 'No discovered markets match the watchlist'
                    await self.pause(settings.poll_seconds)
                    continue
                fetched = await asyncio.wait_for(adapter.fetch(selected), timeout=90)
                selected_ids = {i.id for i in selected}
                if any(s.instrument_id not in selected_ids or s.venue != name for s in fetched):
                    raise ValueError('Adapter returned unexpected market identity')
                snapshots = [self.merge_patch(s) for s in fetched]
                if not snapshots:
                    raise ValueError('No snapshots returned for the selected markets')
                self.snapshots.update({s.instrument_id: s for s in snapshots})
                await asyncio.to_thread(self.store.save_snapshots, snapshots, settings.history_enabled)
                pools = getattr(adapter, 'pools', None)
                if isinstance(pools, dict):
                    self.store.set(f'pools:{name}', [p.model_dump() for p in pools.values()])
                status.parse_errors += sum(not s.valid for s in snapshots)
                status.missing_fields = sorted({key for s in snapshots for key in
                                               ('bid', 'ask', 'source_at', 'volume') if getattr(s, key) is None})
                status.last_received_at = max(s.received_at for s in snapshots)
                status.state = 'online'
                status.message = None
                failures = 0
                await asyncio.to_thread(self.store.prune, settings.retention_days)
                await self.pause(settings.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                status.retries += 1
                # Exception text can contain API URLs and credentials. Expose only safe classification.
                category = type(exc).__name__
                limited = 'ratelimit' in category.lower() or getattr(getattr(exc, 'response', None), 'status_code', None) == 429
                status.rate_limited += int(limited)
                status.parse_errors += int(isinstance(exc, (ValueError, KeyError, TypeError)))
                status.state = 'rate_limited' if limited else 'error'
                delay = min(300, settings.poll_seconds * 2 ** min(failures - 1, 5)) + random.uniform(0, 1)
                status.message = f'{category}; retry in {delay:.0f}s; check network/API access and platform coverage'
                await self.event({'id': f'collector:{name}', 'type': 'collection_failure', 'category': 'system',
                                  'platforms': [name], 'source_at': now_ms(), 'message': status.message})
                await self.pause(delay)

    def markets(self):
        settings = self.store.settings()
        allowed = set(settings.enabled_venues)
        if settings.demo_mode:
            from .demo import demo_markets
            instruments, snapshots = demo_markets()
            return {'instruments': [i.model_dump() for i in instruments],
                    'snapshots': [enrich(s, settings.stale_seconds) for s in snapshots], 'mode': 'demo'}
        ids = {key for key, i in self.instruments.items() if i.venue in allowed
               and i.base.upper() in {s.upper() for s in settings.watch_assets}}
        return {'instruments': [self.instruments[key].model_dump() for key in sorted(ids)],
                'snapshots': [enrich(s, settings.stale_seconds) for key, s in self.snapshots.items() if key in ids],
                'mode': 'live'}
