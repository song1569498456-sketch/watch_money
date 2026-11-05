"""R24: local versioned API data with deliberately delayed/replayed browser delivery.

Exchange requests are disabled. WS frames and delayed HTTP bodies are obtained
from the actual local app; only the query-failure response is explicitly mocked.
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
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.sync_api import Error, expect, sync_playwright
from watch_money.models import Instrument, MarketSnapshot, now_ms
from watch_money.storage import Storage

SERVER = '''
import sys
import uvicorn
from watch_money.app import create_app
from watch_money.models import now_ms
app = create_app(sys.argv[1], start_collector=False, adapters={})
@app.post('/__fixture__/price/{price}')
async def price(price: float):
    stamp = now_ms()
    for identity, old in tuple(app.state.collector.snapshots.items()):
        app.state.collector.snapshots[identity] = old.model_copy(update={
            'bid': price, 'ask': price + 1, 'source_at': stamp, 'received_at': stamp,
            'field_timestamps': {**old.field_timestamps, 'bid': stamp, 'ask': stamp},
            'field_received_at': {**old.field_received_at, 'bid': stamp, 'ask': stamp}})
    return {'synthetic_price': price}
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')
'''


def seed(directory):
    store = Storage(directory)
    settings = store.settings().model_copy(update={"enabled_venues": ["okx", "bybit"], "collection_enabled": False,
        "notifications_enabled": False, "demo_mode": False, "broad_collection_enabled": True, "stale_seconds": 3600})
    store.set("settings", settings.model_dump())
    stamp = now_ms()
    items = [Instrument(id=f"r24:{venue}:{base}", venue=venue, native_symbol=f"{base}/USDT:USDT", base=base,
        quote="USDT", settle="USDT", kind="perpetual", asset_id=f"synthetic:{base}", mapping_verified=True)
        for base in ("PEPE", "BTC") for venue in ("okx", "bybit")]
    store.save_instruments(items)
    fields = ["bid", "ask", "funding_rate", "funding_kind", "funding_interval_hours", "next_funding_at"]
    store.save_snapshots([MarketSnapshot(instrument_id=item.id, venue=item.venue, bid=100, ask=101,
        funding_rate=.001 if item.venue == "okx" else .002, funding_kind="current", funding_interval_hours=8,
        next_funding_at=stamp + 3600000, source_at=stamp, received_at=stamp, source="synthetic:r24-not-live",
        field_timestamps={field: stamp for field in fields}, field_received_at={field: stamp for field in fields})
        for item in items], archive=False)


def main():
    root = Path(__file__).resolve().parents[1]
    evidence, screenshots = root / "docs/evidence", root / "docs/screenshots"
    evidence.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    checks, sequence_evidence = [], []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="watch-money-r24-") as directory:
        seed(directory)
        bootstrap = Path(directory) / "server.py"
        bootstrap.write_text(SERVER)
        env = {**os.environ, "WATCH_MONEY_COLLECT": "0", "WATCH_MONEY_DATA_DIR": directory}
        for key in ("FEISHU_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            env.pop(key, None)
        with (Path(directory) / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, str(bootstrap), directory, str(port)], cwd=root, env=env, stdout=log, stderr=log)
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
                        raise RuntimeError("R24 fixture server startup timeout")
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"])
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        page.set_default_timeout(8000)
                        errors, external, writes, connections, scan_requests, funding_requests = [], [], [], [], [], []
                        holds, state = {}, {"hold_old_market": False, "hold_new_scope": False,
                                            "hold_scan": False, "fail_scan": False,
                                            "hold_funding": False, "fail_funding": False}
                        page.on("pageerror", lambda error: errors.append(str(error)))

                        def track(request):
                            if not request.url.startswith(base + "/"):
                                external.append(request.url)
                            if request.method != "GET":
                                writes.append(request.url)

                        page.on("request", track)

                        def socket_route(ws):
                            source = ws.connect_to_server()
                            entry = {"route": ws, "frames": []}
                            connections.append(entry)
                            source.on_message(lambda message: entry["frames"].append(json.loads(message)))

                        page.route_web_socket("**/ws", socket_route)

                        def market_route(route):
                            if state["hold_old_market"]:
                                state["hold_old_market"] = False
                                holds["old_market"] = (route, route.fetch())
                            elif state["hold_new_scope"]:
                                result = route.fetch()
                                if result.json()["view_context"]["enabled_venues"] == ["okx"]:
                                    holds["new_market"] = (route, result)
                                    state["hold_new_scope"] = False
                                else:
                                    route.fulfill(response=result)
                            else:
                                route.continue_()

                        def query_route(route, kind):
                            query = parse_qs(urlparse(route.request.url).query)
                            (scan_requests if kind == "scan" else funding_requests).append(route.request.url)
                            search = query.get("q" if kind == "scan" else "search", [""])[0]
                            if state[f"hold_{kind}"] and search == "PEPE":
                                state[f"hold_{kind}"] = False
                                holds[kind] = (route, route.fetch())
                            elif state[f"fail_{kind}"]:
                                state[f"fail_{kind}"] = False
                                route.fulfill(status=503, content_type="application/json", body=json.dumps({
                                    "code": "heavy_query_overloaded", "detail": "Synthetic query retry fixture"}))
                            else:
                                route.continue_()

                        page.route("**/api/markets", market_route)
                        page.route("**/api/scanner?**", lambda route: query_route(route, "scan"))
                        page.route("**/api/funding?**", lambda route: query_route(route, "funding"))

                        def wait_for(predicate, label, timeout=8):
                            deadline = time.monotonic() + timeout
                            while time.monotonic() < deadline:
                                if predicate():
                                    return
                                page.wait_for_timeout(50)
                            raise AssertionError(label)

                        def release(name):
                            route, response = holds.pop(name)
                            try:
                                route.fulfill(response=response)
                            except Error as exc:
                                # Aborting the superseded fetch is an expected safe outcome.
                                assert "closed" in str(exc).lower() or "interception" in str(exc).lower() or "handled" in str(exc).lower()

                        def overview():
                            page.get_by_role("navigation", name="主导航", exact=True).get_by_role("button", name="市场监控", exact=True).click()
                            page.get_by_role("button", name="全部市场", exact=True).click()

                        page.goto(base)
                        page.evaluate("""() => { const el=document.createElement('div');el.textContent='R24 合成时序验收 · 本地数据 / 可控重放';el.style.cssText='position:fixed;bottom:8px;right:8px;background:#503b11;color:#ffe49b;padding:8px;z-index:9999;font:12px sans-serif;pointer-events:none';document.body.append(el); }""")
                        overview()
                        page.get_by_role("button", name="展开 PEPE 市场", exact=True).click()
                        row = page.locator("tr.market-row").filter(has_text="PEPE/USDT:USDT").filter(has_text="okx")
                        expect(row.locator("td").nth(2)).to_contain_text("100.5")
                        wait_for(lambda: connections and any(frame["type"] == "markets" for frame in connections[-1]["frames"]), "initial real WS full frame")
                        first_socket = connections[-1]
                        old_full = next(frame for frame in first_socket["frames"] if frame["type"] == "markets")
                        first_socket["route"].send(json.dumps(old_full))
                        api.post("/__fixture__/price/150").raise_for_status()
                        wait_for(lambda: any(frame["type"] == "markets_delta" and any(s.get("bid") == 150 for s in frame["snapshots"])
                                            for frame in first_socket["frames"]), "real changed-price delta")
                        old_delta = next(frame for frame in first_socket["frames"] if frame["type"] == "markets_delta"
                                         and any(s.get("bid") == 150 for s in frame["snapshots"]))
                        for frame in first_socket["frames"]:
                            if frame is old_delta:
                                break
                            if frame["view_seq"] > old_full["view_seq"]:
                                first_socket["route"].send(json.dumps(frame))
                        api.post("/__fixture__/price/200").raise_for_status()
                        with page.expect_response(lambda response: response.url.endswith("/api/markets")) as newest:
                            page.get_by_role("button", name="刷新全部数据", exact=True).click()
                        new_http = newest.value.json()
                        assert new_http["view_seq"] > old_delta["view_seq"] > old_full["view_seq"]
                        expect(row.locator("td").nth(2)).to_contain_text("200.5")
                        first_socket["route"].send(json.dumps(old_full))
                        first_socket["route"].send(json.dumps(old_delta))
                        page.wait_for_timeout(1200)
                        expect(row.locator("td").nth(2)).to_contain_text("200.5")
                        sequence_evidence.append({"old_full": old_full["view_seq"], "old_delta": old_delta["view_seq"], "new_http": new_http["view_seq"]})
                        checks.append("actual older WS full and changed-price delta delivered after a newer HTTP view cannot roll 200.5 back to 100.5/150.5")

                        # Hold a prior HTTP view across the real settings-save flow.
                        state["hold_old_market"] = True
                        page.get_by_role("button", name="刷新全部数据", exact=True).click()
                        wait_for(lambda: "old_market" in holds, "held old-scope HTTP response")
                        state["hold_new_scope"] = True
                        before_connections = len(connections)
                        page.get_by_role("navigation", name="主导航", exact=True).get_by_role("button", name="设置", exact=True).click()
                        page.locator(".venue-checks label").filter(has=page.get_by_text("bybit", exact=True)).get_by_role("checkbox").uncheck()
                        page.get_by_role("button", name="保存配置", exact=True).click()
                        wait_for(lambda: "new_market" in holds and len(connections) > before_connections, "new-scope request and websocket restart")
                        overview()
                        expect(page.locator("tr.asset-row")).to_have_count(0)
                        release("old_market")
                        connections[-1]["route"].send(json.dumps(old_full))
                        page.wait_for_timeout(700)
                        expect(page.locator("tr.asset-row")).to_have_count(0)
                        release("new_market")
                        expect(page.locator("tr.asset-row")).to_have_count(2)
                        expect(page.locator("tr.market-row").filter(has_text="bybit")).to_have_count(0)
                        expect(page.locator("tr.market-row").filter(has_text="okx")).to_have_count(1)
                        checks.append("saving platform scope clears old rows and reconnects; held old HTTP and old-context WS cannot repopulate them before the correct-scope response")

                        # Deliver the real current baseline, then corrupt only its base revision.
                        current_socket = connections[-1]
                        wait_for(lambda: any(frame["type"] == "markets" for frame in current_socket["frames"]), "current-scope full frame")
                        current_full = next(frame for frame in current_socket["frames"] if frame["type"] == "markets")
                        current_socket["route"].send(json.dumps(current_full))
                        bad_delta = {**current_full, "type": "markets_delta", "view_seq": current_full["view_seq"] + 1,
                                     "base_view_seq": current_full["view_seq"] + 999, "instruments": [], "snapshots": [],
                                     "removed_instruments": [], "removed_snapshots": []}
                        before_connections = len(connections)
                        current_socket["route"].send(json.dumps(bad_delta))
                        wait_for(lambda: len(connections) > before_connections and bool(connections[-1]["frames"]), "bad delta must reconnect for a full view", 9)
                        recovered_full = next(frame for frame in connections[-1]["frames"] if frame["type"] == "markets")
                        connections[-1]["route"].send(json.dumps(recovered_full))
                        page.wait_for_timeout(600)
                        expect(page.locator("tr.asset-row")).to_have_count(2)
                        checks.append("a deliberately mismatched delta base revision triggers a new connection and actual full-view recovery instead of applying the delta")
                        page.screenshot(path=str(screenshots / "market-view-order-scope-synthetic.png"), full_page=True)

                        settings = api.get("/api/settings").json()
                        settings["enabled_venues"] = ["okx", "bybit"]
                        api.put("/api/settings", json=settings).raise_for_status()
                        with page.expect_response(lambda response: response.url.endswith("/api/markets")):
                            page.get_by_role("button", name="刷新全部数据", exact=True).click()
                        page.get_by_role("button", name="价差", exact=True).click()
                        expect(page.get_by_role("button", name="展开 PEPE 跨平台组合", exact=True)).to_be_visible()
                        page.get_by_role("button", name="展开 PEPE 跨平台组合", exact=True).click()
                        search = page.get_by_label("搜索扫描资产或原生市场")
                        state["hold_scan"] = True
                        search.fill("PEPE")
                        wait_for(lambda: "scan" in holds, "delayed PEPE scan")
                        expect(page.get_by_role("button", name="测算此组合", exact=True).first).to_be_disabled()
                        with page.expect_response(lambda response: "/api/scanner?" in response.url and "q=BTC" in response.url):
                            search.fill("BTC")
                        expect(page.get_by_role("button", name="展开 BTC 跨平台组合", exact=True)).to_be_visible()
                        release("scan")
                        page.wait_for_timeout(300)
                        expect(search).to_have_value("BTC")
                        expect(page.get_by_role("button", name="展开 PEPE 跨平台组合", exact=True)).to_have_count(0)
                        page.get_by_role("button", name="展开 BTC 跨平台组合", exact=True).click()
                        with page.expect_response(lambda response: "/api/scanner?" in response.url and "sort=asset" in response.url):
                            page.get_by_label("扫描排序").select_option("asset")
                        page.get_by_role("button", name="资金费", exact=True).click()
                        state["fail_scan"] = True
                        page.get_by_role("button", name="价差", exact=True).click()
                        expect(page.get_by_text("本次扫描失败：", exact=False)).to_be_visible()
                        expect(search).to_have_value("BTC")
                        expect(page.get_by_label("扫描排序")).to_have_value("asset")
                        expect(page.get_by_role("button", name="收起 BTC 跨平台组合", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="测算此组合", exact=True).first).to_be_disabled()
                        with page.expect_response(lambda response: "/api/scanner?" in response.url and "q=BTC" in response.url):
                            page.get_by_role("button", name="刷新扫描", exact=True).click()
                        expect(page.get_by_role("button", name="测算此组合", exact=True).first).to_be_enabled()
                        checks.append("scanner delayed old filter response is rejected; return revalidates; failed return keeps search/sort/expanded rows but disables handoff, and same-query retry restores it")

                        page.get_by_role("button", name="资金费", exact=True).click()
                        funding_search = page.get_by_label("资金费资产搜索")
                        state["hold_funding"] = True
                        funding_search.fill("PEPE")
                        wait_for(lambda: "funding" in holds, "delayed funding filter")
                        page.get_by_role("button", name="价差", exact=True).click()
                        release("funding")
                        funding_count = len(funding_requests)
                        page.wait_for_timeout(350)
                        assert len(funding_requests) == funding_count
                        state["fail_funding"] = True
                        page.get_by_role("button", name="资金费", exact=True).click()
                        expect(page.get_by_text("资金费视图获取失败：", exact=False)).to_be_visible()
                        expect(funding_search).to_have_value("PEPE")
                        expect(page.get_by_role("button", name="查看 PEPE · okx · PEPE/USDT:USDT 详情", exact=True)).to_be_disabled()
                        with page.expect_response(lambda response: "/api/funding?" in response.url and "search=PEPE" in response.url):
                            page.get_by_role("button", name="手动刷新", exact=True).click()
                        page.get_by_role("button", name="查看 PEPE · okx · PEPE/USDT:USDT 详情", exact=True).click()
                        detail = page.get_by_role("complementary", name="资金费详情", exact=True)
                        expect(detail.get_by_role("button", name="测算此组合", exact=True)).to_be_enabled()
                        checks.append("funding hidden response cannot commit or refetch; return failure preserves search and disables cached detail actions; current-query retry enables a compatible pair")
                        page.screenshot(path=str(screenshots / "market-view-order-retry-synthetic.png"), full_page=True)
                        assert not errors and not external
                        assert not any("/calculate" in url or "/account" in url or "/orders" in url for url in writes)
                        browser.close()
                    evidence.joinpath("browser-market-view-order.json").write_text(json.dumps({
                        "fixture": "Actual isolated local API/WS synthetic data. Playwright delays/replays delivery and corrupts one delta base. Query failure is an explicit synthetic 503; no live exchange or account operations.",
                        "checks": checks, "version_sequences": sequence_evidence, "connections": len(connections),
                        "page_errors": errors, "external_requests": external, "browser_writes": writes,
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
