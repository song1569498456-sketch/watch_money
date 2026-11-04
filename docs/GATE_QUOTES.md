# Gate 现货盘口时间与覆盖

2026-11-04 独立环境反馈（父线程提供，非本云环境重跑）：真实 Gate/Bitget 官方身份资料在隔离库使覆盖从 3 组/47 市场到 202/487，撤销后恢复 3/47；原库哈希未变。199 组/440 市场是这次临时批准增量，不是生产批准。固定真实盘口方向数 368→866→368，但可靠价格方向始终为 0：Gate 3257 条 REST 价格 patch 的 source_at 都为空。身份通过不等于价格时间通过。

## 官方时间语义

- [Gate 现货 REST ticker](https://www.gate.com/docs/developers/apiv4/en/spot/#retrieve-ticker-information) 的公开 schema 没有盘口事件时间。`etf_pre_timestamp` 是 ETF 前值相关时间，不可作普通买卖报价时间；HTTP Date、`/spot/time` 和本地收包也不是报价时间。单币 ticker 额外提供买卖数量，但没有因此获得盘口时钟。
- [Gate 现货 WS book_ticker](https://www.gate.com/docs/developers/apiv4/ws/en/#best-bid-or-ask-price) 的 `result.t` 明确为盘口更新时间（毫秒），`u` 为更新 ID，`s` 为原生交易对；b/B、a/A 分别为买卖价/量。消息外层 time/time_ms 不替代 result.t。该频道无需认证，支持交易对数组和多次累加订阅。
- [Gate REST order_book](https://www.gate.com/docs/developers/apiv4/en/spot/#retrieve-order-book) 区分 `current`（响应生成时间）与 `update`（盘口最后改变时间）。本地 CCXT 4.5.91 的 orderbook 统一 timestamp 使用 current；这是按需深度响应语义，不能拿它给另一次批量 ticker 补时间。本批未把逐市场 orderbook 轮询宣称为全目录实时方案。

## 实现边界

`gate_streaming.py` 使用固定公开 `wss://api.gateio.ws/ws/v4/`，仅订阅 `spot.book_ticker`。由 REST 已发现的准确原生市场 ID 建立映射；全目录模式下不依赖 BTC/ETH/SOL 白名单。最多 5000 个活跃现货市场、一条连接，50 对一批、批间隔 0.25 秒；这些是本应用资源预算，不是已证实的交易所容量承诺。采集状态保留请求数、实际选择数与截断标记；永续/不支持产品不进入此现货源，REST 继续工作但缺时间仍不可排序。

每市场有界合并保留原始报价时间和收包时间，空侧明确清除，同时间清除屏障不被后续正价抹掉；缺时间、错误市场、未来时间和乱序帧不能产生可靠新报价。完整盘口流与量/资金费等 REST 指标分开。无时间 Gate REST 买卖价不能覆盖已有带时钟 WS 买卖价或清除屏障；原始字段年龄保持，过期后不会靠 REST 收包重新变新鲜。尚未取得带时钟盘口时，REST 值仍可显示为时间未知。

独立 reader 处理 pong，订阅与应用心跳不等待下游；录制观察队列有界，不阻塞盘口接收。连接失败由 collector 原有退避/重连管理，恢复后必须收到新有效盘口；无账户 API、密钥或交易操作。

## 验证与开放项

本批测试是官方结构合成帧与假 WebSocket：无时钟 REST→有效 WS→晚到 REST→自然过期/恢复；身份未批准仍不排行；同时间清除、空侧、未来/乱序、订阅预算、取消与队列压力。真实网络在本云环境仍受限制，未执行 Gate WS 实测或改变环境设置。

在可达的独立环境用原有采集程序启用 Gate 与 `prefer_websocket`、`broad_collection_enabled`，只在隔离库加载/核验身份资料；记录 requested/selected/truncated、实际新鲜 BBO 数、字段来源与原始 result.t/收包差、可靠方向数、重连和循环延迟。要验证订阅拒绝/限流、冷门市场不更新时自然过期、REST并发不覆盖时间，以及持续运行；收到成功订阅或连接在线不代表全市场有效覆盖。全量吞吐、真正可靠方向增加及 24 小时稳定性尚待实测。
