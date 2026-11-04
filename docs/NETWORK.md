# 官方接口网络访问

当前受管云环境允许 GitHub 和依赖源，其他接口被代理策略拒绝。用户已授权添加官方域名，但当前工具只提供环境读取/诊断，没有网络配置修改入口，未声称完成修改。用户随后同意先依据官方文档和 GitHub 实现并测试。

`docs/evidence/live-probes.json` 记录了真实请求结果。所有 SDK 保留继承的代理和 CA 信任，不通过修改路由、关闭证书校验或替代代理绕过策略。

## 可复制的 API 域名清单

```text
api.binance.com
fapi.binance.com
dapi.binance.com
www.okx.com
openapi.okx.com
api.bybit.com
api.bitget.com
api.gateio.ws
api.mexc.com
www.mexc.com
api.kucoin.com
api-futures.kucoin.com
api.coinbase.com
api.kraken.com
futures.kraken.com
api.huobi.pro
api.hbdm.vn
www.deribit.com
api.upbit.com
api.hyperliquid.xyz
mainnet.zklighter.elliot.ai
indexer.dydx.trade
api.prod.paradex.trade
fapi.asterdex.com
edgex-prod-v2.edgex.exchange
omni-client-api.prod.ap-northeast-1.variational.io
arbitrum-api.gmxinfra.io
arbitrum.gmxapi.io
api.dexscreener.com
perps-api.jup.ag
```

这是一份配置清单，非已生效权限。域名应以实际适配器和最新官方服务配置为准；区域产品不应通过随意替换域名规避区域限制。RPC 需由所选链与 provider 决定，凭证应通过私有环境变量配置。

依据：[Binance](https://developers.binance.com/en/docs/products/spot/rest-api)、[OKX](https://www.okx.com/docs-v5/en/)、[Bybit](https://bybit-exchange.github.io/docs/v5/guide)、[CEX 逐平台核对](CEX_SOURCES.md)、[DEX 逐平台核对](DEX_SOURCES.md)。

飞书和 Telegram 本次仅模拟测试，无需开放消息域名。未来启用实际发送时再配置 `open.feishu.cn`、`api.telegram.org` 和私有凭证。

CEX 行情源 WebSocket 的官方主机、端口及动态握手端点另见 [STREAMING.md](STREAMING.md)。它们同样需要宿主网络授权；前端本地 `/ws` 不需要外部网络。
