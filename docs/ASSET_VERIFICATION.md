# 可追溯的批量资产身份核验

现有 CEX 目录的 `cex:venue:client:currencyId` 是交易所局部身份，不能按同 ticker 合并。
本模块提供公开批量证据导入与明确确认流程，扩大可核验范围，不增加币种白名单。
链地址、单位、包装/桥接关系不清楚的市场继续独立显示为未核实。

## 公开来源与当前验证边界

- Gate 的 [公开币种列表](https://www.gate.com/docs/developers/apiv4/en/spot/#query-all-currency-information)
  `GET https://api.gateio.ws/api/v4/spot/currencies` 无需认证，返回交易所 currency ID 与 `chains[].name/addr`。
- Bitget 的 [公开币种信息](https://www.bitget.com/docs/catalog/classic-spot-market/classic-spot-market)
  `GET https://api.bitget.com/api/v2/spot/public/coins` 返回 `coin` 与 `chains[].chain/contractAddress`。
  实现同时对照当前 CCXT `bitget.fetch_currencies` 使用的公开 v2 schema；旧页面重定向时不据此虚构接口成功。
- 不调用账户币种、充值地址或钱包余额接口，不需要账户 API key，不接受用户提供任意抓取 URL。
  HTTP 请求只发向以上两个固定 HTTPS 地址，不跟随重定向，每个来源一次完整列表请求。
- 本轮执行环境的真实公共探测失败为 ProxyError，见
  [data-review-live-probes.json](evidence/data-review-live-probes.json)。没有把失败响应当可用资产证据。
  `test_asset_verification.py` 中 Gate/Bitget 响应是官方字段形状的**合成 fixture**，不是本轮真实成功行情。

导入以同一 venue/client 的不可变原生 currency ID 精确关联目录市场。
不同平台通过**相同 canonical chain + contract address** 才产生共同身份候选，不能因 PEPE 同名关联。
同一局部原生资产的不同报价币、现货/永续可共享该证据，但合约数量乘数仍留给执行计算独立换算。
公开行只有唯一完整链地址，且没有未知/缺失网络表示时才可供确认；多个链或多个地址不任选第一条。
常见网络标签仅做明确的 ETH/ERC20、BSC/BEP20、SOL/Solana 等规范化；未知网络不猜测。
1000 等规模前缀、wrapped/bridged 关系、非 1 的基币单位证明不能当普通等价关系应用。
已有 DEX 原生 chain:address 可按完全相同地址补入本地目录证据，观测时间明确是本地读取而非交易所事件。

## 可运行流程

1. `POST /api/assets/verification/public-refresh`，例如 `{"venues":["gate","bitget"]}`。
   仅保存证据，不修改 mapping。来源状态有 state/error_type/last_attempt_at/received_at/response_rows/
   matched_markets/eligible_markets/blocked_markets，失败原因只保留安全异常类型。
2. `GET /api/assets/verification?limit=200` 查看覆盖、证据、待审分组与审计。
   每个 proposal 的 status 为 ready / conflict / unverified；ready 表示可以审阅确认，尚未应用。
3. `POST /preview`，传 `{"evidence_ids":["..."]}`。返回 proposals、ready、blocked 和 preview_token。
   预览检查 native fingerprint、当前映射、证据有效期、其它活跃冲突证据、链地址与单位。
4. `POST /apply` 传相同 evidence_ids 与 preview_token。SQLite `BEGIN IMMEDIATE` 下重新核验，
   复用 `Storage.map_assets`，证据绑定、所有映射和审计在同一事务内提交；任一组冲突整批不写。
   目录、映射或证据变化会令旧 token 无效，返回 409，需要重新预览。并发重复确认只允许一次成功。
5. `POST /revoke`，传 `{"evidence_ids":["..."],"reason":"修订原因"}`。
   保留原始证据与修订历史，撤销依赖该证据的整组已确认映射，并立即刷新 collector 当前身份。

人工补充或修订用 `POST /evidence` 的结构化 JSON，不要求私钥或账户信息：

```json
{
  "items": [{
    "instrument_id": "cex:example:example:spot:TOKEN_USDT",
    "native_asset_id": "cex:example:example:TOKEN",
    "canonical_asset_id": "ethereum:0x1111111111111111111111111111111111111111",
    "source_url": "https://project.example.invalid/official-contract-reference",
    "source_field": "listing.baseCurrency.chainContract",
    "observed_at": 1800000000000,
    "expires_at": 1800086400000,
    "relationship": "same_asset",
    "unit_multiplier": 1,
    "note": "示例占位，必须替换为实际人工核对的来源和时间",
    "supersedes_evidence_ids": []
  }]
}
```

示例不是可直接使用的真实证据；时间必须符合当前时刻，原生 ID 必须与目录完全一致。
source_url 为无凭证、无 query 的 HTTPS 参考，服务**不抓取或声称独立验证人工填写网页的内容**。
人工记录标为 manual_review，公开响应标为 public_contract_metadata，本地地址标为 catalog_contract_identity。
它们都要经过明确预览/应用；确认表示本地使用者审阅该结构化证据，不把人工声明冒充远端自动核验。
修订时列出同一 instrument 的 supersedes_evidence_ids。旧记录仅标 superseded，不删除。
导入与已应用证据矛盾的新目标会立即把当前映射降为 conflicting_evidence，不能继续参与可靠比较。
撤销矛盾新证据后，仍有效且未撤销的原确认可恢复；这两步都有审计。

## 证据生效与失效

Instrument 增加 mapping_evidence_ids / mapping_verified_at / mapping_expires_at / mapping_evidence_status。
Storage 保存证据元数据与原生目录 fingerprint，映射不覆盖 native_asset_id。
重新发现后原生资产、链、基币符号、产品或合约单位发生变化，会降为 catalog_changed。
证据被替代/撤销/冲突后，保存的映射历史仍可查，collector 立即降级。
公开批量证据默认七天有效；人工有效期须在观测后 90 天内，过期/未来观测不能录入确认。
`identity_candidates(..., as_of=...)` 每次比较检查有效期，**即使旧 Instrument 仍在内存也不能继续可靠比较**。
回放不能使用 as_of 之后才确认的证据；今天已失效的证据也不会因传入历史时间自动复活。
真实历史身份版本重建尚未实现，这里采用保守阻止，而非伪造当时已知身份。

旧直接人工映射/内建已确认目录保留原有兼容行为，覆盖单独列 legacy_verified_markets，
不编造来源、不计入 traceable_verified_markets。新工作流核验后才有可追溯证据绑定。

## 覆盖与资源

GET 返回 coverage、sources、proposals、evidence、audit、truncated。
coverage 区分总目录/活跃/已确认/可追溯确认/旧确认/未确认/过期市场数；
其中 market_count_scope=active_catalog：verified_markets、traceable_verified_markets、legacy_verified_markets、
unverified_markets、expired_markets 和 source_covered_markets 均以活跃目录为范围，
verified_markets + unverified_markets = active_markets。catalog_markets 保留全目录数量，inactive_markets 单列。
source_observed_markets 是观察过元数据的范围，source_covered_markets 仅统计当前未过期、
目录仍匹配、无多网络歧义的公开证据可覆盖范围，**不表示已经确认映射**。
ready_proposals/conflict_proposals 表示预览就绪/受阻组；来源成功行数不能代替可核验覆盖。
历史 evidence_records/source_observed_markets、审计和待审分组保留完整范围，不因市场停用删除来源或修订记录。
GET 默认最多 200 条 evidence 与 proposal，最大 2000；两类截断分别明示，统计基于完整目录。
证据记录由内容 SHA256 标识，重复导入不重复插入；审计保留批次、来源尝试、应用与撤销。

接入：`app.state.asset_verification = AssetVerificationService(store, collector)`，include router，
shutdown `await service.close()`。HTTP client 按需创建，外部注入 client 由调用方管理；没有后台任务。

验证：`.venv/bin/python -m pytest tests/test_asset_verification.py tests/test_identity.py tests/test_core.py -q`。
测试覆盖公开 schema、精确局部 ID、非主流币多产品/多平台、无 ticker 扩散、多个网络/地址、
前缀/包装单位冲突、导入/预览/应用/修订/撤销、重启、过期缓存与回放、并发确认、数据库回滚及来源失败。
