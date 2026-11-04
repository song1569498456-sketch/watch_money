# Gate 现货盘口时间与覆盖

2026-11-04 独立环境反馈（父线程提供，非本云环境重跑）：真实 Gate/Bitget 官方身份资料在隔离库使覆盖从 3 组/47 市场到 202/487，撤销后恢复 3/47；原库哈希未变。199 组/440 市场是这次临时批准增量，不是生产批准。固定真实盘口方向数 368→866→368，但可靠价格方向始终为 0：Gate 3257 条 REST 价格 patch 的 source_at 都为空。身份通过不等于价格时间通过。

## 官方时间语义

- [Gate 现货 REST ticker](https://www.gate.com/docs/developers/apiv4/en/spot/#retrieve-ticker-information) 的公开 schema 没有盘口事件时间。`etf_pre_timestamp` 是 ETF 前值相关时间，不可作普通买卖报价时间；HTTP Date、`/spot/time` 和本地收包也不是报价时间。单币 ticker 额外提供买卖数量，但没有因此获得盘口时钟。
- [Gate 现货 WS book_ticker](https://www.gate.com/docs/developers/apiv4/ws/en/#best-bid-or-ask-price) 的 `result.t` 明确为盘口更新时间（毫秒），`u` 为更新 ID，`s` 为原生交易对；b/B、a/A 分别为买卖价/量。消息外层 time/time_ms 不替代 result.t。该频道无需认证，支持交易对数组和多次累加订阅。
- [Gate REST order_book](https://www.gate.com/docs/developers/apiv4/en/spot/#retrieve-order-book) 区分 `current`（响应生成时间）与 `update`（盘口最后改变时间）。本地 CCXT 4.5.91 的 orderbook 统一 timestamp 使用 current；这是按需深度响应语义，不能拿它给另一次批量 ticker 补时间。本批未把逐市场 orderbook 轮询宣称为全目录实时方案。

## 实现边界

`gate_streaming.py` 使用固定公开 `wss://api.gateio.ws/ws/v4/`，仅订阅 `spot.book_ticker`。由 REST 已发现的准确原生市场 ID 建立映射；全目录模式下不依赖 BTC/ETH/SOL 白名单。最多 5000 个活跃现货市场、一条连接，50 对一批、批间隔 0.25 秒；这些是本应用资源预算，不是已证实的交易所容量承诺。采集状态保留请求数、实际选择数与截断标记；永续/不支持产品不进入此现货源，REST 继续工作但缺时间仍不可排序。

每市场有界合并保留原始报价时间和收包时间，空侧明确清除，同时间清除屏障不被后续正价抹掉；缺时间、错误市场、未来时间和乱序帧不能产生可靠新报价。完整盘口流与量/资金费等 REST 指标分开。无时间 Gate REST 买卖价不能覆盖已有带时钟 WS 买卖价或清除屏障；原始字段年龄保持，过期后不会靠 REST 收包重新变新鲜。尚未取得带时钟盘口时，REST 值仍可显示为时间未知。

独立 reader 处理 pong，订阅与应用心跳不等待下游；录制观察队列有界，不阻塞盘口接收。连接失败由 collector 原有退避/重连管理，恢复后必须收到新有效盘口；无账户 API、密钥或交易操作。

## 验证与开放项

本批测试是官方结构合成帧与假 WebSocket：无时钟 REST→有效 WS→晚到 REST→自然过期/恢复；身份未批准仍不排行；同时间清除、空侧、未来/乱序、订阅预算、取消与队列压力。真实网络在本云环境仍受限制，未执行 Gate WS 实测或改变环境设置。

在可达的独立环境用原有采集程序启用 Gate 与 `prefer_websocket`、`broad_collection_enabled`，只在隔离库加载/核验身份资料；记录 requested/selected/truncated、实际新鲜 BBO 数、字段来源与原始 result.t/收包差、可靠方向数、重连和循环延迟。要验证订阅拒绝/限流、冷门市场不更新时自然过期、REST并发不覆盖时间，以及持续运行；收到成功订阅或连接在线不代表全市场有效覆盖。全量吞吐、真正可靠方向增加及 24 小时稳定性尚待实测。

## 后续实网证据与按需补齐（R34）

父线程独立实测 c53df7d：一连接接受44批、2186个现货订阅，180秒122560帧，无拒绝或重连；47组原始/patch时间盘口一致，15376次REST覆盖保护未失败。固定真实报价在隔离库临时核验身份后可靠方向32→378→撤销32，均只是提示性方向。最后有时间盘口1671、新鲜1463，仍有未更新/过期市场；Gate资金费源时间仍缺。这是外部真实短时证据，不是本云环境重跑，也不是生产批准或24小时验收。

详情原有“获取盘口”现在对Gate现货执行有界公开快照：两请求并发上限、全局起始间隔0.25秒、同市场30秒冷却，准确发现的currency_pair、limit20、with_id；没有全目录高频REST补扫。只请求官方公开order_book，既不读账户也不加载密钥。收到响应后绑定目录仍一致才合并买卖价，空侧显式清除。冷却返回429，界面显示失败；失败和取消也保留冷却，避免反复请求。

这里使用明确不同的源时间语义：原生 `current` 是交易所此次快照生成时点，作为 `exchange_snapshot_generation`；原生 `update` 另列为盘口最后变动。静止盘口仍可由一次新的交易所快照确认，而不是靠本地收包续期。若缺current，仅保留update并标记`book_last_change`，仍按原时效门槛；两者都缺则返回时间未知的深度但不更新监控报价。未来或相互矛盾的原生时钟拒绝。字段source保留语义，详情同时显示两种时点；源时间老旧不会被请求成功变新鲜。晚到无时间ticker继续不能覆盖该快照，较旧WS报价也不能覆盖更新的快照。

`/api/query-work.source_streams.gate` 区分selected、sent、按请求id对应确认的acknowledged、unique_observed/accepted；匿名、重复或前连接确认不增加确认数。每连接generation及state/start/close明确，历史计数不是当前有效覆盖。accepted包含显式清除/结构无效状态，不能当作可靠方向；累计源时间min/max也不是当前年龄。拒绝原因、重复帧、缓冲合并与观察丢弃有固定大小计数，没有每帧全目录扫描。

`/api/status` 的light_book_missing/source_missing/source_stale/source_future计数及light_observation_checked_at补充当前字段诊断，light_fresh_markets仍要求双边盘口通过时间和结构门槛；这些诊断可能重叠，不能求和当作互斥覆盖。身份通过、规模与费用仍需另行判断。按需请求预算见`/api/query-work.gate_on_demand_snapshots`。

## 永续与资金费调查边界

[Gate永续WS](https://www.gate.com/docs/developers/futures/ws/en/#order-book-api) 的futures.book_ticker有独立result.t（毫秒），但与现货并非同一格式：u可能为字符串，无买卖一侧可用空价格加0数量，连接还必须绑定btc/usdt/usd1结算空间。`gate_futures.py`只提供通过合成测试的纯解析器，未接入真实订阅；不能把本轮现货覆盖称为永续覆盖。合约数量没有直接当作基础币数量。

[Gate永续REST](https://www.gate.com/docs/developers/apiv4/en/futures/)的funding_next_apply是结算时点，config_change_time是配置变更；futures.tickers的资金费没有独立更新时间，外层time_ms仅响应时间。它不像order_book.current具有明确整份快照生成语义，因此本批不以外层时间为费率续期。历史funding_rate接口t/r属于已结算记录，必须与当前/预估分开。新增回归确认这些字段不补成当前资金费源时间；尚无实网独立源时钟证据，资金费可靠比较继续受阻。

本批按需快照、诊断与永续解析均为本地合成/浏览器验证；Gate主机仍不在当前云环境允许列表，没有绕过网络限制或重复请求地区受限Lighter。新快照路径和诊断仍需外部实网核对原始current/update、冷门市场覆盖、限流与稳定性。

## 永续实时集成与独立复测（R35）

Gate现在由`GateStreamAdapter`统一管理四个公开命名空间：spot及futures:btc/usdt/usd1。每个命名空间最多一连接、5000个市场，总选择预算8000；每连接50个原生ID一批、间隔0.25秒。这是应用预算，不是官方吞吐承诺。只使用发现目录中活跃、client=gate且swap/settle一致的永续；未知结算币排除，重复原生ID导致歧义时拒绝连接，截断在requested/selected/truncated中明确显示。支持USD1协议不代表当前CCXT发现已覆盖所有USD1合约，以实际目录为准。

共享经过现货验证的接收、心跳、有界合并、清除及取消逻辑，但永续使用独立端点/频道/心跳、字符串更新ID解析及空价格规则。公开连接携带`X-Gate-Size-Decimal: 1`，避免小于1张的数量被取整成空盘口。只发出BBO价格，不把张数转换成基础币量，不改资金费。记录标签包含结算币，防止同原生合约名的跨连接数据污染。来源为gate:futures.book_ticker:<settle>:<nativeID>，只使用result.t；消息外层时间不补缺失。

失败连接独立按5秒起步、最高300秒退避，不关闭健康兄弟连接。401/403/451表示访问限制，本次目录会话停止该连接的重试；不更换域名或绕过地区限制。连接重建重新订阅、重置本连接更新ID，同时collector保留原始事件时间和清除屏障，旧/同时间正价不能复活清除侧。总状态partial表示只有部分连接收到报价；online只说明各连接收到过帧，不保证当前全市场新鲜。子连接的reconnects与状态在query-work.source_streams.gate.streams中，不要仅看旧的全场stream_reconnects计数。

新脚本完全不访问用户数据库、配置或映射。在允许Gate公开域名且无地区限制的独立环境，从仓库根目录运行：

```sh
.venv/bin/python scripts/probe_gate_streams.py --live --seconds 180 \
  --scopes spot,usdt,btc,usd1 --max-markets 8000 \
  --report /tmp/gate-mixed-public-probe.json
```

仅复测永续可用`--scopes usdt,btc,usd1`。脚本限制5–600秒、至多8000市场、16条初始公开patch样本；目录发现最多90秒。报告保留选择前后数与截断、各命名空间selected/observed/timed_bbo/fresh_bbo、运行结束前和关闭后的诊断、错误类型和关闭结果。timed_bbo可以已经过期，fresh_bbo使用60秒原时效门槛但不证明资产身份或可实现收益。关闭前采样诊断，避免把探针主动关闭误写成运行中连接失败。没有--live不会创建请求客户端。

复测应逐项核对：

1. 原始频道、连接settle、s和目录nativeID一致；result.t与patch字段时间完全一致，receipt独立；比对现货和同名永续不串值。
2. 检查普通线性、反向及不同contract_size的价格均保持原生价格；小数B/A不被判成空。真实空侧才清除，不用畸形缺字段冒充空侧。
3. 在隔离程序中中断一个连接，观察该命名空间退避/重连，其他连接继续；恢复后旧时间/低序列不能覆盖新盘口，长期静止盘口自然过期。不要通过代理/地区切换触发或绕过限制。
4. 对照requested/selected、按id确认数、实际接收和当前fresh数；未返回的市场明确缺失。资金费仍需独立源时间，fresh BBO不能让缺时间费率变得可比较。
5. 本脚本是短时诊断；通过后再进行组合应用负载和24小时验收，保留失败/缺覆盖数据，不把临时身份演练当生产批准。

本批证据`gate-mixed-stream-synthetic.json`是四连接假WebSocket合成验证，非实网结果。当前云环境仍禁止Gate主机访问，未运行上面的--live命令，既有Gate现货实测不能替代新永续实测。

## 成功订阅确认与后续实测（R36）

父线程对b7b063b独立实网反馈：3次成功按需快照保留原生current/update，并使过龄或无时间的真实报价得到新的交易所快照时点；3次立即重复被本地429拦截、没有额外交易所请求；另一次BTC快照超时。这是成功与失败同时保留的外部短时证据，不是全市场或24小时验收。

同轮44条Gate现货成功确认带匹配id，但省略可选error，旧现货代码错误要求error:null，显示0确认/44未关联。现货与永续现在统一接受error缺省或null，仍必须为本连接已发送的精确整数id、完整success结果，非null错误、未知/匿名id及异常结果均不算成功；重复确认不增加市场数。测试重建父线程报告的44条返回形状，ID与批大小为合成夹具，未获得原始捕获文件，不假称保存了原始实网包。确认数仍不是报价覆盖。

慢观察者计数测试原先用sleep等待，然后读取丢弃数并关闭；两步之间reader可处理新的心跳pong，令精确差值多1。这是可解释的异步时序脆弱性，不应删除丢弃断言。测试现用socket接收屏障，确认此前帧/pong已处理，后续帧停止在接收边界，再要求关闭恰好增加64排队+1在途丢弃；有/无额外pong均覆盖，重复close仍不得重复计数。生产丢弃计数语义未改变。

## 混合流独立短时结果与连续采样（R37）

父线程对6da269a运行180秒无拒绝/重连，最后新鲜现货1579/2186、USDT永续911/1070、BTC永续1/1；USD1无发现合约，未测。65条原始报文/patch命名空间、价格和源时间核对一致，包含分数合约数量。见[独立报告摘要](evidence/gate-mixed-parent-live-summary.json)；不附未提供的原始抓包，也不把这次短时实验写成24小时验收。5d33823现货确认计数修复尚待独立复验。

沿用前述公开probe命令，新报告增加coverage_samples：启动、默认每15秒、结束时各取当前字段时效，即使没有新消息也继续采样。coverage_by_scope保持结束时统计；每个范围的fresh_bbo、unobserved、missing_bbo、untrusted_time、stale_bbo、invalid_bbo相加等于selected。先区分缺盘口与不可信源时间，再区分过期/其他无效，复合问题不重复累计；observed/timed_bbo是辅助量，不是额外互斥类别。USD1等零市场范围仍列出。

总历史最多122点、仍限600秒和8000市场，不增加交易所请求。字段时间/60秒门槛原样保留；沉默市场会过期，订阅成功不使其续期。该时间序列帮助发现运行中的空窗，不能证明采样点之间无空窗、资金费新鲜、身份可比或24小时稳定；后续真实运行应保留整份报告并与实际身份/费率覆盖分别验收。本批9项离线探针回归，不是新的实网证据。
