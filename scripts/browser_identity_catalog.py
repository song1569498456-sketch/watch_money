"""Isolated UI acceptance: persisted synthetic catalog, mocked refresh, no external API."""
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
from playwright.sync_api import expect, sync_playwright
from watch_money.models import now_ms
from watch_money.storage import Storage


def main():
    root = Path(__file__).resolve().parents[1]
    checks = []
    with tempfile.TemporaryDirectory(prefix='identity-catalog-browser-') as directory:
        store = Storage(directory)
        store.set('settings', store.settings().model_copy(update={'collection_enabled': False, 'notifications_enabled': False}).model_dump())
        rows = [{'instrument_id': f'fixture:{n}', 'venue': 'gate', 'symbol': f'SMALL{n}/USDT', 'kind': 'spot',
                 'change': 'new' if n < 51 else 'unchanged', 'coin_ids': [f'fixture-{n}'], 'contracts': [],
                 'official_contracts': [], 'reasons': ['official_contract_not_corroborated'], 'source_at': now_ms()} for n in range(52)]
        store.set('identity_catalog', {'at': now_ms(), 'rows': rows, 'provider': {'error': 'demo_key_required', 'complete': False, 'sources': {}}, 'next_update_at': now_ms()+86400000})
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'WATCH_MONEY_DATA_DIR': directory, 'WATCH_MONEY_COLLECT': '0', 'COINGECKO_DEMO_API_KEY': ''}
        for name in ['FEISHU_WEBHOOK_URL', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']:
            env.pop(name, None)
        with (Path(directory)/'server.log').open('w') as log:
            proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'watch_money.app:app', '--host', '127.0.0.1', '--port', str(port)], cwd=root, env=env, stdout=log, stderr=log)
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
                        errors, external = [], []
                        page.on('pageerror', lambda e: errors.append(str(e)))
                        page.on('request', lambda r: external.append(r.url) if r.url.startswith('http') and not r.url.startswith(base) else None)
                        page.route('**/api/assets/catalog/refresh', lambda route: route.fulfill(status=200, content_type='application/json', body='{}'))
                        page.goto(base)
                        page.get_by_role('button', name='设置', exact=True).click()
                        page.get_by_text('资产身份与映射管理', exact=True).click()
                        panel = page.get_by_role('region', name='币种目录持续更新', exact=True)
                        expect(panel).to_contain_text('未配置 CoinGecko Demo key')
                        expect(panel).to_contain_text('当前筛选 51 项')
                        expect(panel.locator('tbody tr')).to_have_count(50)
                        checks.append('missing_key_and_candidate_not_verified_coverage_visible')
                        panel.get_by_role('button', name='下一页目录', exact=True).click()
                        expect(panel.locator('tbody tr')).to_have_count(1)
                        panel.get_by_role('checkbox', name='只显示待处理项目').uncheck()
                        expect(panel.locator('tbody tr')).to_have_count(50)
                        expect(panel).to_contain_text('当前筛选 52 项')
                        checks.append('complete_paging_and_filter_resets_page')
                        panel.get_by_label('每天自动更新身份目录').check()
                        expect(panel.get_by_label('每天自动更新身份目录')).to_be_checked()
                        assert api.get('/api/assets/catalog').json()['enabled']
                        page.reload()
                        page.get_by_role('button', name='设置', exact=True).click()
                        page.get_by_text('资产身份与映射管理', exact=True).click()
                        expect(panel.get_by_label('每天自动更新身份目录')).to_be_checked()
                        checks.append('automatic_preference_persists_without_account_or_network_changes')
                        panel.get_by_role('button', name='更新并保存目录候选', exact=True).click()
                        expect(panel.get_by_role('button', name='更新并保存目录候选', exact=True)).to_be_enabled()
                        checks.append('mocked_refresh_returns_to_saved_candidate_review')
                        page.set_viewport_size({'width': 390, 'height': 844})
                        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                        panel.screenshot(path=str(root/'docs/screenshots/identity-catalog-synthetic.png'))
                        assert not errors and not external, (errors, external)
                        checks.append('mobile_no_overflow_no_js_errors_no_external_requests')
                        browser.close()
            finally:
                proc.terminate()
                proc.wait(timeout=10)
    result = {'mode': 'isolated synthetic persisted catalog; refresh browser-mocked; no provider request', 'passed': len(checks), 'checks': checks}
    (root/'docs/evidence/browser-identity-catalog.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
