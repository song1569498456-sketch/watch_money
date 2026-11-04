"""Synthetic Gate depth timestamp labels in an isolated local browser/server."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright, expect
from watch_money.models import Instrument, MarketSnapshot, now_ms
from watch_money.storage import Storage
from watch_money.adapters.gate_snapshot import parse_snapshot

root = Path(__file__).resolve().parents[1]
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
with tempfile.TemporaryDirectory(prefix="gate-snapshot-browser-") as directory:
    store = Storage(directory)
    store.set(
        "settings",
        store.settings()
        .model_copy(update={"collection_enabled": False, "notifications_enabled": False})
        .model_dump(),
    )
    item = Instrument(
        id="fixture:gate:SMALL",
        venue="gate",
        native_symbol="SMALL/USDT",
        asset_id="SMALL",
        base="SMALL",
        quote="USDT",
        kind="spot",
        mapping_verified=True,
    )
    at = now_ms()
    store.save_instruments([item])
    store.save_snapshots(
        [
            MarketSnapshot(
                instrument_id=item.id, venue="gate", source="synthetic", bid=100, ask=101, source_at=at
            )
        ]
    )
    env = {**os.environ, "WATCH_MONEY_DATA_DIR": directory, "WATCH_MONEY_COLLECT": "0"}
    for key in ("FEISHU_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        env.pop(key, None)
    with (Path(directory) / "server.log").open("w") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "watch_money.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get(base + "/api/health").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Local fixture server unavailable")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"]
                )
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                response = {}

                def depth(route):
                    route.fulfill(status=response.get("status", 200), json=response["body"])

                page.route("**/api/orderbook/**", depth)
                book, _, observation = parse_snapshot(
                    item,
                    {"current": at, "update": at - 3600000, "bids": [["100", "2"]], "asks": [["101", "3"]]},
                    at,
                )
                response["body"] = {**book.model_dump(), "observation": {**observation, "merged": True}}
                page.goto(base)
                page.get_by_role("button", name="全部市场", exact=True).click()
                page.get_by_role("button", name="查看 SMALL 详情", exact=True).click()
                page.get_by_role("button", name="获取盘口", exact=True).click()
                expect(
                    page.get_by_text("此处使用交易所快照生成时点，不是盘口变动时点。", exact=False)
                ).to_be_visible()
                response["body"]["observation"].update(
                    source_time_kind="unknown",
                    exchange_generated_at=None,
                    book_last_changed_at=None,
                    merged=False,
                )
                page.get_by_role("button", name="获取盘口", exact=True).click()
                expect(
                    page.get_by_text("未提供快照生成时点；不会用本地收包时间补齐。", exact=False)
                ).to_be_visible()
                response["body"]["source_at"] = None
                response.update(
                    status=429, body={"detail": "Public snapshot rate budget busy; retry after 30 seconds"}
                )
                page.get_by_role("button", name="获取盘口", exact=True).click()
                expect(page.get_by_text("Public snapshot rate budget busy", exact=False)).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="设置", exact=True).click()
                page.get_by_role("button", name="数据源诊断与覆盖", exact=True).click()
                expect(page.get_by_text("盘口诊断：", exact=False).first).to_be_visible()
                assert not errors
                browser.close()
            (root / "docs/evidence/browser-gate-snapshot.json").write_text(
                json.dumps(
                    {
                        "provenance": "synthetic loopback fixture and mocked depth response; no live exchange",
                        "checks": [
                            "exchange generation distinct from last change label",
                            "missing native clock explicit",
                            "budget rejection visible",
                            "390px no page overflow",
                            "field clock diagnosis visible in settings",
                            "no page errors",
                        ],
                    },
                    indent=2,
                )
                + "\n"
            )
            print("6 synthetic Gate snapshot browser checks passed")
        finally:
            server.terminate()
            server.wait(timeout=10)
