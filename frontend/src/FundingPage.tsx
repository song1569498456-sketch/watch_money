import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Clock, Database, Globe, Loader2, RefreshCw, Search, ShieldCheck, X } from "lucide-react";
import { api, num, pct, time } from "./api";
import { fundingComparison, fundingFieldFresh, fundingGroups, fundingMarketState } from "./fundingMatrix";
import type { FundingMarket, FundingResult } from "./fundingMatrix";
import "./funding.css";

type HistoryRecord = { settlement_at: number; funding_rate: number; funding_interval_hours: number | null;
  source: string; received_at: number; event_type?: string };
type HistoryResult = { records: HistoryRecord[]; summary: { truncated?: boolean; issues?: string[] } };
const kindLabel: Record<string, string> = { current: "当前估算", predicted: "预测", settled: "已结算值快照" };
const identityLabel: Record<string, string> = { verified: "身份已核验", unverified: "身份待核验", confirmation_required: "跨平台待确认", conflict: "身份冲突" };
const reasonLabel: Record<string, string> = {
  funding_rate_missing: "未提供费率", funding_kind_missing: "未提供费率类型", funding_kind_unknown: "费率类型未知",
  funding_interval_hours_missing: "实际周期未知", next_funding_at_missing: "下次结算时间未知",
  funding_rate_stale: "费率已过期", funding_interval_hours_stale: "周期元数据已过期",
  next_funding_at_stale: "结算时间数据已过期", funding_kind_stale: "费率类型已过期", snapshot_missing: "尚未采集到快照",
  source_timestamp_missing: "源事件时间未提供，仅依据本地观测", funding_kinds_differ: "存在不同费率类型；仅在同类型内比较，其余类型独立核对",
  two_venues_with_valid_normalized_rates_required: "至少需要两个平台的有效归一化费率",
  normalized_rate_difference_is_not_settlement_cashflow: "归一化费差不是实际结算现金流",
  quotes_differ_equal_valuation_notionals_required: "报价币不同，比较需同估值名义规模", settlement_times_differ: "实际结算时间不同",
  observed_settled_rate_is_not_a_settlement_history_series: "此值是本地观测快照，不是已结算历史序列",
  settled_snapshots_are_not_forward_estimates: "已结算快照不是未来费率估计", next_funding_time_has_elapsed: "下次结算时间已到，等待源端更新",
  funding_interval_observation_time_missing: "周期缺少明确观测时间", snapshot_invalid: "快照质量校验未通过",
  demo_snapshot_excluded: "演示快照已排除", unverified_asset_mapping: "资产映射未核验",
};
const explain = (value: string) => reasonLabel[value] ?? value;
const period = (hours: number | null | undefined) => hours == null ? "周期未知" : `${num(hours, 4)} 小时`;
function rate(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "未提供";
  return value !== 0 && Math.abs(value * 100) < 0.00005 ? `${value > 0 ? "+" : ""}${Number((value * 100).toPrecision(5))}%` : pct(value);
}
function countdown(timestamp: number | null | undefined, now: number) {
  if (timestamp == null) return "结算时间未知";
  const remaining = Math.floor((timestamp - now) / 1000);
  if (remaining <= 0) return "结算时间已到 · 待更新";
  return `${Math.floor(remaining / 3600)}:${String(Math.floor(remaining % 3600 / 60)).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}`;
}

type FundingCandidate = { buy_instrument_id: string; sell_instrument_id: string; strategy: "perp_spread" };
export const FundingPage = memo(function FundingPage({ active = true, onSelectCandidate }: {
  active?: boolean; onSelectCandidate?: (candidate: FundingCandidate) => void;
}) {
  const [data, setData] = useState<FundingResult | null>(null);
  const [venues, setVenues] = useState<string[]>([]);
  const [selectedVenues, setSelectedVenues] = useState<string[] | null>(null);
  const [rankingVenue, setRankingVenue] = useState("");
  const [sort, setSort] = useState("spread");
  const [basis, setBasis] = useState("normalized8h");
  const [limit, setLimit] = useState(500);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [reload, setReload] = useState(0);
  const [view, setView] = useState("matrix");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(Date.now());
  const [selected, setSelected] = useState<{ row: FundingMarket; asOf: number } | null>(null);
  const [history, setHistory] = useState<HistoryResult | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const historyRequest = useRef(0);
  const previousQuery = useRef("");
  const activeVenues = useMemo(() => selectedVenues === null ? venues : venues.filter((venue) => selectedVenues.includes(venue)), [venues, selectedVenues]);
  const platformsKey = selectedVenues === null ? "*" : [...selectedVenues].sort().join(",");
  const requestQuery = useMemo(() => {
    const params = new URLSearchParams({ sort, rate_basis: view === "matrix" ? "normalized8h" : basis, limit: String(limit), search: query });
    if (view === "ranking" && rankingVenue) params.set("venue", rankingVenue);
    if (view === "matrix" && platformsKey !== "*") params.set("platforms", platformsKey);
    return params.toString();
  }, [sort, view, basis, limit, query, rankingVenue, platformsKey]);
  useEffect(() => {
    if (!active) return;
    setClock(Date.now());
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  useEffect(() => () => { historyRequest.current += 1; }, []);
  useEffect(() => { const timer = setTimeout(() => setQuery(search.trim()), 250); return () => clearTimeout(timer); }, [search]);
  useEffect(() => {
    let current = true;
    if (previousQuery.current !== requestQuery) setData(null);
    previousQuery.current = requestQuery;
    setLoading(true); setError("");
    void api<FundingResult>(`/api/funding?${requestQuery}`).then((result) => {
      if (!current) return;
      setData(result); setClock(Date.now());
      setVenues(result.venues ?? [...new Set([...result.rows.map((row) => row.venue), ...result.groups.flatMap((group) => group.venues)])].sort());
    }).catch((caught) => { if (current) setError((caught as Error).message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [requestQuery, reload]);
  const groups = useMemo(() => data ? fundingGroups(data) : [], [data]);
  const marketMap = useMemo(() => new Map([...groups.flatMap((group) => group.cells), ...(data?.rows ?? [])].map((row) => [row.instrument_id, row])), [data, groups]);
  useEffect(() => {
    if (!data) return;
    setSelected((old) => old && marketMap.has(old.row.instrument_id) ? { row: marketMap.get(old.row.instrument_id)!, asOf: data.as_of } : old);
  }, [data, marketMap]);
  const matrix = useMemo(() => groups.map((group) => ({ group, comparison: fundingComparison(group, activeVenues, data?.as_of ?? clock, clock) }))
    .sort((left, right) => sort === "asset" ? left.group.symbols.join().localeCompare(right.group.symbols.join()) :
      Number(left.comparison.spread == null) - Number(right.comparison.spread == null) ||
      (right.comparison.spread ?? 0) - (left.comparison.spread ?? 0) || left.group.id.localeCompare(right.group.id)),
  [groups, activeVenues, data?.as_of, clock, sort]);
  const ranking = useMemo(() => [...(data?.rows ?? [])].filter((row) => !rankingVenue || row.venue === rankingVenue)
    .sort((left, right) => {
      if (sort === "asset") return (left.base ?? left.native_symbol).localeCompare(right.base ?? right.native_symbol);
      const value = (row: FundingMarket) => basis === "raw" ? fundingFieldFresh(row, "funding_rate", data!.as_of, clock) &&
        fundingFieldFresh(row, "funding_kind", data!.as_of, clock) ? row.raw_rate : null :
        fundingMarketState(row, data!.as_of, clock).code === "fresh" ? row.normalized_8h_rate : null;
      return Number(value(left) == null) - Number(value(right) == null) || (value(right) ?? 0) - (value(left) ?? 0);
    }), [data, rankingVenue, sort, basis, clock]);
  const stats = data?.stats;
  const selectedGroup = selected ? groups.find((group) => group.cells.some((row) => row.instrument_id === selected.row.instrument_id)) : null;
  const selectedPair = selectedGroup ? fundingComparison(selectedGroup, activeVenues, data?.as_of ?? clock, clock) : null;
  const showRanking = (venue?: string) => { setRankingVenue(venue ?? activeVenues[0] ?? venues[0] ?? ""); setSort("rate"); setView("ranking"); };
  const openMarket = (row: FundingMarket) => {
    historyRequest.current += 1; setHistory(null); setHistoryError(""); setHistoryLoading(false);
    setSelected({ row: marketMap.get(row.instrument_id) ?? row, asOf: data?.as_of ?? clock });
  };
  const closeMarket = () => { historyRequest.current += 1; setSelected(null); setHistoryLoading(false); };
  const loadHistory = async (refresh: boolean) => {
    if (!selected) return;
    const requestId = ++historyRequest.current;
    setHistory(null); setHistoryError(""); setHistoryLoading(true);
    try {
      const result = await api<HistoryResult>(`/api/funding/history/${encodeURIComponent(selected.row.instrument_id)}?refresh=${refresh}&limit=100`);
      if (requestId === historyRequest.current) setHistory(result);
    } catch (caught) { if (requestId === historyRequest.current) setHistoryError((caught as Error).message); }
    finally { if (requestId === historyRequest.current) setHistoryLoading(false); }
  };
  const toggleVenue = (venue: string) => setSelectedVenues((old) => {
    const current = old ?? venues;
    return current.includes(venue) ? current.filter((value) => value !== venue) : [...current, venue];
  });
  const marketButton = (row: FundingMarket, symbol: string) => {
    const state = fundingMarketState(row, data?.as_of ?? clock, clock);
    return <button type="button" className={`funding-market-cell funding-${state.tone}`} key={row.instrument_id}
      aria-label={`查看 ${symbol} · ${row.venue} · ${row.native_symbol} 详情`} onClick={() => openMarket(row)}>
      <span className={`funding-state funding-${state.tone}`}>{state.label}</span>
      <strong className="mono">{state.code === "fresh" ? rate(row.normalized_8h_rate) : "—"}<small> / 8h 比较</small></strong>
      <span className="funding-raw">{state.code === "stale" ? "最后原始" : "原始"} {rate(row.raw_rate ?? row.observed?.funding_rate)} / {period(row.interval_hours ?? row.observed?.funding_interval_hours)}</span>
      <span className="muted">{row.native_symbol} · {row.quote}</span><span className="funding-cell-action">查看详情</span>
    </button>;
  };
  return <div className="funding-page">
    <div className="stats">
      <div className="stat"><span className="stat-label">范围内永续市场 <Globe size={17} /></span><strong>{num(stats?.perpetual_markets, 0)}</strong><small>按当前平台与搜索范围</small></div>
      <div className="stat"><span className="stat-label">查询时有效费率 <Database size={17} /></span><strong>{num(stats?.fresh_rates, 0)}</strong><small>可归一化 {num(stats?.normalized_rates, 0)} 个市场</small></div>
      <div className="stat"><span className="stat-label">实际周期未知 <Clock size={17} /></span><strong>{num(stats?.unknown_periods, 0)}</strong><small>保留原始费率，不默认填8小时</small></div>
      <div className="stat"><span className="stat-label">身份待核验市场 <ShieldCheck size={17} /></span><strong>{num(stats?.unverified_markets, 0)}</strong><small>同名资产保留独立身份</small></div>
    </div>
    <section className="panel" aria-labelledby="funding-heading">
      <div className="panel-heading"><div><h2 id="funding-heading">全市场资金费率</h2><p>默认按同资产跨平台比较；8h费差仅供比较，不代表实际结算收益。</p></div>
        <button type="button" className="button secondary" disabled={loading} onClick={() => setReload((old) => old + 1)}>{loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}手动刷新</button></div>
      <div className="funding-toolbar">
        <div className="funding-view-tabs"><button type="button" className={`button secondary ${view === "matrix" ? "selected" : ""}`} aria-pressed={view === "matrix"}
          onClick={() => { setView("matrix"); setSort("spread"); }}>同资产跨平台矩阵</button><button type="button" className={`button secondary ${view === "ranking" ? "selected" : ""}`}
            aria-pressed={view === "ranking"} onClick={() => showRanking()}>费率排行榜</button></div>
        <div className="search"><Search size={16} /><input aria-label="资金费资产搜索" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索全目录资产、身份或交易对…" /></div>
        {view === "matrix" ? <details className="funding-platform-picker"><summary>平台筛选 · {activeVenues.length}/{venues.length}</summary>
          <div className="funding-platform-options"><div><button type="button" className="text-button" onClick={() => setSelectedVenues(null)}>全选平台</button><button type="button" className="text-button" onClick={() => setSelectedVenues([])}>清空平台</button></div>
            {venues.map((venue) => <label key={venue}><input type="checkbox" aria-label={`资金费平台 ${venue}`} checked={activeVenues.includes(venue)} onChange={() => toggleVenue(venue)} />{venue}</label>)}</div></details> :
          <select aria-label="排行平台" value={rankingVenue} onChange={(event) => setRankingVenue(event.target.value)}>{!rankingVenue && <option value="">选择单个平台</option>}{venues.map((venue) => <option key={venue}>{venue}</option>)}</select>}
        <select aria-label="资金费率排序" value={sort} onChange={(event) => setSort(event.target.value)}>{view === "matrix" ? <option value="spread">最大跨平台8h费差</option> : <option value="rate">单平台费率降序</option>}<option value="asset">按资产</option></select>
        {view === "ranking" && <select aria-label="费率排序口径" value={basis} onChange={(event) => { setBasis(event.target.value); setSort("rate"); }}><option value="normalized8h">8h归一化比较</option><option value="raw">原始实际周期</option></select>}
        <select aria-label="资金费率返回条数" value={limit} onChange={(event) => setLimit(Number(event.target.value))}>{[100, 500, 1000, 5000].map((count) => <option key={count} value={count}>最多 {count} 项</option>)}</select>
      </div>
      <div className="funding-caption" role="status">数据检查时间 {time(data?.as_of)}。有效性随字段TTL变为过期，页面计时不会刷新源时间。<span>“无市场”仅指当前已发现目录与可确认身份范围内未列出市场，不证明平台不存在该资产；未核验身份不计算跨平台费差。</span></div>
      {error && <div className="notice error" role="alert">资金费视图获取失败：{error}{data ? "；保留上次检查结果。" : ""}</div>}
      {data?.enabled === false && <div className="notice">演示模式下不展示真实资金费视图。</div>}
      {(stats?.rows_truncated || stats?.matrix_groups_truncated) && <div className="notice">本次返回已截断：市场 {stats.rows_returned}/{stats.rows_total}，矩阵资产 {stats.matrix_groups_returned}/{stats.matrix_groups_total}。当前排序仅展示本次返回范围；可增加条数或搜索资产，不能据此判断全部市场最优。</div>}
      {view === "matrix" ? <div className="funding-table-wrap"><table className="funding-matrix-table" style={{ minWidth: 540 + activeVenues.length * 185 }}>
        <colgroup><col style={{ width: 150 }} /><col style={{ width: 155 }} /><col style={{ width: 235 }} />{activeVenues.map((venue) => <col key={venue} style={{ width: 185 }} />)}</colgroup>
        <thead><tr><th>资产 / 身份</th><th>最大8h费差<small>费率差，非收益</small></th><th>对应平台 / 市场</th>{activeVenues.map((venue) => <th key={venue}><button type="button" className="text-button" title={`查看${venue}单平台排行`} onClick={() => showRanking(venue)}>{venue}</button></th>)}</tr></thead>
        <tbody>{matrix.map(({ group, comparison }) => <tr key={group.id} data-funding-asset={group.id}>
          <td className="funding-asset"><strong>{group.symbols.join(" / ")}</strong><small className={group.identity_status === "verified" ? "muted" : "warning"}>{identityLabel[group.identity_status ?? "unverified"] ?? "身份待核验"}</small><small className="funding-identity" title={group.asset_id}>{group.asset_id}</small>
            {!!group.warnings.filter((item) => item !== "normalized_rate_difference_is_not_settlement_cashflow").length && <small className="warning">{group.warnings.filter((item) => item !== "normalized_rate_difference_is_not_settlement_cashflow").map(explain).join("；")}</small>}</td>
          <td className="funding-spread"><strong className="mono">{comparison.spread == null ? "不可比" : rate(comparison.spread)}</strong><small>{comparison.spread == null ? comparison.reason : `${kindLabel[comparison.low?.kind ?? ""] ?? "同类型"}内8h归一化费率相减`}</small></td>
          <td>{comparison.high && comparison.low ? <><strong>高：{comparison.high.venue}</strong><small>{comparison.high.native_symbol} · {rate(comparison.high.normalized_8h_rate)} / 8h</small><strong>低：{comparison.low.venue}</strong><small>{comparison.low.native_symbol} · {rate(comparison.low.normalized_8h_rate)} / 8h</small><small className="muted">{kindLabel[comparison.high.kind ?? ""] ?? "类型未知"}</small></> : <span className="muted">—</span>}</td>
          {activeVenues.map((venue) => <td key={venue}>{group.cells.some((cell) => cell.venue === venue) ? group.cells.filter((cell) => cell.venue === venue).map((cell) => marketButton(cell, group.symbols.join(" / "))) : <span className="funding-no-market">无市场</span>}</td>)}</tr>)}</tbody>
      </table></div> : <div className="funding-table-wrap"><table className="funding-ranking-table"><thead><tr><th>资产 / 市场</th><th>状态</th><th>原始费率 / 实际周期</th><th>8h归一化比较</th><th>费率类型 / 下次结算</th><th>源时间 / 操作</th></tr></thead>
        <tbody>{ranking.map((row) => {
          const state = fundingMarketState(row, data!.as_of, clock);
          return <tr key={row.instrument_id}><td><strong>{row.base} · {row.venue}</strong><small>{row.native_symbol} · {row.quote}</small><small className={row.identity_status === "verified" ? "muted" : "warning"}>{identityLabel[row.identity_status ?? "unverified"]}</small></td>
            <td><span className={`funding-state funding-${state.tone}`}>{state.label}</span></td><td className="mono">{rate(row.raw_rate ?? row.observed?.funding_rate)}<small>{period(row.interval_hours ?? row.observed?.funding_interval_hours)}{state.code === "stale" ? " · 最后观测" : ""}</small></td>
            <td className="mono">{state.code === "fresh" ? rate(row.normalized_8h_rate) : "—"}</td><td>{kindLabel[row.kind ?? row.observed?.funding_kind ?? ""] ?? "类型未知"}<small>{countdown(row.next_funding_at ?? row.observed?.next_funding_at, clock)}</small></td>
            <td><small>源事件 {time(row.field_validity?.funding_rate?.source_at)}</small><small>本地接收 {time(row.field_validity?.funding_rate?.received_at)}</small><button type="button" className="text-button" onClick={() => openMarket(row)}>查看详情</button></td></tr>;
        })}</tbody></table></div>}
      {(!data || (view === "matrix" ? !matrix.length : !ranking.length)) && <div className="empty"><Database size={25} /><h3>{loading ? "正在读取资金费目录" : view === "matrix" && !activeVenues.length ? "请选择平台" : "本次范围暂无永续市场"}</h3><p>可调整平台、搜索词或返回条数。缺少行情与确定没有市场分别展示。</p></div>}
      <div className="panel-foot">8h仅用于同口径比较；实际周期、类型、结算时点与持仓规模不同，费用和执行条件另行核对。</div>
    </section>
    {selected && <aside className="funding-detail panel" aria-label="资金费详情">
      <div className="panel-heading"><div><h2>资金费详情 · {selected.row.base ?? selected.row.native_symbol} · {selected.row.venue}</h2><p>{selected.row.native_symbol} · {selected.row.quote} 报价</p></div><button type="button" className="icon-button" aria-label="关闭资金费详情" onClick={closeMarket}><X size={18} /></button></div>
      <div className="funding-detail-scroll"><div className="form-content"><span className={`funding-state funding-${fundingMarketState(selected.row, selected.asOf, clock).tone}`}>{fundingMarketState(selected.row, selected.asOf, clock).label}</span>
        <p>原始 {rate(selected.row.raw_rate ?? selected.row.observed?.funding_rate)} / {period(selected.row.interval_hours ?? selected.row.observed?.funding_interval_hours)}</p>
        <p>{kindLabel[selected.row.kind ?? selected.row.observed?.funding_kind ?? ""] ?? "类型未知"} · 8h比较 {fundingMarketState(selected.row, selected.asOf, clock).code === "fresh" ? rate(selected.row.normalized_8h_rate) : "不可用"}</p>
        <p>下次结算 {time(selected.row.next_funding_at ?? selected.row.observed?.next_funding_at)}<br /><span className="muted">{countdown(selected.row.next_funding_at ?? selected.row.observed?.next_funding_at, clock)}</span></p>
        <small className="muted">本行数据检查时间 {time(selected.asOf)}；源事件时间不因页面计时更新。</small>
        {selectedPair?.low && selectedPair.high && onSelectCandidate && <p>
          <button type="button" className="button primary" onClick={() => onSelectCandidate({
            buy_instrument_id: selectedPair.low!.instrument_id, sell_instrument_id: selectedPair.high!.instrument_id,
            strategy: "perp_spread",
          })}>测算此组合</button><small className="muted"> 低费率 {selectedPair.low.venue} / 高费率 {selectedPair.high.venue}；组合类型：{kindLabel[selectedPair.low.kind ?? ""] ?? "类型未知"}。其余类型独立比较；仅带入市场，不自动计算。</small>
        </p>}
        {[...selected.row.missing, ...selected.row.warnings, ...(selected.row.identity_blockers ?? [])].map((reason, index) => <p className="warning break" key={`${reason}:${index}`}>{explain(reason)}</p>)}
      </div><div className="funding-field-sources">{Object.entries(selected.row.field_validity ?? {}).map(([field, validity]) => <div key={field}>
        <strong>{({ funding_rate: "原始费率", funding_kind: "费率类型", funding_interval_hours: "实际周期", next_funding_at: "结算时间" } as Record<string, string>)[field] ?? field}</strong>
        <span className={fundingFieldFresh(selected.row, field, selected.asOf, clock) ? "muted" : "warning"}>{fundingFieldFresh(selected.row, field, selected.asOf, clock) ? "字段有效" : explain(validity.reason ?? (validity.rankable === false ? "来源或源事件时间未核验" : "字段已过期"))}</span>
        <small>源事件 {time(validity.source_at)} · 本地接收 {time(validity.received_at)}</small>{validity.metadata_observation && <small>周期按元数据观察时间检查，不是交易所事件时间</small>}<small className="break">{validity.source ?? "来源未提供"}</small></div>)}</div>
      <section aria-labelledby="funding-history-heading" className="funding-history-section"><h3 id="funding-history-heading">已结算公共历史</h3><p>按当前市场手动读取；本地行情快照不当作结算历史，未知历史周期不回填。</p>
        <div className="funding-history-actions"><button type="button" className="button secondary" disabled={historyLoading} onClick={() => void loadHistory(false)}>读取已保存历史</button><button type="button" className="button secondary" disabled={historyLoading} onClick={() => void loadHistory(true)}>{historyLoading ? <Loader2 size={13} className="spin" /> : <Database size={13} />}获取已结算历史</button></div>
        {historyError && <div className="notice error" role="alert">已结算历史获取失败：{historyError}</div>}{history?.summary.issues?.map((issue, index) => <p className="muted break" key={index}>{issue}</p>)}
        {!!history?.records.length && <div className="funding-table-wrap"><table className="funding-history-table"><thead><tr><th>实际结算时间</th><th>已结算原始费率</th><th>实际历史周期</th></tr></thead><tbody>{history.records.map((record, index) => <tr key={`${record.settlement_at}:${index}`}>
          <td>{time(record.settlement_at)}<small>{record.event_type === "regular" ? "常规结算" : record.event_type === "special" ? "特殊结算" : "事件类型未提供"}</small></td><td className="mono">{rate(record.funding_rate)}<small className="break">{record.source}</small><small>本地接收 {time(record.received_at)}</small></td><td>{period(record.funding_interval_hours)}{record.funding_interval_hours == null && <small>不使用当前周期回填历史</small>}</td></tr>)}</tbody></table></div>}
        {history && !history.records.length && <p className="muted">本次未返回已结算记录，不代表该市场没有结算历史。</p>}{history?.summary.truncated && <div className="notice">本次历史结果已截断，不代表全部历史。</div>}
      </section></div>
    </aside>}
  </div>;
});
