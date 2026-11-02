import test from 'node:test';
import assert from 'node:assert/strict';
import {modelAuthority,evidenceStatus,objectSummary,exportCsv,sanitizeJsonExport,apiRequest} from '../src/api.mjs';
test('V2 authority derives only from recorded evidence',()=>{
 assert.equal(modelAuthority({model_authority:'V2_PRODUCTION_V1_OBSERVATION'}),'V2 production · V1 observation');
 assert.equal(modelAuthority({}), 'UNVERIFIED');
 assert.equal(evidenceStatus({status:'NO_EVIDENCE',rows:[]}), 'NO_EVIDENCE');
 assert.equal(evidenceStatus({status:'RECORDED',rows:[{}]}),'RECORDED');
});
test('null values are explicitly unverified',()=>{assert.deepEqual(objectSummary({qualified:null})[0],{key:'qualified',value:'Unverified'});});
test('CSV export escapes formula injection and quoted fields',()=>{assert.match(exportCsv([{symbol:'=HYPERLINK(1)',note:'a,"b"'}]), /'=HYPERLINK/);assert.match(exportCsv([{a:'line\\nnext'}]),/line/);});
test('JSON import fails closed',()=>{assert.throws(()=>sanitizeJsonExport('[]'));assert.throws(()=>sanitizeJsonExport('{oops}'));assert.deepEqual(sanitizeJsonExport('{"scoring":{}}'),{scoring:{}});});
test('API refuses external URLs and missing viewer auth',async()=>{await assert.rejects(apiRequest('https://example.org',{secret:'abc'}));await assert.rejects(apiRequest('/api/test'));});
