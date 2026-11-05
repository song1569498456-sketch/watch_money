import { useMarketRefresh } from "./useMarketRefresh";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowRight, Calculator, ChevronDown, ChevronRight, Loader2, RefreshCw, Search, Star } from "lucide-react";
import { api, num, pct, time } from "./api";
import type { Instrument } from "./types";
import "./ScannerPage.css";
import { scanDeadline, scanElapsed } from "./scanFreshness";
import { QueryGeneration } from "./queryGeneration";

type CandidateReference = { buy_instrument_id: string; sell_instrument_id: string; strategy: string };
type FieldState = { valid: boolean; issue: string | null; source: string | null;
  source_at: number | null; received_at: number | null; age_seconds: number | null; rankable: boolean };
type Market = { instrument_id: string; venue: string; native_symbol: string; native_asset_id: string;
  base: string; quote: string; kind: string; bid: number | null; ask: number | null; fields: Record<string, FieldState> };
type FundingLeg = { raw_rate: number | null; interval_hours: number | null; kind: string | null;
  normalized_8h_rate: number | null; next_funding_at: number | null; position: string; payment_direction: string };
type Candidate = CandidateReference & { id: string; asset_id: string; asset_symbols: string[];
  buy_venue: string; sell_venue: string; buy_quote: string; sell_quote: string;
  buy_ask: number | null; sell_bid: number | null; spread_rate: number | null; spread_absolute: number | null;
  spread_quote: string | null; reverse_spread_rate: number | null; reverse_price_rankable: boolean; price_rankable: boolean; funding_rankable: boolean;
  funding_8h_rate: number | null; funding: { long: FundingLeg | null; short: FundingLeg | null };
  settlement_alignment: string; missing: string[]; requirements: string[]; warnings: string[];
  buy_market?: Market; sell_market?: Market };
type AssetGroup = { id: string; asset_id: string; asset_symbols: string[]; identity_confirmed: boolean;
  identity_blockers: string[]; venues: string[]; venue_count: number; market_count: number;
  possible_directions: number; scanned_directions: number; reliable_price_directions: number;
  excluded_from_price_ranking: number; best_spread_candidate: Candidate | null; best_funding_candidate: Candidate | null;
  best_within_scan: boolean; scan_complete: boolean; directions: Candidate[]; directions_truncated: boolean;
  directions_returned: number; markets: Market[]; oldest_price_source_at: number | null;
  latest_price_received_at: number | null; max_price_age_seconds: number | null };
type ScanResult = { as_of: number; stale_seconds?: number; enabled: boolean; candidates: Candidate[]; groups: AssetGroup[];
  available_platforms: string[]; stats: { catalog_markets: number; collector_catalog_markets: number;
    active_markets: number; excluded_by_settings: number; excluded_by_filters: number; eligible_groups: number;
    identity_groups: number; blocked_groups: number; scanned: number; possible_candidates: number; returned: number;
    reliable_price_candidates: number; truncated: boolean; enumeration_truncated: boolean;
    groups_total: number; groups_returned: number; groups_truncated: boolean;
    group_offset?: number; group_limit?: number; next_group_offset?: number | null } };

const strategies: Record<string, string> = { spot_spread: "现货价差", spot_perp_basis: "现货 / 永续基差", perp_spread: "永续价差" };
const fundingKinds: Record<string, string> = { current: "当前估算", predicted: "预测", settled: "已结算值快照" };
const alignments: Record<string, string> = { synchronized: "下次结算时间一致", different: "下次结算时间不同",
  unknown: "结算同步状态未知", single_perpetual_leg: "仅一条永续腿", not_applicable: "无永续结算" };
const marketKinds: Record<string, string> = { spot: "现货", perpetual: "永续", pool: "现货池", rfq: "RFQ" };
const shortTime = (value: number | null | undefined) => value == null ? "未提供" :
  new Date(value).toLocaleTimeString("zh-CN", { hour12: false });
const reasons: Record<string, string> = {
  snapshot_missing: "未采集到快照", snapshot_invalid: "快照无效", quote_fx_not_provided: "报价币不同，缺少换汇依据",
  unverified_asset_mapping: "资产身份待核验", different_native_chain_addresses: "原生合约地址不同", different_chains: "链身份不同",
  different_base_symbols_require_unit_review: "基币单位或包装关系需核验", cross_venue_mapping_requires_confirmation: "跨平台映射待确认",
  trade_size_not_provided: "尚未填写交易规模", fees_not_configured: "尚未核实费用", executable_depth_not_checked: "尚未核实规模深度",
  size_specific_route_quotes_not_provided: "缺少按规模的路线报价", funding_settlement_schedule_not_verified: "实际结算时间表未核实",
  source_timestamp_missing: "源事件时间未知，不进入可靠排行", funding_kinds_differ: "费率类型不同",
};
function explain(value: string): string {
  const [role, code] = value.includes(":") ? value.split(":", 2) : ["", value];
  const label = reasons[code] ?? (code.endsWith("_stale") ? "对应字段已过期" :
    code.endsWith("_timestamp_in_future") ? "对应字段时间超出查询时刻" :
    code.endsWith("_missing") ? "对应字段未提供" : code);
  return role ? `${({ buy: "买入腿", sell: "卖出腿", long: "多头", short: "空头" } as Record<string, string>)[role] ?? role}：${label}` : label;
}
function groupReason(group: AssetGroup, sort: string): string {
  if (!group.identity_confirmed) return "身份待核验或冲突";
  if (group.possible_directions === 0) return "缺跨平台组合";
  if (group.scanned_directions === 0) return "预算内尚未扫描";
  const names = sort === "funding" ? ["funding_rate", "funding_kind", "funding_interval_hours"] : ["bid", "ask"];
  const fields = group.markets.flatMap((market) => names.map((name) => market.fields[name]));
  const issues = [...group.directions.flatMap((row) => row.missing), ...fields.map((field) => field?.issue ?? "")];
  if (issues.some((issue) => issue.endsWith("_timestamp_in_future"))) return "源时间异常";
  if (issues.some((issue) => issue.endsWith("_stale"))) return sort === "funding" ? "费率数据过期" : "行情过期";
  if (sort !== "funding" && issues.includes("quote_fx_not_provided")) return "报价币不可比";
  if (fields.some((field) => field?.valid && !field.rankable)) return "来源时间待核验";
  if (issues.some((issue) => issue.includes("snapshot_missing"))) return "行情未到达";
  if (sort === "funding") return "费率或周期待补全";
  return "报价待补全";
}
function FieldTime({ label, field }: { label: string; field?: FieldState }) {
  return <div><strong>{label}</strong><small className="muted break">来源 {field?.source ?? "未提供"}</small>
    <small className="muted">源事件 {time(field?.source_at)}</small><small className="muted">本地接收 {time(field?.received_at)}</small>
    <small className={field?.rankable ? "muted" : "warning"}>{field?.rankable ? `查询时数据年龄 ${num(field.age_seconds, 2)} 秒` : field?.issue ? explain(field.issue) : "源时间未知，未核验"}</small></div>;
}
function Funding({ leg }: { leg: FundingLeg | null }) {
  if (!leg) return <small className="muted">现货腿 · 无永续资金费</small>;
  const payment = ({ pay: "支付", receive: "收取", none: "费率为零", unknown: "收付未知" } as Record<string, string>)[leg.payment_direction] ?? "收付未知";
  return <div><small className="mono">原始 {pct(leg.raw_rate)} / {leg.interval_hours == null ? "周期未知" : `${num(leg.interval_hours, 4)}h`}</small>
    <small>{leg.kind ? fundingKinds[leg.kind] ?? leg.kind : "类型未知"} · {payment}</small><small className="muted">下次结算 {time(leg.next_funding_at)}</small></div>;
}
function Directions({ rows, markets, marketNames, sort, expired, onSelectCandidate }: { expired: boolean; rows: Candidate[]; markets: Map<string, Market>;
  marketNames: Map<string, string>; sort: string; onSelectCandidate?: (candidate: CandidateReference) => void }) {
  return <div className="table-scroll scanner-directions"><table><thead><tr><th>组合</th><th>买入 / 做多 ask</th><th>卖出 / 做空 bid</th><th>盘口毛价差</th><th>两腿报价时间</th><th>详情 / 测算</th></tr></thead>
    <tbody>{rows.map((row) => {
      const buy = row.buy_market ?? markets.get(row.buy_instrument_id);
      const sell = row.sell_market ?? markets.get(row.sell_instrument_id);
      const reliable = !expired && (sort === "funding" ? row.funding_rankable : row.price_rankable);
      return <tr key={row.id}><td><strong>{row.buy_venue} → {row.sell_venue}</strong><small>{strategies[row.strategy] ?? row.strategy}</small>
        <small className={reliable ? "scanner-verified" : "warning"}>{reliable ? "来源与时效已核验" : (expired ? "查询结果已过期，需重新核验" : "未进入可靠排行")}</small></td>
        <td><strong className="mono">{num(row.buy_ask, 8)} {row.buy_quote}</strong><small className="muted">{buy?.native_symbol ?? marketNames.get(row.buy_instrument_id) ?? row.buy_instrument_id}</small></td>
        <td><strong className="mono">{num(row.sell_bid, 8)} {row.sell_quote}</strong><small className="muted">{sell?.native_symbol ?? marketNames.get(row.sell_instrument_id) ?? row.sell_instrument_id}</small></td>
        <td className="mono"><strong>{pct(row.spread_rate)}</strong><small>反向 {pct(row.reverse_spread_rate)}{(expired || !row.reverse_price_rankable) && " · 未核验"}</small>
          <small className="muted">差额 {num(row.spread_absolute, 8)} {row.spread_quote ?? "币种不可直接比较"}</small></td>
        <td className="scanner-quote-times"><small title={`买入源事件 ${time(buy?.fields.ask.source_at)}；本地接收 ${time(buy?.fields.ask.received_at)}`}>买源 {shortTime(buy?.fields.ask.source_at)}<small>买收 {shortTime(buy?.fields.ask.received_at)}</small></small>
          <small title={`卖出源事件 ${time(sell?.fields.bid.source_at)}；本地接收 ${time(sell?.fields.bid.received_at)}`}>卖源 {shortTime(sell?.fields.bid.source_at)}<small>卖收 {shortTime(sell?.fields.bid.received_at)}</small></small></td>
        <td><button className="text-button" disabled={!onSelectCandidate} onClick={() => onSelectCandidate?.({ buy_instrument_id: row.buy_instrument_id, sell_instrument_id: row.sell_instrument_id, strategy: row.strategy })}><Calculator size={13} />测算此组合</button>
          <details className="raw-details"><summary>组合详情 <ArrowRight size={12} /></summary>
            <small className="break">买入原生 ID：{buy?.native_asset_id ?? "未提供"} · {row.buy_instrument_id}</small>
            <small className="break">卖出原生 ID：{sell?.native_asset_id ?? "未提供"} · {row.sell_instrument_id}</small>
            <FieldTime label="买入腿 ask" field={buy?.fields.ask} /><FieldTime label="卖出腿 bid" field={sell?.fields.bid} />
            <strong className="mono">8h 费差 {pct(row.funding_8h_rate)}</strong><small>买入 / 多头</small><Funding leg={row.funding.long} />
            <small>卖出 / 空头</small><Funding leg={row.funding.short} /><small className="warning">{alignments[row.settlement_alignment] ?? "同步状态未知"}</small>
            {[...row.missing, ...row.requirements].map((value) => <small className="warning break" key={value}>{explain(value)}</small>)}</details></td></tr>;
    })}</tbody></table></div>;
}

export function ScannerPage({ instruments, onSelectCandidate, favorites = [], toggleFavorite, onOpenAsset, onReviewIdentity, onViewSources, active = true, refreshKey = 0, staleSeconds = 60 }: {
  refreshKey?: number; staleSeconds?: number; instruments: Instrument[]; favorites?: string[]; toggleFavorite?: (instrumentId: string) => void;
  onOpenAsset?: (assetId: string) => void; onReviewIdentity?: () => void; onViewSources?: () => void; onSelectCandidate?: (candidate: CandidateReference) => void; active?: boolean;
}) {
  const [clock, setClock] = useState(Date.now());
  useEffect(() => { setClock(Date.now()); if (!active) return; const timer = setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer); }, [active]);
  const [sort, setSort] = useState("spread");
  const [marketType, setMarketType] = useState("all");
  const [view, setView] = useState("assets");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [reload, setReload] = useState(0);
  const [assetPage, setAssetPage] = useState({ scope: "", offset: 0 });
  const pageScope = JSON.stringify([sort, marketType, search.trim(), platforms]);
  // Reset stored state as well as the effective page: returning to a previous
  // filter must not resurrect its old offset. React retries this render before commit.
  if (assetPage.scope !== pageScope) setAssetPage({ scope: pageScope, offset: 0 });
  const groupOffset = assetPage.scope === pageScope ? assetPage.offset : 0;
  const changeAssetPage = (offset: number) => setAssetPage({ scope: pageScope, offset });
  const [data, setData] = useState<ScanResult | null>(null);
  const [dataVersion, setDataVersion] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requests = useRef(new QueryGeneration());
  const pending = useRef(false);
  useMarketRefresh(active, autoRefresh, pending, setReload);
  const requestKey = JSON.stringify([sort, marketType, query, platforms, refreshKey, groupOffset]);
  const requestVersion = requests.current.update(JSON.stringify([requestKey, search.trim()]), active, reload);
  const currentResults = dataVersion === requests.current.scopeVersion && search.trim() === query;
  const blockingLoad = loading && !currentResults;
  const expired = !!data && Math.max(clock, Date.now()) > scanDeadline(data, staleSeconds);
  const elapsed = data ? scanElapsed(data.as_of, Math.max(clock, Date.now())) : 0;
  const canSelect = !expired && active && !!data && currentResults && !error;
  const marketNames = useMemo(() => new Map(instruments.map((item) => [item.id, item.native_symbol])), [instruments]);
  useEffect(() => { const timer = setTimeout(() => setQuery(search.trim()), 250); return () => clearTimeout(timer); }, [search]);
  useEffect(() => {
    if (!active || search.trim() !== query) { setLoading(false); return; }
    const gate = requests.current;
    const token = gate.begin();
    pending.current = true;
    const load = async () => {
      setLoading(true);
      try {
        const result = await api<ScanResult>("/api/scanner?sort=" + sort + "&limit=1000&max_candidates=10000&q=" +
          encodeURIComponent(query) + "&platforms=" + encodeURIComponent(platforms.join(",")) + "&market_type=" + marketType + "&group_limit=100&group_offset=" + groupOffset,
          "GET", undefined, { signal: token.controller.signal });
        if (gate.accepts(token)) { setData(result); setDataVersion(gate.scopeVersion); setError(""); }
      } catch (e) { if (gate.accepts(token)) setError((e as Error).message); }
      finally { if (gate.accepts(token)) { pending.current = false; setLoading(false); } }
    };
    void load();
    return () => { pending.current = false; gate.cancel(token); };
  }, [requestVersion]);
  const stats = data?.stats;
  const available = data?.available_platforms ?? [...new Set(instruments.map((item) => item.venue))].sort();
  const selectCandidate = canSelect ? onSelectCandidate : undefined;
  const toggleExpanded = (id: string) => setExpanded((previous) => {
    const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next;
  });
  return <div className="scanner-workspace">
    <section className="panel" aria-labelledby="scanner-heading">
      <div className="panel-heading"><div><h2 id="scanner-heading">全市场资产机会 <span className="badge amber">盘口毛价差</span></h2>
        <p>按资产比较跨平台买卖路径，展开核对双向报价与时间，再测算交易规模。</p></div>
        <button className="button secondary" disabled={blockingLoad} onClick={() => setReload((old) => old + 1)}>
          {blockingLoad ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}刷新扫描</button></div>
      <div className="scanner-toolbar">
        <label className="search"><Search size={14} /><input aria-label="搜索扫描资产或原生市场" placeholder="资产 / 原生市场 / ID"
          value={search} onChange={(e) => setSearch(e.target.value)} maxLength={300} /></label>
        <select aria-label="扫描市场类型" value={marketType} onChange={(e) => setMarketType(e.target.value)}>
          <option value="all">全部市场类型</option><option value="spot">现货间（含池 / RFQ）</option>
          <option value="perpetual">永续间</option><option value="spot_perp">现货 / 永续</option></select>
        <select aria-label="扫描排序" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="spread">盘口毛价差排序</option><option value="funding">资金费差排序</option><option value="asset">按资产</option></select>
        <details className="scanner-platform-filter"><summary>平台 · {platforms.length ? platforms.length + " 个" : "全部"}</summary>
          <div className="scanner-platform-options">{available.map((venue) => <label className="inline-checks" key={venue}>
            <input type="checkbox" checked={!platforms.length || platforms.includes(venue)}
              disabled={available.length === 1 || (platforms.length === 1 && platforms[0] === venue)}
              onChange={() => setPlatforms((previous) => previous.length ? previous.includes(venue) ? previous.filter((name) => name !== venue)
                : [...previous, venue] : available.filter((name) => name !== venue))} />{venue}</label>)}
            <button className="text-button" onClick={() => setPlatforms([])}>恢复全部平台</button><small className="muted">至少保留一个平台</small></div></details>
        <label className="inline-checks"><input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />5 秒查询最新数据</label>
      </div>
      <div className="scanner-summary" aria-label="扫描覆盖">
        <span>市场 <strong>{num(stats?.active_markets, 0)}</strong></span><span>可比资产 <strong>{num(stats?.eligible_groups, 0)}</strong></span>
        <span>查询时可靠价差方向 <strong>{num(stats?.reliable_price_candidates, 0)}</strong></span>
        <span>已扫描 <strong>{num(stats?.scanned, 0)} / {num(stats?.possible_candidates, 0)}</strong></span>
        <span>身份待核验 <strong>{num(stats?.blocked_groups, 0)}</strong></span>
        <span className="scanner-check-time">检查于 {time(data?.as_of)}</span>
        <span className={"scanner-result-state " + (expired ? "warning" : "muted")}
          title="过期时保留上次查询的数值供查看，等待服务端更新排名；过期结果不能用于发起组合测算。">
          {expired ? "快照过期，等待更新" : data && !currentResults ? "筛选待核验" : data ? autoRefresh ? "数值持续更新" : "自动更新已暂停" : "首次加载"}</span>
      </div>
      <div className="scanner-caption">按服务端全目录筛选类型后再扫描；只排行同报价币且源时间有效的方向。毛价差未扣费用，不代表可锁定收益。</div>
      <div className="scanner-caption scanner-identity-help">当前目录 {available.length} 个平台
        {onViewSources && <button type="button" className="text-button" onClick={onViewSources}>CEX / DEX 数据源状态</button>}</div>
      {!!stats?.blocked_groups && <div className="scanner-caption scanner-identity-help">行情已发现，跨平台身份仍需核验；同名不代表同一资产。
        {onReviewIdentity && <button type="button" className="text-button" onClick={onReviewIdentity}>查看官方证据并核验</button>}</div>}
      {!error && (blockingLoad || !currentResults) && <div className="notice scanner-loading" role="status">
        {data ? "正在应用筛选 / 刷新，以下保留上次结果，暂不可发起组合测算。" : "正在读取市场与扫描结果。"}</div>}
      {error && <div className="notice error" role="alert">本次扫描失败：{error}{data ? "；以下仍为上次筛选结果，请刷新后再测算。" : ""}</div>}
      {data?.enabled === false && <div className="notice">扫描未启用：请核对收益计算开关和演示模式。</div>}
      {(stats?.truncated || stats?.groups_truncated) && <div className="notice">扫描 / 分页范围：已扫描 {num(stats?.scanned, 0)}/{num(stats?.possible_candidates, 0)} 个方向，资产 {num(stats?.groups_returned, 0)}/{num(stats?.groups_total, 0)}。
        {stats?.enumeration_truncated ? "最佳只限已扫描范围；可搜索资产或缩小平台范围。" : "展开内会说明被省略的方向。"}</div>}
      <div className="scanner-view-switch" aria-label="扫描视图">
        <button className={view === "assets" ? "selected" : ""} onClick={() => setView("assets")}>资产聚合</button>
        <button className={view === "directions" ? "selected" : ""} onClick={() => setView("directions")}>所有方向</button>
      </div>
      {view === "assets" && data && <nav className="scanner-pagination" aria-label="资产分页">
        <span role="status">资产 {data.groups.length ? (stats?.group_offset ?? 0) + 1 : 0}–{data.groups.length ? (stats?.group_offset ?? 0) + data.groups.length : 0} / {num(stats?.groups_total, 0)}</span>
        <button className="button secondary" disabled={!currentResults || groupOffset === 0} onClick={() => changeAssetPage(0)}>首页</button>
        <button className="button secondary" disabled={!currentResults || groupOffset === 0} onClick={() => changeAssetPage(Math.max(0, groupOffset - 100))}>上一页资产</button>
        <button className="button secondary" disabled={!currentResults || stats?.next_group_offset == null} onClick={() => stats?.next_group_offset != null && changeAssetPage(stats.next_group_offset)}>下一页资产</button>
        <small>每页最多100个资产组。翻页会重新检查行情，实时排序可能移动或重复；分页不增加已扫描方向，不能视为同一时点的完整快照。</small>
      </nav>}
      {view === "assets" && !!data?.groups.length && <div className="table-scroll" aria-busy={blockingLoad}>
        <table className="scanner-asset-table"><thead><tr><th>资产</th><th>类型</th><th>{sort === "funding" ? "8h 资金费差" : "盘口毛价差"}</th>
          <th>最佳已扫描路径</th><th>覆盖</th><th>报价时间</th><th>关注</th></tr></thead>
          <tbody>{data.groups.map((group) => {
            const markets = new Map(group.markets.map((market) => [market.instrument_id, market]));
            const best = sort === "funding" ? group.best_funding_candidate : group.best_spread_candidate;
            const symbol = group.asset_symbols.join(" / ") || group.asset_id;
            const representative = group.markets[0];
            const open = expanded.has(group.id);
            const types = [...new Set(group.markets.map((market) => marketKinds[market.kind] ?? market.kind))];
            return <Fragment key={group.id}><tr className={open ? "scanner-asset-row is-expanded" : "scanner-asset-row"}>
              <td><div className="scanner-asset-label"><button className="scanner-expand-button" aria-label={(open ? "收起 " : "展开 ") + symbol + " 跨平台组合"}
                aria-expanded={open} aria-controls={"scanner-group-" + group.id} onClick={() => toggleExpanded(group.id)}>
                {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}<strong>{symbol}</strong></button>
                {onOpenAsset && <button className="text-button" disabled={!canSelect} onClick={() => onOpenAsset(group.asset_id)}>详情</button>}</div>
                {!group.identity_confirmed && <small className="warning" title="已发现原生市场；跨平台资产身份仍需官方链和合约证据核验，不能只按同名合并。">
                  {group.identity_blockers.some(reason => /conflict|different_native_chain_addresses|different_chains/.test(reason)) ? "资产身份存在冲突" : "跨平台身份待确认"}</small>}</td>
              <td><span className="scanner-market-type">{types.join(" / ")}</span></td>
              <td className="scanner-primary-rate mono"><strong>{best ? pct(sort === "funding" ? best.funding_8h_rate : best.spread_rate) : "未提供"}</strong>
                <small className={expired ? "warning" : best ? "muted" : "warning"}>{expired ? "上次查询值 · 排名待更新" : best ? (sort === "funding" ? "归一化 · 非结算收益" : "未扣成本") : groupReason(group, sort)}</small></td>
              <td>{best ? <><strong>{best.buy_venue} <span className="muted">买</span> → {best.sell_venue} <span className="muted">卖</span></strong>
                <small className="muted">{strategies[best.strategy] ?? best.strategy}</small></> : <span className="muted">暂无可靠路径</span>}</td>
              <td title={group.venues.join("、")}><strong>{group.venue_count} 平台 / {group.market_count} 市场</strong>
                <small className="muted">{group.scanned_directions}/{group.possible_directions} 方向 · 排除 {group.excluded_from_price_ranking}</small>
                {!group.scan_complete && <small className="warning">扫描截断</small>}</td>
              <td title={"有效价格最新接收 " + time(group.latest_price_received_at)}><span className="mono">{shortTime(group.latest_price_received_at)}</span>
                <small className="muted" title={`本资产组中通过校验的买卖报价，最旧一项距今多久；按源时间与接收时间较早者计算。超过 ${data.stale_seconds ?? staleSeconds} 秒不进入可靠比较，不是刷新间隔或资金费周期。`}>
                  {group.max_price_age_seconds == null ? "报价时间未知" : "最旧报价 " + num(group.max_price_age_seconds + elapsed, 1) + " 秒前"}</small></td>
              <td><button className={"icon-button " + (representative && favorites.includes(representative.instrument_id) ? "favorite" : "")}
                disabled={!toggleFavorite || !representative || !canSelect}
                aria-label={(representative && favorites.includes(representative.instrument_id) ? "取消关注 " : "关注 ") + symbol + " 代表市场"}
                title={representative ? "关注代表市场：" + representative.venue + " " + representative.native_symbol : "未提供代表市场"}
                onClick={() => representative && toggleFavorite?.(representative.instrument_id)}><Star size={16}
                  fill={representative && favorites.includes(representative.instrument_id) ? "currentColor" : "none"} /></button></td>
            </tr>
            {open && <tr className="scanner-expanded-row" id={"scanner-group-" + group.id}><td colSpan={7}>
              <div className="scanner-group-note"><strong>{symbol} · 双向组合报价</strong><span>查询时已扫描范围内可靠 {group.reliable_price_directions}，排除 {group.excluded_from_price_ranking}。
                {!group.scan_complete && "尚有未扫描方向。"} 源事件最早 {time(group.oldest_price_source_at)}。</span></div>
              {group.identity_blockers.length > 0 && <div className="notice">{group.identity_blockers.map(explain).join("；")}</div>}
              {group.directions.length ? <Directions expired={expired} rows={group.directions} markets={markets} marketNames={marketNames} sort={sort}
                onSelectCandidate={selectCandidate} /> : <div className="notice">当前身份与类型筛选下没有已扫描跨平台组合。</div>}
              {group.directions_truncated && <div className="notice">仅返回本资产 {group.directions_returned}/{group.scanned_directions} 个已扫描方向。</div>}
              <details className="scanner-market-details"><summary>各平台盘口与市场身份 · {group.market_count} 市场</summary>
                <div className="table-scroll"><table><thead><tr><th>平台 / 原生市场</th><th>bid / ask</th><th>bid 来源与时间</th><th>ask 来源与时间</th><th>关注市场</th></tr></thead>
                  <tbody>{group.markets.map((market) => <tr key={market.instrument_id}><td><strong>{market.venue} · {market.native_symbol}</strong>
                    <small className="break">{market.instrument_id}</small><small className="muted break">原生资产 {market.native_asset_id}</small>
                    <small>{marketKinds[market.kind] ?? market.kind} · {market.quote}</small></td>
                    <td className="mono">bid {num(market.bid, 8)}<small>ask {num(market.ask, 8)}</small></td>
                    <td><FieldTime label="bid" field={market.fields.bid} /></td><td><FieldTime label="ask" field={market.fields.ask} /></td>
                    <td><button className={"icon-button " + (favorites.includes(market.instrument_id) ? "favorite" : "")}
                      disabled={!toggleFavorite || !canSelect} aria-label={(favorites.includes(market.instrument_id) ? "取消关注 " : "关注 ") + market.venue + " " + market.native_symbol}
                      onClick={() => toggleFavorite?.(market.instrument_id)}><Star size={15} fill={favorites.includes(market.instrument_id) ? "currentColor" : "none"} /></button></td></tr>)}</tbody></table></div>
              </details>
            </td></tr>}</Fragment>;
          })}</tbody>
        </table></div>}
      {view === "directions" && !!data?.candidates.length && <Directions expired={expired} rows={data.candidates} markets={new Map()} marketNames={marketNames}
        sort={sort} onSelectCandidate={selectCandidate} />}
      {(!data || !data.groups.length) && <div className="empty"><Activity size={25} /><h3>{blockingLoad ? "正在按资产扫描" : groupOffset > 0 ? "当前页没有资产，请返回首页" : "当前筛选没有市场"}</h3>
        <p>调整资产、类型或平台筛选；未核验市场不会按 ticker 自动合并。</p></div>}
      <div className="panel-foot">最多评估 10,000 个方向；来源未知或过期不进可靠排行。关注保存代表市场，展开可逐市场关注。组合测算仅传递两腿，不自动计算或下单。</div>
    </section>
  </div>;
}
