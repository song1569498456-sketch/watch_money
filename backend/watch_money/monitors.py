"""Persisted calculation plans refreshed with current executable market inputs."""
import asyncio
from contextlib import suppress
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from .models import Model, now_ms
from .strategies import CalculateRequest, calculate


class Monitor(Model):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    interval_seconds: float = Field(default=30, ge=10, le=86400)
    calculation: CalculateRequest


class MonitorService:
    def __init__(self, store, collector, notifications):
        self.store, self.collector, self.notifications = store, collector, notifications
        self.task = None
        self.stop_event = asyncio.Event()
        self.next_run = {}

    def list(self):
        return {'monitors': self.store.get('monitors', []), 'results': self.store.get('monitor_results', [])}

    def save(self, monitor):
        with self.store.lock:
            records = [m for m in self.store.get('monitors', []) if m['id'] != monitor.id]
            if len(records) >= 100:
                raise ValueError('At most 100 monitors are supported')
            self.store.set('monitors', records + [monitor.model_dump()])
            self.next_run.pop(monitor.id, None)

    def delete(self, identity):
        with self.store.lock:
            self.store.set('monitors', [m for m in self.store.get('monitors', []) if m['id'] != identity])
            self.store.set('monitor_results', [r for r in self.store.get('monitor_results', []) if r['monitor_id'] != identity])

    async def start(self):
        if self.task and not self.task.done():
            return
        self.stop_event.clear()
        self.task = asyncio.create_task(self.run(), name='opportunity-monitors')

    async def stop(self):
        self.stop_event.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def tick(self):
        settings = self.store.settings()
        if not settings.computation_enabled or settings.demo_mode:
            return
        for raw in self.store.get('monitors', []):
            monitor = Monitor.model_validate(raw)
            if not monitor.enabled or self.next_run.get(monitor.id, 0) > now_ms():
                continue
            self.next_run[monitor.id] = now_ms() + monitor.interval_seconds * 1000
            req = monitor.calculation.model_copy(deep=True)
            ids = {req.buy_instrument_id, req.sell_instrument_id}
            req.instruments = [self.collector.instruments[i] for i in ids if i in self.collector.instruments]
            req.snapshots = [self.collector.snapshots[i] for i in ids if i in self.collector.snapshots]
            # Saved quotes and settled history cannot be refreshed by changing their timestamps.
            req.orderbooks, req.route_quotes, req.funding_events = [], [], []
            req.funding_schedule_complete_until = {}
            errors = []
            for inst in req.instruments:
                adapter = self.collector.adapters.get(inst.venue)
                if inst.venue not in settings.enabled_venues or adapter is None:
                    errors.append(f'{inst.venue}: disabled or unavailable')
                    continue
                if inst.mechanism != 'orderbook':
                    errors.append(f'{inst.id}: live size-specific route provider not configured')
                    continue
                try:
                    book = await asyncio.wait_for(adapter.orderbook(inst), timeout=20)
                    if book.instrument_id != inst.id:
                        raise ValueError('Unexpected order book identity')
                    req.orderbooks.append(book)
                except Exception as exc:
                    errors.append(f'{inst.id}: {type(exc).__name__}')
            req.as_of = now_ms()
            with self.store.lock:
                current = next((m for m in self.store.get('monitors', []) if m['id'] == monitor.id), None)
                if current != raw:
                    continue
                result = calculate(req)
                record = {'monitor_id': monitor.id, 'name': monitor.name, 'at': now_ms(), 'errors': errors, **result}
                records = self.store.get('monitor_results', [])
                previous = next((r for r in records if r['monitor_id'] == monitor.id), None)
                self.store.set('monitor_results', [r for r in records if r['monitor_id'] != monitor.id] + [record])
                self.store.set('monitor_error', None)
            if settings.notifications_enabled:
                from .strategies import opportunity_event
                for opportunity in result['opportunities']:
                    await self.notifications.evaluate(opportunity_event(opportunity, req))
                    old = next((o for o in (previous or {}).get('opportunities', []) if o['id'] == opportunity['id']), None)
                    if old and old.get('net_profit') is not None and opportunity.get('net_profit') is None:
                        event = opportunity_event(opportunity, req)
                        event.update(type='opportunity_invalidated', source_at=now_ms(),
                                     message='Monitored opportunity no longer has complete executable inputs')
                        await self.notifications.evaluate(event)

    async def run(self):
        while not self.stop_event.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.set('monitor_error', {'at': now_ms(), 'error': type(exc).__name__})
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=1)


router = APIRouter(prefix='/api/monitors', tags=['monitors'])


@router.get('')
def list_monitors(request: Request):
    return {**request.app.state.monitors.list(), 'error': request.app.state.store.get('monitor_error')}


@router.post('')
def save_monitor(payload: Monitor, request: Request):
    try:
        request.app.state.monitors.save(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return payload


@router.delete('/{identity}')
def delete_monitor(identity: str, request: Request):
    request.app.state.monitors.delete(identity)
    return {'deleted': identity}
