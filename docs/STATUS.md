# 当前开发状态

2026-11-04 R24 平台切换与视图时序修复：HTTP 与 WS 使用同一服务实例、构建序号和筛选上下文；前端拒绝旧请求、旧订阅和旧显示定时器，WS 增量只应用到该连接自己的基线。修改平台范围立即清空旧范围并重新订阅；基线不匹配重连取得完整视图。价差/资金费页面离开即取消请求，返回重新核验，保留搜索、筛选、排序、展开与滚动；失败明确显示缓存不可用于测算，重试建立新请求代际。服务端断开旧连接后停止继续构建视图，已运行线程保留容量直到实际结束。全部沿用原始行情时间，不延长有效期。**851 项后端测试（31.02 秒）、44 项前端测试、47 组本地浏览器检查、生产构建和 Ruff/diff 检查通过**；R24 新增 5 组确定性乱序检查已加入 CI。真实 HL 断流、OKX 资金费空窗、24 小时运行与身份覆盖仍待独立实网验收，不能以合成测试结论替代。见 [R24 浏览器证据](evidence/browser-market-view-order.json)。

2026-11-04 总览可信状态与重查询负载：总览绿点/有效行情计数按显示价格字段与原始时间判断，新增price_valid/price_fresh等派生状态；结构valid及无关资金费字段保留。重HTTP和WS视图共用单worker，最多2个HTTP排队/1秒，超载503而不堆积或续期数据；/api/query-work提供安全CPU/阶段计时、队列、最慢任务与loop采样。**完整833后端（34.65s）、34前端、29组浏览器、构建通过；最后最慢任务统计补充后6项定向/Ruff/diff通过**。本地10000市场样本markets0.481秒、并行1.215秒，不能替代父线程27.16秒/应用pong超时的实网根因验证。该轮发现的R24已在后续修复（见上文），24小时、身份覆盖与地区限制仍开放。见 [审计清单](AUDIT_BACKLOG.md)、[COLLECTION.md](COLLECTION.md)、[浏览器证据](evidence/browser-overview-freshness.json)。

2026-11-04 压力合并清除屏障修复：同事件时间按买卖两侧保留清除优先，另一侧仍可更新；内部状态携带每侧原始receipt，较新事件允许恢复，缓冲容量不变。新增12项BBO/l2Book压力回归，含300帧重复压缩与逐条collector处理的值/来源/时间对照；独立复审12项通过。**完整818后端测试（28.29s）、Ruff/diff通过**。本批未改前端，未重跑浏览器，未新增实网探测；父线程已独立实测确认Bitget周期回归修复。其他实网断流、24小时与身份覆盖验收仍开放。见 [审计清单](AUDIT_BACKLOG.md) R21 与 [DEX_STREAMING.md](DEX_STREAMING.md)。

2026-11-04 响应/流背压改进：三个大HTTP查询在工作线程编码，浏览器WS视图/delta/编码同样移出事件循环；HL独立reader在订阅前运行，有界压力合并保留原始receipt/明确空侧，应用pong不等下游消费；按阶段展示安全超时类，诊断回调丢弃/未确认完成计数可见。**完整806后端（28.20s）、31前端、构建通过；含最终关闭计数及未来帧门槛修订，Ruff/diff通过；本批24组浏览器检查通过**。合成10000市场ASGI markets从1.387到0.451秒，loop最大迟到1179到109ms；这不是实网断流验收。父线程轻查询仍有HL超时，OKX空窗因果、24小时、身份覆盖与Lighter地区限制继续开放。详见 [COLLECTION.md](COLLECTION.md)、[DEX_STREAMING.md](DEX_STREAMING.md)。

2026-11-04 Bitget周期合并回归：缺失字段不再使用其他字段的快照时间作排序屏障，保留周期观测时间和来源，已存在值/显式清除继续拒绝旧更新。**完整782后端测试通过（27.89s）**，含8项新增字段时钟及适配器到可比较性回归；本批未改前端，未重跑浏览器。查询负载的本地合成测量见 [api-blocking-diagnostic.json](evidence/api-blocking-diagnostic.json)：10000市场的真实ASGI /markets约1.387秒、事件循环最大延迟1.179秒；与/scanner并行约2.104秒、最大延迟1.307秒。存在可量化编码阻塞，但不等于已复现实网10.6秒或证明断流根因。OKX空窗仍未定因，60秒阈值不变；Lighter限制不绕过。

2026-11-04 指标合并与集成流复审：协议原始指标按字段保留独立来源/时间，支持明确清除和持久化删除屏障；未来帧不推进源流或缓存排序水位；目录签名检查降为每秒一次并增加处理/错误诊断。**完整774后端（27.78s）、31前端、37组本地浏览器、构建/Ruff/diff通过**。隔离CPU和真实merge/SQLite模拟断线的测量不等于实网断流根因已确定。OKX短窗原因未知，Gate/Bitget 199组/440现货仅预览未应用，父线程Lighter地区限制code20558本轮未重复探测。见 [METRIC_MERGE.md](METRIC_MERGE.md) 与 [metric-merge-validation.json](evidence/metric-merge-validation.json)。以下保留前批记录。

2026-11-04 完整盘口空侧修复：Hyperliquid完整快照为空的一侧显式清除，双空也发patch；删除时刻随持久化保留，旧/同时间报价不能复活，新快照可恢复。Lighter market_stats保留缺省字段，仅明确无报价时清除。8项新增回归及**完整750后端测试（21.91s）、Ruff/diff通过**；本批未改前端，未重跑本地浏览器。详见 [DEX_STREAMING.md](DEX_STREAMING.md) 和 [审计清单](AUDIT_BACKLOG.md)。全部合成验证，实网限制不变。

2026-11-04 外部实测反馈后续：Hyperliquid/Lighter启用受预算限制的全目录源流；OKX资金费按到达源年龄自适应重查，保持60秒TTL；官方身份源扩为Gate/Bitget/KuCoin/Coinbase Exchange并严格限定原始币种ID、client及现货范围。永续身份和Lighter费率单位仍未充分确认，保持未知。**本批完整742项后端（24.02s）、31项前端、45组浏览器检查、构建/Ruff/diff通过**。证据见 [live-review-validation.json](evidence/live-review-validation.json)，实网父线程反馈与当前代理失败分别见 [LIVE_REVIEW_FOLLOWUP.md](LIVE_REVIEW_FOLLOWUP.md)。以下保留前批记录。

2026-11-04 回放复审：价差与资金费身份核验已传递请求as_of，批准前/时/后三个边界回归通过；200小币种双平台合成批量身份验证通过。完整后端681 passed（19.30s）、Ruff及diff检查通过。本批未改前端，未重跑浏览器；真实身份覆盖及完整历史映射版本仍未验收。详见 [审计清单](AUDIT_BACKLOG.md)。

2026-11-04 界面真实性复审：顶部刷新联动当前价差/资金费查询；缓存排行随时间保守失效、更新年龄并在返回时重查；我的监控可查看和取消星标市场。资金费兼容类型分组已在上一批完成。完整677后端、31前端、构建/Ruff通过，导航12组浏览器通过。具体证据见 [browser-navigation.json](evidence/browser-navigation.json) 与 [审计清单](AUDIT_BACKLOG.md)。本批仍为本地合成验收。

2026-11-04 数据独立复审更新：四项修复已完成本地整合验证，**677项后端、29项前端、48组浏览器验收、生产构建及Ruff通过**。全目录采用产品批量与逐币回退分层调度，读取时重算新鲜双边盘口/有效费率覆盖，不延长60秒过期门槛；状态显示实测轮转与限流预估。新增可溯源资产证据导入、预览、原子应用、冲突降级和撤销审计；首批公共源为Gate/Bitget，未证实身份仍隔离。修复OKX原始资金费时间，统一可信门槛并按兼容类型比较；DEX报价币缺链/合约证据或可信换算时，扫描和手动测算均阻止可靠收益结果。见 [本批证据](evidence/data-review-validation.json)、[调度与限制](COLLECTION.md)、[资产证据](ASSET_VERIFICATION.md)。

当前环境对OKX、Bitget、Gate公开端点实测均为ProxyError，见 [真实失败记录](evidence/data-review-live-probes.json)。上述测试不是实网吞吐或全资产身份验收；逐币来源仍可能无法在60秒内全量刷新，24小时验收未完成。以下保留前批记录。

2026-11-04 前端信息组织更新：主导航收敛为市场监控（默认价差、资金费、全部市场）、我的监控、通知记录、设置。资产表展开组合可进入测算；资金费默认矩阵，保留单平台排行、原始周期、来源和历史。返回保留本次会话筛选/展开/滚动，身份与诊断放入设置。**本批后端575项、前端25项、40组浏览器检查、构建与静态检查通过**，见 [本批汇总](evidence/interface-validation.json)；浏览器证据及界面说明见 [INTERFACE.md](INTERFACE.md)、[browser-navigation.json](evidence/browser-navigation.json)。原计划未验收边界保持不变。

以下为此前功能批次记录：

2026-11-04。**完整三阶段计划尚未通过验收。** 本轮价差聚合/规模计算、资金费、费用配置及对应修复完成离线验证：**Python 563 passed、Node 18 passed；最终浏览器6/12/7组通过；TS/Vite build、CI Ruff E4/E7/E9/F和diffcheck通过**。证据见 [followup-validation.json](evidence/followup-validation.json)。这些是合成fixture、mock HTTP/RPC、loopback及隔离浏览器证据；不是实网全覆盖或24小时运行。

用户最新优先级：先数据正确性，再**按资产聚合 → best价差排序 → 展开双向组合 → 指定规模calculator**；第二资金费聚合，第三其他简洁指标/已有历史。不新增交易账户。本轮已有历史/原生能力保留范围，后续不抢先扩大原生协议开发。

逐项状态、代码/测试/证据和限制统一见 [AUDIT_BACKLOG.md](AUDIT_BACKLOG.md)。**每批修改后更新对应审计ID与证据，再同步本状态和验证报告；收到问题或存在代码不能直接标done。** 父线程报告的市场数量不是本环境真实采集样本。

## 已实现并离线验证

- 新数据目录默认启用全部28个计划平台，已有持久化启用列表保留。CEX目录不按主流币白名单裁剪，部分服务失败保留身份/单独报告。广覆盖按已发现活跃目录分层调度，已审查端点按整产品批量，其余有界逐币回退，新配置目标周期30秒、1800秒重发现；关注资产另有详细REST/可用源WS，不能称全目录同时实时更新。
- CEX公共源WS及Hyperliquid/Lighter/dYdX原生WS接入collector，包含稀疏字段、心跳/重连/选择变化取消与REST fallback。本地浏览器推送为初始完整markets、后续markets_delta增量/删除，保留过期转变、重连/模式重置；大目录实网负载未验。
- 资产级扫描按可核对身份、报价币、来源时间和有效bid/ask筛选best，展开原始市场与双向组合，保留两腿ID/源与接收时间。预算截断明示best仅限已扫范围。点击精确ID/策略进入calculator，不自动提交计算；指示扫描net=null/executable=false。
- Calculator支持基础数量或实际多档买方asks的金额换算，刷新指定两腿深度；不外推或静默舍余。spot base/perpetual contracts的step与min_amount、深度、费用/FX/时效/身份继续校验；未知或不足时不输出假净收益。退出假设有unknown/entry/prices/basis互斥输入；显式双腿holding逐腿名义/时长、一次换汇，独立于funding情景。
- 公共费用API/设置/计算器按venue、spot/perpetual、maker/taker隔离。112档案中22条普通公开参考，保留来源/核验时间/适用scope；Binance perpetual等未知保持空，Bybit特殊区、HIP-3/HL spot等不越scope自动套值。用户覆盖明确为假设、可审计/恢复；切腿/同平台换产品清除相应手填fee与holding、旧异步结果不串腿。不是账户实际费率，不使用账户API。
- CEX资金费独立任务使用产品批量/逐币分层，新配置目标30秒，已有持久化设置保持不变；DEX复用广覆盖REST已有payload并显示via_light_rest。全活跃perpetual排行、同资产矩阵保留raw/真实周期/8h、类型、下次结算/倒计时、逐字段时效；归一化费差不是现金收益。已结算历史是另外显式手动单页服务，保留原始值/来源/修订，不作后台全量回填。历史图表不借新价格事件刷新旧funding，类型/时期/演示/来源分别保留。
- native_asset_id持久化，人工映射不能抹掉原生链地址/单位冲突；全目录候选和批量人工映射已有。REST/轻量/源WS共用volume单位核验，修正OKX合约量误作base；旧未核验CCXT永续volume缓存/历史读取抑制。
- 公共payload诊断录制默认关闭，按venue、额度/保留期配置，脱敏和磁盘失败隔离；是解析JSON/CCXT对象，不是完整wire/frame或全协议回放。
- 通知只做本地/HTTP模拟。入队固定delivery_expires_at，发送/重试claim前再验；停机后过期记录expired且不发，未送达旧通知可释放自身dedup，已sent/新状态不误清；规则/队列/冷却/失败恢复已有回归。
- 六现货DEX搜索改为协议/链关键词和已知池，仍search_limited。Uniswap V3 Ethereum/Arbitrum/Base有显式地址状态；Pancake V2 BSC、Aerodrome Base v1有状态和单池exact-input估算，保留decimal/fee/方向/block hash/time及factory成员检查，始终executable=false，未作编译字节码/交易模拟认证。

## 未通过的实网与能力边界

1. 全币目录、真实两腿盘口与规模、身份/费用/周期的真实样本核对，以及实际限流/完整轮转延迟和24小时稳定性尚未完成。用户已授权官方域名，但本环境数据主机仍受策略限制，未绕过代理/CA，见NETWORK.md；短时空数据soak不能替代实网验收。
2. 官方chain/address全币证据、包装/桥接关系和独立价格单位比例仍不完整，同ticker不能自动合并。公开费用参考不是账户实际费率；未知币种/产品/范围保持未知，不为凑净收益补零。
3. GMX/Jupiter等完整side/collateral/size执行报价及所有协议funding/borrowing/持仓费模型未完成；RFQ缓存不是可执行报价。Drift仅原program metadata，当前链上生命周期和行情未经核验。
4. 原生协议缺口暂不扩大：Curve/Raydium/Orca native、V3规模quoter/tick traversal、多跳、完整池索引、主网RPC/编译字节码认证未完成；其他DEX源WS/历史恢复也未全覆盖。已有单池估算不能冒充这些能力。
5. Windows脚本尚未实机验证。飞书/Telegram真实发送按用户要求延期，不阻塞本轮模拟交付；没有交易账户、交易执行或真实外部通知。

## 下一批顺序

先按AUDIT_BACKLOG维护每批记录。网络可达后优先验证全币真实目录/盘口/身份与价差规模闭环，录制真正样本、人工复算不同规模和双向结果，检查失效/限流/恢复，再运行`scripts/soak.py --hours 24`并修复问题。其次核验资金费真实周期、类型、结算和同资产矩阵；其他简洁指标/已有历史随后。原生路线及协议成本扩展排在这些验证之后。

本轮业务与文档已冻结；验收提交使用 `feat/market-monitor` 和 [草稿 PR #1](https://github.com/song1569498456-sketch/watch_money/pull/1)，不合并或发布。进程可能随云环境回收停止，未承诺会话结束后的后台持续执行。详细范围与证据见 [COVERAGE.md](COVERAGE.md)、[VALIDATION.md](VALIDATION.md)、[ACCEPTANCE.md](ACCEPTANCE.md)。
