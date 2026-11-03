import test from 'node:test';
import assert from 'node:assert/strict';
import {modelAuthority,evidenceStatus,objectSummary,exportCsv,sanitizeJsonExport,apiRequest,statusLevel,readState} from '../src/api.mjs';
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
test('a failed read, a read failure, empty evidence and recorded evidence are four distinct states',()=>{
 // A failed request is never reported as evidence.
 assert.equal(readState(undefined,'HTTP 503: unavailable'),'UNAVAILABLE');
 // A payload that reports its own read failure is not an empty evidence set.
 for(const status of ['UNAVAILABLE','ERROR','FAILED','READ_FAILED']) assert.equal(readState({status,rows:[]}),'UNAVAILABLE',status);
 // An empty result is still an object: truthiness must not read as RECORDED.
 assert.equal(readState({status:'NO_EVIDENCE',rows:[]}),'NO_EVIDENCE');
 assert.equal(readState({status:'RECORDED',rows:[]}),'NO_EVIDENCE');
 assert.equal(readState({status:'RECORDED',rows:[{a:1}]}),'RECORDED');
 assert.equal(readState({status:'RECORDED'}),'RECORDED');
 assert.equal(readState(undefined,undefined),'NOT LOADED');
 assert.equal(readState(null),'NOT LOADED');
 // "not loaded" must not be rendered as a healthy state.
 assert.notEqual(statusLevel(readState(undefined,undefined)),'good');
});
test('CSV export keeps real numbers as numbers and still neutralises formulas',()=>{
 // Negative and signed numbers must survive as values, not become text.
 assert.equal(exportCsv([{pnl:-12.5}]),'pnl\n-12.5');
 assert.equal(exportCsv([{pnl:'-0.0112'}]),'pnl\n-0.0112');
 assert.equal(exportCsv([{size:'+3e4'}]),'size\n+3e4');
 assert.equal(exportCsv([{n:'0'}]),'n\n0');
 // A leading trigger that is not a number is still neutralised.
 assert.equal(exportCsv([{a:'-1+1'}]),'a\n\'-1+1');
 assert.equal(exportCsv([{a:'@SUM(A1)'}]),'a\n\'@SUM(A1)');
 assert.equal(exportCsv([{a:'=1+1'}]),'a\n\'=1+1');
 // Whitespace or control characters in front of a formula are neutralised too.
 assert.match(exportCsv([{a:'\t=1+1'}]),/^a\n'\t=1\+1$/);
 assert.match(exportCsv([{a:'   @cmd'}]),/^a\n'   @cmd$/);
 assert.match(exportCsv([{a:'\r=cmd'}]),/^a\n"\'\r=cmd"$/);
 // Symbols and free text are preserved verbatim.
 assert.equal(exportCsv([{symbol:'BTC/USD'}]),'symbol\nBTC/USD');
 assert.equal(exportCsv([{note:'profit fell -3.2%'}]),'note\nprofit fell -3.2%');
});

