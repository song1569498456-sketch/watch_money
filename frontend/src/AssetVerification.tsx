import { useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { api, num, time } from "./api";
import { IdentityCatalog } from "./IdentityCatalog";
import { IdentityBatchReview } from "./IdentityBatchReview";

type Proposal = { existing_mappings?: Record<string, string>; id: string; canonical_asset_id: string; instrument_ids: string[]; symbols: string[];
  venues: string[]; status: string; blockers: string[]; evidence_ids: string[]; source_urls: string[];
  observed_at?: number; expires_at?: number };
type Evidence = { affected_instrument_ids?: string[]; id: string; instrument_id: string; native_asset_id: string; canonical_asset_id: string;
  evidence_kind: string; source_url: string; source_field: string; observed_at: number; expires_at: number;
  status: string; blockers: string[] };
type Source = { venue: string; endpoint: string; docs_url: string; state: string; last_attempt_at?: number;
  received_at?: number; response_rows?: number; matched_markets?: number; eligible_markets?: number;
  blocked_markets?: number; error_type?: string; client_ids?: string[]; product_kinds?: string[];
  scope_eligible_markets?: number; scope_rejected_markets?: number; scope_reasons?: Record<string, number>;
  unmatched_currency_markets?: number; scope_note?: string };
type PlatformCoverage = { venue: string; active_markets: number; source_supported: boolean; client_ids: string[];
  product_kinds: string[]; source_scope_markets: number; scope_rejected_markets: number;
  scope_reasons: Record<string, number>; source_covered_markets: number; source_blocked_markets: number;
  source_blockers: Record<string, number>; verified_markets: number; traceable_verified_markets: number;
  unverified_markets: number; conflicting_markets: number; scope_note?: string };
type Review = { as_of: number; coverage: Record<string, number>; sources: Source[]; proposals: Proposal[];
  evidence_offset: number; evidence_total: number; evidence_next_offset: number | null; evidence: Evidence[]; audit: { at: number; action: string; payload: unknown }[];
  truncated: { evidence: boolean; proposals: boolean }; platform_coverage?: PlatformCoverage[] };
type Preview = { preview_token: string; proposals: Proposal[]; ready: number; blocked: number };
const labels: Record<string, string> = { not_requested: "尚未读取", observed: "已读取", ready: "证据可用", conflict: "证据冲突", unverified: "待核验",
  active: "有效", applied: "已应用", revoked: "已撤销", expired: "已过期", success: "已读取",
  error: "读取失败", never: "尚未读取", pending: "待核验" };
const reasons: Record<string, string> = {
  unverified_asset_mapping: "资产映射未核验", different_native_chain_addresses: "原生链上地址不同",
  different_chains: "链身份不同", different_base_symbols_require_unit_review: "名称、单位或包装关系需核对",
  source_unavailable: "公开来源不可访问", evidence_expired: "证据已过期",
  native_asset_id_changed: "目录原生身份已变化", multiple_canonical_assets: "公开来源指向多个资产",
  original_currency_id_missing: "缺少原始 baseId，不能用统一 ticker 代替",
  original_currency_id_conflicts_with_native_identity: "原始币种 ID 与目录原生身份不一致",
  native_client_out_of_source_scope: "客户端不在该公开资料的业务范围",
  product_out_of_source_scope: "公开现货目录不能直接证明永续底层身份",
  native_currency_namespace_unresolved: "原生币种命名空间未明确",
  source_venue_not_supported: "暂无已审查的公开合约证据来源",
  public_native_currency_scope_unverified: "原始币种 ID 或来源范围未核验",
  network_or_contract_identity_incomplete: "网络或合约身份不完整",
  multiple_or_missing_contract_identities: "多个合约部署或合约缺失，不能任选首项",
  base_unit_scale_requires_review: "基币单位缩放待核验",
  conflicting_active_evidence: "有效证据指向不同资产，需人工修订",
};
const explain = (value: string) => reasons[value] ?? value;
function SourceLink({ url }: { url: string | null | undefined }) {
  return url && /^https:\/\//.test(url) ? <a className="text-button break" href={url} target="_blank" rel="noreferrer">{url}</a> : <span className="break">{url ?? "本地目录证据，无外部 URL"}</span>;
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
  const [evidenceOffset, setEvidenceOffset] = useState(0);
  const queryKey = `${limit}:${evidenceOffset}`;
  const currentQuery = useRef(queryKey);
  currentQuery.current = queryKey;
  const [venues, setVenues] = useState(["gate", "bitget", "kucoin"]);
  const load = async () => {
    const result = await api<Review>(`/api/assets/verification?limit=${limit}&evidence_offset=${evidenceOffset}`);
    if (currentQuery.current === queryKey) setData(result);
    return result;
  };
  useEffect(() => {
    let current = true;
    void api<Review>(`/api/assets/verification?limit=${limit}&evidence_offset=${evidenceOffset}`).then((result) => { if (current) setData(result); })
      .catch((caught: Error) => { if (current) setError(caught.message); });
    return () => { current = false; };
  }, [limit, evidenceOffset]);
  const act = async (name: string, action: () => Promise<void>) => {
    setBusy(name); setError(""); setMessage("");
    try { await action(); } catch (caught) { setError((caught as Error).message); }
    finally { setBusy(""); }
  };
  const select = (ids: string[]) => { setSelected(ids); setPreview(null); setMessage(""); };
  const coverage = data?.coverage;
  const sourceOptions = data?.sources.map((source) => source.venue) ?? ["gate", "bitget", "kucoin", "coinbase"];
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
      <div className="inline-checks">{sourceOptions.map((venue) => <label key={venue}><input type="checkbox" aria-label={`读取身份来源 ${venue}`}
        checked={venues.includes(venue)} disabled={!!busy} onChange={() => setVenues((old) => old.includes(venue) ? old.filter((item) => item !== venue) : [...old, venue])} />{venue}</label>)}</div>
      <button className="button secondary" type="button" disabled={!!busy || !venues.length} onClick={() => void act("读取公开资料", async () => {
        await api("/api/assets/verification/public-refresh", "POST", { venues });
        await load(); setPreview(null); setMessage("已完成公开来源读取尝试。请核对来源状态与提案；尚未应用映射。");
      })}>读取公开资产资料</button>
      <small className="muted">四个公开注册源均限定现货与真实原生币种 ID；不能把现货资料直接传播到永续。Coinbase 仅限 Exchange，不包含 Advanced Trade。缺 baseId、多链歧义、读取失败继续未核实，资金费身份全覆盖尚未完成。</small>
    </div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {message && <div className="notice" role="status">{message}</div>}
    {!!data?.sources.length && <div className="table-scroll"><table><thead><tr><th>公开来源</th><th>读取与覆盖</th><th>时间 / 限制</th></tr></thead><tbody>
      {data.sources.map((source) => <tr key={source.venue}><td><strong>{source.venue}</strong><small className="break"><SourceLink url={source.docs_url || source.endpoint} /></small></td>
        <td>{labels[source.state] ?? source.state}<small>返回 {num(source.response_rows, 0)} 条 · 匹配 {num(source.matched_markets, 0)} 市场</small><small>可用 {num(source.eligible_markets, 0)} · 合约阻止 {num(source.blocked_markets, 0)}</small>
          <small>原生范围 {num(source.scope_eligible_markets, 0)} · 范围拒绝 {num(source.scope_rejected_markets, 0)} · ID 未返回 {num(source.unmatched_currency_markets, 0)}</small>
          <small className="muted">client: {source.client_ids?.join(" / ") || "未提供"} · {source.product_kinds?.join(" / ") || "范围未知"}</small>
          {Object.entries(source.scope_reasons ?? {}).map(([reason, count]) => <small className="warning" key={reason}>{explain(reason)}：{num(count, 0)}</small>)}</td>
        <td><small>尝试 {time(source.last_attempt_at)} · 接收 {time(source.received_at)}</small>{source.error_type && <small className="warning">{source.error_type} · 未取得可核验证据</small>}
          <small className="muted">仅现货；永续需另核验底层 ID 与单位。</small></td></tr>)}</tbody></table></div>}
    {!!data?.platform_coverage?.length && <details className="calculation-details"><summary>各平台原生范围、冲突与覆盖</summary><div className="table-scroll"><table>
      <thead><tr><th>平台 / 公开范围</th><th>活跃与可追溯确认</th><th>公共证据覆盖</th><th>范围与冲突原因</th></tr></thead><tbody>
        {data.platform_coverage.map((platform) => <tr key={platform.venue}><td><strong>{platform.venue}</strong>
          <small>{platform.source_supported ? platform.client_ids.join(" / ") + " · " + platform.product_kinds.join(" / ") : "暂无审查通过的公开源"}</small></td>
          <td>活跃 {num(platform.active_markets, 0)}<small>可追溯确认 {num(platform.traceable_verified_markets, 0)} · 未核实 {num(platform.unverified_markets, 0)}</small></td>
          <td>可用证据 {num(platform.source_covered_markets, 0)}<small>原生范围 {num(platform.source_scope_markets, 0)} · 合约歧义 {num(platform.source_blocked_markets, 0)}</small></td>
          <td><small>范围拒绝 {num(platform.scope_rejected_markets, 0)} · 映射冲突 {num(platform.conflicting_markets, 0)}</small>
            {Object.entries({ ...platform.scope_reasons, ...platform.source_blockers }).map(([reason, count]) => <small className="warning" key={reason}>{explain(reason)}：{num(count, 0)}</small>)}</td></tr>)}</tbody>
    </table></div></details>}
    <IdentityCatalog onChanged={() => { void load(); onChanged(); }} />
    <IdentityBatchReview refreshKey={data?.as_of ?? 0} onChanged={() => { void load(); onChanged(); }} />
    <div className="filters" style={{ flexWrap: "wrap" }}><span>已选 {selected.length} 条证据</span>
      <button className="text-button" type="button" disabled={!!busy} onClick={() => select([...new Set(data?.proposals.filter((row) => row.status === "ready").flatMap((row) => row.evidence_ids) ?? [])])}>选择当前可核验证据</button>
      <button className="text-button" type="button" disabled={!!busy} onClick={() => select([])}>清空选择</button>
      <select aria-label="身份核验返回条数" value={limit} disabled={!!busy} onChange={(event) => { setData(null); setLimit(Number(event.target.value)); setEvidenceOffset(0); select([]); }}>
        {[200, 500, 2000].map((value) => <option key={value} value={value}>最多 {value} 条</option>)}</select>
      <button className="button secondary" type="button" disabled={!!busy || !selected.length} onClick={() => void act("预览", async () => {
        setPreview(await api<Preview>("/api/assets/verification/preview", "POST", { evidence_ids: selected }));
      })}>预览所选映射</button></div>
    {(data?.truncated.evidence || data?.truncated.proposals) && <div className="notice">本次证据或提案列表已截断；当前选择只覆盖已返回项目，提案列表不代表全部市场；证据可在下方翻页访问完整记录。</div>}
    {!!data?.proposals.length && <div className="table-scroll"><table><thead><tr><th>资产与市场</th><th>链与合约身份</th><th>核验状态</th><th>公开依据</th></tr></thead><tbody>
      {data.proposals.map((row) => <tr key={row.id}><td><strong>{row.symbols.join(" / ")}</strong><small>{row.venues.join(" / ")} · {row.instrument_ids.length} 个市场</small></td>
        <td className="break mono">{row.canonical_asset_id}</td><td><span className={`badge ${row.status === "ready" ? "mint" : "amber"}`}>{labels[row.status] ?? row.status}</span>
          {row.blockers.map((value) => <small className="warning break" key={value}>{explain(value)}</small>)}</td>
        <td>{row.source_urls.map((url) => <small className="break" key={url}><SourceLink url={url} /></small>)}<small>观测 {time(row.observed_at)} · 到期 {time(row.expires_at)}</small></td></tr>)}</tbody></table></div>}
    {preview && <div className="form-content"><h3>所选映射预览</h3><p>可应用 {preview.ready} 项 · 阻止 {preview.blocked} 项。应用时再次校验证据、原生身份与到期时间。</p>
      {preview.proposals.map((row) => <p className={row.status === "ready" ? "muted" : "warning"} key={row.id}>{row.symbols.join(" / ")} → {row.canonical_asset_id} · {labels[row.status] ?? row.status} {row.blockers.map(explain).join("；")} {row.existing_mappings && Object.entries(row.existing_mappings).map(([id, old]) => `${id}: 当前 ${old} → 提议 ${row.canonical_asset_id}`).join("；")}</p>)}
      <button className="button primary" type="button" disabled={!!busy || !preview.ready || preview.blocked > 0} onClick={() => void act("应用映射", async () => {
        await api("/api/assets/verification/apply", "POST", { evidence_ids: selected, preview_token: preview.preview_token });
        select([]); await load(); onChanged(); setMessage("已应用所选核验映射，审计记录已保存。");
      })}>应用已核验映射</button></div>}
    <details className="calculation-details"><summary>证据明细、修订与撤销</summary>
      <p>完整证据共 {data?.evidence_total ?? 0} 条，当前偏移 {data?.evidence_offset ?? 0}；撤销仅针对勾选的 {selected.length} 条证据。当前依赖市场：{[...new Set(data?.evidence.filter(r => selected.includes(r.id)).flatMap(r => r.affected_instrument_ids ?? []))].join("、") || "无"}。提交时按最新依赖关系撤销；不会覆盖人工映射。翻页清空选择。</p>
      <button disabled={!!busy || !evidenceOffset} onClick={() => { setData(null); select([]); setEvidenceOffset(0); }}>证据首页</button>
      <button disabled={!!busy || !evidenceOffset} onClick={() => { setData(null); select([]); setEvidenceOffset(Math.max(0, evidenceOffset-limit)); }}>上一页证据</button>
      <button disabled={!!busy || data?.evidence_next_offset == null} onClick={() => { if (data?.evidence_next_offset != null) { setEvidenceOffset(data.evidence_next_offset); setData(null); select([]); } }}>下一页证据</button>
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
