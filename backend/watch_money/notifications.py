"""Persistent local notifications and explicitly selected webhook deliveries.

No credentials are part of the API or database. External delivery is at least once:
providers may accept a request whose response was lost before a retry.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import sqlite3
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

Channel = Literal["local", "feishu", "telegram"]
router = APIRouter(prefix="/api/notifications", tags=["notifications"])
logger = logging.getLogger(__name__)


class NotificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    category: Literal["opportunity", "system", "market"] = "opportunity"
    event_types: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=lambda: ["local"], min_length=1)
    min_net_profit: float | None = None
    min_spread: float | None = None
    min_funding_spread: float | None = None
    max_breakeven_hours: float | None = Field(default=None, ge=0)
    notional: float | None = Field(default=None, gt=0)
    min_capacity: float | None = Field(default=None, ge=0)
    max_age_seconds: float = Field(default=60, gt=0)
    duration_seconds: float = Field(default=0, ge=0)
    consecutive_hits: int = Field(default=1, ge=1)
    cooldown_seconds: float = Field(default=300, ge=0)
    priority: Literal["low", "normal", "high"] = "normal"
    window_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    window_end: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    min_change_fraction: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def complete_window(self):
        if bool(self.window_start) != bool(self.window_end):
            raise ValueError("window_start and window_end must be provided together (UTC)")
        self.channels = list(dict.fromkeys(self.channels))
        return self


class TestNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Channel = "local"
    message: str = Field(default="Watch Money notification channel test", min_length=1, max_length=2000)


# Whitelisting keeps credentials and opaque provider payloads out of persisted events.
_EVENT_FIELDS = {
    "id", "opportunity_id", "type", "category", "asset", "asset_id", "strategy", "platforms", "venue",
    "buy_venue", "sell_venue", "buy_instrument_id", "sell_instrument_id", "direction", "notional",
    "capacity", "net_profit", "gross_profit", "total_cost", "spread", "funding_spread", "breakeven_hours",
    "holding_hours", "next_funding_at", "source_at", "received_at", "reason", "detail_url", "message",
    "status", "is_demo", "funding_kind", "costs", "settlements", "source", "valid", "issues", "title",
    "capacity_unit", "valuation_currency", "size_base",
    "funding_spread_unit",
}


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        for key in ("FEISHU_WEBHOOK_URL", "FEISHU_SIGNING_SECRET", "FEISHU_SECRET", "TELEGRAM_BOT_TOKEN"):
            secret = os.getenv(key)
            if secret:
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"https?://[^\s]*?(?:/bot[^/\s]+|/open-apis/bot/v2/hook/[^\s]+)", "[redacted]", value)
        return value[:6000]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()
                if not any(x in str(k).lower() for x in ("secret", "token", "password", "webhook", "credential"))}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value[:100]]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:200]


def _number(event: dict, name: str) -> float | None:
    value = event.get(name)
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _fingerprint_value(value):
    """Observation times may advance without changing the economic opportunity."""
    if isinstance(value, dict):
        return {key: _fingerprint_value(item) for key, item in value.items()
                if key not in {"at", "source_at", "received_at", "as_of", "known_at"}}
    if isinstance(value, list):
        return [_fingerprint_value(item) for item in value]
    return value


def _locked(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._db_lock:
            return method(self, *args, **kwargs)
    return guarded


class NotificationService:
    def __init__(self, data_dir: str | Path, *, client: httpx.AsyncClient | None = None,
                 clock=None, poll_seconds: float = .5, min_send_interval: float = 1):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "notifications.sqlite3"
        self.clock = clock or time.time
        self.poll_seconds = poll_seconds
        self.min_send_interval = min_send_interval
        self.client = client
        self._owns_client = client is None
        self._worker: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._db_lock = RLock()
        self.enabled = True
        self.db: sqlite3.Connection | None = None
        self._connect()

    @_locked
    def _connect(self):
        if self.db is not None:
            return
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rule_state(
            rule_id TEXT NOT NULL,event_id TEXT NOT NULL,first_hit REAL NOT NULL,hits INTEGER NOT NULL,
            last_sample TEXT,last_sent REAL,last_fingerprint TEXT,last_value REAL,
            PRIMARY KEY(rule_id,event_id));
        CREATE TABLE IF NOT EXISTS notifications(
            id TEXT PRIMARY KEY,rule_id TEXT,event_id TEXT NOT NULL,created_at INTEGER NOT NULL,
            title TEXT NOT NULL,message TEXT NOT NULL,priority TEXT NOT NULL,event TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(
            id TEXT PRIMARY KEY,notification_id TEXT NOT NULL,channel TEXT NOT NULL,
            status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at REAL NOT NULL,
            last_error TEXT,sent_at INTEGER);
        CREATE INDEX IF NOT EXISTS delivery_due ON deliveries(status,next_attempt_at);
        CREATE TABLE IF NOT EXISTS channel_limits(channel TEXT PRIMARY KEY,next_at REAL NOT NULL);
        """)
        self.db.commit()

    async def start(self):
        if self._worker and not self._worker.done():
            return
        self._connect()
        self.db.execute("UPDATE deliveries SET status='pending' WHERE status='sending'")
        self.db.commit()
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=15, follow_redirects=False)
        self._worker = asyncio.create_task(self._run(), name="notification-delivery")

    async def stop(self):
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        if self._owns_client and self.client:
            await self.client.aclose()
            self.client = None
        if self.db:
            self.db.close()
            self.db = None

    def channels(self) -> list[dict]:
        return [{"channel": "local", "configured": True},
                {"channel": "feishu", "configured": bool(os.getenv("FEISHU_WEBHOOK_URL"))},
                {"channel": "telegram", "configured": bool(os.getenv("TELEGRAM_BOT_TOKEN") and
                                                           os.getenv("TELEGRAM_CHAT_ID"))}]

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        self._wake.set()

    @_locked
    def rules(self) -> list[dict]:
        self._connect()
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM rules ORDER BY id")]

    @_locked
    def save_rule(self, rule: NotificationRule) -> dict:
        self._connect()
        body = _clean(rule.model_dump())
        self.db.execute("INSERT OR REPLACE INTO rules VALUES (?,?)", (rule.id, json.dumps(body)))
        # Changing thresholds or scope starts a fresh observation, while pending sends remain auditable.
        self.db.execute("DELETE FROM rule_state WHERE rule_id=?", (rule.id,))
        self.db.commit()
        return body

    @_locked
    def delete_rule(self, rule_id: str) -> bool:
        self._connect()
        found = self.db.execute("DELETE FROM rules WHERE id=?", (rule_id,)).rowcount
        self.db.execute("DELETE FROM rule_state WHERE rule_id=?", (rule_id,))
        # Explicit deletion cancels any unsent deliveries for this rule.
        self.db.execute("""UPDATE deliveries SET status='cancelled',last_error='rule_deleted'
            WHERE status IN ('pending','retry') AND notification_id IN
            (SELECT id FROM notifications WHERE rule_id=?)""", (rule_id,))
        self.db.commit()
        return bool(found)

    @_locked
    def history(self, limit: int = 100) -> dict:
        self._connect()
        limit = max(1, min(limit, 1000))
        notifications = [dict(row) for row in self.db.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,))]
        for row in notifications:
            row["event"] = json.loads(row["event"])
        deliveries = [dict(row) for row in self.db.execute("""SELECT d.* FROM deliveries d
            JOIN (SELECT id FROM notifications ORDER BY created_at DESC,rowid DESC LIMIT ?) n
            ON d.notification_id=n.id ORDER BY d.rowid DESC""", (limit,))]
        for row in deliveries:
            row["next_attempt_at"] = int(row["next_attempt_at"] * 1000)
        return {"notifications": notifications, "deliveries": deliveries}

    def _matches(self, rule: NotificationRule, event: dict, now: float) -> bool:
        if not rule.enabled or rule.category != event.get("category", "opportunity"):
            return False
        if rule.event_types and event.get("type") not in rule.event_types:
            return False
        if rule.strategies and event.get("strategy") not in rule.strategies:
            return False
        if rule.assets and not set(rule.assets).intersection({event.get("asset"), event.get("asset_id")}):
            return False
        platforms = event.get("platforms") or [event.get("venue"), event.get("buy_venue"), event.get("sell_venue")]
        if rule.platforms and not set(rule.platforms).intersection(platforms):
            return False
        if rule.window_start:
            current = datetime.fromtimestamp(now, UTC).strftime("%H:%M")
            start, end = rule.window_start, rule.window_end
            in_window = start <= current < end if start < end else current >= start or current < end
            if not in_window:
                return False
        stamp = _number(event, "source_at")
        if stamp is None or stamp > (now + 5) * 1000 or now - stamp / 1000 > rule.max_age_seconds:
            return False
        # Fault and lifecycle events are intentionally permitted to describe invalid/stale data.
        if rule.category == "opportunity" and event.get("type") not in (
                "opportunity_invalidated", "opportunity_changed") and event.get("valid") is False:
            return False
        if (rule.category == "opportunity" and event.get("type") not in (
                "opportunity_invalidated", "opportunity_changed") and event.get("status") in (
                "data_pending", "cost_pending", "invalid", "expired", "data_incomplete",
                "costs_missing", "invalidated")):
            return False
        for field, minimum in (("net_profit", rule.min_net_profit), ("spread", rule.min_spread),
                               ("funding_spread", rule.min_funding_spread), ("capacity", rule.min_capacity)):
            if minimum is not None:
                value = _number(event, field)
                if value is None or value < minimum:
                    return False
        if rule.max_breakeven_hours is not None:
            value = _number(event, "breakeven_hours")
            if value is None or value < 0 or value > rule.max_breakeven_hours:
                return False
        if rule.notional is not None:
            value = _number(event, "notional")
            if value is None or not math.isclose(value, rule.notional, rel_tol=1e-6):
                return False
        return True

    async def evaluate(self, event: dict) -> list[str]:
        """Observe one source event. Re-reading a source timestamp never adds another hit."""
        if not self.enabled:
            return []
        return self._evaluate(event)

    @_locked
    def _evaluate(self, event: dict) -> list[str]:
        self._connect()
        event = _clean({k: v for k, v in event.items() if k in _EVENT_FIELDS})
        event_id = str(event.get("opportunity_id") or event.get("id") or "")
        if not event_id:
            # Stable identity fallback supports system and newly discovered market events.
            identity = [event.get(k) for k in ("category", "type", "asset_id", "asset", "strategy", "venue")]
            if not any(identity):
                raise ValueError("notification event needs a stable id or scoped identity")
            event_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        now = self.clock()
        created = []
        for raw in self.rules():
            rule = NotificationRule.model_validate(raw)
            state = self.db.execute("SELECT * FROM rule_state WHERE rule_id=? AND event_id=?",
                                    (rule.id, event_id)).fetchone()
            if not self._matches(rule, event, now):
                # Keep durable cooldown, but clear the consecutive run and deduplication latch.
                if state:
                    self.db.execute("""UPDATE rule_state SET first_hit=?,hits=0,last_sample=NULL,
                        last_fingerprint=NULL WHERE rule_id=? AND event_id=?""", (now, rule.id, event_id))
                continue
            sample = str(event.get("source_at"))
            if state and state["last_sample"] == sample:
                continue
            previous_sample = _number({"sample": state["last_sample"]}, "sample") if state else None
            gap = previous_sample is not None and (float(sample) - previous_sample) / 1000 > rule.max_age_seconds
            hits = (state["hits"] if state and not gap else 0) + 1
            first_hit = state["first_hit"] if state and state["hits"] and not gap else now
            last_sent = state["last_sent"] if state else None
            fingerprint_event = _fingerprint_value(event)
            fingerprint = hashlib.sha256(json.dumps(fingerprint_event, sort_keys=True).encode()).hexdigest()
            self.db.execute("""INSERT INTO rule_state(rule_id,event_id,first_hit,hits,last_sample)
                VALUES (?,?,?,?,?) ON CONFLICT(rule_id,event_id) DO UPDATE SET
                first_hit=excluded.first_hit,hits=excluded.hits,last_sample=excluded.last_sample""",
                            (rule.id, event_id, first_hit, hits, sample))
            if hits < rule.consecutive_hits or now - first_hit < rule.duration_seconds:
                continue
            if last_sent is not None and now - last_sent < rule.cooldown_seconds:
                continue
            if state and state["last_fingerprint"] == fingerprint:
                continue
            current_value = next((_number(event, k) for k in ("net_profit", "spread", "funding_spread")
                                  if _number(event, k) is not None), None)
            if rule.min_change_fraction is not None and state and state["last_fingerprint"] is not None:
                previous = state["last_value"]
                if current_value is None or previous is None:
                    continue
                change = abs(current_value - previous) / max(abs(previous), 1e-12)
                if change < rule.min_change_fraction:
                    continue
            title = f"{rule.name}: {event.get('asset') or event.get('asset_id') or event.get('venue') or 'Market'}"
            message = self._format_message(title, event)
            notification_id = self._enqueue(rule.id, event_id, title, message, rule.priority, event, rule.channels)
            self.db.execute("""UPDATE rule_state SET last_sent=?,last_fingerprint=?,last_value=?
                WHERE rule_id=? AND event_id=?""", (now, fingerprint, current_value, rule.id, event_id))
            created.append(notification_id)
        self.db.commit()
        if created:
            self._wake.set()
        return created

    def _format_message(self, title: str, event: dict) -> str:
        lines = [title]
        if event.get("is_demo"):
            lines.append("DEMO / REPLAY — not live market data")
        for key in ("strategy", "direction", "platforms", "buy_venue", "sell_venue", "valuation_currency",
                    "size_base", "notional", "capacity", "capacity_unit", "gross_profit",
                    "total_cost", "net_profit", "spread", "funding_spread", "funding_spread_unit",
                    "funding_kind", "breakeven_hours",
                    "holding_hours", "next_funding_at", "source_at", "source", "costs", "settlements",
                    "reason", "message", "issues"):
            if event.get(key) is not None:
                lines.append(f"{key}: {event[key]}")
        lines.append(f"trigger: {event.get('type', 'threshold')}")
        # The body always contains standalone numbers and context, even if the link is local-only.
        if event.get("detail_url"):
            lines.append(f"details: {event['detail_url']}")
        return _clean("\n".join(lines))[:4000]

    def _enqueue(self, rule_id, event_id, title, message, priority, event, channels) -> str:
        notification_id = uuid4().hex
        now = self.clock()
        self.db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?)", (
            notification_id, rule_id, event_id, int(now * 1000), _clean(title), _clean(message), priority,
            json.dumps(_clean(event))))
        # Every delivery is represented in the local center, with explicit channel outcomes.
        for channel in dict.fromkeys(["local", *channels]):
            self.db.execute("INSERT INTO deliveries(id,notification_id,channel,status,next_attempt_at) VALUES (?,?,?,?,?)",
                            (uuid4().hex, notification_id, channel, "pending", now))
        return notification_id

    async def test(self, request: TestNotification) -> dict:
        return self._test(request)

    @_locked
    def _test(self, request: TestNotification) -> dict:
        self._connect()
        if not next(x["configured"] for x in self.channels() if x["channel"] == request.channel):
            raise ValueError(f"{request.channel} channel credentials are not configured")
        event = {"type": "test", "category": "system", "source_at": int(self.clock() * 1000)}
        notification_id = self._enqueue(None, uuid4().hex, "Watch Money channel test", request.message,
                                        "normal", event, [request.channel])
        self.db.commit()
        self._wake.set()
        return {"notification_id": notification_id, "status": "queued"}

    @_locked
    def retry_delivery(self, delivery_id: str) -> bool:
        self._connect()
        changed = self.db.execute("""UPDATE deliveries SET status='pending',attempts=0,
            next_attempt_at=?,last_error=NULL WHERE id=? AND status IN ('failed','retry')""",
                                  (self.clock(), delivery_id)).rowcount
        self.db.commit()
        if changed:
            self._wake.set()
        return bool(changed)

    async def _run(self):
        while True:
            try:
                await self.process_pending()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate worker failures; never log exception bodies.
                logger.error("Notification worker failure: %s", type(exc).__name__)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def process_pending(self, limit: int = 50):
        """Process a bounded batch; public for deterministic queue/replay verification."""
        if not self.enabled:
            return
        rows = self._due_rows(limit)
        for row in rows:
            if not self.enabled:
                break
            channel = row["channel"]
            if not self._claim(row):
                continue
            try:
                retry, error, retry_after = await self._send(channel, row["message"])
            except asyncio.CancelledError:
                # Remains 'sending', and start() recovers it on restart.
                raise
            except (httpx.TransportError, TimeoutError):
                retry, error, retry_after = True, "transport_error", None
            except Exception as exc:  # noqa: BLE001 - isolate provider failures and sanitize diagnostics.
                logger.error("Notification delivery failure: %s", type(exc).__name__)
                retry, error, retry_after = True, "delivery_error", None
            self._finish(row, retry, error, retry_after)

    @_locked
    def _due_rows(self, limit):
        self._connect()
        return self.db.execute("""SELECT d.*,n.title,n.message,n.priority FROM deliveries d
            JOIN notifications n ON n.id=d.notification_id WHERE d.status IN ('pending','retry')
            AND d.next_attempt_at<=? ORDER BY CASE n.priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
            d.next_attempt_at,d.rowid LIMIT ?""", (self.clock(), limit)).fetchall()

    @_locked
    def _claim(self, row):
        now = self.clock()
        channel = row["channel"]
        if channel != "local":
            rate = self.db.execute("SELECT next_at FROM channel_limits WHERE channel=?", (channel,)).fetchone()
            if rate and rate[0] > now:
                self.db.execute("UPDATE deliveries SET next_attempt_at=? WHERE id=?", (rate[0], row["id"]))
                self.db.commit()
                return False
        claimed = self.db.execute("""UPDATE deliveries SET status='sending',attempts=attempts+1
            WHERE id=? AND status IN ('pending','retry') AND attempts=?""", (row["id"], row["attempts"])).rowcount
        if claimed and channel != "local":
            self.db.execute("INSERT OR REPLACE INTO channel_limits VALUES (?,?)",
                            (channel, now + self.min_send_interval))
        self.db.commit()
        return bool(claimed)

    @_locked
    def _finish(self, row, retry, error, retry_after):
        now = self.clock()
        attempts = row["attempts"] + 1
        next_attempt = now + max(self.min_send_interval, retry_after or 0)
        if row["channel"] != "local":
            self.db.execute("INSERT OR REPLACE INTO channel_limits VALUES (?,?)", (row["channel"], next_attempt))
        if error is None:
            self.db.execute("UPDATE deliveries SET status='sent',sent_at=?,last_error=NULL WHERE id=?",
                            (int(now * 1000), row["id"]))
        elif retry and attempts < 6:
            delay = max(retry_after or 0, min(3600, 2 ** attempts), self.min_send_interval)
            self.db.execute("UPDATE deliveries SET status='retry',last_error=?,next_attempt_at=? WHERE id=?",
                            (error, now + delay, row["id"]))
        else:
            self.db.execute("UPDATE deliveries SET status='failed',last_error=? WHERE id=?",
                            (error, row["id"]))
        self.db.commit()

    async def _send(self, channel: Channel, message: str) -> tuple[bool, str | None, float | None]:
        if channel == "local":
            return False, None, None
        configured = next(x["configured"] for x in self.channels() if x["channel"] == channel)
        if not configured:
            return False, "channel_not_configured", None
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=15, follow_redirects=False)
        if channel == "feishu":
            url = os.environ["FEISHU_WEBHOOK_URL"]
            payload = {"msg_type": "text", "content": {"text": message}}
            secret = os.getenv("FEISHU_SIGNING_SECRET") or os.getenv("FEISHU_SECRET")
            if secret:
                stamp = str(int(self.clock()))
                signing_key = f"{stamp}\n{secret}".encode()
                payload.update(timestamp=stamp, sign=base64.b64encode(
                    hmac.new(signing_key, b"", hashlib.sha256).digest()).decode())
        else:
            url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
            payload = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": message,
                       "disable_web_page_preview": True}
        response = await self.client.post(url, json=payload)
        retry_after = None
        try:
            body = response.json()
            if not isinstance(body, dict):
                body = {}
        except ValueError:
            body = None
        header = response.headers.get("retry-after")
        if header:
            try:
                retry_after = max(0, float(header))
            except ValueError:
                try:
                    retry_after = max(0, parsedate_to_datetime(header).timestamp() - self.clock())
                except (TypeError, ValueError, OverflowError):
                    pass
        if channel == "telegram" and body:
            provider_wait = body.get("parameters", {}).get("retry_after")
            if isinstance(provider_wait, (int, float)) and math.isfinite(provider_wait):
                retry_after = max(retry_after or 0, provider_wait)
        if response.status_code == 429 or response.status_code >= 500 or response.status_code in (408, 425):
            return True, f"http_{response.status_code}", retry_after
        if not response.is_success:
            return False, f"http_{response.status_code}", None
        if body is None:
            return True, "invalid_provider_response", None
        if channel == "telegram":
            if body.get("ok") is True:
                return False, None, None
            code = body.get("error_code")
            wait = body.get("parameters", {}).get("retry_after")
            wait = float(wait) if isinstance(wait, (int, float)) and math.isfinite(wait) else None
            return code == 429 or isinstance(code, int) and code >= 500, "telegram_rejected", wait
        code = body.get("code", body.get("StatusCode"))
        if code == 0:
            return False, None, None
        # Feishu rate limits can be delivered in a successful HTTP response.
        return code in (9499, 99991400, 99991401), "feishu_rejected", retry_after


def _service(request: Request) -> NotificationService:
    return request.app.state.notifications


@router.get("/channels")
async def channels(request: Request):
    return {"channels": _service(request).channels()}


@router.get("/rules")
async def rules(request: Request):
    return {"rules": _service(request).rules()}


@router.post("/rules")
async def save_rule(rule: NotificationRule, request: Request):
    return _service(request).save_rule(rule)


@router.put("/rules/{rule_id}")
async def replace_rule(rule_id: str, rule: NotificationRule, request: Request):
    rule.id = rule_id
    return _service(request).save_rule(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, request: Request):
    if not _service(request).delete_rule(rule_id):
        raise HTTPException(404, "Notification rule not found")
    return {"deleted": True}


@router.get("/history")
async def history(request: Request, limit: int = Query(default=100, ge=1, le=1000)):
    return _service(request).history(limit)


@router.post("/test")
async def test_notification(body: TestNotification, request: Request):
    try:
        return await _service(request).test(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id: str, request: Request):
    if not _service(request).retry_delivery(delivery_id):
        raise HTTPException(404, "Retryable delivery not found")
    return {"status": "queued"}
