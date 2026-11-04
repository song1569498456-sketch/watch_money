# Independent acceptance report

Acceptance owner: independent acceptance agent. Date: 2026-11-04 UTC.

## Verdict

**Full plan NOT ACCEPTED yet.** The implemented local backend passes independent offline checks. Full phase 1 still has incomplete platform capabilities and lacks live/24-hour operational evidence; phase 2 lacks a live recorded sample with manual reconciliation. The local UI passed read-only Chromium navigation and layout checks; persisted demo/detail/notification screenshots were visually inspected. Final independent source-merge regressions pass, including invalid future timestamps and recovery with corrected quotes. Current mocked-notification scope is independently verified, with actual delivery deferred by the user.

Passing fixture tests does not establish live exchange compatibility, delivery to a real chat, or 24-hour operational stability.

## Scope clarification

The user subsequently instructed: **“先不配置，模拟测试”** for notifications. Actual external delivery remains unverified and deferred by that explicit instruction; it is not required for the currently authorized mocked-notification deliverable. No real Feishu/Telegram message was sent by this acceptance agent.

## Acceptance matrix

| Phase | Criterion | Status | Evidence / remaining condition |
|---|---|---|---|
| 1 | Coverage inventory for all named platforms | PASSED | Enumerated all 28 candidates: 12 CEX, 10 perpetual DEX, 6 spot DEX. Inventory alone does not certify support. |
| 1 | Complete requested platform capabilities | FAILED | Drift has a conservative inactive original-program metadata reader requiring RPC; current lifecycle is explicitly unverified and successor migration claims are attributed, not treated as proof of on-chain pause; Jupiter Perps now has public custody/state/borrow metrics, with unclassified reference price kept outside executable price fields. edgeX/Variational/GMX and Jupiter remain partial; all 6 spot DEX are aggregate-only. Native pool verification/executable routes and full-chain discovery are incomplete. See DEX coverage and source documents. |
| 1 | Asset identity, quote currency, contract units | PASSED | Independent identity/FX/multiplier tests; collector rejects wrong-venue snapshots and retires disappeared markets without deleting identity. Live metadata verification remains separate. |
| 1 | Raw funding rate and actual interval normalization | PASSED | 1h funding normalizes to 8h by a factor of 8; absent interval remains null. Field-level stale funding cannot inherit fresh ticker time. |
| 1 | Source provenance, missing fields, stale source timestamps | PASSED | Null/demo/provenance roundtrip; source age dominates receive age; crossed and future snapshot sources invalidated. |
| 1 | Collector rate-limit backoff and recovery | PASSED | Mock HTTP 429 twice, recorded waits 5s/10s, then success and normal 5s polling; no actual wait required. Source WebSocket lifecycle is separately tested below; neither mock test certifies live exchange reconnection. |
| 1 | Source CEX WebSocket lifecycle | PASSED | Independent fake source fails twice, backoff 5s/10s, then reconnects; BTC→ETH subscription change cancels the outstanding receive; stop cancels generator and closes clients. CCXT source stream tests cover sparse/duplicate/out-of-order events and public transport setup. Live source connectivity remains BLOCKED. |
| 1 | REST/WebSocket merge and restored field ages | PASSED | Slow REST quotes cannot overwrite newer stream prices, including legacy snapshots without per-field timestamp maps. A fresh last trade cannot freshen retained old bid/ask midpoint; missing source times retain original field receipt age through SQLite restart. Funding can refresh independently. Retained future-dated quotes stay invalid after unrelated events, and corrected current quotes recover without future-timestamp cache poisoning. |
| 1 | Historical persistence and query | PASSED | Real temporary SQLite/Parquet/DuckDB, restart, audited mappings, settings, latest and as-of history. |
| 1 | Local API and WebSocket integration | PASSED | TestClient health, settings, markets, WS payload, history, mapping audit, favorites, restart. |
| 1 | UI navigation and empty-live state | PASSED | Independent Chromium at 1440×1000: all 5 navigation views, no JS page errors, no document overflow, WS connected, explicit empty live state and no fabricated substitute. Independent screenshot captured at `/tmp/watch_money_acceptance_overview.png`; persisted `docs/screenshots/live-empty.png` and demo/detail/notification screenshots also reviewed. |
| 1 | Populated screenshot evidence | PASSED | Visually inspected `docs/screenshots/demo-overview.png`, `demo-detail.png`, `notifications.png`: explicit demo marks, separate spot/perpetual, missing values retained, standalone local sent record and external channels unconfigured. |
| 1 | Local running frontend/backend integration | PASSED | Actual local port 8000 served the built React UI and API/WebSocket; independently exercised all navigation views. |
| 1 | Windows native startup validation | BLOCKED | PowerShell startup script provided but not executed on Windows in this Linux environment. |
| 1 | Real API verification across covered venues | BLOCKED | Independent requests to Binance time and Hyperliquid info both raise ProxyError. Existing probes: `docs/evidence/live-probes.json`. |
| 1 | Continuous operation for at least 24 hours | BLOCKED | No 24-hour run completed; a short local smoke test cannot satisfy this criterion. |
| 2 | Executable-size VWAP, liquidity, contract conversion | PASSED | Size 1/2/3 produces correct changing VWAP/profit/capacity; insufficient depth has no net; contract multiplier .01 gives .02 base capacity from 2 contracts. |
| 2 | Complete costs, no double counting, explicit unknowns | PASSED | Four opening/closing fees; default funding notional uses fresh mark rather than entry; unknown costs/mark suppress net; exact DEX route price impact/fees embedded and gas deducted once. |
| 2 | Quote conversion, stale inputs, exact asset identity | PASSED | No assumed USDT peg; explicit .99 FX applied; mismatched venue-scoped identities rejected; stale books/funding and malformed depth suppressed. |
| 2 | Funding direction and heterogeneous cycles | PASSED | 8 one-hour long payments and 1 eight-hour short receipt in selected 8h horizon; manually checked -0.6 funding, .4 fees, -1 net. This is a fixture, not a live sample. |
| 2 | Persisted calculations and point-in-time replay | PASSED | Saved sample stored in app-specific directory, survives restart, identical replay, 1ms observation delay hides unavailable books and suppresses result. |
| 2 | Real recorded sample manually reconciled | BLOCKED | No verified live sample available in this environment. |
| 3 | Local notification rules and persistent records | PASSED | API calculation triggers matching local rule; persisted history; disabled computation returns 409. |
| 3 | Scheduled monitor refresh and lifecycle | PASSED | Independent monitor fetches fresh books rather than reusing saved quotes, computes 19.78, then suppresses net and emits invalidation on simulated book outage; plans/results survive restart. |
| 3 | Deduplication, cooldown, failed sends, restart recovery | PASSED | Mock Telegram HTTP429 JSON retry_after=30 persists; restart retains queue/rules/cooldown; no early retry or duplicate; eventual sent state after retry. |
| 3 | Missing/stale/invalid event suppression | PASSED | Null net, >60s source age and valid=false events produce no notification. |
| 3 | Feishu/Telegram simulated protocol delivery | PASSED | Notification module tests use HTTP mock transports; independent queue/retry integration additionally covers Telegram. Actual provider receipt is not claimed. |
| 3 | Actual Feishu delivery | BLOCKED | Deferred by explicit user request; not a blocker for current mocked-notification scope. |
| 3 | Actual Telegram delivery | BLOCKED | Deferred by explicit user request; not a blocker for current mocked-notification scope. |

PASSED means the stated bounded criterion is independently demonstrated. FAILED means a demonstrated defect or missing required capability. BLOCKED means a prerequisite/evidence is unavailable. Partial checks do not imply a completed phase.

## Executed checks

From repository root:

```sh
.venv/bin/python -m pytest tests/test_acceptance.py -q
# 31 passed in 4.28s (one Starlette TestClient deprecation warning)
.venv/bin/python -m pytest -q
# Final integrated suite: 171 passed in 27.52s; evidence inspected in docs/evidence/pytest.txt.
# One upstream Starlette TestClient/httpx deprecation warning; process exit code 0.
```

Independent source probes, without changing network policy:

```python
import httpx
httpx.get('https://api.binance.com/api/v3/time', timeout=10)  # ProxyError
httpx.get('https://api.hyperliquid.xyz/info', timeout=10)     # ProxyError
```

Final evidence freeze: 2026-11-04. Independent acceptance tests: 31 passed; integrated suite: 171 passed, one upstream deprecation warning. Final frontend build and isolated browser run passed; evidence is in `docs/evidence/pytest.txt`, `docs/evidence/browser-run.txt`, and `docs/evidence/browser-smoke.json`. Independent tests belong to `tests/test_acceptance.py`. The integrated `scripts/browser_smoke.py` and `docs/evidence/browser-smoke.json` were also inspected: isolated temporary server, explicit demo toggle, search/favorites, save/replay/monitor creation, local-only notification delivery, desktop/mobile layout, and zero page errors. They use labeled fixtures and temporary stores, not fabricated “live” market data. Rerun these commands after subsequent code changes.

## Defects found and repaired during independent review

- Collector accepted snapshots with another venue's instrument identity. It now rejects unexpected selected-market/venue combinations; regression proves existing data is not overwritten.
- Successfully rediscovered catalogs retained vanished markets as active. Missing entries are now inactive and historical identity survives; independently tested.
- Telegram HTTP429 JSON `parameters.retry_after` was ignored. It now persists the provider delay; restart/early-retry regression passes.
- Calculator endpoint ignored persisted computation/notification switches and emitted events without required source time. API-to-local-rule and disabled-computation tests now pass.
- Saved opportunity storage ignored application-specific data directory. Independent save/restart/replay now uses the selected directory.
- Fresh ticker timestamps made stale funding fields appear current. Field-time checks now suppress such net calculations; independent regression passes.
- DEX CCXT discovery guessed missing contract multiplier as 1. Owner removed the guess; DEX tests now reject missing/zero multipliers and preserve independent funding timestamps.
- Concurrent monitor-lock integration temporarily broke 14 fake-store tests; the fixture was updated to the actual storage contract and the complete 142-test rerun passed.
- Funding break-even skipped normal offsetting simultaneous long/short flows. Independent test now confirms two-hour break-even from grouped 0.2 hourly net flows after 0.4 costs; long notional 200 uses mark despite entry100.

- Added source-stream review found that slow REST responses replaced newer WebSocket fields and unrelated fresh events made old midpoint prices look current. Both defects now have independent passing regressions, including legacy and unknown-source timestamp persistence.

## Remaining acceptance conditions

1. Finish or explicitly defer missing native DEX and chain deployment capabilities; maintain honest per-platform statuses. Owner: main/DEX adapter owners.
2. Verify reachable official public endpoints and preserve genuine raw sample provenance; re-run adapter checks against those samples. Owner: main/adapter owners; network policy/access prerequisite.
3. Validate the provided PowerShell startup on a Windows machine. Actual Linux browser/API integration and screenshots are already verified.
4. Run at least 24 continuous hours with actual monitored data and inspect outages, stale data, retries, storage growth and discovered errors. The provided soak script is tooling, not completed evidence.
5. Reconcile a real saved calculation by hand, including venue fee assumptions and actual settlement semantics. Owner: main/strategy/acceptance.

## Short-run evidence limit

`docs/evidence/short-soak.jsonl` records an HTTP-only local run with collection disabled, zero live snapshots, and idle venues. It can demonstrate API availability only. It provides no evidence of live market stability and does not fulfill the required 24-hour run.

## Final stream-review follow-up

A retained future-dated quote remains invalid after an unrelated fresh last-trade event. The independent regression `test_fresh_stream_trade_does_not_revalidate_retained_future_quotes` also confirms that a corrected current bid/ask replaces invalid future timestamps and restores validity. Both assertions pass. Source metadata lifecycle claims are conservative: original Drift on-chain lifecycle remains unverified, while successor claims are explicitly attributed.
