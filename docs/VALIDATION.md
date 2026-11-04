# 验证记录

日期：2026-11-04。环境：托管 Linux 云工作区，Python 3.12、Node 24、Chromium。所有操作都是只读行情监控，没有交易执行。

## 自动测试

最终完整测试 **171 项通过**，结果见 [pytest.txt](evidence/pytest.txt)，其中包含 31 项独立验收测试。CEX 源 WS、Jupiter 公共状态与 Drift 原程序只读元数据均已纳入回归。Ruff 检查、TypeScript/Vite 构建、隔离浏览器端到端测试均通过。

```bash
.venv/bin/pytest -q
.venv/bin/ruff check backend tests scripts --select E4,E7,E9,F
cd frontend && npm ci && npm run build
```

覆盖资金费正负与异周期、同时间结算回本、资金费名义价值、合约乘数、过期字段、盘口规模与不足、完整开平仓费用、汇率、DEX 精确路线与费用不重复、时序回放、映射审计、历史/配置恢复、持久化队列、重试、去重、关闭开关、自动监控失效，以及并发保存容量与无丢失更新。

独立验收与具体复现见 [ACCEPTANCE.md](ACCEPTANCE.md)。上游 Starlette/httpx 有一条弃用提示，不影响当前测试结果。

## 浏览器实测

```bash
.venv/bin/pip install -e '.[browser]'
# 本云环境已提供 /usr/bin/chromium；其他环境先 python -m playwright install chromium
.venv/bin/python scripts/browser_smoke.py
```

脚本创建隔离临时数据目录和临时服务，不修改用户数据，移除外部消息凭证。实际验证：真实模式空状态、显式演示开关、搜索、收藏持久化、资产详情、无执行深度时净收益为空、保存/回放、持续监控创建、本地通知入队与送达、390px 手机宽度无横向溢出。五个页面未出现 JavaScript 错误。

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
