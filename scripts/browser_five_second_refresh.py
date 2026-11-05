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
from browser_process import stop_server
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
        if path == '/api/scanner' and len([r for r in state['calls'] if r['path'] == path]) > 1:
            await asyncio.sleep(.8)  # Make a background loading-layout flash observable.
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


def watch_layout(page, table_selector, busy_text):
    page.evaluate('''({selector, busyText}) => {
        const table = document.querySelector(selector);
        const root = table.closest('section');
        const initialTop = table.getBoundingClientRect().top;
        const result = {busyNotices: 0, replaced: false, largestShift: 0};
        const timer = setInterval(() => {
            result.replaced ||= !table.isConnected || document.querySelector(selector) !== table;
            result.largestShift = Math.max(result.largestShift,
                Math.abs(table.getBoundingClientRect().top - initialTop));
            if ([...root.querySelectorAll('.notice')].some(n => n.textContent.includes(busyText)))
                result.busyNotices++;
        }, 25);
        window.finishLayoutWatch = () => {clearInterval(timer); return result;};
    }''', {'selector': table_selector, 'busyText': busy_text})


def stable_layout(page):
    result = page.evaluate('window.finishLayoutWatch()')
    assert result['busyNotices'] == 0 and not result['replaced'], result
    assert result['largestShift'] <= 1, result
    return result


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
                        browser = pw.chromium.launch(executable_path=os.getenv('WATCH_MONEY_BROWSER_EXECUTABLE') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
                        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                        errors = []
                        page.on('pageerror', lambda e: errors.append(str(e)))
                        page.goto(base)
                        expect(page.get_by_role('checkbox', name='5 秒查询最新数据', exact=True)).to_be_checked()
                        expect(page.locator('.scanner-asset-table')).to_be_visible()
                        expect(page.locator('.scanner-loading')).to_have_count(0)
                        watch_layout(page, '.scanner-asset-table', '正在应用筛选')
                        page.wait_for_timeout(6200)
                        scanner_layout = stable_layout(page)
                        scans = [r for r in api.get('/__refresh_test').json()['calls'] if r['path'] == '/api/scanner']
                        intervals = [b['at']-a['at'] for a, b in zip(scans, scans[1:])]
                        assert intervals and max(intervals) >= 4, scans
                        api.post('/__refresh_test')
                        page.get_by_role('button', name='资金费', exact=True).click()
                        page.wait_for_timeout(5500)
                        mid = api.get('/__refresh_test').json()
                        assert len([r for r in mid['calls'] if r['path'] == '/api/funding']) == 1
                        expect(page.locator('tr[data-funding-asset]').first).to_be_visible(timeout=10000)
                        watch_layout(page, '.funding-table-wrap table', '正在核验当前筛选')
                        page.wait_for_timeout(5200)
                        funding_layout = stable_layout(page)
                        end = api.get('/__refresh_test').json()
                        assert len([r for r in end['calls'] if r['path'] == '/api/funding']) == 2
                        assert end['maximum'] == 1
                        assert len([r for r in end['calls'] if r['path'] == '/api/scanner']) == len(scans)
                        page.get_by_role('checkbox', name='5 秒查询最新数据', exact=True).uncheck()
                        page.wait_for_timeout(6200)
                        assert len([r for r in api.get('/__refresh_test').json()['calls'] if r['path'] == '/api/funding']) == 2
                        assert not errors, errors
                        browser.close()
                        result = {'mode': 'isolated synthetic markets; real local HTTP; funding server delayed 7s', 'passed': 7,
                            'checks': ['scanner_default_auto_query_at_5s', 'funding_slow_request_not_aborted_or_overlapped',
                                       'funding_next_query_after_completion', 'inactive_scanner_does_not_poll', 'user_can_pause_funding_queries',
                                       'scanner_background_keeps_table_and_layout', 'funding_background_keeps_table_and_layout'],
                            'scanner_layout': scanner_layout, 'funding_layout': funding_layout,
                            'scanner_periodic_interval_seconds': round(max(intervals), 3), 'maximum_funding_concurrency': end['maximum']}
                        (root/'docs/evidence/browser-five-second-refresh.json').write_text(json.dumps(result, indent=2)+'\n')
                        print(json.dumps(result, indent=2))
            finally:
                stop_server(proc)


if __name__ == '__main__':
    main()
