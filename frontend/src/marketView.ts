import type { MarketData, Settings } from "./types";

export type ViewContext = Pick<Settings, "enabled_venues" | "demo_mode" | "broad_collection_enabled" | "watch_assets" | "stale_seconds">;
export type VersionedView = MarketData & { server_instance: string; view_seq: number; view_context: ViewContext };
export type ViewToken = { epoch: number; server: string | null; sequence: number };

export function contextKey(context: ViewContext): string {
  return JSON.stringify([ [...new Set(context.enabled_venues)].sort(), context.demo_mode,
    context.broad_collection_enabled, [...new Set(context.watch_assets.map((asset) => asset.toUpperCase()))].sort(),
    context.stale_seconds ]);
}

export function versioned(value: MarketData): value is VersionedView {
  return typeof value.server_instance === "string" && value.server_instance.length > 0 &&
    Number.isSafeInteger(value.view_seq) && value.view_seq! > 0 && !!value.view_context &&
    Array.isArray(value.view_context.enabled_venues) && Array.isArray(value.view_context.watch_assets);
}

/** Orders views across transports. Quote timestamps are never used as view revisions. */
export class MarketViewGuard {
  epoch = 0;
  context: string | null = null;
  current: VersionedView | null = null;
  private retired = new Set<string>();

  configure(settings: ViewContext): boolean {
    const key = contextKey(settings);
    if (key === this.context) return false;
    this.context = key;
    this.epoch++;
    this.current = null;
    return true;
  }

  token(): ViewToken {
    return { epoch: this.epoch, server: this.current?.server_instance ?? null, sequence: this.current?.view_seq ?? 0 };
  }

  matches(value: MarketData, epoch = this.epoch): value is VersionedView {
    return epoch === this.epoch && this.context !== null && versioned(value) && contextKey(value.view_context) === this.context;
  }

  accept(value: MarketData, token: ViewToken, newConnection = false): boolean {
    if (!this.matches(value, token.epoch) || this.retired.has(value.server_instance)) return false;
    if (this.current) {
      if (this.current.server_instance === value.server_instance) {
        if (value.view_seq <= this.current.view_seq) return false;
      } else {
        // Only a fresh connection or a request begun in the current server
        // generation may establish a restarted server's sequence space.
        if (!newConnection && token.server !== this.current.server_instance) return false;
        this.retired.add(this.current.server_instance);
      }
    }
    this.current = value;
    return true;
  }

  isCurrent(value: MarketData): boolean {
    return this.matches(value) && this.current?.server_instance === value.server_instance &&
      this.current.view_seq === value.view_seq;
  }
}

/** Deltas belong to the socket's own baseline, never an HTTP replacement. */
export function applyMarketDelta(previous: VersionedView | null, delta: any): VersionedView | null {
  if (!previous || !versioned(delta) || delta.server_instance !== previous.server_instance ||
      contextKey(delta.view_context) !== contextKey(previous.view_context) ||
      delta.base_view_seq !== previous.view_seq || delta.view_seq <= previous.view_seq ||
      !Array.isArray(delta.instruments) || !Array.isArray(delta.snapshots)) return null;
  const instruments = new Map(previous.instruments.map((item) => [item.id, item]));
  const snapshots = new Map(previous.snapshots.map((item) => [item.instrument_id, item]));
  for (const id of delta.removed_instruments ?? []) instruments.delete(id);
  for (const id of delta.removed_snapshots ?? []) snapshots.delete(id);
  for (const item of delta.instruments) instruments.set(item.id, item);
  for (const item of delta.snapshots) snapshots.set(item.instrument_id, item);
  return { ...previous, mode: delta.mode, server_instance: delta.server_instance, view_seq: delta.view_seq,
    view_context: delta.view_context, instruments: [...instruments.values()], snapshots: [...snapshots.values()] };
}
