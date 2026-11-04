# 全目录采集、时效与覆盖

本轮修复的是调度吞吐，不放宽 `stale_seconds=60`。
此前每100市场请求后等待30秒价格/60秒资金费，1000市场至少需要300/600秒一轮，还会反复请求实际上已返回整个产品的端点。
REST成功、收到一条新消息和全目录数据新鲜分别计量；缺源时间、实际周期、类型或可信来源时，不把最近HTTP接收伪装成可靠行情时间。

## 分层调度

1. 已审查的全产品端点按client/产品族构建计划，一次取回该组全部市场，不再固定切成100市场循环。通过已发现身份筛选响应，额外返回项不会混入目录。
2. 原生批量与逐symbol回退使用独立worker/时钟。两种worker分别限并发，仍共享CCXT权重限流与4个总请求槽，保留详细关注采集的可用槽位。
3. 全轮内部只短暂让出执行，完成整轮后按“本轮开始到下一轮开始”的目标周期等待。一个慢速回退层不阻塞全产品层。
4. 部分错误保留成功市场；网络错误只记录类型并退避。全产品方法运行时拒绝能力后，下一次计划降为有界回退，不立即启动上千个单symbol请求。
5. 计划签名包含产品组及全部市场身份；新增/移除市场或批次改变会重置该层完成标记。旧计划的在途请求不能宣称新目录已完成一轮。
6. OKX原生全产品资金费另按**这次响应的到达源年龄**自适应缩短目标间隔，见下节；逐symbol回退不采用该提速规则，请求错误仍优先退避。

`fetch_light(instruments)` / `fetch_funding_light(instruments)` 的直接调用仍最多100市场、每批最多50 symbol。
全目录collector使用 `plan_light(...)` 和 `fetch_light_batch(...)`，只有审核通过的单产品组能绕过symbol数量边界。此时传 `symbols=None`，由端点返回整产品；并非扩大URL中的symbol列表。
API返回整个产品不代表每个市场都确实返回：缺失记录单独列错误，并由实际缓存重新计算覆盖。

## 公共批量范围及依据

这些是公共行情契约和已安装CCXT 4.5.91的方法检查，不是线上可达性承诺。
CCXT `has=True` 本身不等于全目录一次HTTP：Coinbase产品列表可能分页，Deribit摘要另有币种约束，因此仍走有界symbol计划。

| 来源 | 计划范围 | 限流/契约依据 |
| --- | --- | --- |
| Binance spot、USD-M、COIN-M | 各自产品整组ticker；衍生品整组premiumIndex资金费 | [现货ticker](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints)：省略symbols返回整组，24h端点全产品权重80；CCXT启用权重限流。[USD-M标记价](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price)、[COIN-M指数/标记价](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Index-Price-and-Mark-Price)按产品分别请求 |
| OKX | SPOT/SWAP各一个ticker组；资金费一个全部永续组 | [ticker](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers) 按instType；[funding-rate](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate) 明确 `instId=ANY` 返回全部永续与X-Perps，10请求/2秒，规则IP+Instrument ID。安装CCXT `fetch_funding_rates`正是ANY公共请求 |
| Bybit | spot、linear、inverse产品族；linear合并不同结算币 | [tickers](https://bybit-exchange.github.io/docs/v5/market/tickers) category下symbol可省略，含全组资金费；[限流](https://bybit-exchange.github.io/docs/v5/rate-limit)仍由启用的CCXT限流器及请求失败退避执行 |
| Bitget | spot及USDT/USDC/COIN产品族，显式productType | [公共市场目录](https://www.bitget.com/docs/catalog/market/derivatives)公开当前资金费支持按category，不读取账户；CCXT已核对旧public mix ticker整产品路径。不同产品不混用默认类型 |
| Gate | spot及每种settle的futures组 | [官方公共API](https://www.gate.com/docs/developers/apiv4/en/)：ticker整组、资金费使用该settle的公开contracts列表；保持CCXT限流 |
| MEXC | spot/swap整组ticker；资金费仍逐symbol | [资金费](https://www.mexc.com/api-docs/futures/market-endpoints/get-funding-rate)20请求/2秒；安装CCXT rateLimit50ms、该方法cost2。1000市场理论至少100秒，尚未计网络、其他请求及重试，不能满足60秒全量新鲜 |
| KuCoin、Kraken及独立futures客户端 | 已安装CCXT公开整产品ticker/受支持funding列表 | 依据各客户端公开方法对应的[CCXT实现](https://github.com/ccxt/ccxt/tree/master/python/ccxt/async_support)，KuCoin ticker明确uta=False。没有为提高覆盖改用账户接口 |
| HTX | spot及正反向ticker组；inverse资金费可批量 | 已安装CCXT linear批量资金费明确NotSupported，linear保持有界单市场路径，不因has=True宣称覆盖 |
| Upbit Korea | 全报价市场ticker | [韩国官方 `/v1/ticker/all`](https://docs.upbit.com/kr/reference/list-quote-tickers)，quote_currencies列表，ticker组共享10请求/秒；不替换为其他国家API |

OKX若旧SDK没有ANY批量能力或服务拒绝该能力，会降为逐symbol。官方10/2秒意味着1000请求至少200秒；安装CCXT 110ms基础间隔、cost2时下界为220秒。
默认30秒并不能改变这个限制。状态预估会保留已知权重下界，再结合实际观测；不会将“配置30秒”显示成“实际30秒完成”。

## OKX源数据接近过期时的调度

父线程提供的外部150秒观测显示，OKX可靠资金费市场数曾从约540降到6再恢复，收到响应时部分`ts`已接近60秒。此处不把该报告描述成本子任务本地实测。
HTTP成功只能说明收到响应；若源数据在到达前已经变老，再按固定30/60秒等待会延后发现上游下一次更新。

本次核对[OKX官方资金费WebSocket文档](https://www.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel)说明推送间隔30–90秒，`ts`为数据返回时间。
[REST资金费接口](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate)支持`instId=ANY`，限流10请求/2秒（IP+Instrument ID）。
因此WebSocket并非60秒有效性的保证；此次没有增加OKX资金费WebSocket订阅，更没有借盘口时间更新资金费。

对已审查的OKX **全产品**资金费计划，从入站patch计算`funding_rate`源时间到其独立接收时间的年龄。
只接受共享可信门槛允许的正常或已过期率；缺时间、未来时间、无可信来源、demo、invalid及身份不匹配不参与采样。
按排序后nearest-rank p90计算下一整轮开始间隔：

`min(配置 funding_poll_seconds, max(5秒, stale_seconds - 到达年龄p90 - 5秒余量))`

在60秒TTL、30秒配置下，0秒年龄仍取30秒；40秒取15秒；55秒或已过期取5秒。
这是最多每5秒一次`ANY`全产品请求（至多12次/分钟，官方该端点预算的4%），不是每市场5秒一次，也不是全进程请求预算承诺。
CCXT限流、既有并发槽和失败退避仍生效；实际周期可能更长。连续收到相同旧`ts`也不突破此上限，不能因HTTP成功重置源年龄。
单个极老记录不会支配p90，未被p90代表的尾部市场仍可能过期；所有fresh计数继续逐市场执行原TTL。
没有可信年龄时保留配置周期并标记`source_age_unknown`；其它平台及symbol回退维持原计划。

这缩短了发现下一次上游更新的等待，**不保证消除缺口**。上游自身时间超过TTL、推送间隔过长、缺记录、请求延迟或错误时，可靠覆盖必须下降。
本次没有增大`stale_seconds`、重写原`ts`、以本地接收时间冒充源时间，或把“当前可比较”改为“最近HTTP成功”。
父线程后续反馈启用自适应后仍有一次540市场同时过期；本任务没有该时段完整的原始响应、到达年龄和轮询完成间隔序列，不能判定是上游发布节奏、请求/处理延迟还是其它因素。
需对齐`funding_arrival_checked_at`、到达年龄、`funding_cycle_seconds`和脱敏请求错误再分析；单次fresh=0既不能证明请求失败，也不能证明调度已修复所有缺口。

## DEX范围

仅已确认整个public payload的路径采用全产品计划：

- Hyperliquid：每namespace一个metaAndAssetCtxs请求，不把不同HIP-3目录合并；[官方共享预算](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)为1200权重/分钟，该info请求权重20。namespace间至少2秒，将此层控制在最多约600权重/分钟，保留余量给发现和详细采集。不是整个进程所有请求的统一限流保证；namespace太多时仍可能超过60秒。
- Lighter：每轮两个全市场公开GET（orderBookDetails、funding-rates）。
- dYdX：全市场perpetualMarkets一次GET。
- GMX：已有Arbitrum V2 ticker整组GET；不据此声称有可执行规模报价或完整资金费。

其它搜索/逐pool/RFQ或“1+N资金费”路径保留原有有界调度，mode=`legacy_bounded_public_fetch`，不擅自加速。
generic批次默认继续100，避免把这些路径从100改20后重复全payload更多次；未核验能力不算原生bulk。
DEX资金费若来自同一公开payload，状态mode为 `via_public_price_bulk`，但fresh计数仍逐资金费字段过共享可信门槛，不继承价格时效。

## 流采集的目录检查开销

父线程外部反馈Hyperliquid独立采集曾维持347/347新鲜约105秒，而集成进程出现127→187→断线→0的过程；其旧版每patch计算347条JSON签名的测量是性能线索，**并非已确认的断流根因**。
本次仅优化collector流处理热路径，不修改`merge_patch`，也未对受限的Lighter服务发起探测。

`run_stream`将SQLite设置读取、目录筛选/排序和完整metadata JSON签名统一限制为每秒至多一次；没有消息时也按同一单调时钟deadline检查。
接收完成后先处理已到期的检查，停用或订阅签名改变时取消旧的pending receive并关闭旧订阅，随后按新目录重订阅。签名仍包含完整metadata，所以同ID合约乘数修改和退市不会被仅比较ID漏掉。
每条patch的身份检查使用集合，平均O(1)；保存周期使用最近一次已检查的设置，不再为每条消息另查SQLite。
目录/设置变化通常在下一次一秒检查时生效；事件循环阻塞、耗时保存/清理仍可延迟处理，这不是实时线程的绝对延迟保证。停止任务仍可直接取消pending receive。

可复现的本地合成负载：

```sh
.venv/bin/python scripts/stream_selection_load.py --markets 347 --patches 3000 --rate 300 --output docs/evidence/stream-selection-load.json
```

脚本仅从本地Git `eaeb822`提取旧`run_stream`函数，与当前实现对比；固定347市场、3000条patch、目标300条/秒，每版约10秒。
两个版本都包含真实本地SQLite设置读取和模型验证，排除网络、WebSocket解码、`merge_patch`以及快照磁盘写入，以隔离目录检查开销。该CPU测量不代表完整生产采集成本。

| 此次本地样本 | 旧循环 | 新循环 |
| --- | ---: | ---: |
| 完整目录签名检查 | 3001 | 10 |
| JSON市场序列化行数 | 1041347 | 3470 |
| 设置读取次数 | 6001 | 10 |
| 进程CPU秒 | 4.859568 | 0.543388 |
| 10ms定时器延迟p90（ms） | 1.238 | 0.882 |
| 定时器最大延迟（ms） | 14.311 | 67.963 |

[完整合成测量](evidence/stream-selection-load.json)保留样本数、Python版本及排除项。新循环CPU减少约89%，但最大定时器延迟反而有一次更大的抖动；不据此声称所有事件循环延迟都改善，也不把单进程样本当实网断流因果证据。
`tests/test_stream_collector_performance.py`用独立模拟时钟验证10000条patch/347市场在两个模拟秒内仅两次设置与签名检查，以及高消息率下metadata/退市重订阅、闲置时五类停用设置取消和错误脱敏。

## 配置及存量设置

| 设置 | 新默认 | 含义 |
| --- | --- | --- |
| stale_seconds | 60（未改变） | 原始时效门槛；周期元数据另按明确来源的6小时TTL |
| broad_poll_seconds | 30 | 完整原生价格sweep的目标开始间隔 |
| funding_poll_seconds | 30 | 原生资金费sweep基础目标间隔；OKX可信源接近过期时可按上述规则缩短。旧已保存60秒数值继续保留 |
| broad_batch_size | 100 | generic/未审核路径批次上限，未改为20 |
| broad_symbol_batch_size | 20 | CEX symbol回退上限，实际取与broad_batch_size的较小值 |
| broad_inter_batch_seconds | 0.25 | sweep内让出间隔；平台最小间隔仍优先 |
| broad_request_timeout_seconds | 25 | 单计划单元总超时，超时会取消未完成任务 |

存量设置由Pydantic补新增字段，不静默覆盖用户已保存的周期或过期门槛。
如果用户仍配置资金费60秒、过期60秒，网络及处理延迟足以造成边界过期；OKX原生全产品层现在另做有界自适应，其它路径仍可能按原周期越过边界。应在覆盖状态里明确看见，而不是暗中延长TTL。

## 状态字段

`light_` 与 `funding_` 各自提供：

- `subscriptions`：当前活跃目录分母；`bulk_markets`、`fallback_markets`、`bulk_groups`说明计划分层。group不是HTTP次数承诺，部分CCXT方法内部仍可能多次请求。
- `observed_markets`：存在非demo缓存字段的市场数，不等同可靠/可用行情。
- `fresh_markets`、`coverage_ratio`：**读取状态时重新评估**。价格要求bid与ask都通过共享可信时效门槛；新last或资金费envelope不能续期旧买卖价。资金费要求rate/kind/实际周期均rankable。
- `last_attempt_at`、`last_received_at`：最近尝试与接收；不用于替代字段源事件时间。
- `last_cycle_at`：所有实际存在的层都完成本计划至少一轮后，取较旧完成时刻；不是“全目录100%新鲜”的声明。
- `cycle_seconds`：两次完整sweep完成之间的实测间隔，包含目标等待、请求/保存开销；首轮或目录签名变化后暂为空。
- `estimated_cycle_seconds`：目标周期、已知请求权重下界、已完成批次/整轮实测外推的较大值；不是保证。回退过慢、缺数据或受限时必须结合fresh比例与errors。

资金费另外提供：

- `funding_effective_poll_seconds`：原生全产品层本轮选定的开始间隔目标，包含本次计算的等待/错误退避；不是已实测的整轮完成间隔（后者仍是`funding_cycle_seconds`）。
- `funding_schedule_reason`：`configured`（基础周期）、`source_age_unknown`（无可信响应年龄）、`source_near_expiry`（缩短等待）、`upstream_source_stale`（p90源数据本身已过期）或`request_backoff`（请求错误退避）。
- `funding_arrival_source_age_p90_seconds`、`funding_arrival_source_age_max_seconds`、`funding_arrival_source_age_samples`、`funding_arrival_checked_at`：最近一次成功资金费响应中可验证的到达年龄分布、样本市场数及检查时间；不从合并缓存生成，不随状态读取而变年轻或重新采样。失败保留上次分布和检查时间；成功但缺可信时间时分布为空、样本0。多产品/回退时描述最近完成的那个响应批次，不是全venue同一时刻的普查。
- `funding_source_stale_markets`、`funding_source_missing_markets`、`funding_source_future_markets`：读取时按共享字段门槛统计已有非demo原始rate的市场。分别为可信rate已过期、缺可验证时间/来源、时间在未来；未采集市场不计入这三个数。它们解释源数据问题，不写进请求错误或增加请求重试数。周期/类型等其他门槛仍会让`fresh_markets`更低。

`funding_errors`继续描述请求/能力/缺记录错误。比如HTTP成功但源`ts`已老61秒时，`funding_errors`可为空而`funding_source_stale_markets`大于0；不能把这种情况当作网络在线就声称资金费可靠。

`Collector.refresh_status_coverage()`先对目录和缓存取稳定快照，再按venue复用；状态接口每次调用，健康任务也定期调用。停机、禁用、长退避期间fresh会随真实字段年龄下降，observed与历史轮转信息可保留。

流采集诊断独立于源字段时效：`stream_received_patches`累计成功处理的patch数；`stream_last_patch_at`是最近一次本地处理时刻；`stream_last_gap_seconds`和`stream_max_gap_seconds`是相邻成功处理的单调时钟间隔，包含重连、上游沉默、排队和本地处理等待。
这些不是交易所事件时间，也不保证每个symbol或每种字段都更新；不得用它们续期price/funding。
`stream_last_error_type`和`stream_last_error_at`只保留最近一次错误类名及本地记录时刻，不保存异常文本、URL或凭证。恢复后保留历史错误以便对齐gap，当前状态仍看`stream_state`。

## 验证与剩余限制

`tests/test_collection_schedule.py` 使用合成公开对象验证千市场价格/资金费各一次bulk方法、全目录覆盖、低速回退不占批量槽、已知100/220秒下界、读时过期、demo排除、last不能代替双边价、目录变化重置完成状态，以及DEX审核范围。
新增合成顺序覆盖到达时55秒仍有效、同一源时间61秒后过期、请求超时退避、可信新源恢复；另覆盖未来/缺时间/demo/invalid不被采样，以及读取状态不重写最后一次响应年龄。
这些是模拟调度结果，不是实网吞吐；官方文档和已安装解析器检查也不替代真实样本。
本环境公共API受代理限制，根线程另保留 `docs/evidence/data-review-live-probes.json`；本子任务没有绕过网络限制或把父报告描述成本地实测。
待在允许的环境逐平台测量真实耗时、缺失symbol、限流与持续覆盖。无法批量的来源、过多namespace、缺可信源时间或服务失败时，本实现明确允许fresh覆盖低于100%，不宣称所有fallback都能在60秒内完成。
