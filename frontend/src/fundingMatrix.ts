export type FundingValidity = {
  valid: boolean; reason: string | null; source: string | null; source_at: number | null;
  received_at: number | null; age_seconds: number | null; metadata_observation: boolean; ttl_seconds?: number;
  rankable?: boolean;
};
export type FundingMarket = {
  instrument_id: string; venue: string; native_symbol: string; quote: string; base?: string; asset_id?: string;
  identity_group_id?: string; identity_status?: string; identity_blockers?: string[];
  raw_rate: number | null; kind: string | null; interval_hours: number | null;
  normalized_8h_rate: number | null; next_funding_at: number | null;
  field_validity?: Record<string, FundingValidity>; source?: string | null;
  observed?: { funding_rate?: number | null; funding_kind?: string | null;
    funding_interval_hours?: number | null; next_funding_at?: number | null };
  missing: string[]; warnings: string[];
};
export type FundingGroup = {
  id: string; asset_id: string; symbols: string[]; venues: string[]; cells: FundingMarket[];
  spread_8h_rate: number | null; funding_kind: string | null; warnings: string[]; missing: string[];
  identity_status?: string; identity_blockers?: string[]; comparison_ready?: boolean;
};
export type FundingResult = {
  as_of: number; enabled: boolean; rows: FundingMarket[]; groups: FundingGroup[]; matrix_groups?: FundingGroup[];
  venues?: string[]; venue_filter?: string | null; platforms_filter?: string[] | null;
  stats: { perpetual_markets: number; fresh_rates: number; normalized_rates: number; unknown_periods: number;
    unverified_markets: number; rows_total: number; rows_returned: number; groups_total: number;
    comparable_groups: number; rows_truncated: boolean; groups_truncated: boolean; groups_returned: number;
    matrix_groups_total?: number; matrix_groups_returned?: number; matrix_groups_truncated?: boolean };
};
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
function fieldTrusted(row: FundingMarket, field: string, asOf: number) {
  const value = row.field_validity?.[field];
  if (!value?.valid || value.rankable === false || !value.source?.trim() || !finite(value.received_at) ||
      value.received_at <= 0 || value.received_at > asOf) return false;
  if (finite(value.source_at)) return value.source_at > 0 && value.source_at <= asOf;
  return field === "funding_interval_hours" && value.metadata_observation &&
    /:(catalog_metadata_observed_at|public_metadata_observed_at)$/.test(value.source);
}
export function fundingFieldFresh(row: FundingMarket, field: string, asOf: number, now: number) {
  const validity = row.field_validity?.[field];
  if (!fieldTrusted(row, field, asOf) || !finite(validity?.age_seconds) || validity.age_seconds < 0) return false;
  const ttl = validity.ttl_seconds ?? (validity.metadata_observation ? 21600 : 60);
  return validity.age_seconds + Math.max(0, now - asOf) / 1000 <= ttl;
}
export function fundingMarketState(row: FundingMarket | undefined, asOf: number, now: number) {
  if (!row) return { code: "no_market", label: "无市场", tone: "muted" };
  const reasons = [...row.missing, ...Object.values(row.field_validity ?? {}).map((value) => value.reason).filter(Boolean)];
  if (reasons.includes("snapshot_missing")) return { code: "uncollected", label: "未采集", tone: "pending" };
  const rateFresh = fundingFieldFresh(row, "funding_rate", asOf, now);
  const lastRate = row.observed?.funding_rate ?? row.raw_rate;
  if (finite(lastRate) && row.field_validity?.funding_rate?.valid && !fieldTrusted(row, "funding_rate", asOf))
    return { code: "untrusted_time", label: "来源或源事件时间未核验", tone: "pending" };
  if (reasons.some((reason) => reason?.includes("stale") && !reason.startsWith("next_funding_at")) || (finite(lastRate) && !rateFresh &&
      row.field_validity?.funding_rate?.valid)) return { code: "stale", label: "过期", tone: "stale" };
  if (!rateFresh || !finite(row.raw_rate)) return { code: "unavailable", label: "费率未提供或无效", tone: "pending" };
  if (!finite(row.interval_hours) || row.interval_hours <= 0) return { code: "unknown_period", label: "周期未知", tone: "pending" };
  if (!fundingFieldFresh(row, "funding_interval_hours", asOf, now)) return { code: "stale", label: "周期过期", tone: "stale" };
  if (!row.kind || !["current", "predicted", "settled"].includes(row.kind) ||
      !fundingFieldFresh(row, "funding_kind", asOf, now)) return { code: "unknown_kind", label: "类型未知或过期", tone: "pending" };
  if (!finite(row.normalized_8h_rate)) return { code: "unavailable", label: "不可归一化", tone: "pending" };
  return { code: "fresh", label: "有效", tone: "fresh" };
}
export function fundingComparison(group: FundingGroup, venues: string[], asOf: number, now: number) {
  const cells = group.cells.filter((cell) => venues.includes(cell.venue));
  const valid = cells.filter((cell) => fundingMarketState(cell, asOf, now).code === "fresh");
  const none = (reason: string) => ({ spread: null, low: null, high: null, reason });
  if (group.identity_status !== "verified") return none("身份未核验，不计算跨平台费差");
  if (new Set(valid.map((cell) => cell.venue)).size < 2) return none("不足两个平台的有效归一化费率");
  // Exclude a venue from its own high/low comparison, including multiple quotes.
  let best: { spread: number; low: FundingMarket; high: FundingMarket; reason: string | null } | null = null;
  for (const kind of new Set(valid.map((cell) => cell.kind))) {
    const compatible = valid.filter((cell) => cell.kind === kind);
    const byVenue = new Map<string, FundingMarket>();
    for (const cell of compatible) {
      const previous = byVenue.get(cell.venue);
      if (!previous || cell.normalized_8h_rate! > previous.normalized_8h_rate!) byVenue.set(cell.venue, cell);
    }
    const maxima = [...byVenue.values()].sort((a, b) => b.normalized_8h_rate! - a.normalized_8h_rate!);
    for (const low of compatible) {
      const high = maxima.find((cell) => cell.venue !== low.venue);
      if (!high) continue;
      const spread = high.normalized_8h_rate! - low.normalized_8h_rate!;
      if (Number.isFinite(spread) && (best === null || spread > best.spread)) best = { spread, low, high, reason: null };
    }
  }
  return best ?? none("不足两个平台具有相同费率类型的有效数据");
}

/** New matrix groups include isolated identities; the legacy fallback never merges by ticker. */
export function fundingGroups(data: FundingResult) {
  if (data.matrix_groups) return data.matrix_groups;
  const rows = new Map(data.rows.map((row) => [row.instrument_id, row]));
  const groups = data.groups.map((group) => ({ ...group, identity_status: group.identity_status ?? "verified",
    cells: group.cells.map((cell) => ({ ...rows.get(cell.instrument_id), ...cell })) }));
  const covered = new Set(groups.flatMap((group) => group.cells.map((cell) => cell.instrument_id)));
  const isolated = new Map<string, FundingGroup>();
  for (const row of data.rows) {
    if (covered.has(row.instrument_id)) continue;
    const key = row.identity_group_id ?? row.instrument_id;
    const group = isolated.get(key) ?? { id: key, asset_id: row.asset_id ?? row.instrument_id,
      symbols: [row.base ?? row.native_symbol], venues: [], cells: [], spread_8h_rate: null,
      funding_kind: row.kind, missing: [], warnings: [], identity_status: row.identity_status ?? "unverified",
      identity_blockers: row.identity_blockers ?? [] };
    group.cells.push(row);
    if (!group.venues.includes(row.venue)) group.venues.push(row.venue);
    isolated.set(key, group);
  }
  return [...groups, ...isolated.values()];
}
