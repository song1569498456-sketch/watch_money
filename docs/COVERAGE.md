# 平台覆盖报告

统计时间：2026-11-04。本表记录代码范围，**28 个计划平台不等于 28 个实网已验收平台**。本云环境官方数据主机仍受网络策略限制；父线程已提供OKX/Bitget/Gate及Hyperliquid短时真实证据，见STATUS和AUDIT_BACKLOG；它们不能替代全平台或24小时验收。

当前阶段按用户最新顺序收敛：先资产级价差/best/双向组合/规模计算闭环，再资金费聚合，最后其他简洁指标与已有历史。不新增交易账户，不因下表列出原生协议缺口就先扩大其开发范围。

逐项修复与验收证据见 [AUDIT_BACKLOG.md](AUDIT_BACKLOG.md)，每批更新。公开费用、金额/切腿、退出UI与通知发送retry二次时效已完成本轮离线/隔离浏览器复验。完整三阶段和实网能力仍未通过，不因本覆盖清单存在而记完成。

新数据目录默认启用全部28个平台；已有启用配置保持不变。当前广覆盖按整产品批量与逐币回退分层，新配置目标价格/资金费周期30秒，目录1800秒重发现；存量周期不被强制修改。实际端点范围、限流下界、DEX整组支持及回退限制见 [COLLECTION.md](COLLECTION.md)。状态按读取时刻统计有效双边盘口与资金费，配置周期不等于实际完成周期，未提高60秒过期阈值。

批量资产证据源扩为Gate/Bitget/KuCoin/Coinbase Exchange官方币种目录，仅匹配原生currency ID、所列client及现货范围；现有Coinbase Advanced Trade不能套用Exchange目录。另支持可审计人工证据；未核实、歧义、包装/桥接和单位冲突保持隔离。见 [ASSET_VERIFICATION.md](ASSET_VERIFICATION.md)。DEX仍依赖来源实际提供字段，GMX/Jupiter/Drift等不会因调度扩大而升级为已完成。本轮测试证据见VALIDATION.md，当前环境实网未验。

## 12 个 CEX

共用能力为：公开市场目录、分批轻量 ticker、关注资产详细行情、按需盘口及受 CCXT Pro 支持的源 WS。永续的 funding/OI 只保留来源实际提供且单位可核对的字段。目录按 client 隔离部分失败；失败服务的旧身份保留并标为待核实，不当作完整目录或确认退市。下表首先描述代码范围；逐平台短时真实反馈和仍未验项目见本文末尾R38盘点，不应统称全平台已实网通过。

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

资金费专页 API `GET /api/funding` 已接入，按全部已发现活跃 perpetual 展示原始费率、实际周期、8h 归一化、类型、下次时间/倒计时及逐字段时效；未确认身份的市场仍可单独展示，只有身份合格组可作同资产矩阵比较。它是本地行情观测，不是已结算历史。`GET /api/funding/history/{instrument_id}` 另提供缓存读取及显式 `refresh=true` 的公共历史单页拉取；当前代码白名单仅 Binance USDⓈ-M/COIN-M、OKX、Bybit、MEXC、KuCoin Futures、Bitget Classic、Gate及HTX币本位永续，需原生公开能力且无账户凭证。记录独立 SQLite、原始费率/来源/接收时间与修订；不支持后台自动全历史回填，forward since hint 不证明连续完整。资金费页和历史接口已通过本轮离线与浏览器回归，仍待实网核对，不因本地字段名为 settled 就宣称真实结算样本已验收。详见 [SCANNER.md](SCANNER.md)、[FUNDING_VIEW.md](FUNDING_VIEW.md)。

指定规模计算已增加 `min_amount` 最小委托数量门槛：现货按 base，永续按原生 contracts/乘数转换后的数量，不能因盘口使用 base 就跳过。显式配置的双腿 `holding_rate_per_hour` 在各持有期按各腿名义价值计费、各自只换汇一次，独立于资金费维持/减半/归零/反转情景；未配置短现货借贷费仍阻止净收益。这只是用户成本假设的核算支持，不是所有协议持仓费已自动接入。

费用配置 `GET/PUT /api/fees` 已接设置与calculator，112个venue×product×maker/taker档案中22条普通公开参考，来源/checked_at/scope与用户假设分开。Binance perpetual未核实保持null，Bybit特殊区、Hyperliquid spot/HIP-3等需额外市场审核或scope不符则不自动套用。未知/不适用不补零；按腿开平仓角色独立，切腿/产品清手填费与holding；本地覆盖可审计/恢复，GET不刷新来源日期，不代表账户等级费率。详见 [FEES.md](FEES.md)，相关563离线和7组费用浏览器通过。

浏览器 `/ws` 现为首次完整 `markets`，随后 `markets_delta` upsert 与 removed ID；仅数据年龄增长不重复推整个目录，字段由新鲜变过期仍发送，模式切换/重连重发完整状态。服务端仍逐连接构建当前视图，大目录 CPU/持续运行负载未完成实网验证。

## 验收与外部条件

官方 API/WS 数据域名仍受当前云环境 restricted/enforced 网络策略限制；可阅读官方 GitHub 源码不代表允许访问主网行情。RPC URL 默认空，主网链状态及原始真实样本没有验证；不绕过代理/策略。模拟通知符合用户“先不配置，模拟测试”的要求，没有真实飞书/Telegram 发送。

所有实现均须在可达的授权网络下逐平台核对实样、周期/单位/限流，再完成至少 24 小时真实采集及人工核算。Windows 实机启动也未验证。当前完整计划仍未通过验收，见 [STATUS.md](STATUS.md)、[ACCEPTANCE.md](ACCEPTANCE.md)、[VALIDATION.md](VALIDATION.md)。

## R38 CEX数据与身份缺口盘点（2026-11-04）

“目录已接”不表示可比较；新鲜盘口、可核验身份、资金费周期和已结算历史分别验收。以下是代码盘点，不是本轮在线探测。当前四个公开合约身份源仅能为指定client的现货生成待审核证据；衍生品底层及单位证明仍不能从同ticker现货继承。其余8平台的公开自动身份源没有实现，不能从名称合并。

| 平台 | 已有数据路径及下一项数据缺口 | 身份发现范围与下一步 |
| --- | --- | --- |
| Binance | 现货/USDT与币本位永续、批量报价/费率、手动结算历史；逐平台公开实样与地区可用性待验 | 未接公开自动chain/address源，保留原生ID；不为补身份调用账户接口 |
| OKX | 现货/永续批量与历史；父线程确认原生费率时间存在过龄空窗，保持60秒门槛 | 未接公开自动chain/address源，广泛币种底层证据待核验 |
| Bybit | 现货/正反向永续、周期及手动历史；真实周期切换/字段时效待验 | 未接公开自动chain/address源，category与原生ID隔离 |
| Bitget | 批量报价/周期已有短时实证；本批新增Classic已结算历史，单页与实样待验 | 公开coins现货证据已接；与Gate的199候选组只做隔离库演练，生产未批准；永续仍缺底层证据 |
| Gate | 现货/三settle永续BBO已有180秒实证，静默市场覆盖不全；本批新增历史，当前费率独立时钟仍未知 | 公开currencies现货证据已接；BTC/USDT/USD1合约名不等于资产身份，不能借现货批准 |
| MEXC | 现货/永续及手动历史；单市场回退下的全目录时效待实测 | 官方Wallet接口需鉴权，本批不调用；未接其它合格公开合约证据源 |
| KuCoin | 现货与futures隔离、批量数据及futures历史；实样待核对 | 公共v3 currencies已接，严格原生currency ID；不延伸至kucoinfutures |
| Coinbase | 目前Advanced Trade公共现货，产品分页/批量覆盖待完善 | Exchange currencies证据源已有，但client不匹配Advanced Trade，禁止隐式套用；尚未接入Exchange行情client |
| Kraken | spot/futures目录与批量路径；历史结算语义未接 | 未接公开自动合约证据源，XBT等别名不能泛化为全币种核验 |
| HTX | spot/正反向永续批量路径；周期缺失仍不归一化，币本位历史已接R40、线性历史未接 | 已查公开Currency & Chains文档未列token合约地址，不能仅凭baseChain认同一资产；其它官方来源待核验 |
| Deribit | spot/perpetual目录；永续按settle批量摘要已接（R39，本地验证），现货仍有界路径；8h trailing不作为下一笔离散结算 | 未接公开自动合约证据源；连续资金费语义保留，不能套用本批历史实现 |
| Upbit | Korea现货目录/批量，KRW/BTC/USDT报价保留；无本项目永续范围 | 未接公开自动合约证据源；不替换地区服务或按ticker跨平台合并 |

本批官方核查：[Bitget Classic历史](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-historical-funding-rates)、[Gate历史](https://www.gate.com/docs/developers/apiv4/en/futures/#futures-market-historical-funding-rate)、[HTX公共币种/链](https://huobiapi.github.io/docs/spot/v1/en/#apiv2-currency-amp-chains)、[MEXC Wallet鉴权范围](https://www.mexc.com/api-docs/spot-v3/wallet-endpoints/)。前两项增加高质量结算数据，不提高实时资金费新鲜度，也不增加任何身份批准；后两项只记录文档核查边界，不代表已试过私有接口。

独立24小时测试固定其开始时提交；本批只更新现有开发分支、未修改任何正在运行的外部环境或其数据库。新历史功能需在独立实验中显式refresh核验，不应混入旧提交的24小时证据。

## R39 Deribit永续按结算币批量报价（2026-11-04）

官方[summary接口](https://docs.deribit.com/api-reference/market-data/public-get_book_summary_by_currency)提供当前买一卖一及creation_timestamp；[currency指南](https://docs.deribit.com/articles/currency-parameter)区分衍生品结算币与现货匹配。旧CCXT按symbol推导base且禁止混合base，不能据此实现USDC结算小币种的整组覆盖。现在永续批量使用symbols=None、code=原生目录settle、kind=future，再只按当前请求目录中的准确instrument_name/base/quote接收；dated futures、options和其它返回市场不能混入。本地150个不同base同settle只计划一批；跨settle仍隔离，现货及未知settle不标full_product。

每条来源时间取原始creation_timestamp毫秒，缺时间保留未知，不用统一parser的其它时钟或本地收到时间替代。原生bid_price/ask_price显式null清除对应侧，非数字/负值和错原生ID拒绝；旧帧/同时间正值不能越过清除屏障，较新快照恢复。volume按官方base单位，不按contracts乘数重算；未引入额外持仓量推断。

摘要中的current_funding/funding_8h没有被导入资金费标准字段，也未被当成已结算历史；其当前文档语义应单独审阅，不能从摘要字段名推断现有独立资金费端点的语义相同。本批未增加历史端点/资产批准，真实覆盖仍待外部验证；没有新增网络探测，不改独立24小时环境。已安装CCXT公共方法的离线请求替身验证实际USDC/future路由及原始时间解析，不能替代实网。

## R40 HTX币本位已结算历史与其他历史边界（2026-11-04）

[HTX官方币本位文档](https://huobiapi.github.io/docs/coin_margined_swap/v1/en/#query-historical-funding-rate)区分funding_rate与realized_rate，公共GET swap-api/v1/swap_historical_funding_rate无需签名。当前已安装CCXT虽然保留原始字段，却将funding_rate放入统一fundingRate；本服务只使用原始realized_rate，null/非有限值拒绝，不拿预计或名义费率补空。

新范围仅swap/inverse=true/linear=false、费用币与settle/base一致的币本位永续，准确contract_code/symbol/fee_asset及funding_time毫秒必须匹配目录/统一时间。每次手动刷新最多50条、page_index=1、paginate=false；since仅本地筛选，不代表旧页回填。历史周期未知，不引用文档一般8小时或今日目录推断所有历史；保留原始费率、名义funding_rate、单位与时间字段。公共realized_rate不是用户账户实际账单证明。

HTX线性CCXT已路由v5 market funding_rate_history，不直接沿用旧USDT文档或币本位realized_rate规则，本批明确501未审阅；官方旧USDT文档本次读取超时。Kraken已发现[公开历史接口](https://docs.kraken.com/api-reference/historical-funding-rates/historical-funding-rates)，但[官方线性合约说明](https://support.kraken.com/articles/4844359082772-linear-multi-collateral-derivatives-contract-specifications)涉及连续资金费，绝对金额与relativeFundingRate不同；仍不能接入当前“离散已结算”记录模型，保持未支持并待单独建模。上述均不是地区不可用结论，也没有尝试账户或地域替代。

本批只做文档与本地合成/安装解析器测试；新历史实样、保留范围及单位仍待独立实测，没有修改正在运行的24小时环境、生产身份映射或账户。

## R41 Hyperliquid历史补齐（2026-11-04）

原生公开fundingHistory接入已有已结算历史入口，首个perp dex和HIP-3保留完整命名空间，原生time毫秒、fundingRate小数比例及官方1小时周期；不从premium或8h计算公式推算历史值。单请求500行/500小时上限、截断游标和目录变化检查，访问拒绝不绕过。没有用户参数、账户调用、实时费率或跨平台身份批准。普通与HIP-3合约历史真实样本、上游保留范围及完整历史仍待独立实测。官方来源和临时库复测步骤见FUNDING_VIEW R41；原24小时测试不变。

## R43 dYdX历史与24小时条件补充（2026-11-04）

dYdX v4公共historicalFunding新增已结算历史，准确ticker、带时区effectiveAt、原始height/rate；治理可能改变epoch，逐条周期未证实则null。最新单页最多100条，不提供旧页自动回填，尚无实网验证。官方来源/临时库复测步骤见FUNDING_VIEW R43；生产身份未批准。

独立长测的403发生于OKX公共ticker WebSocket经本地代理建连，保留错误仅代理地址，缺底层响应详情；此前51.7秒计时延迟及其他平台覆盖同时下降。不能归因单平台，亦不能确认代理策略/上游/短暂基础设施原因。守卫在13分50秒停止，24小时未完成、不重试或绕过；需要用户环境或支持方核查运行、代理和网络条件。更新证据摘要deribit-replay-and-guard-stop.json，未触碰原环境。

### R44 身份及历史边界复核

本批修复历史请求与目录更新的一致性，未新增平台或批准身份。四个官方公开身份来源继续遵守原生client/现货范围，Coinbase Exchange证据不能替代Advanced Trade或永续证据；199组Gate/Bitget候选仅隔离演练。未知合约证据及历史周期保持未知，详见AUDIT_BACKLOG R44。未调用受阻OKX WS，24小时验收仍未完成。

### R45 历史来源真实性

dYdX/Hyperliquid历史保留观察者前HTTP响应receipt，来源字段变化有完整旧记录修订。仅合成回归，无新增平台、身份批准或实网覆盖；未知周期不变，24小时仍未完成。详见FUNDING_VIEW R45。

## R46 HTX线性历史补齐与下一项

新增官方USDT v1公开已结算历史，固定文档主机api.hbdm.com，严格USDT永续/realized_rate/原生身份和毫秒时间，最多50条最新页、历史周期未知，不使用未审阅的v5替代。14项新离线回归、99项联合历史专项；当前接口实网可用性仍未知，没有生产身份批准。官方来源、边界及复测见FUNDING_VIEW R46。R38/R40中“线性未接”描述的是当时状态；目前v1已实现但未实网验收，v5仍未审阅。

下一项为Coinbase Exchange公开目录与现有身份资料的client范围衔接；需要单独验证行情时间，不向Advanced Trade或永续继承身份。已拒绝OKX WS及Lighter连接不重试，24小时未完成。

## R47 Coinbase Exchange已接入的边界

新增独立coinbaseexchange现货目录和原生book L1/L2，衔接已有Exchange currencies证据，不套用到Advanced Trade或永续。原生目录币种冲突显式记录且目录不标完整；时间使用book.time、数量不乘num-orders、竞价/空侧不保留旧盘口。17项新离线回归及已有111项相关回归通过；无实网和身份批准。R38中“尚未接Exchange client”为当时状态，现已实现但全目录实时覆盖、公共端点可达性和长期运行仍待独立验收。详见COLLECTION R47官方来源及步骤。

## R48 Coinbase产品状态边界

父线程b960509的5次公开GET验证BTC L1/L2、PEPE L1原生时间，属于短时报告而非24小时证据。online受限产品仅合成复现；现在分开目录在线与即时成交资格，disabled/cancel_only/post_only报价保留但不可靠排行或计算可成交净收益；maker订单许可不代表post-only下撮合，limit-only可跨价限价撮合。目录刷新解除可恢复，时间与来源字段保留；未新增实网请求或身份批准。见COLLECTION R48。

## R49 关注ticker流与全目录盘口区别

通用CEX流按watch_assets选择关注币种，并非catalog_stream或全目录book流。Coinbase Exchange原生L1/L2仍是有界REST轮转/按需深度。本批修复CCXT返回后的观察者延迟污染receipt及未来水位，仅离线回归；没有新增实网、身份批准或已拒绝连接重试。见COLLECTION R49。

## R50 dYdX旧页能力

dYdX历史新增显式before_height向前查询，与本地缓存高度筛选一致；每次一页最多100条，next_before_height仅保守提示，无后台回填或完整性承诺。R43最新页限制被此项扩展，历史周期仍未知。未新增实网、身份批准或受阻连接重试；详见FUNDING_VIEW R50官方来源与复测步骤。

## R51 Bybit/OKX历史真实性

两平台历史新增原始合约ID、结算毫秒与统一字段交叉校验，缺原始率不回退；OKX X-Perps/FUTURES不并入SWAP。保留单页100/200，拒收不生成继续提示，历史周期未知。129项历史专项，源文档与安装解析器路由证据见FUNDING_VIEW R51；未访问受阻连接，未新增实网或身份批准。

## R52 撤回不安全的dYdX自动高度游标

官方响应缺唯一事件cursor，高度不保证唯一；next_before_height恒null且pagination_incomplete=true。只保留人工高度筛选，不能保证边界完整。刷新返回实际本页，普通缓存读取单独标明；同页高度/时间冲突拒收且不生成修订。R50的自动减一提示和连续翻页步骤已撤回，详见FUNDING_VIEW R52。未新增实网证据。

## R53 Binance原生历史复核

USDⓈ-M/COIN-M历史加强原生symbol/fundingTime/fundingRate校验、单位和来源记录；USDⓈ-M Special独立保存，COIN-M未知特殊类型拒收。同页冲突不修订，满页与拒收停止时间游标，历史周期未知、完整性不承诺。只做离线验证，无实网/生产身份变更；dYdX R52限制不变。详见FUNDING_VIEW R53。

## R54 MEXC/KuCoin周期依据

MEXC历史collectCycle经官方核实为小时，仅有效正整数保留，原始周期另存；KuCoin Classic无逐条周期依据，保持未知。两平台原生symbol/结算时钟/率交叉校验，明确MEXC最新page1与KuCoin from/to窗口，不发完整继续提示，不访问私有账单/UTA替代。仅离线，dYdX R52及24小时限制不变。来源与测试见FUNDING_VIEW R54。

### R55 Upbit盘口覆盖

Korea全目录轻量轮转新增原生公共book补充，最多50市场一批、准确native ID、level=0、独立毫秒源时间；按需30档深度保留base数量。旧ticker成交统计保留独立时效。120市场离线样本50/50/20分批通过，不等于真实覆盖；本轮实网采样0、生产身份批准无变化。全目录盘口WS、实际吞吐、跨KRW报价换算与24小时仍未验收。详见CEX_LIGHT/AUDIT_BACKLOG R55。

### R56 历史原生页完整性修复

MEXC/KuCoin先校验完整有界原生响应再截取显示，避免SDK limit=1隐藏同刻冲突。MEXC固定公共页100，KuCoin完整from/to窗口本地最多1000，超限整页拒绝；无自动继续游标或完整历史承诺。原生路由保留SDK限流与错误处理，周期/单位未知边界不变。此为离线对抗回归，不增加实网平台或身份覆盖。
