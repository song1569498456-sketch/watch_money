# Notifications

`watch_money.notifications` exports `router` (prefix `/api/notifications`) and `NotificationService(data_dir)`. Integration creates `app.state.notifications`, calls `await service.start()` during startup and `await service.stop()` during shutdown. Call `service.set_enabled(settings.notifications_enabled)` at startup and when settings change. Disabled services neither evaluate rules nor claim queued deliveries; already running HTTP requests can finish. Collection and notification lifecycles remain independent.

Notifications are local by default. Creating a rule never enables an external channel implicitly. Every triggered notification has a local delivery plus any channels explicitly selected in that rule. No default rules are installed. Opportunity, system-fault and market-discovery rules use separate `category` values.

## API

| Method | Path | Body / result |
|---|---|---|
| GET | `/rules` | `{rules: [...]}` |
| POST | `/rules` | Full rule, optional `id`; returns saved rule (upsert) |
| PUT | `/rules/{id}` | Full replacement; returns saved rule |
| DELETE | `/rules/{id}` | `{deleted: true}`; cancels unsent pending/retry deliveries |
| GET | `/channels` | `{channels: [{channel, configured}]}`; booleans only |
| GET | `/history?limit=100` | `{notifications: [...], deliveries: [...]}`; limit 1–1000 |
| POST | `/test` | `{channel: "local" | "feishu" | "telegram", message?: string}` |
| POST | `/deliveries/{id}/retry` | Requeue an existing failed/retry delivery; resets attempt budget |

All API timestamps are UTC epoch milliseconds. Test responses are `{notification_id, status: "queued"}`; successful enqueue does not claim delivery success. Missing external credentials make explicit tests return HTTP 400. Rule deliveries without credentials end visibly in `failed` / `channel_not_configured`. Rule create/replace rejects unknown fields, including credential fields. Editing a rule resets its observation/cooldown state. Deleting a rule preserves history.

## Rule fields

Required: `name`. Optional `id` defaults to a generated stable identifier.

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Evaluate this rule |
| `category` | `opportunity` | `opportunity`, `system` or `market` |
| `event_types` | `[]` | Empty accepts all types in the category |
| `strategies`, `assets`, `platforms` | `[]` | Empty accepts all; configured filters are combined |
| `channels` | `["local"]` | Local / Feishu / Telegram; duplicates removed |
| `min_net_profit` | null | Minimum net profit in event valuation currency |
| `min_spread`, `min_funding_spread` | null | Minimum signed rates; fractions, not percentages |
| `max_breakeven_hours` | null | Maximum nonnegative payback time |
| `notional` | null | Match requested monetary scale (relative tolerance 1e-6) |
| `min_capacity` | null | Minimum capacity, in the producer's declared capacity unit |
| `max_age_seconds` | 60 | Oldest accepted source event age; missing timestamps fail |
| `duration_seconds` | 0 | Time since first continuously qualifying observation |
| `consecutive_hits` | 1 | Distinct qualifying source timestamps required |
| `cooldown_seconds` | 300 | Minimum enqueue interval for this rule and stable event ID |
| `priority` | `normal` | `low`, `normal` or `high`; high delivery queues first |
| `window_start`, `window_end` | null | Both required together, UTC `HH:MM`, start inclusive / end exclusive |
| `min_change_fraction` | null | Minimum relative change from last sent net profit, spread or funding spread |

Overnight windows are supported; identical start/end means all day. A null/unknown metric never passes a numeric threshold. Ordinary opportunity events with `valid=false` or an incomplete/invalid status are suppressed. Lifecycle rules can explicitly subscribe to `opportunity_changed` and `opportunity_invalidated`. System-fault rules use a fresh fault-event timestamp while describing stale/disconnected market data.

A nonqualifying observation resets the hit run and duplicate latch while preserving cooldown. A gap between source observations longer than `max_age_seconds` resets continuous duration/hits. Repeated reads of one source timestamp do not add hits. An unchanged qualifying payload is suppressed even after cooldown; a changed payload can notify again after cooldown. Source timestamps inside nested costs/FX records are excluded from the fingerprint so observation time alone cannot create duplicate alerts. For `min_change_fraction`, the first notification is allowed, then the first available metric in `net_profit`, `spread`, `funding_spread` is compared against the last sent value.

## Event producer contract

Call `await service.evaluate(event)`; it returns newly created notification IDs. It performs no external network call. Events should include:

```json
{
  "id": "stable:strategy:pair:size",
  "category": "opportunity",
  "type": "opportunity",
  "asset": "BTC",
  "asset_id": "bitcoin",
  "strategy": "spot_perpetual",
  "platforms": ["binance", "okx"],
  "source_at": 1800000000000,
  "valid": true,
  "notional": 1000,
  "capacity": 3000,
  "net_profit": 10,
  "gross_profit": 15,
  "total_cost": 5,
  "spread": 0.02,
  "funding_spread": 0.001,
  "breakeven_hours": 8,
  "holding_hours": 24,
  "is_demo": true
}
```

`source_at` is the underlying data timestamp for opportunity calculations; pass the oldest required input to enforce freshness. `opportunity_id` can override `id`. System/market events can derive a stable fallback identity from category/type/asset/strategy/venue. Use an explicit ID whenever multiple independent conditions share those fields. `assets` matches symbol or asset ID; `platforms` matches any involved platform. Namespace unresolved assets consistently with the main market model.

Optional message context: `direction`, `buy_venue`, `sell_venue`, `next_funding_at`, `funding_kind`, `funding_spread_unit`, `reason`, `message`, `detail_url`, `source`, `issues`, `costs`, `settlements`, `valuation_currency`, `capacity_unit`, `size_base`. Messages contain standalone values and context; a details link is supplemental. Demo/replay flags remain persisted and are prominently included in the message. Capacity units and currency conversions must be established by the producer; this service does not infer them. The strategy producer declares funding spread as `fraction_per_8h` and capacity as `base`.

## Credentials and delivery

Keep private credentials outside the database and repository:

```text
FEISHU_WEBHOOK_URL=
FEISHU_SIGNING_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Feishu supports optional HMAC-SHA256 bot signing (`FEISHU_SECRET` is also accepted as an alias). Telegram uses `sendMessage` with plain text and link previews disabled. Redirects are disabled. HTTP clients have a 15-second timeout. Credential availability is read at delivery time, enabling configuration after a failed attempt followed by an explicit retry.

The SQLite file `notifications.sqlite3` stores rules, observations, cooldowns, local notifications, channel deliveries and per-channel rate limits. Credentials are never stored as channel configuration, returned by channel-status APIs, or included in exception text. Event fields are whitelisted; sensitive nested keys, known environment secret values and provider webhook/bot URLs are redacted from persisted message content. Provider error bodies are never saved or logged. Generic error codes remain visible in history.

Delivery states: `pending`, `sending`, `retry`, `sent`, `failed`, `cancelled`. Each delivery has an attempt count, next-attempt timestamp, generic last error and optional sent timestamp. Start recovers interrupted `sending` records to `pending`. Transient network errors, HTTP 408/425/429/5xx and known provider rate-limit codes retry with exponential waits of 2, 4, 8, 16 and 32 seconds (six total attempts). Server `Retry-After` and Telegram `parameters.retry_after` take precedence if longer. Per-channel dispatch spacing is at least one second by default and survives restart. Permanent HTTP/provider errors are immediately visible as failures. Explicit retries are available through the API.

This is a single-service worker queue. DB transactions are protected for app-factory/TestClient thread changes. A provider may accept a request whose response is lost; external delivery is therefore at least once. Source-event dedupe and durable local enqueue/cooldown prevent ordinary repeated market updates from creating a flood, but cannot promise provider-side exactly-once delivery after a network failure.

## Verification

Run `.venv/bin/python -m pytest tests/test_notifications.py -q`. Tests use `httpx.MockTransport` and a controlled clock for signed Feishu requests, Telegram rate limits, provider rejection, exhausted retries, queue restart, cooldown persistence, local default, explicit channel selection, scope/threshold filtering, stale/missing metrics, hit/duration rules, pause/resume and FastAPI TestClient lifecycle.

Current delivery verification is simulated, as requested. No Feishu or Telegram credentials were configured and no real external messages were sent. Actual reception on the two providers remains unverified until credentials are supplied and explicitly tested.

## 发送前过期复核

规则触发时将 `source_at + max_age_seconds` 作为不可刷新的队列有效期限保存。每次首次发送、重试和崩溃恢复后发送都重新检查；超期记录转为 `expired`，`last_error=event_expired_before_send`，保留原消息和审计但不调用渠道。旧格式队列用原事件时间和规则期限恢复判断，不把重启时间当消息时间。完全未送达的过期机会允许未来新的新鲜观察重新触发；已送达事件继续去重，旧队列不会清掉新通知的去重状态。显式渠道测试消息不作为投资机会设置此到期门槛。
