import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi import FastAPI
from watch_money.notifications import NotificationRule, NotificationService, router
from watch_money.notifications import TestNotification as ChannelTest


class Clock:
    def __init__(self):
        self.value = 1_800_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def event(clock, **changes):
    return {"id": "btc:spot_perp:1000", "type": "opportunity", "category": "opportunity", "asset": "BTC",
            "asset_id": "bitcoin", "strategy": "spot_perp", "platforms": ["binance", "okx"],
            "notional": 1000, "capacity": 2000, "net_profit": 10, "gross_profit": 15, "total_cost": 5,
            "spread": .02, "funding_spread": .001, "breakeven_hours": 8,
            "source_at": int(clock() * 1000), "valid": True, **changes}


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for key in ("FEISHU_WEBHOOK_URL", "FEISHU_SIGNING_SECRET", "FEISHU_SECRET", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def service(tmp_path, clock):
    return NotificationService(tmp_path, clock=clock)


async def test_local_default_dedupe_and_rearm(service, clock):
    service.save_rule(NotificationRule(name="Profit", min_net_profit=5, cooldown_seconds=30))
    assert len(await service.evaluate(event(clock))) == 1
    await service.process_pending()
    assert service.history()["deliveries"][0]["status"] == "sent"
    assert not await service.evaluate(event(clock))
    clock.advance(60)
    # A new timestamp alone does not create another identical notification.
    assert not await service.evaluate(event(clock))
    assert not await service.evaluate(event(clock, net_profit=1))
    clock.advance(1)
    assert len(await service.evaluate(event(clock))) == 1
    assert len(service.history()["notifications"]) == 2
    await service.stop()


async def test_scope_all_thresholds_freshness_and_missing(service, clock):
    service.save_rule(NotificationRule(name="Strict", assets=["BTC"], strategies=["spot_perp"],
        platforms=["okx"], notional=1000, min_net_profit=9, min_spread=.01, min_funding_spread=.0005,
        max_breakeven_hours=12, min_capacity=1000))
    for change in ({"asset": "ETH", "asset_id": "ethereum"}, {"strategy": "spot"},
                   {"platforms": ["other"]}, {"notional": 2000}, {"capacity": None},
                   {"net_profit": None}, {"spread": None}, {"funding_spread": -.001},
                   {"breakeven_hours": 13}, {"source_at": int((clock() - 120) * 1000)},
                   {"source_at": None}, {"valid": False}, {"status": "cost_pending"},
                   {"source_at": int((clock() + 120) * 1000)}):
        assert not await service.evaluate(event(clock, **change)), change
    assert len(await service.evaluate(event(clock))) == 1
    await service.stop()


async def test_duration_unique_hits_and_outage(service, clock):
    service.save_rule(NotificationRule(name="Persistent", consecutive_hits=3, duration_seconds=10,
                                       max_age_seconds=20))
    assert not await service.evaluate(event(clock))
    clock.advance(5)
    assert not await service.evaluate(event(clock))
    assert not await service.evaluate(event(clock))
    clock.advance(5)
    assert len(await service.evaluate(event(clock))) == 1
    service.save_rule(NotificationRule(name="Outage", id="outage", consecutive_hits=2, duration_seconds=10,
                                       max_age_seconds=20))
    assert not await service.evaluate(event(clock))
    clock.advance(50)
    assert not await service.evaluate(event(clock))
    clock.advance(10)
    ids = await service.evaluate(event(clock))
    assert len(ids) == 1
    await service.stop()


async def test_restart_queue_and_cooldown(tmp_path, clock):
    first = NotificationService(tmp_path, clock=clock)
    rule = NotificationRule(name="Saved", cooldown_seconds=300)
    first.save_rule(rule)
    await first.evaluate(event(clock))
    # Simulate a crash after claiming work and before receiving a provider response.
    first.db.execute("UPDATE deliveries SET status='sending'")
    first.db.commit()
    await first.stop()
    second = NotificationService(tmp_path, clock=clock)
    await second.start()
    await asyncio.sleep(.02)
    assert second.rules()[0]["id"] == rule.id
    assert second.history()["deliveries"][0]["status"] == "sent"
    clock.advance(1)
    assert not await second.evaluate(event(clock, net_profit=20))
    clock.advance(301)
    assert len(await second.evaluate(event(clock, net_profit=20))) == 1
    await second.stop()


async def test_separate_system_market_and_overnight_window(service, clock):
    service.save_rule(NotificationRule(name="Opportunity"))
    service.save_rule(NotificationRule(name="System", category="system", event_types=["disconnected"],
                                       platforms=["okx"]))
    assert len(await service.evaluate(event(clock, id="system:okx", category="system", type="disconnected",
                                           valid=False))) == 1
    assert not await service.evaluate(event(clock, id="failure", category="system", type="collection_failure"))
    service.save_rule(NotificationRule(name="Market", category="market", event_types=["new_market"]))
    assert len(await service.evaluate(event(clock, id="new", category="market", type="new_market"))) == 1
    service.save_rule(NotificationRule(name="Window", id="window", window_start="23:00", window_end="01:00"))
    # The controlled epoch is 08:00 UTC, outside the overnight window.
    assert not [n for n in await service.evaluate(event(clock, id="windowtest"))
                if any(x["id"] == n and x["rule_id"] == "window" for x in service.history()["notifications"])]
    await service.stop()


async def test_explicit_channel_selection_and_no_credentials(service, clock, monkeypatch):
    service.save_rule(NotificationRule(name="Local only"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    await service.evaluate(event(clock))
    assert {x["channel"] for x in service.history()["deliveries"]} == {"local"}
    with pytest.raises(ValueError, match="credentials"):
        await service.test(ChannelTest(channel="feishu"))
    service.save_rule(NotificationRule(name="Missing Feishu", channels=["feishu"]))
    clock.advance(1)
    await service.evaluate(event(clock))
    await service.process_pending()
    failed = next(x for x in service.history()["deliveries"] if x["channel"] == "feishu")
    assert failed["status"] == "failed"
    assert failed["last_error"] == "channel_not_configured"
    assert service.retry_delivery(failed["id"])
    await service.stop()


async def test_feishu_signed_success_and_redaction(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.test/open-apis/bot/v2/hook/private")
    monkeypatch.setenv("FEISHU_SIGNING_SECRET", "ultra-private-secret")
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        svc = NotificationService(tmp_path, clock=clock, client=client)
        await svc.test(ChannelTest(channel="feishu", message="Test ultra-private-secret"))
        await svc.process_pending()
        body = json.loads(requests[0].content)
        expected = base64.b64encode(hmac.new(f"{int(clock())}\nultra-private-secret".encode(), b"",
                                             hashlib.sha256).digest()).decode()
        assert body["sign"] == expected
        assert body["content"]["text"] == "Test [redacted]"
        persisted = json.dumps(svc.history()) + svc.path.read_bytes().decode(errors="ignore")
        assert "ultra-private-secret" not in persisted
        assert "hook/private" not in persisted
        assert all(x["status"] == "sent" for x in svc.history()["deliveries"])
        await svc.stop()


async def test_telegram_429_body_wait_restart_and_permanent_error(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-id")
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 30}})
        return httpx.Response(403, json={"ok": False, "description": "private-token forbidden"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = NotificationService(tmp_path, client=client, clock=clock)
        await first.test(ChannelTest(channel="telegram"))
        await first.process_pending()
        delivery = next(x for x in first.history()["deliveries"] if x["channel"] == "telegram")
        assert delivery["status"] == "retry"
        assert delivery["next_attempt_at"] == int((clock() + 30) * 1000)
        await first.stop()
        second = NotificationService(tmp_path, client=client, clock=clock)
        clock.advance(29)
        await second.process_pending()
        assert len(calls) == 1
        clock.advance(1)
        await second.process_pending()
        failed = next(x for x in second.history()["deliveries"] if x["channel"] == "telegram")
        assert failed["status"] == "failed"
        assert failed["attempts"] == 2
        assert failed["last_error"] == "http_403"
        assert "private-token" not in json.dumps(second.history())
        await second.stop()


async def test_transport_retry_exhaustion_and_channel_rate_limit(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    attempts = []

    async def handler(request):
        attempts.append(clock())
        raise httpx.ConnectError("sensitive url should not persist", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        svc = NotificationService(tmp_path, clock=clock, client=client)
        await svc.test(ChannelTest(channel="telegram"))
        await svc.test(ChannelTest(channel="telegram"))
        await svc.process_pending()
        assert len(attempts) == 1
        for _ in range(12):
            clock.advance(3600)
            await svc.process_pending()
        remote = [x for x in svc.history()["deliveries"] if x["channel"] == "telegram"]
        assert all(x["status"] == "failed" and x["attempts"] == 6 for x in remote)
        assert "sensitive url" not in json.dumps(svc.history())
        await svc.stop()


async def test_min_change_and_delete_cancel_pending(service, clock):
    rule = NotificationRule(name="Changes", min_change_fraction=.2, cooldown_seconds=0)
    service.save_rule(rule)
    await service.evaluate(event(clock))
    clock.advance(1)
    assert not await service.evaluate(event(clock, net_profit=11))
    clock.advance(1)
    assert len(await service.evaluate(event(clock, net_profit=13))) == 1
    assert service.delete_rule(rule.id)
    assert all(x["status"] == "cancelled" for x in service.history()["deliveries"])
    await service.stop()


async def test_message_preserves_currency_capacity_and_demo(service, clock):
    service.save_rule(NotificationRule(name="Units"))
    await service.evaluate(event(clock, valuation_currency="USDT", capacity_unit="base",
                                 size_base=.01, is_demo=True))
    notification = service.history()["notifications"][0]
    assert notification["event"]["capacity_unit"] == "base"
    assert notification["event"]["valuation_currency"] == "USDT"
    assert "valuation_currency: USDT" in notification["message"]
    assert "capacity_unit: base" in notification["message"]
    assert "size_base: 0.01" in notification["message"]
    assert "DEMO / REPLAY" in notification["message"]
    await service.stop()


async def test_nested_source_times_do_not_bypass_deduplication(service, clock):
    service.save_rule(NotificationRule(name="Stable economics", cooldown_seconds=0))
    costs = [{"amount": 1, "fx": {"rate": 1, "at": int(clock() * 1000)}}]
    assert len(await service.evaluate(event(clock, costs=costs))) == 1
    clock.advance(1)
    costs[0]["fx"]["at"] = int(clock() * 1000)
    assert not await service.evaluate(event(clock, costs=costs))
    costs[0]["amount"] = 2
    clock.advance(1)
    assert len(await service.evaluate(event(clock, costs=costs))) == 1
    await service.stop()


async def test_router_crud_validation_history_and_test(service):
    app = FastAPI()
    app.state.notifications = service
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/notifications/rules", json={"name": "UI rule", "min_net_profit": 1})
        assert response.status_code == 200
        rule_id = response.json()["id"]
        assert (await client.get("/api/notifications/rules")).json()["rules"][0]["name"] == "UI rule"
        assert (await client.put(f"/api/notifications/rules/{rule_id}", json={"name": "Edited"})).status_code == 200
        assert (await client.post("/api/notifications/rules", json={"name": "x", "webhook_url": "bad"})).status_code == 422
        assert (await client.post("/api/notifications/test", json={"channel": "telegram"})).status_code == 400
        assert (await client.post("/api/notifications/test", json={"channel": "local"})).status_code == 200
        assert len((await client.get("/api/notifications/history")).json()["notifications"]) == 1
        assert (await client.get("/api/notifications/channels")).json()["channels"][1]["configured"] is False
        assert (await client.delete(f"/api/notifications/rules/{rule_id}")).status_code == 200
        assert (await client.delete(f"/api/notifications/rules/{rule_id}")).status_code == 404
    await service.stop()


async def test_pause_preserves_queued_deliveries(service, clock):
    service.save_rule(NotificationRule(name="Paused"))
    await service.evaluate(event(clock))
    service.set_enabled(False)
    await service.process_pending()
    assert service.history()["deliveries"][0]["status"] == "pending"
    clock.advance(1)
    assert not await service.evaluate(event(clock, id="another"))
    service.set_enabled(True)
    await service.process_pending()
    assert service.history()["deliveries"][0]["status"] == "sent"
    await service.stop()


def test_constructor_main_thread_and_testclient_lifespan(tmp_path):
    from contextlib import asynccontextmanager

    from fastapi.testclient import TestClient

    svc = NotificationService(tmp_path)

    @asynccontextmanager
    async def lifespan(app):
        await svc.start()
        yield
        await svc.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.notifications = svc
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/notifications/rules", json={"name": "Threaded"}).status_code == 200
        assert client.post("/api/notifications/test", json={"channel": "local"}).status_code == 200
        assert len(client.get("/api/notifications/history").json()["notifications"]) == 1
