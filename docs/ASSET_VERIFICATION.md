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

### R59 安全批量身份审核与覆盖可见性（2026-11-05）

- 已有能力：公开来源采集、只读review_packet、完整策略计划、事务内重验、映射审计与撤销均已存在。本次只补用户难以操作的分组筛选/分页、限定选择预览及UI；旧手动流程和旧全计划API兼容保留。
- 新GET `/api/assets/verification/batch-review`支持来源、规范链、合约包含、状态、具体阻止原因筛选；默认25组、最多100组一页。先对完整证据运行策略再筛选整组，来源筛选不会隐藏另一平台或冲突依据。提供全局候选统计、筛选统计、实际跨平台身份门槛通过的组/市场数量，各自分开展示。合约大小写宽容仅用于搜索，不参与合并判定。
- 新POST `batch-preview`接收1–100个唯一proposal_ids，最多10000条可应用证据；返回精确可应用组/市场和阻止/已核验跳过组。选中集合绑定全状态token；`batch-apply`带同一集合，在原子事务内重新构建策略。资料变化、过期、来源失败、已有人工映射等不能被预览绕过；省略集合或换组不能扩大已预览操作。审计保存选中ID、计数和跳过原因。
- UI在设置身份面板显示来源、链/合约、状态、跳过原因、完整证据URL/字段/摘要/观测及到期时间。一键选择仅当前页可审核组，仍需预览后明确应用。翻页、筛选、父数据刷新清空选择；迟到响应不能恢复旧上下文操作。当前页面每页25组，API支持最多100组的限定批次，不跨页隐式选择。加载时旧页暂不可操作，显示检查时刻；应用前必重新核验。
- 复用official-spot-contract-v1：两个独立登记官方来源、同链同合约、原生client/currency/product/单位一致，观测24小时内且剩余有效期至少1小时，完整性、最新来源收据及映射冲突全部检查。只适用普通现货；永续底层需独立依据。候选绝不等于已批准，身份通过不表示价格新鲜、报价币可比较或资金费可比。
- 本次会话保留上一批证据供填写原因后撤销；更早批次继续原有证据明细/审计撤销。分页是实时重新计算，资料变化可移动组，不保证冻结快照；token防止因此误应用。没有自动批准、生产数据迁移或账户操作。旧不带proposal_ids的全计划API未改为100组限制，新限定流程才有上述批次cap。
- 新9项后端回归：完整组筛选与分页、限定应用/撤销、冲突不隐藏、过期/变更token、空/未知/超100组、混合跳过及审计、API边界；最终78项身份专项通过。完整后端1407通过（66.15秒，1条上游弃用警告），44项前端及TS/Vite构建通过。完整后端后仅追加跳过审计字段与断言，最终78专项复验通过。
- 隔离浏览器7项通过：30合成资产/60现货，额外冲突形成31组，第一页预览应用23组/46市场并跳过2组；实际临时库覆盖0→23/46→撤销0。还核对来源/链/过期原因、翻页清选择、390px无溢出/无JS错误。既有手动审核9项检查通过。见[evidence/browser-identity-batch-review.json](evidence/browser-identity-batch-review.json)、[screenshots/identity-batch-review-synthetic.png](screenshots/identity-batch-review-synthetic.png)；脚本`.venv/bin/python scripts/browser_identity_batch_review.py`仅临时库、MockTransport官方结构及本机HTTP。
- 局限：没有新增真实公开证据或修改任何用户映射；真实候选仍需用户使用此流程审核，现货证据不扩永续。全币种实际可靠覆盖、长期新鲜度、24小时、外部通知及原清单开放；不重试受阻OKX WS或受限Lighter，远端CI未确认。

### R60 旧审核入口保护与完整撤销历史（2026-11-05）

- 复现并修复旧preview/apply未检查最新官方来源读取、可覆盖人工映射的缺口。公开证据现在在新旧入口共用官方现货策略检查（来源收据/失败、完整性、有效期、原生身份、冲突）；应用事务内再次检查，旧token失效。只选择同市场的手动证据也不能绕过仍有效的公开证据来源失败。
- 手动/本地链上证据仍保留独立人工审核，不假称官方来源，且不因与公开证据混组而免检公开部分。已有人工映射或目标不同的现有映射阻止覆盖；预览显示市场、当前值及提议值。需用户自行明确修订已有映射，本批不隐式撤销用户数据。
- status增加evidence_offset/evidence_total/evidence_next_offset；证据明细支持首页/前后页，能访问2000条以后的完整历史。翻页及页大小变化清空选择并禁用旧内容；异步刷新绑定查询范围。所选证据显示当前依赖市场，撤销仅针对明确ID，保留审计；实际影响按提交时最新依赖计算，人工映射没有证据依赖则不删除。
- 原review_packet继续使用一次捕获的目录/证据，加入同一来源/映射检查；不重新读取不同版本的证据来生成页面token。全量策略候选不成为已批准覆盖。
- 81项身份专项通过（新增3项：两路径来源失败/旧token及手动选择绕过、人工映射旧值保护、2100条历史后API分页撤销）。前端构建、Ruff/diff通过。旧手动浏览器10项通过，包含真实本机API的2000后翻页、返回首页、撤销/持久化/冲突和390px检查；发现的页大小旧响应窗口已修复。所有变更/数据仅临时库与合成来源；未改变生产映射、账户或受阻连接。
- 按用户新结束条件，本轮完成后检查最终提交CI并合并，不再以24小时或全部平台完成为前置条件。真实广泛身份仍需审核、现货依据不能自动证明永续、实网长期稳定性/地区限制/通知模拟等原限制保留。部署发布不在本次范围。
