import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// Use the project's existing TS compiler, so these pure-function tests also run
// on Node versions without experimental TypeScript module loading.
const source = readFileSync(new URL("../src/marketData.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022,
  target: ts.ScriptTarget.ES2022 } }).outputText;
const { fundingHistorySeries, marketPriceSummary, priceOverviewState } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const NOW = 1_800_000_000_000;
const snapshot = (patch = {}) => ({ instrument_id: "one", venue: "okx", source: "fixture",
  source_at: NOW, received_at: NOW, field_sources: {}, valid: true, issues: [], is_demo: false, ...patch });
const instrument = (id = "one", patch = {}) => ({ id, venue: "okx", active: true,
  quote: "USDT", asset_id: "asset:fixture", kind: "spot", ...patch });
const summary = (items, values, now = NOW) => marketPriceSummary(items, new Map(
  values.map((value) => [value.instrument_id, value])), now, 60);

test("price-only history updates cannot move or duplicate an old funding event", () => {
  const old = NOW - 120_000;
  const base = snapshot({ funding_rate: .001, funding_kind: "current", funding_interval_hours: 1,
    field_timestamps: { funding_rate: old }, field_received_at: { funding_rate: old + 50 } });
  const result = fundingHistorySeries([base, { ...base, source_at: NOW + 5000, received_at: NOW + 5000 }]);
  assert.equal(result.pointCount, 1);
  assert.equal(result.duplicates, 1);
  assert.deepEqual(result.series[0].points, [{ at: old, value: .001 }]);
  assert.equal(result.series[0].basis, "source");
});

test("null funding source time uses its independent field receipt, not newer price envelope", () => {
  const received = NOW - 90_000;
  const base = snapshot({ funding_rate: 0, field_timestamps: { funding_rate: null },
    field_received_at: { funding_rate: received } });
  const result = fundingHistorySeries([base, { ...base, received_at: NOW + 10_000 }]);
  assert.equal(result.pointCount, 1);
  assert.equal(result.series[0].basis, "field_received");
  assert.equal(result.series[0].points[0].at, received);
  assert.equal(result.series[0].points[0].value, 0);
});

test("legacy unchanged local snapshots are collapsed without claiming a funding event", () => {
  const result = fundingHistorySeries([
    snapshot({ funding_rate: -.001, source_at: NOW - 9999 }),
    snapshot({ funding_rate: -.001, source_at: NOW + 1000, received_at: NOW + 1000 }),
    snapshot({ funding_rate: -.002, source_at: NOW + 2000, received_at: NOW + 2000 }),
  ]);
  assert.equal(result.pointCount, 2);
  assert.equal(result.series[0].basis, "snapshot");
  assert.deepEqual(result.series[0].points.map((point) => point.at), [NOW, NOW + 2000]);
});

test("current, predicted, settled, local observations, periods and demo stay separate", () => {
  const items = ["current", "predicted", "settled"].map((kind) => snapshot({ funding_rate: .001,
    funding_kind: kind, funding_interval_hours: 1, field_timestamps: { funding_rate: NOW } }));
  items.push(snapshot({ funding_rate: .001, funding_kind: "current", funding_interval_hours: 8,
    field_timestamps: { funding_rate: NOW } }));
  items.push(snapshot({ funding_rate: .001, funding_kind: "current", funding_interval_hours: 1 }));
  items.push({ ...items[0], is_demo: true });
  const result = fundingHistorySeries(items);
  assert.equal(result.series.length, 6);
  assert.ok(result.series.every((series) => series.points.length === 1));
});

test("invalid, nonfinite and future-source funding records never become chart points", () => {
  const result = fundingHistorySeries([snapshot({ funding_rate: 0 }),
    snapshot({ funding_rate: Infinity }), snapshot({ funding_rate: .01, valid: false }),
    snapshot({ funding_rate: .01, field_timestamps: { funding_rate: NOW + 1 } })]);
  assert.equal(result.pointCount, 1);
  assert.equal(result.excluded, 3);
});

test("stale bid/ask cannot enter a fresh mid range after a last-price update", () => {
  const result = summary([instrument()], [snapshot({ bid: 50, ask: 52, mid: 51, last: 100,
    field_timestamps: { bid: NOW - 120_000, ask: NOW - 120_000, last: NOW },
    field_received_at: { bid: NOW - 120_000, ask: NOW - 120_000, last: NOW } })]);
  assert.equal(result.ranges.length, 1);
  assert.equal(result.ranges[0].basis, "last");
  assert.equal(result.ranges[0].min, 100);
  assert.equal(result.exclusions.stale, 1);
});

test("quote currencies and mid/last/mark/index bases are never combined", () => {
  const definitions = [
    ["a", "USD", { last: 100 }], ["b", "USDT", { last: 1000 }], ["c", "KRW", { last: 100000 }],
    ["d", "USDT", { bid: 10, ask: 12 }], ["e", "USDT", { mark: 500 }], ["f", "USDT", { index: 600 }],
  ];
  const result = summary(definitions.map(([id, quote]) => instrument(id, { quote })),
    definitions.map(([id, , values]) => snapshot({ instrument_id: id, ...values })));
  assert.equal(result.ranges.length, 6);
  assert.ok(result.ranges.every((range) => range.min === range.max && range.count === 1));
});

test("invalid, stale and missing markets remain excluded with visible counts", () => {
  const result = summary([instrument("a"), instrument("b"), instrument("c"), instrument("d")], [
    snapshot({ instrument_id: "a", last: 100 }), snapshot({ instrument_id: "b", last: 1, valid: false }),
    snapshot({ instrument_id: "c", last: 9999, source_at: NOW - 120_000, received_at: NOW }),
  ]);
  assert.equal(result.includedMarkets, 1);
  assert.equal(result.excludedMarkets, 3);
  assert.deepEqual(result.exclusions, { invalid: 1, stale: 1, missing: 1 });
  assert.equal(result.ranges[0].max, 100);
});

test("live, demo and unknown source time get distinct reference ranges", () => {
  const result = summary([instrument("a"), instrument("b"), instrument("c")], [
    snapshot({ instrument_id: "a", last: 100, field_timestamps: { last: NOW } }),
    snapshot({ instrument_id: "b", last: 9999, is_demo: true, field_timestamps: { last: NOW } }),
    snapshot({ instrument_id: "c", last: 1, field_timestamps: { last: null }, field_received_at: { last: NOW } }),
  ]);
  assert.equal(result.ranges.length, 3);
  assert.ok(result.ranges.every((range) => range.min === range.max));
  assert.equal(result.ranges.filter((range) => !range.sourceKnown).length, 1);
  assert.equal(result.ranges.filter((range) => range.isDemo).length, 1);
});

test("a future source timestamp is excluded even when local receipt would clamp it to now", () => {
  const result = summary([instrument()], [snapshot({ last: 99,
    field_timestamps: { last: NOW + 1 }, field_received_at: { last: NOW } })]);
  assert.equal(result.ranges.length, 0);
  assert.equal(result.exclusions.future, 1);
});

test("one stale side or explicit stale field excludes a midpoint; clock ticks recompute freshness", () => {
  const result = summary([instrument()], [snapshot({ bid: 99, ask: 101,
    field_timestamps: { bid: NOW - 61_000, ask: NOW } })]);
  assert.equal(result.ranges.length, 0);
  const fresh = snapshot({ last: 100 });
  assert.equal(summary([instrument()], [fresh]).includedMarkets, 1);
  assert.equal(summary([instrument()], [fresh], NOW + 61_000).includedMarkets, 0);
  assert.equal(summary([instrument()], [snapshot({ last: 100, field_stale: { last: true } })]).includedMarkets, 0);
  assert.equal(summary([instrument()], [snapshot({ last: 100, stale: true })]).includedMarkets, 0);
});

test("partial field metadata never borrows a new envelope time for another field", () => {
  const result = summary([instrument()], [snapshot({ last: 100, source_at: NOW,
    field_timestamps: { funding_rate: NOW - 1000 }, field_received_at: { last: NOW - 5000 } })]);
  assert.equal(result.ranges[0].sourceKnown, false);
  assert.equal(result.ranges[0].oldestAt, NOW - 5000);
  assert.equal(summary([instrument()], [snapshot({ last: 100 })]).ranges[0].sourceKnown, false);
});


test("overview never turns a future-at-arrival price green after time catches up", () => {
  const row = snapshot({ bid: 100, ask: 101, stale: false,
    field_timestamps: { bid: NOW + 100, ask: NOW + 100 },
    field_received_at: { bid: NOW, ask: NOW } });
  const state = priceOverviewState(row, NOW + 1000, 60);
  assert.equal(state.fresh, false);
  assert.equal(state.valid, false);
  assert.equal(summary([instrument()], [row], NOW + 1000).includedMarkets, 0);
});

test("overview preserves field granularity and ages without another response", () => {
  const row = snapshot({ bid: 100, ask: 101, funding_rate: .001,
    field_timestamps: { bid: NOW, ask: NOW, funding_rate: NOW - 120000 },
    field_received_at: { bid: NOW, ask: NOW, funding_rate: NOW - 120000 },
    field_stale: { funding_rate: true } });
  assert.equal(priceOverviewState(row, NOW, 60).fresh, true);
  assert.equal(priceOverviewState(row, NOW + 61000, 60).fresh, false);
  assert.equal(priceOverviewState({ ...row, field_stale: { bid: true } }, NOW, 60).fresh, false);
  assert.equal(priceOverviewState({ ...row, price_valid: false }, NOW, 60).fresh, false);
});

test("overview missing or unknown price timing is never green", () => {
  for (const row of [snapshot(), snapshot({ bid: 100 }), snapshot({ last: 100, source_at: null }),
    snapshot({ last: 100, source_at: NOW, field_received_at: { last: NOW } })]) {
    assert.equal(priceOverviewState(row, NOW, 60).fresh, false);
  }
});
