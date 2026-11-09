import test from 'node:test';
import assert from 'node:assert/strict';
import {modelAuthority,evidenceStatus,objectSummary,exportCsv,sanitizeJsonExport,apiRequest,statusLevel,readState,utcWithLocal,qualificationFunnel,rejectionBreakdown,aiOperatingState,configAdminState,scannerCoverage,scannerExclusions,readErrorState,READ_TIMEOUT_CODE,f11Display,kpiCards} from '../src/api.mjs';
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

// --- Round 2: source-backed Control Center reads -----------------------------
test('a scan timestamp is shown in UTC with the local rendering beside it',()=>{
 const stamp=utcWithLocal('2026-11-03T00:30:00Z');
 assert.equal(stamp.utc,'2026-11-03T00:30:00Z');
 assert.ok(stamp.local.length>0);
 assert.deepEqual(utcWithLocal(null),{utc:'Unknown',local:'—'});
});
test('the qualification funnel keeps an unreturned field unverified, never zero',()=>{
 const stages=qualificationFunnel({candidates_analyzed:200,qualified:0});
 assert.deepEqual(stages.map(s=>s.label),[ 'Snapshot population analysed','Technical candidates',
  'Long candidates','Short candidates','Shortlisted (directional)','Qualified',
  'Paper-admission eligible']);
 assert.equal(stages[0].value,200);
 assert.equal(stages[1].value,null);
 assert.equal(stages[5].value,0);
 assert.deepEqual(qualificationFunnel(null),[]);
});
test('the rejection breakdown reports the recorded rejection classes only',()=>{
 const rows=rejectionBreakdown({rejected_by_policy:0,rejected_by_model:3});
 assert.equal(rows.find(r=>r.label==='Rejected by model').value,3);
 assert.equal(rows.find(r=>r.label==='Rejected by policy').value,0);
 assert.equal(rows.find(r=>r.label==='Rejected by budget').value,null);
});
test('AI operating states stay distinct: never reached, no invocation, unavailable, budget, failed, invoked',()=>{
 assert.equal(aiOperatingState(null).state,'NO_EVIDENCE');
 assert.equal(aiOperatingState({ai_stage_reached:false}).state,'NOT_REACHED');
 assert.equal(aiOperatingState({ai_stage_reached:true}).state,'NO_INVOCATION');
 assert.equal(aiOperatingState({ai_stage_reached:true,ai_unavailable:true}).state,'UNAVAILABLE');
 assert.equal(aiOperatingState({ai_stage_reached:true,ai_budget_exhausted:true}).state,'BUDGET_EXHAUSTED');
 assert.equal(aiOperatingState({ai_invoked:true,ai_invocation_status:'TIMEOUT'}).state,'FAILED_INVOCATION');
 const invoked=aiOperatingState({ai_invoked:true,ai_invocation_status:'OK',ai_candidates_returned:2});
 assert.equal(invoked.state,'INVOKED');
 assert.match(invoked.detail,/2 candidate/);
});
test('an unavailable strategy-admin capability is not a credential or an empty form',()=>{
 assert.equal(configAdminState(null).state,'READY');
 assert.equal(configAdminState(new Error('HTTP 404: Strategy administration not enabled')).state,'NOT_AVAILABLE');
 assert.equal(configAdminState(new Error('HTTP 401: Invalid dashboard secret')).state,'CREDENTIAL_REFUSED');
 assert.equal(configAdminState(new Error('HTTP 503: unavailable')).state,'UNAVAILABLE');
});
// A scan that replaced an ineligible candidate is COMPLETE *and* still shows the
// exclusion: recovered completeness never hides what was excluded.
const RECOVERED={coverage:{schema_version:2,requested:200,analyzed:200,attempted:201,skipped:1,
 data_quality_rejected:0,failed:0,unique_assets:200,replacement_attempts:1,remaining_candidate_capacity:0},
 exclusions:{recorded:1,total:1,truncated:false,records:[{pair:'KORUSD',base_asset:'KOR',liquidity_rank:49,
  status:'skip',reason:'KORUSD: insufficient history (39 candles)',stage:'initial'}]}};
test('recovered coverage is complete and the exclusion stays visible',()=>{
 const coverage=scannerCoverage(RECOVERED);
 assert.equal(coverage.state,'COMPLETE');
 assert.equal(coverage.level,'good');
 assert.deepEqual([coverage.target,coverage.valid],[200,200]);
 const excluded=coverage.fields.find(field=>field.key==='Excluded candidates');
 assert.equal(excluded.value,1,'the recorded exclusion is counted, not erased');
 const exclusions=scannerExclusions(RECOVERED);
 assert.equal(exclusions.total,1);
 assert.equal(exclusions.rows[0].pair,'KORUSD');
 assert.match(exclusions.rows[0].reason,/insufficient history/);
 assert.equal(exclusions.rows[0].level,'bad');
});
test('a shortfall is partial, never healthy',()=>{
 const partial={...RECOVERED,coverage:{...RECOVERED.coverage,analyzed:199}};
 const coverage=scannerCoverage(partial);
 assert.equal(coverage.state,'PARTIAL');
 assert.equal(coverage.level,'bad');
 assert.deepEqual([coverage.target,coverage.valid],[200,199]);
 assert.equal(scannerCoverage({}).state,'UNAVAILABLE');
 assert.equal(scannerCoverage(undefined).level,'bad');
 assert.equal(scannerCoverage({coverage:{requested:200,analyzed:200}}).state,'COMPLETE');
 assert.equal(scannerCoverage({coverage:{requested:0,analyzed:0}}).state,'PARTIAL','an empty target is not complete');
 assert.equal(statusLevel('COMPLETE'),'good');
 assert.equal(statusLevel('PARTIAL'),'bad');
});
test('bounded exclusion evidence reports truncation instead of dropping silently',()=>{
 const truncated={exclusions:{recorded:25,total:40,truncated:true,
  records:Array.from({length:25},(_,index)=>({pair:`S${index}USD`,liquidity_rank:index,status:'skip',reason:'insufficient history'}))}};
 const exclusions=scannerExclusions(truncated);
 assert.equal(exclusions.total,40);
 assert.equal(exclusions.recorded,25);
 assert.equal(exclusions.truncated,true);
 const empty=scannerExclusions({});
 assert.deepEqual([empty.total,empty.recorded,empty.truncated,empty.rows.length],[0,0,false,0]);
});
// A read that ran out of budget, a refusal and an absent capability are three
// different answers. The browser's own abort text must never be one of them.
test('read failures are classified, never passed through as browser text',()=>{
 assert.equal(readErrorState({code:READ_TIMEOUT_CODE,message:'READ TIMEOUT after 20000 ms: /api/x'}).state,'TIMEOUT');
 assert.equal(readErrorState(new Error('READ TIMEOUT after 20 ms: /api/x')).state,'TIMEOUT');
 assert.equal(readErrorState(new Error('HTTP 401: Invalid dashboard secret')).state,'CREDENTIAL_REFUSED');
 assert.equal(readErrorState(new Error('HTTP 403: forbidden')).state,'CREDENTIAL_REFUSED');
 assert.equal(readErrorState(new Error('HTTP 404: nope')).state,'NOT_AVAILABLE');
 assert.equal(readErrorState(new Error('HTTP 502: bad gateway')).state,'HTTP_502');
 assert.equal(readErrorState(new Error('Invalid API response')).state,'UNAVAILABLE');
 assert.equal(readErrorState(undefined).state,'UNREAD');
 // None of these states may render as healthy.
 for(const state of ['TIMEOUT','CREDENTIAL_REFUSED','NOT_AVAILABLE','HTTP_502','UNAVAILABLE'])
  assert.equal(statusLevel(state),'bad',state);
});
test('a read that exceeds its budget is a timeout, and a cancelled read stays cancelled',async()=>{
 const original=globalThis.fetch;
 const hang=(path,{signal})=>new Promise((_resolve,reject)=>{
  signal.addEventListener('abort',()=>reject(Object.assign(new Error('signal is aborted without reason'),{name:'AbortError'})),{once:true});
 });
 try{
  globalThis.fetch=hang;
  await assert.rejects(apiRequest('/api/slow',{secret:'s',timeoutMs:20}),error=>{
   assert.equal(error.code,READ_TIMEOUT_CODE);
   assert.equal(error.name,'TimeoutError');
   assert.match(error.message,/READ TIMEOUT after 20 ms/);
   assert.equal(error.path,'/api/slow');
   assert.equal(readErrorState(error).state,'TIMEOUT');
   return true;
  });
  // A superseded read keeps its AbortError identity, so the console can cancel
  // silently instead of reporting a failure that never happened.
  const controller=new AbortController();
  const pending=apiRequest('/api/slow',{secret:'s',signal:controller.signal,timeoutMs:5000});
  controller.abort();
  await assert.rejects(pending,error=>{
   assert.equal(error.name,'AbortError');
   assert.notEqual(error.code,READ_TIMEOUT_CODE);
   return true;
  });
 } finally{globalThis.fetch=original}
});
test('F11 renders from recorded protection evidence, never from a default',()=>{
 assert.equal(f11Display({status:'MEASURED',state:'ARMED',source:'/app/data/opip/f11.json'}).label,'ARMED');
 assert.equal(f11Display({status:'MEASURED',state:'ARMED'}).level,'good');
 assert.equal(f11Display({status:'MEASURED',state:'TRIGGERED_HALT'}).label,'TRIGGERED_HALT');
 assert.equal(f11Display({status:'MEASURED',state:'TRIGGERED_HALT'}).level,'bad');
 assert.equal(f11Display({status:'MEASURED',state:'SOMETHING_ELSE'}).level,'bad');
 assert.equal(f11Display({status:'UNVERIFIED',reason_code:'NO_F11_STATE_ARTIFACT'}).label,'UNVERIFIED');
 assert.equal(f11Display(null).label,'UNVERIFIED');
 assert.notEqual(statusLevel(f11Display(null).label),'good');
});
// A router-ledger count is provider invocation attempts in a bounded window. It
// is not a qualified trade, not a delivered alert, and not an all-time total.
// The figures below are an arbitrary fixture: they are not measured production
// counts and must not be read as evidence of any real ledger total.
test('ledger-derived AI counts are labelled as bounded invocation attempts',()=>{
 const ledgerHealth={projection:{status:'MEASURED',kpi:{status:'NO_EVIDENCE'},
  ai:{status:'MEASURED',metric_subject:'PROVIDER_INVOCATION_ATTEMPTS',metric_scope:'BOUNDED_WINDOW',
      window_limit:500,inferences:12,succeeded:9,failed:3,unclassified:0,matured_outcomes:null,
      models:[{provider:'openai',model:'gpt-5',total:12}],metrics_reason:'NO_MATURED_OUTCOME_EVIDENCE'}}};
 const ledgerCard=kpiCards(ledgerHealth).find(card=>card.id==='ai');
 const labels=ledgerCard.rows.map(row=>row.label);
 assert.ok(labels.includes('Provider invocations (bounded window)'),labels.join(' | '));
 assert.ok(!labels.includes('Eligible AI reviews'));
 const count=ledgerCard.rows.find(row=>row.label==='Provider invocations (bounded window)');
 assert.equal(count.value,'12');
 const split=ledgerCard.rows.find(row=>row.label==='Succeeded / failed');
 assert.equal(split.value,'9 / 3');
 assert.match(ledgerCard.source,/opip_ai_router_usage\.jsonl/);
 assert.match(ledgerCard.source,/bounded window/);
 // The projection path keeps its own honest label and source.
 const projectionCard=kpiCards({projection:{status:'MEASURED',kpi:{status:'MEASURED'},
  ai:{status:'MEASURED',inferences:9,matured_outcomes:0,models:[]}}}).find(card=>card.id==='ai');
 assert.ok(projectionCard.rows.some(row=>row.label==='Eligible AI reviews'));
 assert.match(projectionCard.source,/derived projection/);
});


