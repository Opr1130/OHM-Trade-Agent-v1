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
