# DEX 接口与验证记录

本文件对应 `backend/watch_money/adapters/dex.py` 与 `deployments.json`。研究日期：2026-11-04。

## 验证边界

已经读取官方文档、官方 GitHub SDK/部署文件，并通过确定性 HTTP fixtures 验证解析。**尚未证明任何交易 API 当前可达，也没有连续运行 24 小时。** 当前云环境网络策略仅允许 GitHub 和依赖仓库。通过继承代理实际请求 Hyperliquid `/info`、Lighter `/orderBookDetails` 均得到 `ProxyError: 403 Forbidden`，没有绕过代理。文档研究可使用独立网页研究工具，不等于行情接口已验证。

`coverage().last_verified_at` 保持空值；部署登记中的 `source_verified_at` 只代表核对官方源文件。部署地址、池成员关系、合约字节码和区块状态不能混为一项验证。

## 永续覆盖

| 平台 | 实现与数据 | 资金费语义 / 缺口 |
| --- | --- | --- |
| Hyperliquid | 原生 public POST `/info`：`perpDexs`、`spotMeta`、各命名空间 `meta`/`metaAndAssetCtxs`、按需 `l2Book`；mark/index、quote 成交量、base OI | `funding` 保留分数值，当前估算，每小时；不把 impact prices 当买卖一，不把 mark 当成交价。 |
| Lighter | 原生 GET `/api/v1/orderBookDetails`、`funding-rates`、按需 `orderBookOrders`；last/mark/index、USDC 成交量、base OI | 只取 `exchange=lighter`；跨所比较接口未声明周期，保留原始率并标记 `funding_period_unverified`，不做 8h 归一化。 |
| edgeX | 原生 V2 `/public/meta/getMetaData`、`quote/getTicker`、`funding/getLatestFundingRate`、`quote/getDepth` | 使用最新 funding endpoint 的 `fundingRate`、`isSettlement`、`fundingRateIntervalMin` 与 `fundingTimestamp`；不复用 ticker 的费率或时间。 |
| dYdX | 原生 Indexer V4 `perpetualMarkets`、`orderbooks/perpetualMarket/{ticker}`；oracle、USD 成交量、base OI | `nextFundingRate` 是下一小时预测；报价 USD、结算 USDC 分开。CCXT dYdX 不提供 tickers，故未依赖其 ticker 方法。 |
| Paradex | CCXT public markets、tickers、funding rates、order book | 周期取返回 interval 或市场 `funding_period_hours`；包括 24h 等不同合约周期，禁止统一设为 8h。 |
| Aster | CCXT public futures tickers/premium index/order book，另取 funding intervals | 每市场获取实际 interval，未返回即留空；CCXT 加载市场也可能读取公开 spot exchangeInfo。 |
| GMX | 原生 Arbitrum V2 `/markets`、`/tokens`；GMX API `/v1/markets/tickers` 的 indicative mark 与 USD 多空 OI | mark 与 USD OI 使用 30 位定点值；侧别 funding/borrowing 原值仅作来源元数据，不压成统一资金费。无可成交 bid/ask。 |
| Variational | 原生 `/metadata/v2/stats`；RFQ 机制，perpetual mark、USDC 成交量/OI、当前资金费及期限 | 使用 `funding.last.interval_rate`，不是年化率；周期 `interval_seconds`；缓存报价保留规模和更新时间且不当可成交盘口。不同 long/short 费用的 swap 产品明确排除。 |
| Drift | 原程序只读 RPC metadata adapter，状态 `metadata-only-unverified-lifecycle` | drift-labs SDK/IDL 支持原 program 身份与布局；当前生命周期未独立核验。设置 `SOLANA_RPC_URL` 可读取原 State/PerpMarket。inactive 表示本适配器没有验证执行能力，不代表已确认链上暂停。 |
| Jupiter Perps | 原生 read-only public `/jlp-info`、`/pool-info`、`/market-stats` | 虽然概览页标 WIP，但独立官方 OpenAPI 已公开。读取支持 mint 的 custody、reference price、侧别 borrowing/liquidity；借贷费不写进 `funding_rate`，reference price 不猜成 last/mark/index。 |

Hyperliquid 的 HIP-3 市场保留 `deployer:asset` 原生身份，不会双重添加前缀，也不会按 ticker 合并成原生合约。抵押币通过 `collateralToken` 与 `spotMeta.tokens` 解析，无法识别则报错。数量精度来自 `szDecimals`；价格同时有有效位数规则，不伪造固定 tick。原生、HIP-3 的资金费支付周期一致，但公式参数可不同。

Lighter 返回人类可读 decimal prices/sizes，不能再次按链上 precision 缩放。盘口按 `remaining_base_amount` 聚合同价未成交订单。资金费接口也返回其他交易所记录，不能混入 Lighter。本版本未读取二元期权或 spot 为 perpetual。

所有未知币种保持 venue scope；没有自动把同名 ticker 认证成相同资产。所有上游缺失数值保持 `None`。独立接口资金费、mark/index 用 `field_timestamps` 保留各自时间，未提供就明确留空，不继承另一接口的新时间。

## Jupiter Perps 的公开只读数据

独立 OpenAPI `/v1/docs` 列明 `/jlp-info`、`/pool-info`、`/market-stats`，无需访问任何交易生成/执行路径。适配器仅发起这三个 GET。通过 JLP custody 元数据与 OpenAPI 明确枚举的 SOL/ETH/BTC mint 取交集发现市场；稳定币 custody 不是 perpetual，不会成为假市场。`discovery_complete=false` 反映 published enum 边界。

`protocol_metrics.reference_price_usd` 保留 API 参考价格；没有把来源未区分的 `price` 填成成交价/mark/index。`long_borrow_rate_fraction` 与 `short_borrow_rate_fraction` 分别将 API 百分比除以 100；资金费各字段仍为空。API schema 没有明确借贷率周期，因此没有据小时借贷机制推断此接口返回率的周期。侧别 available liquidity 保留原值字符串，不猜单位/缩放，也不是可成交容量。

custody `aumUsd`、`guaranteedUsd`、`globalShortSizes` 按账户文档 USD6 解析为分别命名的指标。`guaranteedUsd` 是 long size minus collateral，并不是总 long OI；`globalShortSizes` 只是一侧 OI，不塞入总 `open_interest`。`owned`/`locked` token raw 使用字符串保留大整数，不能缺少 mint decimals 就转换。全部字段无 source event timestamp 时保持未知，API 响应时间不能替代。缺少完整 costs、side/collateral/size quotes 与账户状态解码仍是明确限制。

## Drift 原程序身份与生命周期验证边界

[Velocity 后继/分叉仓库的迁移文档](https://github.com/velocity-exchange/velocity-v1/blob/master/docs/DRIFT-TO-VELOCITY.md) 声称原 Drift 程序暂停，并使用不同的 Velocity program/PDA/layout。这是该仓库对原程序的说法，**不是已独立核验的 canonical Drift 当前生命周期事实**；已读取的 drift-labs SDK/IDL 只支持原程序身份和字段布局，没有提供当前停运状态的独立佐证，主网 RPC 也尚未验证。因此生命周期标为 `unverified`，不会把文档重定向或后继仓库说法作为全平台暂停判定。该后继说法的 URL/hash 以 `successor_claim_source_*` 单独保留，不能代替 on-chain verification。

可选 `DriftAdapter` 通过配置的 Solana RPC `getProgramAccounts` 读取 **原** program 的 State/PerpMarket。过滤 Anchor discriminator，不用固定 `dataSize`；验证 owner、非 executable、discriminator、market 内嵌 pubkey 以及枚举有效性。只解码文档/官方 IDL 支持的名称、market index、perpetual 类型和暂停状态，读取 offsets 保存于 `deployments.json.solana_readers`，来源 SHA256 同时记录。IDL 字节长度 State=992、PerpMarket=1216 已与 Rust `Size` 常量一致核对；只要求所读字段存在，允许 trailing account growth。Future/Prediction 类型不标为 perpetual。

发现结果 `active=false`、快照 `valid=false` 是本 metadata-only 适配器没有验证执行能力的本地限制，不是对当前部署生命周期的事实判断。`protocol_metrics.deployment_state=unverified-lifecycle` 与 `adapter_execution_state=metadata-only-non-executable` 明确分开；实际解码的 `market_status`、`exchange_status_bits` 和 slot 保留为独立链上读取结果，即使 flags 反映 Active/0，也不会被覆盖成 Paused。没有实时 mark/funding/book。RPC context slot 不能作为历史价格事件时间。`SOLANA_RPC_URL` 未配置则明确报出所需条件。尚未对实际主网 RPC 验证，不能宣称当前已暂停、正常运行或完成恢复接入。

## 现货部署与发现

`deployments.json` 登记了 10 个官方来源支持的协议 × 链 × 版本：

| 协议 | 已登记部署 | 官方来源 |
| --- | --- | --- |
| Uniswap | Ethereum V3、Arbitrum V3、Base V3；Base 工厂与前两者不同 | [V3 部署表](https://github.com/Uniswap/v3-periphery/blob/main/deploys.md)、[官方 SDK 地址](https://github.com/Uniswap/sdks/blob/main/sdks/sdk-core/src/addresses.ts) |
| PancakeSwap | BNB Chain V2 | [官方 SDK 常量](https://github.com/pancakeswap/pancake-swap-sdk/blob/master/src/constants.ts) |
| Aerodrome | Base 原版 stable/volatile factory | [官方合约部署表](https://github.com/aerodrome-finance/contracts/blob/main/README.md) |
| Curve | Ethereum StableSwap NG factory | [官方 curve-js 网络常量](https://github.com/curvefi/curve-js/blob/master/src/constants/network_constants.ts) |
| Raydium | Solana AMM V4、CLMM、CPMM programs | [官方 SDK program IDs](https://github.com/raydium-io/raydium-sdk-V2/blob/master/src/common/programId.ts) |
| Orca | Solana Whirlpools program | [官方 Whirlpools 说明](https://github.com/orca-so/whirlpools/blob/main/README.md) |

每条包含 factory/program 地址、来源 URL、实际读取文件 SHA256、来源核对时间、`onchain_verified=false`。没有生成协议和所有链的笛卡尔积，也没有把尚未核对的版本列作已部署。

六个协议分别通过 [DEX Screener](https://docs.dexscreener.com/api/reference) 获取辅助数据；其结果保留在底层协议 venue 中，不增加一个重复流动性的“聚合器市场”。搜索使用四组主要币对，每次最多取得服务返回的有限结果，**并非全链池目录**。`discovery_complete=false`，目录遗漏不能当作退市。

严格要求 chain/dexId/版本标签匹配已登记部署。未标版本的结果拒绝，不假定 V2。因此某些协议，尤其 Curve、Aerodrome、Orca，可能没有足够标签而返回空列表。这是明确缺口，需要后续官方 pool API 或 RPC 工厂/程序验证；不能将“有适配器”描述成已全量接入。

价格使用 `priceNative` 保留原币对报价币，USD 成交量单独注明 USD。保留链、协议、版本、池地址、两边 token 地址及底层单池 route。`pools` 字典提供 `Pool` 对象及 liquidity USD，池成员尚未 RPC 核验故 `verified=false`。Solana 地址保留大小写，EVM 地址标准化为小写。

这些价格不带规模、区块和价格冲击，明确标 `indicative_pool_price_not_executable`。没有合成 bid/ask，也不提供假订单簿。现阶段不支持 executable route quote、fee tiers、按区块更新与全链发现。`pairCreatedAt` 是池创建时间，绝不充当当前行情时间。

## 网络域名与公开接口条件

以下 12 个 `required_hosts` 已写入 JSON 顶层，可用于申请环境 HTTP 放行；不需要账户交易权限：

```text
api.hyperliquid.xyz
mainnet.zklighter.elliot.ai
edgex-prod-v2.edgex.exchange
indexer.dydx.trade
api.prod.paradex.trade
fapi.asterdex.com
sapi.asterdex.com
arbitrum-api.gmxinfra.io
arbitrum.gmxapi.io
perps-api.jup.ag
omni-client-api.prod.ap-northeast-1.variational.io
api.dexscreener.com
```

Drift 旧 REST 服务未被用作当前行情来源，也不会自行切换到 `data.velocity.exchange`。原 Drift metadata adapter 可选使用 `SOLANA_RPC_URL` 或 `DriftAdapter(rpc_url=...)`，遵循已配置 RPC 的网络条件与权限。`rpc_url` 也可由集成层绑定 `settings.rpc_urls["solana"]`。没有默认请求公共 Solana RPC；配置 URL 可能含凭证，不写入来源/快照/错误信息。

HTTP 客户端默认 20s 超时并继承系统代理；CCXT 使用 `enableRateLimit=true`、`aiohttp_trust_env=true`。HTTP 429、失败状态、业务错误和解析异常向 collector 抛出，不返回模拟数据。原生 adapters 本版本为 REST polling，无 WebSocket/历史分页/重放恢复。Variational 官方限制 10 次/10s/IP，返回 429 的 `retry_after_seconds` 当前交给采集器通用退避，尚无专用调度；DEX Screener 查询应按其 endpoint 限流并缓存目录。没有网络导入副作用，也不在构造器创建客户端。

## 参考语义来源

- [Hyperliquid 官方 SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/info.py)、[资金费](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)
- [Lighter 官方 SDK schema](https://github.com/elliottech/lighter-python/tree/main/lighter/models)、[CCXT public endpoint 解释](https://github.com/ccxt/ccxt/blob/master/python/ccxt/lighter.py)
- [edgeX V2 官方公开 API](https://github.com/edgex-org/edgex-gitbook/tree/main/api-v2/public-api)、[生产域名](https://github.com/edgex-org/edgex-gitbook/blob/main/api-v2/README.md)
- [dYdX 官方 Indexer](https://docs.dydx.xyz/)、[CCXT 接口参考](https://github.com/ccxt/ccxt/blob/master/python/ccxt/dydx.py)
- [Paradex public market 与 funding parsing](https://github.com/ccxt/ccxt/blob/master/python/ccxt/paradex.py)、[Aster 官方 API](https://asterdex.github.io/aster-api-website/)
- [GMX API](https://docs.gmx.io/docs/sdk/v2/)、[官方 SDK npm 包](https://www.npmjs.com/package/@gmx-io/sdk)（核对 2.4.0 的 `utils/markets/api`、`MarketTicker`：`/v1/markets/tickers`、`marketTokenAddress`、fixed-point 字段）
- [Variational V2 文档](https://docs.variational.io/technical-documentation/api.md)：v1 年化率、swap placeholder zero 与 v2 的区分
- [当前 Drift 文档跳转](https://docs.drift.trade/)、[Velocity 读取数据](https://docs.velocity.exchange/developers/ecosystem-builders/reading-data)
- [Jupiter Perps 官方账户说明](https://developers.jup.ag/docs/perps/custody-account)、[独立公开 OpenAPI](https://perps-api.jup.ag/v1/docs)（比 WIP 概览页提供更具体的接口证据）
- [Drift 原程序 SDK/IDL](https://github.com/drift-labs/protocol-v2/blob/master/sdk/src/idl/drift.json)、[State Rust layout](https://github.com/drift-labs/protocol-v2/blob/master/programs/drift/src/state/state.rs)、[PerpMarket Rust layout](https://github.com/drift-labs/protocol-v2/blob/master/programs/drift/src/state/perp_market.rs)、[后继/分叉项目对迁移的说明，非原部署状态独立佐证](https://github.com/velocity-exchange/velocity-v1/blob/master/docs/DRIFT-TO-VELOCITY.md)

## 本地验证

在项目根目录运行 `.venv/bin/python -m pytest tests/test_dex.py -q`，本轮 **39 passed**；Ruff 检查通过。fixtures 覆盖 HIP-3 命名空间/抵押币/精度、错位 contexts、零值/负费率、Lighter 跨所资金费过滤/盘口聚合、独立字段时间、不同周期、缺失 multiplier、原始报价币、池路由/版本拒绝、Solana 大小写、edgeX V2、Variational interval-vs-annualized/swap 排除、GMX 30 位金额、Jupiter read-only endpoint 白名单/借贷费和价格身份隔离/原始大整数保留、Drift 原程序 metadata/链上 flags 与本地执行能力隔离/新程序 owner 拒绝/错误 discriminator/短数据拒绝/扩容尾部兼容/RPC 凭证脱敏。测试使用静态 schema 样本，不是实际录制行情；没有把 fixture 当作 live 验证。
