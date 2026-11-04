import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
const source = readFileSync(new URL("../src/fundingMatrix.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { fundingMarketState, fundingComparison, fundingFieldFresh, fundingGroups } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const NOW = 1_800_000_000_000;
const validity = (patch = {}) => ({ valid: true, reason: null, source: "synthetic", source_at: NOW,
  received_at: NOW, age_seconds: 0, metadata_observation: false, ttl_seconds: 300, ...patch });
const market = (id, venue, value = .0001, patch = {}) => ({ instrument_id: id, venue, native_symbol: "SMALL/USDT:USDT",
  raw_rate: value, interval_hours: 8, normalized_8h_rate: value, kind: "current", quote: "USDT", base: "SMALL",
  identity_status: "verified", missing: [], warnings: [], field_validity: Object.fromEntries(
    ["funding_rate", "funding_kind", "funding_interval_hours", "next_funding_at"].map((key) => [key, validity()])), ...patch });
const group = (cells, patch = {}) => ({ id: "identity:small", identity_status: "verified", asset_id: "SMALL",
  symbols: ["SMALL"], cells, venues: [...new Set(cells.map((row) => row.venue))], missing: [], warnings: [], ...patch });

test("8h spread keeps original periods and identifies the actual low/high platforms", () => {
  const a = market("a", "alpha", .00002, { interval_hours: 8 });
  const b = market("b", "beta", .00001, { interval_hours: 1, normalized_8h_rate: .00008 });
  const result = fundingComparison(group([a, b]), ["alpha", "beta"], NOW, NOW);
  assert.ok(Math.abs(result.spread - .00006) < 1e-12);
  assert.equal(result.low.instrument_id, "a"); assert.equal(result.high.instrument_id, "b");
  assert.equal(b.interval_hours, 1); assert.equal(b.raw_rate, .00001);
});
test("multiple markets on one venue never produce a same-venue maximum", () => {
  const result = fundingComparison(group([market("low", "alpha", -.01), market("high", "alpha", .01),
    market("other", "beta", 0)]), ["alpha", "beta"], NOW, NOW);
  assert.equal(result.spread, .01); assert.notEqual(result.low.venue, result.high.venue);
});
test("no market, uncollected, stale and unknown period remain distinct states", () => {
  assert.equal(fundingMarketState(undefined, NOW, NOW).code, "no_market");
  assert.equal(fundingMarketState(market("a", "alpha", null, { missing: ["snapshot_missing"] }), NOW, NOW).code, "uncollected");
  assert.equal(fundingMarketState(market("a", "alpha", .01), NOW, NOW + 301000).code, "stale");
  assert.equal(fundingMarketState(market("a", "alpha", .01, { interval_hours: null, normalized_8h_rate: null }), NOW, NOW).code, "unknown_period");
});
test("custom source TTL and metadata TTL are honored without moving source timestamps", () => {
  const row = market("a", "alpha", 0);
  row.field_validity.funding_interval_hours = validity({ age_seconds: 3600, source_at: null,
    ttl_seconds: 21600, metadata_observation: true });
  const before = JSON.stringify(row);
  assert.ok(fundingFieldFresh(row, "funding_rate", NOW, NOW + 61000));
  assert.ok(fundingFieldFresh(row, "funding_interval_hours", NOW, NOW + 500000));
  assert.equal(fundingFieldFresh(row, "funding_rate", NOW, NOW + 301000), false);
  assert.equal(fundingFieldFresh(row, "funding_interval_hours", NOW, NOW + 18001000), false);
  assert.equal(JSON.stringify(row), before);
});
test("zero is a real comparable rate; unverified identity or mixed types prevent spread", () => {
  const cells = [market("a", "alpha", 0), market("b", "beta", 0)];
  assert.equal(fundingComparison(group(cells), ["alpha", "beta"], NOW, NOW).spread, 0);
  assert.equal(fundingComparison(group(cells, { identity_status: "unverified" }), ["alpha", "beta"], NOW, NOW).spread, null);
  cells[1].kind = "predicted";
  assert.equal(fundingComparison(group(cells), ["alpha", "beta"], NOW, NOW).spread, null);
});
test("excluded platforms cannot influence spread and stale data expires on revisit", () => {
  const cells = [market("a", "alpha", 0), market("b", "beta", .0001), market("c", "gamma", .9)];
  assert.equal(fundingComparison(group(cells), ["alpha", "beta"], NOW, NOW).spread, .0001);
  assert.equal(fundingComparison(group(cells), ["alpha", "beta"], NOW, NOW + 301000).spread, null);
});
test("legacy fallback preserves single-platform and same-ticker unverified identities separately", () => {
  const a = market("a", "alpha", .01, { identity_status: "unverified", asset_id: "SAME" });
  const b = market("b", "beta", .02, { identity_status: "unverified", asset_id: "SAME" });
  const rows = fundingGroups({ rows: [a, b], groups: [] });
  assert.equal(rows.length, 2); assert.equal(rows[0].cells.length, 1);
  assert.equal(rows[0].identity_status, "unverified");
});
