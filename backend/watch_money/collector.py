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


def field_stale(snapshot: MarketSnapshot, field: str, stale_seconds: float) -> bool:
    if (field == 'funding_interval_hours' and snapshot.field_timestamps.get(field) is None
            and snapshot.field_sources.get(field, '').endswith(
                (':catalog_metadata_observed_at', ':public_metadata_observed_at'))):
        at = snapshot.field_received_at.get(field)
        return at is None or not 0 <= now_ms() - at <= 6 * 3600_000
    return not 0 <= now_ms() - field_time(snapshot, field) <= stale_seconds * 1000


def enrich(snapshot: MarketSnapshot, stale_seconds: float) -> dict:
    result = snapshot.model_dump()
    primary = ['bid', 'ask'] if snapshot.bid and snapshot.ask else [next(
        (f for f in ('last', 'mark', 'index', 'bid', 'ask') if getattr(snapshot, f) is not None), 'last')]
    timestamp = min(field_time(snapshot, f) for f in primary)
    age = max(0, (now_ms() - timestamp) / 1000)
    result.update(age_seconds=round(age, 3), stale=age > stale_seconds,
                  field_ages_seconds={f: max(0, (now_ms() - field_time(snapshot, f)) / 1000)
                                      for f in VALUE_FIELDS if getattr(snapshot, f) is not None},
                  field_stale={f: field_stale(snapshot, f, stale_seconds)
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


def sanitize_legacy_volume(snapshot, instrument):
    """Old CCXT derivative baseVolume labels did not prove a base-unit amount."""
    source = snapshot.field_sources.get('volume', '')
    if (instrument is not None and instrument.kind == 'perpetual' and snapshot.volume is not None
            and source.startswith(('ccxt:', 'ccxt.pro:'))
            and any(key in source for key in ('.baseVolume', '.quoteVolume')) and '.info.' not in source):
        return snapshot.model_copy(update={'volume': None, 'volume_unit': None,
                                          'issues': sorted(set(snapshot.issues + ['legacy_volume_unit_unverified']))})
    return snapshot


class Collector:
    def __init__(self, store, adapters, notifications=None):
        self.store = store
        self.adapters = adapters
        self.notifications = notifications
        self.instruments = store.instruments()
        self.snapshots = {key: sanitize_legacy_volume(value, self.instruments.get(key))
                          for key, value in store.latest().items()}
        self.statuses = {name: VenueStatus(venue=name) for name in adapters}
        self.tasks = []
        self.stopping = asyncio.Event()
        self.discovered = set()
        from .recording import RawMarketRecorder
        self.recorder = RawMarketRecorder(store.directory)
        for adapter in self.adapters.values():
            adapter.on_payload = self.record_payload

    async def start(self):
        self.tasks = [asyncio.create_task(self.run(name, adapter), name=f'collector:{name}')
                      for name, adapter in self.adapters.items()]
        self.tasks.append(asyncio.create_task(self.watch_health(), name='market-health'))
        for name, adapter in self.adapters.items():
            if hasattr(adapter, 'fetch_light'):
                self.tasks.append(asyncio.create_task(self.run_light(name, adapter), name=f'light:{name}'))
            if hasattr(adapter, 'fetch_funding_light'):
                self.tasks.append(asyncio.create_task(self.run_light(name, adapter, funding=True), name=f'funding:{name}'))
        from .adapters.streaming import create_stream
        for name, adapter in self.adapters.items():
            streamer = create_stream(adapter)
            if streamer is not None:
                streamer.on_payload = self.record_payload
                self.tasks.append(asyncio.create_task(self.run_stream(name, streamer), name=f'stream:{name}'))

    async def record_payload(self, venue, transport, payload):
        settings = self.store.settings()
        if not settings.recording_enabled or settings.demo_mode:
            return
        if settings.recording_venues and venue not in settings.recording_venues:
            return
        try:
            await asyncio.to_thread(self.recorder.configure, enabled=True,
                                    max_total_bytes=settings.recording_max_mb * 1024 * 1024,
                                    retention_days=settings.recording_retention_days)
            await asyncio.to_thread(self.recorder.record, venue, transport, payload)
        except Exception:
            # Diagnostic disk failures must not stop market collection.
            pass

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
        if patch.get('volume') is not None:
            issues.discard('legacy_volume_unit_unverified')
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
                    or name not in settings.enabled_venues or name not in self.discovered):
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

    async def run_light(self, name, adapter, funding=False):
        """Rotate over the active catalog independently of detailed watchlist polling."""
        status = self.statuses[name]
        prefix = 'funding' if funding else 'light'

        def update(**values):
            for key, value in values.items():
                setattr(status, f'{prefix}_{key}', value)

        cursor = 0
        failures = 0
        while not self.stopping.is_set():
            settings = self.store.settings()
            period = settings.funding_poll_seconds if funding else settings.broad_poll_seconds
            if (not settings.collection_enabled or not settings.broad_collection_enabled or settings.demo_mode
                    or name not in settings.enabled_venues or name not in self.discovered):
                update(state='disabled')
                await self.pause(1)
                continue
            catalog = sorted((i for i in self.instruments.values() if i.venue == name and i.active
                              and (not funding or i.kind == 'perpetual')),
                             key=lambda i: i.id)
            update(subscriptions=len(catalog))
            if not catalog:
                update(state='no_markets')
                await self.pause(period)
                continue
            cursor %= len(catalog)
            selected = catalog[cursor:cursor + settings.broad_batch_size]
            try:
                update(state='polling')
                fetched = await asyncio.wait_for((adapter.fetch_funding_light(selected) if funding else adapter.fetch_light(selected)), timeout=90)
                ids = {i.id for i in selected}
                if any(s.instrument_id not in ids or s.venue != name for s in fetched):
                    raise ValueError('Light adapter returned unexpected market identity')
                snapshots = [self.merge_patch(s) for s in fetched]
                self.snapshots.update({s.instrument_id: s for s in snapshots})
                await asyncio.to_thread(self.store.save_snapshots, snapshots, settings.history_enabled)
                if snapshots:
                    update(last_received_at=max(s.received_at for s in snapshots))
                errors = getattr(adapter, f'{prefix}_errors', {})
                update(errors=errors, state='partial' if errors else ('online' if snapshots else 'no_data'))
                if not funding and not hasattr(adapter, 'fetch_funding_light'):
                    status.funding_subscriptions = sum(i.kind == 'perpetual' for i in catalog)
                    status.funding_state = 'via_light_rest' if status.funding_subscriptions else 'not_applicable'
                    funding_rows = [s for s in fetched if s.funding_rate is not None]
                    if funding_rows:
                        status.funding_last_received_at = max(s.received_at for s in funding_rows)
                cursor = (cursor + len(selected)) % len(catalog)
                failures = 0
                await self.pause(period)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                update(retries=getattr(status, f'{prefix}_retries') + 1, state=f'{type(exc).__name__}; retry',
                       errors=getattr(adapter, f'{prefix}_errors', {}))
                # Advance past a failing batch so one broken market cannot starve the catalog.
                cursor = (cursor + len(selected)) % len(catalog)
                await self.pause(min(300, period * 2 ** min(failures - 1, 5)))

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
        default_queries = getattr(adapter, 'search_queries', None)
        while not self.stopping.is_set():
            settings = self.store.settings()
            if not settings.collection_enabled or name not in settings.enabled_venues or settings.demo_mode:
                status.state = 'disabled'
                await self.pause(1)
                continue
            status.last_attempt_at = now_ms()
            status.state = 'connecting' if not status.last_received_at else 'polling'
            try:
                if default_queries is not None:
                    queries = settings.dex_search_queries.get(name, default_queries)
                    if adapter.search_queries != queries:
                        adapter.search_queries = queries
                        discovered_at = 0
                if hasattr(adapter, 'rpc_url'):
                    rpc = settings.rpc_urls.get('solana') or default_rpc
                    if adapter.rpc_url != rpc:
                        adapter.rpc_url = rpc
                        discovered_at = 0
                if not discovered_at or now_ms() - discovered_at > settings.discovery_seconds * 1000:
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
                    self.discovered.add(name)
                    status.discovery_complete = getattr(adapter, 'discovery_complete', True)
                    status.discovery_errors = getattr(adapter, 'discovery_errors', {})
                    status.discovery_state = getattr(adapter, 'discovery_state', None)
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
                status.detail_errors = getattr(adapter, 'fetch_errors', {})
                status.state = 'partial' if status.detail_errors else 'online'
                status.message = 'Some markets failed; see detail_errors' if status.detail_errors else None
                failures = 0
                await asyncio.to_thread(self.store.prune, settings.retention_days)
                await self.pause(settings.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                status.retries += 1
                # Exception text can contain API URLs and credentials. Expose only safe classification.
                status.discovery_errors = getattr(adapter, 'discovery_errors', {})
                status.detail_errors = getattr(adapter, 'fetch_errors', {})
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
               and (settings.broad_collection_enabled or i.base.upper() in {s.upper() for s in settings.watch_assets})}
        return {'instruments': [self.instruments[key].model_dump() for key in sorted(ids)],
                'snapshots': [enrich(s, settings.stale_seconds) for key, s in self.snapshots.items() if key in ids],
                'mode': 'live'}
