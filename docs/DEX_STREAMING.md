# Public DEX source WebSockets

Evidence date: 2026-11-04. `adapters/dex_streaming.py` implements public native
Hyperliquid, Lighter and dYdX source sessions alongside their REST adapters. It uses the
same sparse `MarketSnapshot` patch contract as [STREAMING.md](STREAMING.md).
No live exchange connection, exchange heartbeat, recovery history, or 24-hour
acceptance is claimed by this unit's offline tests. Parent-provided observations
must be rechecked against the new broad stream selection before reporting live coverage.

## Integration

```python
from watch_money.adapters.dex_streaming import create_dex_stream

stream = create_dex_stream(rest_adapter)  # HyperliquidAdapter / LighterAdapter / DydxAdapter
if stream:
    async for patch in stream.stream(selected_discovered_instruments):
        supplied = patch.model_dump(exclude_unset=True)
        # Use collector.merge_patch; retain other fields and their provenance.
```

`DexStreamAdapter` exposes `venue`, `unsupported`, `stream(instruments)`, async
`close()`, and optional async `on_payload(venue, transport, payload)`. The callback
receives decoded public JSON, with transport `websocket:hyperliquid` or
`websocket:lighter` / `websocket:dydx`; callback failures are isolated. Connection headers, proxy values,
and URLs are not callback arguments. The official Hyperliquid SDK's optional
non-JSON greeting is ignored, rather than recorded as fabricated JSON.

The caller supplies instruments from successful REST discovery. These REST
adapters do not maintain the CEX adapter's `_markets` map: collection must select
from discovered `Instrument` records instead of requiring that private map.
The stream validates venue, perpetual kind, canonical ID/native symbol relation,
active status and conflicting identities. It cannot independently prove an
arbitrary caller's instrument was returned by discovery. No network discovery or
account operation runs inside the stream constructor or factory.

Hyperliquid and Lighter expose `catalog_stream=True`. When broad collection is
enabled the collector supplies the complete active discovered catalog, rather
than restricting these streams to `watch_assets`. `max_catalog_markets` is 500
for the Hyperliquid catalog wrapper and 5000 for Lighter's local selection map.
The collector records requested, selected and truncated market counts; bypassing
the cap in a direct call raises instead of silently dropping markets. Market
counts are different from topics: 347 Hyperliquid markets require 694 topics;
213 Lighter markets require one topic. `subscription_count` reports topics
planned/sent for the current or most recent session, not server-acknowledged coverage.

Cancel the consuming task before changing the selection. Cancellation closes
the socket and heartbeat/receive tasks. A second concurrent generator is rejected.
A new generator opens a fresh socket, sends fresh subscriptions and resets
session deduplication. Source errors, subscription rejections, handshake timeouts
and heartbeat failures propagate to the collector, which owns retry/backoff and
keeps scheduled REST collection. There is no internal retry or historical replay.

## Hyperliquid

Production endpoint: `wss://api.hyperliquid.xyz/ws`.

The production factory returns `HyperliquidCatalogStream`. Each selected
perpetual subscribes to `l2Book` with `fast:true` and `activeAssetCtx`. Native symbols
such as `xyz:BTC` remain fully qualified; deployer markets and their quote
currencies remain separate. The native complete order book snapshot supplies
positive finite best bid/ask from nonempty positive-size sides, with the
protocol's millisecond `time`. Fast snapshots contain five levels; no significant
figure aggregation is requested. The documented snapshot stream publishes on
blocks at least 0.5 seconds apart; the separate `bbo` channel only publishes
when BBO changes and is still supported by the single-session test wrapper.
Actual source behavior and idle-market cadence still need live verification. Apparent
epoch seconds are rejected rather than converted. A null/empty side supplies no
replacement; the previous side retains its own freshness and can become stale.

`activeAssetCtx` supplies only present mark, oracle index, rolling 24h notional
volume in quote units, OI in base units, and current hourly funding fraction.
Zero funding, volume and OI survive. Context has no documented event timestamp:
all supplied context fields retain null source times and explicit local receipt
times. A simultaneous BBO event never provides time to context/funding. Last
trade, next funding time, and a mid-price masquerading as mark/index are omitted.

The session sends JSON `{"method":"ping"}` every 20 seconds and requires the
JSON pong within 10 seconds. A missing pong fails the session even when other
messages arrive. Control ping/pong frames are also handled by the transport.
The official service closes inactive connections after 60 seconds. The local
selection cap is 500 markets / 1000 topics across at most five 100-market child
connections. This is a total wrapper budget, not 1000 topics per child. The
documented IP limits are 1000 subscriptions, 10 concurrent connections, 30 new
connections/minute and 2000 client messages/minute. At 500 markets all subscription
slots are used; other applications sharing that IP need coordinated headroom.

A persistent 1500-subscription-message / 60-second budget spans all children,
disconnects, `close()` and collector retries, leaving 500 messages/minute for
heartbeats and other traffic. It reserves a whole batch before opening sockets;
the window starts after the final possible subscription send, not before opening.
Partial failures conservatively consume the whole reservation. Budget waits
happen before new sockets open. Injected monotonic clock/wait tests verify retry
bursts, partial failures, delayed sends and pending-reservation wakeups without
real sleeps. The budget coordinates this wrapper, not unrelated processes on an IP.

## Lighter

Production endpoint: `wss://mainnet.zklighter.elliot.ai/stream`.

After the public `connected` handshake, one `market_stats/all` subscription covers
all selected perpetual markets, including `best_bid_price`, `best_ask_price`,
mark/index/last and quote-token daily volume when actually present. Both a map
keyed by market ID and the single-market response shape are parsed. No spot or
binary-options channel is treated as a perpetual feed. Each supplied price uses
the message's real millisecond publication `timestamp`; omitted fields retain
their prior field times and are not refreshed. Publication time is not claimed
to be the last trade's execution time. Missing/seconds/microsecond-looking
publication times stay unknown. JSON server `ping` receives `pong`; announced
shutdowns and failures propagate for fresh root-owned reconnect/resubscription.

The official WebSocket schema distinguishes `current_funding_rate` (upcoming
estimate) from `funding_rate` (last payment at `funding_timestamp`). This unit
could not establish the numeric unit and normalization basis of those WebSocket
fields from explicit official schema descriptions. Both remain raw strings in
`protocol_metrics`, with `funding_unit=unverified`, `funding_rate_basis`, separate
local observation and source publication clocks. Raw settlement timestamps are
retained without conversion; only clear millisecond values also get an `_at_ms`
field. They never supply the timestamp for the upcoming estimate. Canonical
`funding_rate`, kind, interval and next funding fields are not guessed. WebSocket
OI similarly remains raw because its unit was not verified for this field.

The REST `funding-rates` endpoint is a comparative feed; the official agent kit
describes it as 8-hour-equivalent, which is not the native payment interval.
Until its numeric unit is established, the REST adapter also preserves that
value only as an explicitly labelled raw comparative protocol metric. It does
not publish it as a current native canonical funding rate. Hourly payment mechanics
alone do not establish a returned field's numeric unit or basis.

Lighter documents 500 subscriptions/connection, 5000 subscriptions/IP and 200
client messages/minute. This implementation uses one topic, not a per-market
ticker burst. The 5000-market local map cap is a memory bound, not a claim that
5000 perpetual markets exist or that all market counts consume subscription slots.

## dYdX

Production endpoint: `wss://indexer.dydx.trade/v4/ws`.

The session waits for `connected`, then subscribes once to `v4_markets` with
`batched:false` and no market ID. The initial `subscribed.contents.markets`
snapshot and later `channel_data.contents.trading` / `oraclePrices` dictionaries
are separate schemas. All market messages arrive on that feed; only selected
symbols are emitted. Trading deltas never carry forward cached price/funding
fields into a new patch. Optional fields with missing/nonfinite values supply no
numeric replacement.

Initial oracle price maps to index; volume24H remains quote volume, OI remains
base quantity, and `nextFundingRate` remains a predicted hourly funding fraction.
Initial/trading fields have unknown event times. An oracle delta's `effectiveAt`
is used only for that oracle/index field. Block height and connection sequence
are not timestamps. Sequence numbers suppress replayed/older messages when
provided; no undocumented contiguous-sequence guarantee is assumed. Updates
before the initial subscription snapshot or a changed connection identity cause
session failure. Order book deltas, executable depth and last trade are outside
this feed; REST order books remain available.

The server's documented 30-second control ping / 10-second pong requirement is
handled automatically by `websockets`. The client also uses control keepalive
with 20-second ping interval / 10-second timeout. This differs from Hyperliquid's
application JSON heartbeat. Default server subscription limits are per
connection/channel/ID; one initial subscription avoids repeated subscription
bursts. No wallet, subaccount feed or authentication is used.

## Freshness, transport and verification

Exact duplicate patches are suppressed within each source/channel; dated events
older than the last accepted source timestamp are discarded. Future-dated
events do not advance watermarks: collector quality validation can reject them
without blocking subsequent correctly timed updates. Funding/price field
provenance, field receipt time and null source times are preserved independently.
Connected/pong/ack events never emit fake prices or refresh any market field.

`websockets>=15` is imported lazily. Production connections retain verified TLS
with the inherited CA bundle (`SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE`) and inherited
`WSS_PROXY`, then HTTPS/HTTP proxy settings. The implementation does not disable
verification or change routes. The receive buffer is bounded to 16 frames and
2 MiB per message. Slow consumers apply backpressure; this is current market
state collection rather than complete tick retention.

Run:

```sh
.venv/bin/python -m pytest tests/test_dex_catalog_streaming.py tests/test_dex_streaming.py tests/test_dex.py -q
.venv/bin/ruff check backend/watch_money/adapters/dex_streaming.py tests/test_dex_catalog_streaming.py tests/test_dex_streaming.py
```

Deterministic fixtures verify native topics, deployer identity, initial/delta
schemas, sparse fields, zero and nonfinite values, null/independent times,
duplicate/old/future events, disconnect propagation, new-session resubscription,
selection replacement, cancellation cleanup, rejected/malformed subscriptions,
bounded handshake waits, JSON heartbeat success/failure and diagnostic failure
isolation. New catalog fixtures verify 347-market / four-connection / 694-topic
Hyperliquid selection, 213-market / one-topic Lighter selection, all-child cleanup,
global caps, restart budgeting and raw-only updates without canonical promotion.
Real loopback WebSocket sessions for the original protocols verify that
server control pings receive automatic pongs; they do not contact either service.
Factory inspection verifies inherited proxy/TLS/keepalive settings.

Remaining limits: other planned DEX source streams are not implemented here;
quotas shared with unrelated processes, historical recovery and full native
order book reconstruction are not implemented. Current market freshness and
REST fallback remain necessary even with a connected socket. Live deployment,
official service subscription acceptance, unit reconciliation and sustained
24-hour verification require authorized data-host access.

## Official references

- [Hyperliquid subscriptions and field schemas](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
- [Hyperliquid timeouts and JSON heartbeat](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/timeouts-and-heartbeats)
- [Hyperliquid limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)
- [Hyperliquid official Python WebSocket SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/websocket_manager.py)
- [Lighter public WebSocket schemas, timestamp examples and funding field meanings](https://docs.lighter.xyz/api/websocket)
- [Lighter rate limits](https://docs.lighter.xyz/developers/reference/rate-limits)
- [Lighter comparative funding endpoint](https://docs.lighter.xyz/api/funding/funding-rates)
- [Lighter official funding mechanism](https://docs.lighter.xyz/trading/funding)
- [Lighter official agent-kit comparative funding basis](https://github.com/elliottech/lighter-agent-kit/blob/9798b37230d631b4a09a5a28d2436f5405a00992/references/schemas-read.md)
- [Lighter official SDK heartbeat and subscription lifecycle](https://github.com/elliottech/lighter-python/blob/38a34672e7cb43c67d807ece25f125fda2dfdecf/lighter/ws_client.py)
- [dYdX WebSocket protocol and heartbeat](https://docs.dydx.xyz/interaction/data/feeds)
- [dYdX WebSocket API](https://docs.dydx.xyz/indexer-client/websockets)
- [dYdX initial market message](https://docs.dydx.xyz/types/markets_initial_message)
- [dYdX delta market message](https://docs.dydx.xyz/types/markets_update_message)
- [dYdX trading delta fields](https://docs.dydx.xyz/types/trading_perpetual_market)
- [dYdX oracle price and effectiveAt](https://docs.dydx.xyz/types/oracle_price_market)
