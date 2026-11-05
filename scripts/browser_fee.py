"""Isolated synthetic fee UI checks; no account or live exchange requests.

Uses a temporary local server/database and mocked fee references. The fixture
rates are test values, never a claim about current official exchange prices.
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
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from playwright.sync_api import expect, sync_playwright

from watch_money.models import Instrument, MarketSnapshot, now_ms
from watch_money.storage import Storage


def seed(directory):
    store = Storage(directory)
    settings = store.settings().model_copy(update={"collection_enabled": False, "notifications_enabled": False,
                                                  "demo_mode": False, "stale_seconds": 3600})
    store.set("settings", settings.model_dump())
    items = [Instrument(id=f"fee-fixture:{venue}:{kind}:PEPE", venue=venue,
                        native_symbol="PEPE/USDT" + (":USDT" if kind == "perpetual" else ""),
                        asset_id="fee-fixture:PEPE", base="PEPE", quote="USDT", settle="USDT",
                        kind=kind, mapping_verified=True)
             for venue, kind in (("okx", "spot"), ("bybit", "spot"), ("coinbase", "spot"), ("okx", "perpetual"))]
    store.save_instruments(items)
    stamp = now_ms()
    store.save_snapshots([MarketSnapshot(instrument_id=item.id, venue=item.venue, bid=99, ask=100,
                                        source_at=stamp, received_at=stamp, source="browser-fee:synthetic-not-live")
                          for item in items], archive=False)
    return {item.id: item for item in items}


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / "docs" / "evidence"
    screenshots = root / "docs" / "screenshots"
    evidence.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    checks = []
    checked_at = now_ms() - 3600_000
    fee_values = {("okx", "spot"): (0.0008, 0.001), ("bybit", "spot"): (0.0012, 0.0015),
                  ("coinbase", "spot"): (0.005, 0.006), ("okx", "perpetual"): (0.0002, 0.0005)}
    overrides = {}
    calculations = []
    writes = []
    scoped_requests = []

    def rows(venue=None, kind=None):
        profiles = []
        for (row_venue, row_kind), rates in fee_values.items():
            if (venue and row_venue != venue) or (kind and row_kind != kind):
                continue
            for role, rate in zip(("maker", "taker"), rates):
                key = f"{row_venue}:{row_kind}:{role}"
                default = {"rate": rate, "source": f"https://fees-fixture.invalid/{row_venue}/{row_kind}",
                           "conditions": "浏览器合成普通档位条件，非真实费率", "updated_at": None,
                           "checked_at": checked_at, "fee_currency": "USDT", "tier": "standard",
                           "source_verified": True, "evidence_status": "official_public"}
                override = overrides.get(key)
                effective = {**(override or default)}
                if row_venue == "coinbase":
                    effective.update(rate=None, evidence_status="out_of_scope", source_verified=False,
                                     conditions="合成范围不匹配：默认表有值，但此市场不适用")
                profiles.append({"key": key, "venue": row_venue, "kind": row_kind, "liquidity": role,
                                 "applicable": True, "default": default, "override": override, "effective": effective})
        return profiles

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="watch-money-fees-") as directory:
        items = seed(directory)
        env = {**os.environ, "WATCH_MONEY_DATA_DIR": directory, "WATCH_MONEY_COLLECT": "0"}
        for key in ("FEISHU_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            env.pop(key, None)
        with (Path(directory) / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "watch_money.app:app", "--host",
                                       "127.0.0.1", "--port", str(port)], cwd=root, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
                    for _ in range(100):
                        try:
                            if client.get("/api/health").is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        if server.poll() is not None:
                            raise RuntimeError("Temporary fee server failed to start")
                        time.sleep(.2)
                    else:
                        raise RuntimeError("Temporary fee server readiness timeout")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=shutil.which("chromium"), headless=True,
                                                         args=["--no-sandbox"])
                    page = browser.new_page(viewport={"width": 1500, "height": 1100})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def fees_route(route):
                        if route.request.method == "PUT":
                            body = route.request.post_data_json
                            writes.append(body)
                            key = f"{body['venue']}:{body['kind']}:{body['liquidity']}"
                            if body.get("reset"):
                                overrides.pop(key, None)
                            else:
                                overrides[key] = {"rate": body["rate"], "source": body["source"],
                                                  "conditions": body["conditions"], "updated_at": body["updated_at"],
                                                  "checked_at": None, "source_verified": False, "evidence_status": "user_override"}
                            response = {"profile": next(row for row in rows(body["venue"], body["kind"]) if row["key"] == key), "audit": []}
                        else:
                            query = parse_qs(urlparse(route.request.url).query)
                            instrument_id = query.get("instrument_id", [None])[0]
                            if instrument_id:
                                scoped_requests.append(instrument_id)
                            item = items.get(instrument_id)
                            response = {"profiles": rows(item.venue, item.kind) if item else rows(), "audit": [], "as_of": now_ms()}
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(response))

                    def calculate_route(route):
                        calculations.append(route.request.post_data_json)
                        route.fulfill(status=200, content_type="application/json", body=json.dumps({"opportunities": []}))

                    def orderbook_route(route):
                        instrument_id = unquote(urlparse(route.request.url).path.split("/api/orderbook/", 1)[1])
                        route.fulfill(status=200, content_type="application/json", body=json.dumps({
                            "instrument_id": instrument_id, "bids": [[99, 100]], "asks": [[100, 100]],
                            "quantity_unit": "base", "source_at": now_ms(), "received_at": now_ms(),
                            "source": "browser-fee:synthetic-orderbook", "is_demo": False}))

                    page.route("**/api/fees**", fees_route)
                    page.route("**/api/opportunities/calculate", calculate_route)
                    page.route("**/api/orderbook/**", orderbook_route)
                    page.goto(base)
                    page.get_by_role("button", name="手动测算", exact=True).click()
                    buy = page.get_by_label("买入 / 做多市场", exact=False)
                    sell = page.get_by_label("卖出 / 做空市场", exact=False)
                    buy.select_option("fee-fixture:okx:spot:PEPE")
                    sell.select_option("fee-fixture:bybit:spot:PEPE")
                    expect(page.get_by_label("买方开仓费率 / %", exact=True)).to_have_value("0.1")
                    expect(page.get_by_label("卖方开仓费率 / %", exact=True)).to_have_value("0.15")
                    page.get_by_label("买方开仓成交方式", exact=True).select_option("maker")
                    expect(page.get_by_label("买方开仓费率 / %", exact=True)).to_have_value("0.08")
                    expect(page.get_by_label("买方平仓费率 / %", exact=True)).to_have_value("0.1")
                    checks.append("four independent maker/taker legs load instrument-scoped effective profiles; one role change leaves other legs unchanged")

                    page.get_by_label("买方开仓费率 / %", exact=True).fill("0.777")
                    page.get_by_label("买方持仓成本 / % 每小时", exact=False).fill("0.5")
                    page.get_by_label("卖方开仓费率 / %", exact=True).fill("0.888")
                    page.get_by_label("卖方持仓成本 / % 每小时", exact=False).fill("0.6")
                    sell.select_option("fee-fixture:coinbase:spot:PEPE")
                    expect(page.get_by_label("卖方开仓费率 / %", exact=True)).to_have_value("")
                    expect(page.get_by_label("卖方平仓费率 / %", exact=True)).to_have_value("")
                    expect(page.get_by_label("卖方持仓成本 / % 每小时", exact=False)).to_have_value("")
                    expect(page.get_by_label("买方开仓费率 / %", exact=True)).to_have_value("0.777")
                    expect(page.get_by_label("买方持仓成本 / % 每小时", exact=False)).to_have_value("0.5")
                    checks.append("sell instrument switch clears only sell open/close/holding; out-of-scope effective null never uses non-null table default")
                    buy.select_option("fee-fixture:okx:perpetual:PEPE")
                    expect(page.get_by_label("买方开仓费率 / %", exact=True)).to_have_value("0.02")
                    expect(page.get_by_label("买方平仓费率 / %", exact=True)).to_have_value("0.05")
                    expect(page.get_by_label("买方持仓成本 / % 每小时", exact=False)).to_have_value("")
                    checks.append("same venue product switch clears manual fee and holding values and loads the new perpetual profile")
                    buy.select_option("fee-fixture:bybit:spot:PEPE")
                    sell.select_option("fee-fixture:okx:spot:PEPE")
                    expect(page.get_by_label("买方开仓费率 / %", exact=True)).to_have_value("0.12")
                    expect(page.get_by_label("买方平仓费率 / %", exact=True)).to_have_value("0.15")
                    expect(page.get_by_label("卖方开仓费率 / %", exact=True)).to_have_value("0.1")
                    page.get_by_label("规模输入方式", exact=False).select_option("base")
                    page.get_by_role("spinbutton", name="基础资产数量", exact=False).fill("1")
                    page.get_by_role("button", name="计算收益情景", exact=True).click()
                    expect(page.get_by_role("heading", name="计算结果", exact=True)).to_be_visible()
                    assert calculations[-1]["costs"][0]["open_fee_rate"] == .0012
                    assert calculations[-1]["costs"][0]["at"] == checked_at
                    assert "资料核验时间" in calculations[-1]["costs"][0]["source"]
                    assert "fees-fixture.invalid/bybit/spot" in calculations[-1]["costs"][0]["source"]
                    checks.append("default request costs retain actual source/conditions and checked-at when publication update time is unknown")

                    page.get_by_label("买方开仓费率 / %", exact=True).fill("0.777")
                    page.get_by_role("button", name="计算收益情景", exact=True).click()
                    page.wait_for_function("() => !document.querySelector('button[type=submit].primary')?.disabled")
                    mixed = calculations[-1]
                    assert mixed["costs"][0]["at"] == checked_at
                    assert "用户假设" in mixed["costs"][0]["source"]
                    assert str(mixed["as_of"]) in mixed["costs"][0]["source"]
                    checks.append("mixed manual/default request retains per-field source times; one manual edit does not rejuvenate default evidence")
                    for label in ("买方开仓费率 / %", "买方平仓费率 / %", "卖方开仓费率 / %", "卖方平仓费率 / %"):
                        page.get_by_label(label, exact=True).fill("0")
                    for label in ("买方持仓成本 / % 每小时", "卖方持仓成本 / % 每小时"):
                        page.get_by_label(label, exact=False).fill("0")
                    page.get_by_role("button", name="计算收益情景", exact=True).click()
                    page.wait_for_function("() => !document.querySelector('button[type=submit].primary')?.disabled")
                    manual = calculations[-1]
                    assert all(cost["at"] == manual["as_of"] and "用户假设" in cost["source"] for cost in manual["costs"])
                    assert all(cost["open_fee_rate"] == cost["close_fee_rate"] == 0 for cost in manual["costs"])
                    checks.append("explicit zero and manual fees are user assumptions dated at calculation time")

                    page.get_by_role("button", name="设置", exact=True).click()
                    expect(page.get_by_role("heading", name="平台默认手续费", exact=True)).to_be_visible()
                    page.get_by_label("费率平台", exact=True).select_option("okx")
                    rate = page.get_by_label("okx 现货 maker本地默认费率 / %", exact=True)
                    rate.fill("0.23")
                    page.get_by_label("okx 现货 maker覆盖来源", exact=True).fill("浏览器合成用户假设")
                    row = rate.locator("xpath=ancestor::tr")
                    row.get_by_role("button", name="保存覆盖", exact=True).click()
                    expect(page.get_by_text("本地默认覆盖已保存", exact=False)).to_be_visible()
                    assert writes[-1]["rate"] == .0023 and writes[-1]["source"] == "浏览器合成用户假设"
                    assert "source_verified" not in writes[-1] and "apiKey" not in writes[-1]
                    row.get_by_role("button", name="恢复公开默认", exact=True).click()
                    expect(page.get_by_text("已恢复公开默认", exact=False)).to_be_visible()
                    assert writes[-1] == {"venue": "okx", "kind": "spot", "liquidity": "maker", "reset": True}
                    checks.append("configuration saves explicit local overrides without verified/account fields and restores the public default via reset")
                    page.evaluate("""() => {
                        const label=document.createElement('div'); label.textContent='浏览器合成费率验收 · 非真实平台费率';
                        label.style.cssText='position:fixed;top:6px;right:12px;z-index:9999;background:#503b11;color:#ffe49b;padding:8px;font:12px sans-serif';
                        document.body.append(label);
                    }""")
                    page.screenshot(path=str(screenshots / "fee_profiles_synthetic.png"), full_page=True)
                    page.get_by_role("region", name="平台默认手续费", exact=True).screenshot(
                        path=str(screenshots / "fee_profiles_panel_synthetic.png"))
                    assert not errors, errors
                    assert set(scoped_requests) >= set(items), scoped_requests
                    browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    result = {"passed": True, "fixture": "synthetic_fee_references_not_live", "checks": checks, "count": len(checks),
              "account_api_used": False, "at": now_ms()}
    (evidence / "browser_fee.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
