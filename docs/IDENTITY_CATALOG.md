# 币种目录持续更新与 CoinGecko Demo 辅助匹配

## 使用流程

1. 拉取代码后，按 README 启动。设置 → 资产身份与映射管理 → 币种目录持续更新，点击“更新并保存目录候选”。首次仅保存候选；使用同页的公开证据批量预览/审核明确批准。已有映射不被初次匹配覆盖。
2. 如需 CoinGecko 辅助资料，在本地 `.env` 设置 `COINGECKO_DEMO_API_KEY`，重启应用。不要将 key 发到聊天、贴入页面或提交 Git。它只随服务器请求的 `x-cg-demo-api-key` header 发送，不是交易所账户密钥。
3. 勾选“运行期间每天自动更新”。此本地功能偏好默认关闭，启动采集、非演示模式下才运行；进程停机不运行。现有 CCXT 市场目录发现仍使用原 `discovery_seconds`（默认30分钟），本模块不重复启动全市场采集。官方身份资料与辅助目录每天检查一次，重启后保留计划与上次结果。
4. 页面默认只列新增、变化或未确定项目；分页可查看全部。完全未变且已核验的项目在后续更新中隐藏。没有 key 仍能更新已支持的官方公开资料，CoinGecko 显示缺 key，不宣称已完成辅助覆盖。
5. 合约/单位变化、官方完整目录不再包含原币种时，原有证据失效；先前匹配得到支持的 CoinGecko 身份发生变化时，也暂停对应证据映射。保留原映射和审计，不改成新的资产。核实后可在证据列表撤销旧证据，刷新资料，再预览批准新证据；不会自动把一个已变化目标“恢复”成另一个币。

## 官方协议核查（2026-11-05）

- [Demo coins/list](https://docs.coingecko.com/demo/reference/coins-list)：`include_platform=true`，返回全部当前活跃币种，不分页；响应字段为 `platforms`。inactive 数据的更高套餐条件不作免费可用假设。
- [Demo exchanges/{id}/tickers](https://docs.coingecko.com/demo/reference/exchanges-id-tickers)：100条/页，使用显式 `order=base_target`，逐页直到短页。动态目录仍非冻结快照；重页、冲突与中途失败保留不完整状态。
- [官方价格说明](https://www.coingecko.com/en/api/pricing)与[API说明](https://www.coingecko.com/en/api)：核查时 Demo 标示100次/分钟、10,000次/月。实际账户权限和剩余额度未读取；本应用使用更低的20次/分钟、9000次/月本地预算，不代表其它客户端的用量。
- Demo 文档要求 API key。当前云端运行环境已执行的策略检查：`api.coingecko.com` 不在受限网络白名单中，未配置 key；探针因此没有发请求。证据：[coingecko-environment-probe.json](evidence/coingecko-environment-probe.json)。不能据此声称真实HTTP权限、免费配额、实际页数、币种数或匹配覆盖率通过。

## 缓存、限流和范围

仅固定 `https://api.coingecko.com/api/v3`，禁止重定向/切换服务绕过访问限制。每个路径+参数缓存24小时，持久化本地调用计数，失败和取消前已发出的尝试也记账。请求至少间隔3秒；429进入1小时冷却，401/403停止自动请求。来源访问已修正后，可在页面填写非敏感原因确认恢复；不立即请求、不清除预算。官方注册表的401/403也保存停止状态。

每交易所最多50页（5000条），每轮4平台理论上至多201个CoinGecko请求，单响应最多20MiB。达到页预算、重复页或错误时明确显示覆盖不完整，不把“没返回”解释为无市场。不自动重复失败请求，不保证全交易所完整覆盖。缓存超过24小时而网络失败时保留旧辅助身份及原观测时间，标记未确定，不把旧记录包装为新观察。

首批仅选择已有原生官方身份范围的 Gate、Bitget、KuCoin、Coinbase Exchange，辅助 exchange IDs 为 `gate`、`bitget`、`kucoin`、`gdax`；逐条还要求响应 `market.identifier` 一致。真实ID覆盖需在可访问环境验证。其它平台、Advanced Trade、永续及倍率产品仍显示范围未验证；没有自动引入新的平台或账户API。

## 身份边界与续期

CoinGecko 的交易所内 base/target 对只用于找候选 `coin_id`；它不是跨交易所证明。候选需唯一coin_id、单一受支持链合约且与交易所原生currency/client/product证据一致才标为 `corroborated_hint`，此状态仍不批准映射。原生币没有合约、多平台地址、包装币、跨链版本、倍率前缀、异常/过期标记及重复ID均保留待核验；不按同名、去前缀或同CoinGecko ID直接归并。报价币与实际行情的可靠性继续由原有独立门槛判断。

续期仅适用于仍有效、已经批准的公开证据：全部证据成员必须在本轮得到完全相同的内容（除观测/到期时间/记录ID），来源本轮成功，无新冲突，原映射目标和目录指纹均未变。只更换证据引用和有效期，保存续期审计；不改人工映射、不批准新组。变化、过期、已暂停或缺成员不续期。官方读取失败不视为币种删除，但不延长旧批准；到期仍按原门槛失效。

## 本地只读实测

在允许访问 CoinGecko 的环境配置 Demo key 后：

```bash
python scripts/probe_coingecko_identity.py --live --venues gate bitget --pages 2
```

脚本只写临时缓存，最多请求coins/list加指定平台的2页，不读取/修改用户映射，不输出密钥。打印币种数量、含platforms数量、实际页数/行数/错误和截断情况。2页探针不能证明完整覆盖；实际应用按最多50页运行，并与本地已发现目录形成候选和覆盖状态。当前真实增量为未知，不能把合成测试行数当实测。

专项测试：`pytest -q tests/test_identity_catalog.py tests/test_identity_review_filters.py tests/test_identity_batch.py tests/test_asset_verification.py`。浏览器：`python scripts/browser_identity_catalog.py`，仅隔离持久化合成目录，刷新接口浏览器替身；真实本机API配置持久化与后端刷新分别由专项测试验证。
