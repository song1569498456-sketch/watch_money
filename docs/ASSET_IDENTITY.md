# 全目录资产身份与归集候选

`identity_candidates(instruments)` 是纯函数，接收完整发现目录的 `Iterable[Instrument]`，
返回可直接 JSON 序列化的列表；不按 BTC/ETH 或任何主流币白名单过滤，不修改输入或执行自动映射。
API 可提供 `{"candidates": identity_candidates(collector.instruments.values())}`。
每个目录市场出现在一个 `kind=identity` 行中；同 ticker 跨多个身份时额外生成
`kind=ticker_candidate` 行供人工核对。后者是重复呈现的审核入口，不能作为额外流动性统计。
稳定行 ID 不依赖输入顺序；重复 instrument ID 直接报错，避免吞掉冲突证据。

归集依据按以下顺序使用：

1. 已确认 `mapping_verified=True` 的 `asset_id`；同资产、同基币单位的多 CEX 市场可归为一组。
2. 经格式验证的 `chain:address`，限当前支持的 EVM 链和 Solana 地址；EVM 地址忽略大小写，
   Solana 地址严格区分大小写。链标识不做别名合并。
3. 其他 ID 维持 venue 命名空间，仅作为未确认候选，ticker 相同不能证明同资产。

`native_asset_id` 应在首次发现并持久化时保留原始 `asset_id`，后续人工映射不能覆盖。
函数优先从该字段提取原生链/地址证据；旧目录缺少字段时退回当前 `asset_id`。
即使人工把两个地址映射到同一个新 ID，原生地址、链或单位冲突仍会阻止比较。
旧数据如果已丢失原始身份，需要重新发现恢复证据；不能从当前 symbol 猜回来。

输出字段：

- `symbols`、`instrument_ids`、`venues`、`asset_ids`、`quotes`：目录中的实际身份和报价币。
- `markets`：逐市场的 base/quote/settle/kind/chain、原生身份、证据来源、地址、
  `contract_size`、linear、active 和 mapping_verified。
- `identity_compatible`：是否有可信一致的资产身份，排除不同原生链/地址、单位和元数据冲突。
- `comparison_ready`：资产身份门槛已满足；跨平台仍需明确确认，保持现有 calculator 契约。
  **它不保证报价、深度、费用、时效、可转账性或策略本身已满足。**
- `blockers`：未确认映射、不同身份/链/地址、不同基币符号、链元数据矛盾等可机器读取的原因。
- `requirements`：不同 quote 要求带时间的 FX；非 1 合约乘数要求数量换算；反向合约要求对应换算。
  contract_size 是合约数量乘数，不能作为 PEPE/1000PEPE 价格缩放证据。
- `warnings`：非活跃市场、模型尚无 quote token 合约身份等需要额外核对的条件。

不去掉 `1000` 等数字前缀、不移除 `W`、`.e` 等符号部分，不把 PEPE/1000PEPE、
ETH/WETH、USDC/USDC.e、相同地址在不同链上的部署视作同一可计算基币。
已确认映射中的多个不同 base 符号也先阻止比较，因为目前模型不能表达包装/桥接关系或价格单位比例。
唯一显式同单位别名是 BTC/XBT，且必须所有市场都已确认到同一 asset_id；它不会放行包装币或单位缩放。
quote 不同不会改变基币身份；KRW、USD、USDT、USDC 都保留，不隐式按 1:1 换算。

后续需要的明确证据字段是不可变 `native_asset_id`、经核验的 `asset_address`、
资产关系/桥接来源，以及独立于 contract_size 的 `base_unit_multiplier`。
在没有官方元数据或人工审计证据前，不填造这些字段，不自动把热门币的 ticker 当全球身份。
本函数输出供 API 和批量人工映射审阅；最终计算仍必须运行策略模块的身份、报价和执行校验。
报价身份提示按 as_of 核对明确的 chain/address、来源和观测证据；完整有效证据不再一律提示缺失。
没有显式到期时间、仅超过默认检查窗口时，使用 quote_identity_requires_freshness_check，
交由策略按实际 stale_seconds 判断，而不是把已提供字段说成“未提供”。
明确的元数据到期用 quote_identity_evidence_expired；缺失/格式/冲突才保留 unavailable 提示。

验证：`.venv/bin/python -m pytest tests/test_identity.py -q`。
