# 全目录资金费排行榜与跨平台矩阵

`funding_view.router` 提供 `GET /api/funding`，只读取 collector 当前完整目录与快照。
仅纳入启用平台的活跃 perpetual 市场，不使用 watch_assets，不限定主流币，不触发行情/盘口/历史请求。
关闭收益计算模块也可查看行情；演示模式不混入真实排行。
纯函数为 `build_funding_view(instruments, snapshots, as_of, stale_seconds, *, sort="rate",
limit=500, rate_basis="normalized8h")`，输入可为列表/迭代器或以 ID 为键的字典，不修改输入。

参数：sort 支持 rate / spread / asset；rate_basis 支持 raw / normalized8h，
前者按各市场实际周期原始费率排序，后者按八小时归一化排序。
limit 为 1–5000，分别限制 rows 和 groups 的返回数量。排序覆盖完整输入目录，
stats 明确总数、返回数和两种截断情况，不把截断后的结果称作全部市场。

rows 逐市场保留 instrument/venue/base/quote/settle、原始身份、identity_status、
raw_rate / kind / interval_hours / normalized_8h_rate、next_funding_at 与 countdown_seconds。
未确认同 ticker 的市场依旧列出，维持各自身份，不能放入相同可比矩阵。
不同原生链/地址或缩放/包装关系冲突的市场也列出并标明冲突，不能靠人工同 ID 抹掉证据。

field_validity 对资金费、类型、周期和下次结算时间分别返回 valid/reason/source/source_at/received_at/
age_seconds/ttl_seconds/metadata_observation。动态字段使用 stale_seconds；
周期元数据只有明确 observed_at 来源后缀、源时间 null、显式逐字段观测时间时才按六小时有效期核验，
与 scanner/strategy 一致。未知源时间保留 null，warnings 标注 source_timestamp_missing。
缺费率/周期、过期、未来信息、演示或无效快照不产生有效归一化数值。

groups 只包含 identity_candidates 已确认且跨 venue 的同资产组，cells 保留每个市场的实际周期和类型。
同一有效 funding kind 才计算最佳跨平台 `spread_8h_rate = short8h - long8h`；
不同类型或未知类型时留空并说明原因。最佳多空不能来自同一 venue。
结算时间不同、报价币不同、源时间缺失都单独警示；不同 quote 的无量纲费率差只用于同估值名义规模比较。
所有差值都明确为归一化费率差，不是实际结算现金流或净收益，net_profit 永远 null。

该视图 observation_type=local_public_snapshots、is_settled_history=false。
current/predicted 分别标注估算/预测；即便某平台当前返回 settled，也只是本地观察到的已结算值快照，
不能冒充平台完整历史序列或未来费率。

前端 `FundingPage` 无 props，局部声明类型，提供手动刷新、原始/8h 排序、返回条数、
排行榜/已确认同资产矩阵切换、逐市场周期和结算倒计时、源事件与本地接收时间、未知状态与来源明细。
主应用负责将组件接入导航；不自行触发后台任务。

“获取已结算历史”只在用户点击具体市场后调用
`GET /api/funding/history/{instrument_id:path}?refresh=true&limit=100`。
这是独立 public settled history service 提供的真实历史接口，默认排行榜不批量拉历史。
历史表展示 settlement_at / funding_rate / funding_interval_hours / source / received_at；
未知历史周期不使用当前目录周期回填。失败、未覆盖、无返回与截断都有单独提示。

验证：`.venv/bin/python -m pytest tests/test_funding_view.py -q`；前端 `npm run typecheck`。

## R38 Bitget/Gate公开结算历史

现有历史接口的显式refresh新增Bitget Classic和Gate，仍限一页最多100条、单刷新锁/适配器并发及15秒超时。Bitget强制uta=false、paginate=false、pageNo=1，原始fundingTime毫秒必须与统一timestamp一致，symbol必须等于目录nativeID；since只过滤最新页，不作为旧页回填游标。Gate强制paginate=false与精确settle（BTC/USDT/USD1），原始t秒×1000必须吻合统一timestamp，r保留原字符串。目录swap/nativeID/settle不明时拒绝刷新。

费率单位为每次结算的小数比例，不乘合约张数/乘数、不额外除100；正值多付空收。历史原始周期未提供则null，不推定8小时，也不拿当下目录周期回填历史。记录额外保留native_market_id、settle、rate_unit、native_settlement_time/native_time_unit。现有去重、修订、首次收到时间和本地采样隔离保持。缓存及新接口都不是全历史完整性证明；Gate当前费率独立源时间仍未知。

官方来源见COVERAGE R38。本轮使用合成响应和安装版CCXT公开方法替身测试，没有行情主机实测，没有账户凭证/交易/生产映射变更。

## R40 HTX币本位历史

现有历史页/refresh接口新增HTX币本位永续，官方来源见COVERAGE R40。只取原始realized_rate而非CCXT统一fundingRate；funding_time毫秒、contract_code、symbol、fee_asset逐项验证。保留rate_field=realized_rate、advertised_funding_rate、native_market_id、settle与时间单位；收付符号按小数比例，不按反向合约张数或USD面值缩放。历史周期缺失仍null，最多50条最新第一页、since本地过滤、next_since=null，拒绝把有限页当全历史。

线性接口新版本尚未审查，返回明确未支持；Kraken连续历史模型也未加入。无身份批准、自动历史回填、账户或交易。当前测试数据均合成；API公共历史率与用户账户实际费用不同。

## R41 Hyperliquid公共市场结算历史

新增原生Hyperliquid fundingHistory，支持已发现的首个perp dex及HIP-3完整coin前缀，必须匹配目录ID、namespace和collateral/settle；请求后再次检查目录/抵押币没有改变。使用time原生毫秒、fundingRate原始小数比例，官方按小时支付，历史周期记1h；不把8h公式再次除8，也不将premium当资金费。没有user/address参数，不查询userFunding或账户支付。

单手动刷新一请求、最多500行、查询窗口最多500小时，默认从当前时间往前min(limit,500)小时；原有15秒刷新总超时与单刷新锁生效。先按有效原生时间排序再截取limit，游标只基于保存的最后记录，保留拒绝计数、截断和缺口未知。缺coin、错前缀、非毫秒/未来/范围外时间及非有限率拒绝；403/451明确访问限制、不重试或替代地区。新历史单独入库，不注入当前费率/身份排行。

官方依据：[fundingHistory](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals#retrieve-historical-funding-rates)、[每小时资金费](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)、[时间范围分页上限](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)。本批仅MockTransport验证，不是实网或24小时验收。

### 独立实网复测（不要在正在验收的旧提交进程中执行）

在授权可达网络、新提交的独立检出目录中执行下例。它使用临时数据库、仅公开目录和一条指定市场历史请求，不读取项目data目录。先用BTC，再把coin替换成公开发现的完整HIP-3名称；不要猜前缀或改成用户地址。保留打印结果与提交号，遇403/451停止，不换节点绕过。

```bash
PYTHONPATH=backend .venv/bin/python - <<'PYTEST'
import asyncio, json, tempfile
from types import SimpleNamespace
from watch_money.adapters.dex import HyperliquidAdapter
from watch_money.funding_history import FundingHistoryService

async def main():
    adapter = HyperliquidAdapter()
    try:
        catalog = await asyncio.wait_for(adapter.discover(), 90)
        coin = 'BTC'
        item = next(item for item in catalog if item.native_symbol == coin and item.active)
        collector = SimpleNamespace(instruments={item.id: item}, adapters={'hyperliquid': adapter})
        with tempfile.TemporaryDirectory(prefix='wm-public-history-') as directory:
            service = FundingHistoryService(directory, collector)
            report = await service.get(item.id, refresh=True, limit=24)
            print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await adapter.close()

asyncio.run(main())
PYTEST
```

核对结果coin与完整native_market_id、原始time/settlement_at、费率原文、1h周期及正负收付；重复读取缓存不触网，重复refresh不增长重复记录。与独立公开POST fundingHistory相同coin/startTime/endTime的响应对照时，不把查询时点不同导致的新增结算算作错误。故障/空返回及缺口照实记录，不能据此认定全部历史或用户实际到账。

## R43 dYdX v4公共历史

接入GET /v4/historicalFunding/{ticker}，精确匹配已发现dydx/perpetual/chain、原生ticker/base/quote与USDC结算。保留rate小数、effectiveAt带时区ISO原文及转换后的毫秒、effectiveAtHeight字符串；无时区、错ticker、未来时间、无效高度/非有限率拒绝。查询后目录变化拒绝入库。官方默认每小时，但治理可修改epoch且记录未带逐条周期，因此funding_interval_hours保持null，不能为历史做8h收益归一化。高度是indexer报告值，不是节点/区块哈希认证或重组证明。

一个显式refresh最多100条最新页，保留原刷新锁/总超时；since只在本地过滤，next_since=null，不混用上游向过去的effectiveBeforeOrAt与向未来游标。403/451停止该请求，无重试/备用地区。数据仍是市场率，不是账户支付，不影响实时快照或身份批准。

官方[HTTP历史文档](https://docs.dydx.xyz/indexer-client/http#get-historical-funding)、[资金费及治理参数](https://docs.dydx.xyz/concepts/trading/funding)、[公开控制器](https://github.com/dydxprotocol/v4-chain/blob/main/indexer/services/comlink/src/controllers/api/v4/historical-funding-controller.ts)、[返回字段转换](https://github.com/dydxprotocol/v4-chain/blob/main/indexer/services/comlink/src/request-helpers/request-transformer.ts)。官方源码main可能变化，本批按2026-11-04可读版本检查，未执行主网请求。

独立实测可复用上节临时库脚本，将HyperliquidAdapter改为DydxAdapter、coin改为BTC-USD、collector.adapters键改为dydx；先从discover目录选准确ticker，其他步骤保持。只能在授权网络的新检出与临时目录执行，不接入旧24小时进程。检查ticker、effectiveAt、effectiveAtHeight、原始rate与未知周期，并与同limit的公开历史响应核对；保存失败/空返回/截断，不把单页当全历史。

### R44 历史请求与目录更新一致性（2026-11-04）

- 修复CEX历史请求排队及网络等待期间沿用旧目录的缺口。入口复制Instrument，取得刷新锁后重检；CEX在取得限流容量后、响应返回后重检目录、适配器、client ID及原生合约范围（id/symbol/base/quote/settle/swap/linear/inverse/contractSize/active）。变更返回409，不解析保存该响应；无关info更新不误拒绝。Hyperliquid/dYdX既有响应后目录检查增加适配器替换检查。
- 新8项离线回归覆盖目录/原生字段/client/adapter替换与删除、两个排队边界和无关元数据变更。未改变资金费来源、单位、未知周期或新鲜度门槛；不把历史记录解释为账户支付。
- 身份复核：Gate/Bitget/KuCoin/Coinbase Exchange公开证据、平台覆盖统计和可撤销批量审核已有实现；证据的客户端与现货范围必须精确匹配，不向永续或Coinbase Advanced Trade隐式扩展。现有199组演练候选不是生产批准，本批没有新增身份批准或声称覆盖扩大。缺少可靠官方合同证据的来源继续未知。
- 复现命令：`.venv/bin/pytest -q tests/test_funding_history.py tests/test_hyperliquid_funding_history.py tests/test_dydx_funding_history.py`。本批仅合成测试、无实网调用；未重试受阻OKX WS或修改独立环境。24小时未完成与来源归因不明仍开放。

### R45 原生历史接收边界与来源修订（2026-11-04）

- 父线程对715d1f3的离线扰动：结算时间比网络返回超前10ms、观察者等待40ms，可被误收；相同费率高度1234→1235静默沿用旧高度。这是合成复现，不是链上真实异常证据。
- PublicREST在HTTP客户端返回后、JSON解码及诊断观察者之前捕获receipt，通过dict/list兼容容器属性随响应传递，不污染原生JSON字段、不使用共享last_received。dYdX及Hyperliquid历史读取该receipt；取消观察者不保存，后续请求独立取时。边界是HTTP客户端完成响应，不宣称socket首字节时间；自定义普通dict/list测试替身仍以方法返回为后备。
- 历史修订比较全部持久化内容（排除received_at/first_received_at/last_seen_at/revision四项观察字段），包括区块高度、原生时间表示及来源。来源变化会追加旧完整记录到funding_revisions，当前记录revision递增并保留first_received；真正相同记录仅更新last_seen。不把原生高度当作链上最终性证明。
- 新5项合成回归：未来10ms/过去10ms+观察者40ms、取消后无写入且下一请求新receipt、同费率区块高度修订并重复去重、Hyperliquid延迟receipt；85项历史专项通过。已有原生fixture统一冻结两个模块的时钟，保持原断言。
- 未访问受阻OKX WS、未改独立环境或账户，无实网/浏览器验收；24小时、生产广泛身份覆盖仍开放。

### R46 HTX线性永续公开已结算历史（2026-11-04）

- [官方USDT文档](https://huobiapi.github.io/docs/usdt_swap/v1/en/#general-query-historical-funding-rate)此次可读，提供GET `/linear-swap-api/v1/swap_historical_funding_rate`、`realized_rate`及原生`contract_code/symbol/fee_asset/funding_time`。显式接该v1端点；安装版CCXT统一线性历史已走v5，字段不同，本批未审核v5、不混用或失败后降级。
- 请求固定文档主机`https://api.hbdm.com`，使用现有无凭证客户端公共fetch及throttle(1)、适配器并发上限、总超时；不修改其urls/options，不继承库默认`api.hbdm.vn`，不重试/切主机。当前v1实网可用性未知，文档可读不证明接口仍可用。
- 范围仅准确原生base-USDT、linear=true/inverse=false、quote/settle=USDT永续。只保存realized_rate，保留advertised funding_rate供审计；错contract/base/fee资产、非有限/缺失率、未来/秒级时间拒绝。历史周期保持null，原生资金费是每次结算的小数比例，不乘合约乘数，不是账户账单。
- 单请求page_index=1、最多50行；要求status=ok/current_page=1/有界数组，since本地过滤、next_since=null、不宣称完整回填。目录在限流及网络等待后变化则拒绝。该路径新增14项离线回归，含安装版CCXT底层公共fetch的准确URL/参数/无鉴权和共享配置不变；99项联合历史专项通过。
- 身份资料复核：Coinbase官方Exchange products/currencies与Advanced Trade仍为不同client，未因同ticker合并；HTX公开币种元数据尚无充分合约地址证据，未新增生产身份批准。本批没有任何行情主机请求，不触发受阻OKX WS，不修改独立环境。
- 独立复测：在临时data目录构建CexAdapter('htx')并discover，选准确USDT永续后调用FundingHistoryService.get(id,refresh=True,limit=50)，核对原始realized_rate/fee_asset/funding_time。出现拒绝立即停止，不换域名；保留返回状态/时间，不记录凭证。此步骤尚未执行，24小时验收仍开放。
- 下一项：核对Coinbase Exchange公开目录与现有同client身份证据的衔接，先确认产品和报价时间语义，再决定是否增加行情client；不会直接扩张Advanced Trade身份白名单。
