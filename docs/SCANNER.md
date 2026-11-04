# 全目录指示性机会扫描

`watch_money.scanner.scan_markets(instruments, snapshots, as_of, stale_seconds, limit=100,
sort="spread", max_candidates=10000, q="", platforms=None, market_type="all")` 读取已发现的完整市场目录与当前快照，不限定 BTC/ETH，
也不依赖关注列表、手动选择两腿或已保存监控计划。`instruments` / `snapshots` 可为对象列表或以 ID 为键的字典。
时间使用 UTC epoch 毫秒。该函数只计算并返回 JSON 对象，不修改采集器、配置、存储或输入对象。

`router` 提供 `GET /api/scanner?sort=spread&limit=100&max_candidates=10000`，
从 `request.app.state.collector` 和 `request.app.state.store.settings()` 读取数据。
应用只需 include_router。入口过滤停用平台和非活跃市场；关闭 computation_enabled 或开启 demo_mode 时停止扫描。
扫描不请求新行情、不获取盘口、不下单、不发送消息；是否周期刷新由调用方决定。

资产分组复用 `identity_candidates`：只有身份门槛 comparison_ready 的组参与。
同 ticker 未核验的不同资产、不同原生链/合约地址、包装/桥接/缩放符号混用不能生成机会。
所有被阻止的组和市场数量、各类身份阻塞原因在 stats 中计数，不将其伪装成“没有价差”。
每个组枚举不同 venue 的全部有向组合，包括现货/现货、现货/永续、永续/现货、永续/永续；
pool/RFQ 作为需另外提供规模报价的现货类市场展示。相同平台的组合不在本扫描范围。

每条 candidate 通过 buy_instrument_id / sell_instrument_id 表示买入/多头与卖出/空头方向：

- 新鲜 buy ask 与 sell bid、相同原始 quote 下，`spread_absolute = sell_bid - buy_ask`，
  `spread_rate = spread_absolute / buy_ask`，`spread_pct = spread_rate * 100`。
- quote 不同则三项价差为 null，保留各腿报价币和原数值，missing 说明缺少 FX。
  USD、USDT、USDC、KRW 均不视为自动等价。
- perpetual 保留 raw_rate、实际 interval_hours、kind、next_funding_at；
  normalized_8h_rate = raw_rate * 8 / interval_hours。
  `funding_8h_rate = short_normalized - long_normalized`，两种方向都枚举。
  现货没有永续资金费，不将永续缺失值补零；真实零费率仍显示为零。
- 不同 quote 的资金费率可以展示无量纲差异，但这是相同估值名义规模的比较，
  不是跨报价币的现金收益。实际结算时间/次数、持仓单位和汇率必须由计算器另外核验。
  已结算值不能当预测；类型不一致或缺失时 warnings 明示。

买卖报价、资金费率、费率周期逐字段校验来源时间与本地接收时间，取较早者判断过期。
合并行情里新价格不会替旧资金费续期。未来时间、invalid、demo、跨市场身份错误、
交叉盘口及缺失值都不产生对应数值；缺少源时间只能依据本地接收时间。
周期元数据是例外：仅 funding_interval_hours 的来源明确以 `:catalog_metadata_observed_at` /
`:public_metadata_observed_at` 结尾、源事件时间为 null 且有显式逐字段观测时间时，
按六小时有效期核验；不会用外层新快照时间替代元数据观测时间。动态字段仍用 stale_seconds。
任何被使用但没有来源事件时间的字段在 warnings 标注 `source_timestamp_missing`，
不把本地/目录观测时间冒充交易所实时事件时间。
同市场提供多个快照时只使用 as_of 时已经收到的最新版本；未知未来信息不参与历史扫描。

所有条目永远 `net_profit=null`、`executable=false`。
requirements 明示缺少交易规模、费用配置、可成交深度，AMM/RFQ 还缺按规模的路线报价。
永续还需逐笔结算时间表；contract_size 用于合约数量换算，不能据此缩放挂牌价格。
因此正的指示性价差/资金费差不等于可成交净利润，不能替代 `/api/opportunities/calculate`。

枚举和返回有两个独立上限：

- max_candidates 限制实际评估组合数，默认 10000，最大 100000。
- limit 限制排序后返回条数，默认 100，最大 1000。

stats 的 possible_candidates 数学计数全部符合身份门槛的有向跨平台组合；
scanned / returned / unscanned / omitted_by_limit 区分已评估、已返回和两类省略。
enumeration_truncated / result_truncated / truncated 明示上限是否造成截断。
未枚举截断时 selection=global；超过预算时 selection=bounded_prefix，排序仅覆盖按身份/平台/市场 ID
确定顺序枚举的前缀，**不是声称找到了全市场最优机会**。
sort 接受 spread（价差率降序）、funding（有符号八小时费差降序）、asset；null 排在有效数值之后。
负数条目仍可返回；没有可用数值的条目保留 missing 原因，stats 单独统计不可计算条目。

资产聚合和筛选接口：

- `q` 按资产 ID、base、原生市场名称、instrument ID、native_asset_id 做不区分大小写的子串搜索。
  匹配任一成员会选中整个身份组，不会因为搜索移除同组冲突身份证据。
- `platforms=binance,okx` 选择已启用平台中的子集；空字符串为全部平台。
  available_platforms、filters 和 excluded_by_filters 明示范围。
- `market_type=all|spot|perpetual|spot_perp` 在扫描预算之前过滤市场/组合，默认全部。
  spot 是现货类间（含明确标识的 pool/RFQ），perpetual 是永续间，spot_perp 仅枚举一条现货类和一条永续的跨平台组合。
  coverage、possible_candidates、best 与枚举预算均在所选范围内重新计算，未选类型不消耗预算。
- groups 每组包含覆盖平台/市场数、可能/已扫描/可靠/排除方向数、markets、更新时间和截断情况。
  best_spread_candidate 仅从 price_rankable 方向选择，best_funding_candidate 仅从 funding_rankable 选择。
  `best_within_scan=true` 明确“已扫描范围最佳”；scan_complete=false 不能称全市场最优。
- 每组 directions 最多返回 limit 个精简方向，市场逐字段明细在 markets；
  directions_returned/directions_truncated 明示省略。groups 本身也最多 limit 项，
  stats.groups_total/groups_returned/groups_truncated 明示资产摘要截断。
- buy_market/sell_market 及 groups.markets 保留原生市场/资产 ID、bid/ask，fields 分别提供
  source/source_at/received_at/age_seconds/valid/issue/rankable。
  摘要的最新接收、最早源事件与年龄只统计有效价格字段，不把其它新字段当价格续期。
- `reverse_spread_rate = (buy_market.bid - sell_market.ask) / sell_market.ask`，
  使用真实反向 bid/ask，不以正向值取负代替。
- price_rankable 要求已确认身份、同报价币、双方新鲜有效买卖报价，且源事件时间/来源已提供。
  未知源时间可展示本地观测指示值，但不能成为可靠最佳路径。
  funding_rankable 要求原始费率源时间已知、所需归一化字段有效且类型一致。
  两种排序都把不可排行的诊断方向放在可靠方向之后。
- 资金费每腿保留 raw_rate、实际周期、next_funding_at，增加 position/payment_direction
  （pay/receive/none/unknown）；settlement_alignment 区分 synchronized/different/unknown/
  single_perpetual_leg/not_applicable。未知时间不推断同步。

前端默认紧凑资产表格，一资产一行：资产、类型、盘口毛价差、最佳路径、覆盖、更新时间、关注。
按最佳已扫描可靠价差排序，展开优先显示双向组合、买卖报价和每腿源事件/接收时间；
“组合详情”保留资金费和条件明细，“测算此组合”精确引用两腿。各平台盘口身份明细在组合表后。
支持搜索、类型/平台筛选、手动刷新、可选每 30 秒刷新及平铺切换。
切换筛选或刷新时旧结果明确标注，加载/失败期间旧组合不提供测算入口。
关注沿用现有市场级 favorites：资产行按钮关注稳定代表市场，展开区可分别关注每个市场。
可选props为 favorites:string[]、toggleFavorite(instrumentId)、onOpenAsset(assetId)。
active 默认 true；页面保持挂载但隐藏时设置 false，停止自动刷新；重新显示保留搜索、展开和上次检查时间，不额外发起扫描。
专属样式位于 ScannerPage.css，不修改全局样式。
可选 `onSelectCandidate({buy_instrument_id,sell_instrument_id,strategy})` 精确引用两腿，
“按规模计算”只转入现有计算器，不自动计算、补费用或下单。

验证：`.venv/bin/python -m pytest tests/test_scanner.py -q`；前端 `npm run typecheck`。
