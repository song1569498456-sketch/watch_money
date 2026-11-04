# 平台覆盖报告

统计时间：2026-11-04。实现状态与在线验收严格分开；最近在线验证时间为空表示没有在线证据。

共 28 个计划平台。详细语义与官方资料见 [CEX](CEX_SOURCES.md)、[源 WS](STREAMING.md) 和 [DEX](DEX_SOURCES.md)。

| 平台 | 产品 / 链 | 接入方式 | 已实现字段/能力 | 状态 | 最近在线验证 |
|---|---|---|---|---|---|
| binance | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| okx | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| bybit | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| bitget | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| gate | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| mexc | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| kucoin | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| coinbase | spot | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand | implemented_unverified_live | 未验证 |
| kraken | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| htx | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| deribit | spot, perpetual | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand, funding_when_provided, open_interest_when_unit_verified | implemented_unverified_live | 未验证 |
| upbit | spot | CCXT public REST + supported source WebSocket | market_catalog, ticker, orderbook_on_demand | implemented_unverified_live | 未验证 |
| hyperliquid | perpetual | public REST | market-discovery, price, orderbook, funding | implemented-unverified-live | 未验证 |
| lighter | perpetual | public REST | market-discovery, price, orderbook, funding | implemented-unverified-live | 未验证 |
| dydx | perpetual | public REST | market-discovery, price, orderbook, funding | implemented-unverified-live | 未验证 |
| paradex | perpetual | public REST | market-discovery, price, orderbook, funding | implemented-unverified-live | 未验证 |
| aster | perpetual | public REST | market-discovery, price, orderbook, funding | implemented-unverified-live | 未验证 |
| edgex | perpetual | public REST v2 | market-discovery, price, orderbook, funding, open-interest, volume | implemented-partial-unverified-live | 未验证 |
| variational | perpetual | public REST v2 statistics / RFQ | market-discovery, mark, funding, open-interest, volume, indicative-rfq-samples | implemented-partial-unverified-live | 未验证 |
| gmx | perpetual, arbitrum | public REST Oracle API, Arbitrum V2 | market-discovery, indicative-mark, open-interest-usd | implemented-partial-unverified-live | 未验证 |
| jupiter-perps | perpetual, solana | public read-only Perps REST /jlp-info /pool-info /market-stats | custody-discovery, reference-price, side-specific-borrow-metrics, custody-state | implemented-partial-unverified-live | 未验证 |
| drift | perpetual, solana | optional Solana RPC original program metadata audit | inactive-market-discovery, program-state, market-status | metadata-only-unverified-lifecycle | 未验证 |
| uniswap | pool, arbitrum, base, ethereum | DEX Screener aggregated discovery/indicative pool prices | bounded-pool-discovery, indicative-price, volume-usd, liquidity-usd, token-addresses | aggregated-partial-unverified-live | 未验证 |
| pancakeswap | pool, bsc | DEX Screener aggregated discovery/indicative pool prices | bounded-pool-discovery, indicative-price, volume-usd, liquidity-usd, token-addresses | aggregated-partial-unverified-live | 未验证 |
| aerodrome | pool, base | DEX Screener aggregated discovery/indicative pool prices | bounded-pool-discovery, indicative-price, volume-usd, liquidity-usd, token-addresses | aggregated-partial-unverified-live | 未验证 |
| curve | pool, ethereum | DEX Screener aggregated discovery/indicative pool prices | bounded-pool-discovery, indicative-price, volume-usd, liquidity-usd, token-addresses | aggregated-partial-unverified-live | 未验证 |
| raydium | pool, solana | DEX Screener aggregated discovery/indicative pool prices | bounded-pool-discovery, indicative-price, volume-usd, liquidity-usd, token-addresses | aggregated-partial-unverified-live | 未验证 |
| orca | pool, solana | DEX Screener aggregated discovery/indicative pool prices | bounded-pool-discovery, indicative-price, volume-usd, liquidity-usd, token-addresses | aggregated-partial-unverified-live | 未验证 |

## binance

官方文档：[binance](https://developers.binance.com/docs/derivatives/Introduction)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## okx

官方文档：[okx](https://www.okx.com/docs-v5/en/)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## bybit

官方文档：[bybit](https://bybit-exchange.github.io/docs/v5/intro)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## bitget

官方文档：[bitget](https://www.bitget.com/api-doc/common/intro)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## gate

官方文档：[gate](https://www.gate.com/docs/developers/apiv4/en/)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## mexc

官方文档：[mexc](https://www.mexc.com/api-docs/spot-v3/introduction)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## kucoin

官方文档：[kucoin](https://www.kucoin.com/docs-new)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## coinbase

官方文档：[coinbase](https://docs.cdp.coinbase.com/advanced-trade/docs/welcome)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Advanced Trade public spot service. International/US derivatives are separate services and are not claimed as supported.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## kraken

官方文档：[kraken](https://docs.kraken.com/api/)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## htx

官方文档：[htx](https://huobiapi.github.io/docs/spot/v1/en/)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Global service; regional products are separate and are not silently substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## deribit

官方文档：[deribit](https://docs.deribit.com/)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Deribit service; dated futures/options excluded. Funding is continuously accrued: the 8h rate window is not a discrete settlement schedule.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## upbit

官方文档：[upbit](https://docs.upbit.com/)

**条件**：Public API reachable from runtime region; no account/API key required

**限制**：Korea API only; KRW/BTC/USDT quotes preserved. Singapore, Indonesia and Thailand APIs are separate regional services, not substituted.；REST polling; exchange rate limiter enabled; collector owns backoff.；Catalog/history endpoint behavior and live availability not verified in this environment.；Funding interval remains missing unless explicit metadata is available.；Public API access can be regionally blocked; missing fields remain null.；Only BTC/ETH/SOL with reviewed native base identifiers map automatically.

## hyperliquid

官方文档：[hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)

**条件**：Public API network access

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；REST polling; no historical/stream recovery adapter yet

## lighter

官方文档：[lighter](https://apidocs.lighter.xyz/)

**条件**：Public API network access

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；REST polling; no historical/stream recovery adapter yet；Funding raw fraction retained; endpoint period unverified, normalization disabled

## dydx

官方文档：[dydx](https://docs.dydx.xyz/)

**条件**：Public API network access

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；REST polling; no historical/stream recovery adapter yet

## paradex

官方文档：[paradex](https://docs.paradex.trade/)

**条件**：Public API network access；Compatible CCXT version

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；REST polling; no historical/stream recovery adapter yet

## aster

官方文档：[aster](https://github.com/asterdex/api-docs)

**条件**：Public API network access；Compatible CCXT version

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；REST polling; no historical/stream recovery adapter yet；Funding normalization requires returned per-market interval; no fixed 8h assumption

## edgex

官方文档：[edgex](https://github.com/edgex-org/edgex-gitbook/tree/main/api-v2)

**条件**：Public API network access

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；No ticker event timestamps; funding timestamp preserved separately

## variational

官方文档：[variational](https://docs.variational.io/technical-documentation/api)

**条件**：Public API network access

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；RFQ samples may be cached up to 600s and are not executable quotes；Swap products have asymmetric funding and are excluded

## gmx

官方文档：[gmx](https://docs.gmx.io/docs/api/rest-api/markets/)

**条件**：Public API network access

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；No executable prices, funding or borrowing normalization; fees require side/collateral-specific state；Arbitrum V2 only

## jupiter-perps

官方文档：[jupiter-perps](https://perps-api.jup.ag/v1/docs)

**条件**：perps-api.jup.ag network access

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；Reference price has no documented last/mark/index identity; retained in protocol_metrics；Borrow fees are not funding; endpoint does not explicitly declare borrow rate period；Supported market mint enum bounds discovery; no executable quotes or full OI

## drift

官方文档：[drift](https://github.com/drift-labs/protocol-v2/blob/master/sdk/src/config.ts)

**条件**：SOLANA_RPC_URL for original-program metadata only

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；Original identity/layout verified from drift-labs sources; current lifecycle lacks independent verification；Inactive marks local adapter execution unavailability; decoded chain status flags remain separate；Invalid metadata-only snapshots contain no prices/funding/executable books；IDL and deployment metadata verified from official source, live RPC not verified

## uniswap

官方文档：[uniswap](https://raw.githubusercontent.com/Uniswap/v3-periphery/main/deploys.md)

**条件**：api.dexscreener.com network access；RPC/quoter required for executable quotes

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；Search-limited discovery; strict version labels may omit valid pools；No executable quotes, block time/number, verified pool membership or fee tiers；Deployment source documents verified; deployed bytecode not checked

## pancakeswap

官方文档：[pancakeswap](https://raw.githubusercontent.com/pancakeswap/pancake-swap-sdk/master/src/constants.ts)

**条件**：api.dexscreener.com network access；RPC/quoter required for executable quotes

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；Search-limited discovery; strict version labels may omit valid pools；No executable quotes, block time/number, verified pool membership or fee tiers；Deployment source documents verified; deployed bytecode not checked

## aerodrome

官方文档：[aerodrome](https://raw.githubusercontent.com/aerodrome-finance/contracts/main/README.md)

**条件**：api.dexscreener.com network access；RPC/quoter required for executable quotes

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；Search-limited discovery; strict version labels may omit valid pools；No executable quotes, block time/number, verified pool membership or fee tiers；Deployment source documents verified; deployed bytecode not checked

## curve

官方文档：[curve](https://raw.githubusercontent.com/curvefi/curve-js/master/src/constants/network_constants.ts)

**条件**：api.dexscreener.com network access；RPC/quoter required for executable quotes

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；Search-limited discovery; strict version labels may omit valid pools；No executable quotes, block time/number, verified pool membership or fee tiers；Deployment source documents verified; deployed bytecode not checked

## raydium

官方文档：[raydium](https://raw.githubusercontent.com/raydium-io/raydium-sdk-V2/master/src/common/programId.ts)

**条件**：api.dexscreener.com network access；RPC/quoter required for executable quotes

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；Search-limited discovery; strict version labels may omit valid pools；No executable quotes, block time/number, verified pool membership or fee tiers；Deployment source documents verified; deployed bytecode not checked

## orca

官方文档：[orca](https://raw.githubusercontent.com/orca-so/whirlpools/main/README.md)

**条件**：api.dexscreener.com network access；RPC/quoter required for executable quotes

**限制**：Live API verification blocked by enforced environment network policy (2026-11-04)；Search-limited discovery; strict version labels may omit valid pools；No executable quotes, block time/number, verified pool membership or fee tiers；Deployment source documents verified; deployed bytecode not checked

## 验收区别

聚合现货池提供指示信息，不是完整原生协议/RPC接入或可成交路由；保留底层池身份，不将聚合器视为额外流动性。Drift 原程序身份/布局已核对，当前生命周期没有实时确认；inactive只描述此适配器缺少执行能力，链上原始状态单独保留。Jupiter 参考价格与侧别借贷指标不伪装成标记价或资金费。

CEX 源 WS 已实现模拟测试；DEX 源 WS、广覆盖轻量订阅、完整原始录制、完整链上执行报价、真实在线核对和24小时验收仍未通过。协议×链×版本登记及来源在 `backend/watch_money/adapters/deployments.json`。
