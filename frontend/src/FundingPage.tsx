import { useEffect, useRef, useState } from "react";
import { ArrowDownUp, Clock, Database, Globe, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { api, num, pct, time } from "./api";

type Validity = {
  valid: boolean; reason: string | null; source: string | null; source_at: number | null;
  received_at: number | null; age_seconds: number | null; metadata_observation: boolean;
};
type FundingCell = {
  instrument_id: string; venue: string; native_symbol: string; quote: string;
  raw_rate: number | null; kind: string | null; interval_hours: number | null;
  normalized_8h_rate: number | null; next_funding_at: number | null;
  missing: string[]; warnings: string[];
};
type FundingRow = FundingCell & {
  base: string; asset_id: string; identity_status: string; identity_blockers: string[];
  field_validity: Record<string, Validity>; source: string | null;
};
type FundingGroup = {
  id: string; asset_id: string; symbols: string[]; venues: string[]; cells: FundingCell[];
  spread_8h_rate: number | null; funding_kind: string | null; warnings: string[]; missing: string[];
};
type FundingResult = {
  as_of: number; enabled: boolean; rows: FundingRow[]; groups: FundingGroup[];
  stats: { perpetual_markets: number; fresh_rates: number; normalized_rates: number; unknown_periods: number;
    unverified_markets: number; rows_total: number; rows_returned: number; groups_total: number;
    comparable_groups: number; rows_truncated: boolean; groups_truncated: boolean; groups_returned: number };
};
type HistoryRecord = { settlement_at: number; funding_rate: number; funding_interval_hours: number | null;
  source: string; received_at: number; revision?: number; event_type?: string };
type HistoryResult = { instrument_id: string; records: HistoryRecord[];
  summary: { count: number; refreshed: boolean; truncated?: boolean; next_since?: number | null; issues?: string[] } };

const kindLabel: Record<string, string> = { current: "当前估算", predicted: "预测", settled: "已结算值快照" };
const identityLabel: Record<string, string> = {
  verified: "身份已核验", unverified: "身份待核验", confirmation_required: "跨平台待确认", conflict: "身份冲突",
};
const reasonLabel: Record<string, string> = {
  funding_rate_missing: "未提供费率", funding_kind_missing: "未提供费率类型",
  funding_kind_unknown: "费率类型未知", funding_interval_hours_missing: "实际周期未知",
  next_funding_at_missing: "下次结算时间未知", funding_rate_stale: "费率已过期",
  funding_interval_hours_stale: "周期元数据已过期", next_funding_at_stale: "结算时间数据已过期",
  funding_kind_stale: "费率类型已过期", snapshot_missing: "尚未采集到快照",
  source_timestamp_missing: "源事件时间未提供，仅依据本地观测",
  funding_kinds_differ: "费率类型不同，未直接计算费差",
  two_venues_with_valid_normalized_rates_required: "至少需要两个平台的有效归一化费率",
  normalized_rate_difference_is_not_settlement_cashflow: "归一化费差不是实际结算现金流",
  quotes_differ_equal_valuation_notionals_required: "报价币不同，比较需同估值名义规模",
  settlement_times_differ: "实际结算时间不同",
  observed_settled_rate_is_not_a_settlement_history_series: "此值是本地观测快照，不是已结算历史序列",
  settled_snapshots_are_not_forward_estimates: "已结算快照不是未来费率估计",
  next_funding_time_has_elapsed: "下次结算时间已到，等待源端更新",
  funding_interval_observation_time_missing: "周期缺少明确观测时间",
  snapshot_invalid: "快照质量校验未通过", demo_snapshot_excluded: "演示快照已排除",
};
const explain = (value: string) => reasonLabel[value] ?? value;
const period = (hours: number | null) => hours == null ? "周期未知" : `${num(hours, 4)} 小时`;
function countdown(timestamp: number | null, clock: number) {
  if (timestamp == null) return "结算时间未知";
  const remaining = Math.floor((timestamp - clock) / 1000);
  if (remaining <= 0) return "结算时间已到 · 待更新";
  return `${Math.floor(remaining / 3600)}:${String(Math.floor(remaining % 3600 / 60)).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}`;
}

export function FundingPage() {
  const [data, setData] = useState<FundingResult | null>(null);
  const [sort, setSort] = useState("rate");
  const [basis, setBasis] = useState("normalized8h");
  const [limit, setLimit] = useState(500);
  const [reload, setReload] = useState(0);
  const [view, setView] = useState("ranking");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(Date.now());
  const [history, setHistory] = useState<HistoryResult | null>(null);
  const [historyMarket, setHistoryMarket] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const historyRequest = useRef(0);

  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => { clearInterval(timer); historyRequest.current += 1; };
  }, []);
  useEffect(() => {
    let current = true;
    setLoading(true); setError("");
    api<FundingResult>(`/api/funding?sort=${sort}&rate_basis=${basis}&limit=${limit}`)
      .then((result) => { if (current) setData(result); })
      .catch((e: Error) => { if (current) setError(e.message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [sort, basis, limit, reload]);

  const loadHistory = async (row: FundingRow) => {
    const requestId = ++historyRequest.current;
    setHistoryMarket(`${row.venue} · ${row.native_symbol}`);
    setHistory(null); setHistoryError(""); setHistoryLoading(true);
    try {
      const result = await api<HistoryResult>(`/api/funding/history/${encodeURIComponent(row.instrument_id)}?refresh=true&limit=100`);
      if (requestId === historyRequest.current) setHistory(result);
    } catch (e) {
      if (requestId === historyRequest.current) setHistoryError((e as Error).message);
    } finally {
      if (requestId === historyRequest.current) setHistoryLoading(false);
    }
  };
  const sortRate = (nextBasis: string) => { setSort("rate"); setBasis(nextBasis); };
  const stats = data?.stats;
  const venues = [...new Set(data?.groups.flatMap((group) => group.venues) ?? [])].sort();
  return <>
    <div className="stats">
      <div className="stat"><span className="stat-label">全目录永续市场 <Globe size={17} /></span>
        <strong>{num(stats?.perpetual_markets, 0)}</strong><small>启用平台 · 活跃市场 · 不限关注列表</small></div>
      <div className="stat"><span className="stat-label">查询时有效费率 <Database size={17} /></span>
        <strong>{num(stats?.fresh_rates, 0)}</strong><small>可归一化 {num(stats?.normalized_rates, 0)} 个市场</small></div>
      <div className="stat"><span className="stat-label">实际周期未知 <Clock size={17} /></span>
        <strong>{num(stats?.unknown_periods, 0)}</strong><small>缺少周期时保留原始费率</small></div>
      <div className="stat"><span className="stat-label">同资产跨平台组 <ShieldCheck size={17} /></span>
        <strong>{num(stats?.groups_total, 0)}</strong><small>可比费差 {num(stats?.comparable_groups, 0)} 组 · 待核验市场 {num(stats?.unverified_markets, 0)}</small></div>
    </div>
    <section className="panel" aria-labelledby="funding-heading">
      <div className="panel-heading"><div><h2 id="funding-heading">全市场资金费率 <span className="badge amber">本地公共快照</span></h2>
        <p>保留实际周期与费率类型。八小时归一化用于比较，不代表实际结算或持有收益。</p></div>
        <button className="button secondary" disabled={loading} onClick={() => setReload((old) => old + 1)}>
          {loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}手动刷新</button></div>
      <div className="filters" style={{ flexWrap: "wrap" }}>
        <button className={`button secondary ${view === "ranking" ? "selected" : ""}`} onClick={() => setView("ranking")}>费率排行榜</button>
        <button className={`button secondary ${view === "matrix" ? "selected" : ""}`} onClick={() => setView("matrix")}>同资产跨平台矩阵</button>
        <select aria-label="资金费率排序" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="rate">费率降序</option><option value="spread">跨平台费差降序</option><option value="asset">按资产</option></select>
        <select aria-label="费率排序口径" value={basis} onChange={(e) => { setBasis(e.target.value); setSort("rate"); }}>
          <option value="normalized8h">8h 归一化</option><option value="raw">原始实际周期</option></select>
        <select aria-label="资金费率返回条数" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {[100, 500, 1000, 5000].map((count) => <option key={count} value={count}>最多 {count} 项</option>)}</select>
      </div>
      <div className="notice" role="status">检查时间 {time(data?.as_of)}。当前估算、预测与已结算值快照分别标注；未知周期不按八小时填补。历史记录需逐市场手动获取。</div>
      {error && <div className="notice error" role="alert">资金费视图获取失败：{error}{data ? "；保留上次查询结果，请核对时间。" : ""}</div>}
      {data?.enabled === false && <div className="notice">演示模式下不展示真实资金费排行，请切换采集模式。</div>}
      {(stats?.rows_truncated || stats?.groups_truncated) && <div className="notice">返回结果已截断：市场 {stats?.rows_returned}/{stats?.rows_total}，资产组 {stats?.groups_returned}/{stats?.groups_total}。可调整返回条数；排序已覆盖服务器全部可用目录。</div>}
      {view === "ranking" ? <div className="table-scroll"><table>
        <thead><tr><th>资产 / 平台市场</th><th><button className="text-button" onClick={() => sortRate("raw")}>原始费率 <ArrowDownUp size={12} /></button></th>
          <th>类型 / 实际周期</th><th><button className="text-button" onClick={() => sortRate("normalized8h")}>8h 归一化 <ArrowDownUp size={12} /></button></th>
          <th>下次结算 / 倒计时</th><th>数据时间 / 身份 / 来源</th></tr></thead>
        <tbody>{data?.rows.map((row) => <tr key={row.instrument_id}>
          <td><strong>{row.base} · {row.venue}</strong><small className="muted">{row.native_symbol}</small><small>{row.quote} 报价</small></td>
          <td className="mono">{pct(row.raw_rate)}</td><td>{row.kind ? kindLabel[row.kind] ?? row.kind : "类型未知"}<small className="muted">{period(row.interval_hours)}</small></td>
          <td className="mono">{pct(row.normalized_8h_rate)}</td>
          <td><span className="mono">{countdown(row.next_funding_at, clock)}</span><small className="muted">{time(row.next_funding_at)}</small></td>
          <td><span className={`badge ${row.identity_status === "verified" ? "mint" : "amber"}`}>{identityLabel[row.identity_status] ?? row.identity_status}</span>
            <small className="muted">源事件 {time(row.field_validity.funding_rate?.source_at)}</small>
            <small className="muted">本地接收 {time(row.field_validity.funding_rate?.received_at)}</small>
            {row.missing.map((reason) => <small className="warning" key={reason}>{explain(reason)}</small>)}
            {row.warnings.map((reason) => <small className="muted" key={reason}>{explain(reason)}</small>)}
            <details className="raw-details"><summary>来源与逐字段有效状态</summary><pre>{JSON.stringify({ source: row.source, fields: row.field_validity, identity: row.identity_blockers }, null, 2)}</pre></details>
            <button className="text-button" disabled={historyLoading} onClick={() => void loadHistory(row)}><Database size={12} />获取已结算历史</button></td>
        </tr>)}</tbody></table></div> : <div className="table-scroll"><table>
        <thead><tr><th>已核验资产 / 最大跨平台 8h 费差</th>{venues.map((venue) => <th key={venue}>{venue}</th>)}</tr></thead>
        <tbody>{data?.groups.map((group) => <tr key={group.id}><td><strong>{group.symbols.join(" / ")}</strong>
          <small className="mono">{pct(group.spread_8h_rate)}</small><small>{group.funding_kind ? kindLabel[group.funding_kind] : "类型未知或不可比"}</small>
          {[...group.missing, ...group.warnings].map((reason) => <small className="muted" key={reason}>{explain(reason)}</small>)}</td>
          {venues.map((venue) => <td key={venue}>{group.cells.some((cell) => cell.venue === venue) ? group.cells.filter((cell) => cell.venue === venue).map((cell) => <div key={cell.instrument_id} style={{ marginBottom: 12 }}>
            <strong>{cell.native_symbol}</strong><small className="mono">原始 {pct(cell.raw_rate)} / {period(cell.interval_hours)}</small>
            <small className="mono">8h {pct(cell.normalized_8h_rate)}</small><small>{cell.kind ? kindLabel[cell.kind] : "类型未知"} · {cell.quote}</small>
            <small className="muted">{countdown(cell.next_funding_at, clock)}</small>
            {cell.missing.map((reason) => <small className="warning" key={reason}>{explain(reason)}</small>)}</div>) : <span className="muted">未提供市场</span>}</td>)}</tr>)}</tbody>
      </table></div>}
      {(!data || (view === "ranking" ? !data.rows.length : !data.groups.length)) && <div className="empty"><Database size={25} />
        <h3>{loading ? "正在读取全目录资金费率" : view === "ranking" ? "暂无永续市场数据" : "暂无已核验的跨平台资产组"}</h3>
        <p>{view === "matrix" ? "同名但未确认的资产保留在排行榜独立展示，可到资产映射入口核验。" : "启用平台发现市场后将列出全部活跃永续市场；缺少费率也会明确显示。"}</p></div>}
      <div className="panel-foot">正费率通常由多头支付、空头收取；实际规则与结算时间以平台数据为准。跨平台费差未计成本、规模和执行条件。</div>
    </section>
    {historyMarket && <section className="panel" aria-labelledby="funding-history-heading">
      <div className="panel-heading"><div><h2 id="funding-history-heading">已结算公共历史 · {historyMarket}</h2><p>仅本次手动请求的市场。实际历史周期缺失时显示未知。</p></div>
        <button className="button secondary" onClick={() => { historyRequest.current += 1; setHistoryMarket(""); setHistoryLoading(false); }}>关闭</button></div>
      {historyLoading && <div className="notice"><Loader2 size={15} className="spin" />正在向该平台公共历史接口读取，未请求其它市场。</div>}
      {historyError && <div className="notice error" role="alert">已结算历史获取失败：{historyError}</div>}
      {history?.summary.issues?.map((issue, index) => <div className="notice" key={index}>{issue}</div>)}
      {history?.records.length ? <div className="table-scroll"><table><thead><tr><th>实际结算时间</th><th>已结算原始费率 / 事件类型</th><th>实际历史周期</th><th>来源 / 本地接收</th></tr></thead>
        <tbody>{history.records.map((record, index) => <tr key={`${record.settlement_at}:${index}`}><td>{time(record.settlement_at)}</td><td className="mono">{pct(record.funding_rate)}
          <small className="muted">{record.event_type === "regular" ? "常规结算" : record.event_type === "special" ? "特殊结算" : "事件类型未提供"}</small></td>
          <td>{period(record.funding_interval_hours)}{record.funding_interval_hours == null && <small className="muted">不使用当前周期回填历史</small>}</td>
          <td><small className="break">{record.source}</small><small className="muted">本地接收 {time(record.received_at)}</small></td></tr>)}</tbody></table></div>
        : history && <div className="empty"><h3>未返回已结算记录</h3><p>这不等于该市场没有结算历史，请核对接口覆盖与本次查询范围。</p></div>}
      {history?.summary.truncated && <div className="notice">仅显示本次返回的前 100 条记录，不代表全部历史。</div>}
    </section>}
  </>;
}
