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

## DEX范围

仅已确认整个public payload的路径采用全产品计划：

- Hyperliquid：每namespace一个metaAndAssetCtxs请求，不把不同HIP-3目录合并；[官方共享预算](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)为1200权重/分钟，该info请求权重20。namespace间至少2秒，将此层控制在最多约600权重/分钟，保留余量给发现和详细采集。不是整个进程所有请求的统一限流保证；namespace太多时仍可能超过60秒。
- Lighter：每轮两个全市场公开GET（orderBookDetails、funding-rates）。
- dYdX：全市场perpetualMarkets一次GET。
- GMX：已有Arbitrum V2 ticker整组GET；不据此声称有可执行规模报价或完整资金费。

其它搜索/逐pool/RFQ或“1+N资金费”路径保留原有有界调度，mode=`legacy_bounded_public_fetch`，不擅自加速。
generic批次默认继续100，避免把这些路径从100改20后重复全payload更多次；未核验能力不算原生bulk。
DEX资金费若来自同一公开payload，状态mode为 `via_public_price_bulk`，但fresh计数仍逐资金费字段过共享可信门槛，不继承价格时效。

## 配置及存量设置

| 设置 | 新默认 | 含义 |
| --- | --- | --- |
| stale_seconds | 60（未改变） | 原始时效门槛；周期元数据另按明确来源的6小时TTL |
| broad_poll_seconds | 30 | 完整原生价格sweep的目标开始间隔 |
| funding_poll_seconds | 30 | 原生资金费sweep目标间隔；旧已保存60秒配置继续保留 |
| broad_batch_size | 100 | generic/未审核路径批次上限，未改为20 |
| broad_symbol_batch_size | 20 | CEX symbol回退上限，实际取与broad_batch_size的较小值 |
| broad_inter_batch_seconds | 0.25 | sweep内让出间隔；平台最小间隔仍优先 |
| broad_request_timeout_seconds | 25 | 单计划单元总超时，超时会取消未完成任务 |

存量设置由Pydantic补新增字段，不静默覆盖用户已保存的周期或过期门槛。
如果用户仍配置资金费60秒、过期60秒，网络及处理延迟足以造成边界过期；应在配置/覆盖状态里明确看见，而不是暗中延长TTL。

## 状态字段

`light_` 与 `funding_` 各自提供：

- `subscriptions`：当前活跃目录分母；`bulk_markets`、`fallback_markets`、`bulk_groups`说明计划分层。group不是HTTP次数承诺，部分CCXT方法内部仍可能多次请求。
- `observed_markets`：存在非demo缓存字段的市场数，不等同可靠/可用行情。
- `fresh_markets`、`coverage_ratio`：**读取状态时重新评估**。价格要求bid与ask都通过共享可信时效门槛；新last或资金费envelope不能续期旧买卖价。资金费要求rate/kind/实际周期均rankable。
- `last_attempt_at`、`last_received_at`：最近尝试与接收；不用于替代字段源事件时间。
- `last_cycle_at`：所有实际存在的层都完成本计划至少一轮后，取较旧完成时刻；不是“全目录100%新鲜”的声明。
- `cycle_seconds`：两次完整sweep完成之间的实测间隔，包含目标等待、请求/保存开销；首轮或目录签名变化后暂为空。
- `estimated_cycle_seconds`：目标周期、已知请求权重下界、已完成批次/整轮实测外推的较大值；不是保证。回退过慢、缺数据或受限时必须结合fresh比例与errors。

`Collector.refresh_status_coverage()`先对目录和缓存取稳定快照，再按venue复用；状态接口每次调用，健康任务也定期调用。停机、禁用、长退避期间fresh会随真实字段年龄下降，observed与历史轮转信息可保留。

## 验证与剩余限制

`tests/test_collection_schedule.py` 使用合成公开对象验证千市场价格/资金费各一次bulk方法、全目录覆盖、低速回退不占批量槽、已知100/220秒下界、读时过期、demo排除、last不能代替双边价、目录变化重置完成状态，以及DEX审核范围。
这些是模拟调度结果，不是实网吞吐；官方文档和已安装解析器检查也不替代真实样本。
本环境公共API受代理限制，根线程另保留 `docs/evidence/data-review-live-probes.json`；本子任务没有绕过网络限制或把父报告描述成本地实测。
待在允许的环境逐平台测量真实耗时、缺失symbol、限流与持续覆盖。无法批量的来源、过多namespace、缺可信源时间或服务失败时，本实现明确允许fresh覆盖低于100%，不宣称所有fallback都能在60秒内完成。
