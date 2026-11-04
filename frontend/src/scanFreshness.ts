// A cached ranking is a query-time result. Expire it conservatively when any
// previously rankable quote expires; only a server rescan can certify ordering.
export function scanDeadline(data: { as_of: number; stale_seconds?: number; groups: { markets: { fields: Record<string, { rankable: boolean; source_at: number | null }> }[] }[] }, fallbackSeconds: number): number {
  const ttl = (data.stale_seconds ?? fallbackSeconds) * 1000;
  let deadline = data.as_of + ttl;
  for (const group of data.groups) for (const market of group.markets)
    for (const name of ["bid", "ask", "funding_rate", "funding_kind"]) {
      const field = market.fields[name];
      if (field?.rankable && field.source_at != null) deadline = Math.min(deadline, field.source_at + ttl);
    }
  return deadline;
}
export function scanElapsed(asOf: number, now: number): number { return Math.max(0, (now - asOf) / 1000); }
