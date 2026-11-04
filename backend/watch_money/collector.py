"""Supervised, independently switchable public market collection."""
from __future__ import annotations

import asyncio
import math
import random
from contextlib import suppress
from threading import Lock
from time import monotonic
from uuid import uuid4

from .models import MarketSnapshot, VenueStatus, now_ms

VALUE_FIELDS = ('bid', 'ask', 'last', 'mark', 'index', 'volume', 'open_interest',
                'funding_rate', 'funding_kind', 'funding_interval_hours', 'next_funding_at')


def funding_response_source_ages(rows, instruments, as_of, stale_seconds):
    """Arrival ages of this response's trusted rate fields, never merged cache."""
    from .scanner import funding_field_state

    ages = []
    for row in rows:
        item = instruments.get(row.instrument_id)
        if item is None:
            continue
        state = funding_field_state(row, item, 'funding_rate', as_of, stale_seconds)
        source, received = state['source_at'], state['received_at']
        if (state['trusted'] and state['issue'] in (None, 'funding_rate_stale')
                and source is not None and received is not None and 0 < source <= received <= as_of):
            ages.append((received - source) / 1000)
    return sorted(ages)


def field_time(snapshot, field):
    received = snapshot.field_received_at.get(field, snapshot.received_at)
    source = snapshot.field_timestamps.get(field)
    if field not in snapshot.field_timestamps:
        source = snapshot.source_at
    return min(received, source) if source is not None else received


def field_stale(snapshot: MarketSnapshot, field: str, stale_seconds: float) -> bool:
    source = snapshot.field_timestamps.get(field, snapshot.source_at)
    received = snapshot.field_received_at.get(field, snapshot.received_at)
    if received > now_ms() or (source is not None and source > received):
        return True
    if (field == 'funding_interval_hours' and snapshot.field_timestamps.get(field) is None
            and snapshot.field_sources.get(field, '').endswith(
                (':catalog_metadata_observed_at', ':public_metadata_observed_at'))):
        at = snapshot.field_received_at.get(field)
        return at is None or not 0 <= now_ms() - at <= 6 * 3600_000
    return not 0 <= now_ms() - field_time(snapshot, field) <= stale_seconds * 1000


def enrich(snapshot: MarketSnapshot, stale_seconds: float) -> dict:
    result = snapshot.model_dump()
    primary = ['bid', 'ask'] if snapshot.bid and snapshot.ask else [next(
        (f for f in ('last', 'mark', 'index') if getattr(snapshot, f) is not None), None)]
    primary = [field for field in primary if field is not None]
    at = now_ms()
    timestamp = min((field_time(snapshot, f) for f in primary), default=field_time(snapshot, 'last'))
    age = max(0, (at - timestamp) / 1000)
    stale_fields = {f: field_stale(snapshot, f, stale_seconds)
                    for f in VALUE_FIELDS if getattr(snapshot, f) is not None}
    legacy_times = not snapshot.field_timestamps and not snapshot.field_received_at

    def price_source(field):
        return snapshot.field_timestamps.get(field, snapshot.source_at if legacy_times else None)

    # Structural validity remains field-independent. The overview describes
    # only the displayed price fields, not unrelated funding/volume updates.
    price_valid = snapshot.valid and bool(primary) and all(
        getattr(snapshot, f) > 0 and snapshot.field_received_at.get(f, snapshot.received_at) <= at
        and (price_source(f) is None or price_source(f) <=
             snapshot.field_received_at.get(f, snapshot.received_at)) for f in primary)
    if primary == ['bid', 'ask'] and snapshot.bid >= snapshot.ask:
        price_valid = False
    source_known = bool(primary) and all(price_source(f) is not None for f in primary)
    stale = not primary or any(stale_fields.get(f, True) for f in primary)
    result.update(age_seconds=round(age, 3), stale=stale, price_valid=price_valid,
                  price_fresh=price_valid and source_known and not stale,
                  price_source_known=source_known, price_fields=primary,
                  field_ages_seconds={f: max(0, (now_ms() - field_time(snapshot, f)) / 1000)
                                      for f in VALUE_FIELDS if getattr(snapshot, f) is not None},
                  field_stale=stale_fields,
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
        self.server_instance = str(uuid4())
        self._view_seq = 0
        self._view_lock = Lock()
        self._coverage_lock = Lock()
        self._coverage_async_lock = asyncio.Lock()
        self._coverage_cache = {}
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
        self._record_jobs = set()
        self._record_lock = asyncio.Lock()
        self._record_config = None
        self.recording_queue_dropped = 0
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
        if not settings.recording_enabled or settings.demo_mode or self.stopping.is_set():
            return
        if settings.recording_venues and venue not in settings.recording_venues:
            return
        # Bounded across all observers, including cancelled callers whose worker
        # still owns an in-flight disk write. Queued jobs recheck committed config.
        if len(self._record_jobs) >= 16:
            self.recording_queue_dropped += 1
            return

        def write():
            current = self.store.settings()
            if (not current.recording_enabled or current.demo_mode or self.stopping.is_set()
                    or (current.recording_venues and venue not in current.recording_venues)):
                return
            config = (current.recording_max_mb, current.recording_retention_days)
            if config != self._record_config:
                self.recorder.configure(enabled=True, max_total_bytes=config[0] * 1024 * 1024,
                                        retention_days=config[1])
                self._record_config = config
            self.recorder.record(venue, transport, payload)

        async def job():
            async with self._record_lock:
                try:
                    await asyncio.to_thread(write)
                except Exception:
                    # Diagnostic disk failures must not stop market collection.
                    pass

        task = asyncio.create_task(job(), name='raw-payload-write')
        self._record_jobs.add(task)
        task.add_done_callback(self._record_jobs.discard)
        await asyncio.shield(task)

    def merge_patch(self, snapshot):
        from .protocol_metrics import merge_protocol_metrics
        snapshot = quality(snapshot)
        old = self.snapshots.get(snapshot.instrument_id)
        metric_fields = merge_protocol_metrics(old, snapshot, now_ms())
        if old is None:
            return quality(snapshot.model_copy(update=metric_fields))
        patch = snapshot.model_dump(exclude_unset=True)
        clears = set(snapshot.cleared_fields)
        for field in clears:
            patch[field] = None
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
            tombstone = getattr(old, field) is None and sources.get(field, '').endswith(':explicit_unavailable')
            has_previous = getattr(old, field) is not None or tombstone
            older = (stamp is not None and previous is not None and previous <= min(now_ms(), received.get(field, old.received_at))
                     and stamp < previous)
            if stamp is None or previous is None:
                older = snapshot.field_received_at.get(field, snapshot.received_at) < received.get(field, old.received_at)
            # An unrelated price envelope is not an observation of a missing
            # funding/metadata field. Only values or explicit clears establish
            # an ordering barrier; retain the incoming field's original clock.
            older = has_previous and older
            if tombstone and field not in clears and stamp is not None:
                # A contradictory equal-time or delayed quote cannot resurrect
                # a removed side, even after persistence or stream restart.
                barrier = previous if previous is not None else received.get(field)
                older = older or (barrier is not None and barrier <= min(now_ms(), received.get(field, old.received_at)) and stamp <= barrier)
            if (patch[field] is None and field not in clears) or older:
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
        data.update(metric_fields)
        issues = set(old.issues + snapshot.issues)
        issues = {issue for issue in issues if 'crossed' not in issue.lower()}
        if patch.get('volume') is not None:
            issues.discard('legacy_volume_unit_unverified')
        data.update(field_timestamps=timestamps, field_sources=sources, field_received_at=received,
                    cleared_fields=[], issues=sorted(issues))
        return quality(MarketSnapshot.model_validate(data))

    async def run_stream(self, name, streamer):
        status = self.statuses[name]
        failures = 0
        last_saved = 0
        last_patch_clock = None
        next_selection_check = 0
        settings = None
        instruments = []
        signature = ()

        def selection():
            settings = self.store.settings()
            if (not settings.collection_enabled or not settings.prefer_websocket or settings.demo_mode
                    or name not in settings.enabled_venues or name not in self.discovered):
                status.stream_requested_markets = status.stream_selected_markets = 0
                status.stream_truncated = False
                return settings, []
            broad = settings.broad_collection_enabled and getattr(streamer, 'catalog_stream', False)
            watched = {a.upper() for a in settings.watch_assets}
            selected = sorted((i for i in tuple(self.instruments.values()) if i.venue == name and i.active
                               and (broad or i.base.upper() in watched)), key=lambda i: i.id)
            status.stream_requested_markets = len(selected)
            cap = getattr(streamer, 'max_catalog_markets', None)
            if cap is not None:
                selected = selected[:cap]
            status.stream_selected_markets = len(selected)
            status.stream_truncated = len(selected) < status.stream_requested_markets
            return settings, selected

        def check_selection():
            nonlocal settings, instruments, signature, next_selection_check
            # A busy feed must not turn catalogue/settings work into O(markets)
            # work per patch. Idle feeds use the same one-second deadline.
            next_selection_check = monotonic() + 1
            settings, instruments = selection()
            signature = tuple(item.model_dump_json() for item in instruments)

        while not self.stopping.is_set():
            if monotonic() >= next_selection_check:
                check_selection()
            if not instruments:
                status.stream_state = 'disabled'
                await self.pause(1)
                continue
            identity = {i.id for i in instruments}
            active_signature = signature
            iterator = streamer.stream(instruments).__aiter__()
            pending = None
            try:
                status.stream_state = 'connecting'
                while not self.stopping.is_set():
                    if monotonic() >= next_selection_check:
                        check_selection()
                        if signature != active_signature:
                            break
                    if pending is None:
                        pending = asyncio.create_task(anext(iterator))
                    done, _ = await asyncio.wait([pending], timeout=max(.001, next_selection_check - monotonic()))
                    if not done:
                        continue
                    if monotonic() >= next_selection_check:
                        check_selection()
                        if signature != active_signature:
                            break
                    incoming = pending.result()
                    pending = None
                    if incoming.instrument_id not in identity or incoming.venue != name:
                        raise ValueError('Stream returned unexpected market identity')
                    self.snapshots[incoming.instrument_id] = self.merge_patch(incoming)
                    clock = monotonic()
                    if last_patch_clock is not None:
                        gap = max(0, clock - last_patch_clock)
                        status.stream_last_gap_seconds = round(gap, 3)
                        status.stream_max_gap_seconds = max(status.stream_max_gap_seconds or 0, round(gap, 3))
                    last_patch_clock = clock
                    status.stream_last_patch_at = now_ms()
                    status.stream_received_patches += 1
                    status.stream_observer_dropped = getattr(streamer, 'payload_observer_dropped', 0)
                    status.last_received_at = incoming.received_at
                    status.stream_state = 'online'
                    status.transport = 'WebSocket + REST metadata/funding'
                    failures = 0
                    if now_ms() - last_saved >= settings.poll_seconds * 1000:
                        snapshots = [self.snapshots[i] for i in identity if i in self.snapshots]
                        await asyncio.to_thread(self.store.save_snapshots, snapshots, settings.history_enabled)
                        last_saved = now_ms()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                status.stream_reconnects += 1
                status.stream_last_error_type = type(exc).__name__
                status.stream_last_error_at = now_ms()
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
                status.stream_observer_dropped = getattr(streamer, 'payload_observer_dropped', 0)
            if failures:
                await self.pause(min(300, 5 * 2 ** min(failures - 1, 6)))

    @staticmethod
    def _coverage_counts(item, snapshot, as_of, stale_seconds):
        from .scanner import funding_field_state

        counts = dict(light_observed_markets=0, light_fresh_markets=0,
                      funding_observed_markets=0, funding_fresh_markets=0,
                      funding_source_stale_markets=0, funding_source_missing_markets=0,
                      funding_source_future_markets=0)
        if snapshot is None or snapshot.is_demo:
            return counts
        if any(getattr(snapshot, field) is not None for field in ('bid', 'ask', 'last', 'mark', 'index')):
            counts['light_observed_markets'] = 1
            counts['light_fresh_markets'] = int(all(
                funding_field_state(snapshot, item, field, as_of, stale_seconds)['rankable']
                for field in ('bid', 'ask')))
        if item.kind == 'perpetual' and snapshot.funding_rate is not None:
            counts['funding_observed_markets'] = 1
            fields = {field: funding_field_state(snapshot, item, field, as_of, stale_seconds)
                      for field in ('funding_rate', 'funding_kind', 'funding_interval_hours')}
            counts['funding_fresh_markets'] = int(all(state['rankable'] for state in fields.values()))
            rate = fields['funding_rate']
            counts['funding_source_stale_markets'] = int(rate['trusted'] and rate['issue'] == 'funding_rate_stale')
            counts['funding_source_missing_markets'] = int(not rate['trusted'])
            counts['funding_source_future_markets'] = int(any(stamp is not None and stamp > as_of
                for stamp in (rate['source_at'], rate['received_at'])))
        return counts

    @staticmethod
    def _coverage_deadline(snapshot, as_of, stale_seconds):
        """Next exact time gate transition, never a new freshness allowance."""
        if snapshot is None:
            return math.inf
        boundaries = [snapshot.received_at]
        for field in ('bid', 'ask', 'funding_rate', 'funding_kind', 'funding_interval_hours'):
            source = snapshot.field_timestamps.get(field, snapshot.source_at)
            received = snapshot.field_received_at.get(field, snapshot.received_at)
            boundaries.append(received)
            if source is not None:
                boundaries.append(source)
            metadata = (field == 'funding_interval_hours' and source is None and
                        snapshot.field_sources.get(field, '').endswith(
                            (':catalog_metadata_observed_at', ':public_metadata_observed_at')))
            ttl = 6 * 3600000 if metadata else stale_seconds * 1000
            effective = min(source, received) if source is not None else received
            boundaries.append(math.floor(effective + ttl) + 1)
        return min((boundary for boundary in boundaries if boundary > as_of), default=math.inf)

    def refresh_status_coverage(self, venue=None):
        """Read-time counts reuse unchanged fields only until their exact next time gate.

        Collection replaces snapshots/instruments; the cache holds at most one
        reference per active catalog item and is pruned on every refresh.
        """
        with self._coverage_lock:
            settings = self.store.settings()
            as_of = now_ms()
            catalog = tuple(self.instruments.values())
            snapshots = dict(self.snapshots)
            totals = {name: dict(light_subscriptions=0, funding_subscriptions=0,
                light_observed_markets=0, light_fresh_markets=0, funding_observed_markets=0,
                funding_fresh_markets=0, funding_source_stale_markets=0,
                funding_source_missing_markets=0, funding_source_future_markets=0)
                for name in self.statuses if venue is None or name == venue}
            next_cache = ({key: value for key, value in self._coverage_cache.items()
                           if value[0].venue != venue and key in self.instruments} if venue is not None else {})
            for item in catalog:
                if not item.active or item.venue not in totals:
                    continue
                snapshot = snapshots.get(item.id)
                cached = self._coverage_cache.get(item.id)
                if not (cached and cached[0] is item and cached[1] is snapshot
                        and cached[2] == settings.stale_seconds and cached[3] <= as_of < cached[4]):
                    cached = (item, snapshot, settings.stale_seconds, as_of,
                              self._coverage_deadline(snapshot, as_of, settings.stale_seconds),
                              self._coverage_counts(item, snapshot, as_of, settings.stale_seconds))
                next_cache[item.id] = cached
                values = totals[item.venue]
                values['light_subscriptions'] += 1
                values['funding_subscriptions'] += int(item.kind == 'perpetual')
                for field, count in cached[5].items():
                    values[field] += count
            self._coverage_cache = next_cache
            for name, values in totals.items():
                status = self.statuses.get(name)
                if status is None:
                    continue
                for field, value in values.items():
                    setattr(status, field, value)
                for prefix in ('light', 'funding'):
                    count = values[f'{prefix}_subscriptions']
                    setattr(status, f'{prefix}_coverage_ratio', values[f'{prefix}_fresh_markets'] / count if count else 0)
                from .funding_status import observation_state
                status.funding_observation_state = observation_state(status)
                status.funding_comparison_max_age_seconds = settings.stale_seconds
                status.funding_observation_checked_at = as_of
                adapter = self.adapters.get(name)
                if not hasattr(adapter, 'plan_light') or not getattr(adapter, 'layered_light', True):
                    status.light_mode = 'legacy_bounded_public_fetch'
                    batches = (values['light_subscriptions'] + settings.broad_batch_size - 1) // settings.broad_batch_size
                    status.light_estimated_cycle_seconds = batches * settings.broad_poll_seconds if batches else None

    async def _refresh_status_coverage_async(self, venue=None):
        # Only one background coverage job exists; cancellation drains its worker
        # before releasing the admission lock or completing collector shutdown.
        async with self._coverage_async_lock:
            job = asyncio.create_task(asyncio.to_thread(self.refresh_status_coverage, venue))
            try:
                await asyncio.shield(job)
            except asyncio.CancelledError:
                await job
                raise

    async def run_planned_light(self, name, adapter, funding=False):
        """Product-wide bulk sweeps and bounded fallbacks have independent clocks."""
        status = self.statuses[name]
        prefix = 'funding' if funding else 'light'
        layer_errors = {True: {}, False: {}}
        states = {True: 'idle', False: 'idle'}
        completed = {}
        completed_clock = {}
        cycles = {}
        estimates = {}
        signatures = {}
        present = set()

        def update(**values):
            for key, value in values.items():
                setattr(status, f'{prefix}_{key}', value)

        async def summarize():
            errors = {**layer_errors[True], **layer_errors[False]}
            active = [states[layer] for layer in present]
            state = ('partial' if errors else 'polling' if 'polling' in active else
                     'online' if 'online' in active else 'no_data' if active else 'no_markets')
            update(errors=errors, state=state,
                   estimated_cycle_seconds=max((estimates[layer] for layer in present if layer in estimates), default=None),
                   cycle_seconds=max((cycles[layer] for layer in present), default=None)
                   if present and all(layer in cycles for layer in present) else None,
                   last_cycle_at=min((completed[layer] for layer in present), default=None)
                   if present and all(layer in completed for layer in present) else None)
            if not funding and not hasattr(adapter, 'fetch_funding_light'):
                status.funding_mode = 'via_public_price_bulk'
                status.funding_cycle_seconds = status.light_cycle_seconds
                status.funding_estimated_cycle_seconds = status.light_estimated_cycle_seconds
                status.funding_last_cycle_at = status.light_last_cycle_at
                status.funding_last_attempt_at = status.light_last_attempt_at
            await self._refresh_status_coverage_async(name)

        async def worker(full_product):
            failures = 0
            while not self.stopping.is_set():
                settings = self.store.settings()
                period = settings.funding_poll_seconds if funding else settings.broad_poll_seconds
                if (not settings.collection_enabled or not settings.broad_collection_enabled or settings.demo_mode
                        or name not in settings.enabled_venues or name not in self.discovered):
                    update(state='disabled')
                    await self.pause(1)
                    continue
                catalog = sorted((item for item in self.instruments.values() if item.venue == name and item.active
                                  and (not funding or item.kind == 'perpetual')), key=lambda item: item.id)
                try:
                    from .adapters.cex import CexAdapter
                    size = min(settings.broad_batch_size, settings.broad_symbol_batch_size) if isinstance(adapter, CexAdapter) else settings.broad_batch_size
                    plan = adapter.plan_light(catalog, funding=funding, batch_size=size)
                except Exception as exc:
                    update(state=f'{type(exc).__name__}; planning retry')
                    await self.pause(1)
                    continue
                present.clear()
                present.update(batch.full_product for batch in plan)
                bulk = [batch for batch in plan if batch.full_product]
                fallback = [batch for batch in plan if not batch.full_product]
                for layer, layer_jobs in ((True, bulk), (False, fallback)):
                    signature = tuple((batch.key, tuple(item.id for item in batch.instruments)) for batch in layer_jobs)
                    if signatures.get(layer) != signature:
                        signatures[layer] = signature
                        states[layer] = 'idle'
                        for values in (completed, completed_clock, cycles, estimates):
                            values.pop(layer, None)
                    current_ids = {item.id for batch in layer_jobs for item in batch.instruments}
                    layer_errors[layer] = {key: value for key, value in layer_errors[layer].items() if key in current_ids}
                update(subscriptions=len(catalog), bulk_markets=sum(len(batch.instruments) for batch in bulk),
                       fallback_markets=sum(len(batch.instruments) for batch in fallback), bulk_groups=len(bulk),
                       mode='public_bulk_with_symbol_fallback' if bulk and fallback else
                       'public_product_bulk' if bulk else 'bounded_symbol_rotation')
                if not funding and not hasattr(adapter, 'fetch_funding_light'):
                    status.funding_bulk_markets = sum(item.kind == 'perpetual' for batch in bulk for item in batch.instruments)
                    status.funding_fallback_markets = sum(item.kind == 'perpetual' for batch in fallback for item in batch.instruments)
                    status.funding_bulk_groups = sum(any(item.kind == 'perpetual' for item in batch.instruments) for batch in bulk)
                await summarize()
                jobs = bulk if full_product else fallback
                sweep_signature = signatures[full_product]
                minimum = (adapter.minimum_light_cycle_seconds(jobs, funding=funding)
                           if hasattr(adapter, 'minimum_light_cycle_seconds') else 0)
                if minimum:
                    estimates[full_product] = max(period, minimum)
                layer_ids = {item.id for batch in jobs for item in batch.instruments}
                layer_errors[full_product] = {key: value for key, value in layer_errors[full_product].items() if key in layer_ids}
                if not jobs:
                    states[full_product] = 'not_applicable'
                    await summarize()
                    await self.pause(min(period, 5))
                    continue
                started = monotonic()
                finished = 0
                sweep_period = period
                schedule_reason = 'configured'
                for batch in jobs:
                    if self.stopping.is_set():
                        return
                    if signatures[full_product] != sweep_signature:
                        break
                    current = self.store.settings()
                    if not current.collection_enabled or not current.broad_collection_enabled or name not in current.enabled_venues:
                        break
                    states[full_product] = 'polling'
                    update(last_attempt_at=now_ms())
                    try:
                        result = await asyncio.wait_for(adapter.fetch_light_batch(batch, funding=funding),
                                                        timeout=current.broad_request_timeout_seconds)
                        ids = {item.id for item in batch.instruments}
                        if any(row.instrument_id not in ids or row.venue != name for row in result.snapshots):
                            raise ValueError('Bulk adapter returned unexpected market identity')
                        for key in ids:
                            layer_errors[full_product].pop(key, None)
                        layer_errors[full_product].update(result.errors)
                        if result.unsupported:
                            layer_errors[full_product].update((key, 'Public capability unavailable') for key in ids if key not in result.errors)
                        if funding and not result.error_type:
                            # Inspect the incoming funding patch BEFORE cache merge:
                            # a newer ticker envelope cannot renew a rate timestamp.
                            ages = funding_response_source_ages(result.snapshots,
                                {item.id: item for item in batch.instruments}, now_ms(), current.stale_seconds)
                            update(arrival_source_age_p90_seconds=ages[math.ceil(len(ages) * .9) - 1] if ages else None,
                                   arrival_source_age_max_seconds=max(ages, default=None),
                                   arrival_source_age_samples=len(ages),
                                   arrival_checked_at=now_ms())
                            if full_product and hasattr(adapter, 'funding_poll_policy'):
                                target, reason = adapter.funding_poll_policy(batch, source_ages=ages,
                                    period=period, stale_seconds=current.stale_seconds)
                                if target < sweep_period or reason != 'configured' and schedule_reason == 'configured':
                                    sweep_period, schedule_reason = target, reason
                        snapshots = [self.merge_patch(row) for row in result.snapshots]
                        self.snapshots.update({row.instrument_id: row for row in snapshots})
                        await asyncio.to_thread(self.store.save_snapshots, snapshots, current.history_enabled)
                        if snapshots:
                            update(last_received_at=max(row.received_at for row in result.snapshots))
                        states[full_product] = 'online' if snapshots else 'no_data'
                        if not funding and not hasattr(adapter, 'fetch_funding_light'):
                            status.funding_state = 'via_light_rest'
                            status.funding_mode = 'via_public_price_bulk'
                            funding_rows = [row for row in result.snapshots if row.funding_rate is not None]
                            if funding_rows:
                                status.funding_last_received_at = max(row.received_at for row in funding_rows)
                        failures = failures + 1 if result.error_type else 0
                        if result.error_type:
                            update(retries=getattr(status, f'{prefix}_retries') + 1)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        failures += 1
                        layer_errors[full_product].update((item.id, type(exc).__name__) for item in batch.instruments)
                        states[full_product] = 'retry'
                        update(retries=getattr(status, f'{prefix}_retries') + 1)
                    finished += 1
                    elapsed = max(.001, monotonic() - started)
                    if signatures[full_product] == sweep_signature:
                        estimates[full_product] = max(sweep_period, minimum, elapsed * len(jobs) / finished)
                    await summarize()
                    gap = max(current.broad_inter_batch_seconds,
                              getattr(adapter, 'light_min_bulk_interval', 0) if full_product else 0)
                    if failures and not full_product:
                        gap = max(gap, min(300, period * 2 ** min(failures - 1, 5)))
                    if finished < len(jobs):
                        await self.pause(gap)
                if finished == len(jobs) and signatures[full_product] == sweep_signature:
                    ended = monotonic()
                    if full_product in completed_clock:
                        cycles[full_product] = max(0, ended - completed_clock[full_product])
                    completed_clock[full_product] = ended
                    completed[full_product] = now_ms()
                    estimates[full_product] = max(sweep_period, minimum, ended - started)
                    await summarize()
                # Cadence is start-to-start for a whole sweep, not a sleep per
                # 20/100 markets. A long fallback sweep never delays native bulk.
                elapsed = monotonic() - started
                delay = max(.01, sweep_period - elapsed)
                if failures:
                    delay = max(delay, min(300, period * 2 ** min(failures - 1, 5)))
                if funding and full_product:
                    update(effective_poll_seconds=round(elapsed + delay, 3),
                           schedule_reason='request_backoff' if failures else schedule_reason)
                await self.pause(delay)

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(worker(True), name=f'{prefix}:{name}:bulk')
            tasks.create_task(worker(False), name=f'{prefix}:{name}:symbols')

    async def run_light(self, name, adapter, funding=False):
        """Rotate over the active catalog independently of detailed watchlist polling."""
        if hasattr(adapter, 'plan_light') and getattr(adapter, 'layered_light', True):
            return await self.run_planned_light(name, adapter, funding)
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
        if self._record_jobs:
            await asyncio.gather(*tuple(self._record_jobs), return_exceptions=True)
        await asyncio.gather(*(a.close() for a in self.adapters.values()), return_exceptions=True)

    async def pause(self, seconds):
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.stopping.wait(), timeout=seconds)

    def _current_stale_snapshot(self, instrument_id, settings):
        snapshot = self.snapshots.get(instrument_id)
        instrument = self.instruments.get(instrument_id)
        if (snapshot is None or instrument is None or not instrument.active or snapshot.is_demo
                or snapshot.venue not in settings.enabled_venues):
            return None
        primary = ('bid', 'ask') if snapshot.bid and snapshot.ask else tuple(
            field for field in ('last', 'mark', 'index') if getattr(snapshot, field) is not None)[:1]
        return snapshot if not primary or any(
            field_stale(snapshot, field, settings.stale_seconds) for field in primary) else None

    async def event(self, event, *, expected_snapshot=None):
        settings = self.store.settings()
        if not self.notifications or not settings.notifications_enabled:
            return
        if expected_snapshot is not None:
            if (self.stopping.is_set() or not settings.collection_enabled or settings.demo_mode
                    or self._current_stale_snapshot(expected_snapshot.instrument_id, settings) is not expected_snapshot):
                return
        # NotificationService.evaluate has no suspension before enqueueing. Keep
        # the final identity/freshness check adjacent, with no await in between.
        await self.notifications.evaluate(event)

    async def watch_health(self):
        while not self.stopping.is_set():
            await self._refresh_status_coverage_async()
            settings = self.store.settings()
            if (self.notifications and settings.notifications_enabled and
                    settings.collection_enabled and not settings.demo_mode):
                for index, instrument_id in enumerate(tuple(self.snapshots)):
                    if index % 128 == 0:
                        await asyncio.sleep(0)
                        current = self.store.settings()
                        if (not current.notifications_enabled or not current.collection_enabled or current.demo_mode
                                or current.enabled_venues != settings.enabled_venues
                                or current.stale_seconds != settings.stale_seconds):
                            break
                    # A prior yield (including another notification) may have
                    # refreshed or removed this market. Never use the old object.
                    current = self.store.settings()
                    if (not current.notifications_enabled or not current.collection_enabled or current.demo_mode):
                        break
                    snapshot = self._current_stale_snapshot(instrument_id, current)
                    if snapshot is not None:
                        await self.event({'id': f'stale:{snapshot.instrument_id}', 'category': 'system',
                                          'type': 'data_stale', 'platforms': [snapshot.venue],
                                          'source_at': now_ms(), 'message': f'Stale market data: {snapshot.instrument_id}'},
                                         expected_snapshot=snapshot)
                for status in list(self.statuses.values()):
                    settings = self.store.settings()
                    if not settings.notifications_enabled or not settings.collection_enabled or settings.demo_mode:
                        break
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
        # Ordering describes when a view STARTED building, not when its worker
        # happened to finish or when any market field was received.
        with self._view_lock:
            self._view_seq += 1
            view_seq = self._view_seq
        settings = self.store.settings()
        metadata = {'server_instance': self.server_instance, 'view_seq': view_seq,
                    'view_context': {'enabled_venues': sorted(set(settings.enabled_venues)),
                        'demo_mode': settings.demo_mode,
                        'broad_collection_enabled': settings.broad_collection_enabled,
                        'watch_assets': sorted({asset.upper() for asset in settings.watch_assets}),
                        'stale_seconds': settings.stale_seconds}}
        allowed = set(settings.enabled_venues)
        if settings.demo_mode:
            from .demo import demo_markets
            instruments, snapshots = demo_markets()
            return {'instruments': [i.model_dump() for i in instruments],
                    'snapshots': [enrich(s, settings.stale_seconds) for s in snapshots], 'mode': 'demo', **metadata}
        # Worker-built API/WS views may overlap discovery and patch ingestion.
        # Freeze membership once; never iterate a mutating collector dictionary.
        watched = {s.upper() for s in settings.watch_assets}
        selected = {key: i for key, i in tuple(self.instruments.items()) if i.venue in allowed
                    and (settings.broad_collection_enabled or i.base.upper() in watched)}
        snapshots = tuple(self.snapshots.items())
        return {'instruments': [selected[key].model_dump() for key in sorted(selected)],
                'snapshots': [enrich(s, settings.stale_seconds) for key, s in snapshots if key in selected],
                'mode': 'live', **metadata}
