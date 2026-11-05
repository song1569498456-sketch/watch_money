import { useEffect, useState } from "react";
import { api, num, time } from "./api";

type Group = { id: string; canonical_asset_id: string | null; symbols: string[]; venues: string[];
  instrument_ids: string[]; evidence_ids: string[]; status: string; blockers: string[];
  chain: string | null; contract: string | null; sources: string[];
  evidence: { id: string; instrument_id: string; evidence_kind: string; source_url: string;
    source_field: string; source_digest: string; observed_at: number; expires_at: number }[] };
type Review = { as_of: number; actual_comparable_groups: number; actual_comparable_markets: number;
  global_counts: Record<string, number>; filtered_groups: number; filtered_counts: Record<string, number>;
  offset: number; limit: number; next_offset: number | null; groups: Group[];
  options: { sources: string[]; chains: string[]; blockers: Record<string, number> } };
type Plan = { plan_token: string; selected_proposal_ids: string[]; eligible_evidence_ids: string[];
  counts: Record<string, number>; eligible_groups: Group[]; blocked_groups: Group[]; already_verified_groups: Group[] };
const labels: Record<string, string> = { eligible: "可进入批量预览（未批准）", blocked: "被阻止", already_verified: "已核验，跳过" };
const reasons: Record<string, string> = {
  evidence_record_integrity_failed: "证据记录完整性校验失败", source_digest_invalid: "来源摘要无效",
  native_identity_changed: "目录原生身份已变化", official_currency_scope_unverified: "官方币种命名空间未确认",
  relationship_or_base_unit_requires_review: "包装关系或基币单位仍需核验", canonical_identity_unresolved: "缺少明确链与合约身份",
  base_unit_scale_requires_review: "基币单位缩放待核验", evidence_not_active: "证据已撤销或非有效状态",
  different_native_chain_addresses: "原生链上合约不同", different_chains: "链身份不同",
  conflicting_active_evidence: "有效证据指向不同资产", evidence_expired_or_future: "证据过期或时间在未来",
  evidence_older_than_24h_or_future: "公开观测超过24小时或在未来", evidence_validity_window_insufficient: "有效期不足1小时或超出策略范围",
  manual_or_nonpublic_evidence_requires_review: "手动证据需走逐条审核", official_endpoint_unverified: "未匹配登记的官方来源",
  two_distinct_official_venues_required: "缺少两个独立官方平台", single_exact_chain_contract_required: "未证明唯一同链同合约",
  existing_explicit_mapping_requires_manual_review: "已有人工映射，不能批量覆盖", existing_verified_mapping_requires_manual_review: "已有确认映射，需人工核对",
  latest_official_source_receipt_unavailable: "最新官方来源读取不匹配或失败", catalog_identity_changed: "目录身份已变化",
  public_native_currency_scope_unverified: "原生币种或产品范围未确认", active_ordinary_spot_required: "仅支持活跃普通现货",
};
const explain = (value: string) => reasons[value] ?? value;

export function IdentityBatchReview({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const [source, setSource] = useState("");
  const [chain, setChain] = useState("");
  const [contract, setContract] = useState("");
  const [contractQuery, setContractQuery] = useState("");
  const [state, setState] = useState("");
  const [blocker, setBlocker] = useState("");
  const [reload, setReload] = useState(0);
  const scope = JSON.stringify([source, chain, contract, state, blocker]);
  const [page, setPage] = useState({ scope, offset: 0 });
  if (page.scope !== scope) setPage({ scope, offset: 0 });
  const offset = page.scope === scope ? page.offset : 0;
  const key = JSON.stringify([scope, offset, refreshKey, reload]);
  const [selection, setSelection] = useState({ key, ids: [] as string[] });
  if (selection.key !== key) setSelection({ key, ids: [] });
  const selected = selection.key === key ? selection.ids : [];
  const [loaded, setLoaded] = useState<{ key: string; data: Review } | null>(null);
  const [plan, setPlan] = useState<{ signature: string; value: Plan } | null>(null);
  const signature = JSON.stringify([key, selected]);
  const preview = plan?.signature === signature ? plan.value : null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [applied, setApplied] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const current = loaded?.key === key;
  const data = loaded?.data;
  useEffect(() => { const timer = setTimeout(() => setContractQuery(contract), 250); return () => clearTimeout(timer); }, [contract]);
  useEffect(() => {
    if (contractQuery !== contract) return;
    const controller = new AbortController(); let live = true;
    setError("");
    const query = new URLSearchParams({ offset: String(offset), limit: "25", source, chain, contract: contractQuery, state, blocker });
    void api<Review>(`/api/assets/verification/batch-review?${query}`, "GET", undefined, { signal: controller.signal })
      .then((value) => { if (live) setLoaded({ key, data: value }); })
      .catch((e: Error) => { if (live) setError(e.message); });
    return () => { live = false; controller.abort(); };
  }, [key, contractQuery]);
  const act = async (job: () => Promise<void>) => {
    setBusy(true); setError(""); setMessage("");
    try { await job(); } catch (e) { setPlan(null); setError(`操作未完成：${(e as Error).message}。请刷新分组后重新预览。`); }
    finally { setBusy(false); }
  };
  const choose = (ids: string[]) => { setSelection({ key, ids }); setPlan(null); };
  return <section className="form-content identity-batch-review" aria-label="公开证据分组审核">
    <h3>公开证据分组审核</h3>
    <p>实际通过跨平台身份门槛：{num(data?.actual_comparable_groups, 0)} 组 / {num(data?.actual_comparable_markets, 0)} 市场。
      候选可批量审核 {num(data?.global_counts.eligible_groups, 0)} 组，被阻止 {num(data?.global_counts.blocked_groups, 0)} 组。候选不是已批准覆盖，身份通过也不代表行情新鲜或资金费可比。</p>
    <small>检查于 {time(data?.as_of)}；应用前重新核验。</small>
    <button disabled={busy} onClick={() => setReload(v => v+1)}>刷新公开分组</button>
    <div className="scanner-pagination">
      <select aria-label="审核来源筛选" disabled={busy} value={source} onChange={e => setSource(e.target.value)}><option value="">全部来源</option>{data?.options.sources.map(v => <option key={v}>{v}</option>)}</select>
      <select aria-label="审核链筛选" disabled={busy} value={chain} onChange={e => setChain(e.target.value)}><option value="">全部链</option>{data?.options.chains.map(v => <option key={v}>{v}</option>)}</select>
      <input aria-label="审核合约筛选" disabled={busy} value={contract} maxLength={200} placeholder="链 / 合约地址包含…" onChange={e => setContract(e.target.value)} />
      <select aria-label="审核状态筛选" disabled={busy} value={state} onChange={e => setState(e.target.value)}><option value="">全部状态</option><option value="eligible">可批量预览</option><option value="blocked">被阻止（含冲突/过期）</option><option value="already_verified">已核验</option></select>
      <select aria-label="审核阻止原因筛选" disabled={busy} value={blocker} onChange={e => setBlocker(e.target.value)}><option value="">全部阻止原因</option>{Object.entries(data?.options.blockers ?? {}).map(([v, count]) => <option key={v} value={v}>{explain(v)} · {count}</option>)}</select>
    </div>
    <p className="muted">来源筛选保留整组所有证据与冲突；不只留下某个平台的有利证据。仅普通现货，同链同合约且来源、时效、单位全部满足策略才可应用；永续底层仍需独立核验。</p>
    {!current && <p role="status">正在核对分组；旧页面暂不可操作。</p>}
    {error && <p className="notice error" role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <div className="scanner-pagination" aria-label="证据分组分页">
      <span>筛选结果 {num(data?.filtered_groups, 0)} 组；当前 {data?.groups.length ? data.offset + 1 : 0}–{data?.groups.length ? data.offset + data.groups.length : 0}，已选 {selected.length} 组（仅当前页）</span>
      <button disabled={busy || !current || !offset} onClick={() => setPage({ scope, offset: 0 })}>分组首页</button>
      <button disabled={busy || !current || !offset} onClick={() => setPage({ scope, offset: Math.max(0, offset-25) })}>上一页分组</button>
      <button disabled={busy || !current || data?.next_offset == null} onClick={() => data?.next_offset != null && setPage({ scope, offset: data.next_offset })}>下一页分组</button>
      <button disabled={busy || !current} onClick={() => choose(data?.groups.filter(g => g.status === "eligible").map(g => g.id) ?? [])}>选择本页可审核组</button>
      <button disabled={busy || !selected.length} onClick={() => choose([])}>清空分组选择</button>
      <button disabled={busy || !current || !selected.length} onClick={() => void act(async () => {
        const value = await api<Plan>("/api/assets/verification/batch-preview", "POST", { proposal_ids: selected });
        setPlan({ signature, value });
      })}>预览所选公开分组</button>
    </div>
    <p className="muted">本页可审核 {data?.groups.filter(g => g.status === "eligible").length ?? 0} 组；阻止 {data?.groups.filter(g => g.status === "blocked").length ?? 0} 组；已核验 {data?.groups.filter(g => g.status === "already_verified").length ?? 0} 组。一键选择只选可审核组，其他项目的原因见下表。</p>
    <small className="muted">每页25组，此审核流程每批最多100组/10000条证据；翻页或筛选清空选择。资料变化时顺序可变化，应用前会在事务内重新检查完整证据与来源。</small>
    <div className="table-scroll"><table><thead><tr><th>选择 / 资产</th><th>链与合约</th><th>状态与跳过原因</th><th>证据与市场</th></tr></thead><tbody>
      {data?.groups.map(g => <tr key={g.id}><td><label><input type="checkbox" disabled={busy || !current} aria-label={`选择公开分组 ${g.id}`} checked={selected.includes(g.id)} onChange={() => choose(selected.includes(g.id) ? selected.filter(id => id !== g.id) : [...selected, g.id])} />{g.symbols.join(" / ")}</label><small>{g.venues.join(" / ")} · {g.instrument_ids.length} 市场</small></td>
        <td className="break">{g.chain ?? "链未明确"}<small className="break mono">{g.contract ?? g.canonical_asset_id ?? "合约未明确"}</small></td>
        <td>{labels[g.status] ?? g.status}{g.blockers.map(r => <small className="warning break" key={r}>{explain(r)}</small>)}</td>
        <td><details><summary>检查 {g.evidence.length} 条完整依据</summary>{g.evidence.map(f => <div key={f.id}><small className="break">{f.instrument_id} · {f.evidence_kind}</small><small className="break">{f.source_field}</small>{/^https:\/\//.test(f.source_url) && <a href={f.source_url} target="_blank" rel="noreferrer" className="break">{f.source_url}</a>}<small>观测 {time(f.observed_at)} · 到期 {time(f.expires_at)}</small><small className="break">摘要 {f.source_digest ?? "无"}</small></div>)}</details></td></tr>)}
    </tbody></table></div>
    {current && data?.groups.length === 0 && <p>此页没有分组；请返回首页或调整筛选，未读取资料不等于没有市场。</p>}
    {preview && current && <div className="notice" aria-label="公开批量应用预览"><strong>将应用 {preview.counts.eligible_groups} 组 / {preview.counts.eligible_markets} 市场；跳过阻止 {preview.counts.blocked_groups} 组、已核验 {preview.counts.already_verified_groups} 组。</strong>
      {[...preview.eligible_groups, ...preview.blocked_groups, ...preview.already_verified_groups].map(g => <p className="break" key={g.id}>{g.symbols.join(" / ")} → {g.canonical_asset_id} · {labels[g.status]} {g.blockers.map(explain).join("；")}</p>)}
      <button className="button primary" disabled={busy || !preview.counts.eligible_groups} onClick={() => void act(async () => {
        const result = await api<{ applied_groups: number; applied_markets: number }>("/api/assets/verification/batch-apply", "POST", { plan_token: preview.plan_token, proposal_ids: preview.selected_proposal_ids });
        setApplied(preview.eligible_evidence_ids); choose([]); setReload(v => v+1); onChanged();
        setMessage(`实际应用 ${result.applied_groups} 组 / ${result.applied_markets} 市场。行情有效性仍需另查。`);
      })}>确认应用此批公开映射</button></div>}
    {!!applied.length && <div className="notice"><p>本次会话上一批的 {applied.length} 条证据可撤销；历史撤销也可使用下方证据明细。</p>
      <input aria-label="公开批次撤销原因" value={reason} maxLength={500} onChange={e => setReason(e.target.value)} placeholder="填写撤销原因" />
      <button disabled={busy || !reason.trim()} onClick={() => void act(async () => { await api("/api/assets/verification/revoke", "POST", { evidence_ids: applied, reason: reason.trim() }); setApplied([]); setReload(v => v+1); onChanged(); setMessage("已撤销上一批证据并重算身份，审计保留。"); })}>撤销上一批公开映射</button></div>}
  </section>;
}
