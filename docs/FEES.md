# 公开普通等级费用配置

`GET /api/fees` 覆盖 28 个平台 × spot/perpetual × maker/taker 共 112 个独立档案。没有核对到公开依据的费率保持 `null`；未声明的产品明确 `not_applicable`。费用档案不代表该产品已完成实时行情接入。费率是小数，0.001 表示 0.1%，已明确的零费率与未知不同。用户覆盖允许 maker 返佣等负费率，数值范围与计算器 `LegCost` 一致：−0.1 至 1。

资料核对时间为 2026-11-04，证据保存在 `adapters/public_fees.json`。这些是公开普通等级参考，不是登录后账户实际费率；没有调用账户 API。默认不叠加 VIP、平台币、返佣、邀请、质押或活动优惠。

| 平台 | Spot maker / taker | Perpetual maker / taker | 依据与范围 |
| --- | --- | --- | --- |
| OKX | 0.08% / 0.10% | 0.02% / 0.05% | [标准现货说明](https://www.okx.com/learn/how-to-use-okx)、[Lv.1 合约说明](https://www.okx.com/help/how-to-calculate-the-contract-transaction-fee)；自动合约参考仅限 USDT 线性 |
| Binance | 0.10% / 0.10% | 未确认 | [Regular User 表](https://www.binance.com/en/fee/trading)；不套 USDC 特殊表。[合约页面](https://www.binance.com/en/fee/futureFee)本轮返回 No records found，不填传闻常数 |
| Bybit | 0.10% / 0.10% | 0.02% / 0.055% | [VIP 0 表](https://www.bybit.com/en/help-center/article/Trading-Fee-Structure)；特殊区、法币及地区不同，市场元数据无法确认时不自动应用 |
| Bitget | 0.10% / 0.10% | 0.02% / 0.06% | [标准基础费率](https://www.bitget.com/support/articles/12560603892734)；合约自动参考限 USDT 线性，不含 VIP/BGB 优惠 |
| Hyperliquid | 0.04% / 0.07% | 0.015% / 0.045% | [Tier 0 基础表](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)；普通 USDC 原生永续，不套 HIP-3/deployer；spot 的特殊 quote 调整需另外核对 |
| Lighter | 0 / 0 | 0 / 0 | [Standard Account 普通订单](https://docs.lighter.xyz/trading/trading-fees)；不含高级订单、TWAP、RFQ、二元期权、Plus/Premium/Advanced |

其他平台和产品保持未知。AMM 费率随池/版本变化，没有填统一协议费率；计算器已有 route 内嵌交易费处理，不能再重复扣 maker/taker 费用。

## API 与作用域

每行包含 `key`、`venue`、`kind`、`liquidity`、`applicable`，以及 `default`、`override`、`effective`。各值保留 `rate`、`source`、`conditions`、`fee_currency`、`tier`、`source_verified`、`evidence_status`、`scope`。未核对的扣费币保持空，不能从费率自行推断折扣币或链上 Gas。

`updated_at` 是来源提供的更新日期，来源没有给出时为 `null`；`checked_at` 记录本轮公开资料核对时间。GET 不刷新这两个时间。用户保存另记 `saved_at`；显式传入的旧 `updated_at` 原样保留，未来证据不可在当前决策中使用。

计算器应调用 `GET /api/fees?instrument_id=<id>`，或 `FeeProfileService(store).resolve(instrument, liquidity)`。此时按报价币、结算币、线性/反向与 namespace 检查 `scope`；不匹配时 `effective.rate=null`、状态 `out_of_scope`，原始 default 仍可审阅。Bybit 特殊区和 Hyperliquid spot quote 调整无法从当前市场模型判定，公开参考展示但不自动用于该市场。手动填写明确费率属于未核验用户假设，可覆盖这类 review 缺口，仍遵守声明的币种/合约范围。

## 单档案修改与审计

```json
{
  "venue": "okx", "kind": "spot", "liquidity": "maker",
  "rate": 0.0008, "source": "user:explicit fee assumption",
  "conditions": "No discount assumed", "fee_currency": null,
  "updated_at": 1793750400000
}
```

`PUT /api/fees` 只修改一个 venue/product/role。覆盖永远标记 `user_override`、`source_verified=false`；给一个 URL 不能把用户输入升级为官方核验。`rate:null` 是明确覆盖为未知，不能退回已知默认。恢复该行公开默认用 `{"venue":"okx","kind":"spot","liquidity":"maker","reset":true}`。

覆盖存于本地 SQLite settings KV `fee_profiles`；更新和 audit 在同一事务中保存。审计记录之前/之后的覆盖与生效值，重启保留；GET 返回最近 500 条费用审计，不包含其他业务审计。平台、产品和 maker/taker 之间互不影响。页面加载、切换市场或读取配置不会创建覆盖，也不会把旧来源时间改成当前时间。

集成只需 `app.include_router(fee_profiles.router)`，应用已有 `app.state.store`。instrument 查询还使用 `app.state.collector.instruments`，独立测试应用可从 Storage 读取。费率自动加载只填当前匹配产品/角色的有效费率；未知保留空，用户未主动保存不能 PUT 默认配置。

验证：`.venv/bin/python -m pytest tests/test_fee_profiles.py -q`。测试覆盖未知不猜、真实零值、单档案隔离、返佣、审计/重启/事务回滚、恢复默认、未来与旧来源时间、scope 拒绝及禁止客户端伪造核验标记。
