"""Real local query cadence over synthetic markets; no exchange requests."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from browser_followup import seed
from playwright.sync_api import expect, sync_playwright

SERVER = '''
import asyncio, sys, time, uvicorn
from watch_money.app import create_app
app = create_app(sys.argv[1], start_collector=False)
state = {'calls': [], 'delay': False, 'active': 0, 'maximum': 0}
@app.middleware('http')
async def measure(request, call_next):
    path = request.url.path
    tracked = path in ('/api/scanner', '/api/funding')
    if tracked:
        state['calls'].append({'path': path, 'at': time.monotonic()})
    if path == '/api/funding':
        state['active'] += 1
        state['maximum'] = max(state['maximum'], state['active'])
    try:
        if path == '/api/funding' and state['delay']:
            await asyncio.sleep(7)
        return await call_next(request)
    finally:
        if path == '/api/funding': state['active'] -= 1
@app.get('/__refresh_test')
def metrics(): return state
@app.post('/__refresh_test')
def slow():
    state['delay'] = True
    return state
# The application SPA fallback precedes routes added by this isolated fixture.
app.router.routes.insert(0, app.router.routes.pop(-2))
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')
'''


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='five-second-refresh-') as directory:
        seed(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'WATCH_MONEY_COLLECT': '0'}
        with (Path(directory)/'server.log').open('w') as log:
            proc = subprocess.Popen([sys.executable, '-c', SERVER, directory, str(port)], cwd=root, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=base, trust_env=False, timeout=10) as api:
                    for _ in range(100):
                        try:
                            if api.get('/api/health').is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    with sync_playwright() as pw:
                        browser = pw.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
                        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                        errors = []
                        page.on('pageerror', lambda e: errors.append(str(e)))
                        page.goto(base)
                        expect(page.get_by_role('checkbox', name='5 秒查询最新数据', exact=True)).to_be_checked()
                        page.wait_for_timeout(6200)
                        scans = [r for r in api.get('/__refresh_test').json()['calls'] if r['path'] == '/api/scanner']
                        intervals = [b['at']-a['at'] for a, b in zip(scans, scans[1:])]
                        assert intervals and max(intervals) >= 4, scans
                        api.post('/__refresh_test')
                        page.get_by_role('button', name='资金费', exact=True).click()
                        page.wait_for_timeout(5500)
                        mid = api.get('/__refresh_test').json()
                        assert len([r for r in mid['calls'] if r['path'] == '/api/funding']) == 1
                        page.wait_for_timeout(5200)
                        end = api.get('/__refresh_test').json()
                        assert len([r for r in end['calls'] if r['path'] == '/api/funding']) == 2
                        assert end['maximum'] == 1
                        assert len([r for r in end['calls'] if r['path'] == '/api/scanner']) == len(scans)
                        page.get_by_role('checkbox', name='5 秒查询最新数据', exact=True).uncheck()
                        page.wait_for_timeout(6200)
                        assert len([r for r in api.get('/__refresh_test').json()['calls'] if r['path'] == '/api/funding']) == 2
                        assert not errors, errors
                        browser.close()
                        result = {'mode': 'isolated synthetic markets; real local HTTP; funding server delayed 7s', 'passed': 5,
                            'checks': ['scanner_default_auto_query_at_5s', 'funding_slow_request_not_aborted_or_overlapped',
                                       'funding_next_query_after_completion', 'inactive_scanner_does_not_poll', 'user_can_pause_funding_queries'],
                            'scanner_periodic_interval_seconds': round(max(intervals), 3), 'maximum_funding_concurrency': end['maximum']}
                        (root/'docs/evidence/browser-five-second-refresh.json').write_text(json.dumps(result, indent=2)+'\n')
                        print(json.dumps(result, indent=2))
            finally:
                proc.terminate()
                proc.wait(timeout=10)


if __name__ == '__main__':
    main()
