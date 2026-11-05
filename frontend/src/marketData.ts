import type { Instrument, Snapshot } from "./types";

type TimeBasis = "source" | "field_received" | "snapshot";
const own = (value: object | undefined, key: string) => Object.prototype.hasOwnProperty.call(value ?? {}, key);
const stamp = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;

function fieldTime(snapshot: Snapshot, field: string, legacySource = false) {
  const fieldReceived = stamp(snapshot.field_received_at?.[field]);
  const received = fieldReceived ?? stamp(snapshot.received_at);
  // Partial field metadata must never borrow a different field's envelope time.
  const legacy = legacySource && !Object.keys(snapshot.field_timestamps ?? {}).length &&
    !Object.keys(snapshot.field_received_at ?? {}).length;
  const source = own(snapshot.field_timestamps, field) ? stamp(snapshot.field_timestamps?.[field]) :
    legacy ? stamp(snapshot.source_at) : null;
  return { source, received, at: received === null ? null : Math.min(source ?? received, received),
    sourceIsField: own(snapshot.field_timestamps, field) && source !== null,
    basis: (source !== null ? "source" : fieldReceived !== null ? "field_received" : "snapshot") as TimeBasis };
}

/** Overview status follows displayed prices, never unrelated funding clocks. */
export function priceOverviewState(snapshot: Snapshot, now: number, staleSeconds: number) {
  const fields = snapshot.bid != null && snapshot.ask != null ? ["bid", "ask"] :
    snapshot.last != null ? ["last"] : snapshot.mark != null ? ["mark"] : snapshot.index != null ? ["index"] : [];
  const times = fields.map((field) => fieldTime(snapshot, field, true));
  const valid = snapshot.valid && snapshot.price_valid !== false && fields.length > 0 &&
    fields.every((field) => typeof snapshot[field as keyof Snapshot] === "number" &&
      Number.isFinite(snapshot[field as keyof Snapshot]) && Number(snapshot[field as keyof Snapshot]) > 0) &&
    !(fields.length === 2 && snapshot.bid! >= snapshot.ask!) &&
    times.every((timing) => timing.received !== null && timing.received <= now &&
      (timing.source === null || timing.source <= timing.received));
  const sourceKnown = fields.length > 0 && times.every((timing) => timing.source !== null);
  const stale = fields.length === 0 || snapshot.stale === true ||
    fields.some((field) => snapshot.field_stale?.[field] === true) ||
    times.some((timing) => timing.at === null || now - timing.at > staleSeconds * 1000 || timing.at > now);
  return { valid, stale, sourceKnown, fresh: valid && sourceKnown && !stale && snapshot.price_fresh !== false };
}

export interface FundingHistorySeries {
  key: string;
  kind: string;
  intervalHours: number | null;
  basis: TimeBasis;
  isDemo: boolean;
  points: { at: number; value: number }[];
}

export function fundingHistorySeries(snapshots: Snapshot[]) {
  const groups = new Map<string, FundingHistorySeries & { byTime: Map<number, { at: number; value: number }> }>();
  let excluded = 0, duplicates = 0;
  for (const snapshot of [...snapshots].sort((left, right) => left.received_at - right.received_at)) {
    const value = snapshot.funding_rate;
    const timing = fieldTime(snapshot, "funding_rate");
    if (value == null || !Number.isFinite(value) || !snapshot.valid || timing.at === null ||
        (timing.source !== null && timing.received !== null && timing.source > timing.received)) {
      excluded++;
      continue;
    }
    const intervalHours = snapshot.funding_interval_hours != null && Number.isFinite(snapshot.funding_interval_hours) && snapshot.funding_interval_hours > 0 ?
      snapshot.funding_interval_hours : null;
    const kind = snapshot.funding_kind ?? "unknown";
    const key = JSON.stringify([snapshot.instrument_id, kind, intervalHours, timing.basis, snapshot.is_demo]);
    const group = groups.get(key) ?? { key, kind, intervalHours, basis: timing.basis,
      isDemo: snapshot.is_demo, points: [], byTime: new Map() };
    // For legacy snapshots there is no evidence that the funding field changed.
    // An unchanged value on a newer price envelope is not another funding event.
    const previous = [...group.byTime.values()].at(-1);
    if (timing.basis === "snapshot" && previous?.value === value) {
      duplicates++;
      continue;
    }
    if (group.byTime.has(timing.at)) duplicates++;
    group.byTime.set(timing.at, { at: timing.at, value });
    groups.set(key, group);
  }
  const series = [...groups.values()].map(({ byTime, ...group }) =>
    ({ ...group, points: [...byTime.values()].sort((left, right) => left.at - right.at) }));
  return { series, excluded, duplicates, pointCount: series.reduce((total, item) => total + item.points.length, 0) };
}

export interface PriceRange {
  key: string;
  quote: string;
  basis: "mid" | "last" | "mark" | "index";
  isDemo: boolean;
  sourceKnown: boolean;
  min: number;
  max: number;
  count: number;
  oldestAt: number;
  newestAt: number;
}

export function marketPriceSummary(instruments: Instrument[], snapshots: Map<string, Snapshot>,
                                   now: number, staleSeconds: number) {
  const groups = new Map<string, PriceRange>();
  const exclusions: Record<string, number> = {};
  let excludedMarkets = 0;
  const reject = (reason: string) => { exclusions[reason] = (exclusions[reason] ?? 0) + 1; };
  for (const instrument of instruments) {
    const snapshot = snapshots.get(instrument.id);
    if (!instrument.active || !snapshot || !snapshot.valid || snapshot.instrument_id !== instrument.id || snapshot.venue !== instrument.venue) {
      reject(!instrument.active ? "inactive" : !snapshot ? "missing" : !snapshot.valid ? "invalid" : "identity");
      excludedMarkets++;
      continue;
    }
    const choices: { basis: PriceRange["basis"]; fields: string[]; value: number | null | undefined }[] = [
      { basis: "mid", fields: ["bid", "ask"], value: snapshot.bid != null && snapshot.ask != null ?
        (snapshot.bid + snapshot.ask) / 2 : null },
      { basis: "last", fields: ["last"], value: snapshot.last },
      { basis: "mark", fields: ["mark"], value: snapshot.mark },
      { basis: "index", fields: ["index"], value: snapshot.index },
    ];
    let accepted = false;
    for (const choice of choices) {
      if (choice.value == null) continue;
      if (!Number.isFinite(choice.value) || choice.value <= 0 || (choice.basis === "mid" &&
          (!(snapshot.bid! > 0) || !(snapshot.ask! > 0) || snapshot.bid! >= snapshot.ask!))) {
        reject("invalid_price");
        continue;
      }
      const times = choice.fields.map((field) => fieldTime(snapshot, field, true));
      if (times.some((timing) => timing.at === null)) { reject("missing_time"); continue; }
      if (times.some((timing) => timing.received! > now || (timing.source !== null && timing.source > Math.min(now, timing.received!)))) {
        reject("future"); continue;
      }
      const legacyStale = snapshot.stale === true && !Object.keys(snapshot.field_timestamps ?? {}).length &&
        !Object.keys(snapshot.field_received_at ?? {}).length;
      if (legacyStale || times.some((timing) => now - timing.at! > staleSeconds * 1000) ||
          choice.fields.some((field) => snapshot.field_stale?.[field] === true)) { reject("stale"); continue; }
      const sourceKnown = times.every((timing) => timing.sourceIsField);
      const at = Math.min(...times.map((timing) => timing.at!));
      const key = JSON.stringify([instrument.quote, choice.basis, snapshot.is_demo, sourceKnown]);
      const group = groups.get(key) ?? { key, quote: instrument.quote, basis: choice.basis,
        isDemo: snapshot.is_demo, sourceKnown, min: choice.value, max: choice.value, count: 0,
        oldestAt: at, newestAt: at };
      group.min = Math.min(group.min, choice.value);
      group.max = Math.max(group.max, choice.value);
      group.count++;
      group.oldestAt = Math.min(group.oldestAt, at);
      group.newestAt = Math.max(group.newestAt, at);
      groups.set(key, group);
      accepted = true;
      break;
    }
    if (!accepted) { excludedMarkets++; if (!choices.some((choice) => choice.value != null)) reject("missing_price"); }
  }
  const ranges = [...groups.values()].sort((left, right) => left.quote.localeCompare(right.quote) ||
    Number(left.isDemo) - Number(right.isDemo) || left.basis.localeCompare(right.basis) || Number(right.sourceKnown) - Number(left.sourceKnown));
  return { ranges, excludedMarkets, exclusions, includedMarkets: ranges.reduce((total, group) => total + group.count, 0) };
}
