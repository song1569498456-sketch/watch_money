# Watch Money · 加密市场监控

本地运行的公开行情看板、可成交收益情景计算器与通知中心。Python/FastAPI 后端、React/TypeScript 前端；不接入交易账户，不执行下单。

**当前为开发验证版本，未宣布三阶段全部验收通过。** 官方接口适配与离线测试持续完善；真实网络联通、全部协议原生能力和 24 小时运行验收请见 [STATUS](docs/STATUS.md)、[平台覆盖](docs/COVERAGE.md)、[独立验收](docs/ACCEPTANCE.md)。通知按本次用户要求仅进行模拟测试。

## 快速启动

安装 Python 3.11+、Node.js 20.19+，克隆仓库。

Windows 双击 `start.bat`；或运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Linux/macOS：

```bash
bash scripts/start.sh
```

打开 <http://127.0.0.1:8000>；API 文档在 <http://127.0.0.1:8000/docs>。脚本安装依赖、构建前端，并创建本地 `.env`。服务默认仅监听本机。Linux VPS 可使用同样步骤，通过 SSH 端口转发访问；对外发布前自行配置认证和 TLS。

默认真实模式不会生成模拟行情。网络受限时看板显示空状态及采集错误。设置页可以明确开启演示模式；演示数据带标识，不写入真实历史，不提供可成交盘口，也不触发自动机会监控。

## 使用

- 市场：搜索、按资产分组，筛选平台/链/产品，收藏与排序；详情显示资金费原值/实际周期/8h归一化、盘口和历史。
- 平台：查看连接状态、重试、缺失字段和覆盖能力。只有实际提供的字段才有数值。
- 计算：输入基础资产数量（可批量）、买卖市场、费用和持有期；加载盘口后按深度计算均价。不同报价币需要带来源与时间的汇率，缺失成本不会作为零。
- 持续监控：保存计算参数后，按设定间隔重新取得盘口并评估，结果可触发通知。已有报价不会通过更新时间伪装成新报价。
- 通知：默认本地通知中心；可配置规则、查看队列、重试和测试。飞书/Telegram 凭证只放 `.env` 或私有环境变量，本次没有实发。
- 资产映射：仅少量明确资产自动确认，未知同名资产保持隔离；`PUT /api/assets/mappings` 支持人工确认并留审计记录。

SQLite 保存配置、身份映射、机会样本和通知状态；历史行情以 Parquet 按日期与平台分区，通过 DuckDB 查询。数据位于 `data/`，不提交 GitHub。迁移前停止服务，再整体备份数据目录。

## 开发与测试

```bash
python -m venv .venv
# Windows: .venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
cd frontend
npm ci
npm run build
```

开发前端运行 `npm run dev`，Vite 将 `/api` 与 `/ws` 转发到 8000 端口。后端开发可运行 `python -m uvicorn watch_money.app:app --reload --env-file .env`。

真实接口探测和长时观测：

```bash
python scripts/probe_sources.py
python scripts/soak.py --hours 24 --output data/soak.jsonl
```

短时观测与模拟测试不能证明真实行情连续运行 24 小时。官方域名清单见 [网络配置](docs/NETWORK.md)。在受管云环境中修改项目内文件不能改变宿主平台的网络策略。

详细说明：[架构](docs/ARCHITECTURE.md) · [计算语义](docs/CALCULATIONS.md) · [通知](docs/NOTIFICATIONS.md) · [验证](docs/VALIDATION.md) · [开发计划](docs/PLAN.md)。
