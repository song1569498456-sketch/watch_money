# Public DEX source WebSockets

Evidence date: 2026-11-04. `adapters/dex_streaming.py` implements public native
Hyperliquid and dYdX source sessions alongside their REST adapters. It uses the
same sparse `MarketSnapshot` patch contract as [STREAMING.md](STREAMING.md).
No live exchange connection, exchange heartbeat, recovery history, or 24-hour
acceptance is claimed. The managed environment permits official GitHub sources
but does not authorize either production data host.

## Integration

```python
from watch_money.adapters.dex_streaming import create_dex_stream

stream = create_dex_stream(rest_adapter)  # HyperliquidAdapter / DydxAdapter, otherwise None
if stream:
    async for patch in stream.stream(selected_discovered_instruments):
        supplied = patch.model_dump(exclude_unset=True)
        # Use collector.merge_patch; retain other fields and their provenance.
```

`DexStreamAdapter` exposes `venue`, `unsupported`, `stream(instruments)`, async
`close()`, and optional async `on_payload(venue, transport, payload)`. The callback
receives decoded public JSON, with transport `websocket:hyperliquid` or
`websocket:dydx`; callback failures are isolated. Connection headers, proxy values,
and URLs are not callback arguments. The official Hyperliquid SDK's optional
non-JSON greeting is ignored, rather than recorded as fabricated JSON.

The caller supplies instruments from successful REST discovery. These REST
adapters do not maintain the CEX adapter's `_markets` map: collection must select
from discovered `Instrument` records instead of requiring that private map.
The stream validates venue, perpetual kind, canonical ID/native symbol relation,
active status and conflicting identities. It cannot independently prove an
arbitrary caller's instrument was returned by discovery. No network discovery or
account operation runs inside the stream constructor or factory.

Cancel the consuming task before changing the selection. Cancellation closes
the socket and heartbeat/receive tasks. A second concurrent generator is rejected.
A new generator opens a fresh socket, sends fresh subscriptions and resets
session deduplication. Source errors, subscription rejections, handshake timeouts
and heartbeat failures propagate to the collector, which owns retry/backoff and
keeps scheduled REST collection. There is no internal retry or historical replay.

## Hyperliquid

Production endpoint: `wss://api.hyperliquid.xyz/ws`.

Each selected perpetual subscribes to `bbo` and `activeAssetCtx`. Native symbols
such as `xyz:BTC` remain fully qualified; deployer markets and their quote
currencies remain separate. `bbo` supplies positive finite best bid/ask from
nonempty positive-size sides, with the protocol's millisecond `time`. Apparent
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
selection cap is 100 markets / 200 topics on one connection; excess selections
raise `StreamUnavailable` for REST fallback rather than silently dropping markets.
This leaves room under the documented shared-IP limits of 1000 subscriptions,
10 concurrent connections, 30 new connections/minute and 2000 sent messages/minute.
It is a local conservative cap, not a global IP quota manager.

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
.venv/bin/python -m pytest tests/test_dex_streaming.py -q
.venv/bin/ruff check backend/watch_money/adapters/dex_streaming.py tests/test_dex_streaming.py
```

Deterministic fixtures verify native topics, deployer identity, initial/delta
schemas, sparse fields, zero and nonfinite values, null/independent times,
duplicate/old/future events, disconnect propagation, new-session resubscription,
selection replacement, cancellation cleanup, rejected/malformed subscriptions,
bounded handshake waits, JSON heartbeat success/failure and diagnostic failure
isolation. Real loopback WebSocket sessions for both protocols verify that
server control pings receive automatic pongs; they do not contact either service.
Factory inspection verifies inherited proxy/TLS/keepalive settings.

Remaining limits: other planned DEX source streams are not implemented here;
global IP quotas, subscription sharding, historical recovery and full native
order book reconstruction are not implemented. Current market freshness and
REST fallback remain necessary even with a connected socket. Live deployment,
official service subscription acceptance, unit reconciliation and sustained
24-hour verification require authorized data-host access.

## Official references

- [Hyperliquid subscriptions and field schemas](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
- [Hyperliquid timeouts and JSON heartbeat](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/timeouts-and-heartbeats)
- [Hyperliquid limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)
- [Hyperliquid official Python WebSocket SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/websocket_manager.py)
- [dYdX WebSocket protocol and heartbeat](https://docs.dydx.xyz/interaction/data/feeds)
- [dYdX WebSocket API](https://docs.dydx.xyz/indexer-client/websockets)
- [dYdX initial market message](https://docs.dydx.xyz/types/markets_initial_message)
- [dYdX delta market message](https://docs.dydx.xyz/types/markets_update_message)
- [dYdX trading delta fields](https://docs.dydx.xyz/types/trading_perpetual_market)
- [dYdX oracle price and effectiveAt](https://docs.dydx.xyz/types/oracle_price_market)
