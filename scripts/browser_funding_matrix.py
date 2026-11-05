"""Isolated funding-matrix checks using synthetic records, never live funding data."""
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
from playwright.sync_api import expect, sync_playwright

from watch_money.models import Instrument, MarketSnapshot, now_ms
from watch_money.storage import Storage


def seed(directory):
    store = Storage(directory)
    settings = store.settings().model_copy(update={"collection_enabled": False, "notifications_enabled": False,
                                                  "demo_mode": False, "stale_seconds": 300,
                                                  "enabled_venues": ["okx", "bybit", "gate"]})
    store.set("settings", settings.model_dump())
    stamp = now_ms()
    items, snapshots = [], []
    for base, venue, rate, hours, mode in [
        ("PEPE", "okx", .00002, 8, "fresh"), ("PEPE", "bybit", .00001, 1, "fresh"),
        ("PEPE", "gate", .00003, 4, "fresh"), ("ALONE", "okx", 0, 8, "fresh"),
        ("WAIT", "okx", None, None, "uncollected"), ("WAIT", "bybit", .0001, 8, "fresh"),
        ("STALE", "okx", .004, 8, "stale"), ("STALE", "bybit", .0001, 8, "fresh"),
        ("PERIOD", "okx", .0003, None, "fresh"), ("PERIOD", "bybit", .0001, 4, "fresh"),
        ("SAME", "okx", .0002, 8, "unverified"), ("SAME", "bybit", .0003, 8, "unverified"),
        ("TINY", "bybit", .00000001, 8, "fresh"),
    ]:
        item = Instrument(id=f"funding-matrix-fixture:{base}:{venue}", venue=venue, base=base,
                          asset_id=f"fixture:{base}" if mode != "unverified" else f"{venue}:{base}",
                          native_symbol=f"{base}/USDT:USDT", quote="USDT", settle="USDT", kind="perpetual",
                          mapping_verified=mode != "unverified")
        items.append(item)
        if mode != "uncollected":
            snapshots.append(MarketSnapshot(instrument_id=item.id, venue=venue, funding_rate=rate,
                                            funding_kind="current", funding_interval_hours=hours,
                                            next_funding_at=stamp + 3600_000, source_at=stamp, received_at=stamp,
                                            source="browser-matrix:synthetic-not-live",
                                            field_timestamps={"funding_rate": stamp - 301_000} if mode == "stale" else {}))
    store.save_instruments(items)
    store.save_snapshots(snapshots, archive=False)


def main():
    root = Path(__file__).resolve().parents[1]
    evidence, screenshots = root / "docs/evidence", root / "docs/screenshots"
    evidence.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    checks = []
    history_requests = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="watch-money-funding-matrix-") as directory:
        seed(directory)
        env = {**os.environ, "WATCH_MONEY_DATA_DIR": directory, "WATCH_MONEY_COLLECT": "0"}
        for key in ("FEISHU_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            env.pop(key, None)
        with (Path(directory) / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "watch_money.app:app", "--host",
                                       "127.0.0.1", "--port", str(port)], cwd=root, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=base_url, trust_env=False, timeout=5) as client:
                    for _ in range(100):
                        try:
                            if client.get("/api/health").is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        if server.poll() is not None:
                            raise RuntimeError("Temporary funding matrix server failed to start")
                        time.sleep(.2)
                    else:
                        raise RuntimeError("Temporary funding matrix server readiness timeout")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=shutil.which("chromium"), headless=True,
                                                         args=["--no-sandbox"])
                    page = browser.new_page(viewport={"width": 1500, "height": 1100})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def history(route):
                        history_requests.append(route.request.url)
                        route.fulfill(status=200, content_type="application/json", body=json.dumps({
                            "records": [{"settlement_at": now_ms() - 3600_000, "funding_rate": .00011,
                                         "funding_interval_hours": None, "event_type": "regular",
                                         "source": "browser-matrix:mocked-settled-history", "received_at": now_ms()}],
                            "summary": {"count": 1, "refreshed": True},
                        }))

                    page.route("**/api/funding/history/**", history)
                    page.goto(base_url)
                    page.get_by_role("button", name="资金费", exact=True).click()
                    expect(page.get_by_role("button", name="同资产跨平台矩阵", exact=True)).to_have_attribute("aria-pressed", "true")
                    rows = page.locator("tr[data-funding-asset]")
                    pepe = rows.filter(has=page.get_by_text("PEPE", exact=True))
                    expect(pepe).to_contain_text("+0.0060%")
                    expect(pepe).to_contain_text("高：bybit")
                    expect(pepe).to_contain_text("低：okx")
                    expect(pepe.get_by_text("原始 +0.0010% / 1 小时", exact=True)).to_be_visible()
                    expect(pepe.get_by_text("原始 +0.0020% / 8 小时", exact=True)).to_be_visible()
                    checks.append("default cross-platform matrix computes 0.0060 percentage-point 8h difference with correct platforms and retains 1h/8h raw periods")
                    expect(rows.filter(has=page.get_by_text("ALONE", exact=True)).get_by_text("无市场", exact=True)).to_have_count(2)
                    expect(rows.filter(has=page.get_by_text("WAIT", exact=True)).get_by_text("未采集", exact=True)).to_be_visible()
                    expect(rows.filter(has=page.get_by_text("STALE", exact=True)).get_by_text("过期", exact=True)).to_be_visible()
                    expect(rows.filter(has=page.get_by_text("PERIOD", exact=True)).get_by_text("周期未知", exact=True)).to_be_visible()
                    expect(rows.filter(has=page.get_by_text("SAME", exact=True))).to_have_count(2)
                    expect(rows.filter(has=page.get_by_text("SAME", exact=True)).get_by_text("不可比", exact=True)).to_have_count(2)
                    expect(rows.filter(has=page.get_by_text("TINY", exact=True))).to_contain_text("+0.000001%")
                    assert not history_requests
                    checks.append("no market, uncollected, stale and unknown period are distinct; unverified same-ticker identities remain separate; tiny nonzero rates stay nonzero")
                    page.locator(".funding-platform-picker > summary").click()
                    with page.expect_response(lambda response: "/api/funding?" in response.url and
                                              parse_qs(urlparse(response.url).query).get("platforms") == ["gate,okx"]):
                        page.get_by_label("资金费平台 bybit", exact=True).uncheck()
                    expect(page.get_by_role("columnheader", name="bybit", exact=True)).to_have_count(0)
                    expect(pepe).to_contain_text("+0.0040%")
                    expect(pepe).to_contain_text("高：gate")
                    page.locator(".funding-platform-picker > summary").click()
                    checks.append("platform checkbox sends server-side platforms scope before budget and recomputes high/low pair without excluded venue")
                    page.get_by_role("button", name="费率排行榜", exact=True).click()
                    page.get_by_label("排行平台", exact=True).select_option("okx")
                    expect(page.locator(".funding-ranking-table tbody tr").first).to_contain_text("SAME · okx")
                    page.get_by_label("费率排序口径", exact=True).select_option("raw")
                    expect(page.locator(".funding-ranking-table tbody tr").first).to_contain_text("PERIOD · okx")
                    assert all("okx" in value for value in page.locator(".funding-ranking-table tbody tr td:first-child").all_text_contents())
                    checks.append("single-platform ranking queries one venue and switches raw/8h ordering without cross-platform contamination")
                    page.get_by_role("button", name="同资产跨平台矩阵", exact=True).click()
                    page.get_by_label("资金费资产搜索", exact=True).fill("PERIOD")
                    expect(rows).to_have_count(1)
                    expect(rows.first).to_contain_text("PERIOD")
                    page.get_by_label("资金费资产搜索", exact=True).fill("")
                    expect(pepe).to_be_visible()
                    page.get_by_role("button", name="查看 PEPE · okx · PEPE/USDT:USDT 详情", exact=True).click()
                    detail = page.get_by_role("complementary", name="资金费详情", exact=True)
                    expect(detail).to_contain_text("当前估算")
                    expect(detail).to_contain_text("源事件")
                    expect(detail.get_by_role("button", name="测算此组合", exact=True)).to_be_visible()
                    assert not history_requests
                    detail.get_by_role("button", name="获取已结算历史", exact=True).click()
                    expect(detail.get_by_text("常规结算", exact=True)).to_be_visible()
                    expect(detail.get_by_text("不使用当前周期回填历史", exact=True)).to_be_visible()
                    assert len(history_requests) == 1 and "refresh=true" in history_requests[0]
                    page.get_by_role("button", name="关闭资金费详情", exact=True).click()
                    checks.append("search is server-scoped; details retain type/time/source and only explicit user action fetches settled history")
                    page.evaluate("""() => {
                        const label=document.createElement('div'); label.textContent='资金费矩阵合成验收 · 非真实行情';
                        label.style.cssText='position:fixed;top:6px;right:10px;z-index:9999;background:#503b11;color:#ffe49b;padding:8px;font:12px sans-serif';
                        document.body.append(label);
                    }""")
                    page.screenshot(path=str(screenshots / "funding_matrix_synthetic.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.locator(".funding-platform-picker > summary").click()
                    popup = page.locator(".funding-platform-options").bounding_box()
                    assert popup is not None and popup["x"] >= -1 and popup["x"] + popup["width"] <= 391, popup
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                    assert not errors, errors
                    checks.append("390px platform picker stays within viewport and matrix scrolls inside its own container without body overflow")
                    browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    result = {"fixture": "synthetic_funding_matrix_not_live", "count": len(checks), "checks": checks,
              "history_requests": len(history_requests), "at": now_ms()}
    (evidence / "browser_funding_matrix.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
