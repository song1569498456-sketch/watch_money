"""Isolated live-query pagination UI with synthetic small-asset catalog; no exchange traffic."""
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
from watch_money.models import Instrument, MarketSnapshot, now_ms
from watch_money.storage import Storage


def main():
    root = Path(__file__).resolve().parents[1]
    checks = []
    with tempfile.TemporaryDirectory(prefix='scanner-pages-') as directory:
        store = Storage(directory)
        store.set('settings', store.settings().model_copy(update={'collection_enabled': False,
            'notifications_enabled': False, 'demo_mode': False, 'stale_seconds': 600,
            'enabled_venues': ['okx', 'bybit']}).model_dump())
        stamp = now_ms()
        items, snapshots = [], []
        for n in range(1005):
            for venue in ['okx', 'bybit']:
                base = f'SMALL{n:04}'
                item = Instrument(id=f'page-fixture:{base}:{venue}', venue=venue, base=base,
                    quote='USDT', kind='spot', native_symbol=base+'/USDT',
                    asset_id='confirmed:'+base, mapping_verified=True)
                items.append(item)
                snapshots.append(MarketSnapshot(instrument_id=item.id, venue=venue, bid=100, ask=101,
                    source='synthetic:pagination', source_at=stamp, received_at=stamp, is_demo=False))
        store.save_instruments(items)
        store.save_snapshots(snapshots)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base_url = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'WATCH_MONEY_DATA_DIR': directory, 'WATCH_MONEY_COLLECT': '0'}
        for key in ['FEISHU_WEBHOOK_URL', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']:
            env.pop(key, None)
        with open(Path(directory)/'server.log', 'w') as log:
            proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'watch_money.app:app', '--host',
                '127.0.0.1', '--port', str(port)], cwd=root, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=base_url, trust_env=False) as client:
                    for _ in range(100):
                        try:
                            if client.get('/api/health').is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    else:
                        raise RuntimeError('Temporary server unavailable')
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(executable_path=shutil.which('chromium'), headless=True,
                                                args=['--no-sandbox'])
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base_url)
                    pager = page.get_by_role('navigation', name='资产分页', exact=True)
                    expect(pager).to_contain_text('资产 1–100 / 1,005')
                    expect(page.locator('.scanner-asset-table > tbody > tr')).to_have_count(100)
                    checks.append('first_page_has_100_of_1005_groups')
                    for n in range(1, 11):
                        pager.get_by_role('button', name='下一页资产', exact=True).click()
                        expect(pager).to_contain_text(f'资产 {n*100+1}–{min(n*100+100,1005)} / 1,005')
                    expect(page.locator('.scanner-asset-table > tbody > tr')).to_have_count(5)
                    expect(pager.get_by_role('button', name='下一页资产')).to_be_disabled()
                    checks.append('last_page_beyond_1000_is_reachable_and_bounded')
                    nav = page.get_by_role('navigation', name='主导航', exact=True)
                    nav.get_by_role('button', name='我的监控', exact=True).click()
                    nav.get_by_role('button', name='市场监控', exact=True).click()
                    expect(pager).to_contain_text('资产 1001–1005 / 1,005')
                    checks.append('return_preserves_page_and_revalidates')
                    search = page.get_by_role('textbox', name='搜索扫描资产或原生市场')
                    search.fill('SMALL1004')
                    expect(pager).to_contain_text('资产 1–1 / 1')
                    checks.append('search_resets_offset_before_query')
                    search.fill('')
                    expect(pager).to_contain_text('资产 1–100 / 1,005')
                    pager.get_by_role('button', name='下一页资产').click()
                    expect(pager).to_contain_text('资产 101–200 / 1,005')
                    page.get_by_role('combobox', name='扫描市场类型').select_option('perpetual')
                    expect(pager).to_contain_text('资产 0–0 / 0')
                    expect(pager.get_by_role('button', name='首页', exact=True)).to_be_disabled()
                    checks.append('type_filter_resets_offset_and_reports_empty_scope')
                    page.get_by_role('combobox', name='扫描市场类型').select_option('all')
                    expect(pager).to_contain_text('资产 1–100 / 1,005')
                    expect(pager).to_contain_text('不能视为同一时点的完整快照')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
                    checks.append('mobile_no_page_overflow_and_live_query_warning_visible')
                    pager.scroll_into_view_if_needed()
                    out = root/'docs/screenshots/scanner-pagination-synthetic.png'
                    out.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out))
                    assert not errors, errors
                    checks.append('no_browser_runtime_errors')
                    browser.close()
            finally:
                proc.terminate()
                proc.wait(timeout=10)
    evidence = root/'docs/evidence/browser-scanner-pagination.json'
    evidence.write_text(json.dumps({'kind': 'synthetic_isolated_no_exchange_requests', 'checks': checks,
                                   'passed': len(checks), 'catalog_groups': 1005}, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'passed': len(checks), 'evidence': str(evidence)}))


if __name__ == '__main__':
    main()
