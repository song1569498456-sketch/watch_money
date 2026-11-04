"""Browser acceptance against an isolated temporary server; never touches user's data."""
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

root = Path(__file__).resolve().parents[1]
output = root / 'docs' / 'evidence'
output.mkdir(parents=True, exist_ok=True)
screenshots = root / 'docs' / 'screenshots'
screenshots.mkdir(exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
base = f'http://127.0.0.1:{port}'
checks = []
with tempfile.TemporaryDirectory(prefix='watch-money-browser-') as data:
    env = {**os.environ, 'WATCH_MONEY_DATA_DIR': data, 'WATCH_MONEY_COLLECT': '0'}
    # Mock/local channel tests only, even if the caller has private credentials.
    for key in ('FEISHU_WEBHOOK_URL', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
        env.pop(key, None)
    with (Path(data) / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'watch_money.app:app', '--host',
                                   '127.0.0.1', '--port', str(port)], cwd=root, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=base, trust_env=False, timeout=5) as api:
                for _ in range(100):
                    try:
                        if api.get('/api/health').is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    if server.poll() is not None:
                        raise RuntimeError('Temporary server failed to start')
                    time.sleep(.2)
                else:
                    raise RuntimeError('Temporary server readiness timeout')
                with sync_playwright() as p:
                    browser = p.chromium.launch(executable_path=shutil.which('chromium'), headless=True,
                                                args=['--no-sandbox'])
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    errors = []
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.goto(base)
                    expect(page.get_by_text('等待公开行情', exact=True)).to_be_visible()
                    page.screenshot(path=str(screenshots / 'live-empty.png'), full_page=True)
                    checks.append('live default has explicit empty state, zero fabricated snapshots')
                    for label in ('平台状态', '通知中心', '收益计算', '配置'):
                        page.get_by_role('button', name=label, exact=True).click()
                        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                        if label == '平台状态':
                            page.screenshot(path=str(screenshots / 'coverage.png'), full_page=True)
                    page.get_by_role('switch', name='演示模式', exact=False).check()
                    page.get_by_role('button', name='保存配置', exact=True).click()
                    expect(page.get_by_text('演示模式已显式开启。', exact=False)).to_be_visible()
                    page.get_by_role('button', name='市场总览', exact=False).first.click()
                    page.get_by_role('button', name='展开 BTC 市场').click()
                    with page.expect_response(lambda response: response.url.endswith('/api/favorites')
                                              and response.request.method == 'PUT') as favorite_saved:
                        page.get_by_role('button', name='收藏 BTC/USDT', exact=True).first.click()
                    assert favorite_saved.value.ok
                    assert len(api.get('/api/favorites').json()['instrument_ids']) == 1
                    page.get_by_label('搜索资产或市场').fill('BTC')
                    assert page.get_by_role('button', name='查看 ETH 详情').count() == 0
                    page.get_by_label('搜索资产或市场').fill('')
                    page.screenshot(path=str(screenshots / 'demo-overview.png'), full_page=True)
                    page.get_by_role('button', name='查看 BTC 详情').click()
                    expect(page.get_by_text('原始资金费率', exact=True)).to_be_visible()
                    page.screenshot(path=str(screenshots / 'demo-detail.png'), full_page=True)
                    checks.append('explicit demo toggle, search, favorite persistence, spot/perp detail')
                    page.get_by_role('button', name='收益计算', exact=True).click()
                    page.get_by_label('策略', exact=False).select_option('spot_perp_basis')
                    page.get_by_label('买入 / 做多市场', exact=False).select_option('demo-alpha:BTC:USDT:spot')
                    page.get_by_label('卖出 / 做空市场', exact=False).select_option('demo-beta:BTC:USDT:perpetual')
                    page.get_by_role('button', name='计算收益情景', exact=True).click()
                    expect(page.get_by_role('heading', name='计算结果', exact=True)).to_be_visible()
                    page.get_by_label('保存样本名称').fill('Browser demo evidence')
                    page.get_by_role('button', name='保存样本', exact=True).click()
                    expect(page.get_by_text('Browser demo evidence', exact=True).first).to_be_visible()
                    saved = api.get('/api/opportunities/saved').json()['samples']
                    assert len(saved) == 1
                    assert saved[0]['result']['opportunities'][0]['net_profit'] is None
                    page.get_by_role('button', name='持续监控此参数', exact=True).click()
                    expect(page.get_by_role('button', name='删除持续监控 Browser demo evidence')).to_be_visible()
                    assert len(api.get('/api/monitors').json()['monitors']) == 1
                    page.get_by_role('button', name='回放样本', exact=True).click()
                    expect(page.get_by_text('已按保存样本回放', exact=True)).to_be_visible()
                    page.screenshot(path=str(screenshots / 'calculator.png'), full_page=True)
                    checks.append('calculator missing executable inputs stays net=null; save, replay, monitor creation')
                    page.get_by_role('button', name='通知中心', exact=True).click()
                    page.get_by_role('button', name='发送测试', exact=True).click()
                    for _ in range(50):
                        history = api.get('/api/notifications/history').json()
                        if history['deliveries'] and history['deliveries'][0]['status'] == 'sent':
                            break
                        time.sleep(.1)
                    assert history['deliveries'][0]['channel'] == 'local'
                    assert history['deliveries'][0]['status'] == 'sent'
                    page.get_by_role('button', name='刷新通知记录').click()
                    expect(page.get_by_text('local · 已发送', exact=False)).to_be_visible()
                    page.screenshot(path=str(screenshots / 'notifications.png'), full_page=True)
                    checks.append('local test message queued, persisted and sent; no external channels')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.get_by_role('button', name='市场总览', exact=False).first.click()
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                    page.screenshot(path=str(screenshots / 'mobile-demo.png'), full_page=True)
                    checks.append('390px mobile layout has no document horizontal overflow')
                    assert not errors, errors
                    checks.append('zero browser JavaScript errors across tested pages')
                    browser.close()
        finally:
            server.terminate()
            server.wait(timeout=15)
(output / 'browser-smoke.json').write_text(json.dumps({'passed': True, 'checks': checks,
                                                    'data_mode': 'explicit illustrative demo + local test only'}, indent=2), encoding='utf-8')
print(json.dumps({'passed': True, 'checks': checks}, indent=2))
