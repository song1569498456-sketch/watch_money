# 交易所已结算资金费历史

本模块采集交易所公共历史接口返回的已结算记录，独立于 `/api/history` 的本地行情快照采样。
默认只读取之前已获取的历史；只有用户手动传 `refresh=true` 才发送公共历史请求。
没有启动后台全目录历史回填，没有交易、账户流水查询、凭证加载或账户设置修改。

## 官方语义与当前覆盖

[CCXT 官方统一接口](https://docs.ccxt.com/docs/manual#funding-rate-history) 将 `fetchFundingRateHistory(symbol,since,limit,params)` 与当前资金费接口分开：历史结构提供 `fundingRate` 和历史 `timestamp`，since 是毫秒下界，limit 是最大条数。
通用方法的存在本身不能证明某个平台的滚动窗口或预测值是离散结算记录，因此另设已审查来源表，并要求原生 `has.fetchFundingRateHistory is True`。

| CCXT client | 结算语义证据 | 本模块单页请求上限 |
| --- | --- | --- |
| binanceusdm | [官方 USD-M 历史接口](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)，历史 fundingTime 及对应 fee charge | 1000 |
| binancecoinm | [官方 COIN-M 永续历史接口](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Rate-History-of-Perpetual-Futures) | 1000 |
| okx | [官方资金费历史](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history)：`fundingRate` 是预测值，`realizedRate` 才是实际费率，`fundingTime` 为结算时间 | 100 |
| bybit | [官方历史接口](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate) 明确返回过去已结算费率，并明确不同市场周期可能不同 | 200 |
| mexc | [官方历史接口](https://www.mexc.com/api-docs/futures/market-endpoints/get-funding-rate-history) 返回 `settleTime`；每条若带 `collectCycle` 可保留该历史周期 | 100（本模块保守限制） |
| kucoinfutures | [官方公共历史](https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history) 描述每个 settlement time point 的资金费率；强制 `uta=False` 走 public contract 路径 | 100（本模块限制） |

以上仅代表已实现并核对公共语义，**没有完成真实 API 连通性验收**。
Gate、Bitget、HTX、Kraken Futures 当前历史结算语义审查尚未接入本模块，返回501；不表示其官方必定没有公共历史能力。
Deribit 的连续累计/滚动窗口历史需另外建模，不能标成此处的离散已结算付款。现货、DEX 与未审查服务也返回501。
不为获取历史改用 `fetchFundingHistory` 等账户付款流水方法。

## 集成与 API

```python
from watch_money.funding_history import FundingHistoryService, router
app.state.funding_history = FundingHistoryService(data_dir, collector)
app.include_router(router)
```

接口：`GET /api/funding/history/{instrument_id:path}?refresh=false&since=<epoch_ms>&limit=100`。

- `instrument_id` 必须已在 collector 目录中；未知身份404。
- `refresh=false` 不访问交易所，仅查询本地记录；collector 可尚未重新发现该市场。
- `refresh=true` 要求 CexAdapter 当前已发现市场，并通过 `_resolve/_client` 选择准确服务；尚未发现409。
- `limit` 必须为1–1000；实际单页请求还受上表限制。since 为非负、SQLite整数可表示的毫秒下界，不猜测秒值。
- 默认请求超时15秒，覆盖公共请求及适配器限流等待；手动刷新串行化并有超时，避免绕过已有客户端限流。
- 有账户凭证的既存client会被拒绝409，不清除、不修改或发送这些凭证；不自动配置账户。
- 401/403或CCXT认证错误统一返回脱敏401；不请求用户账户凭证。超时504，公共接口异常502，未支持/未审查501。

返回对象：

```json
{
  "instrument_id": "cex:...",
  "records": [],
  "summary": {
    "refreshed": false,
    "count": 0,
    "cached_matching_count": 0,
    "earliest_settlement_at": null,
    "latest_settlement_at": null,
    "requested_since": null,
    "limit": 100,
    "pages": 0,
    "fetched_count": 0,
    "accepted_count": 0,
    "rejected_count": 0,
    "next_since": null,
    "has_more": null,
    "truncated": false,
    "issues": []
  },
  "coverage": {
    "kind": "exchange_settled_funding_history",
    "all_history": false,
    "background_backfill": false,
    "pagination": "one_manual_page",
    "cursor_semantics": "forward_since_hint_only_not_gap_proof",
    "direction": "positive_longs_pay_shorts_negative_shorts_pay_longs"
  }
}
```

每条记录保留 instrument_id、venue、symbol、`settlement_at`、`funding_rate` fraction、`raw_funding_rate` 原始数值文本、`funding_kind='settled'`、source 与官方source_docs，以及接收时间与修订版本。
OKX 强制从历史 `info.realizedRate` 读实际费率，避免未来 parser 回归把预测值当已结算。
缺结算时间、时间在未来、错symbol、非有限/缺失实际费率明确拒收并计入 summary.issues，不用接收时间伪造结算时间。

`funding_interval_hours` 默认null。当前目录周期、相邻记录间隔、常见8h值都不用于回填历史周期；只有 MEXC 每条历史明确给出的 collectCycle 才会填周期及 period_source。
正费率表示多方支付空方，负费率反向；`long_cashflow_sign/short_cashflow_sign` 仅表示收付方向，未计算持仓金额或实际账户扣款。

## 单页边界与存储

每次手动刷新只调用一次历史方法，不开启 CCXT 自动分页。MEXC/KuCoin 这类不处理paginate参数的方法不发送该无关参数；Bybit 的since窗口使用本次时间上界，不拿当前合约周期推断历史查询跨度。
输出只处理最多单页上限的记录。默认本地读取最新limit条并按结算时间升序；给since时读取该下界之后最早limit条。
同次返回会包含本地已有记录，因此 fetched_count、accepted_count 和返回 count 不必相等。

`next_since=max(本页可接受结算时间)+1` 只是向前查询提示，**不是向更早历史翻页的游标或覆盖完整证明**。
若服务忽略since重复返回同一时间页，过滤后返回无进展标记并停止；没有内部循环。
`has_more` 始终null，单页满额、请求超过单页cap或本地结果被截断时 `truncated=true`。
即使一页不足limit，也不宣称交易所没有更早记录、历史窗口没有缺口或已完整覆盖；保留期与可追溯范围需真实环境验证。

专属 `funding_history.sqlite3` 使用WAL、SQLite事务和进程内锁；手动刷新串行，数据库操作在线程执行。
按 `(instrument_id,settlement_at,event_type)` 去重。Binance常规与特殊分红资金费保留不同event_type，不因同一时间互相覆盖。
同一事件费率/原始值/明确历史周期修订时增加revision，并将旧记录保存到funding_revisions。
重复抓到相同记录只更新last_seen_at；first_received_at保留最初观察时间，received_at表示当前修订首次收到的时间。

## 验证

```bash
.venv/bin/pytest -q tests/test_funding_history.py
.venv/bin/ruff check backend/watch_money/funding_history.py tests/test_funding_history.py --select E4,E7,E9,F
```

2026-11-04：25项通过，包括正负方向、0费率、原值和周期未知、实际/预测区别、缺时间拒收、单页与重复游标、去重/修订、并发、持久化、API限量、401脱敏、超时取消和账户client拒用。
OKX/Bybit另通过已安装CCXT真实历史parser及公开方法stub验证；没有真实交易所流量，不能替代线上结算样本核对。
