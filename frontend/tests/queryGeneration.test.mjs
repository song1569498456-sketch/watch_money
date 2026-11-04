import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/queryGeneration.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const { QueryGeneration } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('late A response cannot overwrite B or a later return to A before effect cleanup', () => {
  const gate = new QueryGeneration();
  gate.update('A', true);
  const first = gate.begin();
  assert.equal(gate.accepts(first), true);
  gate.update('B', true);
  assert.equal(gate.accepts(first), false);
  gate.update('A', true);
  assert.equal(gate.accepts(first), false);
  const returned = gate.begin();
  assert.equal(first.controller.signal.aborted, true);
  assert.equal(gate.accepts(returned), true);
});

test('hiding immediately rejects results and returning requires a fresh generation', () => {
  const gate = new QueryGeneration();
  const originalVersion = gate.update('same query', true);
  const pending = gate.begin();
  gate.update('same query', false);
  assert.equal(gate.accepts(pending), false);
  gate.cancel(pending);
  assert.equal(pending.controller.signal.aborted, true);
  assert.notEqual(gate.update('same query', true), originalVersion);
  assert.equal(gate.accepts(pending), false);
  assert.equal(gate.accepts(gate.begin()), true);
});

test('same-query retry keeps context but excludes its old success/error/finally callbacks', () => {
  const gate = new QueryGeneration();
  gate.update('query + settings context', true, 0);
  const failed = gate.begin();
  gate.update('query + settings context', true, 1);
  const retry = gate.begin();
  assert.equal(retry.context, failed.context);
  assert.notEqual(retry.version, failed.version);
  assert.equal(gate.accepts(failed), false);
  assert.equal(gate.accepts(retry), true);
  // Cleanup of an older request cannot cancel the current retry.
  gate.cancel(failed);
  assert.equal(gate.accepts(retry), true);
  assert.equal(retry.controller.signal.aborted, false);
});

test('changed settings or pending raw search invalidates a cached result before debounce fires', () => {
  const gate = new QueryGeneration();
  gate.update(JSON.stringify({ query: 'PEPE', input: 'PEPE', refreshKey: 1 }), true);
  const old = gate.begin();
  const cachedVersion = old.version;
  gate.update(JSON.stringify({ query: 'PEPE', input: 'BTC', refreshKey: 1 }), true);
  assert.equal(gate.accepts(old), false);
  assert.notEqual(gate.version, cachedVersion);
  gate.update(JSON.stringify({ query: 'PEPE', input: 'PEPE', refreshKey: 2 }), true);
  assert.equal(gate.accepts(old), false);
});
