import React,{useEffect,useRef,useState} from 'react';
import{createRoot}from'react-dom/client';
import{Activity,ArrowDownToLine,BarChart3,BrainCircuit,ChartNoAxesCombined,Check,ChevronRight,CircleAlert,ClipboardList,Database,GitCompareArrows,LockKeyhole,RefreshCw,RotateCcw,Search,Settings2,ShieldCheck,SlidersHorizontal}from'lucide-react';
import{apiRequest,modelAuthority,evidenceStatus,objectSummary,exportCsv,sanitizeJsonExport,statusLevel,readState,utcWithLocal,qualificationFunnel,rejectionBreakdown,aiOperatingState,configAdminState,scannerCoverage,scannerExclusions,ENDPOINTS,headerFields,kpiCards,provenanceRows,stageOutcome,provenanceLabel,zeroBuySummary}from'./api.mjs';
import{LOGO_ASSET,LOGO_FALLBACK_MARK,activeFilters,bytes,clampPresets,freshness,metric,payloadState,percent,removeFilter,configDiff,configFieldKind,divergenceLabel,divergenceLevel,precheckStagedValue,rubricTotal,signalQuery,sortRows,windowStartIso}from'./format.mjs';
import './theme.css';
import './style.css';
import './style.v2.css';
const NAV=[
 ['Overview',Activity],['V1 / V2 Evidence',GitCompareArrows],['Qualification',SlidersHorizontal],
 ['Intelligence',BrainCircuit],['Operations',Database],['Data & Retention',ClipboardList],['Config Admin',Settings2]
];
const READS={
 summary:'/api/analytics/summary?scope=all',
 intelligence:'/api/analytics/intelligence?scope=all',
 zero:'/api/opip/zero-trade-explanation?include_candidates=true',
 comparisons:'/api/opip/scoring-comparison',
 health:ENDPOINTS.health,
 scanner:ENDPOINTS.scanner,
 ai:ENDPOINTS.ai,
 data:ENDPOINTS.data,
 config:ENDPOINTS.config
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
// --- Brand -----------------------------------------------------------------
// The approved transparent logo is referenced by path. If the asset is absent
// the console states that fact and renders a declared text mark; it never
// substitutes a different logo or a generated approximation.
function BrandMark(){
 const[failed,setFailed]=useState(false);
 return <div className="op-brand">
  {failed
   ? <span className="op-logo-fallback" title={`Approved logo asset missing: ${LOGO_ASSET}`}>{LOGO_FALLBACK_MARK}</span>
   : <img className="op-logo" src={LOGO_ASSET} alt="O’Pip" height="46" onError={()=>setFailed(true)}/>}
  <span className="op-brand-text"><strong>O’Pip</strong><span>Operational Control Center</span></span>
 </div>;
}
function Skeleton({rows=2}){return <div className="stack" aria-hidden="true">{Array.from({length:rows}).map((_,i)=><div className="op-skeleton" key={i}/>)}</div>}
// A widget-level boundary: one failing panel must not blank the console.
class WidgetBoundary extends React.Component{
 constructor(props){super(props);this.state={failed:false,error:''}}
 static getDerivedStateFromError(error){return{failed:true,error:String(error?.message||error)}}
 render(){
  if(this.state.failed)return <div className="op-banner bad" role="alert"><CircleAlert size={16}/><span>This widget failed: {this.state.error}</span><button className="secondary" onClick={()=>this.setState({failed:false,error:''})}><RefreshCw size={14}/> Retry widget</button></div>;
  return this.props.children;
 }
}
function GlobalHeader({health,authenticated}){
 const fields=headerFields(health,{authenticated});
 return <section className="op-header" aria-label="Global operational status">
  <BrandMark/>
  <div className="op-header-fields">
   {fields.map(field=><div className={'op-header-field '+field.level} key={field.id}>
    <span>{field.label}</span><strong>{field.value}</strong>{field.detail&&<small>{field.detail}</small>}
   </div>)}
  </div>
 </section>;
}
function KpiRibbon({health}){
 const cards=kpiCards(health);
 return <div className="op-ribbon">{cards.map(card=><section className="op-kpi" key={card.id}>
  <header><h3>{card.title}</h3><Status value={card.status}/></header>
  <div className="op-kpi-rows">{card.rows.map(row=><div className="op-kpi-row" key={row.label}><span>{row.label}</span><strong className={row.level==='bad'?'bad':row.level==='good'?'good':''}>{row.value}</strong></div>)}</div>
  <footer><span>{card.source}</span><span>{card.observedAt?utcWithLocal(card.observedAt).utc:'No source timestamp'}</span>{card.note&&<span>{card.note}</span>}</footer>
 </section>)}</div>;
}

// --- Forensics drawer ------------------------------------------------------
// Missing provenance is named, never synthesised. The raw payload stays
// available behind an explicit advanced disclosure.
function ForensicsDrawer({secret,attemptId,symbol,onClose}){
 const[data,setData]=useState(null);
 const[error,setError]=useState('');
 useEffect(()=>{
  if(!attemptId||!symbol)return undefined;
  const controller=new AbortController();let live=true;
  apiRequest(`${ENDPOINTS.forensics}?attempt_id=${encodeURIComponent(attemptId)}&symbol=${encodeURIComponent(symbol)}`,{secret,signal:controller.signal})
   .then(payload=>{if(live)setData(payload)})
   .catch(err=>{if(live)setError(String(err?.message||err))});
  return()=>{live=false;controller.abort()};
 },[secret,attemptId,symbol]);
 const rows=provenanceRows(data);
 const components=Array.isArray(data?.components)?data.components:[];
 const missing=Array.isArray(data?.missing_fields)?data.missing_fields:[];
 return <div className="drawer-backdrop" onClick={onClose}>
  <div className="drawer" role="dialog" aria-modal="true" aria-label={`Forensics for ${symbol}`} onClick={event=>event.stopPropagation()}>
   <div className="card-header"><h3>{symbol} · {attemptId}</h3><button className="secondary" onClick={onClose}>Close</button></div>
   {!data&&!error&&<Skeleton rows={3}/>}
   {error&&<p className="error" role="alert">{error}</p>}
   {data&&data.status!=='MEASURED'&&<Empty title="No recorded row" detail={data.reason_code||'The projection holds no row for this identity.'}/>}
   {data&&data.status==='MEASURED'&&<div className="stack">
    <div className="op-prov">{rows.map(row=><div className={'op-prov-row'+(row.missing?' missing':'')} key={row.label}><span>{row.label}</span><strong>{row.value}</strong></div>)}</div>
    {missing.length>0&&<p className="op-banner warn" role="status">Not recorded for this source: {missing.join(', ')}</p>}
    <Card title="V2 rubric components"><div className="op-prov">
     {components.length===0&&<p className="muted">No component breakdown was recorded for this row.</p>}
     {components.map(item=><div className="op-prov-row" key={`${item.direction}:${item.component}`}><span>{item.direction} · {String(item.component).replaceAll('_',' ')}</span><strong>{metric(item.points,{digits:1})}</strong></div>)}
    </div></Card>
    <Card title="Attempt events"><div className="op-prov">
     {(data.attempt_events||[]).length===0&&<p className="muted">No attempt lifecycle events were projected.</p>}
     {(data.attempt_events||[]).map((event,index)=><div className="op-prov-row" key={index}><span>{event.event_type||'EVENT'} · {event.observed_at_utc||'unknown'}</span><strong>{event.state||'—'} {event.outcome||''} {event.reason_code||''}</strong></div>)}
    </div></Card>
    <Card title="Worker scoring provenance">{data.worker_status?<Inspection object={data.worker_status}/>:<p className="muted">No worker status document was projected for this attempt.</p>}</Card>
    <details><summary>Advanced JSON evidence</summary><Inspection object={data}/></details>
   </div>}
  </div>
 </div>;
}

// --- Workspace A: live signals and V1/V2 comparison ------------------------
const SIGNAL_COLUMNS=[
 {key:'observed_at_utc',label:'Attempt time (UTC)'},
 {key:'symbol',label:'Symbol'},
 {key:'trade_direction',label:'Direction'},
 {key:'timeframe',label:'Timeframe'},
 {key:'technical_score',label:'V1 score'},
 {key:'v2_long_score',label:'V2 LONG'},
 {key:'v2_short_score',label:'V2 SHORT'},
 {key:'v2_feature_status',label:'V2 feature status'},
 {key:'divergence',label:'Divergence'},
 {key:'v2_profile_version',label:'V2 profile'},
 {key:'outcome_maturity',label:'Outcome'},
 {key:'record_sha256',label:'Evidence hash'}
];
function SignalTable({rows,sort,onSort,onSelect,busy,error,status}){
 if(error)return <div className="op-banner bad" role="alert">{error}</div>;
 if(busy&&!rows.length)return <Skeleton rows={3}/>;
 if(status==='UNAVAILABLE')return <Empty title="Analytical projection unavailable" detail="The derived projection is not readable yet. Run bounded ingestion from the Data & Retention workspace."/>;
 if(!rows.length)return <Empty title="No signal evidence in this filter" detail="An empty result is not evidence that no opportunity existed: no projected row matched these filters."/>;
 return <div className="table-scroll"><table>
  <thead><tr>{SIGNAL_COLUMNS.map(column=><th key={column.key} onClick={()=>onSort(column.key)}
   aria-sort={sort.key===column.key?(sort.dir==='asc'?'ascending':'descending'):'none'}>
   {column.label}{sort.key===column.key?(sort.dir==='asc'?' ▲':' ▼'):''}</th>)}<th>Detail</th></tr></thead>
  <tbody>{rows.map(row=><tr key={row.snapshot_key}>
   <td className="numeric">{row.observed_at_utc?utcWithLocal(row.observed_at_utc).utc:'Not recorded'}</td>
   <td>{row.symbol}</td>
   <td>{row.trade_direction||'—'}</td>
   <td>{row.timeframe||'—'}</td>
   <td className="numeric">{metric(row.technical_score)}</td>
   <td className="numeric">{metric(row.v2_long_score,{digits:1})}</td>
   <td className="numeric">{metric(row.v2_short_score,{digits:1})}</td>
   <td>{row.v2_feature_status||'NOT_COMPUTED'}</td>
   <td><span className={'op-badge '+divergenceLevel(row.divergence)}>{divergenceLabel(row.divergence)}</span></td>
   <td>{row.v2_profile_version||'—'}</td>
   <td>{row.outcome_maturity||'OUTCOME NOT MATURED'}</td>
   <td className="op-mono">{(row.record_sha256||'').slice(0,12)||'—'}</td>
   <td><button className="subtle" onClick={()=>onSelect(row)}>Inspect <ChevronRight size={14}/></button></td>
  </tr>)}</tbody>
 </table></div>;
}

function SignalsWorkspace({secret,onTune}){
 const EMPTY={symbols:[],direction:'',v2Status:'',divergence:'',minScore:'',maxScore:'',observedFrom:'',observedTo:'',limit:50};
 const[filters,setFilters]=useState(EMPTY);
 const[symbolInput,setSymbolInput]=useState('');
 const[rows,setRows]=useState([]);
 const[status,setStatus]=useState('NOT LOADED');
 const[nextCursor,setNextCursor]=useState(null);
 const[stack,setStack]=useState([]);
 const[busy,setBusy]=useState(false);
 const[error,setError]=useState('');
 const[sort,setSort]=useState({key:'observed_at_utc',dir:'desc'});
 const[presets,setPresets]=useState([]);
 const[presetName,setPresetName]=useState('');
 const[selected,setSelected]=useState(null);
 const generation=useRef(0);
 const controllerRef=useRef(null);
 async function load(nextFilters,{cursor=null,remember=false}={}){
  if(!secret.trim()){setStatus('AWAITING CREDENTIAL');return}
  const mine=++generation.current;
  controllerRef.current?.abort();
  const controller=new AbortController();controllerRef.current=controller;
  setBusy(true);setError('');
  try{
   const payload=await apiRequest(ENDPOINTS.signals+signalQuery({...nextFilters,cursor}),{secret,signal:controller.signal});
   if(mine!==generation.current)return;
   setRows(Array.isArray(payload.rows)?payload.rows:[]);
   setNextCursor(payload.next_cursor||null);
   setStatus(payload.status||'UNVERIFIED');
   if(remember)setStack(previous=>[...previous,cursor]);
  }catch(err){
   if(mine!==generation.current||String(err?.name)==='AbortError')return;
   setRows([]);setNextCursor(null);setError(String(err?.message||err));setStatus('UNAVAILABLE');
  }finally{if(mine===generation.current)setBusy(false)}
 }
 useEffect(()=>{
  const timer=setTimeout(()=>{if(secret.trim())load(filters,{cursor:null})},250);
  return()=>clearTimeout(timer);
 },[secret,filters]);
 const chips=activeFilters(filters);
 const visible=sortRows(rows,sort.key,sort.dir);
 const windowPresets=clampPresets(30);
 const applyPreset=preset=>{setStack([]);setFilters(current=>({...current,observedFrom:windowStartIso(preset.days),observedTo:''}))};
 const addSymbol=()=>{
  const symbol=symbolInput.trim().toUpperCase();
  if(!symbol)return;
  setStack([]);
  setFilters(current=>({...current,symbols:[...new Set([...current.symbols,symbol])].slice(0,25)}));
  setSymbolInput('');
 };
 const tune=()=>{if(typeof onTune==='function')onTune('V2_SCORING','v2.threshold')};
 const update=key=>event=>{const value=event.target.value;setStack([]);setFilters(current=>({...current,[key]:value}))};
 return <div className="stack">
  <Card title="Live signals · V1 and V2 side by side" action={<Status value={status}/>}>
   <p className="muted">A score that was never computed stays empty; it is never shown as zero. Divergence is classified from the recorded thresholds.</p>
   <div className="op-toolbar">
    <label className="op-field">Symbol
     <input value={symbolInput} onChange={event=>setSymbolInput(event.target.value)}
      onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();addSymbol()}}} placeholder="BTC/USD then Enter"/></label>
    <button className="secondary" onClick={addSymbol} disabled={!symbolInput.trim()}><Search size={14}/> Add symbol</button>
    <label className="op-field">Direction<select value={filters.direction} onChange={update('direction')}>
     <option value="">Any</option><option value="LONG">LONG</option><option value="SHORT">SHORT</option></select></label>
    <label className="op-field">V2 status<select value={filters.v2Status} onChange={update('v2Status')}>
     <option value="">Any</option><option value="READY">READY</option>
     <option value="INSUFFICIENT_FEATURES">INSUFFICIENT_FEATURES</option>
     <option value="NOT_COMPUTED">NOT_COMPUTED</option></select></label>
    <label className="op-field">Divergence<select value={filters.divergence} onChange={update('divergence')}>
     <option value="">Any</option><option value="BOTH">Both agree</option><option value="V1_ONLY">V1 only</option>
     <option value="V2_ONLY">V2 only</option><option value="NEITHER">Neither</option>
     <option value="INSUFFICIENT_FEATURES">Insufficient features</option>
     <option value="V2_NOT_COMPUTED">V2 not computed</option></select></label>
    <label className="op-field">V1 minimum<input type="number" min="0" max="100" value={filters.minScore} onChange={update('minScore')}/></label>
    <label className="op-field">V1 maximum<input type="number" min="0" max="100" value={filters.maxScore} onChange={update('maxScore')}/></label>
   </div>
   <div className="op-toolbar">
    {windowPresets.map(preset=><button className="op-tab" key={preset.id} disabled={preset.disabled}
      title={preset.disabled?'Beyond the active retention policy':preset.label}
      onClick={()=>applyPreset(preset)}>{preset.label}</button>)}
    <button className="secondary" onClick={()=>{setFilters(EMPTY);setStack([]);setSymbolInput('')}}>
     <RotateCcw size={14}/> Reset all</button>
    <button className="secondary" disabled={!secret.trim()} onClick={()=>load(filters,{cursor:null})}>
     <RefreshCw size={14}/> Reload page</button>
    <button className="subtle" onClick={tune}><SlidersHorizontal size={14}/> Tune parameter in Config</button>
   </div>
   {chips.length>0&&<div className="op-chips">{chips.map(chip=>
    <span className="op-chip" key={`${chip.key}:${chip.value}`}>{chip.label}
     <button aria-label={`Remove ${chip.label}`}
      onClick={()=>{setStack([]);setFilters(current=>removeFilter(current,chip.key,chip.value))}}>×</button>
    </span>)}</div>}
   <div className="op-toolbar">
    <label className="op-field">Save preset as
     <input value={presetName} onChange={event=>setPresetName(event.target.value)} placeholder="Preset name"/></label>
    <button className="secondary" disabled={!presetName.trim()}
     onClick={()=>{setPresets([...presets,{name:presetName.trim(),filters}]);setPresetName('')}}>Save filter preset</button>
    {presets.map(preset=><span className="op-chip" key={preset.name}>{preset.name}
     <button className="subtle" onClick={()=>{setStack([]);setFilters(preset.filters)}}>apply</button>
     <button aria-label={`Delete ${preset.name}`}
      onClick={()=>setPresets(presets.filter(item=>item.name!==preset.name))}>×</button></span>)}
   </div>
   <p className="muted">Filter presets live in this tab only; no browser storage is used.</p>
   <div className="op-toolbar">
    <button className="secondary" disabled={!visible.length}
     onClick={()=>download(exportCsv(visible),'opip-signals.csv','text/csv')}><ArrowDownToLine size={14}/> Export CSV</button>
    <button className="secondary" disabled={!visible.length}
     onClick={()=>download(JSON.stringify(visible,null,2),'opip-signals.json','application/json')}><ArrowDownToLine size={14}/> Export JSON</button>
    <button className="secondary" disabled={!stack.length||busy}
     onClick={()=>{const previous=stack[stack.length-1];setStack(stack.slice(0,-1));load(filters,{cursor:previous})}}>Previous page</button>
    <button className="secondary" disabled={!nextCursor||busy}
     onClick={()=>load(filters,{cursor:nextCursor,remember:true})}>Next page</button>
   </div>
   <SignalTable rows={visible} sort={sort} busy={busy} error={error} status={status}
    onSort={key=>setSort(current=>({key,dir:current.key===key&&current.dir==='desc'?'asc':'desc'}))}
    onSelect={setSelected}/>
  </Card>
  {selected&&<ForensicsDrawer secret={secret} attemptId={selected.attempt_id} symbol={selected.symbol} onClose={()=>setSelected(null)}/>}
 </div>;
}

// --- Workspace C: scanner and qualification funnel -------------------------
function StageRow({label,value,state,detail}){
 return <div className="op-stage"><span>{label}</span>
  <small>{detail||''}</small>
  <strong>{value===null||value===undefined?'—':String(value)}</strong>
  <Status value={state||'UNVERIFIED'}/>
 </div>;
}
// Acquisition coverage is its own dimension: what the attempt achieved against
// its target, and which candidates it excluded and why. A recorded exclusion is
// never hidden, and an attempt with no coverage document says so.
function CoveragePanel({worker}){
 const coverage=scannerCoverage(worker);
 const exclusions=scannerExclusions(worker);
 if(coverage.state==='UNAVAILABLE')
  return <StageRow label="Acquisition coverage" value={null} state="UNAVAILABLE" detail={coverage.detail}/>;
 // A flat fragment: these rows belong to the attempt card's own stage grid, so
 // the panel must not nest a second grid inside it.
 return <>
  <StageRow label="Acquisition coverage" value={`${coverage.valid}/${coverage.target}`} state={coverage.state} detail={coverage.detail}/>
  {coverage.fields.map(field=><StageRow key={field.key} label={field.key} value={field.value} state={coverage.state}/>)}
  {exclusions.total>0&&<StageRow label="Recorded exclusions" value={exclusions.total}
   state={exclusions.truncated?'DEGRADED':'RECORDED'}
   detail={exclusions.truncated?`${exclusions.recorded} shown (bounded evidence)`:'each exclusion carries its reason'}/>}
  {exclusions.rows.map(row=><StageRow key={`${row.pair}-${row.rank??'?'}`} label={`Excluded ${row.pair}`}
   value={row.rank===null?row.status:`rank ${row.rank} · ${row.status}`} state="DEGRADED" detail={row.reason}/>)}
 </>;
}
function ScannerFunnelWorkspace({scanner}){
 const attempts=Array.isArray(scanner?.attempts)?scanner.attempts:[];
 if(!attempts.length)return <Empty title="No scanner attempt evidence" detail={scanner?.reason_code||'No scan attempt has been projected yet. An empty funnel is not proof that discovery ran.'}/>;
 return <div className="stack">{attempts.map(attempt=><Card key={attempt.attempt_id}
  title={attempt.attempt_id} action={<Status value={attempt.v2_status?.status||'NO_EVIDENCE'}/>}>
  <div className="op-stage-list">
   <StageRow label="Attempt admitted / reserved" value={attempt.events} state={attempt.state}
    detail={`last event ${attempt.last_event_at_utc?utcWithLocal(attempt.last_event_at_utc).utc:'unknown'}`}/>
   <StageRow label="Acquisition outcome" value={attempt.acquisition_outcome||'—'} state={attempt.acquisition_outcome||'UNVERIFIED'}
    detail={attempt.reason_code||''}/>
   <CoveragePanel worker={attempt.worker_status}/>
   <StageRow label="Snapshots acquired" value={attempt.snapshots} state={attempt.snapshots>0?'RECORDED':'NO_EVIDENCE'}
    detail={attempt.snapshot_first_at_utc?`first ${utcWithLocal(attempt.snapshot_first_at_utc).utc}`:''}/>
   <StageRow label="Manifest / worker status" value={attempt.worker_status?'RECORDED':'NOT PROJECTED'}
    state={attempt.worker_status?'RECORDED':'NO_EVIDENCE'}
    detail={attempt.worker_status_at_utc?utcWithLocal(attempt.worker_status_at_utc).utc:''}/>
   <StageRow label="V2 features READY" value={attempt.v2_status?.READY} state={(attempt.v2_status?.READY||0)>0?'RECORDED':'NO_EVIDENCE'}/>
   <StageRow label="V2 INSUFFICIENT_FEATURES" value={attempt.v2_status?.INSUFFICIENT_FEATURES} state={(attempt.v2_status?.INSUFFICIENT_FEATURES||0)>0?'DEGRADED':'RECORDED'}/>
   <StageRow label="V2 ERROR" value={attempt.v2_status?.ERROR} state={(attempt.v2_status?.ERROR||0)>0?'ERROR':'RECORDED'}/>
   <StageRow label="V2 NOT_COMPUTED" value={attempt.v2_status?.NOT_COMPUTED} state={(attempt.v2_status?.NOT_COMPUTED||0)>0?'UNVERIFIED':'RECORDED'}/>
   <StageRow label="Result promoted" value={null} state="UNAVAILABLE" detail={attempt.promotion_reason}/>
   <StageRow label="Candidates qualified" value={null} state="UNAVAILABLE" detail={attempt.qualification_reason}/>
   <StageRow label="AI review" value={null} state="UNAVAILABLE" detail={attempt.ai_reason}/>
   <StageRow label="Outcome maturity" value={attempt.outcome_maturity} state="UNVERIFIED"/>
  </div>
  {attempt.worker_status&&<details><summary>Advanced JSON evidence</summary><Inspection object={attempt.worker_status}/></details>}
 </Card>)}</div>;
}
// --- Workspace B: AI logic and efficiency ---------------------------------
// A chart is only drawn from recorded numbers. With no inference evidence the
// panel says so instead of rendering an invented curve.
function BarChart({series,label,unit='ms'}){
 if(!Array.isArray(series)||!series.length)return null;
 const max=Math.max(...series.map(item=>item.value),1);
 return <figure className="stack">
  <figcaption className="muted">{label}</figcaption>
  <svg viewBox="0 0 320 90" role="img" aria-label={label} width="100%" height="120">
   {series.map((item,index)=>{
    const width=280/Math.max(series.length,1);
    const height=Math.round((item.value/max)*70);
    return <g key={`${item.label}-${index}`}>
     <rect x={20+index*width} y={80-height} width={Math.max(width-4,2)} height={height} rx="2" fill="var(--op-emerald)"/>
     <title>{`${item.label}: ${item.value} ${unit}`}</title>
    </g>;
   })}
   <line x1="16" y1="82" x2="316" y2="82" stroke="var(--op-border-strong)" strokeWidth="1"/>
  </svg>
  <p className="muted">{series.map(item=>`${item.label} ${item.value}${unit}`).join(' · ')}</p>
 </figure>;
}

function AiWorkspace({aiPayload}){
 const summary=aiPayload?.summary||{};
 const rows=Array.isArray(aiPayload?.page?.rows)?aiPayload.page.rows:[];
 const measured=summary.status==='MEASURED';
 const latencySeries=rows.filter(row=>typeof row.duration_ms==='number').slice(0,12)
  .map(row=>({label:row.symbol||'—',value:Math.round(row.duration_ms)}));
 return <div className="stack">
  <div className="op-ribbon">
   {[['Verified win rate',summary.win_rate,'OUTCOMES NOT MATURED'],
     ['False signal rate',summary.false_signal_rate,'OUTCOMES NOT MATURED'],
     ['Missed opportunity rate',summary.missed_opportunity_rate,'OUTCOMES NOT MATURED'],
     ['Inference latency (p50)',summary.latency_ms_p50,measured?'NO LATENCY RECORDS':'NO INFERENCE EVIDENCE']]
    .map(([label,value,note])=><section className="op-kpi" key={label}>
     <header><h3>{label}</h3><Status value={measured?'MEASURED':'NO_EVIDENCE'}/></header>
     <div className="op-kpi-rows"><div className="op-kpi-row"><span>Value</span>
      <strong className={typeof value==='number'?'':'bad'}>{typeof value==='number'?`${value}`:'INSUFFICIENT DATA'}</strong></div></div>
     <footer><span>{note}</span></footer>
    </section>)}
  </div>
  <Card title="AI inference evidence" action={<Status value={summary.status||'NO_EVIDENCE'}/>}>
   {!measured&&<Empty title="No recorded AI inference" detail={String(summary.reason_code||'NO_AI_INFERENCE_SOURCE').replaceAll('_',' ').toLowerCase()}/>}
   {measured&&<div className="stack">
    <div className="op-kpi-rows">
     <div className="op-kpi-row"><span>Inferences recorded</span><strong>{metric(summary.inferences)}</strong></div>
     <div className="op-kpi-row"><span>Matured outcomes</span><strong className="bad">{metric(summary.matured_outcomes)}</strong></div>
     <div className="op-kpi-row"><span>Model identity</span><strong>{summary.models?.length?`${summary.models[0].model} ${summary.models[0].model_version||''}`.trim():'UNVERIFIED'}</strong></div>
     <div className="op-kpi-row"><span>Observed window</span><strong>{summary.first_observed_at_utc?utcWithLocal(summary.first_observed_at_utc).utc:'UNKNOWN'}</strong></div>
    </div>
    <BarChart series={latencySeries} label="Inference latency by inference (ms)"/>
    <div className="table-scroll"><table>
     <thead><tr><th>Observed (UTC)</th><th>Asset</th><th>Direction</th><th>Model</th><th>Verdict</th><th>Confidence</th><th>Duration</th><th>Maturity</th></tr></thead>
     <tbody>{rows.map(row=><tr key={row.inference_key}>
      <td className="numeric">{row.observed_at_utc?utcWithLocal(row.observed_at_utc).utc:'Not recorded'}</td>
      <td>{row.symbol||'—'}</td><td>{row.direction||'—'}</td>
      <td>{row.model||'—'} {row.model_version||''}</td>
      <td>{row.verdict||'—'}</td>
      <td className="numeric">{metric(row.confidence,{digits:2})}</td>
      <td className="numeric">{metric(row.duration_ms,{digits:0,unit:'ms'})}</td>
      <td>{row.outcome_maturity||'OUTCOME NOT MATURED'}</td>
     </tr>)}</tbody>
    </table></div>
    <p className="muted">An ordinal 0–100 self-reported confidence is not a calibrated probability, so no calibration curve is drawn from it.</p>
   </div>}
  </Card>
 </div>;
}

// --- Workspace E: data and retention ---------------------------------------
function DataRetentionWorkspace({data,secret,adminSecret,onRefresh}){
 const[busy,setBusy]=useState(false);
 const[message,setMessage]=useState('');
 const[failed,setFailed]=useState(false);
 const[days,setDays]=useState(30);
 if(!data||data.status==='UNAVAILABLE')return <Empty title="Data platform unavailable"
  detail={data?.reason_code||'The derived projection is not readable yet.'}/>;
 const sources=Array.isArray(data.sources)?data.sources:[];
 const runs=Array.isArray(data.retention_runs)?data.retention_runs:[];
 const retention=data.retention||{};
 const preview=data.retention_preview||{};
 async function run(path,body,label){
  if(!adminSecret.trim()){setFailed(true);setMessage('The analytics editor credential is required for maintenance actions.');return}
  setBusy(true);setMessage('');
  try{
   const payload=await apiRequest(path,{secret,adminSecret,method:'POST',json:body});
   setFailed(false);
   setMessage(`${label}: ${payload.status||'RECORDED'}${payload.rows_deleted!==undefined?` · ${payload.rows_deleted} rows deleted`:''}`);
   if(typeof onRefresh==='function')onRefresh();
  }catch(err){setFailed(true);setMessage(String(err?.message||err))}
  finally{setBusy(false)}
 }
 return <div className="stack">
  <Card title="Canonical source coverage" action={<Status value={data.status}/>}>
   <div className="table-scroll"><table>
    <thead><tr><th>Source</th><th>Kind</th><th>Verified offset</th><th>Records</th><th>Quarantined</th><th>Last ingest (UTC)</th><th>Lag</th></tr></thead>
    <tbody>{sources.map(source=><tr key={source.source_id}>
     <td className="op-mono">{source.source_id}</td><td>{source.record_kind}</td>
     <td className="numeric">{metric(source.last_offset)}</td>
     <td className="numeric">{metric(source.records_committed)}</td>
     <td className="numeric">{metric(source.quarantined)}</td>
     <td className="numeric">{source.last_ingest_at_utc?utcWithLocal(source.last_ingest_at_utc).utc:'Never'}</td>
     <td className="numeric">{source.ingestion_lag_seconds!==null&&source.ingestion_lag_seconds!==undefined?`${Math.round(source.ingestion_lag_seconds)}s`:'—'}</td>
    </tr>)}</tbody>
   </table></div>
   <p className="muted">Read-only inspectors: canonical JSONL stays authoritative and is never modified by this console.</p>
  </Card>
  <div className="cols">
   <Card title="Storage and aging">
    <div className="op-kpi-rows">
     <div className="op-kpi-row"><span>Analytics DB</span><strong>{bytes(data.db_bytes)}</strong></div>
     <div className="op-kpi-row"><span>WAL</span><strong>{bytes(data.wal_bytes)}</strong></div>
     <div className="op-kpi-row"><span>Schema version</span><strong>{metric(data.schema_version)}</strong></div>
     <div className="op-kpi-row"><span>Quarantine backlog</span><strong className={data.quarantine_backlog>0?'bad':''}>{metric(data.quarantine_backlog)}</strong></div>
     {Object.entries(data.aging||{}).map(([bucket,total])=><div className="op-kpi-row" key={bucket}>
      <span>{String(bucket).replaceAll('_',' ').toLowerCase()}</span><strong>{metric(total)}</strong></div>)}
    </div>
   </Card>
   <Card title="Retention policy">
    <div className="op-kpi-rows">
     <div className="op-kpi-row"><span>Policy</span><strong>{retention.retention_days?`${retention.retention_days} days`:'DEFAULT 30 DAYS'}</strong></div>
     <div className="op-kpi-row"><span>Deletion enabled</span><strong className="bad">{retention.dry_run===0?'AUTHORIZED':'DRY RUN ONLY'}</strong></div>
     <div className="op-kpi-row"><span>Eligible rows</span><strong>{metric(preview.eligible_total)}</strong></div>
     <div className="op-kpi-row"><span>Unverified protected</span><strong>{metric(preview.protected_unverified)}</strong></div>
     <div className="op-kpi-row"><span>Audit-locked protected</span><strong>{metric(preview.protected_locked)}</strong></div>
    </div>
    <div className="op-toolbar">
     <label className="op-field">Policy days<select value={days} onChange={event=>setDays(Number(event.target.value))}>
      {[7,15,30,60,90].map(value=><option key={value} value={value}>{value}</option>)}
     </select></label>
     <button className="secondary" disabled={busy} onClick={()=>run(ENDPOINTS.ingest,{max_runs_per_source:2},'Ingestion')}>Run bounded ingestion</button>
     <button className="secondary" disabled={busy} onClick={()=>run(ENDPOINTS.reconcile,{sample_records:5},'Reconciliation')}>Reconcile projection</button>
     <button className="secondary" disabled={busy} onClick={()=>run(ENDPOINTS.retention,{retention_days:days,dry_run:true},'Retention dry run')}>Preview retention purge</button>
    </div>
    {message&&<p className={failed?'error':'notice'} role={failed?'alert':'status'}>{message}</p>}
    <p className="muted">Deletion requires the separate environment authorization; this console cannot enable it.</p>
   </Card>
  </div>
  <Card title="Recent housekeeping">
   {runs.length===0&&<p className="muted">No housekeeping run has been recorded.</p>}
   {runs.length>0&&<div className="table-scroll"><table>
    <thead><tr><th>Started (UTC)</th><th>Policy</th><th>Dry run</th><th>Considered</th><th>Deleted</th><th>Protected</th><th>Status</th></tr></thead>
    <tbody>{runs.map(item=><tr key={item.run_id}>
     <td className="numeric">{item.started_at_utc?utcWithLocal(item.started_at_utc).utc:'—'}</td>
     <td>{item.policy_version}</td><td>{item.dry_run?'YES':'NO'}</td>
     <td className="numeric">{metric(item.rows_considered)}</td>
     <td className="numeric">{metric(item.rows_deleted)}</td>
     <td className="numeric">{metric((item.rows_protected_unverified||0)+(item.rows_protected_locked||0))}</td>
     <td><Status value={item.status}/></td>
    </tr>)}</tbody>
   </table></div>}
  </Card>
 </div>;
}

// --- Workspace F: schema-driven Admin Console ------------------------------
// Fields are rendered from registry metadata. Nothing about a setting's type,
// bounds or authority is hardcoded here, and a read-only setting renders its
// reason instead of a disabled control that implies it might be editable.
function ConfigField({definition,value,onChange,error}){
 const kind=configFieldKind(definition);
 let control=null;
 if(kind==='number')control=<input type="number" value={value??''} min={definition.minimum} max={definition.maximum}
  step={definition.step||'any'} onChange={event=>onChange(event.target.value)}/>;
 else if(kind==='toggle')control=<label className="op-chip"><input type="checkbox" checked={value===true}
  onChange={event=>onChange(event.target.checked)}/> {value===true?'true':'false'}</label>;
 else if(kind==='enum')control=<select value={value??''} onChange={event=>onChange(event.target.value)}>
  {(definition.allowed||[]).map(option=><option key={option} value={option}>{option}</option>)}</select>;
 else if(kind==='secret')control=<p className="muted">Secret values are never displayed; rotate them through the approved secret store.</p>;
 else if(kind==='readonly')control=<p className="muted">{definition.read_only_reason||'Read-only value'} · {definition.source_of_truth||'no source'}</p>;
 else control=<input value={value??''} onChange={event=>onChange(event.target.value)}/>;
 return <div className={'op-config-field'+(error?' op-invalid':'')}>
  <label htmlFor={`cfg-${definition.key}`}>{definition.display_name}</label>
  <p>{definition.description}</p>
  {control}
  {error&&<span className="op-config-error">{error}</span>}
  <div className="op-config-meta"><span>{definition.data_type}</span><span>{definition.editability}</span>
   <span>authority {definition.activation_authority}</span>{definition.unit&&<span>{definition.unit}</span>}</div>
 </div>;
}

function RegistryConsole({config,secret,adminSecret,initialDomain,onReload}){
 const domains=Array.isArray(config?.domains)?config.domains:[];
 const[domain,setDomain]=useState(initialDomain||domains[0]?.domain||'');
 const[values,setValues]=useState({});
 const[errors,setErrors]=useState({});
 const[note,setNote]=useState('');
 const[actor,setActor]=useState('');
 const[message,setMessage]=useState('');
 const[failed,setFailed]=useState(false);
 const[busy,setBusy]=useState(false);
 const[preview,setPreview]=useState(null);
 useEffect(()=>{if(initialDomain)setDomain(initialDomain)},[initialDomain]);
 const current=domains.find(item=>item.domain===domain);
 const definitions=Array.isArray(current?.definitions)?current.definitions:[];
 const effective=config?.effective?.values||{};
 const staged=current?.staged?.document||{};
 const diff=configDiff(effective,{...staged,...values});
 const weights=rubricTotal({...staged,...values});
 function update(key,value){
  const definition=definitions.find(item=>item.key===key);
  const checked=precheckStagedValue(definition,value);
  setErrors(current=>{const next={...current};if(checked.ok)delete next[key];else next[key]=checked.message;return next});
  setValues(current=>({...current,[key]:checked.ok?checked.value:value}));
 }
 async function submit(){
  if(!adminSecret.trim()){setFailed(true);setMessage('The configuration editor credential is required to stage.');return}
  if(!note.trim()||!actor.trim()){setFailed(true);setMessage('An actor and an audit note are required to stage.');return}
  setBusy(true);setMessage('');
  try{
   const payload=await apiRequest(ENDPOINTS.configStage,{secret,adminSecret,method:'POST',json:{
    domain,values,actor,audit_note:note,expected_checksum:current?.staged?.config_checksum||''}});
   const outcome=stageOutcome(payload);
   setFailed(outcome.state!=='STAGED');
   setMessage(outcome.message);
   setValues({});setPreview(null);
   if(typeof onReload==='function')onReload();
  }catch(err){setFailed(true);setMessage(String(err?.message||err))}
  finally{setBusy(false)}
 }
 async function requestPreview(){
  if(!adminSecret.trim()){setFailed(true);setMessage('The configuration editor credential is required for a preview.');return}
  setBusy(true);setMessage('');
  try{
   const payload=await apiRequest(ENDPOINTS.configPreview,{secret,adminSecret,method:'POST',json:{domain,values}});
   setPreview(payload.preview||null);setFailed(false);setMessage('Historical preview returned.');
  }catch(err){setFailed(true);setMessage(String(err?.message||err))}
  finally{setBusy(false)}
 }
 if(!domains.length)return <Empty title="Configuration registry unavailable"
  detail={config?.status==='READ_ONLY_UNAVAILABLE'?'Strategy administration is not enabled on this deployment.':'No registry was returned.'}/>;
 return <div className="stack">
  <Card title="Schema-driven configuration registry" action={<Status value={config?.status||'UNREADY'}/>}>
   <div className="op-tabs" role="tablist">{domains.map(item=><button className="op-tab" key={item.domain}
    role="tab" aria-selected={item.domain===domain}
    onClick={()=>{setDomain(item.domain);setValues({});setPreview(null)}}>
    {String(item.domain).replaceAll('_',' ')}</button>)}</div>
   <p className="muted">{config?.activation?.reason||''}</p>
   {domain==='V2_SCORING'&&<p className={weights.complete&&!weights.balanced?'error':'muted'}>
    V2 weight total {weights.total} · {weights.present} of 6 components staged
    {weights.complete&&!weights.balanced?' — the validator rejects any total other than 100':''}</p>}
   <div className="op-config-grid">{definitions.map(definition=><ConfigField key={definition.key}
    definition={definition}
    value={values[definition.key]!==undefined?values[definition.key]
     :(staged[definition.key]!==undefined?staged[definition.key]:effective[definition.key]?.value)}
    onChange={value=>update(definition.key,value)}
    error={errors[definition.key]}/>)}</div>
  </Card>
  <Card title="Stage a version (no runtime effect)">
   <div className="op-toolbar">
    <label className="op-field">Actor<input value={actor} onChange={event=>setActor(event.target.value)} placeholder="who is staging"/></label>
    <label className="op-field">Audit note<input value={note} onChange={event=>setNote(event.target.value)} placeholder="why this change"/></label>
    <button className="secondary" disabled={busy} onClick={requestPreview}>Preview historical impact</button>
    <button disabled={busy||!Object.keys(values).length} onClick={submit}>Stage configuration</button>
   </div>
   {diff.length>0&&<div className="diff"><strong>Effective vs staged ({diff.length} fields)</strong>
    {diff.map(row=><div key={row.key}><code>{row.key}</code>
     <span>{JSON.stringify(row.before)} → {JSON.stringify(row.after)}</span></div>)}</div>}
   {preview&&<div className="stack"><Status value={preview.status}/><Pairs data={preview}/>
    {preview.disclaimer&&<p className="muted">{preview.disclaimer}</p>}</div>}
   {message&&<p className={failed?'error notice-error':'notice'} role={failed?'alert':'status'}>{message}</p>}
   <p className="muted">Staging never changes runtime. Activation and rollback stay with the protected strategy-admin endpoints below.</p>
  </Card>
 </div>;
}

function ProvenanceNote({payload}) {
  const note = provenanceLabel(payload);
  if (!note) return null;
  return (
    <div className={'op-banner ' + (note.level === 'warn' ? 'warn' : '')} role="status">
      <ClipboardList size={15} />
      <span>
        <strong>{note.label}</strong> · {note.detail}
      </span>
    </div>
  );
}
function AuthPanel() {
  return (
    <div className="op-banner" role="status">
      <LockKeyhole size={16} />
      <span>
        <strong>Evidence not loaded.</strong> Enter the operator credential above and select
        Refresh. The console shows nothing until a read succeeds; an unauthenticated view is not
        evidence of zero signals.
      </span>
    </div>
  );
}
function ZeroBuyCard({zero}) {
  const summary = zeroBuySummary(zero);
  if (!summary) {
    return (
      <Empty
        title="Zero-BUY explanation unavailable"
        detail="The recorded zero-trade explanation was not returned. No BUY explanation is guessed."
      />
    );
  }
  return (
    <div className="stack">
      <div className="op-kpi-rows">
        <div className="op-kpi-row"><span>Recorded status</span><strong>{summary.status}</strong></div>
        <div className="op-kpi-row"><span>Dominant terminal gate</span>
          <strong className={summary.dominantGate ? '' : 'bad'}>{summary.dominantGate || 'NOT RECORDED'}</strong></div>
        <div className="op-kpi-row"><span>Deepest stage reached</span>
          <strong>{summary.deepestStage || 'NOT RECORDED'}</strong></div>
        <div className="op-kpi-row"><span>Qualified</span>
          <strong className={summary.qualified === 0 ? 'bad' : ''}>
            {summary.qualified === null ? 'NOT RECORDED' : String(summary.qualified)}
          </strong></div>
        <div className="op-kpi-row"><span>Paper-admission eligible</span>
          <strong>{summary.paperAdmissionEligible === null ? 'NOT RECORDED' : String(summary.paperAdmissionEligible)}</strong></div>
        <div className="op-kpi-row"><span>Scan</span><strong>{summary.scanId || 'NOT RECORDED'}</strong></div>
        <div className="op-kpi-row"><span>Observed (UTC)</span>
          <strong>{summary.observedAtUtc ? utcWithLocal(summary.observedAtUtc).utc : 'NOT RECORDED'}</strong></div>
      </div>
      {summary.explanation && <p className="muted">{String(summary.explanation)}</p>}
      {summary.measuredStages.length > 0 && (
        <div className="op-stage-list">
          {summary.measuredStages.map(stage => (
            <div className="op-stage" key={stage.label}>
              <span>{stage.label}</span>
              <small>{stage.value === 0 ? 'stopped here' : ''}</small>
              <strong>{String(stage.value)}</strong>
            </div>
          ))}
        </div>
      )}
      {summary.unverifiedStages.length > 0 && (
        <p className="muted">Not returned by the recorded explanation: {summary.unverifiedStages.join(', ')}.</p>
      )}
      {summary.rejections.length > 0 && (
        <div className="op-chips">
          {summary.rejections.map(row => (
            <span className="op-chip" key={row.label}>{row.label}: {row.value}</span>
          ))}
        </div>
      )}
      <p className="muted">
        This is recorded evidence from the qualification stage, not an inference. A zero here means the
        deterministic gate refused every candidate on the recorded scan.
      </p>
    </div>
  );
}



function ConfigAdmin({secret, config, configDomain, onReload}){

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
 <WidgetBoundary>
  <RegistryConsole config={config} secret={secret} adminSecret={adminSecret} initialDomain={configDomain} onReload={onReload}/>
 </WidgetBoundary>
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
 const[configDomain,setConfigDomain]=useState('');
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
 <aside className="sidebar"><BrandMark/>
 <nav aria-label="Main navigation">{NAV.map(([name,Icon])=><button className={'nav '+(section===name?'selected':'')} key={name} onClick={()=>{setSection(name);setSelected(null)}}><Icon size={18}/>{name}</button>)}</nav>
 <div className="sidebar-bottom"><ShieldCheck size={19}/><div>TRADING AUTHORITY<strong>UNCHANGED</strong><span>Read-only operational surface</span></div></div></aside>
 <main><GlobalHeader health={records.health} authenticated={credentialLocked}/><header className="topbar">
<div><span className="eyebrow">O'PIP / OPERATIONS</span><h1>{section}</h1><p>Evidence-led visibility · no inferred profits · safety-first configuration</p></div><div className="top-actions"><Status value={loadedAt?'SNAPSHOT LOADED':'AWAITING EVIDENCE'}/><button className="secondary" disabled={busy||!secret.trim()} onClick={refresh}><RefreshCw size={16}/>{busy?'Loading':'Refresh'}</button></div></header>
 <div className="auth-bar">{credentialLocked?<><span className="muted">Credential held in this tab's memory only · never written to browser storage</span><button className="secondary" onClick={()=>{setCredentialLocked(false);setSecret('')}}>Change credential</button><button className="subtle" onClick={()=>{setCredentialLocked(false);setSecret('');setRecords({});setErrors({});setLoadedAt('');setSelected(null)}}>Sign out</button></>:<label>Operator API credential<input type="password" value={secret} onChange={e=>setSecret(e.target.value)} autoComplete="off" placeholder="X-Webhook-Secret (session memory only)" /></label>}<label>Window<select value={scope} onChange={e=>setScope(e.target.value)}><option value="all">All recorded</option><option value="today">Today (UTC day)</option></select></label><span className="muted">Last fetched: {loadedAt?utcWithLocal(loadedAt).utc+' (local '+utcWithLocal(loadedAt).local+')':'Never'} · window boundaries are UTC</span></div>
 {!secret&&<div className="notice" role="status"><LockKeyhole size={17}/> Enter the existing O’Pip operator credential and select Refresh. No credentials are persisted.</div>}
 {section==='Overview'&&<div className="stack">{!credentialLocked&&<AuthPanel/>}<WidgetBoundary><ProvenanceNote payload={records.health?.projection}/><KpiRibbon health={records.health}/></WidgetBoundary><div className="metric-grid">
{cards.map(([name,value])=><div className="metric" key={name}><span>{name}</span><Status value={value}/></div>)}</div><div className="cols"><Card title="Production analytics"><Pairs data={records.summary}/>{errors.summary&&<p className="error">{errors.summary}</p>}</Card><Card title="Why zero BUY signals"><ZeroBuyCard zero={records.zero}/></Card></div><Card title="Comparison evidence"><EvidenceTable response={records.comparisons} onSelect={setSelected}/>{errors.comparisons&&<p className="error">{errors.comparisons}</p>}</Card></div>}
 {section==='V1 / V2 Evidence'&&<div className="stack"><WidgetBoundary><SignalsWorkspace secret={secret} onTune={domain=>{setConfigDomain(domain||'V2_SCORING');setSection('Config Admin')}}/></WidgetBoundary><Card title="Recorded V2 production / V1 observation">
<p className="muted">Authority is read from each recorded comparison. Missing records are displayed as missing evidence, not as zero signals.</p><div className="input-row"><label>Filter recorded scans<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Scan ID or symbol"/></label><button className="secondary" disabled={!filtered.length} onClick={()=>download(exportCsv(filtered),'opip-v1-v2-evidence.csv','text/csv')}><ArrowDownToLine size={16}/> Export visible rows</button></div><EvidenceTable response={{status:records.comparisons?.status,rows:filtered}} onSelect={setSelected}/></Card></div>}
 {section==='Qualification'&&<div className="stack"><WidgetBoundary><ScannerFunnelWorkspace scanner={records.scanner}/></WidgetBoundary><Card title="Where candidates stopped">
{records.zero?<div className="stack"><p className="muted">{String(records.zero.explanation||'No explanation sentence was recorded')}</p><Funnel stages={qualificationFunnel(records.zero)} empty="No funnel fields were returned"/><span className="muted">Dominant terminal gate: {String(records.zero.dominant_terminal_gate||'Not verified')} · deepest stage reached: {String(records.zero.deepest_stage_reached||'Not verified')} · scan: {String(records.zero.scan_id||'Not verified')} · observed: {utcWithLocal(records.zero.last_scan_at_utc).utc}</span></div>:<Empty title="Qualification evidence unavailable" detail={errors.zero||'Refresh to request the server-side zero-trade explanation'}/>}</Card><Card title="Rejection breakdown">{records.zero?<Funnel stages={rejectionBreakdown(records.zero)} empty="No rejection fields were returned"/>:<Empty title="No rejection evidence" detail={errors.zero||'No zero-trade explanation was returned'}/>}</Card><Card title="Nearest misses" action={<span className="muted">Recorded evidence only, never a recommendation</span>}>{Array.isArray(records.zero?.nearest_misses)&&records.zero.nearest_misses.length?<Inspection object={records.zero.nearest_misses}/>:<p className="muted">No nearest-miss records were returned for this scan.</p>}</Card></div>}
 {section==='Intelligence'&&<div className="stack"><WidgetBoundary><AiWorkspace aiPayload={records.ai}/></WidgetBoundary><Card title="AI advisory operating state">
<div className="inline-stats"><Status value={ai.state}/><span>{ai.detail}</span></div><Pairs data={aiEvidence}/><p className="muted">A stage that was never reached is not a disabled provider, and an unreachable provider is not a pipeline failure: each state is shown as recorded.</p></Card><Card title="Intelligence read model">{records.intelligence?<><Pairs data={records.intelligence}/><details><summary>Advanced JSON evidence</summary><Inspection object={records.intelligence}/></details></>:<Empty title="Intelligence evidence unavailable" detail={errors.intelligence||'Refresh to query the intelligence read model'}/>}</Card></div>}
 {section==='Operations'&&<div className="stack"><WidgetBoundary><div className="op-ribbon">{[['F11 protection',headerFields(records.health,{authenticated:credentialLocked}).find(f=>f.id==='f11')],['Reconciliation',headerFields(records.health,{}).find(f=>f.id==='reconciliation')],['Scanner heartbeat',headerFields(records.health,{}).find(f=>f.id==='scanner')],['Deployed revision',headerFields(records.health,{}).find(f=>f.id==='release')]].map(([label,field])=><section className="op-kpi" key={label}><header><h3>{label}</h3><Status value={field?field.level==='good'?'HEALTHY':field.level==='bad'?'DEGRADED':'UNVERIFIED':'UNVERIFIED'}/></header><div className="op-kpi-rows"><div className="op-kpi-row"><span>Observed</span><strong>{field?field.value:'UNAVAILABLE'}</strong></div><div className="op-kpi-row"><span>Source</span><strong>{field&&field.detail?field.detail:'not recorded'}</strong></div></div></section>)}</div></WidgetBoundary>
 <div className="cols"><Card title="Analytical diagnostics">
<Pairs data={records.intelligence?.data_platform||records.intelligence}/><p className="muted">CPU, memory, reconciliation and learning readiness require authoritative host telemetry; unknown is not healthy.</p></Card><Card title="Data freshness"><Pairs data={records.intelligence?.freshness||records.summary}/><Status value="LIVE HOST ATTESTATION UNVERIFIED"/></Card></div></div>}
 {section==='Data & Retention'&&<WidgetBoundary><DataRetentionWorkspace data={records.data} secret={secret} adminSecret={secret} onRefresh={refresh}/></WidgetBoundary>}
 {section==='Config Admin'&&<ConfigAdmin secret={secret} config={records.config} configDomain={configDomain} onReload={refresh}/>}
 <footer><span>O’Pip · Review candidate — not an execution system</span><span>No funded trading controls · No F11 override · No automated retention deletion</span></footer>
 </main>
 {selected&&<div className="drawer-backdrop" onClick={()=>setSelected(null)}><div className="drawer" role="dialog" aria-modal="true" aria-label="Comparison evidence" onClick={e=>e.stopPropagation()}><div className="card-header"><h3>Canonical comparison evidence</h3><button onClick={()=>setSelected(null)}>Close</button></div><Inspection object={selected}/></div></div>}
 </div>
}
createRoot(document.getElementById('root')).render(<App/>);
