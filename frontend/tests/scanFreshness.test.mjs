import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const compiled = ts.transpileModule(readFileSync(new URL('../src/scanFreshness.ts', import.meta.url), 'utf8'), {compilerOptions:{module:ts.ModuleKind.ES2022}}).outputText;
const {scanDeadline, scanElapsed} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const at = 1800000000000;
test('oldest still-rankable source expires cached ordering before query TTL; receipt cannot rejuvenate it', () => {
  const data = {as_of:at,stale_seconds:60,groups:[{markets:[{fields:{ask:{rankable:true,source_at:at-59000,received_at:at},bid:{rankable:true,source_at:at}}}]}]};
  assert.equal(scanDeadline(data,3600),at+1000);
  assert.equal(scanElapsed(at,at+61000),61);
});
test('empty/unknown results still require revalidation; query-time invalid fields do not poison deadline', () => {
  assert.equal(scanDeadline({as_of:at,groups:[]},60),at+60000);
  assert.equal(scanDeadline({as_of:at,groups:[{markets:[{fields:{bid:{rankable:false,source_at:at-999999}}}]}]},60),at+60000);
  assert.equal(scanElapsed(at,at-1000),0);
});
