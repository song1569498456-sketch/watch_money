# 当前开发状态

2026-11-04 前端信息组织更新：主导航收敛为市场监控（默认价差、资金费、全部市场）、我的监控、通知记录、设置。资产表展开组合可进入测算；资金费默认矩阵，保留单平台排行、原始周期、来源和历史。返回保留本次会话筛选/展开/滚动，身份与诊断放入设置。**本批后端575项、前端25项、40组浏览器检查、构建与静态检查通过**，见 [本批汇总](evidence/interface-validation.json)；浏览器证据及界面说明见 [INTERFACE.md](INTERFACE.md)、[browser-navigation.json](evidence/browser-navigation.json)。原计划未验收边界保持不变。

以下为此前功能批次记录：

2026-11-04。**完整三阶段计划尚未通过验收。** 本轮价差聚合/规模计算、资金费、费用配置及对应修复完成离线验证：**Python 563 passed、Node 18 passed；最终浏览器6/12/7组通过；TS/Vite build、CI Ruff E4/E7/E9/F和diffcheck通过**。证据见 [followup-validation.json](evidence/followup-validation.json)。这些是合成fixture、mock HTTP/RPC、loopback及隔离浏览器证据；不是实网全覆盖或24小时运行。

用户最新优先级：先数据正确性，再**按资产聚合 → best价差排序 → 展开双向组合 → 指定规模calculator**；第二资金费聚合，第三其他简洁指标/已有历史。不新增交易账户。本轮已有历史/原生能力保留范围，后续不抢先扩大原生协议开发。

逐项状态、代码/测试/证据和限制统一见 [AUDIT_BACKLOG.md](AUDIT_BACKLOG.md)。**每批修改后更新对应审计ID与证据，再同步本状态和验证报告；收到问题或存在代码不能直接标done。** 父线程报告的市场数量不是本环境真实采集样本。

## 已实现并离线验证

- 新数据目录默认启用全部28个计划平台，已有持久化启用列表保留。CEX目录不按主流币白名单裁剪，部分服务失败保留身份/单独报告。广覆盖按已发现活跃目录默认30秒/100市场分批轮转，1800秒重发现；关注资产另有详细REST/可用源WS，不能称全目录同时实时更新。
- CEX公共源WS及Hyperliquid/dYdX原生WS接入collector，包含稀疏字段、心跳/重连/选择变化取消与REST fallback。本地浏览器推送为初始完整markets、后续markets_delta增量/删除，保留过期转变、重连/模式重置；大目录实网负载未验。
- 资产级扫描按可核对身份、报价币、来源时间和有效bid/ask筛选best，展开原始市场与双向组合，保留两腿ID/源与接收时间。预算截断明示best仅限已扫范围。点击精确ID/策略进入calculator，不自动提交计算；指示扫描net=null/executable=false。
- Calculator支持基础数量或实际多档买方asks的金额换算，刷新指定两腿深度；不外推或静默舍余。spot base/perpetual contracts的step与min_amount、深度、费用/FX/时效/身份继续校验；未知或不足时不输出假净收益。退出假设有unknown/entry/prices/basis互斥输入；显式双腿holding逐腿名义/时长、一次换汇，独立于funding情景。
- 公共费用API/设置/计算器按venue、spot/perpetual、maker/taker隔离。112档案中22条普通公开参考，保留来源/核验时间/适用scope；Binance perpetual等未知保持空，Bybit特殊区、HIP-3/HL spot等不越scope自动套值。用户覆盖明确为假设、可审计/恢复；切腿/同平台换产品清除相应手填fee与holding、旧异步结果不串腿。不是账户实际费率，不使用账户API。
- CEX资金费独立任务默认60秒/100市场；DEX复用广覆盖REST已有payload并显示via_light_rest。全活跃perpetual排行、同资产矩阵保留raw/真实周期/8h、类型、下次结算/倒计时、逐字段时效；归一化费差不是现金收益。已结算历史是另外显式手动单页服务，保留原始值/来源/修订，不作后台全量回填。历史图表不借新价格事件刷新旧funding，类型/时期/演示/来源分别保留。
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
