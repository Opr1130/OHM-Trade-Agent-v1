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
  if(response.status==='NO_EVIDENCE' || !Array.isArray(response.rows) || !response.rows.length) return 'NO_EVIDENCE';
  return response.status==='RECORDED'?'RECORDED':'UNVERIFIED';
}
export function objectSummary(payload, depth=0) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || depth>2) return [];
  return Object.entries(payload).filter(([key,value])=> value===null || ['number','string','boolean'].includes(typeof value))
    .slice(0,24).map(([key,value])=>({key:key.replaceAll('_',' '),value:value===null?'Unverified':String(value)}));
}
export function sanitizeJsonExport(text) {
  if(typeof text!=='string' || text.length>65536) throw new Error('Configuration document exceeds allowed size');
  const data=JSON.parse(text);
  if(!data || typeof data!=='object' || Array.isArray(data)) throw new Error('Configuration must be a JSON object');
  return data;
}
export function csvEscape(value) {
  if(value===null || value===undefined) return '';
  let s=typeof value==='object'?JSON.stringify(value):String(value);
  if (/^[=+@\-\t\r]/.test(s)) s="'"+s;
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
