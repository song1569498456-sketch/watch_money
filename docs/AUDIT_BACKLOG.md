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

### R47 Coinbase Exchange目录与原生盘口（2026-11-04）

- 官方：[products](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-all-known-trading-pairs)、[book](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-book)、[ticker](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker)、[currencies](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/currencies/get-all-known-currencies)。新增coinbaseexchange现货client，与原coinbase Advanced Trade并存、instrument/native_asset_id分离，不是故障回退或地域替代。目录需原始id/base_currency/quote_currency与解析结果一致；不一致项拒收并记录NativeCurrencyMismatch，discovery_complete=false。
- 新目录可进入既有Exchange currencies同client公开身份证据流程；public_source_scope仍拒绝Advanced Trade与永续继承。合约地址/链证据不充分、冲突或缺失仍待审，不因接入目录批准映射，不改变BTC/ETH/SOL既有审核规则。
- 安装版CCXT fetch_tickers走products/spark-lines，不能用于BBO。新client轻量及详细快照显式用GET products/{id}/book?level=1；选中组合深度用level=2，保持并发上限与CCXT公共限流，不轮询level3。保留原生带时区time，未知时间不补receipt；ticker最后成交时间不用于盘口。
- bids/asks为完整快照，空侧产生清除屏障；auction_mode=true指示性价格清除两侧且不进入正常排行，深度为空。数量取已聚合size，不乘num-orders。畸形、非有限/负数拒收；过期/未来由共同字段门槛排除，乱序/同刻正值不能复活清除，较新完整快照可恢复。观察者之前固定receipt，取消不返回patch。
- 新17项离线测试：安装版公开目录/盘口路由、无鉴权、原生币种冲突报告、Advanced Trade身份隔离、L1/L2及数量、auction/空侧、未知/过期/未来时间、延迟/取消、清除恢复。已有111项CEX/身份测试通过；未访问行情主机或受阻OKX WS，没有实网/浏览器证据或生产身份批准。
- 全目录发现不代表全目录实时新鲜：当前按既有有界单市场轮转采集，未接Exchange WS或批量BBO，覆盖/扫描周期需独立实测。没有为追求新鲜而放宽有效期。其它服务失败可报告部分目录，不静默替代。
- 独立复测：在临时数据目录discover Coinbase adapter，分别统计coinbase与coinbaseexchange ID，检查discovery_errors；选一个coinbaseexchange现货运行fetch_light/orderbook核对原始book.time/size/auction_mode。身份证据须显式刷新coinbase源并检查仅Exchange匹配，不能自动批准。拒绝即停、不换连接。下一项优先做Exchange短时公开实样与全目录时效评估；只有授权环境可达时执行。

- 最终验证：1199项完整后端（49.32秒，1条上游TestClient弃用警告）、17项Exchange专项、111项既有CEX/身份回归、CI范围Ruff/diff通过。最终增加目录冲突可见诊断后再跑17项专项；无实网/浏览器复测，远端CI未确认。

### R48 Coinbase产品限制与即时成交边界（2026-11-04）

- 父线程独立b960509实测报告：5次公开GET成功，BTC L1/L2及PEPE L1保留原生book.time正确；本环境未重跑、无原始响应附件。online但trading_disabled/cancel_only/post_only仍可测算达标是合成复现，当前实网目录未观察到该受限样本，不能描述成真实机会。
- [官方products语义](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-all-known-trading-pairs)：trading_disabled限制交易；cancel_only只接受撤单；post_only只允许maker下单但不撮合；limit_only允许跨盘口限价单匹配。新增Instrument.execution_restrictions/order_entry_modes/product_status_source/product_status_at，active仍表达目录在线可观察，独立表达匹配限制与来源观测时间。
- discovery每轮重建限制：disabled/cancel_only/post_only/auction与非法flag阻止即时成交；post_only仍保留maker订单许可，limit_only显示maker_limit/taker_marketable_limit而不误当全面禁交易。没有下单实现。限制解除重建为空；不从maker手续费推断未来可成交。
- Scanner仍保留报价与具体原因，双方向price_rankable=false，不能进入可靠价差排行；测算分类检查阻止净收益及threshold_met，包含具体产品原因。快照issues也保留限制说明，现有详情可查看。资金费本身的观测值不是成交承诺，未改变费率数据门槛。
- 新12项离线回归：四种模式、限制解除、盘口保留、两种费用下三种受限状态均不产净收益、limit_only可匹配、双方向排行与恢复；158项Exchange/scanner/strategies专项通过。范围是输入目录已知的产品状态，不承诺在两次目录刷新间获知所有变更，product_status_at保留实际观测时间。
- 无账户/交易、无受阻连接重试或独立环境变更；24小时及受限模式实网仍未验收。下一项先由独立环境复核产品状态到排行/测算的一致性，再推进目录时效与其它未完成数据源。

- 最终验证：1211项完整后端（51.30秒，1条上游弃用警告）、158项Exchange/scanner/strategies专项、CI范围Ruff/diff通过。无前端/浏览器修改、未新增实网或修改受阻连接，远端CI未确认。

### R49 通用CEX流接收时间（2026-11-04）

- 父线程b960509离线复现：watch_tickers方法返回时源时间超前10ms，诊断观察者等待40ms后才取receipt，使未来帧误判可排行。不是实网Coinbase时钟异常证据。
- watch_tickers与watch_ticker每次返回后立即捕获receipt，观察者、逐市场解析、队列等待不改时钟；ticker_patch显式接收该值，并写入每个已提供字段的field_received_at，来源/原生源时间保持独立。直接调用解析器时仍可使用本地当前时间作为缺省。
- 未来帧不推进last_source_times，也不污染正常帧去重指纹；仍输出带真实receipt的可诊断patch，由共同有效性检查拒绝排行。随后正常帧可恢复，已确认的旧帧继续丢弃。移除原先允许超前30秒推进水位的逻辑。
- 新7项离线回归：OKX/Bybit/Coinbase Exchange分别覆盖single/multiple的10ms未来+40ms观察者、随后正常与乱序恢复；取消观察者无patch返回，关闭后重新订阅重新取时。18项流专项通过，无新交易所网络请求。
- 覆盖边界：CexStreamAdapter没有catalog_stream标记，collector按watch_assets选关注币种，分client/产品组和小批订阅ticker；它不是全目录book流，也不是原生逐盘口字段更新时间保证。Coinbase Exchange全目录发现后的L1/L2是R47有界REST轮转/按需深度；此次不把关注ticker流宣称全目录盘口覆盖。CCXT方法返回时刻不是socket首字节时间，不替代原生事件时间。
- 没有重试已拒绝的OKX WS、没有修改独立环境/账户；长期实网覆盖与24小时仍开放。

- 最终验证：1218项完整后端（51.67秒，1条上游TestClient弃用警告）、18项流专项、CI范围Ruff/diff通过；无浏览器/实网验证或账户变更，远端CI未确认。

### R50 dYdX有界历史旧页查询（2026-11-04）

- [官方控制器](https://raw.githubusercontent.com/dydxprotocol/v4-chain/main/indexer/services/comlink/src/controllers/api/v4/historical-funding-controller.ts)提供effectiveBeforeOrAtHeight并按原生高度降序。补齐R43“只能最新页”的功能缺口：GET /api/funding/history/{id}?refresh=true&before_height=N 显式读取截至N（包含N）的单页，仍最多100条，不自动循环/后台回填。
- before_height仅dYdX支持，必须1..2^53-1整数；向官方传effectiveBeforeOrAtHeight，保留原始height字符串。原始高度超安全范围、超过请求边界等拒收；既有ticker/time/rate/来源校验保持。接收时间仍在观察者之前，未知历史周期不补小时值。
- summary.next_before_height为本页最小有效高度-1，仅本页无拒收且值为正时返回；空页/拒收停止提示。它是继续请求的提示，不证明没有漏块、同高度更多记录或历史完整。since仍为本地时间过滤；有过滤拒收时也保守停止高度提示。
- 本地缓存读取同步按native_block_height上界筛选，不让先缓存的新高度混入旧页；重复记录保留修订规则。coverage标为dydx_backward_height_hint_not_gap_proof；其它平台不接受该参数，避免假装具有通用分页语义。
- 新11项离线回归覆盖两页/本地缓存隔离、包含边界、重复去重、拒收不跳页、非法/超安全高度、HTTP参数传递及外平台拒绝。官方源码是协议依据，不是本环境实网数据；未请求交易所或受阻OKX WS、未改账户或生产身份。
- 独立复测步骤：在临时数据目录按FUNDING_VIEW R43建立服务，先get(id,refresh=True,limit=10)，人工检查summary.next_before_height，再显式get(id,refresh=True,limit=10,before_height=该值)，核对官方高度/时间、缓存分页、原始费率。遇拒绝停止，不换域名、不自动重试。历史完整与24小时仍待验收。
- 下一项：继续检查其它平台历史的原生合约/时间与分页证据，优先补可验证的记录边界；Kraken/Deribit连续资金费仍不套入离散已结算模型。

- 最终验证：1229项完整后端（51.85秒，1条上游TestClient弃用警告）、110项历史专项、CI范围Ruff/diff通过；未改前端/重跑浏览器，实网与远端CI未确认。

### R51 Bybit/OKX原生历史边界（2026-11-04）

- 核查官方[Bybit历史](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate)和[OKX历史](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history)。Bybit原生symbol/fundingRateTimestamp/fundingRate与OKX instId/fundingTime/realizedRate分别校验，不能依赖统一symbol/time或拿统一率补原始字段。时间必须与统一毫秒值精确一致，秒/错时/小数偏移拒收；OKX显式FUTURES/X-Perps记录不纳入当前SWAP范围，缺instType兼容旧响应。
- 请求前要求已发现准确原生id、swap、symbol/settle范围；记录新增native_market_id/native_settlement_time/native_time_unit/rate_field/settle/rate_unit，原始值保留以支持修订追踪。历史周期仍未知，不套今日目录周期。
- 保留最小单页范围：本服务OKX上限100（官方当前最大400，但不为扩大数量改策略）、Bybit200。CCXT返回数组超本页界限整页拒绝，不截断后假造cursor；逐记录拒收时不返回next_since。统一方法之前的库内部过滤不等于本服务看到了全部原始行，实网完整性仍未知。
- 安装版解析器离线核实since路由：OKX使用before=since-1（官方before表示更晚记录），Bybit显式startTime=since/endTime=本次当前时间，不让库按今日fundingInterval估历史窗口。paginate=false且每次仅一页，next_since仍只是提示，不证明最新窗口没有跳过旧页，不是完整回填。
- 新19项离线测试覆盖两平台缺原始ID/时间/率、错ID/秒/错时/小数、超页、目录错产品、OKX X-Perps混入及拒收停止提示；已有安装版路由测试增加since/endTime断言。129项联合历史专项通过，尚无新实网证据。
- 只读取官方文档，没有请求OKX受阻WS或任何行情主机、没有账户/地域替代。其它平台原生历史核查、生产身份及24小时仍开放。下一项按同一最小原则审查Binance两个合约client的原始symbol/fundingTime与特殊资金费事件，不擅自引入新结算语义。

- 全量初跑中两项既有followup验收fixture缺少原生ID/时间而失败；补齐官方字段后，缓存重启与修订断言保留。部分拒收时游标断言按新语义改为null；两项复测通过，最终完整结果另记。未因失败放宽原生校验。

- 最终验证：1248项完整后端（52.56秒，1条上游TestClient弃用警告）、129项历史专项、2项旧验收复测、CI范围Ruff/diff通过；无前端/浏览器变更，实网和远端CI未确认。

### R52 dYdX页面一致性与非唯一高度（2026-11-04）

- 父线程c4ae43e离线发现：缓存已存100，since+limit=1刷新300/200/100时返回缓存100，自动299/199/99会跳过新页；同高度事件被减一游标跳过、同页冲突按顺序改写修订。这是合成边界，不是实网同高度事件证据。
- [官方模型](https://raw.githubusercontent.com/dydxprotocol/v4-chain/main/indexer/packages/postgres/src/models/funding-index-updates-model.ts)包含eventId/perpetualId/height；公开historicalFunding响应未提供可继续同高度事件的唯一cursor。撤回R50自动height-1提示，next_before_height恒null，pagination_incomplete=true，coverage改为dydx_manual_height_filter_no_safe_continuation。before_height仅保留人工明确筛选；手动跳低高度仍可能丢边界事件，不能当完整回填。
- refresh=true返回本次响应中通过验证并保存的记录（records_scope=accepted_current_response_page），保留数据库实际revision/first_received等；不使用混合缓存窗口代替本页。普通缓存读取仍是local_cache_query，cached_matching_count继续单独表示缓存匹配量。页面truncated不再由其它缓存行数量推断。
- 同页重复高度（含前导零等价表示）或同一结算时间的记录全部按ambiguous_native_event_identity拒收，不按返回顺序选赢家，不保存或写修订；即便缓存已有记录也不会被该页改写，刷新返回空页而不是旧缓存。单独下一次观测的来源变化仍沿用R45审计修订，不把它当已证明的链上更正。
- 新7项回归覆盖旧缓存+since+limit=1连续手动300/200/100页面、两种响应顺序/同高度同或异时间、已有缓存保护及高度等价拼写；旧自动cursor断言改为停止，既有时区fixture为独立事件补不同高度。保留单页上限/身份/原生时间/取消边界。
- 仅离线、未访问行情主机/受阻OKX连接、未改账户或独立环境。R50“读取next_before_height继续”的复测步骤已被撤回；复测应人工指定已知高度并核对records_scope，不声称能遍历所有事件。完整历史需要官方可证明的事件唯一标识或分页协议，当前为明确阻塞；24小时仍未验收。

- 最终验证：1255项完整后端（51.79秒，1条上游TestClient弃用警告）、136项历史专项、CI范围Ruff/diff通过。无前端/浏览器变更、实网与远端CI未确认。

### R53 Binance两类合约历史边界（2026-11-04）

- 官方[USDⓈ-M历史](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)及[COIN-M历史](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Rate-History-of-Perpetual-Futures)确认symbol/fundingTime/fundingRate、起止毫秒包含边界和单页上限。新增原生symbol与已发现swap目录一致、原始时间与统一毫秒一致、原始费率不可用时拒收，不从统一字段补齐。记录保留原始合约/时间/费用币和小数比例，历史周期仍null，不乘合约面值。
- USDⓈ-M官方rateType区分Regular与股票分红额外资金费Special，继续按事件类型分别存储，不混成同一次修订；COIN-M未核实Special则拒收。未知显式类型不猜，缺rateType沿用历史普通记录兼容。没有增加股票目录、账户收入、交易或收益预测功能。
- 同页相同结算时间/类型但原始率冲突的全部拒收，不按顺序选择；重复相同记录仍去重。任何拒收停止next_since；满页也停止时间+1提示，防止边界同时间另一个事件被跳过。每次一页，无自动回填，不保证完整事件历史。
- 新16项离线回归覆盖安装版fapi/dapi公共路由、startTime及无凭证、正反合约原生单位/符号、错ID/时间/率、未知类型、COIN-M Special、冲突顺序与满页边界；既有普通/特殊同刻独立记录及修订测试保留。旧默认fixture补原生字段，不放宽验证。
- 只读官方文档和离线替身，未请求行情主机、受阻OKX连接或私有接口；dYdX R52分页不可证明完整的限制继续保留。下一项优先核查MEXC/KuCoin历史原生字段及历史周期依据；没有公开可证语义就保持未知。生产身份、跨平台真实覆盖和24小时仍开放。

- 最终验证：1271项完整后端（53.38秒，1条上游TestClient弃用警告）、152项历史专项、CI范围Ruff/diff通过；无前端/浏览器或实网验证，远端CI未确认。

### R54 MEXC/KuCoin历史字段与周期（2026-11-04）

- 官方[MEXC历史](https://www.mexc.com/api-docs/futures/market-endpoints/get-funding-rate-history)明确symbol/fundingRate/settleTime以及collectCycle为整数小时。保存原生ID/结算时间/原始周期；仅有效正整数且安全范围内周期进入funding_interval_hours，缺失、布尔、非有限、非正或小数保持null，费率本身可独立保留。不套8h或当前市场周期。
- 官方[KuCoin Classic公共历史](https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history)给出symbol/fundingRate/timepoint及from/to；没有逐记录周期依据，继续null，即使额外字段恰名collectCycle也不套MEXC语义。强制uta=false，显式until转to，安装版公共futures路由核验；不访问私人资金费账单，也不将UTA视作替代源。
- 两平台要求准确已发现swap原生id/symbol/settle，原始合约/毫秒时间与统一字段一致、原始率存在；不靠统一率补缺。保存单位、原始时间与费率字段。超本服务单页100拒绝；这是保守本地边界，不宣称官方MEXC最大仅100（官方当前上限1000）。
- MEXC显式page_num=1，since只在库/本地筛选，next_since=null；KuCoin只表示一次from/to窗口，排序/保留范围不足以证明无缺口继续，同样不发安全继续提示。无自动回填或完整历史承诺。同页同一结算的率/周期冲突拒收，不按响应顺序修订。
- 新21项离线回归含安装版原生公共端点/参数、时间与小数费率不乘合约面值、错ID/时间/缺率、7种无效MEXC周期、KuCoin不继承周期、同页费率/周期冲突。既有MEXC周期fixture补原生字段，保留原断言。
- 没有行情主机请求、受限OKX WebSocket重试、账户设置或交易变更；dYdX R52分页不可证明完整、全平台实网/身份覆盖和24小时仍开放。下一项优先继续平台原生数据时效与身份缺口，新增接口须先有官方可证语义；未知仍保持未知。

- 最终验证：1292项完整后端（53.62秒，1条上游TestClient弃用警告）、173项历史专项（5.03秒）、CI范围Ruff/diff通过；无前端或实网验证，远端CI未确认。复测：`.venv/bin/pytest -q tests/test_mexc_kucoin_history.py`（21项）；无网络替身覆盖公共路由/原始字段/周期/冲突。

### R55 Upbit可观察盘口覆盖（2026-11-04）

- 价值/原缺口：Korea ticker为成交统计，不提供可靠bid/ask；仅有目录/成交价不构成价差盘口。按官方[韩国公共盘口](https://docs.upbit.com/kr/reference/list-orderbooks)补充GET /v1/orderbook；保留韩国api.upbit.com，不替换其他地区服务，不使用鉴权。官方限额10次/秒为共享IP盘口组；安装CCXT cost=2、50ms限流，沿用client限流及适配器并发容量。
- 已实现：全目录轻量轮转在ticker后补原生book，每批最多50市场，明确level=0/count=1；不是全目录单次无界请求。KRW/BTC/USDT准确native ID匹配，遗漏/重复/错误记录按市场报告，批次失败保留原成交统计；不以最新成交价填盘口。保留last/volume各自source/receipt，不让book更新时间刷新旧ticker。记录盘口组请求预算下界ceil(N/50)*100ms，不含ticker/网络与其它调用争用，不是实际周期保证。
- 原始timestamp作为盘口源时间，网络方法返回后先捕获receipt再调用观察者；无时间保持未知，过期/未来仍被原有效性门槛排除。非零聚合level拒绝；空完整快照/零数量侧发显式清除，乱序及同刻正价不能越过清除屏障。目录原生ID/产品范围改变时拒收；取消传播，后续轮询可恢复。按需深度使用同一公共路由count=30，数量为base、保留源时间，不能冒充无限深度。
- 验证：新18项离线测试，真实安装CCXT公共URL/参数/无鉴权替身；120个合成小币市场分50/50/20，120个有效双边盘口、ticker字段保留，包含三种报价币。其它回归覆盖部分失败、缺失/重复/错误市场、错误格式、非零level、单/双空侧及恢复、时间和延迟观察者、取消/目录变化。复测`.venv/bin/pytest -q tests/test_upbit_books.py`。这不是120个真实市场已验收。
- 实际覆盖：本轮真实Upbit目录和盘口采样数为0（未发行情请求）；生产映射批准数无变化，不能声称增加可靠跨平台资产组。新鲜BBO仍需要已核验身份/报价币或可信FX才能参与跨平台比较；KRW不自动等价USD。通用关注ticker WS仍只给成交统计，未新增Upbit全目录盘口WS。
- 未完成：真实轮转/限流、冷门盘口完整性与长期稳定、全资产证据/报价换算、24小时验收。保留dYdX分页不完整与其它平台限制。未重试受阻OKX WS、未修改账户/独立环境、不增加交易。下一项优先从实际有效盘口/身份覆盖报告继续挑缺口，而非仅扩历史名单。

- 最终证据：1310项全量后端（57.05秒）通过；随后补充请求预算下界并验证最终51项Upbit/轻量专项（4.48秒），CI范围Ruff和diff通过。前序80项Upbit/轻量/Coinbase专项通过。无前端改动/浏览器复验，远端CI未确认。

### R56 MEXC/KuCoin原生页截断前校验（2026-11-04）

- 父线程在cb534c9使用CCXT 4.5.91离线对抗样本复现：同刻冲突两行经统一fetch_funding_rate_history(..., limit=1)先过滤成一行，R54应用冲突检查无法见到另一行；反序会接受不同率并记成修订。新测试先调用真实安装解析器证明len=1，再对服务验证两种顺序均拒收2行。不是实网冲突观测。
- 修复：两平台直接调用同一CCXT client的原生公共方法contractPublicGetFundingRateHistory / futuresPublicGetContractFundingRates；不使用统一解析器的排序/限量/符号预过滤。保留SDK签名路由、原生请求权重/限流器、适配器semaphore、总超时、身份范围前后校验、凭证禁用和原错误转换；不修改client设置或自动重试。接收时间在方法返回即捕获，来源记录明确native_public_funding_history。
- MEXC固定page_num=1/page_size=100，独立于显示limit；原生success/code/currentPage/resultList须匹配，超过100整页502不保存。KuCoin Classic直接from/to窗口，无page-size参数，完整data最多1000行，超过1000整页拒收，不截断伪装完整，不转UTA。100/1000是本地有界验收策略，不声称官方完整历史上限。SDK当前MEXC默认host为api.mexc.com，保持原统一路径同一client路由，未实网验证。
- 完整有界原生页的合约/时间/费率/周期与同刻冲突检查在任何输出limit前执行；接受记录先保存、最终缓存查询才截取显示。summary.fetched_count/accepted_count/rejected_count反映原生页；count为显示条数、cached_matching_count单列。next_since保持null、不保证跨页边界完整。拒收页可返回已有local_cache_query，accepted_count=0，不把旧缓存当新接受；已缓存记录和revision/last_seen不被冲突页改写。
- MEXC周期仍仅原始有效整数小时，KuCoin缺周期依据仍null；合约面值不乘费率。以前保存的历史/修订不自动清除或改写，若曾遇真实冲突需要原始证据再审，本批没有这种实网证据。
- 新增24项离线回归（该文件共45项）：真实安装解析器limit=1×两平台×两顺序、超大页整页拒收、输出小于原生页仍全部校验/单位、非法原生信封、SDK公共HTTP路由/正权重限流调用、超时/权限/限流错误单次调用、旧缓存不修订。原21项及共享fixture改为原生信封；原“统一时间错配”样本改测原始未来时间，因为新路径不再有SDK统一时间层。
- 复测`.venv/bin/pytest -q tests/test_mexc_kucoin_history.py tests/test_funding_history.py`：132 passed（5.52秒）；CI范围Ruff通过。未访问交易所行情主机、受阻OKX WS、账户或独立环境。全市场身份/实时覆盖及24小时限制仍开放。

- 最终验证：1334项完整后端（59.40秒，1条上游TestClient弃用警告）、132项专项、CI范围Ruff/diff通过。无前端变更/浏览器复验，远端CI未确认。其它仍用统一历史解析的平台是否存在类似前置过滤风险，后续逐项复审，本批不宣称已全部排除。

### R57 其余CEX原生页的SDK前置截断审计（2026-11-04）

- 逐个检查安装CCXT 4.5.91：Binance USDⓈ-M/COIN-M最终parse_funding_rate_histories，OKX/Bybit/Gate/Bitget/HTX inverse最终filter_by_symbol_since_limit，均可能在应用前过滤原始行。7个client×正反两序的真实解析器离线用例均复现同刻冲突2行在limit=1变1行；不是实际平台出现冲突的证据。HTX linear已经是原生HTTP路径，Hyperliquid/dYdX也直接接收原生页，不存在本次SDK预截取路径；它们原有完整性限制仍保留。
- 修复：新增adapters/history_pages.py，只调用现有同client公共原生方法，保留SDK请求权重/限流、固定产品与原生合约参数，不切换账户/UTA/地区服务、不修改client设置。请求参数、原生成功信封及返回数组先核对，方法返回即记录receipt；应用在完整有界页上验证、分组，再保存/输出。用户limit仍影响服务器请求数量，但意外多返回且未超本地cap的行也全部校验；超过cap整页502不保存，不让SDK截断隐藏超大页。

| 路径 | 直接公共方法/参数 | 本地完整页cap与单位 |
| --- | --- | --- |
| Binance USDⓈ-M | fapiPublicGetFundingRate，symbol/startTime/limit | 1000；fundingTime毫秒；Regular/Special独立事件 |
| Binance COIN-M | dapiPublicGetFundingRate，同上 | 1000；毫秒；未核实Special拒收 |
| OKX | publicGetPublicFundingRateHistory，instId/before=max(since-1,0)/limit | 本地100（不是声称官方最大100）；fundingTime毫秒、仅realizedRate |
| Bybit | publicGetV5MarketFundingHistory，symbol/category/limit；有since时同时传startTime/endTime=当前观测时刻 | 200；fundingRateTimestamp毫秒；不用当前周期估历史窗口 |
| Gate | publicFuturesGetSettleFundingRate，settle/contract/limit/from=floor(since/1000) | 本地100；t整数秒转毫秒，r小数比例；since再按毫秒过滤 |
| Bitget Classic | publicMixGetV2MixMarketHistoryFundRate，symbol/productType/pageNo=1/pageSize | 本地100；fundingTime毫秒；不切UTA |
| HTX inverse | contractPublicGetSwapApiV1SwapHistoricalFundingRate，contract_code/page_index=1/page_size | 50；funding_time毫秒；仅realized_rate，funding_rate只作原始参考 |

- 官方依据继续对应SETTLED_ENDPOINTS中的逐平台链接，复核[Bybit](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate)、[OKX](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history)、[Bitget Classic](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-historical-funding-rates)、[HTX inverse](https://huobiapi.github.io/docs/coin_margined_swap/v1/en/#query-historical-funding-rate)，以及已登记Binance/Gate原生时间/参数。上表cap是保守应用验收边界，不证明所有官方保留范围/服务可用。
- 所有CEX同页相同结算/事件类型都检查冲突，不再限Binance/MEXC/KuCoin。比较全部待持久化内容，HTX“相同实际率、不同原始advertised率”和MEXC“相同未知周期、不同原始周期”也不能按响应顺序制造修订。完全相同重复行仍去重；先前缓存、last_seen/revision不被冲突页覆盖。原始整数时间不作小数舍入；未来/缺时/缺实际率/原生身份错误仍拒收。
- 所有满页及存在拒收的响应停止next_since，避免同刻边界被时间+1跳过。Bitget/HTX/MEXC本就仅最新第一页，KuCoin窗口无安全继续游标；短非满页上的旧hint仍不是完整回填证明。未增加自动翻页/历史平台/后台采集；未知周期不套8h，合约面值不乘率。
- 新54项离线回归：14组安装解析器前置截断+两序无修订，7平台超cap、原生单位/缓存保护、实际安装公共HTTP路由及正权重限流调用、错误信封、访问拒绝单次请求；Gate/HTX小数原生时钟拒收、原始元数据冲突。旧fixtures改为原生信封；不再存在的“SDK统一时间错配”测试改测原始未来时间，原边界与缓存/重启断言保留。2项HTTP/重启验收单测复跑通过。
- 当前专项命令`.venv/bin/pytest -q tests/test_native_history_pages.py tests/test_funding_history.py tests/test_binance_funding_history.py tests/test_mexc_kucoin_history.py`：202 passed（12.02秒）；CI范围Ruff通过。所有HTTP由本地替身拦截，无行情请求/受阻OKX WS重试/账户设置变更。真实同页冲突、全目录实时覆盖、生产身份和24小时仍未验收。

- 最终验证：1388项完整后端（64.69秒，1条上游TestClient弃用警告）、202项专项（12.02秒）、2项HTTP/重启验收通过，CI范围Ruff/diff通过。无前端变更/浏览器复验，远端CI未确认。本批代码不再调用CCXT统一fetch_funding_rate_history，历史完整性仍取决于原生分页能力和来源可用性，未宣称完整回填。

### R58 全市场资产分页（2026-11-05）

- 选择依据：原计划D01/U01要求全币种可观察。检查现有价差UI及scanner后确认资产组只返回前1000条，没有翻页入口；后续市场需要先知道名字才能搜索。此次补实际浏览缺口，不新增历史平台、采集来源或交易功能。
- 后端scan_markets与GET /api/scanner新增group_offset/group_limit，默认兼容旧limit；偏移0–100000000，每页1–1000。分页只截取完整已生成/排序的资产摘要，与candidate返回limit、每资产方向limit及10000方向扫描预算分离。响应提供group_offset/group_limit/next_group_offset/groups_returned，所有目录/扫描/可靠方向统计仍表示同次查询全筛选范围。超出当前范围返回空页，不偷偷跳页或增加扫描预算。
- UI资产聚合每页100组，显示当前范围/总数及首页/上一页/下一页。未知身份、无报价、过期和未扫描组仍按原状态展示，不为翻页升级可靠性。切换搜索/平台/市场类型/排序回首页；页码参与请求版本，旧响应不能覆盖新页。刷新和离开再返回保留当前页并沿用现有重新核验机制；不跨浏览器重载保存页码。
- 浏览器发现“末页搜索后清空搜索会恢复旧页码”，已修复为筛选改变时同步重置存储的分页状态；不只临时计算0。目录收缩导致当前页为空时，明确显示“当前页没有资产，请返回首页”，保留返回操作。
- 用户可见边界：每次翻页重新读取collector当前公开行情快照（字段各自来源/时间不变），页面提示实时排序可能移动或重复，不是冻结快照/稳定游标，不能保证跨页无重复/无遗漏；需要定向核验时用资产搜索。分页不增加采集覆盖、身份批准或已扫描方向；方向视图及展开方向截断限制保持。
- 数据来源：复用已发现市场、已采集快照、现有身份与字段时效判定；本批没有任何交易所行情请求。UI调用本地scanner只读查询，仍不接账户、下单或重试受阻OKX连接。
- 验证：新增10项后端回归，1005个合成小币双平台目录访问offset1000最后5组、扫描/candidate预算不变、身份/截断保护、搜索和越界参数；scanner专项64通过。隔离浏览器7项（100组首页/1000后末页/返回重验/搜索清空重置/类型空状态/390px无横向溢出/无JS错误），证据见[evidence/browser-scanner-pagination.json](evidence/browser-scanner-pagination.json)及[screenshots/scanner-pagination-synthetic.png](screenshots/scanner-pagination-synthetic.png)。1005为合成资产，不是真实新增覆盖。
- 完整后端1398通过（66.20秒，1条上游TestClient弃用警告）、前端44项通过、TS/Vite生产构建通过、既有导航12项通过（包含组合→测算→返回及过期保护）、CI范围Ruff/diff通过。真实全币种覆盖、生产身份、持续行情和24小时验收仍开放；未扩大全历史功能。

### R59 安全批量身份审核与覆盖可见性（2026-11-05）

- 已有能力：公开来源采集、只读review_packet、完整策略计划、事务内重验、映射审计与撤销均已存在。本次只补用户难以操作的分组筛选/分页、限定选择预览及UI；旧手动流程和旧全计划API兼容保留。
- 新GET `/api/assets/verification/batch-review`支持来源、规范链、合约包含、状态、具体阻止原因筛选；默认25组、最多100组一页。先对完整证据运行策略再筛选整组，来源筛选不会隐藏另一平台或冲突依据。提供全局候选统计、筛选统计、实际跨平台身份门槛通过的组/市场数量，各自分开展示。合约大小写宽容仅用于搜索，不参与合并判定。
- 新POST `batch-preview`接收1–100个唯一proposal_ids，最多10000条可应用证据；返回精确可应用组/市场和阻止/已核验跳过组。选中集合绑定全状态token；`batch-apply`带同一集合，在原子事务内重新构建策略。资料变化、过期、来源失败、已有人工映射等不能被预览绕过；省略集合或换组不能扩大已预览操作。审计保存选中ID、计数和跳过原因。
- UI在设置身份面板显示来源、链/合约、状态、跳过原因、完整证据URL/字段/摘要/观测及到期时间。一键选择仅当前页可审核组，仍需预览后明确应用。翻页、筛选、父数据刷新清空选择；迟到响应不能恢复旧上下文操作。当前页面每页25组，API支持最多100组的限定批次，不跨页隐式选择。加载时旧页暂不可操作，显示检查时刻；应用前必重新核验。
- 复用official-spot-contract-v1：两个独立登记官方来源、同链同合约、原生client/currency/product/单位一致，观测24小时内且剩余有效期至少1小时，完整性、最新来源收据及映射冲突全部检查。只适用普通现货；永续底层需独立依据。候选绝不等于已批准，身份通过不表示价格新鲜、报价币可比较或资金费可比。
- 本次会话保留上一批证据供填写原因后撤销；更早批次继续原有证据明细/审计撤销。分页是实时重新计算，资料变化可移动组，不保证冻结快照；token防止因此误应用。没有自动批准、生产数据迁移或账户操作。旧不带proposal_ids的全计划API未改为100组限制，新限定流程才有上述批次cap。
- 新9项后端回归：完整组筛选与分页、限定应用/撤销、冲突不隐藏、过期/变更token、空/未知/超100组、混合跳过及审计、API边界；最终78项身份专项通过。完整后端1407通过（66.15秒，1条上游弃用警告），44项前端及TS/Vite构建通过。完整后端后仅追加跳过审计字段与断言，最终78专项复验通过。
- 隔离浏览器7项通过：30合成资产/60现货，额外冲突形成31组，第一页预览应用23组/46市场并跳过2组；实际临时库覆盖0→23/46→撤销0。还核对来源/链/过期原因、翻页清选择、390px无溢出/无JS错误。既有手动审核9项检查通过。见[evidence/browser-identity-batch-review.json](evidence/browser-identity-batch-review.json)、[screenshots/identity-batch-review-synthetic.png](screenshots/identity-batch-review-synthetic.png)；脚本`.venv/bin/python scripts/browser_identity_batch_review.py`仅临时库、MockTransport官方结构及本机HTTP。
- 局限：没有新增真实公开证据或修改任何用户映射；真实候选仍需用户使用此流程审核，现货证据不扩永续。全币种实际可靠覆盖、长期新鲜度、24小时、外部通知及原清单开放；不重试受阻OKX WS或受限Lighter，远端CI未确认。

### R60 旧审核入口保护与完整撤销历史（2026-11-05）

- 复现并修复旧preview/apply未检查最新官方来源读取、可覆盖人工映射的缺口。公开证据现在在新旧入口共用官方现货策略检查（来源收据/失败、完整性、有效期、原生身份、冲突）；应用事务内再次检查，旧token失效。只选择同市场的手动证据也不能绕过仍有效的公开证据来源失败。
- 手动/本地链上证据仍保留独立人工审核，不假称官方来源，且不因与公开证据混组而免检公开部分。已有人工映射或目标不同的现有映射阻止覆盖；预览显示市场、当前值及提议值。需用户自行明确修订已有映射，本批不隐式撤销用户数据。
- status增加evidence_offset/evidence_total/evidence_next_offset；证据明细支持首页/前后页，能访问2000条以后的完整历史。翻页及页大小变化清空选择并禁用旧内容；异步刷新绑定查询范围。所选证据显示当前依赖市场，撤销仅针对明确ID，保留审计；实际影响按提交时最新依赖计算，人工映射没有证据依赖则不删除。
- 原review_packet继续使用一次捕获的目录/证据，加入同一来源/映射检查；不重新读取不同版本的证据来生成页面token。全量策略候选不成为已批准覆盖。
- 81项身份专项通过（新增3项：两路径来源失败/旧token及手动选择绕过、人工映射旧值保护、2100条历史后API分页撤销）。前端构建、Ruff/diff通过。旧手动浏览器10项通过，包含真实本机API的2000后翻页、返回首页、撤销/持久化/冲突和390px检查；发现的页大小旧响应窗口已修复。所有变更/数据仅临时库与合成来源；未改变生产映射、账户或受阻连接。
- 按用户新结束条件，本轮完成后检查最终提交CI并合并，不再以24小时或全部平台完成为前置条件。真实广泛身份仍需审核、现货依据不能自动证明永续、实网长期稳定性/地区限制/通知模拟等原限制保留。部署发布不在本次范围。

- 新批量入口7项隔离浏览器复验通过，与旧入口同时保留；无真实覆盖增量。
