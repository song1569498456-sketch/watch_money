"""Isolated browser checks for catalog identity, native pool UI and scanning.

Synthetic fixtures exercise the public non-demo scanner contract only inside a
temporary database. Evidence is explicitly labelled; no live source is queried.
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
from urllib.parse import unquote

import httpx
from playwright.sync_api import expect, sync_playwright
from watch_money.models import Instrument, MarketSnapshot, now_ms
from watch_money.storage import Storage


def seed(directory):
    store = Storage(directory)
    settings = store.settings().model_copy(update={"collection_enabled": False, "notifications_enabled": False,
                                                  "demo_mode": False, "stale_seconds": 3600})
    store.set("settings", settings.model_dump())
    items = [Instrument(id=f"browser-fixture:{venue}:PEPE", venue=venue, native_symbol=f"PEPE/USDT{suffix}",
                        asset_id="asset:pepe" if verified else "gate:unverified-pepe",
                        native_asset_id=f"{venue}:PEPE", base="PEPE", quote="USDT", settle="USDT",
                        kind=kind, mapping_verified=verified)
             for venue, kind, suffix, verified in (("okx", "spot", "", True),
                                                   ("bybit", "perpetual", ":USDT", True),
                                                   ("gate", "spot", "", False))]
    items.append(Instrument(id="browser-fixture:okx-perp:PEPE", venue="okx", native_symbol="PEPE/USDT:USDT",
                            asset_id="asset:pepe", native_asset_id="okx:PEPE", base="PEPE", quote="USDT",
                            settle="USDT", kind="perpetual", mapping_verified=True))
    store.save_instruments(items)
    stamp = now_ms()
    snapshots = [MarketSnapshot(instrument_id=item.id, venue=item.venue,
                               bid=.0000099 if item.venue == "okx" else .0000109,
                               ask=.0000100 if item.venue == "okx" else .0000110,
                               funding_rate=(.00002 if item.venue == "okx" else .00001) if item.kind == "perpetual" else None,
                               funding_interval_hours=(8 if item.venue == "okx" else 1) if item.kind == "perpetual" else None,
                               funding_kind="current" if item.kind == "perpetual" else None,
                               next_funding_at=stamp + 3600_000 if item.kind == "perpetual" else None,
                               field_timestamps={field: stamp for field in ("bid", "ask", "funding_rate",
                                                                           "funding_kind", "funding_interval_hours", "next_funding_at")},
                               field_received_at={field: stamp for field in ("bid", "ask", "funding_rate",
                                                                            "funding_kind", "funding_interval_hours", "next_funding_at")},
                               protocol_metrics={"raw_comparative_funding_rate": "0.12", "raw_upcoming_funding_rate": "0.03"},
                               protocol_metric_sources={"raw_comparative_funding_rate": "synthetic:rest-metric",
                                                        "raw_upcoming_funding_rate": "synthetic:ws-metric"},
                               protocol_metric_timestamps={"raw_comparative_funding_rate": None,
                                                           "raw_upcoming_funding_rate": stamp - 5000},
                               protocol_metric_received_at={"raw_comparative_funding_rate": stamp - 120000,
                                                            "raw_upcoming_funding_rate": stamp - 4999},
                               source_at=stamp, received_at=stamp, source="browser-fixture:synthetic-not-live")
                 for item in (items[0], items[1], items[3])]
    store.save_snapshots(snapshots, archive=False)


def main():
    root = Path(__file__).resolve().parents[1]
    screenshots = root / "docs" / "screenshots"
    evidence = root / "docs" / "evidence"
    screenshots.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    checks = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="watch-money-followup-") as directory:
        seed(directory)
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
                            raise RuntimeError("Temporary follow-up server failed to start")
                        time.sleep(.2)
                    else:
                        raise RuntimeError("Temporary follow-up server readiness timeout")
                    initial = api.get("/api/settings").json()
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(executable_path=shutil.which("chromium"),
                                                             headless=True, args=["--no-sandbox"])
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base)
                        expect(page.get_by_role("button", name="价差", exact=True)).to_have_attribute("aria-current", "page")
                        expect(page.get_by_role("heading", name="全市场资产机会", exact=False)).to_be_visible()
                        page.evaluate("""() => {
                            const label = document.createElement('div');
                            label.textContent = '浏览器合成验收样本 · 非真实行情';
                            label.style.cssText = 'position:fixed;top:6px;right:12px;z-index:9999;background:#503b11;color:#ffe49b;padding:8px;border-radius:5px;font:12px sans-serif';
                            document.body.append(label);
                        }""")
                        page.get_by_role("button", name="设置", exact=True).click()
                        page.get_by_text("资产身份与映射管理", exact=True).click()
                        expect(page.get_by_role("heading", name="资产身份候选", exact=False)).to_be_visible()
                        page.get_by_label("搜索身份候选").fill("PEPE")
                        expect(page.get_by_role("cell").filter(has_text="同 ticker 多身份").first).to_be_visible()
                        expect(page.get_by_text("同名市场属于不同资产身份", exact=False).first).to_be_visible()
                        page.get_by_role("button", name="市场监控", exact=True).click()
                        page.get_by_role("button", name="全部市场", exact=True).click()
                        page.get_by_label("搜索资产或市场").fill("PEPE")
                        expect(page.get_by_text("映射待核验", exact=True).first).to_be_visible()
                        expect(page.get_by_text("参考范围 · 已纳入", exact=False).first).to_be_visible()
                        checks.append("synthetic PEPE identity candidates remain distinct, searchable and unconfirmed")

                        chart_stamp = now_ms()
                        old_funding = chart_stamp - 120_000
                        chart_base = api.get("/api/markets").json()["snapshots"][0]
                        chart_records = [
                            {**chart_base, "funding_rate": .0001, "funding_kind": "current", "funding_interval_hours": 1,
                             "source_at": chart_stamp - 1000, "received_at": chart_stamp - 1000,
                             "field_timestamps": {"funding_rate": old_funding}, "field_received_at": {"funding_rate": old_funding + 1}},
                            {**chart_base, "funding_rate": .0001, "funding_kind": "current", "funding_interval_hours": 1,
                             "source_at": chart_stamp, "received_at": chart_stamp,
                             "field_timestamps": {"funding_rate": old_funding}, "field_received_at": {"funding_rate": old_funding + 1}},
                            {**chart_base, "funding_rate": .0002, "funding_kind": "predicted", "funding_interval_hours": 1,
                             "field_timestamps": {"funding_rate": old_funding + 30_000}},
                            {**chart_base, "funding_rate": .0003, "funding_kind": "settled", "funding_interval_hours": 1,
                             "field_timestamps": {"funding_rate": old_funding + 60_000}},
                            {**chart_base, "funding_rate": .0004, "funding_kind": "current", "funding_interval_hours": 1,
                             "field_timestamps": {"funding_rate": None}, "field_received_at": {}},
                        ]
                        page.route("**/api/history?**", lambda route: route.fulfill(
                            status=200, content_type="application/json", body=json.dumps({"snapshots": chart_records})))
                        page.get_by_role("button", name="查看 PEPE 详情", exact=True).first.click()
                        metrics = page.locator(".protocol-metrics")
                        expect(metrics.get_by_text("raw_comparative_funding_rate", exact=True)).to_be_visible()
                        expect(metrics.get_by_text("raw_upcoming_funding_rate", exact=True)).to_be_visible()
                        expect(metrics.get_by_text("来源 synthetic:rest-metric", exact=True)).to_be_visible()
                        expect(metrics.get_by_text("来源 synthetic:ws-metric", exact=True)).to_be_visible()
                        checks.append("raw comparative and upcoming metrics coexist with independent visible provenance and times")
                        chart = page.get_by_role("img", name="资金费率历史，共 4 条记录", exact=False)
                        expect(chart).to_be_visible()
                        expect(chart.locator("circle")).to_have_count(4)
                        expect(page.get_by_text("已结算值快照 · 1h · 来源字段时间", exact=True)).to_be_visible()
                        expect(page.get_by_text("当前估算 · 1h · 本地快照（来源时间未知）", exact=True)).to_be_visible()
                        expect(page.get_by_text("去重 1 条", exact=False)).to_be_visible()
                        checks.append("funding chart deduplicates carried funding events and separates current/predicted/settled/local snapshot series")

                        book_phase = {"mode": "valid"}
                        calculation_requests = []

                        def requested(request):
                            if request.url.endswith("/api/opportunities/calculate") and request.method == "POST":
                                calculation_requests.append(request.post_data_json)

                        def depth(route):
                            if book_phase["mode"] == "missing":
                                route.fulfill(status=502, content_type="application/json", body='{"detail":"browser fixture unavailable"}')
                                return
                            at = now_ms()
                            instrument_id = unquote(route.request.url.split("/api/orderbook/", 1)[1])
                            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                                "instrument_id": instrument_id, "bids": [[.0000099, 1000]],
                                "asks": [[.00001, 100], [.00002, 1000]], "quantity_unit": "base",
                                "source": "browser-fixture:mocked-depth", "received_at": at,
                                "source_at": at + 60_000 if book_phase["mode"] == "future" else at,
                            }))

                        page.on("request", requested)
                        page.route("**/api/orderbook/**", depth)
                        page.get_by_role("button", name="返回发现页面", exact=True).click()
                        page.get_by_role("button", name="手动测算", exact=True).click()
                        page.get_by_label("策略", exact=False).select_option("spot_perp_basis")
                        page.get_by_label("买入 / 做多市场", exact=False).select_option("browser-fixture:okx:PEPE")
                        page.get_by_label("卖出 / 做空市场", exact=False).select_option("browser-fixture:bybit:PEPE")
                        page.get_by_label("计算金额 /", exact=False).fill("0.003")
                        page.get_by_role("button", name="计算收益情景", exact=True).click()
                        expect(page.get_by_role("heading", name="计算结果", exact=True)).to_be_visible()
                        assert len(calculation_requests) == 1
                        assert abs(calculation_requests[0]["size_base"] - 200) < 1e-10
                        assert calculation_requests[0]["size_base"] != 300  # best-ask division would overstate size
                        page.locator("summary").filter(has_text="高级假设").click()
                        page.get_by_label("退出情景", exact=False).select_option("prices")
                        page.get_by_label("多腿退出卖价 /", exact=False).fill("0.00003")
                        page.get_by_label("空腿退出买价 /", exact=False).fill("0.00004")
                        with page.expect_response(lambda response: response.url.endswith("/api/opportunities/calculate")) as exit_response:
                            page.get_by_role("button", name="计算收益情景", exact=True).click()
                        assert exit_response.value.ok
                        prices_payload = calculation_requests[-1]
                        assert prices_payload["exit_prices"] == {"browser-fixture:okx:PEPE": .00003,
                                                                "browser-fixture:bybit:PEPE": .00004}
                        assert prices_payload["exit_basis_quote"] is None and not prices_payload["assume_exit_at_entry"]
                        assert all(row["net_profit"] is None for row in exit_response.value.json()["opportunities"])
                        page.get_by_label("退出情景", exact=False).select_option("basis")
                        page.get_by_label("退出基差 /", exact=False).fill("0")
                        with page.expect_response(lambda response: response.url.endswith("/api/opportunities/calculate")) as basis_response:
                            page.get_by_role("button", name="计算收益情景", exact=True).click()
                        basis_payload = calculation_requests[-1]
                        assert basis_payload["exit_basis_quote"] == 0 and basis_payload["exit_prices"] == {}
                        assert not basis_payload["assume_exit_at_entry"]
                        assert all(row["net_profit"] is None for row in basis_response.value.json()["opportunities"])
                        checks.append("explicit two-leg exit prices and zero exit basis reach mutually exclusive payload fields; missing fees keep net profit unknown")
                        page.get_by_label("退出情景", exact=False).select_option("unknown")
                        page.get_by_label("计算金额 /", exact=False).fill("0.03")
                        page.get_by_role("button", name="计算收益情景", exact=True).click()
                        expect(page.get_by_text("买方卖盘深度不足", exact=False)).to_be_visible()
                        book_phase["mode"] = "future"
                        page.get_by_label("计算金额 /", exact=False).fill("0.003")
                        page.get_by_role("button", name="计算收益情景", exact=True).click()
                        expect(page.get_by_text("买方盘口时间未知、过期或位于未来", exact=False)).to_be_visible()
                        book_phase["mode"] = "missing"
                        page.get_by_role("button", name="计算收益情景", exact=True).click()
                        expect(page.get_by_text("无法取得买方盘口，不能按金额核算", exact=False)).to_be_visible()
                        assert len(calculation_requests) == 3
                        checks.append("notional calculation posts base size from multi-level asks only; insufficient, future or missing buy depth blocks calculation without price fallback")

                        page.get_by_role("button", name="设置", exact=True).click()
                        page.get_by_text("高级诊断 · 原生池状态", exact=True).click()
                        expect(page.get_by_role("button", name="读取池状态", exact=True)).to_be_disabled()
                        checks.append("native pool state action disabled without saved chain RPC")
                        queries = page.get_by_label("DEX 搜索词 JSON")
                        queries.fill('{"uniswap":[123]}')
                        page.get_by_role("button", name="保存配置", exact=True).click()
                        expect(page.get_by_text("DEX 搜索配置必须是协议名称", exact=False)).to_be_visible()
                        assert api.get("/api/settings").json()["dex_search_queries"] == initial["dex_search_queries"]
                        queries.fill(json.dumps({"uniswap": ["PEPE USDC", "pool:ethereum:0x" + "1" * 40]}))
                        page.get_by_role("textbox", name="关注资产", exact=False).fill("PEPE")
                        page.get_by_label("资金费采集周期 / 秒", exact=False).fill("120")
                        page.get_by_label("市场目录刷新 / 秒", exact=False).fill("900")
                        page.get_by_label("链 → RPC URL（JSON）").fill('{"bsc":"http://browser-fixture.invalid"}')
                        with page.expect_response(lambda response: response.url.endswith("/api/settings")
                                                  and response.request.method == "PUT") as saved:
                            page.get_by_role("button", name="保存配置", exact=True).click()
                        assert saved.value.ok
                        persisted = api.get("/api/settings").json()
                        assert persisted["watch_assets"] == ["PEPE"]
                        assert persisted["funding_poll_seconds"] == 120 and persisted["discovery_seconds"] == 900
                        assert persisted["dex_search_queries"]["uniswap"][0] == "PEPE USDC"
                        for key in ("broad_collection_enabled", "broad_poll_seconds", "broad_batch_size",
                                    "recording_enabled", "recording_venues", "recording_max_mb", "recording_retention_days"):
                            assert persisted[key] == initial[key], key
                        checks.append("invalid DEX JSON blocked; arbitrary ticker, queries, funding/discovery settings saved without losing broad/recording settings")

                        requests = []

                        def native_quote(route):
                            payload = route.request.post_data_json
                            requests.append(payload)
                            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                                "source": "browser-fixture:mocked-read-only-estimate", "executable": False,
                                "amount_in": payload.get("amount_in"), "amount_out": "0.000000000000000002",
                                "issues": ["synthetic_browser_fixture_not_live"],
                            }))

                        page.route("**/api/pools/native/quote", native_quote)
                        page.get_by_label("原生池部署").select_option("3")
                        page.get_by_label("原生池合约地址").fill("0x" + "1" * 40)
                        page.get_by_label("原生池输入代币").fill("0x" + "2" * 40)
                        page.get_by_label("原生池输入数量").fill("0.000000000000000001")
                        page.get_by_role("button", name="读取单池估算", exact=True).click()
                        expect(page.get_by_label("原生池 JSON 结果")).to_contain_text('"executable": false')
                        assert requests[0]["amount_in"] == "0.000000000000000001"
                        assert requests[0]["block"] == "latest" and requests[0]["chain"] == "bsc"
                        checks.append("browser-mocked native estimate preserves decimal string and shows non-executable JSON; no RPC request")

                        page.get_by_role("button", name="市场监控", exact=True).click()
                        expect(page.get_by_role("heading", name="全市场资产机会", exact=False)).to_be_visible()
                        expect(page.get_by_role("button", name="展开 PEPE 跨平台组合", exact=True).first).to_be_visible()
                        page.get_by_role("button", name="展开 PEPE 跨平台组合", exact=True).first.click()
                        expect(page.get_by_role("cell").filter(has_text="+9.0000%").first).to_be_visible()
                        expect(page.get_by_text("差额 0.0000009 USDT", exact=True).first).to_be_visible()
                        expect(page.get_by_text("毛价差未扣费用，不代表可锁定收益", exact=False)).to_be_visible()
                        scan = api.get("/api/scanner").json()
                        assert scan["stats"]["blocked_groups"] == 1
                        assert scan["candidates"] and all(row["net_profit"] is None and not row["executable"]
                                                          for row in scan["candidates"])
                        with page.expect_response(lambda response: "/api/scanner?sort=funding" in response.url) as sorted_scan:
                            page.get_by_label("扫描排序").select_option("funding")
                        assert sorted_scan.value.ok
                        page.get_by_role("checkbox", name="30 秒刷新", exact=True).check()
                        page.get_by_role("checkbox", name="30 秒刷新", exact=True).uncheck()
                        direction = page.locator(".scanner-directions tbody tr").filter(has_text="browser-fixture:okx:PEPE").filter(
                            has_text="browser-fixture:bybit:PEPE").filter(has_text="+9.0000%").first
                        direction.get_by_role("button", name="测算此组合", exact=True).click()
                        expect(page.get_by_label("买入 / 做多市场", exact=False)).to_have_value("browser-fixture:okx:PEPE")
                        expect(page.get_by_label("卖出 / 做空市场", exact=False)).to_have_value("browser-fixture:bybit:PEPE")
                        expect(page.get_by_label("策略", exact=False)).to_have_value("spot_perp_basis")
                        assert len(calculation_requests) == 3
                        checks.append("expanded asset group hands off exact buy/sell instruments and strategy without submitting a calculation")
                        page.get_by_role("button", name="返回发现页面", exact=True).click()
                        checks.append("whole-catalog synthetic PEPE scan exposes direction/spread, blocked identity count and net=null; sorting/optional refresh controls work")
                        truncated = {**scan, "stats": {**scan["stats"], "possible_candidates": 10001,
                                                       "scanned": 10000, "truncated": True,
                                                       "enumeration_truncated": True}}
                        page.route("**/api/scanner?**", lambda route: route.fulfill(
                            status=200, content_type="application/json", body=json.dumps(truncated)))
                        page.get_by_role("button", name="刷新扫描", exact=True).click()
                        expect(page.get_by_text("最佳只限已扫描范围", exact=False)).to_be_visible()
                        page.unroute("**/api/scanner?**")
                        page.get_by_role("button", name="刷新扫描", exact=True).click()
                        expect(page.get_by_text("最佳只限已扫描范围", exact=False)).to_have_count(0)
                        checks.append("browser-mocked enumeration truncation visibly limits ranking claims, then restores actual fixture scan")
                        page.screenshot(path=str(screenshots / "followup.png"), full_page=True)
                        page.get_by_role("button", name="资金费", exact=True).click()
                        expect(page.get_by_role("heading", name="全市场资金费率", exact=False)).to_be_visible()
                        page.get_by_role("button", name="同资产跨平台矩阵", exact=True).click()
                        expect(page.get_by_role("columnheader", name="bybit", exact=True)).to_be_visible()
                        expect(page.get_by_role("columnheader", name="okx", exact=True)).to_be_visible()
                        expect(page.get_by_text("原始 +0.0020% / 8 小时", exact=True)).to_be_visible()
                        page.get_by_role("button", name="费率排行榜", exact=True).click()
                        page.get_by_label("排行平台", exact=True).select_option("okx")
                        with page.expect_response(lambda response: "/api/funding?" in response.url and "rate_basis=raw" in response.url):
                            page.get_by_label("费率排序口径").select_option("raw")
                        expect(page.locator("tbody tr:visible").first).to_contain_text("PEPE")
                        expect(page.locator("tbody tr:visible").first).to_contain_text("okx")
                        settled_requests = []

                        def settled_history(route):
                            settled_requests.append(route.request.url)
                            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                                "instrument_id": "browser-fixture:okx-perp:PEPE", "records": [{
                                    "settlement_at": chart_stamp - 3600_000, "funding_rate": .00011,
                                    "funding_interval_hours": None, "source": "browser-fixture:mocked-settled-history",
                                    "received_at": chart_stamp, "event_type": "regular"}],
                                "summary": {"count": 1, "refreshed": True},
                            }))

                        page.route("**/api/funding/history/**", settled_history)
                        page.get_by_role("button", name="查看详情", exact=True).first.click()
                        page.get_by_role("button", name="获取已结算历史", exact=True).first.click()
                        expect(page.get_by_role("heading", name="已结算公共历史", exact=False)).to_be_visible()
                        expect(page.get_by_text("常规结算", exact=True)).to_be_visible()
                        expect(page.get_by_text("不使用当前周期回填历史", exact=True)).to_be_visible()
                        assert len(settled_requests) == 1 and "refresh=true" in settled_requests[0]
                        page.screenshot(path=str(screenshots / "funding-followup.png"), full_page=True)
                        checks.append("synthetic cross-venue funding matrix and single-platform raw ranking render; detail-only mocked settled history stays distinct from local snapshots")
                        page.set_viewport_size({"width": 390, "height": 844})
                        page.get_by_role("button", name="价差", exact=True).click()
                        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                        assert not errors, errors
                        checks.append("scanner mobile document has no horizontal overflow; zero browser JavaScript errors")
                        browser.close()
            finally:
                server.terminate()
                server.wait(timeout=15)
    result = {"passed": True, "data_mode": "isolated synthetic browser fixtures and mocked native response; no live data",
              "checks": checks}
    (evidence / "browser-followup.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
