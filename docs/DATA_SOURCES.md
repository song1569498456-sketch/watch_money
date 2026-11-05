# 数据来源与语义

官方文档/官方 SDK 的代码核对与线上行情核对是两类证据。当前已核对公开规范并执行样本测试，尚不能宣称云端可访问所有实际 API。

- [CEX 官方接口、限流、周期与单位](CEX_SOURCES.md)
- [DEX 官方接口、部署、费用机制与缺失能力](DEX_SOURCES.md)
- [平台与产品覆盖表](COVERAGE.md)
- [网络域名与实测阻塞](NETWORK.md)

## 统一字段

`source` 标记直接或聚合来源，`field_sources` / `field_timestamps` 保留各字段来源与事件时间；`source_at` 与 `received_at` 分开保存。未提供字段保持 null，界面显示未提供；更新页面不会重置源数据年龄。示例数据永远携带 `is_demo=true`。

`funding_rate` 是小数原值，`funding_kind` 区分 settled/current/predicted；只有已知 `funding_interval_hours` 才提供 `funding_8h`。实际结算时点逐次计算，连续计提或 AMM 借贷费不伪装成周期资金费。字段可能不同步，因此计算使用字段自己的时间判断过期。

成交量和 OI 保留单位；CEX 合约盘口通过原始市场元数据解释数量。聚合池价格是 indicative，不拥有买卖盘口，不得用流动性金额推导可执行容量。路线输入输出已经计入池费和价格冲击时，不再重复扣减。

## 运行状态与凭证

公开行情不需要用户交易账户。HTTP 代理继承环境，TLS 校验保持开启。可选外部通知只读取私有环境变量；本次只模拟测试。RPC、需要身份认证的 RFQ 和受限历史服务列入缺失条件，不生成替代的假数据。
