import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
const compiled = ts.transpileModule(readFileSync(new URL("../src/marketView.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { MarketViewGuard, applyMarketDelta, contextKey } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const scope = (venues = ["okx"]) => ({ enabled_venues: venues, demo_mode: false,
  broad_collection_enabled: true, watch_assets: ["BTC"], stale_seconds: 60 });
const view = (seq, context = scope(), server = "one") => ({ server_instance: server, view_seq: seq,
  view_context: context, mode: "live", instruments: [{ id: "x", venue: "okx" }],
  snapshots: [{ instrument_id: "x", bid: seq, source_at: 123, received_at: 124 }] });
const guard = () => { const value = new MarketViewGuard(); value.configure(scope()); return value; };

test("new HTTP view supersedes pending WS display and rejects later arriving older views", () => {
  const state = guard(), token = state.token();
  const pending = view(2);
  assert.ok(state.accept(pending, token, true));
  assert.ok(state.accept(view(4), token));
  assert.equal(state.isCurrent(pending), false);
  assert.equal(state.accept(view(3), token), false);
  assert.equal(state.accept(view(4), token), false);
  assert.equal(state.current.snapshots[0].source_at, 123);
});

test("WS delta uses its own baseline even when HTTP display is newer", () => {
  const state = guard(), token = state.token();
  const socketBaseline = view(2);
  state.accept(view(4), token);
  const delta = { ...view(5), base_view_seq: 2, instruments: [], snapshots: [], removed_snapshots: ["x"] };
  assert.equal(applyMarketDelta(state.current, delta), null);
  const next = applyMarketDelta(socketBaseline, delta);
  assert.ok(state.accept(next, token));
  assert.equal(state.current.snapshots.length, 0);
  assert.equal(applyMarketDelta(next, delta), null);
});

test("scope switch rejects old generations and wrong filters, including delayed retry", () => {
  const state = guard(), old = state.token();
  state.accept(view(2), old);
  const nextScope = scope(["bybit"]);
  assert.ok(state.configure(nextScope));
  assert.equal(state.current, null);
  assert.equal(state.accept(view(100), old), false);
  assert.equal(state.accept(view(101), state.token()), false);
  assert.ok(state.accept(view(3, nextScope), state.token()));
  assert.equal(state.accept(view(200, nextScope), old), false);
});

test("restart accepts a new sequence space only on a fresh request or connection", () => {
  const state = guard(), initial = state.token();
  state.accept(view(90), initial);
  const request = state.token();
  assert.ok(state.accept(view(1, scope(), "two"), request, true));
  assert.equal(state.accept(view(999), request), false);
  assert.equal(state.accept(view(2, scope(), "three"), request), false);
  assert.ok(state.accept(view(1, scope(), "three"), state.token()));
  assert.equal(state.accept(view(1000, scope(), "two"), state.token(), true), false);
});

test("delta baseline, server and context mismatches require a full resync", () => {
  const previous = view(7);
  for (const patch of [{ base_view_seq: 6 }, { server_instance: "two" },
    { view_context: scope(["bybit"]) }, { view_seq: 7 }]) {
    assert.equal(applyMarketDelta(previous, { ...view(8), base_view_seq: 7, ...patch }), null);
  }
  assert.equal(applyMarketDelta(null, { ...view(8), base_view_seq: 7 }), null);
});

test("equivalent settings do not reset context, invalid view versions never apply", () => {
  const state = guard();
  assert.equal(contextKey(scope()), contextKey({ ...scope(), watch_assets: ["btc", "BTC"] }));
  assert.equal(state.configure(scope()), false);
  for (const row of [{ instruments: [], snapshots: [] }, { ...view(1), view_seq: 0 },
    { ...view(1), view_seq: NaN }, { ...view(1), server_instance: "" }]) {
    assert.equal(state.accept(row, state.token()), false);
  }
});
