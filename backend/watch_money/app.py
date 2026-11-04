"""Local API and websocket application. No order placement or user exchange accounts."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .collector import Collector, sanitize_legacy_volume
from .models import MarketSnapshot, Settings, now_ms
from .storage import Storage


class MappingRequest(BaseModel):
    instrument_id: str
    asset_id: str = Field(min_length=1, max_length=200)


class MappingBatchRequest(BaseModel):
    instrument_ids: list[str] = Field(min_length=1, max_length=1000)
    asset_id: str = Field(min_length=1, max_length=200)


class PoolReadRequest(BaseModel):
    protocol: str = Field(min_length=1, max_length=50)
    chain: str = Field(min_length=1, max_length=50)
    version: str = Field(min_length=1, max_length=30)
    pool_address: str = Field(pattern=r'^0x[0-9a-fA-F]{40}$')
    block: Literal['latest', 'safe', 'finalized'] | int = 'latest'


class PoolQuoteRequest(PoolReadRequest):
    token_in: str = Field(pattern=r'^0x[0-9a-fA-F]{40}$')
    amount_in: str = Field(min_length=1, max_length=1000)


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
    from .scanner import router as scanner_router
    from .funding_view import router as funding_router
    from .funding_history import FundingHistoryService
    from .funding_history import router as funding_history_router
    from .fee_profiles import router as fees_router
    from .asset_verification import AssetVerificationService, router as asset_verification_router
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
            await app.state.asset_verification.close()
            await collector.stop()
            await notifications.stop()

    app = FastAPI(title='Watch Money', version='0.1.0', lifespan=lifespan)
    app.state.store = store
    app.state.collector = collector
    app.state.notifications = notifications
    app.state.data_dir = Path(data_dir)
    app.state.monitors = monitors
    app.state.funding_history = FundingHistoryService(data_dir, collector)
    app.include_router(opportunities_router)
    app.include_router(notifications_router)
    app.include_router(monitors_router)
    app.include_router(scanner_router)
    app.include_router(funding_router)
    app.include_router(funding_history_router)
    app.include_router(fees_router)
    app.state.asset_verification = AssetVerificationService(store, collector)
    app.include_router(asset_verification_router)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': '0.1.0', 'at': now_ms(), 'mode': 'demo' if store.settings().demo_mode else 'live'}

    @app.get('/api/markets')
    def markets():
        # This sync endpoint runs in a worker. Render here rather than returning
        # a large dict for FastAPI to recursively encode on the event loop.
        return JSONResponse(collector.markets())

    @app.get('/api/status')
    def status():
        collector.refresh_status_coverage()
        from .adapters.cex import coverage as cex_coverage
        from .adapters.dex import coverage as dex_coverage
        return {'venues': [s.model_dump() for s in collector.statuses.values()],
                'coverage': [c.model_dump() for c in [*cex_coverage(), *dex_coverage()]],
                'storage': store.usage(), 'recording': collector.recorder.status()}

    @app.get('/api/settings', response_model=Settings)
    def settings():
        return store.settings()

    @app.put('/api/settings', response_model=Settings)
    def save_settings(settings: Settings):
        store.set('settings', settings.model_dump())
        notifications.set_enabled(settings.notifications_enabled)
        return settings

    @app.get('/api/recording')
    def recording_status():
        return {**collector.recorder.status(), 'requested_enabled': store.settings().recording_enabled}

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

    @app.get('/api/assets/candidates')
    def identity_candidates():
        from .identity import identity_candidates
        return {'candidates': identity_candidates(list(collector.instruments.values()))}

    @app.get('/api/pools')
    def pools():
        return {'pools': [p for venue in collector.adapters for p in store.get(f'pools:{venue}', [])]}

    async def native_pool(payload, quote=False):
        from .adapters.onchain import NativePoolReader, UnsupportedPool
        settings = store.settings()
        if payload.chain not in settings.rpc_urls:
            raise HTTPException(422, 'Configure an RPC URL for this chain in settings')
        reader = NativePoolReader(settings.rpc_urls)
        try:
            args = (payload.protocol, payload.chain, payload.version, payload.pool_address)
            operation = (reader.quote_exact_input(*args, payload.token_in, payload.amount_in, block=payload.block)
                         if quote else reader.read_pool(*args, block=payload.block))
            result = await asyncio.wait_for(operation, timeout=60)
            store.set(f'native_pool:{payload.chain}:{payload.pool_address.lower()}',
                      (result.pool if quote else result).model_dump(mode='json'))
            return result
        except UnsupportedPool as exc:
            raise HTTPException(422, str(exc)) from None
        except ValueError:
            raise HTTPException(422, 'Invalid pool or exact input amount') from None
        except Exception as exc:
            raise HTTPException(502, f'Native pool read unavailable: {type(exc).__name__}') from None
        finally:
            await reader.close()

    @app.post('/api/pools/native/state')
    async def pool_state(payload: PoolReadRequest):
        return await native_pool(payload)

    @app.post('/api/pools/native/quote')
    async def pool_quote(payload: PoolQuoteRequest):
        return await native_pool(payload, quote=True)

    @app.put('/api/assets/mappings')
    def mapping(value: MappingRequest):
        if value.instrument_id not in collector.instruments:
            raise HTTPException(404, 'Instrument not found')
        store.map_asset(value.instrument_id, value.asset_id)
        collector.instruments = store.instruments()
        return {'instrument': collector.instruments[value.instrument_id], 'audit': store.audit()}

    @app.put('/api/assets/mappings/batch')
    def mapping_batch(value: MappingBatchRequest):
        identities = list(dict.fromkeys(value.instrument_ids))
        if any(identity not in collector.instruments for identity in identities):
            raise HTTPException(404, 'One or more instruments were not found; no mappings changed')
        store.map_assets(identities, value.asset_id)
        collector.instruments = store.instruments()
        return {'instruments': [collector.instruments[i] for i in identities], 'audit': store.audit()}

    @app.get('/api/history')
    async def history(instrument_id: str, limit: int = Query(500, ge=1, le=10000), before: int | None = None):
        records = await asyncio.to_thread(store.history, instrument_id, limit, before)
        instrument = collector.instruments.get(instrument_id)
        return {'snapshots': [sanitize_legacy_volume(MarketSnapshot.model_validate(row), instrument).model_dump()
                              for row in records]}

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
        from .market_stream import market_delta
        previous = None

        def build_frame(previous):
            current = collector.markets()
            return current, json.dumps(market_delta(previous, current), ensure_ascii=False,
                                       allow_nan=False, separators=(',', ':'))

        try:
            while True:
                current, frame = await asyncio.to_thread(build_frame, previous)
                await ws.send_text(frame)
                previous = current
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
