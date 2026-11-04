# 全目录资金费排行榜与跨平台矩阵

`funding_view.router` 提供 `GET /api/funding`，只读取 collector 当前完整目录与快照。
仅纳入启用平台的活跃 perpetual 市场，不使用 watch_assets，不限定主流币，不触发行情/盘口/历史请求。
关闭收益计算模块也可查看行情；演示模式不混入真实排行。
纯函数为 `build_funding_view(instruments, snapshots, as_of, stale_seconds, *, sort="rate",
limit=500, rate_basis="normalized8h")`，输入可为列表/迭代器或以 ID 为键的字典，不修改输入。

参数：sort 支持 rate / spread / asset；rate_basis 支持 raw / normalized8h，
前者按各市场实际周期原始费率排序，后者按八小时归一化排序。
limit 为 1–5000，分别限制 rows 和 groups 的返回数量。排序覆盖完整输入目录，
stats 明确总数、返回数和两种截断情况，不把截断后的结果称作全部市场。

rows 逐市场保留 instrument/venue/base/quote/settle、原始身份、identity_status、
raw_rate / kind / interval_hours / normalized_8h_rate、next_funding_at 与 countdown_seconds。
未确认同 ticker 的市场依旧列出，维持各自身份，不能放入相同可比矩阵。
不同原生链/地址或缩放/包装关系冲突的市场也列出并标明冲突，不能靠人工同 ID 抹掉证据。

field_validity 对资金费、类型、周期和下次结算时间分别返回 valid/reason/source/source_at/received_at/
age_seconds/ttl_seconds/metadata_observation。动态字段使用 stale_seconds；
周期元数据只有明确 observed_at 来源后缀、源时间 null、显式逐字段观测时间时才按六小时有效期核验，
与 scanner/strategy 一致。未知源时间保留 null，warnings 标注 source_timestamp_missing。
缺费率/周期、过期、未来信息、演示或无效快照不产生有效归一化数值。

groups 只包含 identity_candidates 已确认且跨 venue 的同资产组，cells 保留每个市场的实际周期和类型。
同一有效 funding kind 才计算最佳跨平台 `spread_8h_rate = short8h - long8h`；
不同类型或未知类型时留空并说明原因。最佳多空不能来自同一 venue。
结算时间不同、报价币不同、源时间缺失都单独警示；不同 quote 的无量纲费率差只用于同估值名义规模比较。
所有差值都明确为归一化费率差，不是实际结算现金流或净收益，net_profit 永远 null。

该视图 observation_type=local_public_snapshots、is_settled_history=false。
current/predicted 分别标注估算/预测；即便某平台当前返回 settled，也只是本地观察到的已结算值快照，
不能冒充平台完整历史序列或未来费率。

前端 `FundingPage` 无 props，局部声明类型，提供手动刷新、原始/8h 排序、返回条数、
排行榜/已确认同资产矩阵切换、逐市场周期和结算倒计时、源事件与本地接收时间、未知状态与来源明细。
主应用负责将组件接入导航；不自行触发后台任务。

“获取已结算历史”只在用户点击具体市场后调用
`GET /api/funding/history/{instrument_id:path}?refresh=true&limit=100`。
这是独立 public settled history service 提供的真实历史接口，默认排行榜不批量拉历史。
历史表展示 settlement_at / funding_rate / funding_interval_hours / source / received_at；
未知历史周期不使用当前目录周期回填。失败、未覆盖、无返回与截断都有单独提示。

验证：`.venv/bin/python -m pytest tests/test_funding_view.py -q`；前端 `npm run typecheck`。

## R38 Bitget/Gate公开结算历史

现有历史接口的显式refresh新增Bitget Classic和Gate，仍限一页最多100条、单刷新锁/适配器并发及15秒超时。Bitget强制uta=false、paginate=false、pageNo=1，原始fundingTime毫秒必须与统一timestamp一致，symbol必须等于目录nativeID；since只过滤最新页，不作为旧页回填游标。Gate强制paginate=false与精确settle（BTC/USDT/USD1），原始t秒×1000必须吻合统一timestamp，r保留原字符串。目录swap/nativeID/settle不明时拒绝刷新。

费率单位为每次结算的小数比例，不乘合约张数/乘数、不额外除100；正值多付空收。历史原始周期未提供则null，不推定8小时，也不拿当下目录周期回填历史。记录额外保留native_market_id、settle、rate_unit、native_settlement_time/native_time_unit。现有去重、修订、首次收到时间和本地采样隔离保持。缓存及新接口都不是全历史完整性证明；Gate当前费率独立源时间仍未知。

官方来源见COVERAGE R38。本轮使用合成响应和安装版CCXT公开方法替身测试，没有行情主机实测，没有账户凭证/交易/生产映射变更。
