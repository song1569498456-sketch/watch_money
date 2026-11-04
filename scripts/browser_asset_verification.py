"""Real local identity-review API journey with synthetic evidence and no external reads.

Only public-refresh failure is browser-mocked. Evidence import, preview, apply,
revoke, persistence, coverage and audit use the actual isolated application.
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

from watch_money.models import Instrument, now_ms
from watch_money.storage import Storage


def seed(directory):
    store = Storage(directory)
    settings = store.settings().model_copy(update={"collection_enabled": False, "notifications_enabled": False,
                                                  "demo_mode": False})
    store.set("settings", settings.model_dump())
    instruments = [Instrument(id=f"verification-fixture:{venue}:{symbol}", venue=venue,
                   native_symbol=f"{symbol}/USDT", asset_id=f"cex:{venue}:{venue}:{symbol}",
                   native_asset_id=f"cex:{venue}:{venue}:{symbol}", base=symbol, quote="USDT",
                   kind="spot", mapping_verified=False)
                   for venue, symbol in (("gate", "PEPE"), ("bitget", "PEPE"), ("gate", "OTHER"))]
    store.save_instruments(instruments)
    return instruments


def main():
    root = Path(__file__).resolve().parents[1]
    evidence_dir, screenshots = root / "docs" / "evidence", root / "docs" / "screenshots"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    checks = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="watch-money-verification-") as directory:
        instruments = seed(directory)
        observed = now_ms()
        target = "base:0x" + "11" * 20
        facts = [{"instrument_id": item.id, "native_asset_id": item.native_asset_id,
                  "canonical_asset_id": target,
                  "source_url": f"https://synthetic-evidence.invalid/{item.venue}/public-contract",
                  "source_field": "synthetic.currency.contract", "observed_at": observed,
                  "expires_at": observed + 86400000, "relationship": "same_asset", "unit_multiplier": 1,
                  "note": "Synthetic browser fixture; not an actual public API observation"}
                 for item in instruments[:2]]
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
                            raise RuntimeError("Temporary verification server failed to start")
                        time.sleep(.2)
                    else:
                        raise RuntimeError("Temporary verification server readiness timeout")
                    before = api.get("/api/assets/verification").json()
                    assert before["coverage"]["active_markets"] == before["coverage"]["unverified_markets"] == 3
                    assert before["coverage"]["traceable_verified_markets"] == 0
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(executable_path=shutil.which("chromium"),
                                                             headless=True, args=["--no-sandbox"])
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        errors, external, mocked_refresh = [], [], []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.on("request", lambda request: external.append(request.url)
                                if request.url.startswith(("http://", "https://"))
                                and not request.url.startswith(base + "/") else None)

                        def public_failure(route):
                            mocked_refresh.append(route.request.post_data_json)
                            route.fulfill(status=503, content_type="application/json", body=json.dumps({
                                "detail": "合成公开来源不可访问：浏览器已拦截请求，未读取外部资料"}))

                        page.route("**/api/assets/verification/public-refresh", public_failure)
                        page.goto(base)

                        def open_review():
                            page.get_by_role("button", name="设置", exact=True).click()
                            page.get_by_text("资产身份与映射管理", exact=True).click()
                            panel = page.get_by_role("region", name="批量资产身份核验", exact=True)
                            expect(panel).to_be_visible()
                            return panel

                        panel = open_review()
                        expect(panel.get_by_text("未核验 3", exact=True)).to_be_visible()
                        expect(panel.get_by_role("button", name="预览所选映射", exact=True)).to_be_disabled()
                        panel.locator("summary").filter(has_text="证据明细、修订与撤销").click()
                        panel.get_by_label("导入公开身份依据").fill(json.dumps({"items": facts}))
                        with page.expect_response(lambda response: response.url.endswith("/api/assets/verification/evidence")) as imported:
                            panel.get_by_role("button", name="导入身份依据", exact=True).click()
                        assert imported.value.ok
                        evidence_ids = imported.value.json()["evidence_ids"]
                        assert len(evidence_ids) == 2
                        expect(panel.get_by_text("证据已导入，请选择并预览后再应用映射。", exact=True)).to_be_visible()
                        assert api.get("/api/assets/verification").json()["coverage"]["traceable_verified_markets"] == 0
                        checks.append("actual local evidence import binds two native market identities; import alone does not verify or merge them")
                        panel.get_by_role("button", name="选择当前可核验证据", exact=True).click()
                        with page.expect_response(lambda response: response.url.endswith("/api/assets/verification/preview")) as previewed:
                            panel.get_by_role("button", name="预览所选映射", exact=True).click()
                        assert previewed.value.ok
                        preview = previewed.value.json()
                        assert preview["ready"] == 1 and preview["blocked"] == 0
                        assert set(preview["proposals"][0]["instrument_ids"]) == {item.id for item in instruments[:2]}
                        expect(panel.get_by_role("heading", name="所选映射预览", exact=True)).to_be_visible()
                        with page.expect_response(lambda response: response.url.endswith("/api/assets/verification/apply")) as applied:
                            panel.get_by_role("button", name="应用已核验映射", exact=True).click()
                        assert applied.value.ok and applied.value.json()["applied_markets"] == 2
                        expect(panel.get_by_text("可追溯已核验 2", exact=True)).to_be_visible()
                        applied_status = api.get("/api/assets/verification").json()
                        assert applied_status["coverage"]["unverified_markets"] == 1
                        mapped = {item["id"]: item for item in api.get("/api/markets").json()["instruments"]}
                        assert all(mapped[item.id]["asset_id"] == target and mapped[item.id]["mapping_verified"]
                                   for item in instruments[:2])
                        assert not mapped[instruments[2].id]["mapping_verified"]
                        checks.append("real preview/apply verifies exactly the two selected markets; traceable coverage changes 0 -> 2 and unrelated market stays unverified")
                        page.reload()
                        panel = open_review()
                        expect(panel.get_by_text("可追溯已核验 2", exact=True)).to_be_visible()
                        page.evaluate("""() => { const note=document.createElement('div');
                            note.textContent='身份核验合成验收 · 非真实公开证据';
                            note.style.cssText='position:fixed;bottom:8px;right:8px;z-index:9999;background:#503b11;color:#ffe49b;padding:8px;font:12px sans-serif;pointer-events:none';
                            document.body.append(note); }""")
                        panel.screenshot(path=str(screenshots / "asset-verification-applied-synthetic.png"))
                        checks.append("page reload preserves actual applied mappings and provenance coverage")
                        panel.locator("summary").filter(has_text="证据明细、修订与撤销").click()
                        contradictory = {**facts[0], "canonical_asset_id": "base:0x" + "44" * 20,
                                         "source_url": "https://synthetic-evidence.invalid/new-contradiction"}
                        panel.get_by_label("导入公开身份依据").fill(json.dumps({"items": [contradictory]}))
                        with page.expect_response(lambda response: response.url.endswith("/api/assets/verification/evidence")) as contradiction:
                            panel.get_by_role("button", name="导入身份依据", exact=True).click()
                        assert contradiction.value.ok
                        contradictory_id, = contradiction.value.json()["evidence_ids"]
                        changed = api.get("/api/assets/verification").json()
                        assert changed["coverage"]["traceable_verified_markets"] < 2
                        changed_markets = {item["id"]: item for item in api.get("/api/markets").json()["instruments"]}
                        assert not changed_markets[instruments[0].id]["mapping_verified"]
                        panel.get_by_label(f"选择身份依据 {contradictory_id}", exact=True).check()
                        panel.get_by_label("身份依据撤销原因").fill("Synthetic acceptance: remove contradictory claim")
                        with page.expect_response(lambda response: response.url.endswith("/api/assets/verification/revoke")) as removed_conflict:
                            panel.get_by_role("button", name="撤销所选证据", exact=True).click()
                        assert removed_conflict.value.ok
                        expect(panel.get_by_text("可追溯已核验 2", exact=True)).to_be_visible()
                        checks.append("new contradictory evidence immediately downgrades an applied identity; revoking only that contradiction restores the still-valid original evidence")
                        for evidence_id in evidence_ids:
                            panel.get_by_label(f"选择身份依据 {evidence_id}", exact=True).check()
                        panel.get_by_label("身份依据撤销原因").fill("Synthetic acceptance: withdraw reviewed evidence")
                        with page.expect_response(lambda response: response.url.endswith("/api/assets/verification/revoke")) as revoked:
                            panel.get_by_role("button", name="撤销所选证据", exact=True).click()
                        assert revoked.value.ok
                        expect(panel.get_by_text("可追溯已核验 0", exact=True)).to_be_visible()
                        revoked_status = api.get("/api/assets/verification").json()
                        assert revoked_status["coverage"]["unverified_markets"] == 3
                        assert all(row["status"] == "revoked" for row in revoked_status["evidence"])
                        actions = {row["action"] for row in revoked_status["audit"]}
                        assert {"asset_evidence_import", "asset_verification_apply", "asset_evidence_revoke"} <= actions
                        panel.locator("summary").filter(has_text="身份核验审计记录").click()
                        expect(panel.get_by_role("cell", name="asset_evidence_revoke", exact=True).first).to_be_visible()
                        checks.append("real revoke removes verification, preserves evidence records and displays import/apply/revoke audit")
                        conflicts = [{**facts[0], "canonical_asset_id": "base:0x" + byte * 20,
                                      "source_url": f"https://synthetic-evidence.invalid/conflict-{byte}"}
                                     for byte in ("22", "33")]
                        panel.get_by_label("导入公开身份依据").fill(json.dumps({"items": conflicts}))
                        with page.expect_response(lambda response: response.url.endswith("/api/assets/verification/evidence")) as conflicting_import:
                            panel.get_by_role("button", name="导入身份依据", exact=True).click()
                        assert conflicting_import.value.ok
                        conflict_ids = conflicting_import.value.json()["evidence_ids"]
                        for evidence_id in conflict_ids:
                            panel.get_by_label(f"选择身份依据 {evidence_id}", exact=True).check()
                        with page.expect_response(lambda response: response.url.endswith("/api/assets/verification/preview")) as conflicting_preview:
                            panel.get_by_role("button", name="预览所选映射", exact=True).click()
                        conflict_preview = conflicting_preview.value.json()
                        assert conflict_preview["blocked"] > 0 and conflict_preview["ready"] == 0
                        expect(panel.get_by_role("button", name="应用已核验映射", exact=True)).to_be_disabled()
                        rejected = api.post("/api/assets/verification/apply", json={"evidence_ids": conflict_ids,
                                            "preview_token": conflict_preview["preview_token"]})
                        assert rejected.status_code == 409
                        assert api.get("/api/assets/verification").json()["coverage"]["traceable_verified_markets"] == 0
                        checks.append("two contradictory contract claims disable UI apply; direct real local apply also rejects atomically without verified coverage")
                        panel.get_by_role("button", name="读取公开资产资料", exact=True).click()
                        expect(panel.get_by_role("alert")).to_contain_text("合成公开来源不可访问")
                        assert mocked_refresh == [{"venues": ["gate", "bitget"]}]
                        assert api.get("/api/assets/verification").json()["coverage"]["traceable_verified_markets"] == 0
                        checks.append("browser-mocked public-refresh failure is explicit and creates no evidence or verified mappings; no external source read")
                        page.set_viewport_size({"width": 390, "height": 844})
                        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                        panel.screenshot(path=str(screenshots / "asset-verification-mobile-synthetic.png"))
                        assert not external and not errors, (external, errors)
                        checks.append("390px identity panel has no document overflow; no external/account/trading requests or browser JS errors")
                        browser.close()
            finally:
                server.terminate()
                server.wait(timeout=15)
    result = {"passed": True, "checks": checks, "at": now_ms(), "account_api_used": False,
              "data_mode": "isolated synthetic identity evidence; actual local API mutations; public-refresh failure browser-mocked"}
    (evidence_dir / "browser-asset-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
