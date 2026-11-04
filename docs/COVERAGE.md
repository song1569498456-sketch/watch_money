# 平台覆盖报告

统计时间：2026-11-04。本表记录代码范围，**28 个计划平台不等于 28 个实网已验收平台**。所有平台最近在线验证均为“未验证”；官方数据主机仍受环境网络策略限制，没有真实行情或 24 小时运行证据。

当前阶段按用户最新顺序收敛：先资产级价差/best/双向组合/规模计算闭环，再资金费聚合，最后其他简洁指标与已有历史。不新增交易账户，不因下表列出原生协议缺口就先扩大其开发范围。

逐项修复与验收证据见 [AUDIT_BACKLOG.md](AUDIT_BACKLOG.md)，每批更新。公开费用、金额/切腿、退出UI与通知发送retry二次时效已完成本轮离线/隔离浏览器复验。完整三阶段和实网能力仍未通过，不因本覆盖清单存在而记完成。

新数据目录默认启用全部28个平台；已有启用配置保持不变。当前广覆盖按整产品批量与逐币回退分层，新配置目标价格/资金费周期30秒，目录1800秒重发现；存量周期不被强制修改。实际端点范围、限流下界、DEX整组支持及回退限制见 [COLLECTION.md](COLLECTION.md)。状态按读取时刻统计有效双边盘口与资金费，配置周期不等于实际完成周期，未提高60秒过期阈值。

批量资产证据源扩为Gate/Bitget/KuCoin/Coinbase Exchange官方币种目录，仅匹配原生currency ID、所列client及现货范围；现有Coinbase Advanced Trade不能套用Exchange目录。另支持可审计人工证据；未核实、歧义、包装/桥接和单位冲突保持隔离。见 [ASSET_VERIFICATION.md](ASSET_VERIFICATION.md)。DEX仍依赖来源实际提供字段，GMX/Jupiter/Drift等不会因调度扩大而升级为已完成。本轮测试证据见VALIDATION.md，当前环境实网未验。

## 12 个 CEX

共用能力为：公开市场目录、分批轻量 ticker、关注资产详细行情、按需盘口及受 CCXT Pro 支持的源 WS。永续的 funding/OI 只保留来源实际提供且单位可核对的字段。目录按 client 隔离部分失败；失败服务的旧身份保留并标为待核实，不当作完整目录或确认退市。所有条目均为**实现及本地模拟验证，实网未验**。

| 平台 / 官方文档 | 产品和服务范围 | 具体边界 |
| --- | --- | --- |
| <a id="binance"></a>[Binance](https://developers.binance.com/docs/derivatives/Introduction) | spot、USDⓈ-M、COIN-M perpetual，独立 client | 全球服务；不替换为地区产品；正反向合约与结算币分组 |
| <a id="okx"></a>[OKX](https://www.okx.com/docs-v5/en/) | spot、perpetual | 周期从明确元数据/相邻实际时间取得，不固定假定 8h |
| <a id="bybit"></a>[Bybit](https://bybit-exchange.github.io/docs/v5/intro) | spot、linear/inverse perpetual | 保留原生 category、合约单位及周期元数据来源 |
| <a id="bitget"></a>[Bitget](https://www.bitget.com/api-doc/common/intro) | spot、linear/inverse perpetual | 产品组分开；缺字段保持 null |
| <a id="gate"></a>[Gate](https://www.gate.com/docs/developers/apiv4/en/) | spot、perpetual | 结算币分组，OI contracts 与 USD 分别标单位 |
| <a id="mexc"></a>[MEXC](https://www.mexc.com/api-docs/spot-v3/introduction) | spot、perpetual | 源 WS 依已安装 CCXT 能力，spot 使用其可用的单 ticker 路径 |
| <a id="kucoin"></a>[KuCoin](https://www.kucoin.com/docs-new) | spot、futures client 的 perpetual | 两服务独立发现；不以一侧目录失败抹掉另一侧结果 |
| <a id="coinbase"></a>[Coinbase](https://docs.cdp.coinbase.com/advanced-trade/docs/welcome) | Advanced Trade public spot | 显式公共路径；不声称 International/US 衍生品接入 |
| <a id="kraken"></a>[Kraken](https://docs.kraken.com/api/) | spot、futures client 的 perpetual | 分开保留 client 与产品身份 |
| <a id="htx"></a>[HTX](https://huobiapi.github.io/docs/spot/v1/en/) | spot、perpetual | linear/inverse 显式区分；资金费周期缺失不归一化 |
| <a id="deribit"></a>[Deribit](https://docs.deribit.com/) | spot、perpetual | 排除期货/期权；8h trailing funding 是连续累计窗口，不是离散的下一笔 8h 结算 |
| <a id="upbit"></a>[Upbit](https://docs.upbit.com/) | Korea spot | 保留 KRW/BTC/USDT 报价；不替换为新加坡/印尼/泰国 API |

批量 ticker 只在原生能力可用时使用，内部最多 50 symbols；回退为单 ticker 时有并发和 100 市场总量上限。CCXT 方法可能内部请求整个产品目录、分页或重试，不能把此上限当作各交易所统一限流规则。资金费补丁、价格补丁分别保留字段来源、源时间和接收时间，旧价格不能用来刷新 funding 或相反。

REST 详细/轻量及源 WS 共用经审查的 `volume_units`。OKX 永续不把统一 `baseVolume` 中的 contracts 误标为 base：优先原始 `volCcy24h`；可核对的线性合约才作 contract_size 转换，反向不以现价倒算历史 base volume。未知衍生品 volume 单位保持未提供，真实零值保留。旧缓存/历史中只有旧 CCXT baseVolume/quoteVolume 标签、缺少核验原始来源的永续 volume 在读取时抑制并标 `legacy_volume_unit_unverified`，不会沿用为已核验币种成交量。

BTC/ETH/SOL 小型审核表只用于已核对原生 base ID 的自动身份归集，**不是市场覆盖范围**。其他币种继续用 venue/client/native ID 身份收集；同 ticker 不自动跨平台合并。全目录身份候选和批量人工映射已提供，链地址、包装/桥接及数量单位冲突仍需明确证据。

详见 [CEX_SOURCES.md](CEX_SOURCES.md)、[CEX_LIGHT.md](CEX_LIGHT.md)、[STREAMING.md](STREAMING.md)、[ASSET_IDENTITY.md](ASSET_IDENTITY.md)。

## 10 个永续 DEX

所有 adapter 均有广覆盖调度接口；目前 DEX `fetch_light` 复用各自公开 `fetch`，不会拉盘口，**不意味着每个平台都有廉价批量 endpoint**。edgeX 可能每批 1+N 请求；Jupiter 为 1+2N。仅轮转其实际发现目录，未知、inactive 或能力不足的市场不能生成可执行行情。

| 平台 | 已实现数据与采集路径 | 状态与未完成能力 |
| --- | --- | --- |
| <a id="hyperliquid"></a>Hyperliquid | REST universe、HIP-3 命名空间、mark/index、OI、hourly funding、按需深度；分片原生l2Book/context WS | 本地模拟与 loopback 验证；实网未验。源时间按字段区分，context 无源时间；全目录流最多500市场，预算截断显示，历史恢复未开发 |
| <a id="lighter"></a>Lighter | 公共目录、价格/按需深度；原生market_stats/all WS盘口及原始资金费指标 | 实网未验；官方未明确的费率单位/基准保留原始指标，不输出归一化资金费；盘口使用明确的发布时刻 |
| <a id="dydx"></a>dYdX | Indexer perpetual markets、预测 hourly funding、按需深度；原生 `v4_markets` WS | 本地模拟与 loopback 验证；实网未验。oracle 的 effectiveAt 只用于 index；该 WS 不重建深度；历史恢复未开发 |
| <a id="paradex"></a>Paradex | CCXT 线性 perpetual 目录、ticker/funding/按需深度 | 实网未验；缺 multiplier 拒绝市场，缺周期不推断；原生 WS 未开发 |
| <a id="aster"></a>Aster | CCXT 线性 perpetual 目录、ticker/funding/按需深度 | 实网未验；按市场返回周期，不固定 8h；原生 WS 未开发 |
| <a id="edgex"></a>edgeX | 公共 V2 metadata/ticker/depth、funding、OI/volume | 部分能力，实网未验；ticker 事件时间未知，funding 时间独立；原生 WS 未开发 |
| <a id="variational"></a>Variational | V2 perpetual listings、mark、funding interval rate、OI、volume、缓存 RFQ 样本 | 部分能力，实网未验；排除 swap；RFQ 可缓存至 600s，不是可执行规模报价；原生 WS 未开发 |
| <a id="gmx"></a>GMX | Arbitrum V2 Oracle market tickers、指示性 mark、USD OI | 部分能力，实网未验；无 side/collateral/size 对应可执行价及完整 funding/borrowing 成本模型；其他链版本未纳入 |
| <a id="jupiter-perps"></a>Jupiter Perps | Solana 官方只读 `/jlp-info`、`/pool-info`、`/market-stats`：custody、reference price、侧别借贷与可用流动性 | 部分能力，实网未验；限官方 mint enum；reference price 不冒充 last/mark/index，borrow 不是 funding，周期未知；完整 OI、执行报价和持仓成本未完成 |
| <a id="drift"></a>Drift | 原 program 的可选 Solana RPC State/PerpMarket 元数据读取、名称、index、链上 status flags | metadata-only；原 program 身份/IDL 核对，本地模拟通过，实网生命周期未验证；inactive/invalid 表示本 adapter 不提供行情，不断言链上停运，不替换成后继程序 |

Hyperliquid/Lighter/dYdX WS 已接 collector，包含订阅、心跳、取消、断线/重订阅及稀疏补丁。连接成功不刷新字段年龄，也不证明资金费、执行价和所有字段最新。其他计划 DEX 原生 WS 尚未实现，REST 保留。跨所有 AMM/RFQ 协议的完整持仓成本及可执行路线仍未完成。

官方资料、字段口径与限流见 [DEX_SOURCES.md](DEX_SOURCES.md)、[DEX_CATALOG.md](DEX_CATALOG.md)、[DEX_STREAMING.md](DEX_STREAMING.md)；它们保留具体官方链接和来源核对证据。

## 6 个现货 DEX

六协议的聚合发现均由登记的“协议 × 链 × 版本”过滤。搜索改为可配置协议/链关键词及显式已知池地址，**仍是 `search_limited`、`discovery_complete=false`**，并非全链池索引；严格版本标签也可能漏池。搜索结果减少不作为退市证据。聚合 priceNative、USD volume/liquidity 保留来源，未核验池成员、当前事件时间、规模与冲击，不能合成为 bid/ask。

原生读取是另外的按需接口：`POST /api/pools/native/state` 与 `/quote`，需要用户配置对应链 RPC，不替换聚合目录或声称扩大目录完整性。

| 协议 | 已登记链 / 版本 | 已实现原生能力 | 未完成与证据状态 |
| --- | --- | --- | --- |
| <a id="uniswap"></a>Uniswap | Ethereum / Arbitrum / Base V3 | 按显式池地址读取 factory、tokens/decimals、fee/tick spacing、slot0、active liquidity；成员反查、canonical block hash/time | 模拟验证，实网未验；V3 tick traversal/quoter 规模报价、完整发现/多跳路线未开发 |
| <a id="pancakeswap"></a>PancakeSwap | BNB Chain V2 | 按显式池状态与整数常数积 exact-input 估算，0.25% SDK fee，双向数量/decimal | 模拟验证，实网未验；估算含费/冲击但 executable=false；多跳、税费/交易模拟及完整发现未完成 |
| <a id="aerodrome"></a>Aerodrome | Base v1 stable/volatile | 按显式池状态、动态有效 fee、pause，以及池 `getAmountOut` exact-input 估算 | 模拟验证，实网未验；stable reserve ratio 不作边际价；执行模拟、多跳与完整发现未完成 |
| <a id="curve"></a>Curve | Ethereum StableSwap NG | 暂无 native reader；只有聚合观察和来源部署登记 | 原生状态/币种索引/规模报价未开发；实网未验 |
| <a id="raydium"></a>Raydium | Solana AMM V4 / CLMM / CPMM | 暂无 native reader；只有聚合观察和来源部署登记 | 原生 account 状态/精度/规模报价未开发；实网未验 |
| <a id="orca"></a>Orca | Solana Whirlpools | 暂无 native reader；只有聚合观察和来源部署登记 | 原生 account/tick array/规模报价未开发；实网未验 |

原生 EVM reader 检查 chain ID、非空代码与 factory 成员，并将全部读取固定在同一 canonical block hash；重组或节点不支持 EIP-1898 则失败。**成员检查成功不等于编译产物/实现字节码认证，也不等于交易可执行**。大整数/Decimal 字符串、block number/hash/time、实际方向、单池 route、fee/price impact included 均保留。所有原生规模估算 `executable=false`，无余额/授权、transfer tax/hook、Gas/MEV/交易模拟证明；不能注入为可成交订单簿。

部署地址及原生字段语义见 [DEX_SOURCES.md](DEX_SOURCES.md)、[DEX_CATALOG.md](DEX_CATALOG.md)、[ONCHAIN.md](ONCHAIN.md)。登记表中的 `onchain_verified=false` 没有因本地 fixture 成功改为 true。

## 诊断录制、资产审核与扫描

公共响应 payload 诊断录制已接采集钩子与 `GET /api/recording`，默认关闭，按 venue、保留天数和总磁盘上限配置。CEX 提供已标准化 CCXT 对象，DEX 提供解析后的公共 JSON；递归脱敏并隔离录制失败。这不是完整原始网络字节、全部帧记录或现成历史回放引擎。[RECORDING.md](RECORDING.md) 说明具体边界。

`GET /api/assets/candidates` 遍历完整目录生成身份/同 ticker 审核候选，`native_asset_id` 保留原始链地址证据；`PUT /api/assets/mappings/batch` 提供批量人工映射。人工映射不能抹掉原生链地址或单位冲突，comparison_ready 仅是身份门槛，不表示行情、深度或成本完整。全币种官方 chain/address 证据登记和包装/桥接单位模型仍未完成。

`GET /api/scanner` 已接入，遍历已启用平台的活跃目录与身份合格组，枚举跨 venue 双向现货/永续组合，返回买 ask / 卖 bid 价差及原始周期/8h 归一化资金费比较，可按 spread/funding/asset 排序。默认枚举预算 10000、最大 100000，输出默认 100、最大 1000；超预算明确为 `bounded_prefix`，不是全局最佳机会。缺价格、FX、周期、身份或时效的原因保留；演示/无效/未来快照不参与，`net_profit=null`、`executable=false`，不保证成交、费用或真实结算现金流。该扫描不自动发送消息，也不取代指定规模的七类计算与保存监控。本轮离线集成及最终接续浏览器已通过，不能当作真实成交验证。

扫描器现返回资产级汇总/best，并可展开该资产的原始市场与双向组合，逐腿展示ID/报价币/source/receipt/时效；选中方向把精确两个instrument ID与策略带入calculator，不按ticker重新匹配，也不自动提交计算。best明确是否只在已扫描范围；资产/平台筛选不隐藏原生身份冲突。该闭环与费用UI整合后的最终合成浏览器复验已通过。price/funding best仅从身份/有效字段/已知来源时间满足rankable的方向选择，未知源时间仍显示原因但不进可靠排行。

资金费专页 API `GET /api/funding` 已接入，按全部已发现活跃 perpetual 展示原始费率、实际周期、8h 归一化、类型、下次时间/倒计时及逐字段时效；未确认身份的市场仍可单独展示，只有身份合格组可作同资产矩阵比较。它是本地行情观测，不是已结算历史。`GET /api/funding/history/{instrument_id}` 另提供缓存读取及显式 `refresh=true` 的公共历史单页拉取；当前代码白名单仅 Binance USDⓈ-M/COIN-M、OKX、Bybit、MEXC、KuCoin Futures，需原生公开能力且无账户凭证。记录独立 SQLite、原始费率/来源/接收时间与修订；不支持后台自动全历史回填，forward since hint 不证明连续完整。资金费页和历史接口已通过本轮离线与浏览器回归，仍待实网核对，不因本地字段名为 settled 就宣称真实结算样本已验收。详见 [SCANNER.md](SCANNER.md)、[FUNDING_VIEW.md](FUNDING_VIEW.md)。

指定规模计算已增加 `min_amount` 最小委托数量门槛：现货按 base，永续按原生 contracts/乘数转换后的数量，不能因盘口使用 base 就跳过。显式配置的双腿 `holding_rate_per_hour` 在各持有期按各腿名义价值计费、各自只换汇一次，独立于资金费维持/减半/归零/反转情景；未配置短现货借贷费仍阻止净收益。这只是用户成本假设的核算支持，不是所有协议持仓费已自动接入。

费用配置 `GET/PUT /api/fees` 已接设置与calculator，112个venue×product×maker/taker档案中22条普通公开参考，来源/checked_at/scope与用户假设分开。Binance perpetual未核实保持null，Bybit特殊区、Hyperliquid spot/HIP-3等需额外市场审核或scope不符则不自动套用。未知/不适用不补零；按腿开平仓角色独立，切腿/产品清手填费与holding；本地覆盖可审计/恢复，GET不刷新来源日期，不代表账户等级费率。详见 [FEES.md](FEES.md)，相关563离线和7组费用浏览器通过。

浏览器 `/ws` 现为首次完整 `markets`，随后 `markets_delta` upsert 与 removed ID；仅数据年龄增长不重复推整个目录，字段由新鲜变过期仍发送，模式切换/重连重发完整状态。服务端仍逐连接构建当前视图，大目录 CPU/持续运行负载未完成实网验证。

## 验收与外部条件

官方 API/WS 数据域名仍受当前云环境 restricted/enforced 网络策略限制；可阅读官方 GitHub 源码不代表允许访问主网行情。RPC URL 默认空，主网链状态及原始真实样本没有验证；不绕过代理/策略。模拟通知符合用户“先不配置，模拟测试”的要求，没有真实飞书/Telegram 发送。

所有实现均须在可达的授权网络下逐平台核对实样、周期/单位/限流，再完成至少 24 小时真实采集及人工核算。Windows 实机启动也未验证。当前完整计划仍未通过验收，见 [STATUS.md](STATUS.md)、[ACCEPTANCE.md](ACCEPTANCE.md)、[VALIDATION.md](VALIDATION.md)。
