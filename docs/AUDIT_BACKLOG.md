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
