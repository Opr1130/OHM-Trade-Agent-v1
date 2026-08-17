from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.services.operations_analytics import build_operations_summary
from app.services.secret_auth import secret_matches


router = APIRouter()


def _require_secret(value: str | None) -> None:
    if not secret_matches(value, get_settings().webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid dashboard secret")


@router.get("/api/analytics/summary")
def analytics_summary(
    scope: str = "today",
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    _require_secret(x_webhook_secret)
    try:
        return build_operations_summary(scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>OHM AI Operations Dashboard</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#e8edf6;background:#08111f}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#08111f 0%,#0b1627 100%);min-height:100vh}
.wrap{max-width:1280px;margin:0 auto;padding:24px}.header{display:flex;gap:16px;justify-content:space-between;align-items:flex-start;margin-bottom:18px}.title h1{margin:0;font-size:26px}.sub{color:#95a6bd;margin-top:6px;font-size:14px}.controls{display:flex;gap:8px;flex-wrap:wrap}.btn{border:1px solid #2a3c55;background:#111f33;color:#dce7f5;padding:9px 13px;border-radius:10px;cursor:pointer}.btn.active{background:#204b7a;border-color:#3979b8}.status{display:inline-flex;gap:8px;align-items:center;font-size:13px;color:#a9bad0}.dot{width:9px;height:9px;border-radius:50%;background:#44d28b;box-shadow:0 0 10px #44d28b}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.card{background:rgba(17,31,51,.94);border:1px solid #20334c;border-radius:16px;padding:16px;box-shadow:0 10px 30px rgba(0,0,0,.18)}.span3{grid-column:span 3}.span4{grid-column:span 4}.span6{grid-column:span 6}.span12{grid-column:span 12}.kicker{font-size:12px;text-transform:uppercase;letter-spacing:.09em;color:#7592b4}.big{font-size:30px;font-weight:700;margin-top:7px}.muted{color:#8ea0b7}.metric{display:flex;justify-content:space-between;gap:18px;padding:8px 0;border-bottom:1px solid #1c2d43}.metric:last-child{border-bottom:0}.metric b{font-variant-numeric:tabular-nums}.good{color:#62d99f}.bad{color:#ff7a86}.warn{color:#f5c96b}.funnel{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:12px}.stage{background:#0d1929;border:1px solid #1d3048;border-radius:12px;padding:12px;text-align:center}.stage .n{font-size:24px;font-weight:700}.stage .l{font-size:12px;color:#8ea0b7;margin-top:4px}.valuebox{background:#0c1a2b;border-left:3px solid #3aa7ff;padding:14px;border-radius:10px;line-height:1.55}.error{display:none;background:#3a1820;border:1px solid #6a2735;color:#ffb3bd;padding:12px;border-radius:10px;margin-bottom:14px}.table{width:100%;border-collapse:collapse}.table th,.table td{text-align:left;padding:8px;border-bottom:1px solid #1d3048;font-size:13px}.table th{color:#8299b5}.footer{color:#6f839e;font-size:12px;margin-top:16px}.secret{display:flex;gap:8px;margin-top:12px}.secret input{flex:1;background:#08111f;border:1px solid #2b3e57;color:white;border-radius:10px;padding:10px}
@media(max-width:900px){.span3,.span4,.span6{grid-column:span 12}.funnel{grid-template-columns:1fr 1fr}.header{flex-direction:column}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="title"><h1>OHM AI — Operations & Learning</h1><div class="sub">Read-only production analytics • UTC day boundary</div></div>
    <div><div class="controls"><button class="btn active" id="todayBtn">Today</button><button class="btn" id="allBtn">All Time</button><button class="btn" id="refreshBtn">Refresh</button></div><div class="status" style="margin-top:10px"><span class="dot"></span><span id="healthText">Waiting for data</span></div></div>
  </div>
  <div class="error" id="errorBox"></div>
  <div class="card span12" id="unlockBox">
    <div class="kicker">Secure analytics</div><div style="margin-top:6px">Enter the existing OHM operator/webhook secret. It is stored only in this browser tab and sent as a request header, never in the URL.</div>
    <div class="secret"><input id="secretInput" type="password" placeholder="OHM operator secret" /><button class="btn" id="unlockBtn">Unlock</button></div>
  </div>
  <div id="dashboardBody" style="display:none">
    <div class="grid">
      <div class="card span3"><div class="kicker">Pairs analyzed</div><div class="big" id="pairsAnalyzed">—</div><div class="muted" id="scanCount">— scans</div></div>
      <div class="card span3"><div class="kicker">Chief AI calls</div><div class="big" id="aiCalls">—</div><div class="muted" id="tokenCount">— tokens</div></div>
      <div class="card span3"><div class="kicker">Accepted / rejected</div><div class="big"><span class="good" id="accepted">—</span> / <span class="bad" id="rejected">—</span></div><div class="muted" id="waitCount">— wait/watch</div></div>
      <div class="card span3"><div class="kicker">Net P/L after costs</div><div class="big" id="netPnl">—</div><div class="muted"><span class="good" id="wins">— wins</span> • <span class="bad" id="losses">— losses</span></div></div>

      <div class="card span12"><div class="kicker">Decision funnel</div><div class="funnel"><div class="stage"><div class="n" id="fAnalyzed">—</div><div class="l">Analyzed</div></div><div class="stage"><div class="n" id="fShortlisted">—</div><div class="l">Shortlisted</div></div><div class="stage"><div class="n" id="fAi">—</div><div class="l">AI reviewed</div></div><div class="stage"><div class="n" id="fQualified">—</div><div class="l">AI qualified</div></div><div class="stage"><div class="n" id="fFinal">—</div><div class="l">Final qualified</div></div></div></div>

      <div class="card span4"><div class="kicker">Market</div><div class="metric"><span>Current regime</span><b id="regime">—</b></div><div class="metric"><span>Technical shortlist</span><b id="shortlist">—</b></div><div class="metric"><span>LONG / SHORT</span><b id="longShort">—</b></div><div class="metric"><span>Movement W / R / C</span><b id="movementStages">—</b></div><div class="metric"><span>Last scan</span><b id="lastScan">—</b></div></div>
      <div class="card span4"><div class="kicker">Rejection gates</div><div class="metric"><span>Margin</span><b id="marginReject">—</b></div><div class="metric"><span>Execution</span><b id="executionReject">—</b></div><div class="metric"><span>Target quality</span><b id="targetReject">—</b></div><div class="metric"><span>Economic</span><b id="economicReject">—</b></div></div>
      <div class="card span4"><div class="kicker">Current system</div><div class="metric"><span>Active trades</span><b id="activeTrades">—</b></div><div class="metric"><span>Pending setups</span><b id="pendingSetups">—</b></div><div class="metric"><span>Live order intents</span><b id="liveOrders">—</b></div><div class="metric"><span>Learning AI calls</span><b class="good">0</b></div></div>

      <div class="card span6"><div class="kicker">Learning value</div><div class="metric"><span>Decisions tracked</span><b id="tracked">—</b></div><div class="metric"><span>Outcome observations</span><b id="observations">—</b></div><div class="metric"><span>Movement signals / observations</span><b id="movementLearning">—</b></div><div class="metric"><span>Evaluated decisions</span><b id="evaluated">—</b></div><div class="metric"><span>Correct avoids</span><b class="good" id="correctAvoids">—</b></div><div class="metric"><span>Missed profitable opportunities</span><b class="warn" id="missed">—</b></div></div>
      <div class="card span6"><div class="kicker">Trading outcomes</div><div class="metric"><span>Closed financial trades</span><b id="closedTrades">—</b></div><div class="metric"><span>Profitable</span><b class="good" id="profitCount">—</b></div><div class="metric"><span>Losing</span><b class="bad" id="lossCount">—</b></div><div class="metric"><span>Breakeven</span><b id="breakeven">—</b></div><div class="metric"><span>Recorded costs</span><b id="costs">—</b></div></div>

      <div class="card span12"><div class="kicker">OHM is building value</div><div class="valuebox" id="valueText">Waiting for analytics…</div></div>
      <div class="card span12"><div class="kicker">Decision coverage</div><table class="table"><thead><tr><th>Decision</th><th>Samples</th></tr></thead><tbody id="decisionTable"></tbody></table></div>
    </div>
    <div class="footer" id="generatedAt"></div>
  </div>
</div>
<script>
let scope='today';
const $=id=>document.getElementById(id);
const fmt=n=>Number(n||0).toLocaleString();
const money=n=>{const x=Number(n||0);return (x>=0?'+':'-')+'$'+Math.abs(x).toFixed(2)};
function setScope(s){scope=s;$('todayBtn').classList.toggle('active',s==='today');$('allBtn').classList.toggle('active',s==='all');load();}
async function load(){
 const secret=sessionStorage.getItem('ohmDashboardSecret'); if(!secret){$('unlockBox').style.display='block';$('dashboardBody').style.display='none';return;}
 try{
  const r=await fetch('/api/analytics/summary?scope='+scope,{headers:{'X-Webhook-Secret':secret}});
  if(r.status===401){sessionStorage.removeItem('ohmDashboardSecret');throw new Error('Invalid secret. Please unlock again.');}
  if(!r.ok) throw new Error('Analytics request failed: '+r.status);
  const d=await r.json();$('unlockBox').style.display='none';$('dashboardBody').style.display='block';$('errorBox').style.display='none';render(d);
 }catch(e){$('errorBox').textContent=e.message;$('errorBox').style.display='block';$('unlockBox').style.display='block';}
}
function render(d){const m=d.market,a=d.ai,f=d.funnel,q=d.decisions,t=d.trading,l=d.learning,c=d.current;
 $('pairsAnalyzed').textContent=fmt(m.pairs_analyzed);$('scanCount').textContent=fmt(m.scans)+' scans';$('aiCalls').textContent=fmt(a.chief_calls);$('tokenCount').textContent=fmt(a.total_tokens)+' tokens';
 $('accepted').textContent=fmt(q.accepted);$('rejected').textContent=fmt(q.rejected);$('waitCount').textContent=fmt(q.wait)+' wait/watch';$('netPnl').textContent=money(t.net_pnl);$('netPnl').className='big '+(Number(t.net_pnl)>=0?'good':'bad');$('wins').textContent=fmt(t.profit_count)+' wins';$('losses').textContent=fmt(t.loss_count)+' losses';
 $('fAnalyzed').textContent=fmt(f.analyzed);$('fShortlisted').textContent=fmt(f.shortlisted);$('fAi').textContent=fmt(f.ai_reviewed);$('fQualified').textContent=fmt(f.ai_qualified);$('fFinal').textContent=fmt(f.final_qualified);
 $('regime').textContent=m.latest_regime;$('shortlist').textContent=fmt(m.technical_shortlist);$('longShort').textContent=fmt(m.long_shortlist)+' / '+fmt(m.short_shortlist);$('movementStages').textContent=fmt(m.movement_watch)+' / '+fmt(m.movement_ready)+' / '+fmt(m.movement_confirmed);$('lastScan').textContent=m.last_scan_utc?new Date(m.last_scan_utc).toLocaleString():'—';
 $('marginReject').textContent=fmt(f.margin_rejects);$('executionReject').textContent=fmt(f.execution_rejects);$('targetReject').textContent=fmt(f.target_rejects);$('economicReject').textContent=fmt(f.economic_rejects);
 $('activeTrades').textContent=fmt(c.active_trades);$('pendingSetups').textContent=fmt(c.pending_setups);$('liveOrders').textContent=fmt(c.live_order_intents);
 $('tracked').textContent=fmt(l.decisions_tracked);$('observations').textContent=fmt(l.observations);$('movementLearning').textContent=fmt(l.movement_signals)+' / '+fmt(l.movement_observations);$('evaluated').textContent=fmt(l.evaluated_decisions);$('correctAvoids').textContent=fmt(l.correct_avoids);$('missed').textContent=fmt(l.missed_profitable_opportunities);
 $('closedTrades').textContent=fmt(t.closed_financial_trades);$('profitCount').textContent=fmt(t.profit_count);$('lossCount').textContent=fmt(t.loss_count);$('breakeven').textContent=fmt(t.breakeven_count);$('costs').textContent='$'+Number(t.total_costs||0).toFixed(2);
 $('valueText').innerHTML=`${scope==='today'?'Today':'All time'}, OHM analyzed <b>${fmt(m.pairs_analyzed)}</b> pairs, sent <b>${fmt(a.candidates_reviewed)}</b> finalists to the Chief AI, tracked <b>${fmt(l.decisions_tracked)}</b> decisions, accumulated <b>${fmt(l.observations)}</b> future-outcome observations, and recorded <b>${fmt(l.correct_avoids)}</b> correct avoids so far. Realized net P/L after recorded costs: <b class="${Number(t.net_pnl)>=0?'good':'bad'}">${money(t.net_pnl)}</b>. Routine learning AI calls: <b class="good">0</b>.`;
 const rows=Object.entries(q.by_type||{}).sort((x,y)=>y[1]-x[1]);$('decisionTable').innerHTML=rows.length?rows.map(([k,v])=>`<tr><td>${k}</td><td>${fmt(v)}</td></tr>`).join(''):'<tr><td colspan="2" class="muted">No decisions recorded in this period yet.</td></tr>';
 $('generatedAt').textContent='Generated '+new Date(d.generated_at_utc).toLocaleString()+' • Auto-refreshes every 60 seconds';$('healthText').textContent='Analytics online';
}
$('unlockBtn').onclick=()=>{const v=$('secretInput').value.trim();if(v){sessionStorage.setItem('ohmDashboardSecret',v);$('secretInput').value='';load();}};
$('secretInput').addEventListener('keydown',e=>{if(e.key==='Enter')$('unlockBtn').click()});$('todayBtn').onclick=()=>setScope('today');$('allBtn').onclick=()=>setScope('all');$('refreshBtn').onclick=load;setInterval(load,60000);load();
</script>
</body>
</html>
"""
