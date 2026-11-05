import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const compiled = ts.transpileModule(readFileSync(new URL("../src/bookSizing.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { bookBaseForNotional } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const NOW = 1_800_000_000_000;
const instrument = { id: "fixture", mechanism: "orderbook", kind: "spot", linear: true,
  base: "PEPE", quote: "USDT", settle: "USDT", contract_size: 1 };
const book = { instrument_id: "fixture", bids: [[99, 1]], asks: [[100, 1], [200, 1]],
  quantity_unit: "base", received_at: NOW, source_at: NOW - 1, source: "fixture:book" };

test("quote budget consumes asks across prices instead of dividing by best ask", () => {
  const result = bookBaseForNotional(instrument, book, 200, NOW);
  assert.equal(result.sizeBase, 1.5);
  assert.equal(result.spentQuote, 200);
  assert.equal(result.levelsUsed, 2);
  assert.notEqual(result.sizeBase, 200 / book.asks[0][0]);
});
test("linear contract quantities use the explicit base multiplier", () => {
  const result = bookBaseForNotional({ ...instrument, kind: "perpetual", contract_size: .1 },
    { ...book, quantity_unit: "contracts", asks: [[100, 10], [200, 10]] }, 200, NOW);
  assert.equal(result.sizeBase, 1.5);
});
test("inverse contracts spend fixed quote notional per contract, converting base at each level", () => {
  const result = bookBaseForNotional({ ...instrument, kind: "perpetual", linear: false,
    contract_size: 100, settle: "PEPE" }, { ...book, quantity_unit: "contracts", asks: [[100, 1], [200, 2]] }, 200, NOW);
  assert.equal(result.sizeBase, 1.5);
});
test("no rounding or extrapolation is applied for precision steps or insufficient depth", () => {
  assert.equal(bookBaseForNotional({ ...instrument, amount_precision: 1 }, book, 150, NOW).sizeBase, 1.25);
  assert.throws(() => bookBaseForNotional(instrument, book, 300.01, NOW), /深度不足/);
  assert.throws(() => bookBaseForNotional(instrument, { ...book, asks: [] }, 1, NOW), /深度不足/);
});
test("both source and receipt time reject stale/future books; missing source stays explicitly unknown", () => {
  for (const patch of [{ source_at: NOW + 1 }, { received_at: NOW + 1 },
    { source_at: NOW - 61_000 }, { received_at: NOW - 61_000 }, { source_at: NaN }, { received_at: 0 }])
    assert.throws(() => bookBaseForNotional(instrument, { ...book, ...patch }, 50, NOW), /盘口时间/);
  assert.equal(bookBaseForNotional(instrument, { ...book, source_at: null }, 50, NOW).sourceTimeKnown, false);
});
test("invalid quantities, identity, units, crossed/unsorted books and unsupported inverse payoff fail", () => {
  for (const patch of [{ instrument_id: "other" }, { quantity_unit: "unknown" },
    { asks: [[100, -1]] }, { asks: [[200, 1], [100, 1]] }, { bids: [[101, 1]] }])
    assert.throws(() => bookBaseForNotional(instrument, { ...book, ...patch }, 50, NOW));
  assert.throws(() => bookBaseForNotional({ ...instrument, linear: false }, book, 50, NOW), /反向合约/);
  assert.throws(() => bookBaseForNotional(instrument, { ...book, quantity_unit: "contracts" }, 50, NOW), /合约乘数/);
});
