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

## HTTP、推送与写入的阻塞诊断

父线程`df55e83`实网集成反馈包含`/markets`最长约10.6秒、scanner多秒以及HL重连/OKX过期，但尚无能建立因果关系的统一trace。本批只做只读分析和本地诊断，未修改生产性能路径或时效门槛。

代码检查发现：`/api/markets`与scanner主体虽为sync endpoint、在线程池执行，当前安装FastAPI对无response model的dict仍在事件循环内执行`jsonable_encoder`，随后Starlette同步`json.dumps`完整响应。`/ws`也直接在async循环里重建全部`collector.markets()`、计算delta并编码；即使最终delta很小，前面的全目录工作仍发生。
此外`run_planned_light.summarize()`会同步重算全部venue覆盖；无批次的worker每轮也在计划后及`not_applicable`分支各调用一次。健康任务另定期全扫。它们是需单独profile/限频的候选热点，不能把单次耗时直接当成真实运行调用率。

复现命令：`.venv/bin/python scripts/api_blocking_diagnostic.py --sizes 1000 10000`。
[原始结果](evidence/api-blocking-diagnostic.json)使用临时库和合成四平台同身份市场，无交易所、TCP、浏览器或后台collector；每阶段一个样本，机器未隔离。
10000市场时：

| 本地诊断阶段 | wall耗时 | 10ms timer最大迟到 |
| --- | ---: | ---: |
| 真实ASGI `/api/markets`，约17.36MB | 1.387s | 1179ms |
| 真实ASGI scanner，默认扫描上限10000方向 | 0.836s | 137ms |
| 两个请求并行 | 2.104s | 1307ms |
| markets构建，直接在loop执行 | 0.215s | 210ms |
| markets `jsonable_encoder`，直接在loop执行 | 0.870s | 865ms |
| markets JSON编码 | 0.102s | 97ms |
| 全venue覆盖重算一次 | 0.111s | 106ms |
| 三个并行1000行SQLite+Parquet写入，在线程执行 | 0.075s | 5.23ms |

三个真实写入调用因`Storage.lock`串行，最长锁等待约47ms。该锁覆盖latest序列化、SQLite及Parquet写入；历史查询也持有同一锁，理论上能使等待中的collector保存延后，但此样本**没有复现多秒写争用**，不据此认定数据库为根因。
测量证实大响应序列化可阻塞本地事件循环，也显示线程中的CPU工作仍可能争用GIL；它没有复现父报告10.6秒，也不能解释具体TimeoutError或OKX源数据更新缺口。下一批性能改动应分别测量序列化、覆盖重算调用频率、推送和真实写入等待，保持现有正确性门槛。

## 重查询准入与诊断

`QueryWork`现在为`GET /api/markets`、`GET /api/scanner`、`GET /api/funding`及`/ws`的build/render提供同一个专用单线程执行槽；HTTP最多排队2项、等待1秒。队列满或超时返回`503`、`Retry-After: 2`及`code=heavy_query_overloaded`，不创建替代行情或刷新旧源时间。前端应保留已有数据并按原字段时间继续老化。
WebSocket只在执行槽及HTTP队列都空闲时构建新帧，否则跳过本帧且不更新上次发送的delta基线。客户端取消无法杀死已运行的Python线程，因此槽保持到实际工作完成，避免取消后隐性并发增加。

`GET /api/query-work`是轻量诊断：显示当前active/queued、容量/超时，以及固定内部route的method/path、完成/失败/拒绝/取消计数。`last_execution`提供`queue_ms/build_ms/render_ms/wall_ms`、响应字节、`worker_cpu_ms`和`process_cpu_ms_during_job`；后者包含同进程其它线程，不能当成该查询独占CPU。`last_rejection`独立标记排队超时/队满/WS跳帧。诊断不保留查询参数、响应payload、异常文本、URL或凭证，仅错误类名。
app lifespan启动每0.5秒的本地timer采样，提供最近/最大调度迟到及采样时刻；关闭时取消采样、等待已接纳worker并关闭executor。timer迟到不是网络延迟或数据源年龄。
该控制限制并发和排队放大，不保证单次大查询耗时、交易所连接或全市场fresh覆盖；不缓存结果、不修改数据源时间/`as_of`含义，也不据此断言已解决实网PongTimeout或资金费过期原因。

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

## 响应编码优化后的本地对照

`/api/markets`、`/api/scanner`、`/api/funding` 的同步端点直接在工作线程构建 `JSONResponse`，避免大字典回到事件循环再由框架递归编码。浏览器 `/ws` 每次构建视图、计算delta、编码文本也在工作线程完成，网络发送仍在事件循环；每个连接同一时间仅构建一个帧，不增加发送队列。目录和快照成员先复制，避免与采集更新并发遍历字典。JSON形状、字段时效与扫描限制保持不变。

相同诊断脚本的一次合成10000市场对照（共享机器、非配对基准、无交易所流）见 `evidence/api-blocking-after.json`：实际ASGI markets 1.387→0.451秒，事件循环最大迟到1179→109ms；markets与scanner并行2.104→1.003秒、1307→107ms。响应仍约17.36MB；工作线程仍竞争GIL，不代表消除所有阻塞。脚本保留直接在loop执行的分阶段诊断作为基线，并不代表优化后路由仍如此执行。

父线程补充的133秒轻查询实网对照达到HL347/347后仍在约84秒TimeoutError，最低152，最大patch间隔约20秒；OKX轻查询50秒后样本均540。该证据支持重响应会加重问题，却不能将其认定为唯一原因。本批没有成功实网或24小时验收，身份覆盖和Lighter地区限制继续开放。

独立响应验收（`tests/test_response_worker_acceptance.py`）用非空行情验证三路新旧JSON完全一致、源/接收时间不变；在构建线程被阻塞时取消WS，取消后不补发帧或启动下一轮；并发替换目录与快照不会改变已捕获成员。取消无法硬终止已经运行的只读线程，该轮计算会收尾，但不会再发送或继续排队。

总览价格状态现在与显示字段对应：`price_fields`选择bid/ask或last/mark/index，`price_valid`描述这些价格和时序是否有效，`price_source_known`说明是否有其自身源时间，`price_fresh`还要求未过期。既有`valid`保留结构层意义，不应单独用于绿点/新鲜计数；无价格、单侧且无参考价格、未知源时间或异常时间不计新鲜。无关资金费字段的stale标记保留，不给价格字段续期，也不替代资金费门槛。

本批合成10000市场样本：markets0.481秒、scanner0.730秒、并行二查询1.215秒，10ms诊断timer最大延迟约113ms；实际query-work的0.5秒采样只测到最大6.253ms，采样间隔不同不可相互替代。此次与完整pytest/浏览器工作共享机器，不把数字当吞吐保证。证据query-admission-diagnostic.json中的每路队列/CPU/阶段计时用于定位，诊断端点包含最后完成与进程启动以来总耗时最慢的任务（slowest_execution，样本采集后新增并单独回归）；不记录请求参数或返回行情。此前父线程markets27.16秒和HL应用pong超时仍需同时间实网轨迹解释，未放宽任何阈值。

## 可控卡顿取证（2026-11-04）

在同一环境、同一公开平台集合下，启动应用前设置 `WATCH_MONEY_STALL_PROBE=1`，重启应用后访问 `/api/query-work`。默认不开启；不改账户设置、不读取账户 API，也不改变价格/资金费有效期。诊断线程每 0.5 秒检查事件循环心跳；超过 2 秒捕获，10 秒冷却，只在内存保留最近 12 次（重启清空）。关闭应用会停止线程。

`stall_probe.events` 含观测 UTC 时间、心跳年龄、独立线程自身调度延迟、最多 16 个线程各 24 帧的文件 basename/函数名/行号，以及累计进程 CPU、最大 RSS、上下文切换、可读时的 cgroup v2 CPU 限流和 OOM 计数。无局部变量、源代码、参数、环境、绝对路径、请求体或凭证。`ru_maxrss` 在 Linux 单位 KiB；CPU/限流计数要比较相邻样本差值，不是当前使用率。未知 cgroup 数据为 null。捕获有开销，所以默认关闭且限频。

建议外部验证分两轮，每轮记录启动/结束时间、提交、平台、是否开启探针及 `/api/query-work`：

1. status-only：固定低频请求 status 与 query-work，记录客户端请求耗时、失败和采样空窗；不调用 markets/scanner/funding。同时观察 HL/OKX 原始来源新鲜度及断连原因，保持既定有效性门槛。
2. 相同配置下按固定频率加入 markets/scanner/funding，记录 query-work 中 active/queued、各阶段 CPU/墙钟时间、拒绝计数和探针事件。不要无界并发。若外部执行服务也断开，单独记录其时间区间，与应用观测对齐。

`loop_heartbeat_stall` 表示观察线程仍调度而循环心跳停止，查看 event_loop 栈是否持续停在同一同步调用；其他线程可提供锁/工作线程线索。`shared_scheduling_gap` 表示观察线程自身也晚醒，可能是环境暂停、CPU 限流、GIL 或调度问题，不能据此唯一归因。恢复后抓到的栈不代表停顿期间的栈。Python 无法保证在进程被冻结或 GIL 被长期持有时抓栈，需结合宿主或执行服务证据。该探针不测网络 RTT，也不能独自证明网络故障；不要把时间相关性当成因果。

不探测或绕过 Lighter 的 restricted jurisdiction。此环境公开交易所网络受限，本地可控阻塞只验证取证功能，不替代持续实网和 24 小时验收。

### R25 热路径优化后的对照边界

设置读取使用进程内、按数据库路径共享的已提交快照，返回独立副本；同进程多个 Storage 实例在写入提交后立即可见，写失败不发布，重启重新从 SQLite 加载。SQLite 写入仍持久化。此应用按单个采集进程部署；设置应通过应用 API 或同进程 Storage.set 修改，不支持跨进程直接改 SQLite 后自动失效。开发外部迁移/恢复后需重启应用，不能把旧进程缓存当作数据库实时视图。

关闭录制时 observer 不连接 SQLite、不扫描录制目录；开启时配置未变不再重复 configure/status 目录扫描。跨 observer 最多 16 个录制作业，超出在 `/api/recording`状态中记录 queue_dropped，pending_writes 包括取消调用后尚未完成的写入。排队作业执行前复核最新设置；关闭后尚未开始的作业不落盘，已经开始的单次写入允许完成，停机等待其完成。录制本身保留原有大小/保留天数/脱敏约束。

保持相同公开平台与采样频率复跑 status-only 和固定重查询两组，关注探针事件、进程 CPU 增量、coverage 计算以及录制 pending/dropped；对照时始终明确录制/通知开关。本地 settings 5000 次相同读取对照见 `evidence/settings-hotpath-synthetic.json`，只证明此环境消除反复 SQLite 读取，不能直接推断真实 HL/OKX 时效改善。

### R27 公共批量解析与原生 WS 解码对照

CCXT OKX/Bitget 普通 spot/swap 批量 ticker 的同步解析在有界工作线程执行；HTTP/session/限流器仍留在创建它们的事件循环。该窄路径只针对已审阅的 CCXT 4.5.91，其他版本/特殊路由保留上游实现，并在 `/api/query-work` 的 `public_ticker_parsers` 报告 fallback 与已安装版本。不能把 fallback 模式当成已启用解析优化。每适配器最多一个工作线程和一个等待请求；取消等待已开始的解析完成，close 不把在途解析交给已关闭客户端。诊断只含计数、容量、解析 CPU/墙钟时间和接收/完成时刻，不包含响应或账户信息。

HL 仅 >=8KiB 的 JSON 帧交给每连接一个解码线程；小帧保持快速解析和每 64 帧让出事件循环，避免每个小包交接线程导致心跳回退。最多五个 catalog 连接，传输帧限制 2MiB，原有有界消息队列、空侧清除和乱序屏障不变。原始接收时间在 recv 返回后、解码和诊断回调前保存。取消/关闭等待当前解码结束，重连可创建新线程。线程不能保证消除 GIL 竞争；小帧解码、HTTP JSON 解码、其余平台解析和市场转换仍有事件循环 CPU，需继续抓栈，不宣称全部 CPU 已移走。

`benchmark_native_decode.py` 对同样 128 个约 457KB 合成帧比较：inline 总耗时约 0.393s、最大循环延迟 199.869ms；有界线程约 0.425s、4.079ms。目标是减少连续占用，不是承诺吞吐加速。真实 HL 通常帧更小，不能将此大帧合成改善直接套用真实运行。见 `evidence/native-decode-synthetic.json`。

#### OKX 资金费时间语义核查（2026-11-04）

官方 [REST funding-rate](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate) 将 `ts` 定义为数据返回时间；`fundingTime`/`nextFundingTime` 是结算时间，不是行情更新时间。官方 [资金费 WS](https://www.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel) 标注推送间隔 30–90 秒。此说明不保证 REST 每个请求获得小于 60 秒的新 `ts`，也不能证明实测 61–72 秒全部来自上游缓存。客户端等待、响应解码和同步解析亦可能贡献时延。

继续记录原始 `info.ts`、本地接收/解析完成时间、请求耗时与相邻响应 `ts` 是否变化；现有 `funding_arrival_source_age_*` 诊断继续保留，资金费目前本地接收观测在 CCXT 返回后，因此包含其解析时间，不能直接称为网络 RTT。61/72 秒源年龄仍在 60 秒门槛之外；成功 HTTP 或更近本地接收时间不使旧源数据重新有效。新增边界回归验证，不改 TTL、不用结算时间代替 `ts`、不伪造 source 时间。实网覆盖和无空窗验收保持开放。

### R28 解析取消与 shutdown 所有权

CPU 工作开始后，超时/重复取消只标记调用方取消；每次等待都保护工作 future，等线程实际结束后再传播 CancelledError。CEX 解析锁与 pending、HL 解码锁与 future 在此期间保留。关闭由共享内部任务完成，多个 close 调用及 close 调用方被取消不会提前结束客户端/子连接清理。关闭中不允许新会话；清理完成后可以重连。

这不是强制杀线程：若解析永久不返回，容量和 shutdown 也不会伪报完成。超时清理可能超过请求原定超时。当前解析输入有既有帧/目录边界，不通过额外无限线程掩盖阻塞。测试用可控阻塞线程验证重复取消与实际结束的先后，不触碰实网或账户。

### R29 OKX 直接/批量时间取证及状态解释

2026-11-04 官方资料交叉核对：REST `ts` 是数据返回时间，结算时间是另一个字段；[资金费页面](https://www.okx.com/en-us/trade-market/funding/swap)说明按分钟计算，[WS 文档](https://www.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel)推送30–90秒。这些并非每次REST请求小于60秒源年龄的承诺。父线程实测p90=70.663秒无重试仍0/540，尚缺同一环境直接/ANY响应配对，不能认定是批量缓存、网络、客户端排队或服务器时钟中的单一原因。

在允许访问 OKX 的运行环境，可运行（仅固定公开 `/api/v5/public/time` 与 `/api/v5/public/funding-rate`）：

```bash
python scripts/probe_okx_funding_timing.py --live --samples 18 --interval 10 --symbols BTC-USDT-SWAP --output /tmp/watch-money-okx-timing.json
```

每周期配对直接合约和 `instId=ANY`，最多两并发、五个诊断合约、60周期；周期之间至少5秒。记录完整收包时间与JSON解码完成时间、单调请求耗时、原始ts/结算时间、相邻ts变化、整批源年龄p90/最大、60秒外数量、选定合约的直接/批量差值。诊断选样不是采集白名单。服务器时间给出相对本地时钟的偏移区间，不自动校时或改source。保留仅Date/Age两个公开HTTP诊断头，不保存认证、cookie、正文错误或账户字段。响应上限4MiB/2000行；请求超时/传输失败保留后继续后续轮次，非可恢复响应仍停止，具体R31规则见下文。不通过换域名绕过限制。

判读时比较：直接和批量ts是否同时停更、直接是否持续比批量新、变化是否跨相同计算周期，以及时钟区间/请求与解码耗时。两次请求非原子同刻，单次不同ts不能证明缓存缺陷；相同ts也不能证明服务器没在正常按周期发布。`http_cache_age_seconds`不存在时表示未知。需保存足够连续样本，再决定是否调整平台观测期望；本次不扩大可靠比较门槛。

`funding_state` 保持传输/轮询状态；新增 `funding_observation_state` 单独表示费率字段门槛状态，`source_stale`表示“已收到但源时间超限”，不是断连。`fields_ready/partially_ready`只描述所需费率字段，不证明资产身份、报价币或成本可比较；身份仍需独立门槛。旧费率保留可查看，但不进入可靠排序。源码期望周期与严格机会比较有效期不能混为一谈。

`evidence/okx-funding-probe-synthetic.json` 仅为MockTransport构造的不同直接/批量ts样本，证明取证格式与旧时间不续期，不代表真实OKX行为。本云环境无法直接访问公开交易所，实网配对尚未执行。

### R31 探针超时后继续采样（2026-11-04）

父线程 cabd664 独立配对：13对成功直接/ANY的原始ts一致，一轮799条ANY全部超60秒（p90=65.572秒），BTC/ETH/SOL直取约64.6秒；没有证据表明直取能修复该样本时效。不自动用直取替代批量，不改源时间和门槛。第五轮ETH/SOL ReadTimeout使旧探针早停，本轮已修复此取证限制。

原命令继续有效，可添加 `--request-timeout 20`（总请求时限，允许1..60秒；HTTP客户端仍有15秒阶段超时）。逐请求记录排队时间、开始/结束、总耗时、超时类型和发生阶段、部分收到字节数；未完整收包不生成 received_at 或数据样本。当前轮每个端点只请求一次，ReadTimeout/其他超时/传输错误或HTTP408/5xx在后续正常轮次继续，无当前轮立即重试。

总轮次仍最多60、每轮最多7请求、最多2并发；新增全局请求起始间隔至少0.25秒。失败轮次以原间隔退避，逐级增加但最多60秒；恢复全成功后归零。连续3轮所有请求均失败即停止；HTTP401/403/429、其他非恢复状态或API/结构错误仍停止，不重复访问拒绝/受限接口。不重试Lighter，不更换域名规避限制。

每轮原子更新输出文件；`finished=false` 表示尚未完成（中断后前面完整轮次仍可读），最终写 `finished=true`。`completed_requested_cycles` 区分完成轮次与提前停止，`stop_reason`说明原因，`stopped_on_error`仅表示终止性错误，不意味着此前没有失败。summary汇总每条请求流的尝试/成功/超时、错误类别、最大耗时和过龄响应，以及配对同ts/不同ts/不可用次数；失败不被悄悄丢弃或算作成功。

`evidence/okx-funding-probe-timeout-synthetic.json`是首轮ETH超时、次轮恢复且ts保持旧值的MockTransport证据，非真实OKX请求。诊断界面将“请求状态”与“统计时费率字段状态”分别展示，超时/连接错误与源时间过龄分别提示；旧观测可以与新请求失败同时存在，不能互相替代。
