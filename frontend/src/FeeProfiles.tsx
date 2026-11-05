import { useEffect, useState } from "react";
import { Loader2, RefreshCw, Save } from "lucide-react";
import { api, kindName, pct, time } from "./api";
import type { Instrument } from "./types";

export type FeeLiquidity = "maker" | "taker";
export type FeeEvidence = {
  rate: number | null;
  source: string | null;
  conditions: string;
  updated_at: number | null;
  checked_at?: number | null;
  fee_currency?: string | null;
  tier?: string | null;
  source_verified?: boolean;
  evidence_status?: string;
};
export type FeeProfile = {
  key: string;
  venue: string;
  kind: string;
  liquidity: FeeLiquidity;
  applicable: boolean;
  default: FeeEvidence;
  override: FeeEvidence | null;
  effective: FeeEvidence;
};
type FeeResponse = { profiles: FeeProfile[]; as_of?: number };
type FeeLeg = {
  instrumentId: string;
  liquidity: FeeLiquidity;
  value: string;
  manual: boolean;
  evidence: FeeEvidence | null;
  overridden: boolean;
};
const legNames = ["买方开仓", "买方平仓", "卖方开仓", "卖方平仓"];
const emptyLeg = (instrumentId = "", liquidity: FeeLiquidity = "taker"): FeeLeg => ({
  instrumentId, liquidity, value: "", manual: false, evidence: null, overridden: false,
});
const percentInput = (value: number | null | undefined) =>
  value != null && Number.isFinite(value) ? String(Number((value * 100).toPrecision(12))) : "";
const evidenceTime = (evidence: FeeEvidence | null) => evidence?.updated_at ?? evidence?.checked_at ?? null;
function fromProfile(instrumentId: string, liquidity: FeeLiquidity, profiles: FeeProfile[]): FeeLeg {
  const profile = profiles.find((row) => row.liquidity === liquidity);
  return { instrumentId, liquidity, value: profile?.applicable ? percentInput(profile.effective.rate) : "",
    manual: false, evidence: profile?.effective ?? null, overridden: !!profile?.override };
}
function parsePercent(value: string, label: string) {
  if (value.trim() === "") return null;
  const parsed = Number(value) / 100;
  if (!Number.isFinite(parsed) || parsed < -0.1 || parsed > 1) throw new Error(`${label}必须为 -10% 到 100% 的有限数值`);
  return parsed;
}

/** Each fee value is owned by an instrument identity; stale state is never reusable. */
export function useTradeFees(buy?: Instrument, sell?: Instrument) {
  const [legs, setLegs] = useState<FeeLeg[]>(() => legNames.map(() => emptyLeg()));
  const [holding, setHolding] = useState([{ instrumentId: "", value: "" }, { instrumentId: "", value: "" }]);
  const [profiles, setProfiles] = useState<Record<string, FeeProfile[]>>({});
  const [errors, setErrors] = useState(["", ""]);
  const [loading, setLoading] = useState([false, false]);
  const [reload, setReload] = useState(0);
  const instrumentIds = [buy?.id ?? "", sell?.id ?? ""];

  const loadSide = (side: number, instrumentId: string) => {
    let current = true;
    setLegs((old) => old.map((leg, index) => Math.floor(index / 2) === side ? emptyLeg(instrumentId, leg.liquidity) : leg));
    setHolding((old) => old.map((value, index) => index === side ? { instrumentId, value: "" } : value));
    setErrors((old) => old.map((value, index) => index === side ? "" : value));
    setLoading((old) => old.map((value, index) => index === side ? !!instrumentId : value));
    if (instrumentId) void api<FeeResponse>(`/api/fees?instrument_id=${encodeURIComponent(instrumentId)}`)
      .then((data) => {
        if (!current) return;
        const rows = data.profiles ?? [];
        setProfiles((old) => ({ ...old, [instrumentId]: rows }));
        setLegs((old) => old.map((leg, index) => Math.floor(index / 2) === side &&
          leg.instrumentId === instrumentId && !leg.manual ? fromProfile(instrumentId, leg.liquidity, rows) : leg));
      })
      .catch((error) => { if (current) setErrors((old) => old.map((value, index) => index === side ? (error as Error).message : value)); })
      .finally(() => { if (current) setLoading((old) => old.map((value, index) => index === side ? false : value)); });
    return () => { current = false; };
  };
  useEffect(() => loadSide(0, buy?.id ?? ""), [buy?.id, reload]);
  useEffect(() => loadSide(1, sell?.id ?? ""), [sell?.id, reload]);

  const currentLeg = (index: number): FeeLeg => legs[index].instrumentId === instrumentIds[Math.floor(index / 2)]
    ? legs[index] : emptyLeg(instrumentIds[Math.floor(index / 2)], legs[index].liquidity);
  const holdingValue = (side: number) => holding[side].instrumentId === instrumentIds[side] ? holding[side].value : "";
  const setValue = (index: number, value: string) => setLegs((old) => old.map((leg, position) => position === index ?
    { ...currentLeg(index), value, manual: true } : leg));
  const setLiquidity = (index: number, liquidity: FeeLiquidity) => setLegs((old) => old.map((leg, position) =>
    position === index ? fromProfile(instrumentIds[Math.floor(index / 2)], liquidity,
      profiles[instrumentIds[Math.floor(index / 2)]] ?? []) : leg));
  const setHoldingValue = (side: number, value: string) => setHolding((old) => old.map((entry, index) =>
    index === side ? { instrumentId: instrumentIds[side], value } : entry));
  const cost = (instrument: Instrument, side: number, now: number) => {
    const open = currentLeg(side * 2), close = currentLeg(side * 2 + 1);
    const evidence = (leg: FeeLeg, label: string) => ({
      source: `${label} ${leg.liquidity}: ${leg.manual ? "用户假设（本次手填覆盖）" : leg.overridden ?
        `用户本地默认覆盖：${leg.evidence?.source ?? "未注明"}` : leg.evidence?.source ?? "费率来源未提供"}`,
      at: leg.manual ? now : evidenceTime(leg.evidence),
      conditions: leg.manual ? `${label}使用本次手填费率，属于用户假设` : `${label}: ${leg.evidence?.conditions || "适用条件未提供"}`,
    });
    const facts = [evidence(open, "开仓"), evidence(close, "平仓")];
    const held = holdingValue(side);
    if (held !== "") facts.push({ source: "持仓费：用户假设（本次手填）", at: now, conditions: "每小时持仓成本为用户假设" });
    const times = facts.map((fact) => fact.at).filter((value): value is number => value != null);
    const holdingRate = held === "" ? null : Number(held) / 100;
    if (holdingRate != null && (!Number.isFinite(holdingRate) || holdingRate < 0)) throw new Error("持仓成本必须为非负有限数值");
    return { instrument_id: instrument.id, open_fee_rate: parsePercent(open.value, `${side ? "卖" : "买"}方开仓费率`),
      close_fee_rate: parsePercent(close.value, `${side ? "卖" : "买"}方平仓费率`), holding_rate_per_hour: holdingRate,
      source: facts.map((fact, index) => `${fact.source} · ${index < 2 && ![open, close][index].manual &&
        [open, close][index].evidence?.updated_at == null ? "资料核验时间" : "来源时间"} ${fact.at ?? "未知"}`).join("；"),
      at: times.length === facts.length ? Math.min(...times) : null,
      conditions: facts.map((fact) => fact.conditions).join("；") };
  };
  return { currentLeg, holdingValue, setValue, setLiquidity, setHoldingValue, cost, errors, loading,
    reload: () => setReload((old) => old + 1) };
}

export function FeeCostFields({ fees }: { fees: ReturnType<typeof useTradeFees> }) {
  return <>
    <div className="form-grid four">
      {legNames.map((name, index) => {
        const leg = fees.currentLeg(index);
        return <div className="field" key={name}>
          <label htmlFor={`fee-liquidity-${index}`}>{name}成交方式</label>
          <select id={`fee-liquidity-${index}`} value={leg.liquidity}
            onChange={(event) => fees.setLiquidity(index, event.target.value as FeeLiquidity)}>
            <option value="taker">Taker · 吃单</option><option value="maker">Maker · 挂单</option>
          </select>
          <label htmlFor={`fee-rate-${index}`}>{name}费率 / %</label>
          <input id={`fee-rate-${index}`} type="number" min="-10" max="100" step="any" placeholder="未知 / 待配置"
            value={leg.value} onChange={(event) => fees.setValue(index, event.target.value)} />
          <small className="muted break">{leg.manual ? "用户假设 · 本次手填覆盖，计算时记录当前时间" : leg.overridden ?
            "用户本地默认覆盖" : leg.value === "" ? "默认未知或适用范围未确认" : "普通公开默认"}</small>
          {!leg.manual && <small className="muted break">{leg.evidence?.source || "来源未提供"} · 更新 {time(leg.evidence?.updated_at)}
            {leg.evidence?.checked_at != null && ` · 资料核验 ${time(leg.evidence.checked_at)}`}</small>}
          {leg.evidence?.conditions && <small className="muted break">{leg.manual ? "参考默认条件" : "适用条件"}：{leg.evidence.conditions}</small>}
          {leg.evidence?.fee_currency && <small className="muted">计费币种：{leg.evidence.fee_currency}</small>}
        </div>;
      })}
    </div>
    <div className="form-grid three">
      {["买方", "卖方"].map((name, side) => <label className="field" key={name}>
        <span>{name}持仓成本 / % 每小时</span>
        <input type="number" min="0" step="any" placeholder="未知 / 待配置" value={fees.holdingValue(side)}
          onChange={(event) => fees.setHoldingValue(side, event.target.value)} />
        <small>手填值为用户假设；切换该方市场后清空</small>
      </label>)}
      <div className="field"><span>费率资料</span><button type="button" className="button secondary"
        disabled={fees.loading.some(Boolean)} onClick={fees.reload}>
        <RefreshCw size={14} />重新加载平台费率</button><small>重新加载会清除本次手填费用；未知值保持空白。</small></div>
    </div>
    {fees.loading.some(Boolean) && <p className="muted" role="status">正在加载所选市场适用的普通公开费率…</p>}
    {fees.errors.filter(Boolean).map((error, index) => <div className="notice error" role="alert" key={index}>
      费率资料加载失败，缺失费用保持待配置：{error}</div>)}
  </>;
}

type Draft = { rate: string; source: string; conditions: string };
export function FeeProfiles() {
  const [profiles, setProfiles] = useState<FeeProfile[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState("");
  const [venue, setVenue] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true); setError("");
    void api<FeeResponse>("/api/fees").then((data) => {
      if (!current) return;
      const rows = data.profiles ?? [];
      setProfiles(rows);
      setDrafts(Object.fromEntries(rows.map((row) => [row.key, { rate: percentInput(row.override?.rate),
        source: row.override?.source || "用户配置：普通默认费率假设", conditions: row.override?.conditions || "本地计算使用的用户费率假设" }])));
    }).catch((caught) => { if (current) setError((caught as Error).message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [reload]);
  const update = (key: string, field: keyof Draft, value: string) =>
    setDrafts((old) => ({ ...old, [key]: { ...old[key], [field]: value } }));
  const save = async (row: FeeProfile, reset = false) => {
    setError(""); setMessage(""); setSaving(row.key);
    try {
      const draft = drafts[row.key];
      const body = reset ? { venue: row.venue, kind: row.kind, liquidity: row.liquidity, reset: true } : {
        venue: row.venue, kind: row.kind, liquidity: row.liquidity,
        rate: parsePercent(draft.rate, "本地默认覆盖费率"), source: draft.source || "用户配置：普通默认费率假设",
        conditions: draft.conditions || "本地计算使用的用户费率假设", updated_at: Date.now(),
      };
      await api("/api/fees", "PUT", body);
      setMessage(reset ? "已恢复公开默认；公开费率未知时仍保持待配置。" : "本地默认覆盖已保存，来源标记为用户假设。");
      setReload((old) => old + 1);
    } catch (caught) { setError((caught as Error).message); }
    finally { setSaving(""); }
  };
  return <section className="panel" aria-labelledby="fee-profiles-heading" style={{ marginBottom: 20 }}>
    <div className="panel-heading"><div><h2 id="fee-profiles-heading">平台默认手续费</h2>
      <p>普通公开档位与本地计算覆盖。来源、适用条件和更新时间分别保留，不读取账户等级或私密凭证。</p></div>
      <button type="button" className="button secondary" disabled={loading} onClick={() => setReload((old) => old + 1)}>
        {loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}刷新费率资料</button></div>
    <div className="filters"><label>费率平台 <select aria-label="费率平台" value={venue} onChange={(event) => setVenue(event.target.value)}>
      <option value="">全部平台</option>{[...new Set(profiles.map((row) => row.venue))].sort().map((name) => <option key={name}>{name}</option>)}
    </select></label><span className="muted">输入单位为%；留空保存表示明确未知，恢复默认请使用对应按钮。</span></div>
    {error && <div className="notice error" role="alert">费率配置失败：{error}</div>}
    {message && <div className="notice" role="status">{message}</div>}
    {!profiles.length && !loading && <div className="form-content"><p className="muted">尚无费率资料；计算费用保持待配置。</p></div>}
    {!!profiles.length && <div className="table-scroll"><table style={{ minWidth: 1060, tableLayout: "fixed", whiteSpace: "normal" }}>
      <colgroup>{[13, 28, 15, 26, 18].map((width, index) => <col key={index} style={{ width: `${width}%` }} />)}</colgroup>
      <thead><tr><th>平台 / 产品 / 成交方式</th><th>公开默认与有效费率</th><th>本地默认覆盖 / %</th><th>覆盖来源与条件</th><th>保存</th></tr></thead>
      <tbody>{profiles.filter((row) => row.applicable && (!venue || row.venue === venue)).map((row) => {
        const label = `${row.venue} ${kindName[row.kind] ?? row.kind} ${row.liquidity}`;
        const draft = drafts[row.key];
        return <tr key={row.key}><td><strong>{row.venue}</strong><small>{kindName[row.kind] ?? row.kind} · {row.liquidity}</small>
          <small className="muted">{row.override ? "用户覆盖" : "公开默认"}</small></td>
          <td>公开 {pct(row.default.rate)}<small>有效 {pct(row.effective.rate)}</small>
            <small className="muted break">{row.effective.source || "来源未提供"}</small><small className="muted">更新 {time(row.effective.updated_at)}
              {row.effective.checked_at != null && ` · 资料核验 ${time(row.effective.checked_at)}`}</small>
            <small className="muted break">{row.effective.conditions || "适用条件未提供"}</small></td>
          <td><input aria-label={`${label}本地默认费率 / %`} type="number" step="any" min="-10" max="100"
            placeholder={percentInput(row.default.rate) || "未知"} value={draft?.rate ?? ""}
            onChange={(event) => update(row.key, "rate", event.target.value)} /></td>
          <td><div className="field"><input aria-label={`${label}覆盖来源`} value={draft?.source ?? ""} onChange={(event) => update(row.key, "source", event.target.value)} />
            <textarea aria-label={`${label}覆盖条件`} rows={2} value={draft?.conditions ?? ""}
              onChange={(event) => update(row.key, "conditions", event.target.value)} /></div></td>
          <td><div className="field"><button type="button" className="button secondary" disabled={!!saving || loading} onClick={() => void save(row)}>
            <Save size={13} />{saving === row.key ? "保存中…" : "保存覆盖"}</button>
            <button type="button" className="button secondary" disabled={!!saving || loading || !row.override} onClick={() => void save(row, true)}>恢复公开默认</button></div></td></tr>;
      })}</tbody></table></div>}
  </section>;
}
