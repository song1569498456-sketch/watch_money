"""Navigation journeys against isolated synthetic public-market fixtures only."""
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
from watch_money.models import Instrument, MarketSnapshot, now_ms
from watch_money.storage import Storage


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "docs" / "evidence"
    screenshots = root / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    checks = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="watch-money-navigation-") as directory:
        seed(directory)
        store = Storage(directory)
        stamp = now_ms()
        instruments = [Instrument(id=f"navigation:{venue}:{asset}", venue=venue,
                      native_symbol=f"{asset}/USDT", asset_id=f"navigation:{asset}", base=asset,
                      quote="USDT", settle="USDT", kind="perpetual", mapping_verified=True)
                      for asset in [f"NAV{number:02d}" for number in range(1, 9)]
                      for venue in ["okx", "bybit", "gate"]]
        store.save_instruments(list(store.instruments().values()) + instruments)
        store.save_snapshots([MarketSnapshot(instrument_id=item.id, venue=item.venue,
                             bid=99 if item.venue == "okx" else 109,
                             ask=100 if item.venue == "okx" else 110,
                             funding_rate=.001 if item.venue == "okx" else .002,
                             funding_kind="current", funding_interval_hours=8,
                             next_funding_at=stamp + 3600000, source_at=stamp, received_at=stamp,
                             source="navigation:synthetic-not-live") for item in instruments], archive=False)
        env = {**os.environ, "WATCH_MONEY_DATA_DIR": directory, "WATCH_MONEY_COLLECT": "0"}
        for key in ("FEISHU_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            env.pop(key, None)
        with (Path(directory) / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "watch_money.app:app", "--host",
                                       "127.0.0.1", "--port", str(port)], cwd=root, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=base, trust_env=False, timeout=5) as api:
                    for _ in range(100):
                        try:
                            if api.get("/api/health").is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        if server.poll() is not None:
                            raise RuntimeError("Temporary navigation server failed to start")
                        time.sleep(.2)
                    else:
                        raise RuntimeError("Temporary navigation server readiness timeout")
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(executable_path=shutil.which("chromium"),
                                                             headless=True, args=["--no-sandbox"])
                        page = browser.new_page(viewport={"width": 1440, "height": 900})
                        errors, calculations, external_requests, scanner_requests = [], [], [], []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.on("request", lambda request: calculations.append(request.url)
                                if request.url.endswith("/api/opportunities/calculate") else None)
                        page.on("request", lambda request: scanner_requests.append(request.url)
                                if "/api/scanner?" in request.url else None)
                        page.on("request", lambda request: external_requests.append(request.url)
                                if request.url.startswith(("https://", "http://"))
                                and not request.url.startswith(base + "/") else None)
                        page.goto(base)
                        page.evaluate("""() => {
                            const label = document.createElement('div');
                            label.textContent = '导航合成验收 · 非真实行情';
                            label.style.cssText = 'position:fixed;bottom:8px;right:8px;z-index:9999;background:#503b11;color:#ffe49b;padding:8px;font:12px sans-serif;pointer-events:none;max-width:90vw';
                            document.body.append(label);
                        }""")
                        nav = page.get_by_role("navigation", name="主导航", exact=True)
                        expect(nav.get_by_role("button")).to_have_count(4)
                        assert nav.get_by_role("button").all_text_contents() == ["市场监控", "我的监控", "通知记录", "设置"]
                        expect(page.get_by_role("button", name="价差", exact=True)).to_have_attribute("aria-current", "page")
                        expect(page.get_by_role("heading", name="全市场资产机会", exact=False)).to_be_visible()
                        expect(nav.get_by_role("button", name="手动测算", exact=True)).to_have_count(0)
                        page.screenshot(path=str(screenshots / "navigation-default-synthetic.png"), full_page=True)
                        checks.append("default is market monitoring / spread; four primary destinations; manual calculator remains secondary")
                        page.get_by_role("button", name="资金费", exact=True).click()
                        page.get_by_role("button", name="同资产跨平台矩阵", exact=True).click()
                        expect(page.get_by_role("columnheader", name="bybit", exact=True)).to_be_visible()
                        expect(page.get_by_role("columnheader", name="okx", exact=True)).to_be_visible()
                        page.screenshot(path=str(screenshots / "navigation-funding-desktop-synthetic.png"), full_page=True)
                        checks.append("funding matrix is directly discoverable under market monitoring")
                        page.get_by_label("资金费资产搜索").fill("PEPE")
                        page.get_by_role("button", name="查看 PEPE · okx · PEPE/USDT:USDT 详情", exact=True).click()
                        funding_detail = page.get_by_role("complementary", name="资金费详情", exact=True)
                        funding_detail.get_by_role("button", name="测算此组合", exact=True).click()
                        expect(page.get_by_label("买入 / 做多市场", exact=False)).to_have_value("browser-fixture:okx-perp:PEPE")
                        expect(page.get_by_label("卖出 / 做空市场", exact=False)).to_have_value("browser-fixture:bybit:PEPE")
                        with page.expect_response(lambda response: "/api/funding?" in response.url and "search=PEPE" in response.url):
                            page.get_by_role("button", name="返回发现页面", exact=True).click()
                        expect(page.get_by_role("button", name="资金费", exact=True)).to_have_attribute("aria-current", "page")
                        expect(page.get_by_label("资金费资产搜索")).to_have_value("PEPE")
                        expect(funding_detail).to_be_visible()
                        assert not calculations
                        page.get_by_role("button", name="关闭资金费详情", exact=True).click()
                        page.get_by_label("资金费资产搜索").fill("")
                        checks.append("funding detail hands off the exact low/high pair and returns to funding with its search and detail preserved, without calculating")
                        page.get_by_role("button", name="价差", exact=True).click()
                        search = page.get_by_label("搜索扫描资产或原生市场")
                        with page.expect_response(lambda response: "/api/scanner?" in response.url and "q=NAV" in response.url):
                            search.fill("NAV")
                        page.get_by_label("扫描排序").select_option("funding")
                        page.get_by_label("扫描市场类型").select_option("perpetual")
                        platform_details = page.locator("details.scanner-platform-filter")
                        platform_details.locator("summary").click()
                        platform_details.get_by_role("checkbox", name="gate", exact=True).uncheck()
                        expect(platform_details.get_by_role("checkbox", name="gate", exact=True)).not_to_be_checked()
                        page.get_by_role("button", name="展开 NAV08 跨平台组合", exact=True).click()
                        expanded = page.get_by_role("button", name="收起 NAV08 跨平台组合", exact=True)
                        group_id = expanded.get_attribute("aria-controls")
                        group = page.locator(f'[id="{group_id}"]')
                        button = group.get_by_role("button", name="测算此组合", exact=True).first
                        expect(button).to_be_enabled()
                        button.scroll_into_view_if_needed()
                        before_y = page.evaluate("window.scrollY")
                        assert before_y > 200, "Fixture must exercise an actual scrolled position"
                        scanned_before_handoff = len(scanner_requests)
                        button.click()
                        expect(page.get_by_role("heading", name="组合测算", exact=True)).to_be_visible()
                        expect(page.get_by_label("买入 / 做多市场", exact=False)).to_have_value("navigation:okx:NAV08")
                        expect(page.get_by_label("卖出 / 做空市场", exact=False)).to_have_value("navigation:bybit:NAV08")
                        assert not calculations, "Opening a candidate must not submit any calculation"
                        with page.expect_response(lambda response: "/api/scanner?" in response.url and "q=NAV" in response.url):
                            page.get_by_role("button", name="返回发现页面", exact=True).click()
                        expect(search).to_have_value("NAV")
                        expect(page.get_by_label("扫描排序")).to_have_value("funding")
                        expect(page.get_by_label("扫描市场类型")).to_have_value("perpetual")
                        expect(platform_details.get_by_role("checkbox", name="gate", exact=True)).not_to_be_checked()
                        expect(expanded).to_have_attribute("aria-expanded", "true")
                        page.wait_for_function("previous => Math.abs(window.scrollY - previous) < 32", arg=before_y)
                        after_y = page.evaluate("window.scrollY")
                        page.wait_for_timeout(300)
                        assert len(scanner_requests) > scanned_before_handoff, "Returning must revalidate while preserving controls and position"
                        checks.append(f"candidate calculator returns search/platforms/market type/sort/expanded asset and scroll position ({before_y}px -> {after_y}px), without submitting")
                        page.screenshot(path=str(screenshots / "navigation-return-synthetic.png"))
                        search.fill("NAV08")
                        page.get_by_role("button", name="资金费", exact=True).click()
                        page.wait_for_timeout(350)
                        assert not any("q=NAV08" in url for url in scanner_requests), "Hidden scanner must not fetch its deferred query"
                        with page.expect_response(lambda response: "/api/scanner?" in response.url and "q=NAV08" in response.url):
                            page.get_by_role("button", name="价差", exact=True).click()
                        expect(search).to_have_value("NAV08")
                        expect(page.get_by_role("button", name="收起 NAV08 跨平台组合", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="展开 NAV01 跨平台组合", exact=True)).to_have_count(0)
                        checks.append("leaving before search debounce pauses hidden scans; returning applies deferred query instead of trapping old results")
                        nav.get_by_role("button", name="我的监控", exact=True).click()
                        expect(page.get_by_role("heading", name="我的监控", exact=True)).to_be_visible()
                        expect(page.get_by_role("heading", name="持续机会监控", exact=False)).to_be_visible()
                        expect(page.get_by_role("button", name="计算收益情景", exact=True)).to_have_count(0)
                        checks.append("my monitors has its own primary entry and no calculator form")
                        nav.get_by_role("button", name="设置", exact=True).click()
                        page.get_by_text("资产身份与映射管理", exact=True).click()
                        expect(page.get_by_role("heading", name="资产身份候选", exact=False)).to_be_visible()
                        page.get_by_role("button", name="数据源诊断与覆盖", exact=True).click()
                        expect(page.get_by_role("heading", name="数据源诊断", exact=True)).to_be_visible()
                        checks.append("identity management and source diagnostics are reachable from settings")
                        page.set_viewport_size({"width": 768, "height": 1024})
                        for name in ["市场监控", "我的监控", "通知记录", "设置"]:
                            expect(nav.get_by_role("button", name=name, exact=True).locator("span")).to_be_visible()
                        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                        checks.append("768px tablet primary navigation keeps all four visible text labels")
                        page.set_viewport_size({"width": 390, "height": 844})
                        for destination in ["市场监控", "我的监控", "通知记录", "设置"]:
                            nav.get_by_role("button", name=destination, exact=True).click()
                            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), destination
                        nav.get_by_role("button", name="市场监控", exact=True).click()
                        page.get_by_role("button", name="资金费", exact=True).click()
                        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                        page.screenshot(path=str(screenshots / "navigation-mobile-synthetic.png"), full_page=True)
                        with page.expect_response(lambda response: "/api/funding?" in response.url):
                            page.get_by_role("button", name="刷新全部数据", exact=True).click()
                        checks.append("global refresh refetches current funding query")
                        with page.expect_response(lambda response: "/api/scanner?" in response.url):
                            page.get_by_role("button", name="价差", exact=True).click()
                        # Global refresh also invalidates the hidden scanner's cached generation.
                        with page.expect_response(lambda response: "/api/scanner?" in response.url):
                            page.get_by_role("button", name="刷新全部数据", exact=True).click()
                        expect(page.get_by_role("button", name="刷新扫描", exact=True)).to_be_enabled()
                        star = page.get_by_role("button", name="关注 NAV08 代表市场", exact=True)
                        star.click()
                        nav.get_by_role("button", name="我的监控", exact=True).click()
                        expect(page.get_by_role("heading", name="关注的市场 · 1", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="取消关注", exact=True)).to_be_visible()
                        page.get_by_role("button", name="取消关注", exact=True).click()
                        expect(page.get_by_role("heading", name="关注的市场 · 0", exact=True)).to_be_visible()
                        checks.append("starred market is discoverable and removable in My Monitors without creating a combination monitor")
                        nav.get_by_role("button", name="市场监控", exact=True).click()
                        page.get_by_role("button", name="价差", exact=True).click()
                        page.evaluate("window.originalDateNow = Date.now; Date.now = () => window.originalDateNow() + 3601000")
                        expect(page.get_by_text("查询结果已过期，排名与路径需重新核验；以下仅为查询时快照，请刷新扫描。", exact=True)).to_be_visible()
                        expect(page.get_by_text("来源与时效已核验", exact=True)).to_have_count(0)
                        expect(page.get_by_role("button", name="测算此组合", exact=True).first).to_be_disabled()
                        nav.get_by_role("button", name="我的监控", exact=True).click()
                        with page.expect_response(lambda response: "/api/scanner?" in response.url):
                            nav.get_by_role("button", name="市场监控", exact=True).click()
                        page.evaluate("Date.now = window.originalDateNow")
                        checks.append("cached scanner ages without auto-refresh, removes reliable claims and handoff, and rechecks on return")
                        assert not calculations and not external_requests and not errors, (calculations, external_requests, errors)
                        assert api.get("/api/monitors").json()["monitors"] == []
                        checks.append("390px primary destinations and funding matrix have no document overflow; no external/account/trading requests or JS errors")
                        browser.close()
            finally:
                server.terminate()
                server.wait(timeout=15)
    result = {"passed": True, "checks": checks, "at": now_ms(), "account_api_used": False,
              "data_mode": "isolated synthetic navigation fixtures, no live exchange data"}
    (output / "browser-navigation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
