"""Local API and websocket application. No order placement or user exchange accounts."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .collector import Collector
from .models import Settings, now_ms
from .storage import Storage


class MappingRequest(BaseModel):
    instrument_id: str
    asset_id: str = Field(min_length=1, max_length=200)


class Favorites(BaseModel):
    instrument_ids: list[str] = Field(default_factory=list, max_length=10000)


def create_app(data_dir: str | Path | None = None, start_collector: bool | None = None,
               adapters: dict | None = None) -> FastAPI:
    data_dir = data_dir or os.getenv('WATCH_MONEY_DATA_DIR', 'data')
    store = Storage(data_dir)
    if adapters is None:
        from .adapters.cex import build_cex_adapters
        from .adapters.dex import build_dex_adapters
        adapters = {**build_cex_adapters(), **build_dex_adapters()}
    from .monitors import MonitorService
    from .monitors import router as monitors_router
    from .notifications import NotificationService
    from .notifications import router as notifications_router
    from .strategies import router as opportunities_router
    notifications = NotificationService(data_dir)
    notifications.set_enabled(store.settings().notifications_enabled)
    collector = Collector(store, adapters, notifications)
    monitors = MonitorService(store, collector, notifications)
    run_collectors = start_collector if start_collector is not None else os.getenv('WATCH_MONEY_COLLECT', '1') == '1'

    @asynccontextmanager
    async def lifespan(app):
        await notifications.start()
        if run_collectors:
            await collector.start()
        await monitors.start()
        try:
            yield
        finally:
            await monitors.stop()
            await collector.stop()
            await notifications.stop()

    app = FastAPI(title='Watch Money', version='0.1.0', lifespan=lifespan)
    app.state.store = store
    app.state.collector = collector
    app.state.notifications = notifications
    app.state.data_dir = Path(data_dir)
    app.state.monitors = monitors
    app.include_router(opportunities_router)
    app.include_router(notifications_router)
    app.include_router(monitors_router)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': '0.1.0', 'at': now_ms(), 'mode': 'demo' if store.settings().demo_mode else 'live'}

    @app.get('/api/markets')
    def markets():
        return collector.markets()

    @app.get('/api/status')
    def status():
        from .adapters.cex import coverage as cex_coverage
        from .adapters.dex import coverage as dex_coverage
        return {'venues': [s.model_dump() for s in collector.statuses.values()],
                'coverage': [c.model_dump() for c in [*cex_coverage(), *dex_coverage()]],
                'storage': store.usage()}

    @app.get('/api/settings', response_model=Settings)
    def settings():
        return store.settings()

    @app.put('/api/settings', response_model=Settings)
    def save_settings(settings: Settings):
        store.set('settings', settings.model_dump())
        notifications.set_enabled(settings.notifications_enabled)
        return settings

    @app.get('/api/favorites')
    def favorites():
        return {'instrument_ids': store.get('favorites', [])}

    @app.put('/api/favorites')
    def save_favorites(value: Favorites):
        store.set('favorites', list(dict.fromkeys(value.instrument_ids)))
        return favorites()

    @app.get('/api/assets')
    def assets():
        grouped = {}
        for instrument in collector.instruments.values():
            item = grouped.setdefault(instrument.asset_id, {'id': instrument.asset_id, 'symbol': instrument.base,
                                                            'verified': instrument.mapping_verified, 'instruments': []})
            item['instruments'].append(instrument.id)
        return {'assets': list(grouped.values()), 'audit': store.audit()}

    @app.get('/api/pools')
    def pools():
        return {'pools': [p for venue in collector.adapters for p in store.get(f'pools:{venue}', [])]}

    @app.put('/api/assets/mappings')
    def mapping(value: MappingRequest):
        if value.instrument_id not in collector.instruments:
            raise HTTPException(404, 'Instrument not found')
        store.map_asset(value.instrument_id, value.asset_id)
        collector.instruments = store.instruments()
        return {'instrument': collector.instruments[value.instrument_id], 'audit': store.audit()}

    @app.get('/api/history')
    async def history(instrument_id: str, limit: int = Query(500, ge=1, le=10000), before: int | None = None):
        records = await asyncio.to_thread(store.history, instrument_id, limit, before)
        return {'snapshots': records}

    @app.get('/api/orderbook/{instrument_id:path}')
    async def orderbook(instrument_id: str):
        instrument = collector.instruments.get(instrument_id)
        if not instrument:
            raise HTTPException(404, 'Instrument not found; illustrative demo has no executable depth')
        adapter = collector.adapters.get(instrument.venue)
        if adapter is None:
            raise HTTPException(501, 'No order book adapter configured')
        try:
            return await asyncio.wait_for(adapter.orderbook(instrument), timeout=30)
        except NotImplementedError:
            raise HTTPException(501, 'This market does not provide an order book; a size-specific route quote is required')
        except Exception as exc:
            raise HTTPException(502, f'Order book unavailable: {type(exc).__name__}')

    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_json({'type': 'markets', **collector.markets()})
                await asyncio.sleep(store.settings().refresh_ms / 1000)
        except (WebSocketDisconnect, RuntimeError, OSError):
            return

    # An editable checkout has backend/watch_money/app.py; installed deployments may override.
    frontend = Path(os.getenv('WATCH_MONEY_FRONTEND_DIR', str(Path(__file__).resolve().parents[2] / 'frontend' / 'dist')))

    @app.get('/{path:path}', include_in_schema=False)
    def static(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, 'API route not found')
        resolved = (frontend / path).resolve()
        if resolved.is_relative_to(frontend.resolve()) and resolved.is_file():
            return FileResponse(resolved)
        index = frontend / 'index.html'
        if index.exists():
            return FileResponse(index)
        return {'message': 'Build frontend with npm run build in frontend/, then open this address again.', 'api': '/docs'}

    return app


app = create_app()
