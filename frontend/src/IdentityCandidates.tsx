import { useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { api } from "./api";
import type { IdentityCandidate } from "./types";

const conditionNames: Record<string, string> = {
  unverified_asset_mapping: "资产映射尚未核验",
  different_asset_identities: "同名市场属于不同资产身份",
  different_native_chain_addresses: "原生链上地址不同",
  different_chains: "链不同，需核对跨链关系",
  different_base_symbols_require_unit_review: "基础币种标识不同，需核对单位与包装关系",
  cross_venue_mapping_requires_confirmation: "跨平台映射需要人工确认",
  chain_metadata_conflict: "链元数据与原生身份冲突",
  mapped_address_conflicts_with_native_identity: "映射地址与原生身份冲突",
  invalid_native_chain_address: "原生链上地址格式无效",
  contains_inactive_markets: "包含非活跃市场",
  quote_token_contract_identity_unavailable: "报价代币合约身份尚未提供或存在冲突",
  quote_identity_requires_freshness_check: "报价身份需按策略时效核验",
  quote_identity_evidence_expired: "报价身份证据已到期",
  mapping_evidence_expired: "资产映射证据已到期",
  mapping_not_known_as_of: "该时点尚未确认此资产映射",
  quote_conversion_with_timestamped_fx: "报价币不同，需要带时间的汇率换算",
  contract_quantity_to_base_conversion: "合约数量需要换算为基础币数量",
  inverse_contract_conversion: "反向合约需要独立单位换算",
};
const kindNames: Record<string, string> = { identity: "身份分组", ticker_candidate: "同 ticker 多身份" };
const pageSize = 20;

export function IdentityCandidates({ refreshKey = "" }: { refreshKey?: string }) {
  const [candidates, setCandidates] = useState<IdentityCandidate[]>([]);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [page, setPage] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    void api<{ candidates: IdentityCandidate[] }>("/api/assets/candidates")
      .then((data) => { if (current) { setCandidates(data.candidates ?? []); setPage(0); } })
      .catch((e) => { if (current) setError((e as Error).message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [refreshKey, reload]);
  const filtered = useMemo(() => candidates.filter((row) =>
    (!kind || row.kind === kind) && JSON.stringify(row).toLowerCase().includes(query.trim().toLowerCase()))
    .sort((left, right) => Number(right.kind === "ticker_candidate") - Number(left.kind === "ticker_candidate") ||
      left.symbols.join("/").localeCompare(right.symbols.join("/"))),
    [candidates, kind, query]);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const visible = filtered.slice(page * pageSize, (page + 1) * pageSize);
  return (
    <section className="panel" aria-labelledby="identity-candidates-heading">
      <div className="panel-heading">
        <div>
          <h2 id="identity-candidates-heading">资产身份候选 <span className="count">{candidates.length}</span></h2>
          <p>查看同名 ticker 与原生身份关系。候选不会自动合并或确认，人工核验后可在配置页保存映射。</p>
        </div>
        <button type="button" className="button secondary" disabled={loading} onClick={() => setReload((old) => old + 1)}>
          {loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}刷新候选
        </button>
      </div>
      <div className="filters">
        <div className="search">
          <Search size={17} />
          <input aria-label="搜索身份候选" value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }}
            placeholder="搜索任意 ticker、原生身份、合约或平台…" />
        </div>
        <select aria-label="筛选身份候选类型" value={kind} onChange={(e) => { setKind(e.target.value); setPage(0); }}>
          <option value="">全部候选类型</option>
          {[...new Set(candidates.map((row) => row.kind))].sort().map((value) =>
            <option key={value} value={value}>{kindNames[value] ?? value}</option>)}
        </select>
        <span className="muted">显示 {filtered.length} 项</span>
      </div>
      {error && <div className="notice error" role="alert">身份候选加载失败：{error}</div>}
      {!error && !loading && !filtered.length && <div className="form-content">
        <p className="muted">{candidates.length ? "没有符合筛选条件的身份候选。" : "当前尚无候选；市场发现完成后可刷新查看。"}</p>
      </div>}
      {filtered.length > 0 && <div className="table-scroll">
        <table>
          <thead><tr><th>候选 / ticker</th><th>平台与原生身份</th><th>身份与比较条件</th><th>待核验事项</th></tr></thead>
          <tbody>{visible.map((row) => <tr key={`${row.kind}:${row.id}`}>
            <td><strong>{row.symbols.join(" / ") || row.id}</strong><small className="muted break">{kindNames[row.kind] ?? row.kind} · {row.id}</small>
              <small className="muted">{row.instrument_ids.length} 个市场</small></td>
            <td><div className="platform-stack">{row.venues.map((venue) => <span className="badge" key={venue}>{venue}</span>)}</div>
              {[...new Set(row.markets.map((market) => String(market.native_asset_id ?? market.asset_id ?? "未提供")))]
                .slice(0, 3).map((identity) => <small className="muted break mono" key={identity}>{identity}</small>)}
              <details className="raw-details"><summary>原生身份与市场明细</summary>
                <pre>{JSON.stringify({ identities: row.identities ?? row.asset_ids, markets: row.markets }, null, 2)}</pre>
              </details></td>
            <td><span className={`badge ${row.identity_compatible ? "green" : "amber"}`}>
              {row.identity_compatible ? "原生身份兼容" : "身份尚不兼容"}</span>
              <small className="muted">{row.comparison_ready ? "身份比较条件满足 · 行情与成本另行核对" : "比较条件未满足"}</small>
              <small className="muted"><ShieldCheck size={12} /> 未自动确认映射</small></td>
            <td>{(row.blockers ?? []).map((item, index) => <p className="warning break" key={`b${index}`}>{conditionNames[item] ?? item}</p>)}
              {(row.requirements ?? []).map((item, index) => <p className="muted break" key={`r${index}`}>附加条件：{conditionNames[item] ?? item}</p>)}
              {(row.warnings ?? []).map((item, index) => <p className="muted break" key={`w${index}`}>{conditionNames[item] ?? item}</p>)}
              {!row.blockers?.length && !row.requirements?.length && !row.warnings?.length && <span className="muted">没有额外身份提示</span>}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      {filtered.length > pageSize && <div className="panel-foot">
        <span>第 {page + 1} / {pages} 页 · 全部 {filtered.length} 个候选均可搜索</span>
        <div className="platform-stack">
          <button className="button secondary" type="button" disabled={page === 0} onClick={() => setPage((old) => old - 1)}>上一页候选</button>
          <button className="button secondary" type="button" disabled={page + 1 >= pages} onClick={() => setPage((old) => old + 1)}>下一页候选</button>
        </div>
      </div>}
    </section>
  );
}
