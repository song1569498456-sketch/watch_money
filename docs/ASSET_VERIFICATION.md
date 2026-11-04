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
- KuCoin 的 [公开全部币种资料](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-all-currencies)
  `GET https://api.kucoin.com/api/v3/currencies` 无需权限，返回固定 `currency` 与可变化的 name/fullName。
  本模块用 `currency` 关联真实原生 baseId，使用 `chains[].chainId/chainName/contractAddress` 规范化合约证据。
  显示名称改成 NANO 也不能把官方原生 ID XRB 换成 ticker 猜测；大小写和原字符不做币种别名转换。
- Coinbase Exchange 的 [公开币种列表](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/currencies/get-all-known-currencies)
  与 [官方网络字段更新说明](https://docs.cdp.coinbase.com/exchange/changes/changelog) 提供 `id` 与
  `supported_networks[].id/contract_address`。只接受 `coinbaseexchange` 原生客户端；
  当前 `coinbase` Advanced Trade 目录不能据此自动确认，不能按公司名或统一 ticker 跨业务线传播。
- 不调用账户币种、充值地址或钱包余额接口，不需要账户 API key，不接受用户提供任意抓取 URL。
  HTTP 请求只发向以上四个固定 HTTPS 地址，不跟随重定向，每个来源一次完整列表请求。
- 本轮执行环境的真实公共探测失败为 ProxyError，见
  [data-review-live-probes.json](evidence/data-review-live-probes.json)。没有把失败响应当可用资产证据。
  `test_asset_verification.py` 中 Gate/Bitget 响应是官方字段形状的**合成 fixture**，不是本轮真实成功行情。
  新增 KuCoin/Coinbase 的真实探测同样 ProxyError，见
  [live-review-public-probes.json](evidence/live-review-public-probes.json)。新增能力由带明确合成标签的官方 schema
  MockTransport 回归证明，**不声称真实新增可核验覆盖或真实映射数量**；仍需外部网络复测。

导入以同一 venue/client 的不可变原生 currency ID 精确关联目录市场。
`Instrument.native_currency_id` 仅由适配器原始 `market.baseId` 填写，保留原字符；缺失保持 null。
原生 asset_id 中仅靠统一 base 回退拼出的字符串不能证明原始 ID 已知。
导入必须同时满足 venue/client/product scope、真实 native_currency_id 与原生命名空间后缀完全相等，
再与官方 registry 的 currency ID 原字符精确关联；不会 upper、去数字前缀或按重命名后的 ticker 查找。
不同平台通过**相同 canonical chain + contract address** 才产生共同身份候选，不能因 PEPE 同名关联。
同一局部原生资产的不同现货报价币可共享该证据。
**四个公开注册源本轮均只覆盖 spot；Gate/Bitget 也不再把 spot 币种目录隐式传播到 perpetual。**
KuCoin 的 kucoinfutures 与 Coinbase 的 Advanced Trade 均明确拒绝，scope_note 和拒绝原因保留在状态/审计中。
这是缺少已核验跨产品 underlying/currency ID 与价格单位保证时的保守范围，不能宣称已解决全资金费身份覆盖。
公开行只有唯一完整链地址，且没有未知/缺失网络表示时才可供确认；多个链或多个地址不任选第一条。
常见网络标签仅做明确的 ETH/ERC20、BSC/BEP20、SOL/Solana 等规范化；未知网络不猜测。
1000 等规模前缀、wrapped/bridged 关系、非 1 的基币单位证明不能当普通等价关系应用。
已有 DEX 原生 chain:address 可按完全相同地址补入本地目录证据，观测时间明确是本地读取而非交易所事件。
KuCoin 的 chainId 与可识别 chainName 冲突时拒绝；未知网络、零地址/native 占位、缺 contract_address
也不采用。Coinbase 的 convertible_to 或浏览器 URL 不是合约身份，不从这些字段猜地址。

HTX 本轮核对的[公开 Currency & Chains 文档](https://huobiapi.github.io/docs/spot/v1/en/#apiv2-currency-amp-chains)
没有给出可用的 token contract 字段；不将网络名或 chain 路由 ID 冒充合约地址。
Binance 的[币种资料端点](https://developers.binance.com/en/docs/catalog/core-trading-wallet/api/rest-api/capital)
明确为签名 USER_DATA，不作为免凭证来源加入。其它未审查通过的平台继续未核实。

## 可运行流程

1. `POST /api/assets/verification/public-refresh`，例如 `{"venues":["gate","bitget","kucoin"]}`。
   来源选择来自服务端 sources，最多四项；Coinbase Exchange 可显式选入，默认不代替 Advanced Trade。
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
新增来源没有延长 TTL。旧公开证据若缺少原始 native_currency_id，或不在当前已审查产品/client范围，
启动时降为 scope_unverified，写 asset_public_scope_downgrade 审计并立即刷新内存，不静默保留旧可靠状态。
`identity_candidates(..., as_of=...)` 每次比较检查有效期，**即使旧 Instrument 仍在内存也不能继续可靠比较**。
回放不能使用 as_of 之后才确认的证据；今天已失效的证据也不会因传入历史时间自动复活。
真实历史身份版本重建尚未实现，这里采用保守阻止，而非伪造当时已知身份。

旧直接人工映射/内建已确认目录保留原有兼容行为，覆盖单独列 legacy_verified_markets，
不编造来源、不计入 traceable_verified_markets。新工作流核验后才有可追溯证据绑定。

## 覆盖与资源

GET 返回 coverage、sources、proposals、evidence、audit、truncated。
新增 platform_coverage 每平台统计 active/verified/traceable/unverified/conflicting、source_scope_markets、
source_covered_markets、source_blocked_markets 与 scope_reasons/source_blockers。
sources 保留原有请求错误、响应行数和匹配统计，增加 client_ids/product_kinds/scope_note、
scope_eligible_markets/scope_rejected_markets/scope_reasons/unmatched_currency_markets。
因此“端点读取成功”与“该市场属于已审查业务范围”“原始 ID 在返回目录里”“合约证据足够”分别呈现。
coverage 区分总目录/活跃/已确认/可追溯确认/旧确认/未确认/过期市场数；
其中 market_count_scope=active_catalog：verified_markets、traceable_verified_markets、legacy_verified_markets、
unverified_markets、expired_markets 和 source_covered_markets 均以活跃目录为范围，
verified_markets + unverified_markets = active_markets。catalog_markets 保留全目录数量，inactive_markets 单列。
source_observed_markets 是观察过元数据的范围，source_covered_markets 仅统计当前未过期、
目录仍匹配、无多网络歧义的公开证据可覆盖范围，**不表示已经确认映射**。
ready_proposals/conflict_proposals 表示预览就绪/受阻组；来源成功行数不能代替可核验覆盖。
历史 evidence_records/source_observed_markets、审计和待审分组保留完整范围，不因市场停用删除来源或修订记录。
GET 默认最多 200 条 evidence 与 proposal，最大 2000；两类截断分别明示，统计基于完整目录。
200 个合成小币、双公开来源的规模回归仍使用完整预览/应用流程：默认 200 条截断不掩盖完整统计，
放大返回数量后可审阅全部待核验项；没有把合成规模测试当真实市场覆盖。
证据记录由内容 SHA256 标识，重复导入不重复插入；审计保留批次、来源尝试、应用与撤销。

接入：`app.state.asset_verification = AssetVerificationService(store, collector)`，include router，
shutdown `await service.close()`。HTTP client 按需创建，外部注入 client 由调用方管理；没有后台任务。

验证：`.venv/bin/python -m pytest tests/test_asset_verification.py tests/test_identity.py tests/test_core.py -q`。
测试覆盖公开 schema、精确局部 ID、非主流币多产品/多平台、无 ticker 扩散、多个网络/地址、
前缀/包装单位冲突、导入/预览/应用/修订/撤销、重启、过期缓存与回放、并发确认、数据库回滚及来源失败。

## 尚未完成的永续身份范围

本批扩展提供真实可调用的公开现货注册资料导入能力，不能解决默认全永续仍只有少量内建可靠资产组的缺口。
下一步应逐平台核对官方 contract/instruments endpoint 的 underlying/baseCoin/baseCurrency 与币种注册 ID
是否有明确关系，保留原始字段、官方引用、产品/client范围和基币价格单位；
有证据后才开放对应 perpetual source scope，1000 倍/包装/桥接关系仍要独立处理。
在官方保证不足或公共网络不可访问时继续显示未核实，不能靠统一 ticker 或扩大币种白名单制造覆盖。


回放检查：价差扫描、资金费视图、组合计算均传入请求时点核验批准时间。回放早于映射批准时不得使用该映射。现有撤销/过期状态仍保守拒绝，未实现完整历史版本恢复。新增200个小币种、两个平台的合成批量验收可形成200组，证明链路不受主流币白名单限制；它不是实网身份覆盖统计。
