import { useEffect, useState } from "react";
import { Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { api, num, time } from "./api";

type Proposal = { id: string; canonical_asset_id: string; instrument_ids: string[]; symbols: string[];
  venues: string[]; status: string; blockers: string[]; evidence_ids: string[]; source_urls: string[];
  observed_at?: number; expires_at?: number };
type Evidence = { id: string; instrument_id: string; native_asset_id: string; canonical_asset_id: string;
  evidence_kind: string; source_url: string; source_field: string; observed_at: number; expires_at: number;
  status: string; blockers: string[] };
type Source = { venue: string; endpoint: string; docs_url: string; state: string; last_attempt_at?: number;
  received_at?: number; response_rows?: number; matched_markets?: number; eligible_markets?: number;
  blocked_markets?: number; error_type?: string };
type Review = { as_of: number; coverage: Record<string, number>; sources: Source[]; proposals: Proposal[];
  evidence: Evidence[]; audit: { at: number; action: string; payload: unknown }[];
  truncated: { evidence: boolean; proposals: boolean } };
type Preview = { preview_token: string; proposals: Proposal[]; ready: number; blocked: number };
const labels: Record<string, string> = { not_requested: "尚未读取", observed: "已读取", ready: "证据可用", conflict: "证据冲突", unverified: "待核验",
  active: "有效", applied: "已应用", revoked: "已撤销", expired: "已过期", success: "已读取",
  error: "读取失败", never: "尚未读取", pending: "待核验" };
const reasons: Record<string, string> = {
  unverified_asset_mapping: "资产映射未核验", different_native_chain_addresses: "原生链上地址不同",
  different_chains: "链身份不同", different_base_symbols_require_unit_review: "名称、单位或包装关系需核对",
  source_unavailable: "公开来源不可访问", evidence_expired: "证据已过期",
  native_asset_id_changed: "目录原生身份已变化", multiple_canonical_assets: "公开来源指向多个资产",
};
const explain = (value: string) => reasons[value] ?? value;
function SourceLink({ url }: { url: string }) {
  return /^https:\/\//.test(url) ? <a className="text-button break" href={url} target="_blank" rel="noreferrer">{url}</a> : <span className="break">{url}</span>;
}

export function AssetVerification({ onChanged }: { onChanged: () => void }) {
  const [data, setData] = useState<Review | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [input, setInput] = useState("");
  const [reason, setReason] = useState("");
  const [limit, setLimit] = useState(200);
  const [venues, setVenues] = useState(["gate", "bitget"]);
  const load = async () => {
    const result = await api<Review>(`/api/assets/verification?limit=${limit}`);
    setData(result);
    return result;
  };
  useEffect(() => {
    let current = true;
    void api<Review>(`/api/assets/verification?limit=${limit}`).then((result) => { if (current) setData(result); })
      .catch((caught: Error) => { if (current) setError(caught.message); });
    return () => { current = false; };
  }, [limit]);
  const act = async (name: string, action: () => Promise<void>) => {
    setBusy(name); setError(""); setMessage("");
    try { await action(); } catch (caught) { setError((caught as Error).message); }
    finally { setBusy(""); }
  };
  const select = (ids: string[]) => { setSelected(ids); setPreview(null); setMessage(""); };
  const coverage = data?.coverage;
  return <section className="panel" aria-labelledby="asset-verification-heading">
    <div className="panel-heading"><div><h2 id="asset-verification-heading"><ShieldCheck size={16} /> 批量资产身份核验</h2>
      <p>读取公开链与合约资料，预览可核验映射后应用；相同 ticker 不作为证明。</p></div>
      <button className="button secondary" type="button" disabled={!!busy} onClick={() => void act("刷新", async () => { await load(); setPreview(null); })}>
        {busy ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}刷新核验状态</button></div>
    <div className="form-content">
      <div className="platform-stack" aria-label="身份核验覆盖">
        <span className="badge">活跃目录 {num(coverage?.active_markets, 0)}</span>
        <span className="badge mint">可追溯已核验 {num(coverage?.traceable_verified_markets, 0)}</span>
        <span className="badge">原有映射 {num(coverage?.legacy_verified_markets, 0)}</span>
        <span className="badge amber">未核验 {num(coverage?.unverified_markets, 0)}</span>
        <span className="badge amber">过期 {num(coverage?.expired_markets, 0)}</span>
      </div>
      <p className="muted">公开资料覆盖 {num(coverage?.source_covered_markets, 0)} 个市场；可核验提案 {num(coverage?.ready_proposals, 0)}，冲突 {num(coverage?.conflict_proposals, 0)}。核验状态不代表行情新鲜或可成交。</p>
      <div className="inline-checks">{["gate", "bitget"].map((venue) => <label key={venue}><input type="checkbox" aria-label={`读取身份来源 ${venue}`}
        checked={venues.includes(venue)} disabled={!!busy} onChange={() => setVenues((old) => old.includes(venue) ? old.filter((item) => item !== venue) : [...old, venue])} />{venue}</label>)}</div>
      <button className="button secondary" type="button" disabled={!!busy || !venues.length} onClick={() => void act("读取公开资料", async () => {
        await api("/api/assets/verification/public-refresh", "POST", { venues });
        await load(); setPreview(null); setMessage("已完成公开来源读取尝试。请核对来源状态与提案；尚未应用映射。");
      })}>读取公开资产资料</button>
      <small className="muted">当前批量来源为 Gate 与 Bitget 的公开币种目录；其他平台保留未核实状态，可用下方证据导入补充。读取失败不会生成可核验身份。</small>
    </div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {message && <div className="notice" role="status">{message}</div>}
    {!!data?.sources.length && <div className="table-scroll"><table><thead><tr><th>公开来源</th><th>读取与覆盖</th><th>时间 / 限制</th></tr></thead><tbody>
      {data.sources.map((source) => <tr key={source.venue}><td><strong>{source.venue}</strong><small className="break"><SourceLink url={source.docs_url || source.endpoint} /></small></td>
        <td>{labels[source.state] ?? source.state}<small>返回 {num(source.response_rows, 0)} 条 · 匹配 {num(source.matched_markets, 0)} 市场</small><small>可用 {num(source.eligible_markets, 0)} · 阻止 {num(source.blocked_markets, 0)}</small></td>
        <td><small>尝试 {time(source.last_attempt_at)} · 接收 {time(source.received_at)}</small>{source.error_type && <small className="warning">{source.error_type} · 未取得可核验证据</small>}</td></tr>)}</tbody></table></div>}
    <div className="filters" style={{ flexWrap: "wrap" }}><span>已选 {selected.length} 条证据</span>
      <button className="text-button" type="button" disabled={!!busy} onClick={() => select([...new Set(data?.proposals.filter((row) => row.status === "ready").flatMap((row) => row.evidence_ids) ?? [])])}>选择当前可核验证据</button>
      <button className="text-button" type="button" disabled={!!busy} onClick={() => select([])}>清空选择</button>
      <select aria-label="身份核验返回条数" value={limit} disabled={!!busy} onChange={(event) => { setLimit(Number(event.target.value)); select([]); }}>
        {[200, 500, 2000].map((value) => <option key={value} value={value}>最多 {value} 条</option>)}</select>
      <button className="button secondary" type="button" disabled={!!busy || !selected.length} onClick={() => void act("预览", async () => {
        setPreview(await api<Preview>("/api/assets/verification/preview", "POST", { evidence_ids: selected }));
      })}>预览所选映射</button></div>
    {(data?.truncated.evidence || data?.truncated.proposals) && <div className="notice">本次证据或提案列表已截断；当前选择只覆盖已返回项目，请增大返回条数，不代表全部市场。</div>}
    {!!data?.proposals.length && <div className="table-scroll"><table><thead><tr><th>资产与市场</th><th>链与合约身份</th><th>核验状态</th><th>公开依据</th></tr></thead><tbody>
      {data.proposals.map((row) => <tr key={row.id}><td><strong>{row.symbols.join(" / ")}</strong><small>{row.venues.join(" / ")} · {row.instrument_ids.length} 个市场</small></td>
        <td className="break mono">{row.canonical_asset_id}</td><td><span className={`badge ${row.status === "ready" ? "mint" : "amber"}`}>{labels[row.status] ?? row.status}</span>
          {row.blockers.map((value) => <small className="warning break" key={value}>{explain(value)}</small>)}</td>
        <td>{row.source_urls.map((url) => <small className="break" key={url}><SourceLink url={url} /></small>)}<small>观测 {time(row.observed_at)} · 到期 {time(row.expires_at)}</small></td></tr>)}</tbody></table></div>}
    {preview && <div className="form-content"><h3>所选映射预览</h3><p>可应用 {preview.ready} 项 · 阻止 {preview.blocked} 项。应用时再次校验证据、原生身份与到期时间。</p>
      {preview.proposals.map((row) => <p className={row.status === "ready" ? "muted" : "warning"} key={row.id}>{row.symbols.join(" / ")} → {row.canonical_asset_id} · {labels[row.status] ?? row.status} {row.blockers.map(explain).join("；")}</p>)}
      <button className="button primary" type="button" disabled={!!busy || !preview.ready || preview.blocked > 0} onClick={() => void act("应用映射", async () => {
        await api("/api/assets/verification/apply", "POST", { evidence_ids: selected, preview_token: preview.preview_token });
        select([]); await load(); onChanged(); setMessage("已应用所选核验映射，审计记录已保存。");
      })}>应用已核验映射</button></div>}
    <details className="calculation-details"><summary>证据明细、修订与撤销</summary>
      {!!data?.evidence.length && <div className="table-scroll"><table><thead><tr><th>选择 / 原生市场</th><th>证据身份与来源</th><th>时间与状态</th></tr></thead><tbody>
        {data.evidence.map((row) => <tr key={row.id}><td><label className="inline-checks"><input type="checkbox" aria-label={`选择身份依据 ${row.id}`} disabled={!!busy} checked={selected.includes(row.id)}
          onChange={() => select(selected.includes(row.id) ? selected.filter((id) => id !== row.id) : [...selected, row.id])} /><span className="break">{row.instrument_id}</span></label><small className="break">原生 {row.native_asset_id}</small></td>
          <td className="break"><strong>{row.canonical_asset_id}</strong><small>{row.evidence_kind} · {row.source_field}</small><SourceLink url={row.source_url} /></td>
          <td>{labels[row.status] ?? row.status}<small>观测 {time(row.observed_at)} · 到期 {time(row.expires_at)}</small>{row.blockers.map((value) => <small className="warning" key={value}>{explain(value)}</small>)}</td></tr>)}</tbody></table></div>}
      <div className="form-content"><label className="field"><span>撤销原因</span><input aria-label="身份依据撤销原因" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} /></label>
        <button className="button secondary" type="button" disabled={!!busy || !selected.length || !reason.trim()} onClick={() => void act("撤销", async () => {
          await api("/api/assets/verification/revoke", "POST", { evidence_ids: selected, reason: reason.trim() });
          select([]); await load(); onChanged(); setMessage("已撤销所选证据并重算映射状态；审计记录保留。");
        })}>撤销所选证据</button>
        <label className="field"><span>导入或修订公开身份依据（JSON）</span><textarea aria-label="导入公开身份依据" rows={6} value={input} onChange={(event) => setInput(event.target.value)} placeholder={'{"items":[{"instrument_id":"…","native_asset_id":"…","canonical_asset_id":"chain:contract","source_url":"https://…","source_field":"…","observed_at":0,"expires_at":0,"relationship":"same_asset","unit_multiplier":1,"note":"…"}]}'} /></label>
        <small className="muted">需绑定准确市场、原生身份、链合约、公开来源及毫秒时间。导入不会自动应用；无来源、单位不明或冲突会阻止核验。</small>
        <button className="button secondary" type="button" disabled={!!busy || !input.trim()} onClick={() => void act("导入证据", async () => {
          const body = JSON.parse(input); await api("/api/assets/verification/evidence", "POST", body);
          await load(); setPreview(null); setMessage("证据已导入，请选择并预览后再应用映射。");
        })}>导入身份依据</button></div>
    </details>
    {!!data?.audit.length && <details className="calculation-details"><summary>身份核验审计记录</summary><div className="table-scroll"><table><thead><tr><th>时间</th><th>操作</th><th>记录</th></tr></thead>
      <tbody>{data.audit.map((entry, index) => <tr key={`${entry.at}:${index}`}><td>{time(entry.at)}</td><td>{entry.action}</td><td><details><summary>查看详情</summary><pre>{JSON.stringify(entry.payload, null, 2)}</pre></details></td></tr>)}</tbody></table></div></details>}
  </section>;
}
