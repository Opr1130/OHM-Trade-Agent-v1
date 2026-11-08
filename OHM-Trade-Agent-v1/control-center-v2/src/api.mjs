import {bytes, isNumber, metric, payloadState} from './format.mjs';

const REQUEST_LIMIT_MS=20000;
// A read that ran out of time is not an empty evidence set, a refusal, or a
// cancelled (superseded) read. The browser reports every abort as the same
// DOMException, whose message ("signal is aborted without reason") tells an
// operator nothing, so the console classifies it itself.
export const READ_TIMEOUT_CODE='READ_TIMEOUT';
export function readErrorState(error){
  const text=String(error?.message||error||'');
  if(!text) return {state:'UNREAD',detail:''};
  if(error?.code===READ_TIMEOUT_CODE||/^READ TIMEOUT\b/.test(text))
    return {state:'TIMEOUT',detail:text};
  const status=text.match(/^HTTP (\d{3})\b/);
  if(status){
    const code=status[1];
    if(code==='401'||code==='403') return {state:'CREDENTIAL_REFUSED',detail:text};
    if(code==='404') return {state:'NOT_AVAILABLE',detail:text};
    return {state:'HTTP_'+code,detail:text};
  }
  return {state:'UNAVAILABLE',detail:text};
}
// Only explicitly verified positive states receive a positive visual treatment.
// Substring matching would misclassify INACTIVE, UNHEALTHY and NOT_READY.
export function statusLevel(value) {
  const status=String(value??'').trim().toUpperCase().replace(/[^A-Z0-9]+/g,'_');
  if (new Set(['UNVERIFIED','UNAVAILABLE','ERROR','BLOCKED','STALE','NO_EVIDENCE','UNKNOWN','FAILED','FAILURE','UNHEALTHY','INACTIVE','NOT_READY','NOT_ACTIVE','NOT_ATTESTED','DEGRADED','DISABLED','PARTIAL','TIMEOUT','CREDENTIAL_REFUSED','NOT_AVAILABLE','UNREAD']).has(status)) return 'bad';
  // A refused read is never neutral: HTTP_401/HTTP_502 and friends are failures.
  if (/^HTTP_\d{3}$/.test(status)) return 'bad';
  if (new Set(['PASS','HEALTHY','RECORDED','SUCCESS','READY','ACTIVE','ACTIVE_VERIFIED','COMPLETE']).has(status)) return 'good';
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
export async function apiRequest(path,{secret='',adminSecret='',method='GET',json,expectedDigest='',signal,timeoutMs=REQUEST_LIMIT_MS}={}) {
  if(!path.startsWith('/api/')) throw new Error('API path must be same-origin /api/');
  if (!secret.trim()) throw new Error('Viewer credential required');
  const budget=Number.isFinite(timeoutMs)&&timeoutMs>0?timeoutMs:REQUEST_LIMIT_MS;
  const controller=new AbortController();
  let timedOut=false;
  const timeout=setTimeout(()=>{timedOut=true;controller.abort()},budget);
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
  }catch(error){
    // Only our own budget converts to a timeout; a caller-cancelled request stays
    // an AbortError so a superseded read is never reported as a failure.
    if(timedOut){
      const failure=new Error(`READ TIMEOUT after ${budget} ms: ${path} did not answer inside the console read budget`);
      failure.name='TimeoutError';
      failure.code=READ_TIMEOUT_CODE;
      failure.path=path;
      failure.timeoutMs=budget;
      throw failure;
    }
    throw error;
  }finally{clearTimeout(timeout);signal?.removeEventListener('abort',abort)}
}

// --- Round 3: Control Center V2 operational surfaces ------------------------
// Every path below is one of the explicit same-origin routes the V2 edge
// allowlists. Nothing here can reach an unlisted endpoint.
export const ENDPOINTS = Object.freeze({
  summary: '/api/analytics/summary',
  intelligence: '/api/analytics/intelligence',
  zero: '/api/opip/zero-trade-explanation',
  comparisons: '/api/opip/scoring-comparison',
  health: '/api/opip/control-center/health',
  signals: '/api/opip/control-center/signals',
  forensics: '/api/opip/control-center/forensics',
  scanner: '/api/opip/control-center/scanner',
  ai: '/api/opip/control-center/ai',
  data: '/api/opip/control-center/data',
  config: '/api/opip/control-center/config',
  configPreview: '/api/opip/control-center/config/preview',
  configStage: '/api/opip/control-center/config/stage',
  ingest: '/api/opip/control-center/maintenance/ingest',
  reconcile: '/api/opip/control-center/maintenance/reconcile',
  retention: '/api/opip/control-center/maintenance/retention',
});

const F11_STATES = ['ARMED', 'TRIGGERED_HALT', 'OVERRIDE_SHADOW'];

/** F11 never renders as healthy unless a recorded state proves it. */
export function f11Display(protection) {
  if (!protection || typeof protection !== 'object')
    return {label: 'UNVERIFIED', level: 'bad', detail: 'No protection evidence was returned'};
  const state = String(protection.state || '').toUpperCase();
  if (protection.status === 'MEASURED' && state === 'ARMED')
    return {label: 'ARMED', level: 'good', detail: protection.source || null};
  if (protection.status === 'MEASURED' && F11_STATES.includes(state))
    return {label: state, level: 'bad', detail: protection.reason_code || protection.source || null};
  if (protection.status === 'MEASURED' && state)
    return {label: `UNKNOWN (${state})`, level: 'bad', detail: 'Unrecognised protection state'};
  return {
    label: 'UNVERIFIED',
    level: 'bad',
    detail: protection.reason_code || 'No persisted protection state artifact',
  };
}

export function scannerHealth(scanner) {
  if (!scanner || typeof scanner !== 'object')
    return {label: 'UNAVAILABLE', level: 'bad', detail: 'No scanner evidence'};
  if (scanner.status !== 'MEASURED')
    return {label: 'UNAVAILABLE', level: 'bad', detail: scanner.reason_code || null};
  const outcome = String(scanner.acquisition_outcome || scanner.state || 'RECORDED').toUpperCase();
  const good = outcome === 'COMPLETED';
  return {
    label: outcome,
    level: good ? 'good' : 'bad',
    detail: scanner.attempt_id
      ? `${scanner.attempt_id} · ${scanner.age_seconds ? Math.round(scanner.age_seconds) + 's ago' : 'age unknown'}`
      : null,
  };
}

// --- Scanner acquisition coverage -------------------------------------------
// Coverage version 2 counts every attempted candidate, including an exclusion a
// deterministic replacement recovered. The operator therefore sees the achieved
// target, the recorded exclusions and their reasons side by side: a scan that
// replaced an ineligible candidate is COMPLETE, and one that could not reach the
// target stays PARTIAL. Nothing here turns a recorded exclusion into a healthy
// label, and a missing coverage document is UNAVAILABLE rather than complete.
const _scannerCount = (value) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const _scannerSum = (...values) => (values.every((value) => value === null) ? null : values.reduce((total, value) => total + (value || 0), 0));

export function scannerCoverage(worker) {
  const coverage = worker && typeof worker === 'object' ? worker.coverage : null;
  if (!coverage || typeof coverage !== 'object') {
    return {
      state: 'UNAVAILABLE',
      level: 'bad',
      detail: 'worker_status carries no coverage document',
      target: null,
      valid: null,
      fields: [],
    };
  }
  const target = _scannerCount(coverage.requested);
  const valid = _scannerCount(coverage.analyzed);
  const excluded = _scannerSum(
    _scannerCount(coverage.skipped),
    _scannerCount(coverage.data_quality_rejected),
    _scannerCount(coverage.failed),
  );
  // A target is only met when it is positive and fully achieved. The schema
  // version is not accepted as a proxy for completeness.
  const complete = target !== null && valid !== null && target > 0 && valid === target;
  return {
    state: complete ? 'COMPLETE' : 'PARTIAL',
    level: complete ? 'good' : 'bad',
    detail: complete
      ? 'target met by distinct eligible assets'
      : 'target not met; inspect the recorded exclusions',
    target,
    valid,
    fields: [
      {key: 'Target valid snapshots', value: target},
      {key: 'Valid snapshots', value: valid},
      {key: 'Candidates attempted', value: _scannerCount(coverage.attempted)},
      {key: 'Excluded candidates', value: excluded},
      {key: 'Replacement attempts', value: _scannerCount(coverage.replacement_attempts)},
      {key: 'Distinct underlying assets', value: _scannerCount(coverage.unique_assets)},
      {key: 'Unused replacement capacity', value: _scannerCount(coverage.remaining_candidate_capacity)},
    ],
  };
}

export function scannerExclusions(worker) {
  const document = worker && typeof worker === 'object' ? worker.exclusions : null;
  const raw = document && typeof document === 'object' && Array.isArray(document.records) ? document.records : [];
  const rows = raw.slice(0, 25).map((record) => ({
    pair: String(record?.pair || 'unidentified pair'),
    rank: _scannerCount(record?.liquidity_rank),
    status: String(record?.status || 'EXCLUDED').toUpperCase(),
    stage: record?.stage ? String(record.stage) : null,
    // The reason is the whole point of the evidence: never replaced by a label.
    reason: String(record?.reason || 'no reason recorded'),
    level: record?.status === 'ok' ? 'good' : 'bad',
  }));
  const total = _scannerCount(document?.total);
  return {
    total: total === null ? rows.length : total,
    recorded: rows.length,
    truncated: document?.truncated === true || (total !== null && total > rows.length),
    rows,
  };
}

export function reconciliationDisplay(reconciliation) {
  if (!reconciliation || typeof reconciliation !== 'object')
    return {label: 'UNVERIFIED', level: 'bad', detail: 'No reconciliation run recorded'};
  const state = String(reconciliation.state || 'UNVERIFIED').toUpperCase();
  if (state === 'RECONCILED')
    return {label: 'RECONCILED', level: 'good', detail: reconciliation.run_id || null};
  if (state === 'DRIFT_DETECTED')
    return {label: 'DRIFT DETECTED', level: 'bad', detail: reconciliation.detail || null};
  return {
    label: state.replaceAll('_', ' '),
    level: 'bad',
    detail: reconciliation.reason_code || reconciliation.detail || null,
  };
}


/** The persistent global header, as data. Order is deliberate. */
export function headerFields(health, {authenticated = false} = {}) {
  const runtime = health?.runtime || {};
  const projection = health?.projection || {};
  const f11 = f11Display(runtime.protection);
  const scanner = scannerHealth(runtime.scanner);
  const reconciliation = reconciliationDisplay(projection.reconciliation || {});
  const learning = runtime.learning || {};
  const environment = runtime.environment || {};
  const release = runtime.release || {};
  const configVersion = runtime.config_version || {};
  const learningMeasured = learning.status === 'MEASURED';
  const dbMeasured = typeof projection.db_bytes === 'number';
  const synced = health?.generated_at_utc ? utcWithLocal(health.generated_at_utc) : null;
  return [
    {
      id: 'environment',
      label: 'Environment',
      value: environment.label || 'UNVERIFIED',
      level: 'neutral',
      detail: environment.release_profile || environment.raw || null,
    },
    {id: 'f11', label: 'F11 safety', value: f11.label, level: f11.level, detail: f11.detail},
    {
      id: 'reconciliation',
      label: 'Reconciliation',
      value: reconciliation.label,
      level: reconciliation.level,
      detail: reconciliation.detail,
    },
    {id: 'scanner', label: 'Scanner heartbeat', value: scanner.label, level: scanner.level, detail: scanner.detail},
    {
      id: 'learning_cpu',
      label: 'Learning CPU',
      value: learningMeasured ? `${learning.cpu_pct}%` : 'UNAVAILABLE',
      level: learningMeasured ? 'neutral' : 'bad',
      detail: learningMeasured ? null : learning.reason_code || null,
    },
    {
      id: 'learning_ram',
      label: 'Learning RAM',
      value:
        learningMeasured && typeof learning.memory_used_mib === 'number'
          ? `${learning.memory_used_mib} / ${learning.memory_total_mib ?? '?'} MiB`
          : 'UNAVAILABLE',
      level: learningMeasured ? 'neutral' : 'bad',
      detail: learningMeasured ? null : learning.reason_code || null,
    },
    {
      id: 'db',
      label: 'Analytics DB',
      value: dbMeasured ? bytes(projection.db_bytes) : 'UNAVAILABLE',
      level: dbMeasured ? 'neutral' : 'bad',
      detail: projection.wal_bytes ? `WAL ${bytes(projection.wal_bytes)}` : null,
    },
    {
      id: 'config',
      label: 'Config version',
      value: configVersion.digest ? String(configVersion.digest).slice(0, 12) : 'UNVERIFIED',
      level: configVersion.status === 'MEASURED' ? 'neutral' : 'bad',
      detail: configVersion.authority || configVersion.reason_code || null,
    },
    {
      id: 'release',
      label: 'Deployed revision',
      value: release.deployed_sha ? String(release.deployed_sha).slice(0, 12) : 'UNAVAILABLE',
      level: release.status === 'MEASURED' ? 'neutral' : 'bad',
      detail: release.source || release.reason_code || null,
    },
    {
      id: 'sync',
      label: 'Last synchronized',
      value: synced ? synced.utc : 'UNVERIFIED',
      level: synced ? 'neutral' : 'bad',
      detail: synced ? `local ${synced.local}` : null,
    },
    {
      id: 'auth',
      label: 'Authentication',
      value: authenticated ? 'AUTHENTICATED' : 'NOT AUTHENTICATED',
      level: authenticated ? 'good' : 'bad',
      detail: authenticated ? 'credential held in this tab only' : null,
    },
  ];
}


function _cardRow(label, value, level = 'neutral') {
  return {label, value, level};
}

/** Tier 1 KPI ribbon, as data. Absence is always explicit. */
export function kpiCards(health) {
  const projection = health?.projection || {};
  const kpi = projection.kpi || {};
  const comparison = projection.comparison || null;
  const ai = projection.ai || {};
  const retention = projection.retention || {};
  const preview = projection.retention_preview || {};
  const reconciliation = reconciliationDisplay(projection.reconciliation || {});
  const v2Status = projection.v2_status || {};
  const measured = projection.status === 'MEASURED' && kpi.status === 'MEASURED';
  const projectionState = measured ? 'MEASURED' : payloadState(projection, []);
  const cards = [];

  cards.push({
    id: 'signals',
    title: 'Live V2 signals',
    status: projectionState,
    rows: [
      _cardRow('Symbols scanned', metric(kpi.symbols_scanned)),
      _cardRow('V2 features ready', metric(kpi.v2_ready), isNumber(kpi.v2_ready) ? 'neutral' : 'bad'),
      _cardRow('V2 directional candidates', metric(kpi.v2_directional), isNumber(kpi.v2_directional) ? 'neutral' : 'bad'),
      _cardRow('Qualified signals', isNumber(kpi.qualified) ? metric(kpi.qualified) : 'UNAVAILABLE', isNumber(kpi.qualified) ? 'neutral' : 'bad'),
      _cardRow('Rejected candidates', isNumber(kpi.rejected) ? metric(kpi.rejected) : 'UNAVAILABLE', isNumber(kpi.rejected) ? 'neutral' : 'bad'),
      _cardRow('V2 not computed', metric(v2Status.NOT_COMPUTED), isNumber(v2Status.NOT_COMPUTED) && v2Status.NOT_COMPUTED > 0 ? 'bad' : 'neutral'),
      _cardRow('Last successful scan', kpi.last_observed_at_utc ? utcWithLocal(kpi.last_observed_at_utc).utc : 'UNVERIFIED'),
    ],
    note: kpi.qualification_reason
      ? `Qualification: ${String(kpi.qualification_reason).replaceAll('_', ' ').toLowerCase()}`
      : null,
    source: 'scanner_snapshots (derived projection)',
    observedAt: kpi.last_observed_at_utc || null,
  });

  const v1Count = Array.isArray(comparison?.v1_top) ? comparison.v1_top.length : null;
  const v2Count = Array.isArray(comparison?.v2_top) ? comparison.v2_top.length : null;
  cards.push({
    id: 'attribution',
    title: 'V1 vs V2 alpha attribution',
    status: comparison ? 'MEASURED' : 'NO_EVIDENCE',
    rows: [
      _cardRow('V1 shortlist', metric(v1Count)),
      _cardRow('V2 shortlist', metric(v2Count)),
      _cardRow('Verified avoided losses', 'OUTCOME NOT MATURED', 'bad'),
      _cardRow('Verified missed opportunities', 'OUTCOME NOT MATURED', 'bad'),
      _cardRow('Agreement rate', 'OUTCOME NOT MATURED', 'bad'),
    ],
    note: 'Directional disagreement alone is not an avoided loss or a missed winner.',
    source: 'scoring_comparisons (derived projection)',
    observedAt: comparison?.observed_at_utc || null,
  });

  const aiMeasured = ai.status === 'MEASURED';
  cards.push({
    id: 'ai',
    title: 'AI agreement rate',
    status: aiMeasured ? 'MEASURED' : 'NO_EVIDENCE',
    rows: [
      _cardRow('Eligible AI reviews', aiMeasured ? metric(ai.inferences) : 'NO EVIDENCE', aiMeasured ? 'neutral' : 'bad'),
      _cardRow('Matured outcomes', aiMeasured ? metric(ai.matured_outcomes) : 'NO EVIDENCE', 'bad'),
      _cardRow('Agreement rate', 'INSUFFICIENT DATA', 'bad'),
      _cardRow(
        'Model',
        aiMeasured && ai.models?.length
          ? `${ai.models[0].model} ${ai.models[0].model_version || ''}`.trim()
          : 'UNVERIFIED',
        aiMeasured && ai.models?.length ? 'neutral' : 'bad',
      ),
    ],
    note: ai.reason_code
      ? String(ai.reason_code).replaceAll('_', ' ').toLowerCase()
      : ai.metrics_reason
        ? String(ai.metrics_reason).replaceAll('_', ' ').toLowerCase()
        : null,
    source: 'ai_inferences (derived projection)',
    observedAt: ai.last_observed_at_utc || null,
  });

  cards.push({
    id: 'performance',
    title: 'Strategy performance',
    status: 'INSUFFICIENT DATA',
    rows: [
      _cardRow('Win rate', 'INSUFFICIENT DATA', 'bad'),
      _cardRow('Average win / loss', 'INSUFFICIENT DATA', 'bad'),
      _cardRow('Expectancy', 'INSUFFICIENT DATA', 'bad'),
      _cardRow('Sample size', 'NO MATURED OUTCOMES', 'bad'),
    ],
    note: 'Realized fills, paper fills and theoretical outcomes are different populations.',
    source: 'outcome evidence not yet projected',
    observedAt: null,
  });

  cards.push({
    id: 'retention',
    title: 'Retention & storage health',
    status: projectionState,
    rows: [
      _cardRow(
        'Analytics DB size',
        typeof projection.db_bytes === 'number' ? bytes(projection.db_bytes) : 'UNAVAILABLE',
        typeof projection.db_bytes === 'number' ? 'neutral' : 'bad',
      ),
      _cardRow('Retention policy', retention.retention_days ? `${retention.retention_days} days` : 'DEFAULT 30 DAYS'),
      _cardRow('Eligible for cleanup', isNumber(preview.eligible_total) ? metric(preview.eligible_total) : 'UNVERIFIED'),
      _cardRow(
        'Protected records',
        isNumber(preview.protected_unverified) || isNumber(preview.protected_locked)
          ? metric((preview.protected_unverified || 0) + (preview.protected_locked || 0))
          : 'UNVERIFIED',
      ),
      _cardRow('Reconciliation', reconciliation.label, reconciliation.level),
    ],
    note: 'Retention deletion is disabled until separately authorized.',
    source: 'retention_policies / retention_cleanup_logs',
    observedAt: projection.generated_at_utc || null,
  });
  return cards;
}


/** Provenance rows for the forensics drawer, naming every unproven field. */
export function provenanceRows(forensics) {
  if (!forensics || typeof forensics !== 'object') return [];
  const signal = forensics.signal || {};
  const comparison = forensics.comparison || {};
  const missing = new Set(forensics.missing_fields || []);
  const row = (label, value, key) => ({
    label,
    value: value === null || value === undefined || value === '' ? 'NOT RECORDED' : String(value),
    missing: key ? missing.has(key) : false,
  });
  return [
    row('Scanner attempt', signal.attempt_id),
    row('Symbol', signal.symbol),
    row('Direction', signal.trade_direction),
    row('Timeframe', signal.timeframe, 'timeframe'),
    row('Technical score version', signal.technical_score_version),
    row('V2 profile version', signal.v2_profile_version, 'v2_profile_version'),
    row('V2 profile digest', signal.v2_profile_digest, 'v2_profile_digest'),
    row('Rejection / exclusion', signal.rejection_reason, 'rejection_reason'),
    row('Canonical source', signal.source_id),
    row('Byte offset', signal.byte_offset, 'byte_offset'),
    row('Line number', signal.line_number, 'line_number'),
    row('Source record hash', signal.record_sha256),
    row('Comparison evidence', comparison.comparison_key || null),
    row('Reconciliation', comparison.record_sha256 ? 'PROVENANCE RECORDED' : 'UNVERIFIED'),
    row('Observed (UTC)', signal.observed_at_utc),
  ];
}

/** Stage outcome classification, so a refusal never looks like a success. */
export function stageOutcome(response) {
  if (!response || typeof response !== 'object')
    return {state: 'UNAVAILABLE', message: 'No staging response'};
  const status = String(response.status || '').toUpperCase();
  if (status === 'STAGED')
    return {
      state: 'STAGED',
      message:
        'Staged without runtime effect. Activation stays with the protected strategy-admin endpoint.',
    };
  if (status === 'STAGE_REFUSED' || status === 'CONFLICT')
    return {state: status, message: 'Refused: the stored draft changed. Reload and review again.'};
  return {state: status || 'UNVERIFIED', message: 'No staging outcome was recorded'};
}


/** How a payload was obtained, so a direct read is never read as reconciled. */
export function provenanceLabel(payload) {
  if (!payload || typeof payload !== 'object') return null;
  if (payload.source === 'CANONICAL_DIRECT') {
    return {
      label: 'CANONICAL (DIRECT)',
      level: 'warn',
      detail:
        payload.detail ||
        'Read directly from canonical evidence. Not yet reconciled through the analytical projection.',
    };
  }
  if (payload.projection_fallback) {
    return {
      label: 'PROJECTION FALLBACK',
      level: 'warn',
      detail: 'The projection had no rows for this surface, so canonical evidence answered instead.',
    };
  }
  return null;
}

/**
 * Why zero BUY signals: the recorded qualification explanation, structured.
 * Nothing is inferred - every field comes from the recorded payload.
 */
export function zeroBuySummary(explanation) {
  if (!explanation || typeof explanation !== 'object') return null;
  const stages = qualificationFunnel(explanation);
  const rejections = rejectionBreakdown(explanation).filter(
    row => typeof row.value === 'number' && row.value > 0
  );
  return {
    status: explanation.status || 'UNRECORDED',
    reason: explanation.reason || null,
    explanation: explanation.explanation || null,
    dominantGate: explanation.dominant_terminal_gate || null,
    deepestStage: explanation.deepest_stage_reached || null,
    scanId: explanation.scan_id || null,
    observedAtUtc: explanation.last_scan_at_utc || null,
    stages,
    measuredStages: stages.filter(row => typeof row.value === 'number'),
    unverifiedStages: stages.filter(row => row.value === null).map(row => row.label),
    rejections,
    qualified: typeof explanation.qualified === 'number' ? explanation.qualified : null,
    paperAdmissionEligible:
      typeof explanation.paper_admission_eligible === 'number'
        ? explanation.paper_admission_eligible
        : null,
  };
}

