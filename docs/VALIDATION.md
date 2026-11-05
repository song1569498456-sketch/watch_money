# 验证记录

2026-11-05 R58：价差资产聚合新增每页100组及首页/前后页，可访问原先1000条之后的资产；筛选变化真正重置页码，返回保留当前页并重验，旧响应受查询版本保护。分页与扫描预算/方向返回限制分离，页面明确实时分页可能移动或重复。**1398项后端、44项前端、7项新分页浏览器、12项既有导航、生产构建及Ruff/diff通过**。1005资产为隔离合成样本，未增加真实行情/身份覆盖或访问受阻连接。详见AUDIT_BACKLOG/INTERFACE R58及browser-scanner-pagination证据；全计划与24小时仍未验收。

2026-11-04 R57：审查其余7个CEX client并用安装CCXT复现limit=1隐藏同页冲突，全部改为完整有界原生公共页先校验。保留逐平台请求参数、SDK限流/错误、Gate秒/其他毫秒和实际结算率语义；同页费率或持久化来源元数据冲突拒收，满页/拒收停止时间继续提示。**1388项完整后端（64.69秒）、202项专项（12.02秒）、2项HTTP/缓存重启验收及CI范围Ruff/diff通过**。新增54项离线回归，无行情主机请求或受阻OKX WS重试、不加新平台/交易功能。完整历史分页、真实平台覆盖、生产身份和24小时仍开放；逐平台参数/本地cap与证据见AUDIT_BACKLOG/FUNDING_VIEW R57。

2026-11-04 R56：修复MEXC/KuCoin统一解析器limit先截断而隐藏同页冲突的问题。改走同client原生公共方法，保留SDK限流与错误；完整有界响应先验证，用户limit仅控制最终展示。MEXC页100、KuCoin窗口本地1000，超限整页拒收；冲突不改变旧缓存或修订，原生周期/单位和安全分页限制保留。**1334项完整后端（59.40秒）、132项专项（5.52秒）、CI范围Ruff/diff通过**；新增24项离线回归，包含安装版CCXT解析器复现与公共HTTP路由/限流。非实网观测，未请求受阻OKX连接、未改账户。其它平台统一历史解析的前置过滤风险待逐项复审；全市场身份/真实覆盖及24小时仍开放。详见AUDIT_BACKLOG/FUNDING_VIEW R56。

2026-11-04 R55：优先补Upbit可观察盘口缺口，全目录轻量采集增加韩国公开原生book，50市场一批，保留ticker独立字段时间；按需30档深度核对原始时间/base数量。新18项离线回归覆盖120合成小币、公共路由、缺失/错误隔离、清除/乱序/恢复、未来/过期、观察者延迟及取消。**1310项完整后端（57.05秒，1条上游弃用警告）通过**；本轮无真实行情请求，实网覆盖增量未知，生产身份未批准。未重试受阻OKX连接。全目录盘口WS、实际限流/覆盖/24小时和原清单仍开放；详见AUDIT_BACKLOG/CEX_LIGHT R55。

2026-11-04 R54：MEXC/KuCoin Classic历史新增原生合约、毫秒结算时间、费率与单位核验。MEXC仅采用原始collectCycle有效整数小时，KuCoin缺逐记录周期依据保持null；两者不发无法证明安全的继续游标，同页费率/周期冲突拒收。**1292项完整后端（53.62秒）、173项历史专项（5.03秒）、CI范围Ruff/diff通过**；新增21项离线回归含安装版公共路由和参数。只读官方文档及离线替身，未访问行情主机、私有接口或受阻OKX连接；无前端变更，远端CI未确认。dYdX不完整分页、生产身份/真实覆盖和24小时验收仍开放。详见FUNDING_VIEW/AUDIT_BACKLOG R54。

2026-11-04 R53：Binance USDⓈ-M/COIN-M历史新增原生symbol/fundingTime/fundingRate核验与来源单位记录；USDⓈ-M Regular/Special独立，COIN-M未核实特殊类型拒收，同页冲突不按顺序修订，满页/拒收停止时间游标。**1271项全量后端（53.38秒）、152项历史专项、CI范围Ruff/diff通过**；新16项回归含安装版公共fapi/dapi路由与无凭证。仅文档/离线，不扩交易或访问受阻连接；未知历史周期、dYdX不完整分页、生产身份/实网/24小时限制保持。下一项为MEXC/KuCoin原生历史字段与周期依据，详见FUNDING_VIEW/AUDIT_BACKLOG R53。

2026-11-04 R52：修复dYdX刷新页与缓存窗口混用；刷新仅返回本次有效保存页面，缓存查询明确分开。官方公开响应不能区分同高度eventId，撤回自动height-1游标，next_before_height恒null并标pagination_incomplete；同页高度/时间冲突拒收、不产生顺序依赖修订。**1255项全量后端（51.79秒）、136项历史专项、CI范围Ruff/diff通过**，新增7项混合缓存/since/多页/冲突与顺序回归。R50自动翻页步骤已撤回，仅保留有风险提示的人工高度筛选；完整回填待可证明的官方事件分页能力。本批仅离线，未请求行情主机或受阻连接，24小时仍未完成。详见FUNDING_VIEW/AUDIT_BACKLOG R52。

2026-11-04 R51：Bybit/OKX历史新增原始合约ID、结算毫秒与统一字段交叉核验，缺原始率不回退，OKX显式FUTURES不并入SWAP；保留有界单页，拒收停止继续提示。**1248项全量后端（52.56秒）、129项历史专项、2项旧验收复测、CI范围Ruff/diff通过**。新19项回归及安装解析器since参数验证，旧验收fixture补原生字段并保留缓存/重启/修订断言。只读官方文档与离线测试，无行情请求、受阻连接重试或账户更改。未知周期、完整历史、实网及24小时仍开放；下一项为Binance两个合约client原生字段/特殊事件边界，详见FUNDING_VIEW/AUDIT_BACKLOG R51。

2026-11-04 R50：补齐dYdX历史旧页读取，显式before_height对应官方effectiveBeforeOrAtHeight，缓存同步按高度上界筛选；每次最多100条，无自动回填，拒收时停止继续高度提示，未知周期保持null。**1229项完整后端（51.85秒）、110项历史专项、CI范围Ruff/diff通过**；新增11项边界/缓存/HTTP回归。仅协议文档与离线验证，未请求受阻OKX或其它行情接口，没有账户/生产身份变更。历史完整、实网及24小时仍未验收；来源、复测和下一项见FUNDING_VIEW/AUDIT_BACKLOG R50。

2026-11-04 R49：修复通用CEX watch_tickers/watch_ticker在诊断观察者后取receipt的边界；方法返回即捕获并传播到每字段，未来帧不推进乱序水位或污染去重。**1218项完整后端（51.67秒）、18项流专项、CI范围Ruff/diff通过**，新增7项覆盖三client/single与multiple、10ms未来+40ms延迟、正常/乱序恢复、取消与重订阅。仅合成回归，不声称实网Coinbase时钟异常；没有交易所请求或受阻连接重试。COLLECTION/COVERAGE明确关注币种ticker流不等于全目录book流。24小时与新修复实网验收仍开放，远端CI未确认。

2026-11-04 R48：修复Coinbase Exchange online受限产品仍可测算达标的合成边界。保留在线行情，独立记录产品限制、订单类型、来源和观测时间；disabled/cancel_only/post_only不进可靠价差排行且无即时净收益，maker费率不代表post-only撮合，limit-only保留跨价限价能力。**1211项全量后端（51.30秒）、158项相关专项、CI范围Ruff/diff通过**；新增12项限制/恢复/费用/排行回归。父线程5次公开GET正确保留book.time仅记作短时实测，未发现真实online受限样本。本批未访问外网行情、无前端或账户变更，24小时未完成。详见COLLECTION/AUDIT_BACKLOG R48。

2026-11-04 R47：新增Coinbase Exchange独立现货目录及book L1/L2原生盘口；与Advanced Trade身份隔离，目录原生币种冲突显式诊断，已有Exchange currencies证据仅同client可用。避免CCXT批量spark-line及ticker成交时间冒充BBO；保留book.time、聚合size、竞价/空侧清除与receipt。**1199项全量后端（49.32秒）、17项新Exchange专项、已有111项CEX/身份回归、CI范围Ruff/diff通过**；最终目录冲突诊断后重跑17项专项。仅离线，无行情主机请求、身份批准或前端变更；全目录时效、实网和24小时未验收。来源/复测/下一项见COLLECTION与AUDIT_BACKLOG R47。

2026-11-04 R46：新增HTX USDT线性永续v1公开已结算历史，严格realized_rate/原生合约/费用币/毫秒时间，周期未知、最多50条最新页。固定文档api.hbdm.com并保留限流，不修改共享客户端或切换主机；v5未审阅。**1182项完整后端（46.11秒）、99项联合历史专项、CI范围Ruff/diff通过**；新增14项回归含安装版底层公共请求URL及配置不变。仅离线，无行情主机请求或身份批准，接口当前实网可用性未知；24小时仍未完成。下一项为Coinbase Exchange目录与同client身份资料衔接，先验证报价时间。详见FUNDING_VIEW/COVERAGE/AUDIT_BACKLOG R46。

2026-11-04 R45：修复父线程离线复现的dYdX历史未来结算误收及同费率区块高度变化未留修订问题。PublicREST在观察者前固定每响应receipt，dYdX/Hyperliquid历史使用该时间；来源内容变化保存旧完整记录并递增revision。**1168项完整后端（42.77秒）、85项历史专项、CI范围Ruff/diff通过**。新增未来/过去延迟、取消及下次取时、高度修订去重和Hyperliquid延迟回归；详见FUNDING_VIEW/AUDIT_BACKLOG R45。仅合成验证，无实网或前端变更；未重试受阻OKX WS，未改独立环境。24小时、广泛身份覆盖及远端CI仍未验收。

2026-11-04 R44：修复历史请求排队和网络等待期间目录变化仍接受旧响应的问题；CEX重检原生合约/client/适配器，DEX补适配器替换保护。新增8项离线回归，80项历史专项、1163项完整后端（42.55秒）、CI范围Ruff/diff通过；最终将接收取时提前至scope检查之前，再跑80项历史专项通过。身份候选不自动批准，本批无覆盖扩张、实网或前端变更。未重试受阻OKX WS；24小时、广泛生产身份与新历史实网验证仍开放，远端CI未确认。详见AUDIT_BACKLOG/FUNDING_VIEW R44。

2026-11-04 R43：新增dYdX v4公共市场已结算历史，严格ticker/chain/settle、时区ISO/原生高度和费率校验；最多100条最新页、since本地过滤，历史治理周期未证明则null。**1155项完整后端（42.52秒）、72项联合历史专项、CI范围Ruff/diff通过**，新路径仅MockTransport；官方语义及独立临时库实测步骤见 [FUNDING_VIEW R43](FUNDING_VIEW.md#r43-dydx-v4公共历史)。长测403已明确发生于OKX公共ticker WS经本地代理建连，缺底层详情；同期51.7秒计时延迟与其他平台覆盖下降，责任方未知。830秒守卫停止、不重试/绕过，24小时未完成，需用户环境或支持方核查条件。未修改独立环境/前端/账户/生产身份；实网与远端CI待验。

2026-11-04 R42：修复CEX通用批量回退、single轻量、详细查询在诊断观察者之后取接收时间的问题；详细价格字段保留各自receipt，不随后续funding/OI延后。通用边界为CCXT方法返回，优化OKX/Bitget仍为原始HTTP解码后/后台解析前，不宣称通用wire时间。**1141项完整后端（46.43秒）、50项首次针对回归及49项最终调度/时间/解析专项、CI范围Ruff/diff通过**；调度fixture同步新增取时模块以保持同一模拟时钟。独立Deribit144永续核对一致/64交割排除/51专项通过已记录；24h尝试830秒因本地代理403守卫停止，原因未定且未完成，不重试/绕过。证据见 [摘要](evidence/deribit-replay-and-guard-stop.json)，复现/边界见 [COLLECTION R42](COLLECTION.md#r42-cex接收时间与观察者边界)。未改独立环境或前端，新修复实网与远端CI未确认。

2026-11-04 R41：新增Hyperliquid公共fundingHistory（首个perp dex及完整HIP-3名称），原生毫秒/小数费率/官方1h周期，严格目录/抵押币身份及请求后scope重检。单页最多500行/500小时，排序截断及游标保留缺口未知，403/451不重试。**1131项完整后端（42.08秒）、58项联合历史专项、CI范围Ruff/diff通过**。本批为MockTransport而非实网，未改前端或独立24小时环境，没有账户调用/身份批准；临时库可复现实测命令与官方依据见 [FUNDING_VIEW R41](FUNDING_VIEW.md#r41-hyperliquid公共市场结算历史)。新路径实样、完整历史和远端CI仍待确认。

2026-11-04 R40：新增HTX币本位永续公开已结算历史，严格使用原生realized_rate（安装CCXT统一字段用funding_rate），核验原始合约、费用币与funding_time毫秒。最多50条最新页，历史周期未知不补8h，since不冒充回填。**1119项完整后端（43.38秒）、46项历史专项、CI范围Ruff/diff通过**。HTX线性新版与Kraken连续资金费历史尚未接，官方语义/读取超时及限制见 [COVERAGE R40](COVERAGE.md#r40-htx币本位已结算历史与其他历史边界2026-11-04)。未改前端或父线程固定提交24小时环境，没有实网/账户访问或身份批准；新历史实样、完整覆盖与远端CI仍待确认。

2026-11-04 R39：Deribit永续按结算币批量摘要，解决现有CCXT按base分片对USDC小币种覆盖的限制；精确nativeID/base/quote过滤，源时间只取creation_timestamp，null盘口明确清除并保留同时间清除屏障，volume不按合约乘数换算。**1107项完整后端（51.62秒）、18项新增专项、CI范围Ruff及diff通过**。安装CCXT公共方法替身验证真实代码路由，不是实网；未改前端或父线程固定提交的24小时环境。现货仍有界路径，摘要资金费不自动分类，未新增身份批准；实网覆盖和完整验收开放，远端CI未确认。官方来源与范围见 [COVERAGE R39](COVERAGE.md#r39-deribit永续按结算币批量报价2026-11-04)。

2026-11-04 R38：盘点12个CEX的数据/身份缺口，新增Bitget Classic与Gate公开已结算资金费历史。严格原生市场/settle和原始结算时间核对，费率保留小数比例与符号，历史周期未知不补8h；Bitget仅最新page1并禁止伪回填游标。**1089项完整后端（41.44秒）、34项历史专项、CI范围Ruff/diff通过**；包含安装版CCXT公开路由/秒毫秒离线校验。未改前端、未新增实网或身份批准、未触碰父线程固定提交的24小时测试。HTX所查公开币种文档缺合约地址、MEXC Wallet需鉴权，未调用私有端点。逐平台缺口和官方来源见 [COVERAGE.md](COVERAGE.md#r38-cex数据与身份缺口盘点2026-11-04)，新增历史边界见 [FUNDING_VIEW.md](FUNDING_VIEW.md)。新历史实样、广泛身份覆盖及长期验收仍开放，远端CI未确认。

2026-11-04 R37：记录父线程对6da269a的180秒混合流实测：无拒绝/重连，最后新鲜现货1579/2186、USDT永续911/1070、BTC永续1/1；USD1未发现合约，未测。65条原始报文与patch的命名空间/价格/源时间一致，包含分数合约数量；这不是24小时验收，5d33823确认计数修复待独立复验。新增Gate探针定时覆盖序列（默认15秒，最多122点），无消息时继续检查，分开未收到/缺盘口/不可信时间/过期/其他无效，禁止静默市场算作实时。**9项探针离线回归、CI范围Ruff及diff通过**；本批未重跑全量或浏览器，未新增实网测试。身份生产覆盖、资金费独立时钟和长期验收继续开放。见 [实网报告摘要](evidence/gate-mixed-parent-live-summary.json) 和 [GATE_QUOTES.md](GATE_QUOTES.md)。

2026-11-04 R36：修复Gate现货成功确认省略可选error时误报未关联，现货/永续统一保留精确请求ID、成功对象与非空error校验。44批确认回归按父线程报告形状重建，非原始抓包。慢观察者测试以接收屏障替代sleep，覆盖额外pong，保留关闭新增64排队+1在途及重复关闭精确断言；生产计数不变。**1074项完整后端（41.57秒）、20轮/40例观察者重复验证、CI范围Ruff（E4/E7/E9/F）及diff检查通过**。默认全规则Ruff仍报告既有导入/异常类型规范问题，非本批验收范围。父线程3次原生快照成功/3次本地429/1次超时已记录；本批未改前端、未做实网或浏览器复测。Gate永续覆盖、资金费时钟与24小时仍开放，远端CI未确认。详见 [GATE_QUOTES.md](GATE_QUOTES.md) 与审计清单。

2026-11-04 R35 Gate永续实时集成：现货与BTC/USDT/USD1永续最多4个独立公开连接，准确nativeID/settle、总8000/命名空间5000预算、公共小数数量头；复用有界合并、清除/乱序、独立退避与取消清理。partial/blocked诊断保留，资金费不借用盘口时钟。**1059项完整后端（40.95秒）、最终探针3项专项、Ruff/diff通过**；未改前端，本批未重跑浏览器。合成四命名空间证据见 [gate-mixed-stream-synthetic.json](evidence/gate-mixed-stream-synthetic.json)，公开无用户DB复测命令见 [GATE_QUOTES.md](GATE_QUOTES.md)。当前云环境无Gate访问许可，未执行实网；永续真实覆盖、长期稳定及24小时仍开放，远端CI未确认。

2026-11-04 R34 Gate按需快照与诊断：详情获取盘口使用有界公开原生快照，区分交易所生成current与盘口变动update；无原生时钟不更新监控、旧快照不虚报更新，保留2并发/30秒单市场冷却。订阅确认按请求id关联，收到/确认/实际报价分开，字段缺失/过期/未来在设置诊断显示。永续仅新增官方BBO纯解析器，未接实时流；资金费仍缺独立源时钟。**1034项后端（41.15秒）、44项前端、6项浏览器检查、生产构建及Ruff/diff通过**。父线程实网2186订阅/180秒122560帧及1671有时间/1463新鲜已记录，新快照路径未在当前受限环境实网验证。见 [GATE_QUOTES.md](GATE_QUOTES.md)、[浏览器证据](evidence/browser-gate-snapshot.json) 与审计清单；不将临时身份批准写作生产覆盖，24小时等开放项不变，远端CI未确认。

2026-11-04 R33 Gate报价时钟：官方REST现货ticker缺盘口更新时间；新增公开spot.book_ticker全现货目录流，仅使用result.t，保留原始收包/字段时间、清除屏障和有界队列。晚到无时间REST不覆盖有效WS盘口或续期，未知/过期仍不排行。**983项完整后端（40.30秒）、Ruff/diff通过**；本批未改前端，未重跑浏览器。合成可靠方向0→2→2→0见 [证据](evidence/gate-quote-time-synthetic.json)，真实Gate流覆盖尚待外部实测。父线程真实身份隔离演练3/47→202/487→3/47通过、原库哈希未变，不是生产批准。官方语义、运行预算与开放项见 [GATE_QUOTES.md](GATE_QUOTES.md)，24小时/资金费等验收保持开放；远端CI未确认。

2026-11-04 R32 官方身份批量核验：新增严格普通现货策略、精确计划 token、事务内复核与既有映射保护；只读原库、白名单复制到临时库演练批准/撤销。**949 项完整后端（40.30 秒）、Ruff/diff通过**，其中 34 项新增策略/演练回归；未改前端，本批未重跑浏览器。合成覆盖 0→1→0（2 现货市场），同名异链拒绝；真实本地目录为空，没有批准任何真实映射。父线程199组/440市场仍是待验证候选、实际覆盖仍按3组观察，需在其数据环境执行隔离演练。命令、证据及网络/产品边界见 [IDENTITY_REVIEW.md](IDENTITY_REVIEW.md)；其余实网与24小时验收仍开放，远端CI未确认。

2026-11-04 R31 探针单点超时继续：逐请求记录超时/阶段/耗时/部分字节，保留同轮成功结果，后续轮次继续；2并发、0.25秒起始间隔、60轮上限及最多60秒退避，连续3轮全失败或不可恢复响应终止。逐轮原子检查点与按请求流汇总；原始ts和60秒比较门槛不变，未增加直接替换批量。UI将请求状态与源时间过龄分开。**915项完整后端（39.66秒）、44项前端、6组浏览器、构建与Ruff/diff通过**。新版只完成本地模拟验证，真实配对延长测试与过龄根因仍待确认，远端CI状态仍未确认。见 [COLLECTION.md](COLLECTION.md) 与 [审计清单](AUDIT_BACKLOG.md)。

2026-11-04 R29/R30：新增公开OKX直接/ANY配对取证脚本，保留原始ts、收包与解码时刻、时钟区间；传输成功与统计时费率字段状态分开显示，60秒比较门槛不变。新增只读身份审核包与一致快照token，候选/实际覆盖分开统计。**911项完整后端（39.39秒）、44项前端、6组浏览器、生产构建及Ruff/diff通过**。此环境未执行真实OKX配对且本地身份数据为空，未新增真实确认组；真实资产覆盖仍按父线程三组观察记录。远端Actions查询Forbidden，CI状态未确认。详见 [COLLECTION.md](COLLECTION.md)、[IDENTITY_REVIEW.md](IDENTITY_REVIEW.md) 与 [审计清单](AUDIT_BACKLOG.md)。

2026-11-04 R28 重复取消边界修复：CEX/HL 解析等待在每次取消后仍保护实际工作 future，锁/计数保留到线程完成；close 使用共享内部任务，关闭客户端与子连接后才传播调用者取消。关闭中禁止重开，完成后可重连。新增阻塞线程、超时后多次取消、并发关闭、容量和错误回归；**903 项完整后端测试（44.65秒）、Ruff/diff通过**。取消可能等待超过原超时，不能强制杀线程或虚报完成。实网时效仍未验收。详见 [审计清单](AUDIT_BACKLOG.md)。

2026-11-04 R27 解析占用优化：OKX/Bitget 已审阅普通批量 ticker 使用有界解析线程，固定 CCXT4.5.91；HL >=8KiB JSON 帧有界后台解码，小帧保留快速路径。接收时间在解析前保存，取消/关闭等待在途工作；未知版本与特殊路由的回退可在 query-work 诊断确认。**897 项完整后端测试（37.00 秒）通过，最终新增时间回归后的19项解析专项及Ruff/diff通过**。合成同响应结果一致，显著缩短循环连续占用；HTTP JSON、资金费解析及小帧仍有循环CPU，不能宣称实网HL超时/OKX空窗解决。OKX官方时间语义及证据见 [COLLECTION.md](COLLECTION.md) 和 [审计清单](AUDIT_BACKLOG.md)。

2026-11-04 R26 健康提醒交错修复：扫描仅保存市场 ID，yield 后重读当前快照；发送前复核相同版本、有效期、配置及撤销/退役状态。真实过期继续提醒，重复检查由现有通知服务去重。新增 7 项交错/取消/重复提醒回归；**873 项完整后端测试（31.23 秒）、Ruff/diff 通过**。范围为扫描/入队前核验，未新增已入队通知的状态撤回协议；实网时效仍待对照。见 [审计清单](AUDIT_BACKLOG.md)。

2026-11-04 R25 热路径优化：关闭录制/通知不再为每个事件读取 SQLite；设置快照在成功提交后发布，读副本隔离且不等待写锁。录制队列上限 16、配置不变不重复扫描目录，排队任务复核配置并在关闭时等待在途写入。覆盖分类缓存至精确时间边界，内部统计在后台单任务执行；健康检查移除全量快照序列化，通知关闭跳过扫描。**866 项完整后端（31.28 秒）、5 组平台切换浏览器回归、Ruff/diff 通过**。7000 市场本地合成重复统计约 10ms，设置读取对照见 evidence；不代表 HL/OKX 实网问题解决。单采集进程设置一致性、取消与持久化边界见 [COLLECTION.md](COLLECTION.md)，详细证据见 [审计清单](AUDIT_BACKLOG.md)。

2026-11-04 R25 卡顿取证：新增默认关闭的独立线程探针（`WATCH_MONEY_STALL_PROBE=1`），固定预算抓取循环/线程栈及资源计数，区分循环心跳停滞与共同调度空窗。855 项完整后端测试通过（30.98 秒）；最终资源基线与运行状态补充后 4 项定向测试及 Ruff 通过。本地可控阻塞证据见 [stall-probe-synthetic.json](evidence/stall-probe-synthetic.json)，操作及解释见 [COLLECTION.md](COLLECTION.md)。无实网根因结论；HL/OKX 持续时效、身份覆盖和 24 小时验收仍开放。

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

## 数据独立复审批次（2026-11-04）

根任务完整后端677 passed（19.38s，1条上游弃用提示）、前端29 passed、TS/Vite构建、Ruff通过。最后Hyperliquid节流间隔从1秒调整为2秒后专项25 passed；其后最终六套浏览器 baseline6 / followup12 / fee7 / navigation9 / funding6 / asset-verification8，共48组通过。资产证据浏览器使用真实本地API完成导入、预览、应用、重载、冲突降级和撤销；只有公共拉取失败通过浏览器模拟。

新增独立数据验收29例覆盖调度/费率/身份/DEX报价币边界。全部成功数据为合成样本或本地模拟，不是实网。当前公开端点真实探测均ProxyError，未取得成功响应或24小时证据。汇总见 [data-review-validation.json](evidence/data-review-validation.json)，具体范围与未完成项见 [AUDIT_BACKLOG.md](AUDIT_BACKLOG.md)。

## 前端信息组织批次（2026-11-04）

根任务独立运行完整后端 **575 passed**（17.80s，1条上游Starlette弃用提示）、Node **25 passed**、TS/Vite生产构建、Ruff `E4,E7,E9,F`、diff检查通过。新增测试覆盖类型筛选在扫描预算前生效、矩阵平台筛选在排序/截断前生效、同名未知身份隔离、原始周期与字段TTL。

隔离浏览器 baseline6、followup12、fee7、navigation9组通过。导航证据包含默认价差、资金费矩阵入口、精确两腿交接、315px原位返回、搜索/平台/类型/排序/展开保留、隐藏期间debounce返回补扫、独立监控、768px导航文字及390px文档无溢出；表格在自身区域横向滚动。新建本地监控后在独立入口找到，未发自动计算、账户或交易请求。最终CSS调整后根任务再次运行navigation9组通过。一次前端点击超时未复现，复跑同一构建通过；不将它写作已证实产品缺陷。

资金费专项浏览器另6组通过：四类状态、极小非零费率、筛选前置、单平台排行、详情历史仅手动及手机布局。总计40组浏览器检查。汇总见 [interface-validation.json](evidence/interface-validation.json)。

[导航证据](evidence/browser-navigation.json) · [界面使用说明](INTERFACE.md)。全部是合成数据和本地模拟，**不是实网或24小时验收**。

以下保留上一功能批次验证记录。


日期：2026-11-04。环境：托管 Linux 云工作区，Python 3.12、Node 24、Chromium。所有操作都是只读行情监控，没有交易执行。

## 自动测试

本轮最终离线回归 **Python 563 passed，1 条上游 Starlette 弃用 warning，17.71s**；**前端 Node 18 passed**；TypeScript/Vite build、CI Ruff `E4,E7,E9,F` 与 `git diff --check` 均通过。机器记录与版本见 [followup-validation.json](evidence/followup-validation.json)：Python 3.12.14、CCXT 4.5.91、FastAPI 0.162.0、Pydantic 2.14.2、pytest 9.2.0、websockets 17.4。最终bundle的整合浏览器已由root实跑通过：baseline **6组**、followup **12组**、费用 **7组**；结果均在聚合证据browser_results内。

本轮功能范围已收敛，扫描/资金费/原生/历史/费用配置与通知过期重试修复已有离线回归。费率目录是22个普通公开参考，范围不匹配或未知保持空，包含 Binance perpetual 未确认；费用切腿与整合浏览器也已复跑通过；这仍是离线/隔离模拟交付。逐项见 [AUDIT_BACKLOG.md](AUDIT_BACKLOG.md)。下一阶段先验证真实全币盘口、身份与稳定性，资金费聚合其次，其他简洁指标/已有历史再次，不扩展账户或本轮原生范围。

本次文档审计只执行 9 项定向检查，全部通过，命令记录在 AUDIT_BACKLOG.md：OKX原生volume/已安装parser/盘口与OI、原生最小数量、人工映射不能掩盖原生合约冲突。没有重跑全套，也不把此9项视为最终总数；其他修复按各自新增回归/浏览器证据更新，不因收到报告直接关闭。

前端18项纯函数用例见 `frontend/tests/marketData.test.mjs` 与 `bookSizing.test.mjs`：字段时效/范围/资金费事件去重与类型隔离、实际逐档金额换算/单位/深度/时间校验。旧171项仅是上一轮历史基线，不是当前总数。

```bash
.venv/bin/pytest -q
.venv/bin/ruff check backend tests scripts --select E4,E7,E9,F
cd frontend && npm ci && npm run build
# frontend 目录：npm test
```

覆盖资金费正负与异周期、同时间结算回本、资金费名义价值、合约乘数、过期字段、盘口规模与不足、完整开平仓费用、汇率、DEX 精确路线与费用不重复、时序回放、映射审计、历史/配置恢复、持久化队列、重试、去重、关闭开关、自动监控失效，以及并发保存容量与无丢失更新。

## 本轮新增验证范围

| 模块 / 回归文件 | 已有证据与明确边界 |
| --- | --- |
| `test_cex_light.py`、`test_dex_catalog.py` | 非主流币完整发现、部分服务失败/恢复、client/产品/结算币分组、批量/单条成本、可配置搜索/已知池、空目录与稀疏补丁。六现货 DEX 仍 search_limited；不是全链索引证明 |
| `test_dex_streaming.py` | Hyperliquid/dYdX 订阅与字段协议、心跳、取消/重连、独立时间与 loopback control ping/pong；本地 socket 不是生产服务接入 |
| `test_onchain.py` | 66 项独立 mock RPC 验证：chain ID、factory/成员/代码存在、canonical block hash、decimal/ABI/方向/数量、Pancake/Aerodrome estimate 与凭证脱敏。与原 DEX 测试当次合跑 105 项通过；这是模块检查记录，不是最终总数。没有主网 RPC/编译字节码/交易执行证明 |
| `test_recording.py` | 有限公共 payload 录制开关、脱敏、额度、保留期、重启/并发/磁盘失败。没有真实生产录制，也不是完整原始帧/协议回放验证 |
| `test_identity.py` | 全目录身份候选、同名不同原生 ID、链地址大小写/冲突、包装/桥接/单位限制及 native_asset_id 持久化，不声称全币种官方身份已核验 |
| `test_followup_acceptance.py` | 独立集成回归：广覆盖批次/小币可见、字段时间、防未来时间污染、精度/资金费覆盖值、原生池 API/身份冲突，以及停机过期通知的发送/重试/去重修复；已纳入563项通过 |
| `test_volume_units.py` 与旧缓存回归 | 详细/轻量 REST 和源 WS 共用原始字段 volume helper，OKX contract/base 区分及反向不以现价倒算；旧未核验 CCXT derivative volume 读取时抑制，不传播错误单位 |
| 最小数量 / 双腿持仓成本 / WS delta 回归 | min_amount 按 spot base / perpetual contracts 校验；两腿小时持仓费按各自名义/时长与一次 FX 计费；local WS 首次完整、后续增量/删除、过期转变及重置；仍非所有协议自动成本或大规模持续负载证明 |
| `test_scanner.py` | 已纳入563项通过；身份组跨 venue 双向指示价差/8h funding、资产best/展开、时效/缺字段/截断，净收益 null、executable=false；不当作真实成交验收 |
| 广覆盖资金费采集 | 已纳入563项通过：CEX 独立任务默认 60 秒/100 市场；DEX 通过 fetch_light 复用已有公共 REST 资金费 payload，funding_state=via_light_rest，不启动独立 funding task。各自遍历已发现活跃 perpetual；缺字段/周期未知，实网未验 |
| `test_funding_view.py` / `test_funding_history.py` | 已纳入563项通过；全 perpetual 行/身份矩阵/字段有效性与手动单页历史、来源/原始值、分页提示、重复/修订/失败处理分别验证。当前快照与结算历史明确分开，未完成实网核对或后台全历史回填 |
| `test_fee_profiles.py` | 已纳入563项通过；112个venue/product/role档案中22条普通公开参考，其余未知或not_applicable；scope/时间/覆盖审计/重启/事务与用户输入不可伪造核验标记。不是账户实际费率，前端切腿/同平台换产品/角色隔离7组浏览器也通过 |

默认 28 平台启用只针对新数据目录，已有持久化设置保留。广覆盖价格默认 30 秒/100 市场分批轮转，目录 1800 秒重发现；真实限流、每批请求数量与全目录更新延迟待实网核对。CEX 的资金费独立频率与 DEX 复用广覆盖路径分开显示；资金费周期有明确元数据才归一化，不能以轮转频率替代真实 funding interval。

独立验收与具体复现见 [ACCEPTANCE.md](ACCEPTANCE.md)。本轮唯一 warning 为上游 Starlette TestClient/httpx 弃用提示，测试退出成功；它不是行情接入或生产验收证据。

## 浏览器实测

```bash
.venv/bin/pip install -e '.[browser]'
# 本云环境已提供 /usr/bin/chromium；其他环境先 python -m playwright install chromium
.venv/bin/python scripts/browser_smoke.py
```

脚本创建隔离临时数据目录和临时服务，不修改用户数据，移除外部消息凭证。上轮实际验证：真实模式空状态、显式演示开关、搜索、收藏持久化、资产详情、无执行深度时净收益为空、保存/回放、持续监控创建、本地通知入队与送达、390px 手机宽度无横向溢出，五个页面无 JavaScript 错误。

本轮 [browser-followup.json](evidence/browser-followup.json) 当前记录 **12 组通过**：合成 PEPE 候选隔离/搜索；funding carry事件去重及current/predicted/settled/local系列隔离；退出prices/basis0互斥payload；多档ask预算换算及无效/不足深度阻止；无RPC禁用；DEX设置校验/保留；mock原生decimal/不可执行；资产展开到精确ID/策略handoff且不自动提交；扫描方向/排序/刷新；截断范围声明；funding raw/8h排序/矩阵与独立mock结算历史；手机布局/零JS错误。该流程使用隔离合成数据和浏览器 mock 响应，**没有请求真实 RPC，也没有证明真实小币报价或执行能力**。该最终bundle的构建、563 Python / 18 Node和12组接续浏览器均通过。

最终 [browser_fee.json](evidence/browser_fee.json) **7组通过**：按instrument加载四个独立maker/taker开平仓字段、换腿只清当前侧/holding、同平台换产品、scope外有效null不回落表参考、保留来源/核验时间、手填/零值标用户假设、保存覆盖与恢复默认。root独立复跑已通过。所有费用浏览器资料为synthetic_fee_references_not_live，没有账户API。

证据：[机器可读结果](evidence/browser-smoke.json)、[真实模式空状态](screenshots/live-empty.png)、[显式演示总览](screenshots/demo-overview.png)、[资产详情](screenshots/demo-detail.png)、[计算与监控](screenshots/calculator.png)、[本地通知](screenshots/notifications.png)、[手机布局](screenshots/mobile-demo.png)。截图中的演示行情不是真实市场样本。

## 云端运行与真实网络

首批 6 个官方 API 的实际探测全部返回 ProxyError，结果在 [live-probes.json](evidence/live-probes.json)。这是实际网络阻塞证据，不是接口实现成功证据。官方文档及 SDK 核对见 DATA_SOURCES.md。

[short-soak.jsonl](evidence/short-soak.jsonl) 记录了 180 秒、36 次 HTTP 健康/状态/行情探测，HTTP 错误 0；采集关闭、有效真实行情为 0。这仅验证短时应用可用性，**不是 24 小时真实行情稳定性验收**。

随后 [final-runtime-soak.jsonl](evidence/final-runtime-soak.jsonl) 记录了开启实际采集的 72 秒、12 次探测，HTTP 错误仍为 0；行情源被代理拒绝、重试状态可见、没有有效实时行情。这证明受限网络下应用仍可服务，不证明成功接入实时源。

## 当前验收边界

- 本地可运行基础版本、计算样本与模拟通知通过已执行检查。
- 全计划尚未通过；原生协议覆盖与可执行报价、在线真实样本核对、Windows 实机和至少 24 小时真实行情运行仍需完成。
- 用户明确要求机器人暂不配置，只做模拟测试；没有真实飞书/Telegram 消息发送。
- GitHub Actions 将重复后端、前端和隔离浏览器验证；只有实际 workflow 成功才记作 GitHub CI 通过。
