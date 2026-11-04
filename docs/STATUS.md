# 当前开发状态

2026-11-04。仓库从空 README 开始，已集成一个可运行版本。**整个三阶段计划尚未完成验收。** 当前交付的是有真实接口实现、可运行 UI、持久化与计算/通知闭环的开发版本。

## 当前目标和完成项

- 建立数据契约、资产身份隔离与映射审计、质量检查、采集退避、SQLite 与 Parquet/DuckDB 历史。
- 实现 12 CEX 公共适配与受支持产品的源 WebSocket，9 永续 DEX 全部或部分公开数据适配、Drift 原程序 RPC 元数据读取，以及 6 现货 DEX 的明确标识聚合池观察。
- 构建七类策略输入校验、规模/深度均价、费用、资金费时间表、8/24/72h 情景、样本保存和不前视回放。
- 增加持久化计算监控计划，重新拉取盘口并触发本地规则；失败时停止提供净收益、触发失效事件。
- 实现通知规则、持久化队列、冷却去重、失败重试与重启恢复。按用户最新要求只做模拟渠道测试。
- React 页面提供市场/详情/平台状态/计算/通知/配置，以及人工映射、连续监控、批量规模与 FX 输入。
- 云端 Chromium 验证与截图已保存；Windows 启动脚本已提供，未在 Windows 真机验证。

## 团队与任务板

所有子 Agent 与主 Agent 共享工作区，主 Agent 集成并提交；没有未说明的后台自治任务。

| ID | 负责人 | 依赖 | 文件范围 | 状态 | 验收与证据 |
|---|---|---|---|---|---|
| CORE | 主 Agent | 空仓库 | models/app/storage/collector/monitors/scripts/CI | 本地集成复测通过 | 核心 API、Parquet、恢复、身份保护；tests/test_acceptance.py |
| CEX | cex，gpt-6.1-sol/high | CORE 契约 | adapters/cex.py + 专属测试/文档 | 自测/独立测试通过，在线待验 | tests/test_cex.py；CEX_SOURCES.md |
| DEX | dex，gpt-6.1-sol/high | CORE 契约 | adapters/dex.py、deployments.json | 部分能力自测通过，覆盖未全 | tests/test_dex.py；DEX_SOURCES.md |
| CALC | strategies，gpt-6.1-sol/high | CORE 契约 | strategies.py + 专属测试/文档 | 自测/独立测试通过，真实样本待验 | tests/test_strategies.py；CALCULATIONS.md |
| ALERT | notifications，gpt-6.1-sol/high | CORE/CALC | notifications.py、通知/监控测试 | 模拟验证通过 | tests/test_notifications.py、test_monitors.py |
| UI | frontend，gpt-6.1-sol/high | API 契约 | frontend/** | 构建与浏览器验证 | docs/screenshots；VALIDATION.md |
| ACCEPT | acceptance，gpt-6-astra/high | 全部模块 | 独立测试与 ACCEPTANCE.md | 完整计划未通过 | ACCEPTANCE.md 中的独立结论 |

## 未完成项与外部条件

1. Drift 已核对原程序身份/布局，可选 RPC 元数据读取；当前实时生命周期未经验证。inactive 表示本适配器不支持可执行行情，不代表断言链上暂停；没有把后继协议冒充原服务。Jupiter 已实现官方公共只读 API，侧别借贷/状态单独显示。GMX/Jupiter 等完整持仓成本模型和所有 AMM/RFQ 可执行规模报价尚未实现。
2. 六类现货 DEX 目前是经部署登记过滤的聚合观察，缺完整原生 RPC、区块状态和路由报价。不能将其计为计划要求的全部原生链上接入。
3. CEX 源端 WebSocket 已实现并完成模拟断线/重订阅测试；DEX 源 WS、全目录轻量行情订阅、完整原始消息诊断录制尚未实现。当前收集关注资产，前端 WS 与源时效独立。
4. 云端代理拒绝官方行情 API。用户已授权添加官方域名，但工具没有环境网络配置写入能力。已整理 NETWORK.md，未修改宿主策略；用户同意先用官方文档/GitHub 构建代码。
5. 未完成真实数据人工核算与真实行情连续运行 24 小时。短时 HTTP 探测不能替代。
6. 机器人凭证和真实消息发送按用户最新要求延期，不阻塞本次模拟通知验证。

## 恢复与下一步

先读 PLAN、CONTRACTS、ACCEPTANCE 和本状态，再运行测试并核对 Git 状态。最靠前开发任务：补齐原生链上路由/区块状态、DEX 源 WS 与广覆盖订阅；网络可达后执行 probe_sources，逐平台拉取真实样本核对金额/周期/单位，再执行 soak.py --hours 24。不要因为当前单元测试通过而勾选完整阶段验收。

最终完整测试 171 项通过，构建与隔离浏览器流程通过。准确命令、截图和短时运行见 VALIDATION.md。进程可能随云环境回收而停止；未承诺会话结束后的持续后台工作。
