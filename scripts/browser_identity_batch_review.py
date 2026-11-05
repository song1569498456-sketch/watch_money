"""Temporary DB + mocked official schemas. Never approve production mappings."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from playwright.sync_api import expect, sync_playwright
from watch_money.asset_verification import AssetVerificationService, EvidenceInput, _digest
from watch_money.models import Instrument, now_ms
from watch_money.storage import Storage


async def seed(directory):
    store = Storage(directory)
    store.set('settings', store.settings().model_copy(update={'collection_enabled':False,
        'notifications_enabled':False, 'demo_mode':False, 'enabled_venues':['gate','bitget']}).model_dump())
    items, gate, bitget = [], [], []
    for n in range(30):
        symbol, address = f'SMALL{n:02}', '0x'+f'{n+1:040x}'
        for venue in ['gate', 'bitget']:
            native = f'cex:{venue}:{venue}:{symbol}'
            items.append(Instrument(id=f'batch-fixture:{venue}:{symbol}', venue=venue, base=symbol, quote='USDT',
                kind='spot', native_symbol=symbol+'/USDT', asset_id=native, native_asset_id=native,
                native_currency_id=symbol, mapping_verified=False))
        gate.append({'currency':symbol,'chain':'ETH','chains':[{'name':'ETH','addr':address}]})
        bitget.append({'coin':symbol,'chains':[{'chain':'ERC20','contractAddress':address}]})
    store.save_instruments(items)
    def transport(request):
        assert request.method == 'GET' and 'authorization' not in request.headers
        return httpx.Response(200, json=gate if request.url.host == 'api.gateio.ws' else {'code':'00000','data':bitget})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service = AssetVerificationService(store, SimpleNamespace(instruments=store.instruments()), client=client)
        await service.public_refresh(['gate','bitget'])
        item = items[0]
        service.add_evidence([EvidenceInput(instrument_id=item.id, native_asset_id=item.native_asset_id,
            canonical_asset_id='ethereum:0x'+'ff'*20, source_url='https://synthetic.invalid/conflict',
            source_field='synthetic.conflict', observed_at=now_ms(), expires_at=now_ms()+86400000,
            note='Synthetic conflicting evidence; not a live observation')])
        # Intentionally insufficient application TTL, retaining record integrity.
        fact = next(r for r in service._records() if r['instrument_id']=='batch-fixture:gate:SMALL01')
        old = fact.pop('id')
        fact.pop('status')
        fact['expires_at'] = now_ms()+100000
        fact['id'] = _digest(fact)
        with store.connect() as db:
            db.execute('DELETE FROM asset_identity_evidence WHERE id=?', (old,))
            db.execute("INSERT INTO asset_identity_evidence VALUES(?,?,'active')", (fact['id'],json.dumps(fact)))


def main():
    root = Path(__file__).resolve().parents[1]
    checks = []
    with tempfile.TemporaryDirectory(prefix='identity-batch-review-') as directory:
        asyncio.run(seed(directory))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ,'WATCH_MONEY_DATA_DIR':directory,'WATCH_MONEY_COLLECT':'0'}
        for name in ['FEISHU_WEBHOOK_URL','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID']:
            env.pop(name,None)
        with open(Path(directory)/'server.log','w') as log:
            proc = subprocess.Popen([sys.executable,'-m','uvicorn','watch_money.app:app','--host','127.0.0.1','--port',str(port)],cwd=root,env=env,stdout=log,stderr=log)
            try:
                with httpx.Client(base_url=base,trust_env=False,timeout=10) as api:
                    for _ in range(100):
                        try:
                            if api.get('/api/health').is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    else:
                        raise RuntimeError('Local test server did not start')
                    with sync_playwright() as pw:
                        browser = pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                        page = browser.new_page(viewport={'width':1440,'height':1000})
                        errors = []
                        page.on('pageerror',lambda e: errors.append(str(e)))
                        page.goto(base)
                        page.get_by_role('button',name='设置',exact=True).click()
                        page.get_by_text('资产身份与映射管理',exact=True).click()
                        panel = page.get_by_role('region',name='公开证据分组审核',exact=True)
                        expect(panel).to_contain_text('实际通过跨平台身份门槛：0 组 / 0 市场')
                        expect(panel).to_contain_text('筛选结果 31 组；当前 1–25')
                        expect(panel.locator('tbody > tr')).to_have_count(25)
                        checks.append('actual_zero_distinguished_from_candidates_and_25_group_page')
                        panel.get_by_role('button',name='选择本页可审核组',exact=True).click()
                        expect(panel).to_contain_text('已选 23 组')
                        panel.get_by_role('button',name='下一页分组',exact=True).click()
                        expect(panel).to_contain_text('当前 26–31，已选 0 组')
                        checks.append('pagination_is_bounded_and_clears_selection')
                        panel.get_by_label('审核来源筛选').select_option('gate')
                        expect(panel).to_contain_text('当前 1–25')
                        panel.get_by_label('审核链筛选').select_option('ethereum')
                        panel.get_by_label('审核阻止原因筛选').select_option('evidence_validity_window_insufficient')
                        expect(panel).to_contain_text('筛选结果 1 组')
                        expect(panel).to_contain_text('有效期不足1小时或超出策略范围')
                        checks.append('source_chain_and_time_blocker_filters_reset_page_without_hiding_reason')
                        panel.get_by_label('审核阻止原因筛选').select_option('')
                        panel.get_by_label('审核合约筛选').fill('0x'+f'{3:040x}')
                        expect(panel).to_contain_text('筛选结果 1 组')
                        expect(panel).to_contain_text('bitget / gate')
                        panel.get_by_label('审核合约筛选').fill('')
                        expect(panel).to_contain_text('筛选结果 31 组')
                        panel.get_by_role('button',name='选择本页可审核组',exact=True).click()
                        boxes = panel.locator('tbody input[type=checkbox]')
                        boxes.nth(0).check()
                        boxes.nth(1).check()
                        panel.get_by_role('button',name='预览所选公开分组',exact=True).click()
                        preview = panel.get_by_label('公开批量应用预览')
                        expect(preview).to_contain_text('将应用 23 组 / 46 市场；跳过阻止 2 组')
                        assert api.get('/api/assets/verification/batch-review').json()['actual_comparable_groups'] == 0
                        checks.append('preview_counts_eligible_and_skipped_groups_without_writing')
                        panel.get_by_role('button',name='确认应用此批公开映射',exact=True).click()
                        expect(panel).to_contain_text('实际应用 23 组 / 46 市场')
                        expect(panel).to_contain_text('实际通过跨平台身份门槛：23 组 / 46 市场')
                        assert api.get('/api/assets/verification/batch-review').json()['actual_comparable_groups'] == 23
                        checks.append('explicit_scoped_apply_changes_only_46_temporary_markets')
                        panel.get_by_label('公开批次撤销原因').fill('Synthetic browser rehearsal rollback')
                        panel.get_by_role('button',name='撤销上一批公开映射',exact=True).click()
                        expect(panel).to_contain_text('实际通过跨平台身份门槛：0 组 / 0 市场')
                        assert api.get('/api/assets/verification/batch-review').json()['actual_comparable_groups'] == 0
                        checks.append('last_batch_revoke_restores_identity_coverage_and_keeps_audit')
                        page.set_viewport_size({'width':390,'height':844})
                        panel.get_by_role('heading',name='公开证据分组审核').scroll_into_view_if_needed()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
                        out=root/'docs/screenshots/identity-batch-review-synthetic.png'
                        out.parent.mkdir(parents=True,exist_ok=True)
                        page.screenshot(path=str(out))
                        assert not errors,errors
                        checks.append('mobile_no_document_overflow_and_no_runtime_errors')
                        browser.close()
            finally:
                proc.terminate()
                proc.wait(timeout=10)
    evidence=root/'docs/evidence/browser-identity-batch-review.json'
    evidence.write_text(json.dumps({'mode':'isolated_database_synthetic_official_schemas_no_exchange_requests',
        'checks':checks,'passed':len(checks),'production_mappings_changed':False},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':len(checks),'evidence':str(evidence)}))


if __name__=='__main__':
    main()
