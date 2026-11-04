import React,{useEffect,useRef,useState} from 'react';
import{createRoot}from'react-dom/client';
import{Activity,ArrowDownToLine,BarChart3,BrainCircuit,ChartNoAxesCombined,Check,ChevronRight,CircleAlert,ClipboardList,Database,GitCompareArrows,LockKeyhole,RefreshCw,RotateCcw,Search,Settings2,ShieldCheck,SlidersHorizontal}from'lucide-react';
import{apiRequest,modelAuthority,evidenceStatus,objectSummary,exportCsv,sanitizeJsonExport,statusLevel,readState,utcWithLocal,qualificationFunnel,rejectionBreakdown,aiOperatingState,configAdminState}from'./api.mjs';
import './style.css';
const NAV=[
 ['Overview',Activity],['V1 / V2 Evidence',GitCompareArrows],['Qualification',SlidersHorizontal],
 ['Intelligence',BrainCircuit],['Operations',Database],['Config Admin',Settings2]
];
const READS={
 summary:'/api/analytics/summary?scope=all',
 intelligence:'/api/analytics/intelligence?scope=all',
 zero:'/api/opip/zero-trade-explanation?include_candidates=true',
 comparisons:'/api/opip/scoring-comparison'
};
function download(text,filename,type){
 const blob=new Blob([text],{type});const url=URL.createObjectURL(blob);
 const a=document.createElement('a');a.href=url;a.download=filename;a.click();URL.revokeObjectURL(url);
}
function Status({value}){return <span className={'status '+statusLevel(value)}>{value}</span>}
function Formatted({value}){return <span>{value===null||value===undefined?'Not verified':typeof value==='object'?JSON.stringify(value):String(value)}</span>}
function Pairs({data}){const rows=objectSummary(data);return rows.length?<div className="pairs">{rows.map(row=><div className="pair" key={row.key}><span>{row.key}</span><strong>{row.value}</strong></div>)}</div>:<p className="muted">No verified summary fields available</p>}
function Inspection({object}){return <pre className="json">{JSON.stringify(object,null,2)}</pre>}
function EvidenceTable({response,onSelect}) {
 const rows=response?.rows||[];
 if(!rows.length)return <Empty title="No paired comparison evidence" detail="The API did not return a recorded V1/V2 comparison. This is not evidence of a disabled model or zero opportunity."/>;
 return <div className="table-scroll"><table><thead><tr><th>Scan</th><th>Observed (UTC)</th><th>Authority</th><th>V1 shortlist</th><th>V2 shortlist</th><th>Detail</th></tr></thead><tbody>{rows.map((r,i)=><tr key={String(r.scan_id||i)}><td>{String(r.scan_id||'Unverified')}</td><td>{String(r.observed_at_utc||'Unknown')}</td><td><Status value={modelAuthority(r)}/></td><td className="numeric">{Array.isArray(r.v1_top)?r.v1_top.length:'—'}</td><td className="numeric">{Array.isArray(r.v2_top)?r.v2_top.length:'—'}</td><td><button className="subtle" onClick={()=>onSelect(r)}>Inspect <ChevronRight size={15}/></button></td></tr>)}</tbody></table></div>
}
function Empty({title,detail}){return <div className="empty"><CircleAlert size={21}/><strong>{title}</strong><p>{detail}</p></div>}
function Card({title,children,action}){return <section className="card"><div className="card-header"><h3>{title}</h3>{action}</div>{children}</section>}
// Ordered, source-backed funnel. A field the server did not send renders as
// 'Not verified' rather than 0: zero is a claim about the market.
function Funnel({stages,empty}){if(!stages.length)return <p className="muted">{empty}</p>;return <div className="pairs">{stages.map(stage=><div className="pair" key={stage.label}><span>{stage.label}</span><strong>{stage.value===null?'Not verified':String(stage.value)}</strong></div>)}</div>}
function ConfigAdmin({secret}){
 const[adminSecret,setAdminSecret]=useState('');
 const[activationSecret,setActivationSecret]=useState('');
 const[draft,setDraft]=useState(null);
 const[active,setActive]=useState(null);
 const[document,setDocument]=useState('');
 const[ack,setAck]=useState('');
 const[reason,setReason]=useState('');
 const[message,setMessage]=useState('');
 const[messageError,setMessageError]=useState(false);
 const[busy,setBusy]=useState(false);
 const[diff,setDiff]=useState([]);
 // Round 2: the strategy-administration endpoints answer 404 when the
 // capability is not provisioned. That is classified once, here, so the
 // console cannot present an unusable form as if it were ready.
 const[availability,setAvailability]=useState('READY');
 // `ok` and `fail` are the only two ways a message may be produced, so a refused
 // or failed operation can never be rendered in the confirmation style.
 const ok=text=>{setMessage(text);setMessageError(false)};
 const fail=text=>{setMessage(text);setMessageError(true)};
 const authGeneration=useRef(0);
 const baseHeaders={secret,adminSecret};
 // A different viewer must never inherit the previous viewer's admin authority.
 useEffect(()=>{setAdminSecret('');setActivationSecret('');setAck('');setReason('')},[secret]);
 useEffect(()=>{authGeneration.current+=1;setDraft(null);setActive(null);setDocument('');setMessage('');setMessageError(false);setDiff([]);setBusy(false)},[secret,adminSecret]);
 async function load(){const generation=++authGeneration.current;setBusy(true);setMessage('');try{
  const [d,a]=await Promise.all([
   apiRequest('/api/opip/strategy-admin/draft',baseHeaders),
   apiRequest('/api/opip/strategy-admin/active',baseHeaders)
  ]);if(generation!==authGeneration.current)return;
  setDraft(d);setActive(a);setDocument(JSON.stringify(d.document,null,2));setAvailability('READY');ok('Loaded server-side advisory configuration.');setDiff([]);
 }catch(e){if(generation===authGeneration.current){const state=configAdminState(e);setAvailability(state.state);fail(state.detail)}}finally{if(generation===authGeneration.current)setBusy(false)}}
 function changes(next){
  if(!draft?.document)return[];
  const old=draft.document,results=[];
  const isObj=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  for(const cat of new Set([...Object.keys(old),...Object.keys(next)])){
   const before=old[cat],after=next[cat];
   if(!isObj(before)||!isObj(after)){
    if(JSON.stringify(before)!==JSON.stringify(after))results.push({path:cat,before,after});
    continue;
   }
   for(const key of new Set([...Object.keys(before),...Object.keys(after)])){
    if(JSON.stringify(before[key])!==JSON.stringify(after[key]))
     results.push({path:cat+'.'+key,before:before[key],after:after[key]});
   }
  }
  return results;
 }
 function validate(){try{const next=sanitizeJsonExport(document);const changeset=changes(next);if(!changeset.length)throw Error('No changed fields to stage');setDiff(changeset);ok('Review the changes below. The server enforces protected-field validation.');return next}catch(e){fail(e.message);setDiff([]);return null}}
 async function save(){const next=validate();if(!next||!reason.trim())return fail('A non-empty change reason is required before staging.');
  const generation=authGeneration.current;
  setBusy(true);
  try{
   const saved=await apiRequest('/api/opip/strategy-admin/draft',{...baseHeaders,method:'PUT',expectedDigest:draft.digest,json:next});
   if(generation!==authGeneration.current)return;
   ok('Draft staged without runtime activation. Reason (local only): '+reason);
   setDraft({...draft,digest:saved.digest,document:next});setDiff([]);
  }catch(e){if(generation===authGeneration.current)fail(e.message)}
  finally{if(generation===authGeneration.current)setBusy(false)}
 }
 async function activate(rollback=false){if(!draft||!active)return fail('Load configuration first');
  const exact=rollback?'ROLLBACK_ADVISORY_ONLY':'ACTIVATE_ADVISORY_ONLY';
  if(ack!==exact)return fail('Type the exact advisory acknowledgment before proceeding.');
  if(!activationSecret.trim())return fail('Separate activation credential required.');
  // Activation secret is not accepted by the legacy backend: the configured admin secret
  // is the server's second factor; this field is deliberately a local confirmation only.
  if(activationSecret!==adminSecret)return fail('Activation confirmation must match the protected admin credential.');
  const generation=authGeneration.current;
  setBusy(true);
  try{
   const result=await apiRequest('/api/opip/strategy-admin/'+(rollback?'rollback':'activate'),{...baseHeaders,method:'POST',json:{expected_digest:rollback?active.digest:draft.digest,acknowledgment:exact}});
   if(generation!==authGeneration.current)return;
   ok(result.status+' — funded trading not activated.');setAck('');setActivationSecret('');
   await load();
  }catch(e){if(generation===authGeneration.current)fail(e.message)}
  finally{if(generation===authGeneration.current)setBusy(false)}
 }
 if(availability!=='READY')return <div className="stack">
 <Card title="Configuration administration unavailable">
 <Empty title="Strategy administration is not enabled on this deployment"
 detail={availability==='NOT_AVAILABLE'
 ?'The backend answered HTTP 404 for /api/opip/strategy-admin/*, so this release has no draft, activation or rollback capability to offer.'
 :'The backend refused the configuration read ('+availability+'): '+(message||'no detail')}/>
 <p className="muted">Nothing here is hidden by the console: the capability is absent server-side. No credential is requested, no write is attempted, and no protected functionality is activated.</p>
 </Card>
 {message&&<p className="error notice-error" role="alert">{message}</p>}
 </div>;
 return <div className="stack">
 <Card title="Protected advisory configuration"><p className="muted">Existing O’Pip backend controls runtime validation and authorized advisory-only activation. This console cannot alter funded trading, F11, or hard-coded provider secrets.</p>
 <div className="input-row"><label>Strategy Admin secret<input type="password" autoComplete="off" value={adminSecret} onChange={e=>setAdminSecret(e.target.value)} placeholder="Admin credential (memory only)"/></label><button onClick={load} disabled={busy||!secret||!adminSecret}>Load Active & Draft</button></div>
 {active&&<div className="inline-stats"><Status value={active.status||'UNVERIFIED'}/><span>Active digest: <code>{active.digest}</code></span></div>}
 </Card>
 {draft&&<Card title="Review and stage"><div className="split"><div><span className="muted">Current draft — server-validated JSON</span><textarea aria-label="Strategy JSON draft" rows={16} spellCheck="false" disabled={busy} value={document} onChange={e=>{setDocument(e.target.value);setDiff([])}}/></div><div><span className="muted">Active reference</span><Inspection object={active?.document||{}}/></div></div>
 <div className="input-row"><label>Audit reason<input value={reason} onChange={e=>setReason(e.target.value)} placeholder="Why are you changing this?"/></label><button className="secondary" disabled={busy} onClick={validate}>Preview changes</button><button onClick={save} disabled={!reason.trim()||busy}>Stage draft</button></div>
 {diff.length>0&&<div className="diff"><strong>Active draft diff ({diff.length} fields)</strong>{diff.map(change=><div key={change.path}><code>{change.path}</code><span>{JSON.stringify(change.before)} → {JSON.stringify(change.after)}</span></div>)}</div>}
 </Card>}
 {draft&&<Card title="Second-step advisory authorization"><p className="muted">Activation and rollback are disabled by the production backend unless separate server-side gates are enabled. These actions cannot authorize funded trading.</p>
 <div className="input-row"><label>Re-enter admin credential<input type="password" value={activationSecret} onChange={e=>setActivationSecret(e.target.value)} autoComplete="off"/></label><label>Exact acknowledgment<input value={ack} onChange={e=>setAck(e.target.value)} placeholder="ACTIVATE_ADVISORY_ONLY"/></label></div><div className="actions"><button disabled={busy||ack!=='ACTIVATE_ADVISORY_ONLY'} onClick={()=>activate(false)}><Check size={16}/> Authorize advisory profile</button><button disabled={busy||ack!=='ROLLBACK_ADVISORY_ONLY'} className="secondary" onClick={()=>activate(true)}><RotateCcw size={16}/> Roll back advisory profile</button></div>
 </Card>}
 {message&&<p className={messageError?'error notice-error':'notice'} role={messageError?'alert':'status'}>{message}</p>}
 </div>
}
function App(){
 const[section,setSection]=useState('Overview');
 const[secret,setSecret]=useState('');
 const[scope,setScope]=useState('all');
 const[records,setRecords]=useState({});
 const[errors,setErrors]=useState({});
 const[loadedAt,setLoadedAt]=useState('');
 const[busy,setBusy]=useState(false);
 const[selected,setSelected]=useState(null);
 const[search,setSearch]=useState('');
 // Collapse the credential entry once a read has been accepted, and offer an
 // explicit re-entry. The value never reaches storage of any kind.
 const[credentialLocked,setCredentialLocked]=useState(false);
 const requestGeneration=useRef(0);
 useEffect(()=>{setCredentialLocked(false)},[secret]);
 useEffect(()=>{requestGeneration.current+=1;setRecords({});setErrors({});setLoadedAt('');setSelected(null);setBusy(false)},[secret,scope]);
 async function refresh(){if(!secret.trim())return;const generation=++requestGeneration.current;setBusy(true);const endpoints={...READS,summary:'/api/analytics/summary?scope='+scope,intelligence:'/api/analytics/intelligence?scope='+scope};
  const requestedSecret=secret;
  const results=await Promise.allSettled(Object.entries(endpoints).map(async([key,path])=>[key,await apiRequest(path,{secret:requestedSecret})]));
  if(generation!==requestGeneration.current)return;
  const got={},errs={};results.forEach((result,i)=>{const key=Object.keys(endpoints)[i];if(result.status==='fulfilled')got[key]=result.value[1];else errs[key]=String(result.reason?.message||'Unavailable')});
  setRecords(got);setErrors(errs);setLoadedAt(new Date().toISOString());setBusy(false)
  if(Object.keys(got).length)setCredentialLocked(true)}
 const comparisonStatus=evidenceStatus(records.comparisons);
 const latestComparison=Array.isArray(records.comparisons?.rows)?records.comparisons.rows[0]:null;
 const ai=aiOperatingState(records.zero);
 // Every figure below is read from a recorded payload. A value the server did
 // not return is 'Not verified', never 0 and never a hard-coded green.
 const count=(value,of)=>typeof value==='number'?(of===undefined?String(value):value+' / '+of):'Not verified';
 const aiEvidence=records.zero?{invoked:records.zero.ai_invoked,invocation_status:records.zero.ai_invocation_status,failure_type:records.zero.ai_failure_type,eligible_before_ai:records.zero.ai_eligible_candidates_before_ai,returned_by_ai:records.zero.ai_candidates_returned,confidence:records.zero.ai_confidence_distribution,budget_exhausted:records.zero.ai_budget_exhausted,unavailable:records.zero.ai_unavailable}:null;
 const cards=[
  ['Model authority',modelAuthority(latestComparison)],
  ['V1 / V2 comparison',comparisonStatus],
  ['Scanned (latest comparison)',latestComparison?count(latestComparison.scanned):'Not verified'],
  ['V2 features ready',latestComparison?count(latestComparison.v2_ready,latestComparison.scanned):'Not verified'],
  ['V1 shortlist',Array.isArray(latestComparison?.v1_top)?String(latestComparison.v1_top.length):'Not verified'],
  ['V2 shortlist (production)',Array.isArray(latestComparison?.v2_top)?String(latestComparison.v2_top.length):'Not verified'],
  ['Qualified (last scan)',records.zero?count(records.zero.qualified):'Not verified'],
  ['Paper-admission eligible',records.zero?count(records.zero.paper_admission_eligible):'Not verified'],
  ['AI advisory',ai.state],
  ['Qualification evidence',readState(records.zero,errors.zero)],
  ['Analytics summary',readState(records.summary,errors.summary)],
  ['F11 verification','NOT ATTESTED'],
 ];
 const filtered=Array.isArray(records.comparisons?.rows)?records.comparisons.rows.filter(row=>JSON.stringify(row).toLowerCase().includes(search.toLowerCase())):[];
 return <div className="shell">
 <aside className="sidebar"><div className="brand"><div className="brand-icon">◈</div><div><strong>O’Pip</strong><span>Operational Control Center</span></div></div>
 <nav aria-label="Main navigation">{NAV.map(([name,Icon])=><button className={'nav '+(section===name?'selected':'')} key={name} onClick={()=>{setSection(name);setSelected(null)}}><Icon size={18}/>{name}</button>)}</nav>
 <div className="sidebar-bottom"><ShieldCheck size={19}/><div>TRADING AUTHORITY<strong>UNCHANGED</strong><span>Read-only operational surface</span></div></div></aside>
 <main><header className="topbar"><div><span className="eyebrow">O'PIP / OPERATIONS</span><h1>{section}</h1><p>Evidence-led visibility · no inferred profits · safety-first configuration</p></div><div className="top-actions"><Status value={loadedAt?'SNAPSHOT LOADED':'AWAITING EVIDENCE'}/><button className="secondary" disabled={busy||!secret.trim()} onClick={refresh}><RefreshCw size={16}/>{busy?'Loading':'Refresh'}</button></div></header>
 <div className="auth-bar">{credentialLocked?<><span className="muted">Credential held in this tab's memory only · never written to browser storage</span><button className="secondary" onClick={()=>{setCredentialLocked(false);setSecret('')}}>Change credential</button></>:<label>Operator API credential<input type="password" value={secret} onChange={e=>setSecret(e.target.value)} autoComplete="off" placeholder="X-Webhook-Secret (session memory only)" /></label>}<label>Window<select value={scope} onChange={e=>setScope(e.target.value)}><option value="all">All recorded</option><option value="today">Today (UTC day)</option></select></label><span className="muted">Last fetched: {loadedAt?utcWithLocal(loadedAt).utc+' (local '+utcWithLocal(loadedAt).local+')':'Never'} · window boundaries are UTC</span></div>
 {!secret&&<div className="notice" role="status"><LockKeyhole size={17}/> Enter the existing O’Pip operator credential and select Refresh. No credentials are persisted.</div>}
 {section==='Overview'&&<div className="stack"><div className="metric-grid">{cards.map(([name,value])=><div className="metric" key={name}><span>{name}</span><Status value={value}/></div>)}</div><div className="cols"><Card title="Production analytics"><Pairs data={records.summary}/>{errors.summary&&<p className="error">{errors.summary}</p>}</Card><Card title="Why zero trades?"><Pairs data={records.zero}/>{errors.zero&&<p className="error">{errors.zero}</p>}</Card></div><Card title="Comparison evidence"><EvidenceTable response={records.comparisons} onSelect={setSelected}/>{errors.comparisons&&<p className="error">{errors.comparisons}</p>}</Card></div>}
 {section==='V1 / V2 Evidence'&&<div className="stack"><Card title="Recorded V2 production / V1 observation"><p className="muted">Authority is read from each recorded comparison. Missing records are displayed as missing evidence, not as zero signals.</p><div className="input-row"><label>Filter recorded scans<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Scan ID or symbol"/></label><button className="secondary" disabled={!filtered.length} onClick={()=>download(exportCsv(filtered),'opip-v1-v2-evidence.csv','text/csv')}><ArrowDownToLine size={16}/> Export visible rows</button></div><EvidenceTable response={{status:records.comparisons?.status,rows:filtered}} onSelect={setSelected}/></Card></div>}
 {section==='Qualification'&&<div className="stack"><Card title="Where candidates stopped">{records.zero?<div className="stack"><p className="muted">{String(records.zero.explanation||'No explanation sentence was recorded')}</p><Funnel stages={qualificationFunnel(records.zero)} empty="No funnel fields were returned"/><span className="muted">Dominant terminal gate: {String(records.zero.dominant_terminal_gate||'Not verified')} · deepest stage reached: {String(records.zero.deepest_stage_reached||'Not verified')} · scan: {String(records.zero.scan_id||'Not verified')} · observed: {utcWithLocal(records.zero.last_scan_at_utc).utc}</span></div>:<Empty title="Qualification evidence unavailable" detail={errors.zero||'Refresh to request the server-side zero-trade explanation'}/>}</Card><Card title="Rejection breakdown">{records.zero?<Funnel stages={rejectionBreakdown(records.zero)} empty="No rejection fields were returned"/>:<Empty title="No rejection evidence" detail={errors.zero||'No zero-trade explanation was returned'}/>}</Card><Card title="Nearest misses" action={<span className="muted">Recorded evidence only, never a recommendation</span>}>{Array.isArray(records.zero?.nearest_misses)&&records.zero.nearest_misses.length?<Inspection object={records.zero.nearest_misses}/>:<p className="muted">No nearest-miss records were returned for this scan.</p>}</Card></div>}
 {section==='Intelligence'&&<div className="stack"><Card title="AI advisory operating state"><div className="inline-stats"><Status value={ai.state}/><span>{ai.detail}</span></div><Pairs data={aiEvidence}/><p className="muted">A stage that was never reached is not a disabled provider, and an unreachable provider is not a pipeline failure: each state is shown as recorded.</p></Card><Card title="Intelligence read model">{records.intelligence?<><Pairs data={records.intelligence}/><details><summary>Advanced JSON evidence</summary><Inspection object={records.intelligence}/></details></>:<Empty title="Intelligence evidence unavailable" detail={errors.intelligence||'Refresh to query the intelligence read model'}/>}</Card></div>}
 {section==='Operations'&&<div className="cols"><Card title="Analytical diagnostics"><Pairs data={records.intelligence?.data_platform||records.intelligence}/><p className="muted">CPU, memory, reconciliation and learning readiness require authoritative host telemetry; unknown is not healthy.</p></Card><Card title="Data freshness"><Pairs data={records.intelligence?.freshness||records.summary}/><Status value="LIVE HOST ATTESTATION UNVERIFIED"/></Card></div>}
 {section==='Config Admin'&&<ConfigAdmin secret={secret}/>}
 <footer><span>O’Pip · Review candidate — not an execution system</span><span>No funded trading controls · No F11 override · No automated retention deletion</span></footer>
 </main>
 {selected&&<div className="drawer-backdrop" onClick={()=>setSelected(null)}><div className="drawer" role="dialog" aria-modal="true" aria-label="Comparison evidence" onClick={e=>e.stopPropagation()}><div className="card-header"><h3>Canonical comparison evidence</h3><button onClick={()=>setSelected(null)}>Close</button></div><Inspection object={selected}/></div></div>}
 </div>
}
createRoot(document.getElementById('root')).render(<App/>);
