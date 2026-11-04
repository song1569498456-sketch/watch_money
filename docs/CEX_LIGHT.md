# CEX 广覆盖轻量行情

`CexAdapter.fetch_light(instruments: list[Instrument]) -> list[MarketSnapshot]`
接受 collector 从已发现目录挑选的一批市场，返回只包含 ticker 已提供字段的补丁。
全目录轮转、30 秒价格调度、暂停和重试退避由 collector 负责。
`fetch_funding_light()` 独立覆盖永续目录的轻量资金费；关注资产的 `fetch()` 详细行情路径继续提供详细行情与 OI。

## 请求与能力边界

- 按市场 ID 去重后每次最多 100 个市场，超限或未发现身份会在任何行情请求前报错；不静默截断。
- 按 CCXT client、现货/永续、结算币、正反向分组。Binance 等独立服务不混合，Deribit 另按 base 分组，其 summary 方法只能接受一种 base。
- `client.has['fetchTickers'] is True` 才使用原生 `fetch_tickers(symbols, params)`，每批最多 50 个 symbol。显式传 symbol 列表并忽略批次之外的返回项。
- 混合产品 client 显式指定 spot/swap；Bybit、Bitget、HTX 的合约明确指定 linear/inverse；Gate 明确指定结算币；Coinbase 明确 `usePrivate=False`。
- 批量能力缺失、标为 `emulated` 或运行时抛 `NotSupported`，且具备原生 `fetchTicker=True` 时，转为单 ticker。每批单 ticker 最多 4 个并发，与详细采集共用适配器信号量；每次仍最多处理 collector 指定的 100 个市场。发生错误或取消会清理未完成任务。
- 没有可用 ticker 能力时不伪造快照：跳过对应市场，并在本次调用的 `adapter.light_unsupported` 中列出能力缺口。该列表下次调用清空。
- 不调用资金费、资金费周期或 OI 方法，不拉深度，不自动扩大到全目录。
- 单 ticker 请求失败按市场隔离，批量失败按批次隔离，并继续其他产品组；`adapter.light_errors` 按 instrument ID 仅记录异常类型，不暴露可能含凭证的异常文本。部分成功返回可用补丁；没有任何成功请求且发生错误则上抛首个错误，交给 collector 退避。该错误列表每次调用清空。取消时清理未完成请求。

50 是本适配器的 symbol 批次上限，并非宣称所有交易所统一的 API 上限或每批恰好一次 HTTP。
部分 CCXT 方法会返回整个产品目录再筛选、内部分页或触发多次请求；仍使用客户端 `enableRateLimit`。
单 ticker 回退的一轮最多可能产生 100 次公共请求；实际轮转周期取决于交易所限流和请求耗时，不能将 30 秒调度配置当作全目录更新保证。

## 补丁合并与时效

调用方必须使用 `patch.model_dump(exclude_unset=True)` 合并，并逐字段合并 `field_sources`、`field_timestamps`。
只更新有效的正数价格和非负成交量；保留真实零成交量。
现货保留原始 base/quote 成交量单位；永续通过共享 `volume_units.volume_fields()` 按已审查的原始字段确定单位，不把统一 `baseVolume` 无条件当作 base。
OKX 永续优先 `info.volCcy24h` 的 base 数量；只有明确线性合约证据才按 `vol24h * contract_size` 转换，反向合约不会用现价估算历史 base 成交量。
未知衍生品单位保持未提供，已识别值保留币种/合约单位和 rolling_24h 原始来源。详细、轻量与源 WebSocket 共用同一单位规则。
返回空、只含成交量、错 symbol、未返回市场或非有限数值时不刷新对应价格；交叉买卖价保留并标 `valid=False`。

省略的 funding/OI、未提供价格不会变成 null 覆盖。返回补丁的 source 为 `ccxt:<client>:fetch_tickers:<symbol>` 或 `fetch_ticker`，不是 WebSocket。
统一 ticker 时间用于所提供字段，字段来源明确带 `unified_ticker_event_time`，issues 说明各组成字段时间可能不同。
缺时间保持 `source_at=None`、字段时间为 None，不以本地接收时间代替源时间；旧 ticker 时间不会被提升到当前时间。
已有详细字段的时效由 collector 各字段合并规则维护。

## 可选诊断记录

可设置 async `adapter.on_payload(venue, transport, payload)`。在解析之前传递 CCXT 已解码的公共统一 ticker 对象：

- 批量：`transport='rest:ccxt:fetch_tickers'`，payload 为 symbol 到 ticker 的映射。
- 单条与现有详细 `fetch()`：`transport='rest:ccxt:fetch_ticker'`，payload 为统一 ticker。
- 资金费批量/单条/周期元数据：`rest:ccxt:fetch_funding_rates`、`fetch_funding_rate`、`fetch_funding_intervals`，payload 为对应公共统一对象。

该接口不提供请求头、凭证或请求 URL，也不声称保存交易所原始网络字节。回调异常不打断采集，取消信号正常传播。

## 全币种轻量资金费与周期时效

`fetch_funding_light(instruments) -> list[MarketSnapshot]` 过滤到永续交易对，按 client/结算币/正反向分组，去重上限100、原生批量每批50，缺少批量能力时使用最多4并发的单市场公开资金费接口。
不调用 ticker、盘口或 OI；独立于轻量价格任务轮转全永续目录，collector 默认资金费轮转间隔60秒。
Binance 还按选定市场获取公共 funding interval 调整列表，不为列表缺席市场假定8小时。
Bybit/HTX/Binance 显式指定正反向，Gate指定结算币，Bitget指定productType；不使用账户或私有方法。

保留原始 fraction 费率（含0、负数）、current/predicted/settled 类型、明确提供的周期与下一结算时间。
Deribit 是已实现的连续累计/历史窗口费率，不构造未来离散结算时间。
`funding_errors` 按市场列异常类型，`funding_unsupported` 明示能力缺口；缺失返回记 `MissingFundingRate`，非有限费率补丁标 `valid=False`，不填0。
部分请求成功时保留正常补丁；全部资金费请求失败时抛首错用于退避。Binance周期接口单独失败时仍收集费率，但记录 `funding_interval:<异常类型>` 并保持未知周期。
现有详细 `fetch()` 也隔离单市场失败，通过 `fetch_errors` 明示部分结果，全部失败才上抛。

周期可能来自目录里的静态合约元数据，不能借最近一次资金费请求让它“变新”：

| 来源 | `field_timestamps['funding_interval_hours']` | `field_received_at['funding_interval_hours']` |
| --- | --- | --- |
| 目录market.info | None，未知交易所事件时间 | 该份目录成功收到的真实时间，失败/资金费轮询不刷新 |
| 独立公共周期接口 | 若提供，使用交易所事件时间；否则None | 周期接口本次实际接收时间 |
| 资金费payload内明确周期 | 若提供，使用资金费事件时间；否则None | 本次实际接收时间 |

目录来源严格以 `:catalog_metadata_observed_at` 结尾；公共元数据缺事件时间时严格以 `:public_metadata_observed_at` 结尾。
issues 明示“metadata observation time，不是 exchange event time”。策略可仅对该明确标记的周期字段使用显式记录的观察时间及6小时TTL；缺观察时间仍不可当作新鲜。
raw funding、mark、next funding 的 freshness 继续检查交易所源时间，不能用元数据TTL或接收时间替代。
因此“存在真实费率数值”不等于“当前可安全计算”：缺源时间、周期过期、周期未知、已过结算时间仍由质量/策略层阻止计算。

## 全交易对目录与部分失败

目录保留每个 client 的所有有效现货和永续交易对，不以 BTC/ETH/SOL 或关注资产筛选；报价币、结算币及合约乘数仍保留。
BTC/ETH/SOL 的小型审核表只影响资产自动归集，不是目录/行情覆盖白名单。
其他币种默认使用 `cex:<venue>:<client>:<nativeBaseId>` 身份，同一 client 的同一原生资产在不同报价币交易对间可归集；同 ticker 但不同 nativeBaseId、不同 client 或 venue 不自动合并。
`mapping_verified=False` 保持可见，不能把同名币直接当成同资产。

`discover()` 逐 client 隔离公共目录请求失败。`adapter.discovery_complete` 表示本次所有服务是否成功，`adapter.discovery_errors` 按 client 仅记录异常类型。
可访问 client 的新目录会返回；失败 client 的最后已知目录保留原身份并以 `active=False` 返回，表示本次未核实可用性，结合服务错误展示“目录过期/待核实”，不能据此断言交易所退市。
恢复成功后依据新目录恢复正常 active 状态；首次所有 client 失败且没有任何缓存目录时抛首个异常。
调用方须用完整性标志决定退市处理，不把部分目录当作全量空目录。

## Chain/address 身份映射设计（待实现）

当前统一市场目录不提供足够可靠的链/合约地址证据，本次没有声称完成全币种跨平台身份核验，也没有增加 ticker 白名单。
后续在现有人工映射审计基础上增加独立、显式的身份登记，而不自动启用可能需要账户权限的 `fetchCurrencies`：

| 字段 | 审核含义 |
| --- | --- |
| venue、client、native_base_id | CEX 原生资产作用域，禁止只按 ticker 查找 |
| canonical_chain_id、address | 链与合约共同构成身份；EVM 地址按规范校验，Solana 等地址保留大小写；原生币用显式原生标记 |
| asset_id、relationship | 确认的目标身份；原生、包装、桥接关系单独记录，不能因为同 ticker/经济敞口直接合并 |
| source_url、source_kind、observed_at | 官方公开币种/网络元数据或官方合约公告的可复核出处和观察时间 |
| evidence_hash、reviewer、reviewed_at、status | 原始证据摘要、审核人/时间、候选/已确认/冲突/撤销状态；变更保留前后值 |

只有已确认、没有冲突的 `(venue, client, native_base_id, chain, address)` 证据才允许跨平台归集。
链别名必须来自显式审核表；缺 chain/address 或官方证据时继续隔离，保留人工审核入口。
同一 CEX 资产暴露多个链的提充地址也不能证明所有包装资产完全等价；应将这些地址记为有关系的证据，审核后决定可比身份。
公共接口不可达或仅提供账户接口时，将证据状态列为缺失，等待授权可达环境或人工官方资料，不回退到猜测 ticker。
该登记及来源审计尚未实现；现有人工映射只存 instrument→asset 与修改审计，不足以替代上述链地址证据。

## 验证范围

本实现只使用本地 mock 测试与已安装 CCXT 源码核对；未发送真实交易所行情请求，未绕过受限网络，未进行交易、发布或合并。
`tests/test_cex_light.py` 覆盖批量/分组边界、稀疏补丁、真实零值、时效缺失、非有限数值、回退并发、部分失败/全失败、缺失能力、记录回调、非大币完整交易对、同名原生身份冲突，以及目录部分失败/恢复。
2026-11-04：CEX 轻量价格/全币种资金费、原有 CEX、WebSocket 与成交量单位 mock/本地解析器回归共 110 项通过；限定文件 Ruff 检查通过。

```bash
.venv/bin/pytest -q tests/test_cex_light.py tests/test_cex.py tests/test_streaming.py tests/test_volume_units.py
.venv/bin/ruff check backend/watch_money/adapters/cex.py tests/test_cex_light.py --select E4,E7,E9,F
```

生产可达性、实际 API 限流与完整目录轮转延迟仍待允许的真实网络环境验证，mock 通过不表示 live 验收完成。
