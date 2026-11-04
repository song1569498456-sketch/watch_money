import { useEffect, useMemo, useState } from "react";
import { Activity, ArrowRight, Calculator, Globe, Loader2, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { api, num, pct, time } from "./api";
import type { Instrument } from "./types";

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
type ScanResult = { as_of: number; enabled: boolean; candidates: Candidate[]; groups: AssetGroup[];
  available_platforms: string[]; stats: { catalog_markets: number; collector_catalog_markets: number;
    active_markets: number; excluded_by_settings: number; excluded_by_filters: number; eligible_groups: number;
    identity_groups: number; blocked_groups: number; scanned: number; possible_candidates: number; returned: number;
    reliable_price_candidates: number; truncated: boolean; enumeration_truncated: boolean;
    groups_total: number; groups_returned: number; groups_truncated: boolean } };

const strategies: Record<string, string> = { spot_spread: "现货价差", spot_perp_basis: "现货 / 永续基差", perp_spread: "永续价差" };
const fundingKinds: Record<string, string> = { current: "当前估算", predicted: "预测", settled: "已结算值快照" };
const alignments: Record<string, string> = { synchronized: "下次结算时间一致", different: "下次结算时间不同",
  unknown: "结算同步状态未知", single_perpetual_leg: "仅一条永续腿", not_applicable: "无永续结算" };
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
function Directions({ rows, markets, marketNames, sort, onSelectCandidate }: { rows: Candidate[]; markets: Map<string, Market>;
  marketNames: Map<string, string>; sort: string; onSelectCandidate?: (candidate: CandidateReference) => void }) {
  return <div className="table-scroll"><table><thead><tr><th>买入 / 做多</th><th>卖出 / 做空</th><th>正向 / 反向价差</th><th>逐腿资金费 / 结算</th><th>可靠状态 / 规模计算</th></tr></thead>
    <tbody>{rows.map((row) => {
      const buy = row.buy_market ?? markets.get(row.buy_instrument_id);
      const sell = row.sell_market ?? markets.get(row.sell_instrument_id);
      const reliable = sort === "funding" ? row.funding_rankable : row.price_rankable;
      return <tr key={row.id}><td><strong>{row.buy_venue}</strong><small className="break">{buy?.native_symbol ?? marketNames.get(row.buy_instrument_id) ?? row.buy_instrument_id}</small>
        <small className="mono">卖一 {num(row.buy_ask, 8)} {row.buy_quote}</small><small>{strategies[row.strategy] ?? row.strategy}</small></td>
        <td><strong>{row.sell_venue}</strong><small className="break">{sell?.native_symbol ?? marketNames.get(row.sell_instrument_id) ?? row.sell_instrument_id}</small>
          <small className="mono">买一 {num(row.sell_bid, 8)} {row.sell_quote}</small></td>
        <td className="mono"><strong>{pct(row.spread_rate)}</strong><small>反向 {pct(row.reverse_spread_rate)}{!row.reverse_price_rankable && " · 未核验"}</small>
          <small className="muted">差额 {num(row.spread_absolute, 8)} {row.spread_quote ?? "币种不可直接比较"}</small></td>
        <td><strong className="mono">8h 费差 {pct(row.funding_8h_rate)}</strong><small>买入 / 多头</small><Funding leg={row.funding.long} />
          <small>卖出 / 空头</small><Funding leg={row.funding.short} /><small className="warning">{alignments[row.settlement_alignment] ?? "同步状态未知"}</small></td>
        <td><span className={`badge ${reliable ? "mint" : "amber"}`}>{reliable ? "来源与时效已核验" : "未进入可靠排行"}</span>
          <button className="text-button" disabled={!onSelectCandidate} onClick={() => onSelectCandidate?.({ buy_instrument_id: row.buy_instrument_id, sell_instrument_id: row.sell_instrument_id, strategy: row.strategy })}><Calculator size={13} />按规模计算</button>
          <details className="raw-details"><summary>两腿来源 / 缺失条件 <ArrowRight size={12} /></summary>
            <small className="break">买入原生 ID：{buy?.native_asset_id ?? "未提供"} · {row.buy_instrument_id}</small>
            <small className="break">卖出原生 ID：{sell?.native_asset_id ?? "未提供"} · {row.sell_instrument_id}</small>
            <FieldTime label="买入腿 ask" field={buy?.fields.ask} /><FieldTime label="卖出腿 bid" field={sell?.fields.bid} />
            {[...row.missing, ...row.requirements].map((value) => <small className="warning break" key={value}>{explain(value)}</small>)}</details></td></tr>;
    })}</tbody></table></div>;
}

export function ScannerPage({ instruments, onSelectCandidate }: { instruments: Instrument[];
  onSelectCandidate?: (candidate: CandidateReference) => void }) {
  const [sort, setSort] = useState("spread");
  const [view, setView] = useState("assets");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [reload, setReload] = useState(0);
  const [data, setData] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const marketNames = useMemo(() => new Map(instruments.map((item) => [item.id, item.native_symbol])), [instruments]);
  useEffect(() => { const timer = setTimeout(() => setQuery(search.trim()), 250); return () => clearTimeout(timer); }, [search]);
  useEffect(() => {
    let current = true;
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true; setLoading(true); setError("");
      try {
        const result = await api<ScanResult>(`/api/scanner?sort=${sort}&limit=1000&max_candidates=10000&q=${encodeURIComponent(query)}&platforms=${encodeURIComponent(platforms.join(","))}`);
        if (current) setData(result);
      } catch (e) { if (current) setError((e as Error).message); }
      finally { pending = false; if (current) setLoading(false); }
    };
    void load();
    const timer = autoRefresh ? setInterval(() => void load(), 30000) : undefined;
    return () => { current = false; if (timer !== undefined) clearInterval(timer); };
  }, [sort, autoRefresh, reload, query, platforms]);
  const stats = data?.stats;
  const available = data?.available_platforms ?? [...new Set(instruments.map((item) => item.venue))].sort();
  return <>
    <div className="stats">
      <div className="stat"><span className="stat-label">筛选后市场 <Globe size={17} /></span><strong>{num(stats?.active_markets, 0)}</strong>
        <small>已发现 {num(stats?.collector_catalog_markets, 0)} · 筛选排除 {num(stats?.excluded_by_filters, 0)}</small></div>
      <div className="stat"><span className="stat-label">身份可比资产组 <ShieldCheck size={17} /></span><strong>{num(stats?.eligible_groups, 0)}</strong><small>待核验或冲突 {num(stats?.blocked_groups, 0)} 组</small></div>
      <div className="stat"><span className="stat-label">可靠价差方向 <ShieldCheck size={17} /></span><strong>{num(stats?.reliable_price_candidates, 0)}</strong><small>只使用有源事件时间的有效同币种报价</small></div>
      <div className="stat"><span className="stat-label">已扫描方向 <Activity size={17} /></span><strong>{num(stats?.scanned, 0)}</strong><small>可能方向 {num(stats?.possible_candidates, 0)}</small></div>
    </div>
    <section className="panel" aria-labelledby="scanner-heading">
      <div className="panel-heading"><div><h2 id="scanner-heading">全市场资产机会 <span className="badge amber">指示排行 · 非锁利</span></h2>
        <p>按已核验资产自动组合跨平台市场，展开核对方向与原始时间，再按规模计算成本和深度。</p></div>
        <button className="button secondary" disabled={loading} onClick={() => setReload((old) => old + 1)}>{loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}刷新扫描</button></div>
      <div className="filters" style={{ flexWrap: "wrap" }}>
        <label className="search"><Search size={14} /><input aria-label="搜索扫描资产或原生市场" placeholder="搜索资产、原生市场或 ID" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={300} /></label>
        <select aria-label="扫描排序" value={sort} onChange={(e) => setSort(e.target.value)}><option value="spread">最佳已扫描价差</option><option value="funding">已核验资金费差</option><option value="asset">按资产</option></select>
        <button className={`button secondary ${view === "assets" ? "selected" : ""}`} onClick={() => setView("assets")}>资产聚合</button>
        <button className={`button secondary ${view === "directions" ? "selected" : ""}`} onClick={() => setView("directions")}>所有方向</button>
        <label className="inline-checks"><input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />每 30 秒刷新</label>
      </div>
      <details className="calculation-details"><summary>平台筛选 · {platforms.length ? platforms.join("、") : "全部已启用平台"}</summary>
        <div className="filters" style={{ flexWrap: "wrap" }}>{available.map((venue) => <label className="inline-checks" key={venue}><input type="checkbox" checked={!platforms.length || platforms.includes(venue)}
          disabled={available.length === 1 || (platforms.length === 1 && platforms[0] === venue)}
          onChange={() => setPlatforms((previous) => previous.length ? previous.includes(venue) ? previous.filter((name) => name !== venue) : [...previous, venue] : available.filter((name) => name !== venue))} />{venue}</label>)}
          <button className="text-button" onClick={() => setPlatforms([])}>恢复全部平台</button><span className="muted">至少保留一个平台</span></div></details>
      <div className="notice" role="status">检查时间 {time(data?.as_of)}。源时间未知、过期、未来时间或不同报价币不进入可靠价差排行；诊断方向保留并标记。净收益与可成交规模尚未计算。</div>
      {error && <div className="notice error" role="alert">扫描失败：{error}{data ? "；保留上次结果，请核对时间。" : ""}</div>}
      {data?.enabled === false && <div className="notice">扫描未启用：请核对收益计算开关和演示模式。</div>}
      {(stats?.truncated || stats?.groups_truncated) && <div className="notice">扫描/返回有上限：已扫描 {num(stats?.scanned, 0)}/{num(stats?.possible_candidates, 0)} 个方向，资产组 {num(stats?.groups_returned, 0)}/{num(stats?.groups_total, 0)}。
        {stats?.enumeration_truncated ? "最佳仅指已扫描范围，不代表全市场最优；可按资产/平台缩小范围。" : "方向或资产返回数量已截断，展开内会说明。"}</div>}
      {view === "assets" ? data?.groups.map((group) => {
        const markets = new Map(group.markets.map((market) => [market.instrument_id, market]));
        const best = sort === "funding" ? group.best_funding_candidate : group.best_spread_candidate;
        return <details className="calculation-details" key={group.id} onToggle={(event) => {
          const open = event.currentTarget.open;
          setExpanded((previous) => { const next = new Set(previous); if (open) next.add(group.id); else next.delete(group.id); return next; });
        }}><summary><strong>{group.asset_symbols.join(" / ") || group.asset_id}</strong>
          <span>{group.venue_count} 平台 · {group.market_count} 市场 · 已扫描 {group.scanned_directions}/{group.possible_directions} 方向</span>
          <span>{best ? `${best.buy_venue} 买入 / 多 → ${best.sell_venue} 卖出 / 空 · ${sort === "funding" ? "8h费差 " + pct(best.funding_8h_rate) : "价差 " + pct(best.spread_rate)}` : (sort === "funding" ? "暂无可靠资金费路径" : "暂无可靠价差路径")}</span>
          <span>{group.identity_confirmed ? "身份已确认" : "身份待核验或冲突"} · 价格排行排除 {group.excluded_from_price_ranking} 方向 · {group.scan_complete ? "枚举完成" : "扫描截断"}</span>
          <span>最新接收 {time(group.latest_price_received_at)} · 展开市场与组合</span></summary>
          {expanded.has(group.id) && <>
          <div className="notice">最佳路径仅针对已扫描方向。可靠方向 {group.reliable_price_directions}，时效/源时间/报价币条件排除 {group.excluded_from_price_ranking}。
            {!group.scan_complete && "该资产扫描尚未完整，未扫描方向不作最优判断。"}源事件最早 {time(group.oldest_price_source_at)} · 最后接收 {time(group.latest_price_received_at)} · 查询时最大年龄 {num(group.max_price_age_seconds, 2)} 秒。
            {best && `反向价差 ${pct(best.reverse_spread_rate)}${best.reverse_price_rankable ? "" : "（未核验）"}。`}</div>
          {group.identity_blockers.length > 0 && <div className="notice">{group.identity_blockers.map(explain).join("；")}</div>}
          <div className="table-scroll"><table><thead><tr><th>平台 / 原生市场与资产 ID</th><th>买一 / 卖一</th><th>bid 来源与时间</th><th>ask 来源与时间</th></tr></thead>
            <tbody>{group.markets.map((market) => <tr key={market.instrument_id}><td><strong>{market.venue} · {market.native_symbol}</strong><small className="break">{market.instrument_id}</small><small className="muted break">原生资产 {market.native_asset_id}</small><small>{market.kind} · {market.quote}</small></td>
              <td className="mono">bid {num(market.bid, 8)}<small>ask {num(market.ask, 8)}</small></td><td><FieldTime label="bid" field={market.fields.bid} /></td><td><FieldTime label="ask" field={market.fields.ask} /></td></tr>)}</tbody></table></div>
          {group.directions.length ? <Directions rows={group.directions} markets={markets} marketNames={marketNames} sort={sort} onSelectCandidate={onSelectCandidate} /> : <div className="notice">当前身份与筛选条件下没有已扫描的跨平台方向。</div>}
          {group.directions_truncated && <div className="notice">本资产仅返回 {group.directions_returned}/{group.scanned_directions} 个已扫描方向；并非全部组合。</div>}
          </>}
        </details>;
      }) : data?.candidates.length ? <Directions rows={data.candidates} markets={new Map()} marketNames={marketNames} sort={sort} onSelectCandidate={onSelectCandidate} /> : null}
      {(!data || !data.groups.length) && <div className="empty"><Activity size={25} /><h3>{loading ? "正在按资产扫描" : "当前筛选没有市场"}</h3><p>调整资产搜索或平台筛选；未核验市场不会按 ticker 自动合并。</p></div>}
      <div className="panel-foot">最多评估 10,000 个方向；最多返回 1,000 个资产组及每组 1,000 个方向。“按规模计算”仅打开精确两腿计算器，不自动计算或下单。</div>
    </section>
  </>;
}
