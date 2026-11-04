const REQUEST_LIMIT_MS=12000;
// Only explicitly verified positive states receive a positive visual treatment.
// Substring matching would misclassify INACTIVE, UNHEALTHY and NOT_READY.
export function statusLevel(value) {
  const status=String(value??'').trim().toUpperCase().replace(/[^A-Z0-9]+/g,'_');
  if (new Set(['UNVERIFIED','UNAVAILABLE','ERROR','BLOCKED','STALE','NO_EVIDENCE','UNKNOWN','FAILED','FAILURE','UNHEALTHY','INACTIVE','NOT_READY','NOT_ACTIVE','NOT_ATTESTED','DEGRADED','DISABLED']).has(status)) return 'bad';
  if (new Set(['PASS','HEALTHY','RECORDED','SUCCESS','READY','ACTIVE','ACTIVE_VERIFIED']).has(status)) return 'good';
  return 'neutral';
}
export function modelAuthority(row) {
  if (!row || typeof row !== 'object') return 'UNKNOWN';
  const authority=String(row.model_authority||'');
  if(authority==='V2_PRODUCTION_V1_OBSERVATION') return 'V2 production · V1 observation';
  if(authority==='V1_PRODUCTION_V2_OBSERVATION') return 'V1 production · V2 observation';
  return 'UNVERIFIED';
}
export function evidenceStatus(response) {
  if(!response || typeof response!=='object') return 'UNAVAILABLE';
  // Explicit read failures are not equivalent to an empty evidence set.
  if(['UNAVAILABLE','ERROR','FAILED','READ_FAILED'].includes(response.status)) return 'UNAVAILABLE';
  if(response.status==='NO_EVIDENCE' || !Array.isArray(response.rows) || !response.rows.length) return 'NO_EVIDENCE';
  return response.status==='RECORDED'?'RECORDED':'UNVERIFIED';
}
export function objectSummary(payload, depth=0) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || depth>2) return [];
  const rows=[];
  function visit(object,prefix,level) {
    for(const [key,value] of Object.entries(object)) {
      if(rows.length>=24) return;
      const label=[...prefix,key.replaceAll('_',' ')];
      if(value===null || ['number','string','boolean'].includes(typeof value))
        rows.push({key:label.join(' · '),value:value===null?'Unverified':String(value)});
      else if(value && typeof value==='object' && !Array.isArray(value) && level<2)
        visit(value,label,level+1);
    }
  }
  visit(payload,[],depth);
  return rows;
}
export function sanitizeJsonExport(text) {
  if(typeof text!=='string' || text.length>65536) throw new Error('Configuration document exceeds allowed size');
  const data=JSON.parse(text);
  if(!data || typeof data!=='object' || Array.isArray(data)) throw new Error('Configuration must be a JSON object');
  return data;
}
export function readState(response, error) {
  // One honest classification for a whole-payload read: a request that failed,
  // a payload that reports its own read failure, an empty evidence set and a
  // recorded payload are four DIFFERENT states. The truthiness of the response
  // object is not one of them, because an empty result is still an object.
  if (error) return 'UNAVAILABLE';
  if (!response || typeof response !== 'object') return 'NOT LOADED';
  const status = String(response.status ?? '').trim().toUpperCase();
  if (['UNAVAILABLE', 'ERROR', 'FAILED', 'READ_FAILED'].includes(status)) return 'UNAVAILABLE';
  if (status === 'NO_EVIDENCE') return 'NO_EVIDENCE';
  if (Array.isArray(response.rows) && !response.rows.length) return 'NO_EVIDENCE';
  return 'RECORDED';
}
// A genuine numeric literal is not a formula. Prefixing it would silently turn
// -12.5 or +3e4 into text and corrupt the exported value, so numbers are exempt
// from the spreadsheet formula guard below.
const NUMERIC_LITERAL = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/u;
export function csvEscape(value) {
  if(value===null || value===undefined) return '';
  let s=typeof value==='object'?JSON.stringify(value):String(value);
  // Spreadsheet importers can ignore leading whitespace/control characters.
  // Prefix the entire cell before quoting so a later formula is still inert.
  if (!NUMERIC_LITERAL.test(s) && (/^[\s\u0000-\u001f\u007f]*[=+@-]/u.test(s) || /^[\t\r\n]/.test(s))) s="'"+s;
  return /[",\n\r]/.test(s)?'"'+s.replaceAll('"','""')+'"':s;
}
export function exportCsv(rows) {
  if(!Array.isArray(rows)||!rows.length) return '';
  const headers=[...new Set(rows.flatMap(row=>Object.keys(row||{})))];
  return [headers.map(csvEscape).join(','),...rows.map(row=>headers.map(key=>csvEscape(row?.[key])).join(','))].join('\n');
}
// --- source-backed read shapes (Round 2) -----------------------------------
// A recorded scan timestamp is UTC, and the reporting window is a UTC day
// (`dashboard_read_model._scope_rows` compares `datetime.now(timezone.utc)`).
// The local rendering is therefore shown BESIDE the UTC value, never instead of
// it, so a 00:30Z scan cannot be read as "yesterday" by a local operator.
export function utcWithLocal(iso){
  if(!iso) return {utc:'Unknown',local:'—'};
  const at=new Date(iso);
  if(Number.isNaN(at.getTime())) return {utc:String(iso),local:'Unparsed'};
  return {utc:at.toISOString().replace('.000Z','Z'),local:at.toLocaleString()};
}
function _count(value){
  return typeof value==='number'&&Number.isFinite(value)?value:null;
}
// Ordered funnel over the fields the zero-trade explanation actually returns.
// A field the server did not send is reported as unverified; it is never shown
// as zero, because zero is a claim about the market and null is not.
export function qualificationFunnel(explanation){
  if(!explanation||typeof explanation!=='object') return [];
  const stage=(label,key)=>({label,value:_count(explanation[key])});
  return [
    stage('Snapshot population analysed','candidates_analyzed'),
    stage('Technical candidates','technical_candidates'),
    stage('Long candidates','long_candidates'),
    stage('Short candidates','short_candidates'),
    stage('Shortlisted (directional)','directional_candidates'),
    stage('Qualified','qualified'),
    stage('Paper-admission eligible','paper_admission_eligible'),
  ];
}
export function rejectionBreakdown(explanation){
  if(!explanation||typeof explanation!=='object') return [];
  const stage=(label,key)=>({label,value:_count(explanation[key])});
  return [
    stage('Rejected by policy','rejected_by_policy'),
    stage('Rejected by budget','rejected_by_budget'),
    stage('Rejected by model','rejected_by_model'),
    stage('Rejected for other reasons','rejected_other'),
    stage('Operational failures','operational_failures'),
    stage('Incomplete evidence','incomplete'),
  ];
}
// Five distinguishable advisory states. "Nothing reached the AI stage" must not
// be displayed as a disabled provider, and an unreachable provider must not be
// displayed as a pipeline failure.
export function aiOperatingState(explanation){
  if(!explanation||typeof explanation!=='object')
    return {state:'NO_EVIDENCE',detail:'No zero-trade explanation was returned'};
  const status=String(explanation.ai_invocation_status||'').toUpperCase();
  if(explanation.ai_invoked===true){
    if(status&&!['OK','SUCCESS','COMPLETED','INVOKED'].includes(status))
      return {state:'FAILED_INVOCATION',
        detail:status+(explanation.ai_failure_type?' · '+explanation.ai_failure_type:'')};
    return {state:'INVOKED',
      detail:'Advisory returned '+(_count(explanation.ai_candidates_returned)??0)+' candidate(s)'};
  }
  if(explanation.ai_budget_exhausted===true)
    return {state:'BUDGET_EXHAUSTED',detail:'The advisory budget for this scan was exhausted'};
  if(explanation.ai_unavailable===true)
    return {state:'UNAVAILABLE',detail:'No advisory provider was reachable for this scan'};
  if(explanation.ai_stage_reached===true)
    return {state:'NO_INVOCATION',detail:'The advisory stage was reached and no request was made'};
  return {state:'NOT_REACHED',detail:'No candidate reached the advisory stage'};
}
// The strategy-administration endpoints answer 404 when the capability is not
// provisioned on the deployment. That is a deployment fact: it must never be
// rendered as an empty form, a credential failure, or "not started yet".
export function configAdminState(error){
  const text=String(error?.message||error||'');
  if(!text) return {state:'READY',detail:''};
  if(/HTTP 404\b/.test(text)) return {state:'NOT_AVAILABLE',detail:text};
  if(/HTTP 401\b|HTTP 403\b/.test(text)) return {state:'CREDENTIAL_REFUSED',detail:text};
  return {state:'UNAVAILABLE',detail:text};
}
export async function apiRequest(path,{secret='',adminSecret='',method='GET',json,expectedDigest='',signal}={}) {
  if(!path.startsWith('/api/')) throw new Error('API path must be same-origin /api/');
  if (!secret.trim()) throw new Error('Viewer credential required');
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),REQUEST_LIMIT_MS);
  const headers={'X-Webhook-Secret':secret,'Accept':'application/json'};
  if(adminSecret) headers['X-Strategy-Admin-Secret']=adminSecret;
  if(expectedDigest) headers['X-Expected-Profile-Sha256']=expectedDigest;
  if(json!==undefined) headers['Content-Type']='application/json';
  if(signal?.aborted) controller.abort();
  const abort=()=>controller.abort(); signal?.addEventListener('abort',abort,{once:true});
  try {
    const response=await fetch(path,{method,headers,body:json===undefined?undefined:JSON.stringify(json),signal:controller.signal,credentials:'same-origin',cache:'no-store'});
    let data;try{data=await response.json()}catch{throw new Error('Invalid API response')}
    if(!response.ok) throw new Error(`HTTP ${response.status}: ${typeof data.detail==='string'?data.detail:'Request refused'}`);
    return data;
  }finally{clearTimeout(timeout);signal?.removeEventListener('abort',abort)}
}
