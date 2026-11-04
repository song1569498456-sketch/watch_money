"""Actual local API/browser freshness regression with synthetic market observations.

No exchange is contacted. A temporary fixture-only route holds the real query
worker to exercise its actual HTTP 503 response; market JSON is never mocked.
"""
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

SERVER = '''
import asyncio
import sys
import threading
import uvicorn
import watch_money.collector as collector_module
from watch_money.app import create_app

directory, port, at = sys.argv[1:]
collector_module.now_ms = lambda: int(at) + 1000
app = create_app(directory, start_collector=False, adapters={})
release = threading.Event()
hold = None

@app.post('/__fixture__/hold')
async def hold_worker():
    global hold
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    release.clear()
    def build():
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(15):
            raise TimeoutError('Synthetic worker barrier exceeded its safety timeout')
        return {'synthetic': True}
    hold = asyncio.create_task(app.state.query_work.run('synthetic_hold', build, lambda value: value))
    await asyncio.wait_for(entered.wait(), 3)
    return {'synthetic_worker_held': True}

@app.post('/__fixture__/release')
async def release_worker():
    release.set()
    if hold is not None:
        await hold
    return {'released': True}

uvicorn.run(app, host='127.0.0.1', port=int(port), log_level='warning')
'''


def seed(directory):
    store = Storage(directory)
    settings = store.settings().model_copy(update={"enabled_venues": ["okx"], "collection_enabled": False,
        "notifications_enabled": False, "demo_mode": False, "broad_collection_enabled": True, "stale_seconds": 60})
    store.set("settings", settings.model_dump())
    at = now_ms() - 1000
    items = [Instrument(id=f"freshness-fixture:{symbol}", venue=venue, native_symbol=f"{symbol}/USDT:USDT",
        base=symbol, quote="USDT", settle="USDT", kind="perpetual", asset_id=f"synthetic:{symbol}")
        for symbol, venue in (("FUTURE", "okx"), ("FRESH", "bybit"))]
    store.save_instruments(items)
    snapshots = []
    for item in items:
        fields = ["bid", "ask", "funding_rate", "funding_kind", "funding_interval_hours"]
        timestamps = {field: at for field in fields}
        if item.base == "FUTURE":
            timestamps.update(bid=at + 100, ask=at + 100)
        else:
            timestamps.update(funding_rate=at + 100, funding_kind=at + 100)
        snapshots.append(MarketSnapshot(instrument_id=item.id, venue=item.venue, bid=100, ask=102,
            funding_rate=.001, funding_kind="current", funding_interval_hours=8, source_at=at + 100,
            received_at=at, source="https://synthetic-market.invalid/public-observation", valid=True,
            field_sources={field: f"https://synthetic-market.invalid/{item.base}/{field}" for field in fields},
            field_timestamps=timestamps, field_received_at={field: at for field in fields}))
    store.save_snapshots(snapshots, archive=False)
    return at


def main():
    root = Path(__file__).resolve().parents[1]
    evidence, screenshots = root / "docs/evidence", root / "docs/screenshots"
    evidence.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    checks, api_evidence = [], {}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="watch-money-overview-freshness-") as directory:
        stamp = seed(directory)
        bootstrap = Path(directory) / "synthetic_server.py"
        bootstrap.write_text(SERVER)
        env = {**os.environ, "WATCH_MONEY_COLLECT": "0", "WATCH_MONEY_DATA_DIR": directory}
        for key in ("FEISHU_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            env.pop(key, None)
        with (Path(directory) / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, str(bootstrap), directory, str(port), str(stamp)],
                cwd=root, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=base, trust_env=False, timeout=5) as api:
                    for _ in range(100):
                        try:
                            if api.get("/api/health").is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        if server.poll() is not None:
                            raise RuntimeError((Path(directory) / "server.log").read_text())
                        time.sleep(.1)
                    else:
                        raise RuntimeError("Temporary freshness server readiness timeout")
                    initial = api.get("/api/markets").json()
                    assert len(initial["snapshots"]) == 1
                    bad = initial["snapshots"][0]
                    assert bad["valid"] is True and bad["price_valid"] is False and bad["price_fresh"] is False
                    assert bad["stale"] is True and bad["field_stale"]["bid"] is True
                    assert bad["funding_rate"] == .001 and bad["field_stale"]["funding_rate"] is False
                    assert bad["field_timestamps"]["bid"] == stamp + 100 and bad["field_received_at"]["bid"] == stamp
                    funding = api.get("/api/funding").json()
                    assert funding["rows"][0]["rate_rankable"] is True
                    api_evidence["future_price_normal_funding"] = bad
                    checks.append("actual API at T+1s rejects price observed with source T+100ms / receipt T; structural validity and normal funding remain intact")
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(executable_path=shutil.which("chromium"), headless=True,
                                                              args=["--no-sandbox"])
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        errors, external, private_requests, ws_frames = [], [], [], []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.on("websocket", lambda ws: ws.on("framereceived", lambda payload: ws_frames.append(json.loads(payload))))

                        def guard(route):
                            url = route.request.url
                            if url.startswith(("https://", "http://")) and not url.startswith(base + "/"):
                                external.append(url)
                                route.abort()
                            else:
                                route.continue_()

                        page.route("**/*", guard)
                        page.on("request", lambda request: private_requests.append(request.url)
                                if any(part in request.url for part in ("/account", "/trade", "/orders")) else None)
                        page.goto(base)
                        page.evaluate("""() => { const label = document.createElement('div');
                            label.textContent = '新鲜度合成验收 · 非真实行情';
                            label.style.cssText = 'position:fixed;bottom:8px;right:8px;z-index:9999;background:#503b11;color:#ffe49b;padding:8px;font:12px sans-serif;pointer-events:none';
                            document.body.append(label); }""")
                        page.get_by_role("button", name="全部市场", exact=True).click()
                        count = page.locator(".stat").filter(has=page.get_by_text("有效行情", exact=True)).locator("strong")
                        expect(count).to_have_text("0")
                        page.get_by_role("button", name="展开 FUTURE 市场", exact=True).click()
                        bad_row = page.locator("tr.market-row").filter(has_text="FUTURE/USDT:USDT")
                        bad_age = bad_row.locator('span[title^="显示价格字段时间"]')
                        expect(bad_age).to_contain_text("无效")
                        expect(bad_age).to_contain_text("过期")
                        expect(bad_age.locator(".dot.green")).to_have_count(0)
                        expect(bad_age.locator(".dot.amber")).to_have_count(1)
                        expect(bad_row).to_contain_text("+0.1000%")
                        page.screenshot(path=str(screenshots / "overview-freshness-invalid-only-synthetic.png"), full_page=True)
                        checks.append("overview reports zero valid price observations and amber invalid/stale price age, while retaining the independent normal funding observation")
                        settings = api.get("/api/settings").json()
                        settings["enabled_venues"] = ["okx", "bybit"]
                        api.put("/api/settings", json=settings).raise_for_status()
                        with page.expect_response(lambda response: response.url.endswith("/api/markets")):
                            page.get_by_role("button", name="刷新全部数据", exact=True).click()
                        # HTTP can finish before the previous WS state is painted.
                        # Wait for the source stream to observe the new platform scope.
                        for _ in range(100):
                            if any(any(item["id"] == "freshness-fixture:FRESH" for item in frame.get("instruments", []))
                                   for frame in ws_frames):
                                break
                            page.wait_for_timeout(50)
                        else:
                            raise AssertionError("Local WebSocket did not publish the newly enabled market")
                        expect(count).to_have_text("1")
                        page.get_by_role("button", name="展开 FRESH 市场", exact=True).click()
                        good_row = page.locator("tr.market-row").filter(has_text="FRESH/USDT:USDT")
                        good_age = good_row.locator('span[title^="显示价格字段时间"]')
                        expect(good_age.locator(".dot.green")).to_have_count(1)
                        expect(good_age).not_to_contain_text("无效")
                        mixed = api.get("/api/markets").json()
                        good = next(row for row in mixed["snapshots"] if row["instrument_id"].endswith(":FRESH"))
                        assert good["valid"] is True and good["price_valid"] is True and good["price_fresh"] is True
                        assert good["stale"] is False and good["field_stale"]["funding_rate"] is True
                        api_evidence["normal_price_future_funding"] = good
                        checks.append("adding a second market with normal price and future-at-arrival funding raises the count to exactly one; its price age remains green")
                        page.screenshot(path=str(screenshots / "overview-freshness-mixed-desktop-synthetic.png"), full_page=True)
                        title_before = good_age.get_attribute("title")
                        age_before = good_age.inner_text()
                        held = api.post("/__fixture__/hold")
                        held.raise_for_status()
                        try:
                            with page.expect_response(lambda response: response.url.endswith("/api/markets")) as overloaded:
                                page.get_by_role("button", name="刷新全部数据", exact=True).click()
                            assert overloaded.value.status == 503
                            assert overloaded.value.json()["code"] == "heavy_query_overloaded"
                            assert overloaded.value.headers["retry-after"] == "2"
                            expect(page.get_by_text("查询繁忙，请稍后重试；已有数据仍按原始时间判断时效", exact=False)).to_be_visible()
                            expect(count).to_have_text("1")
                            expect(bad_row).to_be_visible()
                            expect(good_row).to_be_visible()
                            expect(good_age).to_have_attribute("title", title_before)
                            page.wait_for_function("before => [...document.querySelectorAll('tr.market-row')].find(row => row.textContent.includes('FRESH/USDT:USDT')).querySelector('span[title^=\"显示价格字段时间\"]').textContent !== before", arg=age_before)
                            assert good_age.get_attribute("title") == title_before
                            api_evidence["overload"] = {"status": overloaded.value.status, "body": overloaded.value.json(),
                                "age_before": age_before, "age_after": good_age.inner_text(), "original_time_title": title_before}
                            pressure = api.get("/api/query-work").json()
                            assert pressure["active"] == 1 and pressure["routes"]["markets"]["rejected"] >= 1
                            api_evidence["overload"]["query_worker_pressure"] = pressure
                            page.screenshot(path=str(screenshots / "overview-freshness-overload-synthetic.png"), full_page=True)
                        finally:
                            api.post("/__fixture__/release").raise_for_status()
                        checks.append("actual shared query worker saturation returns HTTP 503/Retry-After; UI keeps both rows, original time labels and an advancing age instead of resetting freshness")
                        with page.expect_response(lambda response: response.url.endswith("/api/markets") and response.status == 200):
                            page.get_by_role("button", name="刷新全部数据", exact=True).click()
                        expect(good_age).to_have_attribute("title", title_before)
                        expect(count).to_have_text("1")
                        page.set_viewport_size({"width": 390, "height": 844})
                        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                        expect(bad_age.locator(".dot.green")).to_have_count(0)
                        expect(good_age.locator(".dot.green")).to_have_count(1)
                        page.screenshot(path=str(screenshots / "overview-freshness-mobile-synthetic.png"), full_page=True)
                        checks.append("releasing the worker restores successful refresh without changing source times; 390px overview has no document overflow")
                        assert not errors and not external and not private_requests
                        browser.close()
                    evidence.joinpath("browser-overview-freshness.json").write_text(json.dumps({
                        "fixture": "Synthetic observations only; actual isolated local API, WebSocket and query-worker overload; no live exchange verification",
                        "backend_as_of": stamp + 1000, "checks": checks, "api_evidence": api_evidence,
                        "page_errors": errors, "external_requests": external, "private_requests": private_requests,
                        "known_limitations": [{
                            "id": "R24", "scope": "Existing HTTP/WebSocket view overlap during enabled-platform changes",
                            "reproduction": "Start with only okx/FUTURE enabled; enable bybit/FRESH through PUT /api/settings and immediately refresh all data. HTTP can show two markets/count 1 before a queued old WebSocket state briefly restores one market/count 0; the next scoped WebSocket frame restores two/count 1.",
                            "verification_boundary": "The desktop comparison screenshot waits for the actual WebSocket frame containing FRESH. API future-at-arrival, per-field freshness, zero/one count and overload age assertions remain unchanged. The overlap itself is not fixed by this regression script.",
                        }],
                    }, ensure_ascii=False, indent=2))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
