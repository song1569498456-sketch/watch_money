import { useEffect, useState } from "react";
import { api, time } from "./api";

type Row = { instrument_id: string; venue: string; symbol: string; kind: string; change: string;
  coin_ids: string[]; contracts: string[]; official_contracts: string[]; reasons: string[]; source_at?: number };
type Catalog = { enabled: boolean; demo_key_configured: boolean; at?: number; next_update_at?: number;
  counts: Record<string, number>; total: number; offset: number; next_offset: number | null; rows: Row[];
  provider?: { error?: string; complete: boolean; sources: Record<string, { pages: number; rows: number; complete: boolean; error?: string }> };
  official_sources?: { venue: string; state: string; http_status?: number; error_type?: string }[]; denied_official_sources?: string[];
  budget: { used?: number; blocked?: boolean }; paused?: string[] };
const labels: Record<string, string> = { new: "新增待审核", changed: "身份变化待审核", unresolved: "尚未确定", unchanged: "已核验且未变化",
  demo_key_required: "未配置 CoinGecko Demo key", access_denied_requires_configuration_review: "访问被拒，已停止自动请求；需检查环境或密钥配置",
  rate_limit_cooldown: "触发限流，冷却中", page_budget_reached: "达到本轮分页预算，覆盖不完整",
  missing_or_ambiguous_exchange_coin_id: "交易所币种 ID 缺失或不唯一", official_contract_not_corroborated: "官方合约证据不一致或缺失",
  cross_chain_or_native_asset_requires_review: "跨链、多合约或原生币需单独核验", provider_coverage_incomplete: "辅助来源覆盖不完整",
  provider_ticker_flagged_or_unknown: "辅助来源行情状态异常或未知", product_out_of_source_scope: "永续等产品不使用现货身份证据",
  scaled_product_requires_independent_proof: "倍率产品需独立单位依据", source_venue_not_supported: "尚无已审阅的官方身份来源" };
const explain = (v: string) => labels[v] ?? v;
export function IdentityCatalog({ onChanged }: { onChanged: () => void }) {
  const [data, setData] = useState<Catalog | null>(null);
  const [offset, setOffset] = useState(0);
  const [changed, setChanged] = useState(true);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [accessReason, setAccessReason] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true; setData(null);
    void api<Catalog>(`/api/assets/catalog?offset=${offset}&changed_only=${changed}`).then(v => { if (live) setData(v); })
      .catch((e: Error) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [offset, changed, version]);
  const act = async (job: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await job(); setOffset(0); setVersion(v => v+1); onChanged(); }
    catch (e) { setError((e as Error).message); setVersion(v => v+1); }
    finally { setBusy(false); }
  };
  return <section className="form-content" aria-label="币种目录持续更新">
    <h3>币种目录持续更新</h3>
    <p>首次更新保存匹配候选，再在下方批量审核；仅更新已批准且证据完全未变的映射有效期。新币、冲突和无法确定的项目保留待审核，合约或倍率变化会暂停相关可靠比较。</p>
    <p>CoinGecko 只作辅助资料，不按同名合并，不用现货证据证明永续或跨链包装币。<a href="https://www.coingecko.com/en/api" target="_blank" rel="noreferrer">Data provided by CoinGecko</a></p>
    <small>Demo key：{data?.demo_key_configured ? "后端已配置" : "未配置；在本地 .env 设置 COINGECKO_DEMO_API_KEY，不在页面粘贴"}。本应用当月请求 {data?.budget.used ?? 0}/9000；其它客户端用量未知。</small>
    <div className="scanner-pagination">
      <button disabled={busy} onClick={() => void act(() => api("/api/assets/catalog/refresh", "POST"))}>{busy ? "更新中…" : "更新并保存目录候选"}</button>
      <label><input aria-label="每天自动更新身份目录" type="checkbox" disabled={busy || !data} checked={data?.enabled ?? false}
        onChange={e => { const enabled = e.target.checked; setData(old => old ? { ...old, enabled } : old); void act(() => api("/api/assets/catalog/automatic", "PUT", { enabled })); }} />运行期间每天自动更新</label>
      <label><input type="checkbox" checked={changed} disabled={busy} onChange={e => { setOffset(0); setChanged(e.target.checked); }} />只显示待处理项目</label>
    </div>
    <small>上次更新 {time(data?.at)} · 下次计划 {time(data?.next_update_at)}。停机不运行；缺密钥仍可更新支持的平台官方资料。访问拒绝不会自动重试。</small>
    {error && <p role="alert">{error}</p>}
    {data?.provider?.error && <p className="warning">{explain(data.provider.error)}</p>}
    {!!data?.budget.blocked && <p className="warning">CoinGecko 访问已暂停；请核对网络或Demo key，不能通过更换主机绕过限制。</p>}
    {data?.official_sources?.filter(s => s.state !== "observed").map(s => <p key={s.venue} className="warning">{s.venue} 官方资料未成功更新：{s.http_status ?? s.error_type ?? s.state}；不视为币种消失。</p>)}
    {(!!data?.budget.blocked || !!data?.denied_official_sources?.length) && <details><summary>已修正访问配置后恢复</summary><p>仅在已解决权限、密钥或网络配置问题后填写原因；不会更换主机或立即发请求，也不会清除请求预算。不要填写密钥。</p><input aria-label="访问配置修正说明" value={accessReason} onChange={e => setAccessReason(e.target.value)} maxLength={300} /><button disabled={busy || accessReason.trim().length < 10} onClick={() => void act(() => api("/api/assets/catalog/access-reviewed", "POST", { reason: accessReason.trim() }))}>确认配置已修正</button></details>}
    {Object.entries(data?.provider?.sources ?? {}).map(([v, s]) => <small key={v}>{v}：{s.rows} 条 / {s.pages} 页 · {s.complete ? "本轮分页结束（非全部市场保证）" : explain(s.error ?? "provider_coverage_incomplete")}</small>)}
    <p>待处理/当前筛选 {data?.total ?? 0} 项 · 新增 {data?.counts.new ?? 0} · 变化 {data?.counts.changed ?? 0} · 未确定 {data?.counts.unresolved ?? 0}。不计为新增可靠覆盖。</p>
    <div className="table-scroll"><table><thead><tr><th>市场 / 状态</th><th>辅助 ID / 合约</th><th>官方证据 / 原因</th></tr></thead><tbody>{data?.rows.map(r => <tr key={r.instrument_id}>
      <td className="break">{r.venue} · {r.symbol}<small>{r.kind} · {explain(r.change)}</small></td>
      <td className="break">{r.coin_ids.join(" / ") || "未知"}<small>{r.contracts.join(" / ")}</small><small>来源观测 {time(r.source_at)}</small></td>
      <td className="break">{r.official_contracts.join(" / ") || "未匹配"}{r.reasons.map(v => <small key={v}>{explain(v)}</small>)}</td></tr>)}</tbody></table></div>
    <div className="scanner-pagination"><button disabled={busy || !data || !offset} onClick={() => setOffset(Math.max(0, offset-50))}>上一页目录</button><button disabled={busy || data?.next_offset == null} onClick={() => setOffset(data?.next_offset ?? 0)}>下一页目录</button></div>
    <small>暂停后保留原映射供核对；可在证据明细撤销旧证据、刷新官方资料，再按现有预览流程重新批准。人工映射不被自动覆盖。多链合约、未知倍率和辅助来源缺失仍需审核。</small>
  </section>;
}
