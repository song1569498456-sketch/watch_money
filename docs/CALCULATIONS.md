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

DEX 报价币还必须有具体代币身份：`Instrument.quote_asset_id` / `native_quote_asset_id` 使用完整 `chain:address`，并提供可追溯的 `quote_identity_source` 与毫秒观察时间 `quote_identity_at`。EVM 地址规范为小写，Solana 区分大小写；链不符、原生与映射地址冲突、未知/过期/未来观察时间或仅写“verified”均不能作为报价币证明。这里的时间是代币元数据观察时间，不伪装为价格事件时间。`RouteQuote.quote_asset_id` 还须与该市场已确认的报价币一致，不能由手填路线自行替代市场身份证据；手动、演示和 `snapshot.valid=true` 都不豁免。

静态报价币证据可提供 `quote_identity_expires_at`，须晚于观察时间且最长不超过观察后 6 小时；这是本地元数据审核期限，不是交易所保证。到期立即失效；未声明期限时使用请求的 `stale_seconds`。聚合 DEX 目录只在实际收到完整 `quoteToken.address` 时记录独立观察时间及受限期限，价格 tick 不续期。重发现通常每 1800 秒运行，因此不将静态代币证据硬套为每 60 秒重新确认，也不拿新价格掩盖过期身份。历史回放还按 information cutoff 排除后来才观察到的身份。

该合约身份证据适用于已支持链上的现货报价代币。永续的 `quote=USD` 可能只是定价单位，`settle=USDC` 可能是另一个结算单位，不能从两个符号猜出同一 USDC 合约。Hyperliquid、dYdX 等协议内部定价/抵押/结算单位尚未完整接入本计算器的可核验身份模型时，测算会明确保持缺条件/未知；不会把其真实费率采集或指示价展示描述为已支持完整可执行净利润。本轮没有扩大原生协议适配范围，也不保证全部 DEX 产品已能测算净值。

同链同合约且证据完整的报价币可按相同单位计量；合约不同或 CEX 账面币与链上代币之间，必须额外提供 `FXRate.base_asset_id` / `quote_asset_id`，严格绑定两端具体身份，保留新鲜时间及可追溯来源。CEX 账面单位表示为 `cex:<venue>:<quote>`，例如 `cex:okx:USDT`；相同“USDT”符号不是这个换算。默认估值取与 `valuation_currency` 对应的买腿报价身份，否则取卖腿；第三链上计价币必须使用完整 `chain:address`，不能凭一个 `valuation:USDC` 标签造身份。明确法币计量单位使用 `fiat:USD` 等标识，也要求绑定换算，不隐含稳定币锚定。逐腿及报价币 Gas 费用沿同一已证实换算计量，避免毛收益换算而费用仍按假定 1:1 扣除。

修复前合成复现：两条同名 USDT 的手动池路线，买入 100、卖出 105、每腿 Gas 0.1，会在缺失报价代币身份时输出净 4.8；独立零 Gas 样本输出净 5.0。现在两者都拒绝净收益，即使删掉来源的缺失警告也不能绕过。测试中证据完整的同链同代币正例仍可得 4.8/5.0；这些是合成模型验证，不是实际录制交易或实网验收。

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

若提供 `amount_precision`，它表示正数数量步长（TICK_SIZE），现货按基础数量、永续按合约张数检查完整成交量。永续即使使用 `base` 盘口，也会先按合约乘数和必要的成交价格换算张数。不满足步长的请求直接失效，不会自动舍入后展示净收益。

`min_amount` 是独立的最小下单数量，CEX 发现读取 `limits.amount.min`，与 `min_notional` 的报价币名义金额门槛分开。现货按基础数量、永续按换算后的张数校验；低于最小数量的请求失效。Deribit 的 CCXT 数量限额和精度使用原生基础币/报价币单位，适配器与盘口一样先除以 `contract_size` 统一为张数。

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

预置库存路径假设两地已有资产及足够抵押。`inventory_path=transfer` 要求 `transfer_cost`，并提示转账期间价格未锁定。持有且卖出腿为现货时必须提供其 `holding_rate_per_hour`。双腿任何明确输入的每小时持有费（含永续、现货多腿）都会按各自入场名义金额×每小时费率×持有小时累计，按各自报价币的明确 FX 换算一次。未输入的永续/现货多腿持有费不自动增加未知收费；短现货借贷仍是必填成本。负持有费率不接受，资金费收入另算。即时现货策略不接受非零持有费，避免静默忽略不适用输入。AMM/RFQ 持有模型仍明确不支持，填入费率不会绕过它。

`holding_cost` 是双腿持有费合计，`borrowing_cost` 只列短现货借贷子项，不再重复扣除；逐腿来源/时间/条件和原币种见 `holding_costs`，主持有期费用也加入 `costs`。各期限情景独立累计小时费用，资金费 half/zero/reverse 情景不缩放持有费；盈亏平衡在每个结算点扣除截至该时刻已累计的持有费。未来才可知的费用配置阻止净收益；未填 `at` 的明确费率按手动用户假设使用并标注，不能冒充已核验账户费率。其他附加费用由用户明确列出；系统不能声称已验证真实账户收费等级或资金可用性。

`breakeven_hours` 是资金费与持续持有费路径上的首次回本时间。零入场费用不自动表示 0 小时回本；全程没有正净余额的持有情景保持 `null`。已经有明确正入场净额（例如净返佣）时可为 0；若此后持有费或资金费导致余额再次为负，会在假设中标明，不将首次回本宣称为持续盈利。

持有策略基础资产数量为 q，买腿为多、卖腿为空：

`basis_profit = q × [(long_exit − long_entry) + (short_entry − short_exit)]`

各价格先按明确 FX 换算。必须提供两腿 `exit_prices`（原报价币每基础资产的假定可成交价），或显式 `assume_exit_at_entry=true`。后一选项固定两腿退出为各自入场 VWAP，即基差损益 0，不能把初始基差当必然兑现利润。也可提供 `exit_basis_quote`（估值币/基础资产），此时固定多腿退出为其入场 VWAP，再由目标退出基差推导空腿退出；响应明确记录该假设。退出假设不是未来可成交报价。

## 资金费事件与情景

正费率下多头付款、空头收款；负费率方向反转。每笔事件：

`cashflow = (+1 short / −1 long) × funding_notional × raw_rate`

默认 `funding_notional_basis=mark`：需要带新鲜来源时间的 `snapshot.mark`，并明确假设未来各结算标记价保持该值。名义金额为 q×mark×FX，执行 VWAP 不替代结算标记价。显式选择 `entry_vwap` 才允许以入场 VWAP 代替未来标记价的估算情景。真正未来结算标记价变化仍未知，因此显示为情景，不承诺实际结算收益。

规则时间表必须同时有 `funding_interval_hours` 和未来的 `next_funding_at`，按真实起点逐笔增加周期，只计 `(as_of, as_of + hold]` 内事件。不用八小时归一化费率直接乘持有期。`settled` 是历史已结算记录，不能用作当前/预测费率外推。

不等间隔时间表可输入 `funding_events=[{instrument_id,settlement_at,rate,kind,known_at,source}]`，并用 `funding_schedule_complete_until` 声明各市场完整计划的终点。`funding_schedule_known_at` 默认请求原始 `as_of`。只有声明覆盖整个持有期时，才能将没有事件的区间算为 0；缺失周期不会被猜成无结算。每个结算点使用当时已知的最新估计。完整计划及覆盖规则结算点的部分预测都要求最新估计的 `known_at` 未超过 `stale_seconds`；过期预测会使资金费和净收益不可用，未来才可知的预测不参与计算。已结算费率必须在结算发生后才能被标记为已知。

固定输出 8、24、72 小时及自定义 `hold_hours`，每个时长四种情景：维持原费率、逐笔减半、归零、反转。分别列出资金费、基差损益、完整费用、借贷成本和净值。费率情景不会覆盖真实结算记录。

回本时间只在明确假设基差不变、费用和持有费已知时计算；同时间资金费事件先合并，检查入场、各结算前后和期末的资金费减持续持有费余额，搜索自定义持有期内首次回本。全程无正余额或期内未回本则 `null`；路径可有正负资金费，回本后再亏损会单独提示，不按 headline 年化费率硬算。

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
