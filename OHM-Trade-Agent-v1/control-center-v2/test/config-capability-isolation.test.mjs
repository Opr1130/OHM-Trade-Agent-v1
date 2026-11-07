import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const jsx = readFileSync(new URL('../src/main.jsx', import.meta.url), 'utf8');

test('legacy strategy-admin refusal never hides independent configuration registry', () => {
  const failedLegacy = jsx.split("if(availability!=='READY')return")[1]?.split('return <div className="stack">')[0];
  assert.ok(failedLegacy, 'legacy unavailable branch must be explicitly rendered');
  assert.match(failedLegacy, /<RegistryConsole config=\{config\}/,
    'the independent configuration registry must remain visible');
  assert.match(failedLegacy, /Retry legacy administration/,
    'operator must be able to recover after a refused credential');
  assert.match(failedLegacy, /type="password"/,
    'retry must accept a fresh memory-only credential');
  assert.doesNotMatch(failedLegacy, /onClick=\{\(\)=>activate\(/,
    'unavailable legacy APIs cannot expose activate or rollback actions');
});

test('legacy unavailable state describes separate registry staging without implying activation', () => {
  const failedLegacy = jsx.split("if(availability!=='READY')return")[1]?.split('return <div className="stack">')[0];
  assert.match(failedLegacy, /staging is inert/);
  assert.match(failedLegacy, /no activation, rollback, funded order, F11 override or learning control/);
});
