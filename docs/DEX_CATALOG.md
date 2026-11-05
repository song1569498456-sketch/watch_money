# DEX 目录与广覆盖采集

所有 DEX adapter 现在提供 `fetch_light(instruments)`，空列表不会发网络请求；传入目录不按 BTC/ETH 等币名筛选。它是 collector 广覆盖调度接口，**复用现有 `fetch`，不是新的轻量 endpoint**，不会请求 orderbook。每个 adapter 的 `light_fetch_mode` / `light_fetch_cost` 明确这一点。

单次调用成本如下；N 为本次选择的市场数，仍需遵守公开接口限流和 collector 批次、间隔及退避设置。分页、CCXT 内部重试或隐式加载市场可能增加请求。

| 平台 | 当前公开请求成本 | 目录边界 |
| --- | --- | --- |
| Hyperliquid | 每个所选命名空间 1 次 `metaAndAssetCtxs` POST | 原生及已发现 HIP-3 全 universe，保留命名空间身份 |
| Lighter | 2 GET：`orderBookDetails` + `funding-rates` | 已发现 perpetual 市场；不含其他产品 |
| dYdX | 1 GET：`perpetualMarkets` | Indexer perpetual markets |
| Paradex / Aster | batch ticker、funding 方法各一次（如支持）；否则分别最多 N 次；可另查 funding intervals | CCXT 返回的线性 swap，未知 multiplier 不猜测 |
| edgeX | 1 次全市场 ticker + N 次 funding GET | 公共 metadata 合约目录 |
| Variational | 1 GET：`metadata/v2/stats` | 返回的 perpetual listings，不含 swap；目录不保证完整 |
| GMX | 1 GET：Arbitrum V2 market tickers | 当前实现仅 Arbitrum V2；非可成交报价 |
| Jupiter Perps | 1 次 `jlp-info` + N 次 `market-stats` + N 次 `pool-info` | 仍限官方 API 已枚举 mint；不把 custody 全部当永续市场 |
| Drift | 2 次 `getProgramAccounts` RPC | metadata only，发现为 inactive，collector 活跃目录不会伪造其行情 |
| 六个现货 DEX | 每链每至多 30 个池 1 次 pairs GET | 仅发现到的池；始终 `search_limited` |

这里只改进调度覆盖，不升级来源证明等级。缺失价格/时间戳/资金费周期保持未知，RFQ/AMM 指示价不变成可成交盘口；真实接口当前可达性与持续运行仍需单独验证。原始来源与既有核对记录见 [DEX_SOURCES.md](DEX_SOURCES.md)。其中旧“四组主要币对搜索”的说明由本文件的配置行为替代。

## 现货池发现配置

`AggregatedSpotAdapter.search_queries` 可直接接收 `Settings.dex_search_queries[venue]` 的字符串列表。无配置时使用协议名及 deployment registry 的 `dex_id + chain` 查询，例如 Uniswap 默认 `uniswap`、`uniswap ethereum`、`uniswap arbitrum`、`uniswap base`；不再固定搜索四个大币对。DEX Screener 搜索仍有返回数量和索引覆盖限制，这些关键词不保证搜索服务按协议/链穷举。

配置实例：

```json
{
  "dex_search_queries": {
    "uniswap": ["uniswap", "SMALL OTHER", "pool:ethereum:0xPoolAddress"],
    "raydium": ["raydium solana"],
    "curve": []
  }
}
```

普通字符串作为搜索 API 的 `q`；`pool:<chain>:<address>` 直接查询已知池 pairs API，chain 必须属于该协议已登记部署，地址只允许 ASCII 字母数字。这里的 `0xPoolAddress` 是语法示例，需替换真实池地址。协议/版本/链匹配仍严格校验，已知地址不绕过 membership 验证边界。重复查询只执行一次。显式 `[]` 禁用该协议的搜索来源，不会自动退回默认大币。

adapter 的 `discovery_state="search_limited"`、`discovery_complete=False`，快照 issues 也保留 `search_limited`。搜索目录减少不能作为全链池退市证据。仍未实现官方工厂事件全量回扫或全链 program account 池索引，不声称完整现货 DEX 目录。

集成层需要新增 `Settings.dex_search_queries: dict[str, list[str]]`，在发现前应用对应配置；配置缺 key 时使用 `adapter.default_search_queries`，显式空列表保持为空，变更后触发重发现。此 adapter 改动不修改 Settings 或 collector。

## 可选原始响应录制

`PublicREST.on_payload` 接收异步 callback `(venue, transport, payload)`，payload 仅为解析后的公开响应 JSON；transport 为 `rest:public:GET/POST`，不传请求 URL、参数、headers 或认证信息，因此含凭证的 RPC URL 不会进入录制 envelope。CCXT wrapper 也按 `rest:ccxt:<public_method>` 记录返回值。录制异常隔离，采集继续；取消任务仍正常传播。录制开关、保留期与磁盘上限由 collector/recorder 管理。

验证命令：`.venv/bin/python -m pytest tests/test_dex.py tests/test_dex_catalog.py -q`。测试是合成公开 schema fixtures，覆盖小币目录、HIP-3、批量/逐市场成本、空目录零请求、可配置池发现和录制失败隔离，不是 live 接入验收。
