# CEX public data adapters

All twelve requested CEX have a credential-free asynchronous REST adapter. `build_cex_adapters()` constructs them without importing CCXT or opening connections; clients are created on first discovery. This is implementation coverage, **not evidence of successful live access**.

Evidence recorded on 2026-11-04: reviewed parser implementations in the downloaded `ccxt==4.5.91` dependency wheel, retrieved official documentation through the web research tool and executed deterministic fixtures, including native contract units. Official documentation pages for all twelve venues were opened; targeted field checks confirmed Bybit fundingInterval minutes, OKX OI contract/coin/USD distinctions, Bitget interval hours and base-coin OI, KuCoin granularity fields, Binance fundingInfo adjustment metadata, HTX adjacent funding times and Deribit native quantity units. Some old documentation URLs now redirect to broad catalogs; a retrieved overview does not establish every endpoint's current behavior. Live exchange calls remain unverified because the cloud execution network permits dependency/GitHub hosts only. `last_verified_at` therefore remains null, and every coverage row says `implemented_unverified_live`. A successful fixture or documentation fetch does not establish present exchange availability or regional access.

## Products and services

| Venue | Distinct client/service identity | Public catalog and market endpoints | Funding and OI treatment |
| --- | --- | --- | --- |
| Binance | `binance` global spot, `binanceusdm` USD-M, `binancecoinm` COIN-M | Spot `/api/v3/exchangeInfo`, `/api/v3/ticker/24hr`, `/api/v3/depth`; derivative `/fapi/v1` and `/dapi/v1` exchangeInfo, ticker/24hr, depth | Premium index/current rate plus one separate fundingInfo adjustment-list request per derivative client/poll when supported. Explicit fundingIntervalHours or unified interval carrying a unit; symbols absent from the adjustment list remain unknown. USD-M OI amount is base; COIN-M amount is contracts. Binance.US is not substituted. |
| OKX | `okx`, global API | `/api/v5/public/instruments`, `/api/v5/market/ticker`, `/api/v5/market/books` | `/api/v5/public/funding-rate` and `/api/v5/public/open-interest`; adjacent fundingTime/nextFundingTime establish actual interval. OI amount is contracts. |
| Bybit | `bybit`, categories spot/linear/inverse | `/v5/market/instruments-info`, `/v5/market/tickers`, `/v5/market/orderbook` | Current estimate from ticker; native instrument fundingInterval is minutes. Native metadata precedes CCXT interval, preserving fractional hours. Linear OI is base; inverse OI value uses original quote. |
| Bitget | `bitget`, spot and mix perpetual services | Spot `/api/v2/spot/public/symbols`, `/api/v2/spot/market/tickers`, `/api/v2/spot/market/orderbook`; mix contracts/ticker/merge-depth | `/api/v2/mix/market/current-fund-rate`, funding-time and open-interest through unified API. ratePeriod/fundingRateInterval/fundInterval hours; linear OI amount is base. Unverified inverse OI amount is omitted. |
| Gate | `gate` (legacy CCXT `gateio` fallback), spot and perpetual | `/api/v4/spot/currency_pairs`, tickers/order_book; `/api/v4/futures/{settle}/contracts`, tickers/order_book | Native funding_interval seconds. Current funding_rate retained; funding_rate_indicative is a separate prediction and is not substituted when current is present. OI amount is contracts. |
| MEXC | `mexc`, spot and supported swaps | Spot `/api/v3/exchangeInfo`, ticker/24hr, depth; contract `/api/v1/contract/detail`, ticker, depth | collectCycle hours when provided. Installed CCXT applies a multiplier to its unified volume, but this implementation withholds an unaudited derivative volume unit rather than trusting the unified field name alone. OI omitted unless a reviewed public unit is available. |
| KuCoin | `kucoin` spot and `kucoinfutures` independent futures API | Spot `/api/v2/symbols`, market/stats and orderbook; futures `/api/v1/contracts/active`, ticker and level2 depth | `/api/v1/funding-rate/{symbol}/current`; granularity/currentGranularity/fundingRateGranularity milliseconds. Raw nextFundingRate is classified predicted. Unified nextFundingTimestamp can indicate a future granularity change and is not used as the current rate's settlement time. OI amount is contracts. |
| Coinbase | `coinbase` Advanced Trade public spot | `/api/v3/brokerage/market/products`, public product ticker and product_book | Spot only. US/International derivatives and Coinbase Exchange are separate services; their availability is not implied. No authentication fallbacks. |
| Kraken | `kraken` spot; `krakenfutures` independent derivatives service | Spot `/0/public/AssetPairs`, Ticker, Depth; futures `/derivatives/api/v3/instruments`, tickers, orderbook | CCXT converts native futures funding cash amount to fractional rate using mark price and reports 1h explicitly. OI is missing when the installed unified client does not expose a verified method/unit. |
| HTX | `htx`, spot/linear/inverse perpetual | Spot `/v1/common/symbols`, `/market/detail/merged`, `/market/depth`; linear/swap public contract catalogs and market data | Adjacent funding_time/next_funding_time give actual interval. OI volume/CCXT amount is contracts; do not interpret its deprecated baseVolume alias as base units. |
| Deribit | `deribit`, spot and perpetual | `/api/v2/public/get_instruments`, ticker, get_order_book | CCXT funding method explicitly queries the trailing 8h realized funding value. It is labelled settled with an explicit continuous-accrual caveat; this is a historical window, not a discrete next-payment schedule. Native depth quantities are base (linear) or quote notional (inverse); divide by contract_size and emit contracts. Inverse OI value uses quote currency. |
| Upbit | `upbit`, **Korea API** | `/v1/market/all`, ticker, orderbook | Spot only. Original KRW/BTC/USDT quote currency retained. Singapore/Indonesia/Thailand services require separate explicit regional identities and are not claimed. |

The exact transport requests and endpoint weights are delegated to the installed CCXT implementation. Exchange-specific endpoint changes after the inspected version require fresh live validation. Dated futures, options and inactive instruments are excluded; a perpetual missing its multiplier or linear/inverse classification is excluded instead of receiving a guessed contract specification. The logical venue ID groups its supported clients, while instrument IDs include the client identity, product kind and escaped native market ID.

Official references:

- [Binance](https://developers.binance.com/docs/derivatives/Introduction)
- [OKX](https://www.okx.com/docs-v5/en/)
- [Bybit](https://bybit-exchange.github.io/docs/v5/intro)
- [Bitget](https://www.bitget.com/api-doc/common/intro)
- [Gate](https://www.gate.com/docs/developers/apiv4/en/)
- [MEXC](https://www.mexc.com/api-docs/spot-v3/introduction)
- [KuCoin](https://www.kucoin.com/docs-new)
- [Coinbase Advanced Trade](https://docs.cdp.coinbase.com/advanced-trade/docs/welcome)
- [Kraken](https://docs.kraken.com/api/)
- [HTX spot](https://huobiapi.github.io/docs/spot/v1/en/), [linear swaps](https://huobiapi.github.io/docs/usdt_swap/v1/en/), [coin swaps](https://huobiapi.github.io/docs/coin_margined_swap/v1/en/)
- [Deribit](https://docs.deribit.com/)
- [Upbit Korea](https://docs.upbit.com/)
- [Reviewed CCXT asynchronous source](https://github.com/ccxt/ccxt/tree/v4.5.91/python/ccxt/async_support)

Targeted documentation evidence: [Bybit native minutes](https://bybit-exchange.github.io/docs/v5/market/instrument), [Binance adjusted funding intervals](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info), [Bitget current funding and OI](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market), [KuCoin current funding](https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-current-funding-rate), [Deribit depth and quantity units](https://docs.deribit.com/api-reference/market-data/public-get_order_book). Binance's fundingInfo list covers **adjusted symbols**, so absence from that list does not provide verified metadata and must not automatically imply 8h. Bitget documents 20 requests/second/IP for current funding and OI; Binance fundingInfo shares a 500 requests/5 minutes/IP quota with fundingRate. CCXT endpoint weights plus collector backoff govern runtime scheduling; no fixture verifies those live quotas.

## Runtime network destinations

Default public endpoint hosts from installed CCXT 4.5.91, restricted to the products used here (unused account/options endpoints are not requested):

| Venue | Required hosts |
| --- | --- |
| Binance | `api.binance.com`, `fapi.binance.com`, `dapi.binance.com` |
| OKX | `www.okx.com` (installed CCXT default; current official docs also advertise `openapi.okx.com`) |
| Bybit | `api.bybit.com` |
| Bitget | `api.bitget.com` |
| Gate | `api.gateio.ws` |
| MEXC | `api.mexc.com`, `www.mexc.com` |
| KuCoin | `api.kucoin.com`, `api-futures.kucoin.com` |
| Coinbase | `api.coinbase.com` |
| Kraken | `api.kraken.com`, `futures.kraken.com` |
| HTX | `api.huobi.pro`, `api.hbdm.vn` (installed CCXT contract default; official docs also show `api.hbdm.com`) |
| Deribit | `www.deribit.com` |
| Upbit Korea | `api.upbit.com` |

The asynchronous client enables `aiohttp_trust_env` to use inherited HTTP(S) proxy settings and creates a verified SSL context with inherited `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` or the OS trust store. It never disables certificate verification or bypasses a restricted proxy. The managed cloud network policy currently does not authorize these exchange destinations. Enabling the listed hosts through the supported environment configuration process, or running the app on an authorized local network, is a prerequisite for live acceptance.

## Field semantics and missing data

Prices remain in the native quote currency: USD, USDT, USDC and KRW are never silently converted. Rates are fractions; a native rate of 0.0001 stays 0.0001. A funding countdown is never used to infer the funding interval. No universal 8h default exists. An explicit `interval` such as `1h` or `90m` is accepted; an unlabelled numeric interval is rejected. The eight-hour display normalization is unavailable if the actual interval is unknown. Rates labelled current remain current estimates, predicted fields remain predicted and Deribit's realized trailing data remains settled.

Spot unified baseVolume/quoteVolume retain base/quote units. Derivatives use the explicit audited contracts in `adapters/volume_units.py`; unified field names alone do not establish units. In particular OKX raw vol24h is contracts and volCcy24h is base volume; a reported 10,000 contracts with ctVal=0.01 must not become 10,000 BTC. Unknown derivative units stay null. REST and source WS share this interpretation; unverified legacy cached/history volume labels are suppressed at read time. They are rolling 24h metrics, not the quantity during the local polling period. Real zero volume/OI survives; unavailable data stays null. NaN, Infinity, zero/negative prices and invalid timestamps are not emitted as valid prices. Crossed bid/ask invalidates the snapshot. Source timestamps are UTC milliseconds; seconds are rejected instead of guessed. An event timestamp missing upstream stays null.

Each populated numeric field carries its endpoint and raw/unified field path in `field_sources`, and its own event time in `field_timestamps`. Ticker, funding and OI may come from different calls and have different event times. Interval metadata from a catalog or separate fundingInfo call retains an unknown event time when that endpoint does not provide one; it does not inherit the funding ticker's timestamp. `source_at` is the ticker event time; `received_at` records completion of this combined snapshot. Receipt time must not be interpreted as freshness evidence for an upstream field with an unknown source time. Missing fields and unverified units are listed in `issues`. No fabricated snapshots or demo values are generated.

Depth is fetched on demand. Spot quantities are base units; perpetual quantities are contracts, with `contract_size` and linear/inverse flag supplied by discovered market metadata. The caller must perform the corresponding base/notional conversion. Deribit is normalized explicitly because its raw API notional/base quantity differs from its one-contract multiplier. Missing depth is returned as empty depth, never synthetic liquidity.

## Asset identity

Automatic cross-venue mapping is deliberately restricted to BTC, ETH and SOL, and requires both the unified base and a reviewed native `baseId`: BTC/XBT/XXBT, ETH/XETH and SOL respectively. CCXT market normalization supplies the native ID from each exchange's base asset metadata. This is a reviewed economic-underlying mapping for established exchange markets, **not verification of any deposit network, token contract, wrapper, bridge or redeemability**. Markets with BTC as the ticker but an unrecognized underlying ID stay scoped. Unknown assets use `cex:<venue>:<client>:<native-base-id>` and `mapping_verified=false`; matching ticker text alone never merges them. Add further mappings through explicit audited user mappings rather than expanding by ticker resemblance. Contract multiplier assets such as 1000PEPE are not merged with PEPE automatically.

## Scheduling, recovery and remaining validation

All requests are public and no account credentials are read. Currency catalogs are disabled because some CCXT clients obtain those through account endpoints; public market catalogs supply the needed instrument identity. CCXT `enableRateLimit=true` enforces the version's native endpoint costs; request timeout is 15 seconds. Four concurrent per-symbol tasks per logical venue bound the detail workload. Product catalogs load sequentially across independent clients; all clients must succeed before an adapter commits its new discovered market set. Any catalog failure raises, preserving the previous set. There are no local unbounded retries.

Detailed and light collection isolate per-market/service failures and expose explicit error maps. All-failure batches reach collector backoff. Full discovered active catalogs rotate in bounded batches; watch assets retain detailed data and available WS. Independent CEX funding rotation calls only funding endpoints, not ticker/depth/OI. Catalog period metadata has its actual observation time and a six-hour validity ceiling; it does not acquire a fabricated exchange event timestamp. Source WS is described in STREAMING.md and native DEX WS in DEX_STREAMING.md.

Public settled funding history is separate from local snapshot samples: the manually refreshed, bounded service in FUNDING_HISTORY.md supports only endpoints whose actual settlement semantics were reviewed. It does not automatically backfill complete exchange history. Actual endpoint reachability, quotas, complete coverage, and 24-hour runtime acceptance remain outstanding in this environment.

Validate with `.venv/bin/python -m pytest tests/test_cex.py -q` and `.venv/bin/ruff check backend/watch_money/adapters/cex.py tests/test_cex.py`. Fixtures cover explicit native intervals, missing intervals, real zero values, fiat quotes, unknown identities, native OI units, per-field timestamps, separate service identities, invalid values and Deribit depth normalization. Client-construction checks instantiate all configured installed CCXT clients without issuing a request.
