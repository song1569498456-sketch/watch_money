# Public CEX source WebSocket collection

`backend/watch_money/adapters/streaming.py` adds a source WebSocket path alongside the CEX REST adapters. The collector can prefer this for watched price updates while continuing scheduled REST collection for funding, OI and other unavailable fields. This is distinct from the local `/ws` connection that pushes collected data from this app to its browser.

## Integration API

```python
from watch_money.adapters.streaming import StreamUnavailable, create_stream

stream = create_stream(rest_adapter)  # None for non-CEX or missing ccxt.pro
if stream is not None:
    try:
        async for patch in stream.stream(selected_discovered_instruments):
            supplied = patch.model_dump(exclude_unset=True)
            # Merge only supplied market fields into the matching stored snapshot.
            # Merge field_sources and field_timestamps dictionaries separately.
            # Keep existing REST funding/OI, their field times and their sources.
            # Retain existing issue context alongside the stream's timing caveats.
            ...
    except StreamUnavailable:
        # Record unavailable source WS capability; keep public REST collection.
        ...
    finally:
        await stream.close()
```

`CexStreamAdapter(rest_adapter, client_factory=...)` permits mocked clients for tests. It exposes `.venue`, `.unsupported` (descriptions of unsupported client/product groups), `stream(instruments)` and asynchronous `close()`. REST discovery must have completed before streaming. Native IDs are resolved through that adapter's discovered map; arbitrary caller IDs and markets belonging to another venue raise. Existing market/currency metadata is shared with the corresponding CCXT Pro client using `set_markets_from_exchange`, so establishing ticker subscriptions does not refetch a currency/account catalog.

Cancel the task consuming `stream` before replacing its instrument list. Generator cancellation closes all source clients and subscriptions; a new call then constructs fresh clients for the replacement selection. Concurrent `stream` calls on the same wrapper are rejected. No application-level retry loop or sleep runs inside this module: source/network errors are raised to the collector, which owns retry counts, backoff and reporting. Explicit `NotSupported` bulk methods fall back to supported individual subscriptions; absence of public ticker capability raises `StreamUnavailable` and never creates a fake patch. CCXT itself handles protocol heartbeat and its version's internal connection behavior; this module does not claim independent native heartbeat validation.

## Subscription grouping and payload semantics

Subscriptions are separated by CCXT service client, spot/perpetual kind, settlement currency and linear/inverse classification. Bulk requests use at most ten symbols per request, conservatively respecting small request-topic limits; this is not a claim that a venue permits unlimited connections or subscriptions. The installed client's limiter and native protocol implementation remain authoritative. When `watchTickers` is unavailable, separate `watch_ticker` workers are used. MEXC spot explicitly uses its individual ticker method because its bulk method rejects spot in inspected CCXT 4.5.91. Binance requests the full ticker stream rather than the default mini ticker to retain public quote fields when that product provides them. Spot/perpetual and regional identities are the same identities established by the REST adapter.

`MarketSnapshot` objects yielded here are **patches**, not complete REST snapshots. Only positive finite bid/ask/last/mark/index prices actually supplied in the unified ticker are set. Rolling 24h volume uses the same audited `volume_units.py` contract as REST. Spot base/quote and verified native derivative units are labelled explicitly; unreviewed derivative volume remains absent. Real zero volume survives. Missing numeric fields are absent from `model_fields_set`; serializing with `exclude_unset=True` is required. A ticker containing only missing/nonfinite prices yields no patch. Funding rates, funding intervals, settlement times and OI are not guessed or copied into a price event. `received_at` and `is_demo=False` are explicitly set.

Each included field has its source method and upstream unified ticker timestamp recorded. Unknown upstream time stays null with a visible issue. CCXT may build a unified ticker from cached constituents or deltas, so that timestamp is a ticker-event time rather than independently verified event time for every price. Every patch includes that uncertainty caveat; consumers must not interpret a source event as proof that every constituent changed simultaneously. Source timestamps are milliseconds; apparent seconds are rejected rather than guessed. Exact duplicate ticker fingerprints are suppressed and events older than the previously emitted source timestamp for that symbol are discarded. A supplied crossed bid/ask pair marks the patch invalid; the collector must also preserve its normal validation of the merged result.

The queue is bounded to twice the requested instrument count, applying backpressure rather than retaining an unbounded message history. It is a current-price path, not a raw tick recording or historical backfill implementation. Initial subscriptions may return a cached snapshot from the upstream CCXT client, but new clients are created after reconnect/reset, avoiding reuse of previous source sessions.

## Runtime and verification

The full `ticker` name override is restricted to Binance spot, USD-M and COIN-M clients. Other clients retain their SDK topic defaults: Bybit single and multiple ticker subscriptions use `tickers.{symbol}` for spot, linear and inverse products. Applying Binance's singular name globally makes otherwise reachable Bybit public subscriptions fail.

Public subscriptions require no account API key. Clients enable rate limiting, `newUpdates`, inherited proxy settings and a verified SSL context honoring `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`. Because aiohttp's WSS environment lookup differs from HTTPS lookup, the module explicitly selects inherited `WSS_PROXY`, then inherited `HTTPS_PROXY`/`HTTP_PROXY`, as CCXT's `wssProxy`. Proxy values and credentials are never written to source metadata or logs by this module. TLS verification remains enabled. The same cloud network restriction described in [CEX_SOURCES.md](CEX_SOURCES.md) still blocks exchange data hosts; no WebSocket connection or heartbeat has been validated live.

Default production ticker hosts, in addition to the REST catalog/handshake hosts in CEX_SOURCES:

| Venue | Production public source hosts |
| --- | --- |
| Binance | `stream.binance.com` (spot, port 9443), `fstream.binance.com` (USD-M), `dstream.binance.com` (COIN-M) |
| OKX | `ws.okx.com` |
| Bybit | `stream.bybit.com` |
| Bitget | `ws.bitget.com` |
| Gate | `api.gateio.ws`, `fx-ws.gateio.ws`, `ws.gate.io` depending on product/client version |
| MEXC | `wbs-api.mexc.com`, `contract.mexc.com` |
| KuCoin | Public bullet handshake returns an endpoint dynamically; current installed source also lists `x-push-spot.kucoin.com` and `x-push-futures.kucoin.com`. Returned endpoints require explicit network authorization; no alternate routing is attempted. Public bullet tokens authenticate a public connection, not a user account. |
| Coinbase | `advanced-trade-ws.coinbase.com` |
| Kraken | `ws.kraken.com`, `futures.kraken.com` |
| HTX | `api.huobi.pro`, `api.hbdm.vn` |
| Deribit | `www.deribit.com` |
| Upbit Korea | `api.upbit.com` |

These are installed-source endpoint destinations; actual negotiated endpoints and protocol access must be verified after network authorization. Sandbox/private endpoints exposed by the library are not used. Idle instruments may not generate events, so the collector should retain its independent REST polling and freshness checks even when the source task is connected.

Evidence date: 2026-11-04. Reviewed installed `ccxt==4.5.91` Pro implementations and exercised deterministic stream fixtures. Tests cover sparse merge semantics, native identity, product grouping, individual-method fallback, runtime bulk fallback, disconnect propagation, fresh reconnect subscriptions, selection changes, cancellation cleanup, duplicate/old events, missing source time and explicit REST-only capability. Installed-client checks require no source connection. This closes the implementation gap for preferring public source WebSockets where supported; live access and the plan's 24-hour acceptance remain outstanding.

Source references are [CCXT Pro source](https://github.com/ccxt/ccxt/tree/v4.5.91/python/ccxt/pro), [Binance public streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams), [OKX public ticker channel](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-tickers-channel), [Bybit public tickers](https://bybit-exchange.github.io/docs/v5/websocket/public/ticker), [Bitget public data](https://www.bitget.com/api-doc/contract/websocket/public/Tickers-Channel), [Gate WebSocket documentation](https://www.gate.com/docs/developers/futures/ws/en/), [MEXC individual spot ticker](https://www.mexc.com/api-docs/spot-v3/websocket-market-streams/individual-symbol-book-ticker-streams), [KuCoin WebSocket introduction](https://www.kucoin.com/docs-new/websocket-api/base-info/introduction), [Coinbase public WebSockets](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview), [Kraken WebSockets](https://docs.kraken.com/api/), [HTX public market data](https://huobiapi.github.io/docs/usdt_swap/v1/en/), [Deribit subscriptions](https://docs.deribit.com/api-reference/subscriptions) and [Upbit public API](https://docs.upbit.com/kr/reference/api-overview). Endpoint references are documentation/source evidence, not live validation.

Run `.venv/bin/python -m pytest tests/test_streaming.py -q` and `.venv/bin/ruff check backend/watch_money/adapters/streaming.py tests/test_streaming.py`.

## Browser transport

The local `/ws` sends an initial `type=markets` full state, then `type=markets_delta` upserts and removed ID lists. Age-only changes are omitted; field stale transitions still transmit. A mode switch or reconnect sends a new full state. This reduces unchanged-catalog bandwidth; the server still builds a current view per connection, and full-scale CPU/24h load remains unverified.
