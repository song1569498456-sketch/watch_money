"""Large responses render away from the ingestion event loop, preserving JSON."""
import asyncio
import json
import threading

import httpx
import pytest
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from watch_money.app import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/markets', '/api/scanner', '/api/funding'])
async def test_large_http_response_render_is_in_worker(tmp_path, monkeypatch, path):
    app = create_app(tmp_path, start_collector=False, adapters={})
    loop_thread = threading.get_ident()
    rendered = []
    render = JSONResponse.render

    def checked_render(self, content):
        rendered.append(threading.get_ident())
        return render(self, content)

    monkeypatch.setattr(JSONResponse, 'render', checked_render)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert isinstance(response.json(), dict)
    assert rendered and all(identity != loop_thread for identity in rendered)
    await app.state.asset_verification.close()
    await app.state.query_work.close()


@pytest.mark.asyncio
async def test_browser_ws_builds_in_worker_sends_on_loop_and_preserves_delta(tmp_path, monkeypatch):
    app = create_app(tmp_path, start_collector=False, adapters={})
    loop_thread = threading.get_ident()
    calls, frames = [], []
    store = app.state.store
    settings = store.settings()
    settings.refresh_ms = 1  # This test bypasses validation only to avoid a UI refresh wait.
    monkeypatch.setattr(store, 'settings', lambda: settings)

    def markets():
        calls.append(threading.get_ident())
        return {'mode': 'live', 'instruments': [{'id': 'x', 'base': '币'}],
                'snapshots': [{'instrument_id': 'x', 'bid': 1, 'stale': len(calls) > 1}]}

    monkeypatch.setattr(app.state.collector, 'markets', markets)

    class Socket:
        async def receive(self):
            await asyncio.Event().wait()

        async def accept(self):
            assert threading.get_ident() == loop_thread

        async def send_text(self, text):
            assert threading.get_ident() == loop_thread
            frames.append(json.loads(text))
            if len(frames) == 2:
                raise WebSocketDisconnect()

    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', None) == '/ws')
    await asyncio.wait_for(endpoint(Socket()), 3)
    assert all(identity != loop_thread for identity in calls)
    assert frames[0]['type'] == 'markets'
    assert frames[0]['instruments'][0]['base'] == '币'
    assert frames[1]['type'] == 'markets_delta'
    assert frames[1]['instruments'] == []
    assert frames[1]['snapshots'][0]['stale'] is True
    await app.state.asset_verification.close()
    await app.state.query_work.close()
