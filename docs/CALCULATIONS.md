# 第二阶段计算口径与 API

本模块只做公开数据与用户成本假设的计算，不验证账户余额，不下单。`strategies.calculate(CalculateRequest)` 是不访问全局行情、不联网的确定性函数。金额原始币种、报价来源、事件与接收时间、FX 来源、DEX 区块及路线、演示标记随结果保留。未知数保持 `null`。

## 请求与状态

`POST /api/opportunities/calculate` 接收：

```json
{
  "instruments": [], "snapshots": [], "orderbooks": [], "route_quotes": [],
  "buy_instrument_id": "venue-a:BTC/USDT", "sell_instrument_id": "venue-b:BTC/USDT",
  "size_base": 0.01, "sizes_base": [], "strategy": "spot_spread",
  "valuation_currency": "USDT", "fx_rates": [],
  "costs": [
    {"instrument_id": "venue-a:BTC/USDT", "open_fee_rate": 0.001,
     "close_fee_rate": 0.001, "source": "用户费率假设", "at": 1800000000000}
  ],
  "extra_costs": [], "hold_hours": 24, "assume_exit_at_entry": false,
  "funding_notional_basis": "mark", "exit_prices": {},
  "funding_events": [], "funding_schedule_complete_until": {},
  "inventory_path": "prepositioned", "stale_seconds": 60, "as_of": 1800000000000
}
```

`Instrument` / `MarketSnapshot` / `OrderBook` 使用共享模型；需要为买卖两腿提供元数据，资产身份必须相同，跨平台映射须经确认。`size_base` 是基础资产数量而非美元本金；`sizes_base` 非空时批量比较多个规模。美元、USDT、USDC 不默认等价。跨币种时提供 `fx_rates=[{base,quote,rate,source,at}]`，可自动反向使用直接汇率，不猜测交叉路径；过期或未来 FX 不使用。

响应为 `{opportunities:[...]}`。每条包含 `legs`（VWAP、逐档成交量、完整/部分成交、容量、报价币种、来源时间、FX）、`costs`（原币成本及换算值）、`gross_profit`、`known_costs`、`net_profit`、`basis_profit`、`funding_profit`、`scenarios`、`funding_events`、`assumptions`、`missing_prerequisites`、`invalid_reasons`、`is_demo`。

| 状态 | 含义 |
| --- | --- |
| `data_incomplete` | 缺盘口、报价、FX、资金费计划、标记价或退出假设 |
| `cost_pending` | 可算毛收益，必要费用尚未配置 |
| `watching` | 完整计算，净收益低于输入阈值 |
| `threshold_met` | 完整计算，净收益达到输入 `threshold`，默认 0 |
| `invalidated` | 身份不符、失效市场、过期数据、非法盘口或不一致路线 |

非法数字/负规模/交叉盘口等共享模型校验返回 HTTP 422。状态不是可成交保证。过期判断同时看源时间与本地接收时间，资金费和标记价另外检查 `field_timestamps`；新 ticker 不会使旧资金费变新。

## 七种分类

| strategy | 允许的输入与计算 |
| --- | --- |
| `spot_spread` | 两个现货/池子，卖出收入减买入支出减两腿交易成本 |
| `spot_perp_basis` | 一现货一永续，持有价格损益、资金费、完整开平费用 |
| `perp_spread` | 两永续，持有价格损益、资金费、完整开平费用 |
| `funding_spread` | 两永续，按结算事件展示资金费差，同时保留基差与退出损益 |
| `funding_basis` | 至少一永续，资金费和基差联合持有情景 |
| `cex_dex_spot` | 一个链上现货与一个无链的交易所现货，需要规模匹配的路线报价 |
| `dex_pool_spread` | 同一明确链上的两个池子/现货路线，要求同区块、底层流动性独立 |

后两类不会把聚合器和其底层池计成独立流动性。路线有交集时，必须另行提供联合原子路线模拟；当前拒绝独立腿估算。

## 盘口、合约与 DEX

买入逐档取 asks，卖出逐档取 bids。`quote_amount = Σ(price × filled_base)`，`average_price = quote_amount / filled_base`。只提供 ticker 的 bid/ask/last 不足以计算指定规模的可成交结果。深度不足时保留部分成交明细与已知容量，但完整毛/净收益为未知；不把部分成交当完整套利。

`quantity_unit=base` 直接使用基础资产数量。`contracts` 时，线性 `base = contracts × contract_size`；反向 `base = contracts × contract_size / price`，仅当反向结算币明确为基础资产时支持该盘口换算。线性持有计算要求明确 `settle=quote`；缺结算币或不同结算币涉及尚未建模的转换/quanto 口径，净收益保持未知，USD 与 USDC 不会自动等同。反向合约未来币本位盈亏与资金费需要结算价格路径，当前持有净收益明确不可计算。合约单位不能由 ticker 推断。

DEX 输入 `RouteQuote`：

```json
{
  "instrument_id": "pool-id", "side": "sell", "input_asset": "BTC", "output_asset": "USDT",
  "input_amount": 0.01, "output_amount": 1000,
  "route": ["chain:protocol:pool-address"], "block_number": 123,
  "source": "size-specific quote", "source_at": 1800000000000, "received_at": 1800000000100,
  "fees_included": true, "gas_amount": 0.1, "gas_currency": "USDT", "is_demo": false
}
```

买入路线输出数量或卖出路线输入数量必须等于请求规模，不能按比例放大旧路线。路线价格冲击和交易费已经在输入/输出数量内，不再次扣除；Gas 单列，缺 Gas 或币种时净收益未知。持有 AMM/RFQ 的退出及协议借贷模型未实现，当前明确列出前置条件。

## 成本、退出和库存

`costs` 输入各市场有效开/平费率（小数，如 0.001=0.1%），包括账户折扣后的用户假设，并保留来源、时间及条件。没有开户费率不会默认为 0；已知成本独立求和，任何必要成本未知则净收益为 `null`。现货即时比较只有两腿成交费；持有策略包括两腿的开仓及平仓费。按预计平仓价格计算平仓名义金额。`extra_costs=[{name,amount,currency,source,at}]` 可列额外执行、Gas、桥接或库存再平衡费用，`amount:null` 表示待配置。

预置库存路径假设两地已有资产及足够抵押。`inventory_path=transfer` 要求 `transfer_cost`，并提示转账期间价格未锁定。持有且卖出腿为现货时必须提供其 `holding_rate_per_hour`；借贷成本为入场卖出名义金额×每小时费率×持有小时。其他附加费用由用户明确列出；系统不能声称已验证真实账户收费等级或资金可用性。

持有策略基础资产数量为 q，买腿为多、卖腿为空：

`basis_profit = q × [(long_exit − long_entry) + (short_entry − short_exit)]`

各价格先按明确 FX 换算。必须提供两腿 `exit_prices`（原报价币每基础资产的假定可成交价），或显式 `assume_exit_at_entry=true`。后一选项固定两腿退出为各自入场 VWAP，即基差损益 0，不能把初始基差当必然兑现利润。也可提供 `exit_basis_quote`（估值币/基础资产），此时固定多腿退出为其入场 VWAP，再由目标退出基差推导空腿退出；响应明确记录该假设。退出假设不是未来可成交报价。

## 资金费事件与情景

正费率下多头付款、空头收款；负费率方向反转。每笔事件：

`cashflow = (+1 short / −1 long) × funding_notional × raw_rate`

默认 `funding_notional_basis=mark`：需要带新鲜来源时间的 `snapshot.mark`，并明确假设未来各结算标记价保持该值。名义金额为 q×mark×FX，执行 VWAP 不替代结算标记价。显式选择 `entry_vwap` 才允许以入场 VWAP 代替未来标记价的估算情景。真正未来结算标记价变化仍未知，因此显示为情景，不承诺实际结算收益。

规则时间表必须同时有 `funding_interval_hours` 和未来的 `next_funding_at`，按真实起点逐笔增加周期，只计 `(as_of, as_of + hold]` 内事件。不用八小时归一化费率直接乘持有期。`settled` 是历史已结算记录，不能用作当前/预测费率外推。

不等间隔时间表可输入 `funding_events=[{instrument_id,settlement_at,rate,kind,known_at,source}]`，并用 `funding_schedule_complete_until` 声明各市场完整计划的终点。`funding_schedule_known_at` 默认请求原始 `as_of`。只有声明覆盖整个持有期时，才能将没有事件的区间算为 0；缺失周期不会被猜成无结算。每个结算点使用当时已知的最新估计。已结算费率必须在结算发生后才能被标记为已知。

固定输出 8、24、72 小时及自定义 `hold_hours`，每个时长四种情景：维持原费率、逐笔减半、归零、反转。分别列出资金费、基差损益、完整费用、借贷成本和净值。费率情景不会覆盖真实结算记录。

回本时间只在明确假设基差不变、所有费用已知、无未建模借贷、同一结算时间净现金流均非负时显示；按同时间事件合并后逐笔累计找到首次覆盖往返费用的实际结算点，只搜索自定义持有期。不能满足口径或期内未回本则 `null`，不按 headline 年化费率硬算。

## 保存、回放与通知

- `POST /api/opportunities/saved`：`{name,calculation:<完整请求>}`，保存原始输入及确定性结果；返回 `{id,name,created_at,calculation,result}`。
- `GET /api/opportunities/saved`：`{samples:[...]}`，最近最多 500 条。
- `POST /api/opportunities/replay`：`{sample_id,as_of,receive_delay_ms:0,operation_delay_ms:0}`。

SQLite 路径使用应用 `data_dir`，否则 `WATCH_MONEY_DATA_DIR`，默认 `data/opportunities.sqlite3`。回放的 `as_of` 是模拟执行时刻，信息可见截止点为 `as_of − receive_delay_ms − operation_delay_ms`。之后接收的盘口/快照、字段时间、路线、FX、费率来源、尚未公布的资金费事件及计划不会参与。输出 `replay.information_cutoff`。回放使用保存时的静态市场身份与用户退出/成本假设，不能代替完整历史撮合或验证未来成交；本模块不回读未来行情。

`opportunity_event(opportunity, calculation)` 为手工计算及监控统一生成通知事件：缺净收益时 `valid=false`；保留最旧相关行情来源时间、平台、资产、基础资产容量及单位、估值币。`funding_spread` 是带多空符号的八小时归一化费差（fraction_per_8h），仅用于规则比较；美元 `funding_profit` 始终按逐笔事件计算。应用计算/通知开关会被遵守。保存和回放接口本身不发通知。

关闭 `computation_enabled` 后 `/calculate`、`POST /saved` 和 `/replay` 均返回 HTTP 409；`GET /saved` 仍可查看已有记录。

## 人工核对与验证边界

以下为带固定时间的合成回归样本，**不是实时市场样本**：

- 买入 1 BTC 的 asks 为 100×1、102×9；另一市场 bids 为 105×1、103×9。1 BTC 毛收益为 5，双方有效费率 0.001，费用 0.100+0.105=0.205，净 4.795。2 BTC 买 VWAP=101、卖 VWAP=104，毛 6、费 0.410、净 5.590；11 BTC 深度不足。
- 8h 持有：多腿 q=1、mark=100、费率 0.001、下一结算 +1h、周期 8h；空腿 mark=105、费率 0.002、下一结算 +1h、周期 4h。多腿只在 +1h 付 0.1，空腿在 +1h、+5h 各收 0.21，总资金费 +0.32。两腿开费率 0.001、平费率 0.002，退出价各为入场价：往返费用 (100+105)×0.003=0.615，基差损益 0，净 −0.295。半费情景资金费 0.16；归零情景净 −0.615；反转情景资金费 −0.32。

`tests/test_strategies.py` 覆盖规模、部分成交、四笔费用、费率正负号/周期/不规则结算、退出假设、标记价、FX 溯源、合约乘数/反向拒算、Gas 不重复扣费、过期字段、未知成本、SQLite 重复性及无前视回放。真实样本的人工对照、真实账户费率及持续运行验收需另行记录，不能用这些合成样本替代。
