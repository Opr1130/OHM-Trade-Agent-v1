import test from 'node:test';
import assert from 'node:assert/strict';
import {modelAuthority,evidenceStatus,objectSummary,exportCsv,sanitizeJsonExport,apiRequest,statusLevel} from '../src/api.mjs';
test('V2 authority derives only from recorded evidence',()=>{
 assert.equal(modelAuthority({model_authority:'V2_PRODUCTION_V1_OBSERVATION'}),'V2 production · V1 observation');
 assert.equal(modelAuthority({}), 'UNVERIFIED');
 assert.equal(evidenceStatus({status:'NO_EVIDENCE',rows:[]}), 'NO_EVIDENCE');
 assert.equal(evidenceStatus({status:'RECORDED',rows:[{}]}),'RECORDED');
 assert.equal(evidenceStatus({status:'UNAVAILABLE',rows:[]}), 'UNAVAILABLE');
 assert.equal(evidenceStatus({status:'ERROR',rows:[]}), 'UNAVAILABLE');
});
test('negative operational states never render as healthy',()=>{
 for(const state of ['INACTIVE','UNHEALTHY','NOT_READY','NOT_ACTIVE','UNVERIFIED','NO_EVIDENCE','DEGRADED','BLOCKED']) assert.equal(statusLevel(state),'bad',state);
 for(const state of ['ACTIVE','ACTIVE_VERIFIED','HEALTHY','READY','RECORDED','SUCCESS']) assert.equal(statusLevel(state),'good',state);
 for(const state of ['NOT LOADED','AWAITING EVIDENCE','UNKNOWN_PROVISIONAL','V2 production · V1 observation']) assert.notEqual(statusLevel(state),'good',state);
});
test('null values are explicitly unverified',()=>{assert.deepEqual(objectSummary({qualified:null})[0],{key:'qualified',value:'Unverified'});});
test('nested operational metrics are visible with source paths and bounded depth',()=>{
 const rows=objectSummary({market:{regime:'RISK_OFF'},funnel:{qualified:9},trading:{paper:{outcomes:0}}});
 assert.deepEqual(rows,[{key:'market · regime',value:'RISK_OFF'},{key:'funnel · qualified',value:'9'},{key:'trading · paper · outcomes',value:'0'}]);
});
test('CSV export escapes formula injection, quoted fields and embedded newlines',()=>{
 assert.match(exportCsv([{symbol:'=HYPERLINK(1)'}]), /'=HYPERLINK/);
 assert.equal(exportCsv([{note:'a,"b"'}]), 'note\n"a,""b"""');
 assert.equal(exportCsv([{a:'line\nnext'}]), 'a\n"line\nnext"');
 assert.equal(exportCsv([{a:'\n=HYPERLINK(1)'}]), 'a\n"\'\n=HYPERLINK(1)"');
 assert.equal(exportCsv([{a:'  \n\t+SUM(1,2)'}]), 'a\n"\'  \n\t+SUM(1,2)"');
});
test('JSON import fails closed',()=>{assert.throws(()=>sanitizeJsonExport('[]'));assert.throws(()=>sanitizeJsonExport('{oops}'));assert.deepEqual(sanitizeJsonExport('{"scoring":{}}'),{scoring:{}});});
test('API refuses external URLs and missing viewer auth',async()=>{await assert.rejects(apiRequest('https://example.org',{secret:'abc'}));await assert.rejects(apiRequest('/api/test'));});
