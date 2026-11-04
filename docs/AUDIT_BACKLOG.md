# 分阶段审计与待办

更新：2026-11-04 UTC。每批修改都要更新本表的状态、代码、测试和证据，再更新 STATUS / COVERAGE / VALIDATION；**收到问题、存在代码或有一个 fixture 不等于完成验收**。用户当前顺序为：数据正确性 → 按资产监控聚合/价差规模闭环 → 资金费 → 其他简洁指标/已有历史。不新增交易账户，不扩大本轮历史/原生范围。

状态含义：“定向mock/离线通过”仅证明所列本地检查，不承诺生产兼容；“实网受阻/未验”与“未开发”继续保持对应缺口，不能用mock或父线程报告勾选完成。上一功能批次离线结果记录于 [followup-validation.json](evidence/followup-validation.json)：Python 563 passed（1条上游Starlette warning）、前端Node 18 passed、TS/Vite build、CI Ruff E4/E7/E9/F和diffcheck通过；最终整合浏览器全部通过：baseline6组、followup12组、费用7组，均见同一聚合证据。完整三阶段仍未通过验收。

父线程曾报告 OKX **1750**、Bitget **4597**、Hyperliquid **347**、Lighter **213** 个市场。这些是**父线程报告，非本执行环境采集样本**；未收到可复核的当前原始响应、请求时间/产品口径和网络来源前，不作本环境已实测数量，也不按这些数字认定发现完整。

<a id="p0"></a>

## P0：数据与计算正确性

| ID / Priority | Status | Code | Tests | Evidence | Limitations / 下一步 |
| --- | --- | --- | --- | --- | --- |
| D01 全币种目录真实采集 / P0 | 目录路径已实现；实网受阻 | [cex.py](../backend/watch_money/adapters/cex.py)、[dex.py](../backend/watch_money/adapters/dex.py)、[collector.py](../backend/watch_money/collector.py) | [test_cex_light.py](../tests/test_cex_light.py)、[test_dex_catalog.py](../tests/test_dex_catalog.py) | 非主流币、不同产品/client、部分发现失败的 fixture 已有；上述四个数量仅父线程报告 | 在授权可达环境保存各平台真实目录、产品/时间/分页/失败信息；DEX 搜索目录不是全链索引，不能用返回个数证完整 |
| D02 同名异币及原生证据 / P0 | 保护已实现；本次定向 mock 通过 | [identity.py](../backend/watch_money/identity.py)、[storage.py](../backend/watch_money/storage.py)、[strategies.py](../backend/watch_money/strategies.py) | [test_identity.py](../tests/test_identity.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py) 的 `test_manual_mapping_cannot_hide_conflicting_native_contracts_from_calculation` | 本次独立定向检查 9 项中的该项通过；native_asset_id 持久化，人工映射不能抹掉不同原生链/地址/单位 | 全币种官方 chain/address、包装/桥接与价格单位证据仍不完整；确认前保留候选/冲突，不按 ticker 自动合并 |
| D03 源过期、未来时间及恢复 / P0 | 已实现；563离线回归通过，实网未验 | [collector.py](../backend/watch_money/collector.py)、[streaming.py](../backend/watch_money/adapters/streaming.py)、[dex_streaming.py](../backend/watch_money/adapters/dex_streaming.py) | [test_core.py](../tests/test_core.py)、[test_streaming.py](../tests/test_streaming.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py) 的未来 watermark/保留字段年龄用例 | 字段级 source/receipt、未来事件不推进 watermark、修正报价恢复、旧 REST 不覆盖新 WS 的 fixture 已有 | 缺源时间必须明确；新 envelope/价格不得替旧 funding/其他价格续期。实网断线、重放及 24h 待验 |
| D04 OKX volume、盘口与 OI 单位 / P0 | 修正已实现；本次定向 mock 通过 | [volume_units.py](../backend/watch_money/adapters/volume_units.py)、[cex.py](../backend/watch_money/adapters/cex.py)、[collector.py](../backend/watch_money/collector.py) | [test_volume_units.py](../tests/test_volume_units.py)：原生量、已安装 CCXT parser、盘口/OI；[test_followup_acceptance.py](../tests/test_followup_acceptance.py) | 本次 9 项检查含 linear/inverse 原生 volume、parser 错标复现、book 只转换一次且 OI 保留 contracts；全部通过 | 不把 unified baseVolume 的 contracts 当 base；反向不以现价倒推历史量。旧未核验 CCXT volume 缓存/历史读取抑制；真实 OKX 样本与跨平台单位仍待验 |
| D05 逐市场失败隔离与限流 / P0 | 已实现；563离线回归通过，实网未验 | [cex.py](../backend/watch_money/adapters/cex.py)、[collector.py](../backend/watch_money/collector.py) | [test_cex_light.py](../tests/test_cex_light.py)、[test_acceptance.py](../tests/test_acceptance.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py) | 单条/批次/client 分组隔离、失败批次游标继续、429 退避 fixture 已有 | 实际 limiter、endpoint 内部分页/重试、一次全目录延迟和部分错误持续性未实测；100 市场批次不保证100次以内 HTTP或全部实时 |
| D06 step / min amount / 剩余敞口 / P0 | 数量门槛/金额helper/费用联动离线验证通过 | [strategies.py](../backend/watch_money/strategies.py)、[bookSizing.ts](../frontend/src/bookSizing.ts)、[main.tsx](../frontend/src/main.tsx) | [test_strategies.py](../tests/test_strategies.py) 的 step/minimum；[test_followup_acceptance.py](../tests/test_followup_acceptance.py) 的 `test_native_minimum_quantity_is_enforced_in_correct_units` | 本次 9 项包含 spot base / perp base-depth / contracts-depth 的最小量3例通过；不静默四舍五入数量 | root报告前端纯函数18项通过；已检查 [bookSizing.test.mjs](../frontend/tests/bookSizing.test.mjs) 及12组浏览器记录的多档asks/深度不足/未来/缺盘口拦截。最终563 Python、18 Node及6/12/7组浏览器均通过；不得静默舍去剩余金额/敞口或外推未提供深度 |
| D07 平台 spot/perp maker/taker 公共默认费率 / P0 | API/22参考档案与费用UI已接；离线/7组费用浏览器通过 | [fee_profiles.py](../backend/watch_money/fee_profiles.py)、[public_fees.json](../backend/watch_money/adapters/public_fees.json)、[main.tsx](../frontend/src/main.tsx)、[FeeProfiles.tsx](../frontend/src/FeeProfiles.tsx) | [test_fee_profiles.py](../tests/test_fee_profiles.py)、[test_strategies.py](../tests/test_strategies.py)；563离线与最终7组费用浏览器通过 | 已检查22个普通公开参考：OKX/Bybit/Bitget/Hyperliquid/Lighter各4，Binance仅spot2；来源/checked_at/scope分别保留，Binance perpetual未知，Bybit特殊区/HL spot/HIP-3等scope guard不把表参考当有效值。563离线含fee_profiles回归通过，root最终独立7组费用浏览器通过 | 按 venue/client/spot-perp/maker-taker 分清，记录官方 URL、时间、适用等级/折扣；默认可改，未知不能补零；不引入账户/本金要求 |
| D08 切换两腿后费用残留 / P0 | 已修；root最终独立7组费用mock浏览器通过 | [FeeProfiles.tsx](../frontend/src/FeeProfiles.tsx) useTradeFees、[main.tsx](../frontend/src/main.tsx) | [browser_fee.json](evidence/browser_fee.json)、[test_fee_profiles.py](../tests/test_fee_profiles.py) | 已读instrument ID归属/切腿清空holding和开平仓fee/取消旧加载结果；7组记录包含只清换侧、同平台换产品、role独立、out_of_scope不回落非空默认；最终bundle的7组费用浏览器已由root独立复跑通过 | 切换买卖 ID、产品或调换方向应重载匹配默认/清空无效自填值，并明确用户覆盖；不能把旧平台费用带入新腿 |
| D09 双腿 holding 与借贷不静默 / P0 | 已实现；563离线回归通过，实网未验 | [strategies.py](../backend/watch_money/strategies.py)、[main.tsx](../frontend/src/main.tsx) | [test_strategies.py](../tests/test_strategies.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py) 的双腿 holding/一次 FX | 两腿显式小时费按各自名义与8/24/72h计，短现货借贷未知会阻止净收益；持续成本与首次回本回归已有 | 用户假设不是协议实测费率；funding 情景不缩放 holding，未来费配置不能前视。所有协议自动持仓费模型仍未完成 |
| D10 退出价格/退出基差 UI / P0 | 已修；12组mock浏览器含prices/basis0与最终构建通过 | [strategies.py](../backend/watch_money/strategies.py) `exit_prices` / `exit_basis_quote`、[main.tsx](../frontend/src/main.tsx) exitMode | [test_strategies.py](../tests/test_strategies.py) 有退出缺失门槛；[browser_followup.py](../scripts/browser_followup.py) 的互斥payload检查 | 已读unknown/entry/prices/basis、正退出价/有限基差及互斥请求；[12组浏览器证据](evidence/browser-followup.json) 包含两腿prices及basis=0。请求valuation_currency固定buyI.quote，因此当前基差标签与后端估值币一致 | 按腿 quote 单位展示退出价格，基差使用估值币单位；未知/非正/与策略冲突应拦截，不默认零基差或关闭费 |
| D11 通知 retry 的二次时效校验 / P0 | 已修；563离线回归通过，实网发送未验 | [notifications.py](../backend/watch_money/notifications.py) 队列发送/重试 | [test_notifications.py](../tests/test_notifications.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py)；新增 `test_restart_never_delivers_expired_opportunity_from_queue` 三状态与 `test_unsent_expired_notification_allows_new_fresh_same_opportunity` | 已读 enqueue固定delivery_expires_at/claim复验代码；root/acceptance报告19项相关测试通过，已包含最终563离线回归。停机1h后 pending/retry/sending 不发送且expired留审计；未送达过期且仍为最新通知时释放旧dedup，已sent/更新状态不误清 | 本地模拟证明修复路径，不是provider或真实停机实测；最终后端离线通过，保留无真实外发边界 |

## P1：监控聚合与价差规模闭环

| ID / Priority | Status | Code | Tests | Evidence | Limitations / 下一步 |
| --- | --- | --- | --- | --- | --- |
| M01 全目录轮转与用户配置 / P1 | 已接入；563离线回归通过，实网未验 | [models.py](../backend/watch_money/models.py)、[collector.py](../backend/watch_money/collector.py) | [test_cex_light.py](../tests/test_cex_light.py)、[test_dex_catalog.py](../tests/test_dex_catalog.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py) | 新库默认28平台，已有启用列表保留；30s/100批次、1800s重发现，关注资产详细采集另行进行 | 轮转不是同时实时全覆盖；部分服务目录不完整、六DEX search_limited 必须显示；真实负载和全目录更新延迟未验 |
| M02 自动双向盘口价差与筛选 / P1 | 后端冻结；563离线/最终12组浏览器通过 | [scanner.py](../backend/watch_money/scanner.py)、[ScannerPage.tsx](../frontend/src/ScannerPage.tsx) | [test_scanner.py](../tests/test_scanner.py) 的反向 bid/ask、平台/资产筛选、预算截断 | 按身份合格组枚举跨 venue 两个方向，quote不同缺FX则无价格差，净收益一直null/executable=false | 是 bid/ask 指示筛选，不含规模深度/费率；超 max_candidates 仅 bounded_prefix，不能宣称找到全市场最佳 |
| M03 资产行 best 与展开双向组合 / P1 | 后端/资产展开UI冻结；563离线/最终12组浏览器通过 | [scanner.py](../backend/watch_money/scanner.py) asset summaries、[ScannerPage.tsx](../frontend/src/ScannerPage.tsx) | [test_scanner.py](../tests/test_scanner.py) 的资产best/截断口径；最终12组浏览器通过 | 已读每资产 best_spread_candidate/scan_complete 及前端资产行details展开；root报告scanner冻结/96项相关通过，已检查最新12组浏览器记录含展开与精确handoff。最终bundle构建、563离线与12组接续浏览器全部通过 | 一行资产排序→展开组合，显示身份受阻与候选不足；best须注明是否只在已扫前缀，避免重复行冒充独立流动性 |
| M04 正确 ID、方向与两腿时间 / P1 | 精确handoff mock浏览器与最终构建通过 | [scanner.py](../backend/watch_money/scanner.py)、[ScannerPage.tsx](../frontend/src/ScannerPage.tsx)、[main.tsx](../frontend/src/main.tsx) | [test_scanner.py](../tests/test_scanner.py) 的方向/逐字段provenance；[browser_followup.py](../scripts/browser_followup.py) 的exact-ID/strategy断言 | 已读 candidate buy/sell ID、raw quote和逐腿时间；Scanner onSelectCandidate将两个ID/策略交main切入calculator，不靠ticker回找；96相关mock通过由root报告；最新browser-followup精确ID/策略handoff通过，且点击本身不自动提交计算 | 选中组合必须把原始两个ID/策略带入calculator，反向不能复用正向book/fee；两腿源时间与本地时间都可核对 |
| M05 选中规模的实际核算 / P1 | 金额/退出/fee切腿离线闭环及最终浏览器通过 | [bookSizing.ts](../frontend/src/bookSizing.ts)、[main.tsx](../frontend/src/main.tsx)、[strategies.py](../backend/watch_money/strategies.py) | [test_strategies.py](../tests/test_strategies.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py)；[bookSizing.test.mjs](../frontend/tests/bookSizing.test.mjs)、最新browser-followup；7组费用切腿浏览器通过 | 后端 supplied depth VWAP/容量有fixtures；12组浏览器包含多档asks预算→base、缺费net未知及无效深度阻止。纯函数18项通过由root报告；fee UI最终7组浏览器通过 | 不能把headline价当成交价、按最后价外推深度或静默舍去剩余；必须刷新选中两腿、相同base规模、精度/min amount、费用、退出假设均满足才给net |
| M06 本地 WS delta 与源连接 / P1 | 已接入并有回归；真实负载未验 | [market_stream.py](../backend/watch_money/market_stream.py)、[app.py](../backend/watch_money/app.py)、[streaming.py](../backend/watch_money/adapters/streaming.py)、[dex_streaming.py](../backend/watch_money/adapters/dex_streaming.py) | [test_market_stream.py](../tests/test_market_stream.py)、[test_streaming.py](../tests/test_streaming.py)、[test_dex_streaming.py](../tests/test_dex_streaming.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py) | 初始full、后续upserts/removals、stale transition、模式重置；HL/dYdX loopback heartbeat已有 | 本地WS连接不证明源WS在线；源支持平台不同，无全协议深度重建/历史恢复；大目录CPU与24h未验 |

## P2：资金费聚合与真实结算语义

| ID / Priority | Status | Code | Tests | Evidence | Limitations / 下一步 |
| --- | --- | --- | --- | --- | --- |
| F01 全 perpetual 资金费采集/同步 / P2 | 离线回归通过；实网周期/时效待验 | [cex.py](../backend/watch_money/adapters/cex.py)、[collector.py](../backend/watch_money/collector.py) | [test_cex_light.py](../tests/test_cex_light.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py) 的 catalog funding rotation | CEX独立60s/100轮转；DEX复用广覆盖payload，funding_state=via_light_rest；非大币不按watchlist裁剪 | 来源不提供周期/资金费时保持未知；本地采集频率不是funding interval；metadata观测TTL与动态费率源时间不可互相刷新 |
| F02 矩阵周期、类型与下一结算 / P2 | 563离线/12组聚合浏览器通过，实网未验 | [funding_view.py](../backend/watch_money/funding_view.py)、[FundingPage.tsx](../frontend/src/FundingPage.tsx) | [test_funding_view.py](../tests/test_funding_view.py)、[test_scanner.py](../tests/test_scanner.py) | raw/actual interval/8h、current/predicted/settled、next/倒计时和逐字段有效性；未确认身份仅单行、不同kind不混算矩阵 | 不同报价币/结算时点须明确，未同步就提示；归一化费差不是现金利润，正负收付、实际结算笔数需计算器单独验证 |
| F03 已结算历史与观测采样分离 / P2 | 有限手动历史已接入；真实样本未验 | [funding_history.py](../backend/watch_money/funding_history.py)、[funding_view.py](../backend/watch_money/funding_view.py) | [test_funding_history.py](../tests/test_funding_history.py)、[test_funding_view.py](../tests/test_funding_view.py) | 独立SQLite、raw/source/时间/修订；OKX只接受realizedRate；本地快照is_settled_history=false | 只限已审查服务白名单、显式手动单页；非后台全量回填，cursor不是缺口证明；旧预测/采样不得画成完整结算历史 |
| F04 历史字段时间、范围、过期排除 / P2 | 字段/范围纯函数、mock图表及最终构建通过 | [storage.py](../backend/watch_money/storage.py)、[app.py](../backend/watch_money/app.py)、[FundingPage.tsx](../frontend/src/FundingPage.tsx)、[main.tsx](../frontend/src/main.tsx) | [test_funding_history.py](../tests/test_funding_history.py)、[test_acceptance.py](../tests/test_acceptance.py)；[marketData.test.mjs](../frontend/tests/marketData.test.mjs)、最新12组browser-followup | 已检查marketData测试：carry funding不重复、kind/period/demo分开、过期/future中间价排除与范围分组；root报告纯函数18项通过，12组浏览器含funding chart与排行/矩阵/独立历史mock | 价格事件时间不能代替funding时间；图表区分本地采样与交易所结算，筛选范围外/过期值不能因插值或envelope变新获得“有效”资格 |

## P3：受限原生能力、证据和运行边界

| ID / Priority | Status | Code | Tests | Evidence | Limitations / 下一步 |
| --- | --- | --- | --- | --- | --- |
| O01 全28平台逐项覆盖 / P3 | 清单已维护；全部实网未验 | [COVERAGE.md](COVERAGE.md)、[deployments.json](../backend/watch_money/adapters/deployments.json) | [test_cex.py](../tests/test_cex.py)、[test_dex.py](../tests/test_dex.py) | 下方28条稳定链接区分产品、链、版本、缺字段与接入方式；来源文档/部署hash已记录 | 默认启用不等于支持完整或当前可用；partial/metadata/search_limited与在线证据必须分开，新增平台能力逐条更新 |
| O02 DEX native / route 缺口 / P3 | EVM有限实现；其它未开发，暂不扩大 | [onchain.py](../backend/watch_money/adapters/onchain.py)、[ONCHAIN.md](ONCHAIN.md) | [test_onchain.py](../tests/test_onchain.py) | 本模块66项mock通过；V3池状态、Pancake V2/Aerodrome单池exact-in、block hash/time/fee/decimal/方向与成员检查 | 所有估算executable=false；无编译字节码/交易模拟认证。Curve/Raydium/Orca native、V3规模quoter/tick、多跳/完整池索引仍未完成，排在价差闭环/实网数据之后 |
| O03 mock vs real / 24h / Windows / P3 | 实网受阻；24h和Windows未验 | [VALIDATION.md](VALIDATION.md)、[NETWORK.md](NETWORK.md)、[soak.py](../scripts/soak.py) | [test_acceptance.py](../tests/test_acceptance.py)、[test_followup_acceptance.py](../tests/test_followup_acceptance.py) | [live-probes.json](evidence/live-probes.json) 是ProxyError证据；短soak零真实行情；浏览器mock明确标注 | 官方源码可读不等于行情主机可达；不绕过代理/CA/策略。需真实录制/人工核对及至少24h采集后才可声明对应验收 |
| O04 通知仅模拟 / P3 | 模拟路径已有；外部发送延期 | [notifications.py](../backend/watch_money/notifications.py)、[NOTIFICATIONS.md](NOTIFICATIONS.md) | [test_notifications.py](../tests/test_notifications.py)、[test_acceptance.py](../tests/test_acceptance.py) | 用户指示“先不配置，模拟测试”；HTTP mock/本地通知不是provider receipt | 没有真实飞书/Telegram发送；D11已修且563离线通过，仍不替代真实运行或外部provider验收 |
| O05 无账户/交易与隐私 / P3 | 当前公共只读边界；持续审计 | [cex.py](../backend/watch_money/adapters/cex.py)、[onchain.py](../backend/watch_money/adapters/onchain.py)、[funding_history.py](../backend/watch_money/funding_history.py)、[recording.py](../backend/watch_money/recording.py) | [test_recording.py](../tests/test_recording.py)、[test_onchain.py](../tests/test_onchain.py)、[test_funding_history.py](../tests/test_funding_history.py) | 无交易执行/账户导入；credential-free历史客户、RPC错误脱敏、payload键级脱敏/去URL、默认关闭录制有回归 | RPC URL仍是私有配置；键级脱敏不识别任意字符串内秘密，不得投入私有响应。录制只是公共JSON/CCXT对象，非完整wire/frame或审计级不可篡改日志；不提交凭证 |

## 前端信息组织 / 本批

| ID / Priority | Status | Code | Tests | Evidence | Limitations / 下一步 |
| --- | --- | --- | --- | --- | --- |
| U01 发现 → 详情 → 测算 → 监控 / P1 | 本地实现与浏览器主流程通过 | [main.tsx](../frontend/src/main.tsx)、[ScannerPage.tsx](../frontend/src/ScannerPage.tsx)、[FundingPage.tsx](../frontend/src/FundingPage.tsx) | 后端575、Node25、构建与静态检查通过；导航9、原浏览器6/12/7、资金费专项6组通过 | [本批汇总](evidence/interface-validation.json)、[导航证据](evidence/browser-navigation.json)、[使用流程](INTERFACE.md) | 四项主导航；价差紧凑资产表与资金费矩阵默认，手动测算次级入口；保留完整性和只读边界，不替代真实行情验收 |

## 数据独立复审（本批）

父线程在旧提交 `6dde23e` 报告：OKX 一轮约9分钟、Bitget约23分钟，133秒时1409快照中691过期；7145目录仅3个可靠跨平台资产组。这些是父线程实测报告，不是本环境本批成功采集证据。本环境实际公开探测均为 ProxyError，见 [data-review-live-probes.json](evidence/data-review-live-probes.json)。不扩大stale阈值，不把fixture吞吐当实网性能。

| ID / Priority | Status | Code | Tests | Evidence / 限制 |
| --- | --- | --- | --- | --- |
| R01 全目录新鲜度与真实覆盖 / P0 | 已实现，整合验证通过；实网未验 | adapters/cex.py、adapters/dex.py、collector.py、models.py、main.tsx | test_collection_schedule.py、test_cex_light.py、test_followup_acceptance.py | 已审查原生产品bulk不按100币重复截断请求；独立回退轮转，按整轮起点调度；读时重算新鲜双边盘口与资金费字段，新增目录清旧轮次标记。默认stale仍60秒；单symbol接口限流可能天然超过60秒，必须报告未覆盖，不能宣称全部新鲜 |
| R02 可溯源批量身份核验 / P0 | 本地服务与8组浏览器通过；公开响应实网受阻 | asset_verification.py、identity.py、storage.py、AssetVerification.tsx | test_asset_verification.py、test_identity.py、browser_asset_verification.py | [来源与流程](ASSET_VERIFICATION.md)、[浏览器证据](evidence/browser-asset-verification.json)。Gate/Bitget公开币种合约元数据按原生ID关联，不按ticker；多链/缩放/冲突保留未核实，预览/应用/撤销原子审计。覆盖以活跃市场为母集，人工与公开证据区分；不宣称28平台全币身份已确认 |
| R03 OKX时间与兼容费率分组 / P0 | 已修，独立回归通过 | adapters/cex.py、scanner.py、funding_view.py、fundingMatrix.ts | test_funding_correctness.py、test_data_review_acceptance.py、fundingMatrix.test.mjs | 已安装CCXT parser离线复现timestamp为空而info.ts保留，按官方更新时间取值；不以fundingTime/nextFundingTime冒充源时间。scanner/矩阵共用门槛，current/predicted/settled在各自同类型组比较，单个异类平台不毒化有效pair。官方字段形状样本非本次实网响应 |
| R04 DEX同名报价币净值与可靠排行 / P0 | 漏洞已复现并修复，独立正负例通过 | quote_identity.py、strategies.py、scanner.py、adapters/dex.py | test_data_review_acceptance.py、test_scanner_quote_identity.py、test_strategies.py、test_dex_catalog.py | 原同symbol缺身份证据可返回净值4.8/5.0，现必须chain+contract+来源/时效、route绑定；跨token/ledger换算绑定两端资产，同名不豁免。合法同token与明确FX正例保留。静态proof有独立受限expiry，价格不续期，回放遵守截止时间。部分永续DEX的报价/抵押/结算身份尚未覆盖，保持未知，见[计算边界](CALCULATIONS.md) |

## 全部28平台的覆盖链接

CEX：[Binance](COVERAGE.md#binance)、[OKX](COVERAGE.md#okx)、[Bybit](COVERAGE.md#bybit)、[Bitget](COVERAGE.md#bitget)、[Gate](COVERAGE.md#gate)、[MEXC](COVERAGE.md#mexc)、[KuCoin](COVERAGE.md#kucoin)、[Coinbase](COVERAGE.md#coinbase)、[Kraken](COVERAGE.md#kraken)、[HTX](COVERAGE.md#htx)、[Deribit](COVERAGE.md#deribit)、[Upbit](COVERAGE.md#upbit)。

永续 DEX：[Hyperliquid](COVERAGE.md#hyperliquid)、[Lighter](COVERAGE.md#lighter)、[dYdX](COVERAGE.md#dydx)、[Paradex](COVERAGE.md#paradex)、[Aster](COVERAGE.md#aster)、[edgeX](COVERAGE.md#edgex)、[Variational](COVERAGE.md#variational)、[GMX](COVERAGE.md#gmx)、[Jupiter Perps](COVERAGE.md#jupiter-perps)、[Drift](COVERAGE.md#drift)。

现货 DEX：[Uniswap](COVERAGE.md#uniswap)、[PancakeSwap](COVERAGE.md#pancakeswap)、[Aerodrome](COVERAGE.md#aerodrome)、[Curve](COVERAGE.md#curve)、[Raydium](COVERAGE.md#raydium)、[Orca](COVERAGE.md#orca)。

## 本次审计检查与更新规则

本次未重跑 full suite。读取当前代码/fixture 后，只独立执行以下定向检查：

```bash
.venv/bin/python -m pytest -q \
  tests/test_volume_units.py::test_okx_native_currency_volume_prevents_contract_count_mislabel \
  tests/test_volume_units.py::test_installed_ccxt_okx_parser_exposes_the_reported_scale_error_without_network \
  tests/test_volume_units.py::test_okx_synthetic_book_contracts_convert_once_and_open_interest_remains_contracts \
  tests/test_followup_acceptance.py::test_native_minimum_quantity_is_enforced_in_correct_units \
  tests/test_followup_acceptance.py::test_manual_mapping_cannot_hide_conflicting_native_contracts_from_calculation
# 9 passed in 0.60s — 全部为本地合成/已安装parser测试，无实网请求。
```

最终563 Python / 18 Node、build/Ruff/diffcheck见 [followup-validation.json](evidence/followup-validation.json)；本审计者只额外独立跑上述9项，不重复full。root最终整合浏览器baseline6/followup12/fee7组均通过；旧171或中间总数不替代最终结果。每批至少更新对应 ID：问题是否重现、改动范围、命令/结果、mock或真实来源、尚缺什么；新问题先开项，修复后凭对应定向测试和集成证据升级状态，不能收到即 done。

本批整合验收：677后端、29前端、48组浏览器、构建/Ruff通过；最后HL节流调整另25项通过。详见 [data-review-validation.json](evidence/data-review-validation.json)。成功数据均为合成或本地模拟，公开真实探测仍受代理限制。

## 界面真实性复审（刷新、时效与关注）

- U02 已修复：顶部刷新同时触发价差/资金费查询，保留筛选。`main.tsx`、`ScannerPage.tsx`、`FundingPage.tsx`；真实本地接口浏览器断言两个查询均重发。
- U03 已修复：服务端返回扫描TTL，前端按查询时刻及已核验字段最早失效点保守废止整个缓存排行（不在局部结果上重新宣称最佳）；每秒更新最大年龄，过期撤下可靠路径/标记并停用测算交接，保留查询时原始报价。返回过期结果自动重查，不必开启自动刷新。`scanFreshness.ts`、`scanner.py`，2项纯函数用例及浏览器模拟时间流逝/返回检查通过。
- U04 已核实：current/predicted/settled分组比较在62f1815已修复，本轮前端与后端回归继续通过，不重复实现。
- U05 已修复：我的监控新增简洁的关注市场列表，标明星标是单市场、不是组合监控；可查看资产详情或取消关注，缺目录项保留ID及状态。浏览器完整关注/查找/取消通过。

本批完整677后端、31前端、构建/Ruff通过，导航浏览器12组通过；成功数据为合成本地样本，未增加实网或24小时验收声明。

## 回放身份可知性复审

- R05 已修复：`scanner.py` 和 `funding_view.py` 将请求 `as_of` 传入 `identity_candidates`，避免当前有效但回放时尚未批准的映射进入可靠价差/资金费排名。新增批准前1ms、批准时、批准后1ms三例；同时断言价差、资金费最佳路径和矩阵。计算器原已传递请求时点，保持不变。
- 全目录身份链路补充容量验收：200个非主流合成资产、两个公开响应格式、400个可核对市场可批量导入/预览/应用并形成200组；另有同名不同合约样本不进入跨平台组。没有新增ticker白名单或以同名替代证据。此为合成规模测试，不是实际400市场验证；真实覆盖仍需公开链/合约数据源可达后记录统计，当前首批公共源仅Gate/Bitget。
- 当前持久化模型仍不重建完整历史映射版本；已撤销/过期证据保守阻止回放，不将本次修复描述为完整历史身份库。

## 外部实测反馈后续（身份覆盖与源流）

- R06 全目录可靠盘口：已审查的Hyperliquid/Lighter流不再按watch_assets过滤；collector记录请求/选取市场数、预算截断，禁用广覆盖时回关注范围。Hyperliquid分片l2Book、Lighter单个market_stats/all，仅用官方发布的实际字段/时间，缺字段更新不续旧盘口或资金费。不得把订阅数量当成有效覆盖。
- R07 OKX资金费时效：原生整产品按响应可信源年龄调整重查，最短5秒/批，异常退避优先；不修改60秒TTL。新增最近响应年龄P90/最大/样本/时间及源过期、缺时间、未来时间诊断。HTTP成功不等于字段有效；上游自身老旧仍可能产生覆盖空档。
- R08 官方资产证据扩源：新增KuCoin及限定client的Coinbase Exchange来源；四来源均严格限定现货与原始currency ID，不以CCXT统一ticker回填；旧缺证public映射降级有审计。逐平台显示证据覆盖、scope拒绝、歧义和冲突。永续underlying、包装/跨链单位证据仍不足，不能宣称全资金费身份覆盖已解决。
- R09 Lighter费率语义：官方文档区分当前预估和上次结算字段，但单位/基准仍未充分确定。REST比较费率与WS原值保留协议指标并明确未知，不编造小时费率；HL context没有源时间也不借book事件时间补齐。

外部62f1815真实计数及当前环境失败探测分别见 [LIVE_REVIEW_FOLLOWUP.md](LIVE_REVIEW_FOLLOWUP.md)，不可与本地合成验收混用。

本批最终整合：742后端、31前端、45组浏览器（baseline6/navigation12/followup12/funding6/identity9）全部通过，生产构建/Ruff/diff通过。共享订阅窗口跨重连保留预算；身份变更使隐藏扫描查询失效，返回重新检查。仍无当前环境成功实网或24小时证据。

## 完整盘口空侧复审

- R10 已修复：Hyperliquid完整l2Book/BBO空侧不再省略patch；明确清除bid/ask并保存删除时刻。两侧空也发patch；旧帧和同时间冲突帧不得复活已清除报价，持久化重载仍保留屏障，新快照可恢复。单侧缺失只阻止使用该侧的方向，不捏造对手价。
- Lighter market_stats保留稀疏更新语义：省略字段不清空/不续期，明确null/零/无效价格清除对应侧。没有把order_book增量规则误作全量替换；完整原生深度重建仍未实现。
- 回归包含单侧/两侧空、恢复、乱序/同时间、过期、无源时间和持久化屏障。测试均合成，未新增实网声明。

## 指标覆盖与集成流复审

- R11 已修复：REST/WS protocol_metrics逐字段合并及独立来源/时刻；明确清除、持久化删除屏障、无源时间顺序、派生结算时间同步清除。旧缓存来源不可知时保持未知。独立回归14项通过。
- R12 已修复：+20秒等未来帧不再推进Hyperliquid水位，也不能使collector拒绝随后正常报价；不修改源时间，扫描仍拒绝未来字段。
- R13 已优化、实网根因未定：全目录签名/设置检查至多每秒一次，集合成员检查及本地处理/错误诊断；保留订阅变化响应。347市场隔离CPU和真实merge/SQLite+模拟断线负载证据见 [METRIC_MERGE.md](METRIC_MERGE.md)，不能把优化等同实网断流已解决。
- R14 待外部同步轨迹：OKX 540条同时过期的短窗未确定根因，不扩大TTL或改时间。
- R15 待核验应用：父线程真实Gate/Bitget预览199组/440现货市场，尚未应用，不增加已验证覆盖统计。
- R16 外部限制：父线程Lighter WS restricted jurisdiction code20558；不绕过，本轮未重复探测，不宣称可用。

本批最终774后端、31前端、37组浏览器（baseline6/followup13/navigation12/funding6）及构建/Ruff/diff通过；独立指标回归14项，根任务另补墙钟追上未来帧后仍不得污染排序的回归。全部成功数据为合成/本地验证。

## Bitget 周期回归与查询负载复审

- R17 已修复并本地验证：缺失字段不再借用价格快照接收时间作为乱序屏障。`collector.merge_patch` 只对已有值或显式清除比较先后；目录周期可填补缺失值，保留其真实观测时间、空事件时间和来源，不能覆盖较新的周期。`tests/test_field_merge_ordering.py` 覆盖两种冷启动顺序、旧元数据、独立字段事件时间、持久化重载、明确清除屏障，以及 Bitget 适配器→合并→资金费可比较性。
- R18 继续诊断：父线程 df55e83 三平台约187秒实测 /markets 最慢10.6秒、/scanner多秒，Hyperliquid峰值219/347、两次TimeoutError、结束160/347。代码审查确认同步HTTP端点返回dict后框架仍在事件循环编码JSON，/ws每轮同步构造全目录/快照及delta；采集覆盖汇总也有同步全量扫描。它们是本地待量化阻塞路径，尚不能归因实网断线。
- R14 追加外部证据：父线程OKX有效费率540→0→6→540→540→0。未改60秒阈值或源时间；仍缺同时记录源时间、响应到达、调度延迟、写入耗时的同步实网轨迹。
- R16 保持：Lighter地区限制未绕过、未重试。本批无交易所实网访问，成功用例均为合成数据。

## 响应阻塞与流读取改进

- R18 响应编码已改进、本地验证：三个大HTTP查询在同步工作线程直接编码JSONResponse；浏览器WS视图/delta/JSON构建移至工作线程，成员快照避免并发迭代目录。字段时间、60秒有效性和JSON结构不变。新增线程归属/WS增量回归；相同合成10000市场诊断，markets约1.387→0.451秒，loop最大迟到1179→109ms，并行scanner约2.104→1.003秒、1307→107ms。见COLLECTION与api-blocking-after.json；单次共享机器样本，不是实网验收或完全消除阻塞。
- R13/R14 追加父线程轻查询实网证据：133秒仅轻量status，HL曾347/347，约84秒TimeoutError后152并恢复，最大patch间隔约20秒；OKX50秒后采样均540。不能将大响应认定为唯一根因。24小时、真实身份覆盖、OKX短窗同步轨迹和Lighter地区限制保持开放。
- R19 已实现、等待实网：HL独立reader在订阅前开始读取，应用pong与下游消费/录制解耦；最新快照有界压力合并保留原始receipt、可信源顺序与明确清除，超时细分安全错误类型，原始诊断回调丢弃/未完成计数可见。定向测试及限制见DEX_STREAMING；此修复仅证实本地背压问题，不将父线程真实TimeoutError归为单一根因。
- R20 独立复审补充修复：到达时source_at晚于字段received_at的记录，不能在墙钟追上后自动变为可靠。扫描/资金费共享门槛、测算的新鲜度与字段过期标记均拒绝该顺序；原始时间不改，正常后续观测可恢复。`tests/test_future_at_arrival.py`覆盖盘口、费率、周期、类型及恢复。

## 压力合并中的同时间盘口清除

- R21 已修复并定向验证：容量16、15个清除加2个同时间正价帧能复现清除丢失；在l2Book/BBO、双侧/单侧/相反侧清除的8个失败场景修复后通过。同源事件时间时逐侧保留清除优先，未清除侧仍使用最后报价；内部压缩状态保存每侧原始接收时间，不改raw输入。较早帧不能恢复，较新事件能恢复。共享盘口解析使无效价格/数量的清除语义一致，缓冲容量不变。`tests/test_pressure_clear_barrier.py` 12项包含300帧多轮压缩与直接逐条合并的值/来源/时间对照；与已有背压/空侧回归共29项通过。
- R17 父线程补充实网确认：4372805之后独立验证Bitget周期缺失回归已修复。该外部结果仅确认周期修复，不替代其他平台或24小时验收。

## 总览可信状态与重查询负载

- R22 已实现、本地完整验收通过：enrich的stale按显示价格字段计算，新增price_valid/price_fresh/price_source_known/price_fields；结构valid不用于冒充所有字段可用。未来到达时间、无价格、未知源时间或过期价格不计为新鲜；字段级资金费/成交量状态保留。前端总览绿点、有效行情计数和详情价格状态统一使用按字段判断且随本地时钟老化的逻辑，不能被新消息envelope续期；价格范围也拒绝source晚于原receipt。
- R18/R19 追加父线程4372805实网反例：Bitget已921/928可比较，但markets曾27.16秒，funding/scanner约12秒；随后OKX0/540、HL约98秒ApplicationPongTimeout，覆盖320→21→12。此前合成优化不是实网稳定性验收；轻负载新对照尚待反馈。继续分析CPU/loop/查询并发，不预设唯一原因。
- R23 已实现、本地验收通过，实网稳定性待验证：重HTTP查询/浏览器WS视图共用有界单worker准入；超载明确503，旧数据保持原时间，WS忙时跳帧不排队。安全诊断包括排队/构建/编码耗时、线程与进程CPU、响应大小、本地loop延迟，供下一次实网对照。详见本批后续验证记录；Lighter地区限制及24小时、身份覆盖继续开放。
- R24 历史复现（现已修复，见下方验收记录）：修改启用平台后，较旧WS缓存可短暂覆盖刚返回的HTTP目录/价格视图，下一目标WS帧会恢复；未修改source/receipt。见browser-overview-freshness.json的historical_regressions。当前浏览器截图等目标WS帧再保存，不能把该等待当作产品修复。

### R24 验收记录（2026-11-04）

- 状态：实现与本地回归完成；不代表真实交易所运行稳定性验收。
- 实现：`collector.py` 在视图构建开始分配服务实例/单调序号，实际筛选设置作为上下文；`market_stream.py` 标明完整帧及增量基线；`app.py` 及时清理断开的订阅。`marketView.ts` 与 `main.tsx` 统一 HTTP/WS 排序，平台切换取消旧 HTTP、清空旧范围、关闭旧 WS；错误增量要求完整重同步，服务重启使用新实例且旧实例不能复活。
- 页面：`queryGeneration.ts`、`ScannerPage.tsx`、`FundingPage.tsx` 隔离筛选、离开/返回、失败重试的响应与错误；返回会重新请求，缓存明确标记且禁止旧组合测算。保留用户筛选、排序、展开和滚动。
- 测试：`test_market_view_version.py`、`test_market_stream.py`、`test_query_work.py` 覆盖构建开始排序、上下文、断开连接及工作线程容量；前端新增 10 项时序测试，完整 44 项通过。`browser_market_view_order.py` 5 组通过，旧真实本地 WS 完整帧/增量晚到、HTTP 晚到、范围切换、错误基线重连、返回 503 后重试均覆盖；导航 12 组、新鲜度 5 组及其他本轮浏览器 25 组通过（合计 47）。
- 证据：`docs/evidence/browser-market-view-order.json`、`browser-navigation.json`、`browser-overview-freshness.json` 与截图；行情为本地合成，未访问账户或执行交易。原始 source/receipt 时间不变，未放宽有效性门槛。实网超时、资金费空窗、24 小时和其他未完成清单仍开放。

- 最终完整验证：851 项后端测试（31.02 秒）、44 项前端测试、生产构建、Ruff 与 diff 检查通过。后端仅一条上游 TestClient 弃用警告。

### R25 持续时效与停顿归因（2026-11-04，开放）

- 独立 e4405d6 实网证据：Bitget 921/928 可比较；重查询时 HL 347→0、OKX 540→0。status-only 无重查询仍出现 21.64 秒 loop 延迟、37.97 秒 status 与 HL ApplicationPongTimeout；执行服务亦曾断连。不能仅由时延认定程序或环境为唯一根因。
- 已实现取证：`stall_probe.py` 可选独立线程/循环心跳对照，限频、限容量栈与固定资源计数；由 `WATCH_MONEY_STALL_PROBE=1` 开启，`/api/query-work` 获取。默认关闭、不采集局部值或凭证。可控循环阻塞、共同调度空窗、关闭清理、默认禁用及隐私边界见 `test_stall_probe.py`。
- 待验收：按 COLLECTION.md 两轮对照，在真实卡顿时收集事件、资源增量、客户端采样空窗和执行服务时间线；尚未取得根因证据，HL/OKX 持续时效未通过。身份映射真实覆盖、原生 DEX 和 24 小时运行仍按原清单开放；不以本次诊断实现标记完成，也不重试受限 Lighter。

### R25 抓栈定位后的热路径修复（2026-11-04，实网效果待验收）

- 新证据：父线程 5f7f562 status-only 的 6 次抓栈中 3 次位于关闭录制仍执行 record_payload→settings→SQLite；其余涉及覆盖统计、健康检查完整序列化与关闭通知仍读取设置。多个 10–18 秒段进程 CPU 增量接近墙钟时间，至少这些段存在计算，不能统一归因于主机暂停；HL/OKX 时效仍有缺口。
- 已实现：Storage 按数据库路径共享已提交设置快照，写入成功才发布，读副本不等待 SQLite 写锁；录制关闭直接返回，开启仅配置变化才 configure，排队上限 16，取消调用后仍持有未完成写入，停机等待完成。queued 作业复核开关/平台/演示状态，已开始单次写入可完成。
- 已实现：覆盖分类每市场最多一个缓存条目，以模型替换、设置、时钟回退和精确字段到期时刻失效；定向更新不重复分类其他平台。内部覆盖统计单任务后台执行，取消等待工作结束。健康扫描不再 enrich 全对象序列化，关闭通知跳过扫描；开启时分段让出循环并复核设置，防止旧范围通知。
- 测试：`test_settings_hotpath.py` 覆盖关闭功能零 SQLite 访问、同进程多实例一致、失败提交不发布、写锁期间循环继续、持久化重启、启停和配置切换、队列有界与取消停机；`test_coverage_hotpath.py` 覆盖计数一致、精确到期、来源未来时间、替换/设置变更/回退、关闭通知无序列化、后台执行取消及中途配置切换。
- 本地同负载证据：5000 设置读取 SQLite 路径 0.586s、缓存副本 0.082s；7000 市场首次分类 350.89ms，未变化重复统计 10.25–10.64ms、重新分类 0，后台冷计算心跳最大延迟 10.345ms。`settings-hotpath-synthetic.json` 与 `coverage-hotpath-synthetic.json`；后者可运行 `scripts/benchmark_coverage_hotpath.py` 重现。均为合成样本，不能宣称实网超时或资金费空窗解决。
- 边界：单采集进程部署，跨进程直接改数据库不会自动刷新设置，迁移/恢复后重启；覆盖缓存依赖现有不可变替换方式，不允许原地修改行情模型。真实稳定性继续由相同公开平台/负载/开关的独立对照验收。Lighter 地区限制、身份真实覆盖、24 小时及既有其他事项继续开放。

- 最终验证：866 项完整后端测试（31.28 秒）、5 组 R24 平台切换浏览器检查、Ruff 和 diff 检查通过。仅一条上游 TestClient 弃用警告；无新增实网验收结论。

### R26 健康扫描异步交错错误过期提醒（2026-11-04）

- 复现：watch_health 保存旧对象后 yield，期间采集替换成新鲜盘口，恢复仍按旧对象触发 data_stale。
- 修复：扫描只保存市场 ID；每次恢复（包括前一条提醒 await 后）重新读取当前快照及设置，撤销/退役市场跳过。发送前再次检查相同对象版本、当前配置和实际字段有效期；无 await 插入检查与当前 NotificationService 无悬挂的入队过程之间。替换成仍然过期的行情继续提醒，没有放宽过期阈值或忽略真实过期。
- 验证：test_coverage_hotpath 新增 7 项用真实协程调度交错测试刷新、删除快照、移除市场、退役、仍然过期和发送前版本替换；真实本地通知服务检查后续真实到期可提醒、重复检查不重复通知，并覆盖扫描取消后不入队。原健康规则测试补齐有效目录条目，孤立或已撤销市场不再触发过期提醒。32 项相关测试通过；无外部通知、凭证或实网访问。
- 范围：阻止本次扫描/入队前失效的提醒；本次未新增已入队通知的市场状态撤回协议。真实交易所持续时效及原有未完成事项仍开放。

- 最终验证：873 项完整后端测试（31.23 秒）、Ruff/diff 通过；仅一条上游 TestClient 弃用警告。

### R27 批量行情解析的事件循环占用（2026-11-04，实网效果待验收）

- 父线程匹配实网对照：413d028 status-only HL 至 125 秒保持 347/347、无重连；最大循环延迟约 19.49→2.46 秒，关闭录制 3000 次回调的 SQLite 连接 3000→0。但组合负载约 60 秒仍发生 ApplicationPongTimeout。新探针样本为 OKX/Bitget CCXT ticker 解析/精度与 HL JSON；部分 11–12 秒区间进程 CPU 接近墙钟。原 SQLite/coverage/health 热点未再出现在这些采样中；不据此宣称所有热点或断流解决。
- 实现：审阅并固定 CCXT 4.5.91，对 OKX/Bitget 普通批量 ticker 保持原异步 HTTP/限流器，仅将同步 parse_tickers 移到每适配器一个工作线程（最多一个等待者）；捕获独立解析器视图和批次市场元数据，目录替换不改变在途单位解释；取消与关闭等待 CPU 工作完成，原始来源时间及解析前接收时间保留。其他版本/特殊产品回退上游，diagnostics 显式计数，不静默声称已优化。见 `bulk_tickers.py`、`test_bulk_ticker_parser.py`。
- 实现：HL 大于等于 8KiB 帧单连接一个解码工作线程，catalog 最多五连接；小帧直接解析、每 64 帧让出循环。2MiB 帧上限、有界原队列和清除屏障保留。recv 后即记录接收时间，排队/CPU/诊断回调不能刷新时间。见 `test_native_decode_worker.py`，原有 34 项 DEX 流/背压回归通过。小帧逐条进线程实验导致心跳回退，因此未采用该方案。
- 同负载合成：真实 CCXT 解析 7000 市场，所有字段结果一致；OKX 最大循环延迟 157.365→5.316ms，Bitget179.229→5.386ms。128 大 JSON 帧199.869→4.079ms，但总耗时0.393→0.425s。见 `bulk-ticker-parser-synthetic.json`、`native-decode-synthetic.json` 及对应 benchmark 脚本；不是实网验收或 CPU 消失证明。
- OKX 官方语义：ts 为数据返回时间、fundingTime 为结算时间；资金费 WS 文档推送30–90秒，不保证REST源年龄小于60秒。详见 COLLECTION.md 官方链接和取证步骤。新回归验证60秒边界、61/72秒被排除、成功HTTP更近接收时间不恢复旧源有效性；没有伪造时间或放宽门槛。资金费 HTTP/CCXT 解析分段尚未完全解耦，不能将观测年龄全称为上游延迟。
- 仍开放：真实同负载超时/资金费空窗、HTTP JSON 与小帧等剩余循环 CPU/GIL 争用、身份覆盖和24小时验收。仅公开接口/合成数据，无账户或交易操作，不重试受限Lighter。

- 最终验证：897 项完整后端（37.00秒）通过，最后新增时间回归后的19项解析专项通过；Ruff/diff通过。上游TestClient弃用警告一条，无新增实网验收结论。

### R28 重复取消不能释放仍运行的解析线程（2026-11-04）

- 复现：cce59b4 首次取消后 cleanup 直接 await executor future；第二次取消可取消 asyncio 包装 future，提前清空 pending/解锁，close 返回时线程仍在运行。HL 解码器有相同模式。
- 修复：`async_work.finish_owned` 在每次等待中都 shield 真正拥有的 future，记录并延迟传播调用者取消，直到工作实际完成；工作异常在取消胜出时仍取回。CEX 解析槽位/计数、HL 解码锁/future 均保持到完成。close 由单个共享内部任务拥有，重复或并发 close 不能取消内部清理；CEX 客户端、HL 子连接和 socket 清理完成后才传播取消。关闭进行中禁止重开，完整关闭后允许新会话，流 finally 始终释放 running 标记。
- 新回归：超时先触发后连续三次取消；阻塞工作线程仍持槽位；一活动一等待容量与第三项拒绝；重复取消 close 及并发 close；工作完成后报错；CEX 客户端关闭；HL 单连接/catalog 子连接关闭和完成后重开。27 项解析/解码专项通过，无网络和账户访问。
- 语义：取消仍会传播，不会伪装成功；Python 线程不能强制中断，因此若线程不结束，拥有者和 shutdown 保持等待，超时取消也可能超过原超时时限。不能通过丢弃 future 或释放容量假装任务已结束。此修复不代表实网 HL 超时或 OKX 时效空窗已解决。

- 最终完整验证：903 项后端测试（44.65秒）、Ruff/diff通过，仅一条上游TestClient弃用警告。

### R29 OKX 观测语义与直接/批量取证（2026-11-04，实网配对待验收）

- 最新父线程 cce59b4 实测：三平台约191秒，HL在29–178秒347/347、零重连；OKX/Bitget真实走4.5.91优化路径无回退、源/接收时间与合约量正确。这是短时改善，不能当长期稳定。OKX控制仍两次0/540，响应源年龄p90=70.663秒无重试，组合亦短暂0。
- 官方核查：ts为数据返回时间，按分钟计算与WS30–90秒发布不等于REST<60秒承诺；本次不能认定直接/ANY差异或上游缓存为根因。具体来源与语义在 COLLECTION.md。
- 实现：新增固定公开端点的 `probe_okx_funding_timing.py`，有界并发/采样、记录收包前后时刻、解码耗时、服务器时钟区间、原始ts及直接/ANY配对和相邻变化；失败周期停止，无认证/私密响应记录。仅MockTransport回归与示例证据执行，本环境交易所网络受限，真实配对仍待父线程运行。
- 状态：传输状态与 `funding_observation_state` 分离；统计时刻显式记录。源时间超限显示“已收到但源时间超过比较门槛”，不误报断连，也不把旧费率送入可靠排行；字段符合门槛不意味着身份等其他比较条件已通过。保持60秒现有门槛与全部原始时间。

### R30 身份审核包与真实覆盖（2026-11-04，真实覆盖仍开放）

- 新增只读分页 `/api/assets/verification/review-packet`，每页最多200组；输出准确原生ID、链/地址、单位、目录指纹、完整来源证据和冲突。候选ready数与实际跨平台身份门槛组数分列，候选不计已确认覆盖。操作说明见 IDENTITY_REVIEW.md。
- 审核包与本页token来自同一组已捕获目录/证据/时点；原子apply仍复核当前状态。强制目录及证据在读取期间改变的回归验证旧展示不能批准未展示新状态。61项身份相关测试通过。
- 本地只读核查目录0、证据0、映射0；无法审核此前父线程199组/440现货候选，没有执行真实映射批准。父线程可靠跨平台仍主要BTC/ETH/SOL，不能因新增工具宣称覆盖扩大。应在持有真实新鲜证据的环境导出审核包，逐项核对后应用并记录实际组数；没有仅凭ticker合并，没有将现货注册表证据推广至永续。
- 本批远端推送CI读取：CLI查询GitHub Actions返回Forbidden；连接器仅列PR触发记录且未返回运行，因此远端CI结果尚未确认，不假称通过。本地检查与远端结果分开记录。

- 本批最终本地验证：911项后端（39.39秒）、44项前端、6组浏览器、生产构建及Ruff/diff通过；一条上游TestClient弃用警告。无新增真实接口/身份批准验收。

### R31 OKX 探针单点超时与有限继续（2026-11-04）

- 父线程真实配对13对成功ts完全相同；一轮ANY799条均超60秒（p90 65.572秒），三大币直取约64.6秒。没有证据支持自动直取替换；过龄根因仍未知。旧探针第五轮ETH/SOL ReadTimeout早停。
- 实现：逐请求失败也记录起止/排队/总耗时、超时类别/阶段与部分字节；不伪造完整接收时间。单次超时保留同轮其他结果并继续后续轮次，不在当前轮重试。全局0.25秒起始间隔、2并发、最多60轮/每轮7请求；失败退避最多60秒，连续3轮全失败终止，权限/限流/API结构等非恢复响应仍停。
- 持久化与汇总：每轮原子检查点、完成/提前停止标志与原因；逐流成功/超时/错误计数与耗时、过龄响应、配对同/不同/不可用ts计数。UI明确请求状态与源时间状态，源过龄突出显示但不修改60秒门槛。
- 测试：新增单点超时恢复、并发上限、旧源不续期、检查点、全失败退避终止、总超时、部分响应超时和请求节奏回归。MockTransport证据 `okx-funding-probe-timeout-synthetic.json`；尚未执行新版实网长对照，身份覆盖及原有未完成事项保持开放。

- 最终本地验证：915项完整后端（39.66秒）、44项前端、6组浏览器、生产构建、Ruff/diff通过。一条上游TestClient弃用警告；不把模拟继续采样当作新版实网验收。

### R32 官方证据批量核验与只读隔离演练（2026-11-04，真实覆盖待验收）

- 父线程独立实网确认 Gate/Bitget 199 个候选组、440 个现货市场，但实际批准覆盖仍仅 3 组。候选未应用，本地数据库目录/证据为空，不将工具完成或合成夹具计入真实覆盖。
- 新增 `identity_batch.py` 明确版本策略：多官方平台普通现货、精确原生币种作用域、单一同链合约、单位及来源摘要完整性、最近成功收取与有效期、冲突及既有映射保护。GET 计划只读；POST 精确 token 显式应用，事务内复核并记录策略/token审计；原证据撤销流程保留。人工/模糊/缩放/永续证据不自动批准，不用同 ticker 归集。
- `scripts/rehearse_identity_batch.py` 对源库使用 SQLite mode=ro/query_only；白名单复制身份元数据到临时库，排除私密配置、通知、行情、人工备注/URL。临时应用→覆盖统计→撤销→覆盖恢复，有限导入与原子报告；可仅在副本刷新 Gate/Bitget 官方公共资料。实际原库不应用映射。
- 官方字段核查与命令见 IDENTITY_REVIEW.md。Gate 官方文档明确链名/token地址；Bitget 文档旧路径重定向、当前 catalog 网页工具不可读。当前云环境无交易所主机网络许可，未绕过或重复请求 Lighter。KuCoin/Coinbase 来源未由此轮实际验证；来源状态必须按实际收集报告。
- `identity-batch-empty-local.json` 是真实本地空目录演练，明确无真实数据可供覆盖验收。父线程持有真实候选环境需执行同一只读流程，核对实际合格/阻断/撤销结果后再应用真实映射。盘口新鲜度、资金费空窗、24 小时与通知/DEX等其余开放事项维持原状。

- 最终本地验证：949 项完整后端（40.30秒）、Ruff/diff通过；34项新增批量策略/隔离演练测试。合成覆盖0→1→0、同名异链拒绝、源逻辑元数据不变，见 identity-batch-synthetic.json。未改前端，本批未重跑浏览器；一条上游TestClient弃用警告，无新增实网验收结论。

### R33 Gate 真实报价时间与全现货盘口流（2026-11-04，实网流待验收）

- 新实测证据：父线程真实隔离身份覆盖3/47→202/487→3/47，源库哈希不变，R32真实演练已取得外部证据；不是生产批准。价格方向368→866→368但可靠方向始终0，Gate3257条REST价格patch无source_at。
- 官方核查：REST spot ticker无盘口事件时钟，ETF时间、HTTP时间及收包时间不能填补。WS spot.book_ticker.result.t是盘口更新时间（ms），u是更新ID；REST orderbook的current为响应生成、update为盘口改变。完整来源与边界见GATE_QUOTES.md；CCXT4.5.91原码核对吻合。
- 实现：Gate使用独立公开现货BBO流，准确nativeID、全目录现货选择、5000市场预算、一连接/50对每批/0.25秒间隔；不是官方吞吐承诺。unsupported产品及预算导致selected小于requested时明确truncated。原始源时间和收包时间独立，缺时间、未来、乱序拒绝，清除屏障和有界压力合并保留；重复帧同样让出事件循环。独立心跳/接收与有界观察队列，关闭等待实际子任务结束。
- 集成：无时间Gate REST买卖价不覆盖带时钟WS买卖价或清除屏障，也不刷新原始字段年龄；REST其他字段继续更新。没有WS时REST值仍可见但不可可靠排序。合成双平台验证未知时间0方向→有效时间2方向→过期0方向；身份未批准仍0。
- 限制：当前云环境网络策略未包含Gate，未进行真实WS请求或修改账户/环境设置；现货流订阅容量、真实新鲜覆盖、限流/重连和持续稳定需独立实网验证。永续盘口、OKX资金费空窗、24小时及其余开放项未宣称解决。无交易功能。

- 最终验证：983项完整后端（40.30秒）、Ruff/diff通过；新增34项Gate/集成回归，合成方向0→2→2→0见gate-quote-time-synthetic.json。一条上游TestClient弃用警告；未改前端，未重跑浏览器，远端CI未确认。

### R34 Gate冷门市场按需快照、订阅与字段诊断（2026-11-04）

- 父线程独立实网c53df7d：44批2186现货、180秒122560帧，无拒绝/重连；47组原始/patch核对通过，15376次REST保护无失败。隔离身份批准使固定可靠方向32→378→撤销32；最后1671有时间盘口、1463新鲜，仍不完整。资金费源时间缺失。这是外部真实短时证据，不是本云环境复测、生产批准或长期验收。
- 现货按需深度：原详情“获取盘口”接入Gate公开order_book，2并发、0.25秒起始间隔/同市场30秒冷却、准确nativeID/20档；不新增全目录循环。使用原生current作为明确标记的交易所快照生成时点，update另列为盘口最后改变；不是本地收包代替源时间。缺current只用原生update并保持年龄，两者皆缺不更新监控。未来、矛盾、无效深度拒绝；目录变更或较新WS已经胜出时不虚报已更新。详情同时显示两个时间、未知与预算错误。
- 订阅诊断：query-work.source_streams.gate按连接generation区分selected/sent/对应id的acknowledged/实际观察，不能用匿名或重复确认凑覆盖。固定大小帧/拒绝原因/缓冲计数，累计源时间范围不等于当前新鲜度。status与设置诊断页新增缺双边盘口、源时间未知/过期/未来及核查时刻，保留原字段门槛与缓存失效，不延长TTL。
- 永续调查：官方futures.book_ticker有独立t、settle命名空间、字符串u及空价格/零数量规则；新增纯解析器gate_futures.py和26项测试，尚未启用永续实时流。资金费无独立更新时钟，不用next_apply/config_change或WS外层响应时间冒充费率时间，历史结算t/r与预估保持区分。
- 详细来源/限制见GATE_QUOTES.md。新路径均合成验证，当前Gate主机不在云网络允许列表；不绕过、不重复请求受限Lighter、不修改用户数据或账户。新按需快照/诊断的真实覆盖、限流、永续流接入、资金费时效与24小时继续开放。

- 最终本地验证：1034项后端（41.15秒）、44项前端、6项浏览器检查、生产构建、Ruff/diff通过；上游TestClient弃用警告一条。浏览器证据browser-gate-snapshot.json；新路径实网及远端CI未确认。

### R35 Gate永续公开盘口实时集成（2026-11-04，实网待复测）

- 统一GateStreamAdapter，现货与BTC/USDT/USD1永续最多4个隔离连接，总8000/每命名空间5000预算。严格active/client/nativeID/swap/settle，歧义nativeID拒绝；选择器与截断状态一致。永续请求公开小数数量头、按原始t/string-u/空侧规则解析，价格不按合约乘数转换。
- 复用现货有界收包/清除/乱序/取消，外层合并也保留清除屏障；独立5–300秒退避、健康连接不重启；401/403/451本会话停重试。跨连接及重连数据不借用身份/时间；Gate无时间REST不覆盖永续BBO，旧清除持久化后仍不能复活。资金费未获得独立源时间仍未知。
- 诊断包含命名空间与partial/blocked/重试状态，录制标签带settle。新增scripts/probe_gate_streams.py：显式--live、5–600秒、无用户DB/配置修改、固定公开范围、限量样本和关闭前后诊断，失败不输出私密响应文本。复测步骤见GATE_QUOTES.md。
- 回归覆盖四连接同名合约、线性/反向及不同乘数/小数数量、未来和缺时间、同时间清除、缓冲压力、重连/访问拒绝/重复取消、native歧义、资金费时钟污染，以及公开探针截断/失败/关闭边界。gate-mixed-stream-synthetic.json明确标记模拟；当前环境无Gate网络许可，没有执行实网，先前现货短时证据不能算永续验收。24小时及其余原计划开放项保持不变。

- 最终本地验证：1059项完整后端（40.95秒）、最后探针覆盖字段补充后的3项专项及Ruff/diff通过；上游TestClient弃用警告一条。本批未改前端，未重跑浏览器；实网永续和远端CI未确认。

### R36 Gate可选error确认与观察者测试时序（2026-11-04）

- 父线程实测：3次Gate按需快照原生current/update核对成功、3次本地429无额外请求、1次BTC超时，记录在GATE_QUOTES.md；不隐去失败。
- 复现诊断缺陷：44个匹配id的成功现货订阅确认省略error，旧代码判未关联。修复现货/永续统一允许缺省或null，但仍校验已发送精确id与success结果，非null错误/异常结果/匿名或未知id不批准，重复不累计。44条形状回归使用报告重建的合成ID，非原始捕获文件。
- 观察者测试：原sleep后基线到close之间仍可处理pong，导致212/211差1；增加明确接收屏障及额外pong分支，保留关闭+65和重复关闭不增的精确断言、队列上限和pong畅通要求，生产计数未改。
- 本批无真实网络请求、未改前端；持续覆盖、Gate永续实测、资金费独立时钟与24小时等原验收继续开放。

- 最终验证：1074项完整后端（41.57秒）、20轮/40例确定性交错重复验证、CI范围Ruff E4/E7/E9/F及diff通过；默认全规则Ruff的既有规范问题未纳入本批。上游TestClient弃用警告一条，远端CI未确认。

### R37 短时混合流证据与持续覆盖采样（2026-11-04）

- 父线程独立6da269a实测180秒：无拒绝/重连，现货1579/2186、USDT永续911/1070、BTC永续1/1最后时点新鲜，USD1未发现合约故未测；65条原始/patch命名空间、价格、源时间一致（包含分数数量）。摘要保存在evidence/gate-mixed-parent-live-summary.json，明确不是本地抓包或24小时验收；5d33823确认修复仍待独立复验。
- 全市场验收缺口：探针旧版只有最后覆盖，无法显示中途时效下降。现在每15秒独立于消息到达采样，最多122点（含启动/结束），不取消沉默接收、不增加交易所请求；从原始字段时间重新判定，区分未收到、缺盘口、不可信时间、过期及其他无效。零市场范围保留，确认数不充当新鲜数。
- 9项离线探针回归通过，含可控时钟的未收到→新鲜→静默过期、未知/未来/交叉盘口、截断、异常清理、显式live门槛及参数边界；CI范围Ruff/diff通过。本批无生产采集逻辑或前端更改，未重复全量/浏览器；采样点之间空窗仍未知。身份真实批准、费率独立更新时间、全平台与24小时验收保持开放。

### R38 CEX盘点与Bitget/Gate公开结算历史（2026-11-04）

- COVERAGE逐项盘点12个CEX，纠正旧文件“全部未有真实证据”的过时概括，同时明确父线程短时实测不是完整验收。四个现货合约证据源仍不能授权永续；未新增生产身份批准。HTX所查公共链资料缺合约地址，MEXC Wallet鉴权端点不调用；其余未审阅源保留待核查，不能按名称猜测。
- funding_history.py扩展Bitget Classic/Gate公共历史。分页禁用、最多100条、一刷新锁/限时；Gate精确settle/nativeID/t秒，Bitget精确native symbol/fundingTime毫秒、uta=false/pageNo=1。原始与统一时间矛盾、缺原始费率、错币/未来拒绝；不将当前目录周期填到历史。小数比例/收付方向与原始时间单位可追溯。
- Bitget latest-page endpoint不接收历史since窗口，明确本地过滤与未知遗漏，next_since为null。历史数据不补实时费率时钟，Gate当前费率独立更新时间仍未知；前端原历史入口复用。
- 34项历史专项和1089项全量后端（41.44秒）通过；含安装CCXT的实际公开方法与替身HTTP响应，未使用真实行情网络；CI范围Ruff/diff通过，一条上游TestClient弃用警告。没有前端修改/浏览器重跑；新历史实网、全身份、24小时和远端CI仍开放。父线程固定提交运行不受本批变更影响，不合并测试证据。

### R39 Deribit永续批量摘要与清除语义（2026-11-04）

- 选择R38中明确未接的Deribit整组报价，官方currency参数对衍生品按结算币匹配；现有CCXT有symbols基础币限制。现改perpetual按settle计划、fetch_tickers(None, code=settle, kind=future)，150个不同base同settle一批；跨settle隔离，spot/未知settle不宣称整组覆盖，复用有界并发及缺市场/异常诊断。
- 新解析严格目录swap/settle/nativeID和原始base/quote，不接受仅同ticker或其它返回合约；creation_timestamp是唯一摘要源时钟，未知/过期/未来继续被可靠排行门槛排除。原生null侧输出明确清除标记，集成回归验证旧帧/同时间值不能恢复、较新快照可以恢复；volume保持base单位。
- 摘要的current_funding/funding_8h不注入标准资金费或已结算历史；原独立端点与摘要语义不可混用，保持后续单独审阅。没有增加跨平台身份批准，没有账户/交易/地域替代；本云环境未执行实网、未改父线程固定提交的24小时运行。
- 新增test_deribit_summary.py共18项：结算币分组、空侧、volume、源时间、ID/字段冲突、安装CCXT公开路由替身、额外市场过滤、清除交错和恢复；18项专项通过。完整套件结果见本节末尾与VALIDATION。

- 最终验证：1107项完整后端（51.62秒）、18项专项、CI范围Ruff/diff通过；上游TestClient弃用警告一条。未改前端，未重跑浏览器；实网与远端CI未确认。

### R40 HTX币本位公开结算历史（2026-11-04）

- 官方历史原始realized_rate与funding_rate含义不同；安装CCXT统一fundingRate使用后者。新增仅逆向币本位严格读取realized_rate，缺失/非有限不补值；核验contract_code/base/fee_asset和funding_time毫秒，不把外层响应ts当结算时间。
- 限swap/inverse且settle=base，最多50条page_index=1/paginate=false，since仅本地过滤，next_since=null；原始名义/实际率、字段名和时间单位保留。未知历史周期不假定8h，公共费率不证明用户账户实际支付。
- HTX线性已安装CCXT使用v5新接口、语义未审阅，明确501；旧USDT官方文档本次读取超时。Kraken公开历史与连续资金费/绝对金额模型已定位官方来源但未接，不能冒充离散settled记录。详细来源见COVERAGE R40。
- 46项历史专项通过，含新增12项原始费率偏差/空值、单位、原生身份、时间、公开路由、线性与凭证拒绝；安装CCXT方法替身未发生网络访问。未改独立24小时测试或已有历史独立核验环境，未改前端、账户、交易或生产映射；全量结果见本节末尾。

- 最终验证：1119项全量后端（43.38秒）、46项历史专项、CI范围Ruff/diff通过；上游TestClient弃用警告一条。未改前端，未重跑浏览器；新路径实网与远端CI未确认。

### R41 Hyperliquid原生公开历史（2026-11-04）

- 新增fundingHistory分支，严格完整native coin/目录ID、已发现namespace及collateral；请求后重检scope。时间必须原生毫秒且在有限窗口，原始小数率保留、官方hourly周期1h；premium和账户资金记录不使用。
- 原刷新锁/总超时；单请求最多500行和500小时，默认有限回看，排序后按limit保存/游标，不跳过未保存有效行。403/451拒绝无重试，错误脱敏；非list/超上限拒绝。缓存、去重和修订沿用已有表，不回写实时快照或身份。
- 12项新MockTransport测试覆盖普通/HIP-3、坏时间/币种/率、单页游标、403/451/500、超大响应、目录变化、超时取消；58项联合历史专项通过。未执行本环境实网。FUNDING_VIEW给出独立检出+临时库公开实测步骤，不读用户data、不触碰固定提交24小时环境。完整历史/实网覆盖和生产身份批准仍开放。

- 最终验证：1131项完整后端（42.08秒）、58项联合历史专项、CI范围Ruff/diff通过；上游TestClient弃用警告一条。未改前端/重跑浏览器，实网与远端CI未确认。

### R42 接收时间提前固定与独立停止证据（2026-11-04）

- 独立实测4d89f2e：144永续price/time/base volume一致、排除64交割、51项专项通过。报告的+10ms源时间/40ms录制延迟是合成复现，不写为实网故障。独立24h尝试13分50秒后本地代理403触发守卫停止，根因未定、未完成；不重试/绕过，不改运行环境。摘要见evidence/deribit-replay-and-guard-stop.json。
- BulkTickerParser两种通用回退统一在CCXT方法返回后封装receipt；消费者在观察者前提取receipt。single轻量与详细fetch_ticker也提前取时，详细put保留字段receipt防止后续funding/OI延后价格时钟。优化原生OKX/Bitget后台解析前时刻保持不变。资金费bulk/single及interval元数据检查已有观察者前时间，无需重写。
- 通用CCXT内部解码/解析未暴露原始网络到达时刻，明确方法返回边界，不夸大为所有协议wire捕获；未更改DEX网络层或全局client。新10项确定性测试覆盖未来/过去、三路径观察者取消/下一轮及详细多字段交错；独立实网回验仍待完成。

- 最终验证：1141项全量后端（46.43秒）、50项首次专项及49项最终调度/时间/解析专项、CI范围Ruff/diff通过。新增模块提前取时时，调度fixture补同一冻结时钟；保持原新鲜计数断言。上游TestClient弃用警告一条，无前端变更/浏览器重跑；新修复实网和远端CI未确认。

### R43 dYdX公开历史与长测归因补充（2026-11-04）

- 补齐dYdX v4 indexer历史：原生ticker/chain/settle目录门槛，ISO必须有时区，height原文/小数rate保留，未来/错币/非有限拒绝；目录变化不入库。一页100条、since本地过滤、不暴露假向前游标；周期因历史治理信息不足保留null。原生高度不是链验证证明。
- 新14项MockTransport回归，联合72项历史测试通过：缓存去重、单位与时区、错误身份/高度/未来、范围过滤、403/451无重试、目录变化和超大响应；全量结果见下。无实际数据主机请求、账户/生产映射或前端变更。
- 长测证据更新：OKX公共ticker WS通过本地代理建连返回403，原记录仅含代理地址，无法确认代理/平台/基础设施责任；51.7秒计时延迟与其他来源覆盖下降同时保留。24h在830秒停止、未完成，不重试/绕过。需要用户环境/支持方进一步核查验收条件，当前未执行任何环境设置变更。

- 最终验证：1155项完整后端（42.52秒）、72项联合历史专项、CI范围Ruff/diff通过；上游TestClient弃用警告一条。本批无前端/浏览器变更，新路径实网与远端CI未确认。

### R44 历史请求与目录更新一致性（2026-11-04）

- 修复CEX历史请求排队及网络等待期间沿用旧目录的缺口。入口复制Instrument，取得刷新锁后重检；CEX在取得限流容量后、响应返回后重检目录、适配器、client ID及原生合约范围（id/symbol/base/quote/settle/swap/linear/inverse/contractSize/active）。变更返回409，不解析保存该响应；无关info更新不误拒绝。Hyperliquid/dYdX既有响应后目录检查增加适配器替换检查。
- 新8项离线回归覆盖目录/原生字段/client/adapter替换与删除、两个排队边界和无关元数据变更。未改变资金费来源、单位、未知周期或新鲜度门槛；不把历史记录解释为账户支付。
- 身份复核：Gate/Bitget/KuCoin/Coinbase Exchange公开证据、平台覆盖统计和可撤销批量审核已有实现；证据的客户端与现货范围必须精确匹配，不向永续或Coinbase Advanced Trade隐式扩展。现有199组演练候选不是生产批准，本批没有新增身份批准或声称覆盖扩大。缺少可靠官方合同证据的来源继续未知。
- 复现命令：`.venv/bin/pytest -q tests/test_funding_history.py tests/test_hyperliquid_funding_history.py tests/test_dydx_funding_history.py`。本批仅合成测试、无实网调用；未重试受阻OKX WS或修改独立环境。24小时未完成与来源归因不明仍开放。

- 验证：80项历史专项、1163项完整后端（42.55秒，1条上游弃用警告）、CI范围Ruff/diff通过。最终取时位置微调后重跑80项历史专项；无浏览器/实网验证，远端CI未确认。

### R45 原生历史接收边界与来源修订（2026-11-04）

- 父线程对715d1f3的离线扰动：结算时间比网络返回超前10ms、观察者等待40ms，可被误收；相同费率高度1234→1235静默沿用旧高度。这是合成复现，不是链上真实异常证据。
- PublicREST在HTTP客户端返回后、JSON解码及诊断观察者之前捕获receipt，通过dict/list兼容容器属性随响应传递，不污染原生JSON字段、不使用共享last_received。dYdX及Hyperliquid历史读取该receipt；取消观察者不保存，后续请求独立取时。边界是HTTP客户端完成响应，不宣称socket首字节时间；自定义普通dict/list测试替身仍以方法返回为后备。
- 历史修订比较全部持久化内容（排除received_at/first_received_at/last_seen_at/revision四项观察字段），包括区块高度、原生时间表示及来源。来源变化会追加旧完整记录到funding_revisions，当前记录revision递增并保留first_received；真正相同记录仅更新last_seen。不把原生高度当作链上最终性证明。
- 新5项合成回归：未来10ms/过去10ms+观察者40ms、取消后无写入且下一请求新receipt、同费率区块高度修订并重复去重、Hyperliquid延迟receipt；85项历史专项通过。已有原生fixture统一冻结两个模块的时钟，保持原断言。
- 未访问受阻OKX WS、未改独立环境或账户，无实网/浏览器验收；24小时、生产广泛身份覆盖仍开放。

- 最终验证：1168项完整后端（42.77秒，1条上游TestClient弃用警告）、85项历史专项、CI范围Ruff/diff通过；无浏览器或实网复测，远端CI未确认。

### R46 HTX线性永续公开已结算历史（2026-11-04）

- [官方USDT文档](https://huobiapi.github.io/docs/usdt_swap/v1/en/#general-query-historical-funding-rate)此次可读，提供GET `/linear-swap-api/v1/swap_historical_funding_rate`、`realized_rate`及原生`contract_code/symbol/fee_asset/funding_time`。显式接该v1端点；安装版CCXT统一线性历史已走v5，字段不同，本批未审核v5、不混用或失败后降级。
- 请求固定文档主机`https://api.hbdm.com`，使用现有无凭证客户端公共fetch及throttle(1)、适配器并发上限、总超时；不修改其urls/options，不继承库默认`api.hbdm.vn`，不重试/切主机。当前v1实网可用性未知，文档可读不证明接口仍可用。
- 范围仅准确原生base-USDT、linear=true/inverse=false、quote/settle=USDT永续。只保存realized_rate，保留advertised funding_rate供审计；错contract/base/fee资产、非有限/缺失率、未来/秒级时间拒绝。历史周期保持null，原生资金费是每次结算的小数比例，不乘合约乘数，不是账户账单。
- 单请求page_index=1、最多50行；要求status=ok/current_page=1/有界数组，since本地过滤、next_since=null、不宣称完整回填。目录在限流及网络等待后变化则拒绝。该路径新增14项离线回归，含安装版CCXT底层公共fetch的准确URL/参数/无鉴权和共享配置不变；99项联合历史专项通过。
- 身份资料复核：Coinbase官方Exchange products/currencies与Advanced Trade仍为不同client，未因同ticker合并；HTX公开币种元数据尚无充分合约地址证据，未新增生产身份批准。本批没有任何行情主机请求，不触发受阻OKX WS，不修改独立环境。
- 独立复测：在临时data目录构建CexAdapter('htx')并discover，选准确USDT永续后调用FundingHistoryService.get(id,refresh=True,limit=50)，核对原始realized_rate/fee_asset/funding_time。出现拒绝立即停止，不换域名；保留返回状态/时间，不记录凭证。此步骤尚未执行，24小时验收仍开放。
- 下一项：核对Coinbase Exchange公开目录与现有同client身份证据的衔接，先确认产品和报价时间语义，再决定是否增加行情client；不会直接扩张Advanced Trade身份白名单。

- 最终验证：1182项完整后端（46.11秒，1条上游TestClient弃用警告）、99项联合历史专项、CI范围Ruff/diff通过。首次路由测试发现CCXT默认主机不符，修复为显式官方地址后全量通过；没有实网/浏览器验收，远端CI未确认。
