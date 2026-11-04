# Independent acceptance report

Original acceptance owner: independent acceptance agent. Continuation updated from code inspection, targeted document-audit checks and the main integrator’s frozen evidence. Date: 2026-11-04 UTC.

## Verdict

**Full plan NOT ACCEPTED yet.** The continuation’s bounded offline deliverable passed **563 Python tests, 18 Node tests, TypeScript/Vite build, CI Ruff and diff checks**, plus the final isolated browser suites: **6 baseline, 12 follow-up and 7 fee checks**. Frozen evidence is [followup-validation.json](evidence/followup-validation.json). This includes the asset/best/directed-pair/calculator workflow, scoped public fee defaults and retry-expiry fixes; it does not establish production catalog completeness or execution. Phase 1 still lacks required native capabilities and genuine live/24-hour evidence; phase 2 lacks a genuine recorded sample manually reconciled. External notifications remain mock-only by user request.

Passing fixture tests does not establish live exchange compatibility, delivery to a real chat, or 24-hour operational stability.

## Scope clarification

The user subsequently instructed: **“先不配置，模拟测试”** for notifications. Actual external delivery remains unverified and deferred by that explicit instruction; it is not required for the currently authorized mocked-notification deliverable. No real Feishu/Telegram message was sent by this acceptance agent.

## Acceptance matrix

| Phase | Criterion | Status | Evidence / remaining condition |
|---|---|---|---|
| 1 | Coverage inventory for all named platforms | PASSED | Enumerated all 28 candidates: 12 CEX, 10 perpetual DEX, 6 spot DEX. Inventory alone does not certify support. |
| 1 | Complete requested platform capabilities | FAILED | Drift remains original-program metadata-only with unverified current lifecycle; Jupiter reference price/borrow metrics are distinct from executable market/funding data. Several perpetual DEX remain partial. Explicit-address Uniswap V3 state and Pancake V2/Aerodrome state/exact-input estimates now exist, with factory checks and canonical block pinning, but implementation bytecode/transaction execution are not attested. Curve/Raydium/Orca native reads, V3 size quotes, full pool discovery and complete protocol holding costs remain missing. All six spot discovery paths remain search_limited. |
| 1 | Asset identity, quote currency, contract units | PASSED | Independent identity/FX/multiplier tests; collector rejects wrong-venue snapshots and retires disappeared markets without deleting identity. Live metadata verification remains separate. |
| 1 | Raw funding rate and actual interval normalization | PASSED | 1h funding normalizes to 8h by a factor of 8; absent interval remains null. Field-level stale funding cannot inherit fresh ticker time. |
| 1 | Source provenance, missing fields, stale source timestamps | PASSED | Null/demo/provenance roundtrip; source age dominates receive age; crossed and future snapshot sources invalidated. |
| 1 | Collector rate-limit backoff and recovery | PASSED | Mock HTTP 429 twice, recorded waits 5s/10s, then success and normal 5s polling; no actual wait required. Source WebSocket lifecycle is separately tested below; neither mock test certifies live exchange reconnection. |
| 1 | Source CEX WebSocket lifecycle | PASSED | Independent fake source fails twice, backoff 5s/10s, then reconnects; BTC→ETH subscription change cancels the outstanding receive; stop cancels generator and closes clients. CCXT source stream tests cover sparse/duplicate/out-of-order events and public transport setup. Live source connectivity remains BLOCKED. |
| 1 | Broad full-catalog collection | MOCK TESTED | CEX public catalogs are not filtered to major coins; partial service failure and recovery preserve original identities. CEX and DEX broad interfaces are integrated with default batches of 100 and 30-second waits. Mock follow-up checks cover small assets outside the watchlist, failed-batch advancement and sparse field freshness. A batch cadence is not simultaneous real-time coverage of all markets. Actual catalog completeness/rotation latency remains unverified. |
| 1 | Broad funding collection | MOCK TESTED | CEX uses an independent funding-only task with default 60-second waits, batches up to 100, perpetual-only selection and separate status/freshness. DEX reuses its broad REST payload and reports funding_state=via_light_rest rather than starting another funding task. The full discovered active perpetual catalog is eligible for these respective paths; unsupported fields stay unknown. This does not prove all protocol funding/holding-cost semantics or production freshness. |
| 1 | Funding ranking/matrix and separately stored settled history | MOCK TESTED | GET /api/funding exposes local perpetual observations, identity-gated matrices and per-field validity without presenting snapshots as settled history. A separate history endpoint reads cached records or explicitly refreshes one reviewed-service public page, with source/raw/revision metadata. No automatic full-history backfill, complete cursor coverage or live settled-rate reconciliation is claimed. Final offline regressions passed within the 563-test suite; production evidence remains unavailable. |
| 1 | Native Hyperliquid/dYdX WebSocket lifecycle | MOCK TESTED | Protocol fixtures and actual local loopback sockets cover subscriptions, sparse updates, independent field times, application/control heartbeat, cancellation and resubscription. Integrated factory selects these sessions after discovery. Other DEX native WS, historical recovery and complete source-depth reconstruction remain missing; production hosts are blocked. |
| 1 | EVM native state and single-pool size estimates | MOCK TESTED | tests/test_onchain.py covers registered chain/factory scope, code presence, reverse membership, ABI/decimal/quantity direction, canonical hash pin/reorg rejection and safe errors. V3 is state-only; Pancake V2 and Aerodrome estimates always executable=false. No live RPC, compiled-bytecode attestation, transfer-tax/allowance/gas/transaction simulation or multi-hop execution evidence. |
| 1 | Diagnostic payload recording | MOCK TESTED | Default-off public payload hooks, redaction, UTF-8/total size caps, retention, restart/concurrency and disk-failure isolation are tested. CEX objects are CCXT-normalized, DEX objects are decoded public JSON. This is not complete raw wire/frame capture or a replay parser for every protocol. |
| 1 | Full-catalog identity candidates and native evidence | MOCK TESTED | Pure candidates cover nonmajor assets; immutable native_asset_id survives mappings/restarts. Conflicting chain/address/base-unit evidence blocks comparison despite an explicit same asset mapping. Same ticker, wrapped/bridged tokens and scaled units are not silently equated. Complete official chain/address evidence registration is still missing. |
| 1 | Audited derivative volume and old-cache suppression | MOCK TESTED | REST/light/source WS share volume_units; OKX native base volume/contract counts are distinguished without inverse conversion at current prices. Unreviewed derivative units stay absent. Old CCXT unified-volume-only cache/history fields are suppressed with legacy_volume_unit_unverified instead of retaining an unproven base label. |
| 1 | Browser WebSocket full/delta lifecycle | MOCK TESTED | Initial state uses markets; later markets_delta messages contain upserts/removals, omit age-only churn and preserve stale transitions. Reconnect/mode changes reset full state. Follow-up tests cover actual local WebSocket behavior; full-scale CPU/24h load remains unverified. |
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
| 2 | Native minimum order size and per-leg holding costs | MOCK TESTED | min_amount gates base-unit spot sizes and native perpetual contract quantities, even when depth is in base units. Explicit hourly holding charges apply to both legs at each leg's notional/horizon, convert FX once and remain separate from funding scenarios. Missing short-spot borrow costs still suppress net. These user-assumed costs do not substitute for missing automatic protocol holding models. |
| 2 | Public product/role fee defaults and calculator switch/exit/amount workflow | MOCK TESTED | Explicit unknown/entry/prices/basis UI and quote-budget conversion now have synthetic browser evidence, including prices/basis0 mutually exclusive payloads and insufficient/future/missing depth rejection. Request valuation currency is buy quote, matching the current exit-basis label. Root reports 18 frontend pure tests passed. Public fee profiles and product/role frontend are integrated: 22 standard references with scope guards, unknown Binance perpetual and other unknowns left null, audited user overrides and no account API. Final build and 7 fee-browser checks passed, including leg/product switches; see D06–D10 in AUDIT_BACKLOG.md. |
| 2 | Quote conversion, stale inputs, exact asset identity | PASSED | No assumed USDT peg; explicit .99 FX applied; mismatched venue-scoped identities rejected; stale books/funding and malformed depth suppressed. |
| 2 | Funding direction and heterogeneous cycles | PASSED | 8 one-hour long payments and 1 eight-hour short receipt in selected 8h horizon; manually checked -0.6 funding, .4 fees, -1 net. This is a fixture, not a live sample. |
| 2 | Persisted calculations and point-in-time replay | PASSED | Saved sample stored in app-specific directory, survives restart, identical replay, 1ms observation delay hides unavailable books and suppresses result. |
| 2 | Full-catalog cross-platform opportunity scanning | MOCK TESTED | GET /api/scanner enumerates directed cross-venue pairs and asset summaries/best with field freshness, identity gates and explicit budgets; bounded-prefix ranking is not global. Root reports 96 related tests passed; the inspected 12-group synthetic browser record includes asset expansion and exact ID/strategy calculator handoff without auto-submission. Net remains null/executable=false until separate size/depth/cost computation. Final build and 6/12/7 browser suites including fee UI passed. |
| 2 | Real recorded sample manually reconciled | BLOCKED | No verified live sample available in this environment. |
| 3 | Local notification rules and persistent records | PASSED | API calculation triggers matching local rule; persisted history; disabled computation returns 409. |
| 3 | Scheduled monitor refresh and lifecycle | PASSED | Independent monitor fetches fresh books rather than reusing saved quotes, computes 19.78, then suppresses net and emits invalidation on simulated book outage; plans/results survive restart. |
| 3 | Deduplication, cooldown, failed sends, restart recovery | PASSED | Mock Telegram HTTP429 JSON retry_after=30 persists; restart retains queue/rules/cooldown; no early retry or duplicate; eventual sent state after retry. |
| 3 | Missing/stale/invalid event suppression | PASSED | Null net, >60s source age and valid=false events produce no notification. |
| 3 | Revalidate opportunity freshness immediately before retry/send | MOCK TESTED | Enqueue persists a fixed delivery_expires_at; claim rechecks before every send/retry, records expired without dispatch, and preserves old-schema source age. Root/acceptance report 19 related tests passing, including pending/retry/sending restart after one hour and fresh same-event rearming only when the old latest notification never reached sent. Code and new fixtures were inspected; the final 563-test offline integration also passed. No real provider delivery is claimed. |
| 3 | Feishu/Telegram simulated protocol delivery | PASSED | Notification module tests use HTTP mock transports; independent queue/retry integration additionally covers Telegram. Actual provider receipt is not claimed. |
| 3 | Actual Feishu delivery | BLOCKED | Deferred by explicit user request; not a blocker for current mocked-notification scope. |
| 3 | Actual Telegram delivery | BLOCKED | Deferred by explicit user request; not a blocker for current mocked-notification scope. |

PASSED retains the bounded independent baseline criterion; MOCK TESTED means the continuation’s deterministic adapter/integration/browser evidence passed in the frozen offline suite, with no production compatibility claim. FAILED means a missing required capability or demonstrated defect. BLOCKED means the prerequisite/evidence remains unavailable. Passing these bounded checks does not complete a phase.

## Executed checks

Final continuation evidence is [followup-validation.json](evidence/followup-validation.json): Python 3.12.14, CCXT 4.5.91, FastAPI 0.162.0, Pydantic 2.14.2, pytest 9.2.0 and websockets 17.4.

```sh
.venv/bin/python -m pytest -q
# 563 passed, 1 upstream Starlette warning in 17.71s
.venv/bin/ruff check backend tests scripts --select E4,E7,E9,F
# passed
# frontend/: npm test -> 18 passed; npm run build -> passed
.venv/bin/python scripts/browser_smoke.py
.venv/bin/python scripts/browser_followup.py
# final baseline 6 / follow-up 12 checks passed
# browser_fee.json: root independently reran all 7 fee checks, passed
```

The main integrator ran the final backend/build/browser checks. The document auditor additionally ran only the 9 targeted volume/minimum/identity regressions recorded in AUDIT_BACKLOG.md; all passed, without repeating the full suite. Earlier 171-test/31-independent-test baseline evidence remains historical and is not the current total. Final browser evidence uses isolated stores, synthetic data, loopback/mocked public responses and local-only notifications; it is not a real exchange recording.

Real endpoint probes remain blocked: Binance time and Hyperliquid info raised ProxyError, as recorded in [live-probes.json](evidence/live-probes.json). No network-policy bypass, real external notification or 24-hour real-data run was performed.

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

The live audit source is [AUDIT_BACKLOG.md](AUDIT_BACKLOG.md). Update the matching priority/status/code/tests/evidence/limitations entry after every batch; receiving an issue or adding code does not close it. The catalog counts reported in the parent thread are explicitly attributed there and are not local production samples.

1. Validate with genuine reachable sources the user's first-priority spread workflow: asset grouping → best spread ranking → both directed venue combinations → exact-size calculator with refreshed depth, identity, unit, fee and freshness checks. Prioritize genuine all-asset catalogs/books, identity and stability evidence; funding aggregation is second, simple metrics/history third. Keep existing native/history scope bounded, add no trading accounts. Missing Curve/Raydium/Orca native state, V3 size routes and protocol holding models remain recorded below that priority rather than becoming the next expansion automatically. Owner: main/adapter/strategy owners.
2. Verify reachable official public endpoints and preserve genuine raw sample provenance; re-run adapter checks against those samples. Owner: main/adapter owners; network policy/access prerequisite.
3. Validate the provided PowerShell startup on a Windows machine. Actual Linux browser/API integration and screenshots are already verified.
4. Run at least 24 continuous hours with actual monitored data and inspect outages, stale data, retries, storage growth and discovered errors. The provided soak script is tooling, not completed evidence.
5. Reconcile a real saved calculation by hand, including venue fee assumptions and actual settlement semantics. Owner: main/strategy/acceptance.

## Short-run evidence limit

`docs/evidence/short-soak.jsonl` records an HTTP-only local run with collection disabled, zero live snapshots, and idle venues. It can demonstrate API availability only. It provides no evidence of live market stability and does not fulfill the required 24-hour run.

## Final stream-review follow-up

A retained future-dated quote remains invalid after an unrelated fresh last-trade event. The independent regression `test_fresh_stream_trade_does_not_revalidate_retained_future_quotes` also confirms that a corrected current bid/ask replaces invalid future timestamps and restores validity. Both assertions pass. Source metadata lifecycle claims are conservative: original Drift on-chain lifecycle remains unverified, while successor claims are explicitly attributed.
