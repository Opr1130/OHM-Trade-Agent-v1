from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.services.evolution_dashboard import build_evolution_dashboard
from app.services.operations_analytics import build_operations_summary
from app.services.secret_auth import secret_matches


router = APIRouter()


def _require_secret(value: str | None) -> None:
    if not secret_matches(value, get_settings().webhook_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard secret",
        )


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


@router.get("/api/analytics/evolution")
def evolution_summary(
    scope: str = "30d",
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    _require_secret(x_webhook_secret)
    try:
        return build_evolution_dashboard(scope=scope)
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
<title>OHM · O’Pip Evolution Console</title>
<style>
:root{
 --bg:#f5f7fa;--panel:#ffffff;--ink:#17212b;--muted:#6b7785;--line:#e5eaf0;
 --nav:#101821;--navmuted:#8fa0b1;--accent:#2d6cdf;--good:#1f8f62;--warn:#b7791f;--bad:#c64545;
 --soft:#eef3f8;--shadow:0 10px 30px rgba(16,24,33,.055);--radius:16px;
 font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-size:14px}
button,input{font:inherit}.app{min-height:100vh;display:grid;grid-template-columns:232px minmax(0,1fr)}
.sidebar{background:var(--nav);color:white;padding:22px 16px;position:sticky;top:0;height:100vh}
.brand{padding:4px 10px 22px;border-bottom:1px solid rgba(255,255,255,.08)}.brand b{font-size:18px;letter-spacing:.01em}.brand div{font-size:12px;color:var(--navmuted);margin-top:4px}
.nav{margin-top:18px;display:grid;gap:5px}.nav button{border:0;background:transparent;color:var(--navmuted);text-align:left;padding:10px 12px;border-radius:10px;cursor:pointer;display:flex;gap:10px;align-items:center}
.nav button.active,.nav button:hover{background:rgba(255,255,255,.08);color:#fff}.nav .glyph{width:18px;text-align:center;opacity:.9}
.side-note{position:absolute;left:20px;right:20px;bottom:22px;color:#73879a;font-size:11px;line-height:1.45}
.main{min-width:0}.topbar{height:68px;background:rgba(255,255,255,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 26px;position:sticky;top:0;z-index:5}
.page-title{font-weight:700;font-size:16px}.page-sub{font-size:12px;color:var(--muted);margin-top:2px}.top-actions{display:flex;align-items:center;gap:8px}.scope{display:flex;background:#eef2f6;border-radius:10px;padding:3px}
.scope button{border:0;background:transparent;padding:7px 10px;border-radius:8px;color:var(--muted);cursor:pointer;font-size:12px}.scope button.active{background:#fff;color:var(--ink);box-shadow:0 1px 4px rgba(0,0,0,.08)}
.refresh{border:1px solid var(--line);background:#fff;padding:8px 11px;border-radius:9px;cursor:pointer;color:var(--ink)}
.content{padding:22px 26px 34px;max-width:1540px;margin:0 auto}.section{display:none}.section.active{display:block}.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:16px;min-width:0}.span2{grid-column:span 2}.span3{grid-column:span 3}.span4{grid-column:span 4}.span5{grid-column:span 5}.span6{grid-column:span 6}.span7{grid-column:span 7}.span8{grid-column:span 8}.span12{grid-column:span 12}
.kicker{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:#8994a0;font-weight:700}.metric-title{display:flex;align-items:center;gap:5px;color:var(--muted);font-size:12px}.value{font-size:27px;font-weight:720;margin-top:7px;letter-spacing:-.02em}.value.sm{font-size:21px}.support{color:var(--muted);font-size:12px;margin-top:5px;min-height:17px}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}.neutral{color:var(--ink)}
.tip{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;border-radius:50%;background:#edf1f5;color:#778594;font-size:10px;font-weight:700;cursor:help;position:relative}
.tip:hover:after{content:attr(data-tip);position:absolute;z-index:20;left:20px;top:-6px;width:260px;background:#111923;color:#eef3f8;padding:9px 10px;border-radius:9px;font-size:11px;line-height:1.45;font-weight:400;box-shadow:0 8px 24px rgba(0,0,0,.2)}
.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.card-title{font-weight:700;font-size:14px}.card-desc{color:var(--muted);font-size:11px;margin-top:3px;line-height:1.45}
.chart{height:245px;width:100%;position:relative}.chart.tall{height:320px}.chart svg{width:100%;height:100%;overflow:visible}.axis{stroke:#dfe5eb;stroke-width:1}.gridline{stroke:#eef2f5;stroke-width:1}.line-primary{fill:none;stroke:#2d6cdf;stroke-width:2.2}.line-secondary{fill:none;stroke:#5b9b7b;stroke-width:2}.line-tertiary{fill:none;stroke:#b88743;stroke-width:2}.dot-primary{fill:#2d6cdf}.chart-label{fill:#7d8996;font-size:10px}.empty{height:100%;display:grid;place-items:center;color:#8b96a1;font-size:12px;text-align:center;padding:20px}
.legend{display:flex;gap:15px;flex-wrap:wrap;font-size:11px;color:var(--muted)}.legend span:before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:#2d6cdf;margin-right:6px}.legend span:nth-child(2):before{background:#5b9b7b}.legend span:nth-child(3):before{background:#b88743}
.funnel{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:7px;align-items:stretch}.funnel-stage{background:#f7f9fb;border:1px solid var(--line);border-radius:12px;padding:12px 8px;text-align:center;position:relative}.funnel-stage:not(:last-child):after{content:"›";position:absolute;right:-7px;top:50%;transform:translateY(-50%);color:#a9b3bd;background:var(--panel);font-size:17px;z-index:1}.funnel-n{font-size:23px;font-weight:720}.funnel-l{font-size:10px;color:var(--muted);margin-top:3px}.funnel-c{font-size:10px;color:#8795a3;margin-top:5px}
.health-list{display:grid;gap:8px}.health-row{display:grid;grid-template-columns:16px 140px 76px 1fr;gap:8px;align-items:center;border-bottom:1px solid #f0f3f6;padding:8px 0}.health-row:last-child{border-bottom:0}.status-dot{width:8px;height:8px;border-radius:50%;background:#aab5bf}.status-dot.ok{background:var(--good)}.status-dot.attn{background:var(--warn)}.status-dot.bad{background:var(--bad)}.status-pill{font-size:10px;font-weight:700;padding:4px 6px;border-radius:999px;background:#f1f4f7;text-align:center}
.comp-table{width:100%;border-collapse:collapse}.comp-table th,.comp-table td{padding:9px 8px;border-bottom:1px solid #edf1f4;text-align:left;font-size:12px}.comp-table th{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#87939e}.comp-table td.num{text-align:right;font-variant-numeric:tabular-nums}
.delta{font-weight:700}.delta.up{color:var(--good)}.delta.down{color:var(--bad)}.delta.flat{color:var(--muted)}.baseline{font-size:10px;padding:3px 6px;border-radius:999px;background:#f5efe3;color:#986b25;font-weight:700}
.fail-row{display:grid;grid-template-columns:minmax(120px,1.6fr) 70px 70px 95px;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid #edf1f4;font-size:12px}.fail-row:last-child{border-bottom:0}.tag{font-size:10px;border-radius:999px;padding:4px 7px;font-weight:700;text-align:center}.tag.ERADICATED{background:#e7f5ef;color:#177953}.tag.IMPROVING{background:#edf5ff;color:#2d65a7}.tag.REGRESSED{background:#fdecec;color:#b23a3a}.tag.NEW{background:#fff4df;color:#98641f}.tag.RECURRING{background:#eef1f4;color:#5f6d79}
.bar-row{display:grid;grid-template-columns:minmax(150px,1.8fr) minmax(110px,3fr) 58px 70px;gap:10px;align-items:center;padding:8px 0;font-size:11px}.bar-track{height:8px;background:#edf1f5;border-radius:99px;overflow:hidden}.bar-fill{height:100%;background:#5f7fa8;border-radius:99px}.bar-pos{background:#5b9b7b}.bar-neg{background:#c96a6a}
.table-wrap{overflow:auto;max-height:430px}.data-table{width:100%;border-collapse:collapse;white-space:nowrap}.data-table th,.data-table td{padding:9px 10px;border-bottom:1px solid #edf1f4;font-size:11px;text-align:left}.data-table th{position:sticky;top:0;background:#fff;color:#7d8996;text-transform:uppercase;letter-spacing:.05em;font-size:9px;z-index:1}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px}.infobox{background:#f7f9fb;border:1px solid var(--line);border-radius:12px;padding:12px;line-height:1.5;color:#5f6d79;font-size:11px}.hero-callout{padding:17px 18px;border-radius:14px;background:linear-gradient(120deg,#f3f7fb,#f8fafc);border:1px solid #e1e8ef}.hero-callout strong{font-size:15px}.hero-callout p{margin:5px 0 0;color:var(--muted);font-size:12px;line-height:1.5}
.unlock{max-width:540px;margin:90px auto;background:#fff;border:1px solid var(--line);border-radius:18px;padding:24px;box-shadow:var(--shadow)}.unlock h2{margin:0 0 6px}.unlock p{color:var(--muted);font-size:12px;line-height:1.5}.unlock-line{display:flex;gap:8px;margin-top:15px}.unlock input{flex:1;border:1px solid #dfe5eb;border-radius:10px;padding:10px 12px}.unlock button{border:0;background:#17212b;color:white;border-radius:10px;padding:10px 15px;cursor:pointer}.error{display:none;background:#fff1f1;border:1px solid #f0c9c9;color:#a63a3a;border-radius:10px;padding:10px 12px;margin-bottom:14px;font-size:12px}
.small-note{font-size:10px;color:#8a96a1;line-height:1.45}.statline{display:flex;gap:18px;flex-wrap:wrap}.statline div{min-width:110px}.statline b{display:block;font-size:19px;margin-top:2px}
@media(max-width:1180px){.app{grid-template-columns:76px minmax(0,1fr)}.brand div,.nav .label,.side-note{display:none}.brand{text-align:center;padding-left:0;padding-right:0}.nav button{justify-content:center}.span2{grid-column:span 4}.span3{grid-column:span 6}.span4,.span5,.span6,.span7,.span8{grid-column:span 12}.funnel{grid-template-columns:repeat(4,1fr)}}
@media(max-width:720px){.app{display:block}.sidebar{position:static;height:auto;padding:10px}.brand,.side-note{display:none}.nav{margin:0;display:flex;overflow:auto}.nav button{min-width:48px}.topbar{position:static;height:auto;align-items:flex-start;gap:10px;padding:14px;flex-direction:column}.content{padding:14px}.span2,.span3,.span4,.span5,.span6,.span7,.span8,.span12{grid-column:span 12}.funnel{grid-template-columns:repeat(2,1fr)}.health-row{grid-template-columns:16px 100px 70px 1fr}}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"><b>OHM · O’Pip</b><div>Evolution console</div><div style="font-size:9px;margin-top:6px;opacity:.62">OHM AI — Operations & Learning</div></div>
    <nav class="nav">
      <button class="active" data-page="overview"><span class="glyph">◫</span><span class="label">Overview</span></button>
      <button data-page="evolution"><span class="glyph">↗</span><span class="label">Evolution</span></button>
      <button data-page="monitor"><span class="glyph">◉</span><span class="label">Intelligence Monitor</span></button>
      <button data-page="funnel"><span class="glyph">▽</span><span class="label">Signal Funnel</span></button>
      <button data-page="paper"><span class="glyph">⌁</span><span class="label">Paper Trades</span></button>
      <button data-page="signals"><span class="glyph">⌗</span><span class="label">Signal Intelligence</span></button>
      <button data-page="journeys"><span class="glyph">⋯</span><span class="label">Journeys</span></button>
      <button data-page="health"><span class="glyph">✓</span><span class="label">Learning Health</span></button>
    </nav>
    <div class="side-note">Read-only analytics.<br/>No Kraken or Freqtrade mutation authority.</div>
  </aside>
  <main class="main">
    <header class="topbar">
      <div><div class="page-title" id="pageTitle">Executive overview</div><div class="page-sub" id="pageSub">Intelligence → signal → paper execution → learning</div></div>
      <div class="top-actions">
        <div class="scope">
          <button data-scope="today">Today</button><button data-scope="7d">7D</button><button class="active" data-scope="30d">30D</button><button data-scope="all">All</button>
        </div>
        <button class="refresh" id="refreshBtn">Refresh</button>
      </div>
    </header>
    <div class="content">
      <div class="error" id="errorBox"></div>
      <div class="unlock" id="unlockBox">
        <h2>Unlock analytics</h2>
        <p>Use the existing OHM operator secret. It stays in this browser tab and is sent only as a request header, never in the URL.</p>
        <div class="unlock-line"><input id="secretInput" type="password" placeholder="OHM operator secret"/><button id="unlockBtn">Unlock</button></div>
      </div>
      <div id="dashboardBody" style="display:none">
        <section class="section active" id="page-overview">
          <div class="grid">
            <div class="card span2"><div class="metric-title">Paper engine <span class="tip" data-tip="Authoritative Freqtrade dry-run worker health. The OHM internal simulator is not counted here.">?</span></div><div class="value sm" id="kPaper">—</div><div class="support" id="kPaperSub">—</div></div>
            <div class="card span2"><div class="metric-title">Paper win rate <span class="tip" data-tip="Profitable closed Freqtrade paper trades divided by all mature closed paper trades in this window.">?</span></div><div class="value" id="kWin">—</div><div class="support" id="kWinSub">—</div></div>
            <div class="card span2"><div class="metric-title">Early → signal <span class="tip" data-tip="Share of intelligence journeys with Early Watch / Early Mover evidence that later produced a qualified signal.">?</span></div><div class="value" id="kEarly">—</div><div class="support">Conversion quality</div></div>
            <div class="card span2"><div class="metric-title">Avg return <span class="tip" data-tip="Average closed-trade return percentage. This compares return ratios and does not combine USD and USDT absolute P/L.">?</span></div><div class="value" id="kReturn">—</div><div class="support">Closed paper trades</div></div>
            <div class="card span2"><div class="metric-title">Repeated failure <span class="tip" data-tip="Share of explicit failure events whose failure family had already been observed earlier. Lower is better.">?</span></div><div class="value" id="kFailure">—</div><div class="support">Lower is better</div></div>
            <div class="card span2"><div class="metric-title">Learning health <span class="tip" data-tip="Evidence pipeline status based on outbox integrity and dead-letter activity.">?</span></div><div class="value sm" id="kHealth">—</div><div class="support">Evidence trust</div></div>

            <div class="card span8">
              <div class="card-head"><div><div class="card-title">Intelligence effectiveness trend</div><div class="card-desc">Daily paper win rate and Early-Watch-associated win rate. Empty periods stay empty; no synthetic interpolation.</div></div><div class="legend"><span>Paper win rate</span><span>Early-watch win rate</span></div></div>
              <div class="chart tall" id="trendChart"></div>
            </div>
            <div class="card span4">
              <div class="card-head"><div><div class="card-title">Evolution scorecard</div><div class="card-desc">Recent 7 days versus the preceding 7 days.</div></div><span class="tip" data-tip="Metrics remain marked Baseline building until both comparison windows have enough mature paper outcomes.">?</span></div>
              <div id="evolutionMini"></div>
            </div>

            <div class="card span12">
              <div class="card-head"><div><div class="card-title">Signal-to-outcome funnel</div><div class="card-desc">Where opportunities are progressing—and where they are being intentionally filtered out.</div></div></div>
              <div class="funnel" id="overviewFunnel"></div>
            </div>

            <div class="card span7">
              <div class="card-head"><div><div class="card-title">Failure eradication</div><div class="card-desc">Explicit rejection/exit families. Recent 7 days versus prior 7 days.</div></div><span class="tip" data-tip="This is recurrence tracking, not causal attribution. A family is Eradicated when it appeared in the prior 7 days and is absent in the recent 7 days.">?</span></div>
              <div id="failureMini"></div>
            </div>
            <div class="card span5">
              <div class="card-head"><div><div class="card-title">Intelligence source health</div><div class="card-desc">Freshness and availability of the intelligence stack.</div></div></div>
              <div class="health-list" id="sourceHealthMini"></div>
            </div>
          </div>
        </section>

        <section class="section" id="page-evolution">
          <div class="grid">
            <div class="card span12 hero-callout"><strong>Is OHM actually getting better?</strong><p>This view separates intelligence improvement, signal conversion, trading outcomes and recurring failures. Improvement is not promoted from this page; it is evidence for later governed champion/challenger decisions.</p></div>
            <div class="card span7"><div class="card-head"><div><div class="card-title">Evolution trend</div><div class="card-desc">Daily paper win rate versus Early-Watch-associated win rate. Average return stays in the scorecard so unlike scales are not mixed on one axis.</div></div></div><div class="chart tall" id="evolutionTrend"></div></div>
            <div class="card span5"><div class="card-head"><div><div class="card-title">7-day comparison</div><div class="card-desc">Measured change, with directionality translated for operator review.</div></div></div><div id="evolutionTable"></div></div>
            <div class="card span7"><div class="card-head"><div><div class="card-title">Failure families</div><div class="card-desc">Are known weaknesses recurring, improving, disappearing or regressing?</div></div></div><div id="failureFull"></div></div>
            <div class="card span5"><div class="card-head"><div><div class="card-title">Attribution readiness</div><div class="card-desc">Version tags required to prove which layer caused improvement.</div></div></div><div id="versionReadiness"></div></div>
          </div>
        </section>

        <section class="section" id="page-monitor">
          <div class="grid">
            <div class="card span12"><div class="card-head"><div><div class="card-title">Live intelligence stack</div><div class="card-desc">What OHM can see now, how fresh it is, and where evidence is degraded.</div></div></div><div class="health-list" id="sourceHealthFull"></div></div>
            <div class="card span4"><div class="kicker">Market scan</div><div class="statline"><div><span class="support">Pairs analyzed today</span><b id="mPairs">—</b></div><div><span class="support">Technical shortlist</span><b id="mShort">—</b></div><div><span class="support">Regime</span><b id="mRegime">—</b></div></div></div>
            <div class="card span4"><div class="kicker">Chief AI</div><div class="statline"><div><span class="support">Calls today</span><b id="mAiCalls">—</b></div><div><span class="support">Candidates</span><b id="mAiCandidates">—</b></div><div><span class="support">Tokens</span><b id="mTokens">—</b></div></div></div>
            <div class="card span4"><div class="kicker">Movement states</div><div class="statline"><div><span class="support">Watch</span><b id="mWatch">—</b></div><div><span class="support">Ready</span><b id="mReady">—</b></div><div><span class="support">Confirmed</span><b id="mConfirmed">—</b></div></div></div>
            <div class="card span12"><div class="card-head"><div><div class="card-title">Live Early Watch candidates</div><div class="card-desc">Latest intelligence observation per active journey in the last six hours, ranked by opportunity score. This is observational and cannot promote a trade.</div></div><span class="tip" data-tip="Scores come from the persisted Early Watch / Early Mover evidence. A high score is not a trade instruction; use the journey and paper outcome views to evaluate predictive value.">?</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Symbol</th><th>Stage</th><th>Pattern</th><th>Opportunity</th><th>Explosion</th><th>Tradeability</th><th>Relative strength</th><th>Persistence</th><th>Alert</th><th>Age</th></tr></thead><tbody id="liveCandidates"></tbody></table></div></div>
          </div>
        </section>

        <section class="section" id="page-funnel">
          <div class="grid">
            <div class="card span12"><div class="card-head"><div><div class="card-title">Intelligence → execution funnel</div><div class="card-desc">Counts and stage-to-stage conversion for the selected analysis window.</div></div></div><div class="funnel" id="fullFunnel"></div></div>
            <div class="card span6"><div class="card-head"><div><div class="card-title">Conversion profile</div><div class="card-desc">Stage volume normalized to the largest funnel stage.</div></div></div><div id="funnelBars"></div></div>
            <div class="card span6"><div class="card-head"><div><div class="card-title">Interpretation</div><div class="card-desc">Operator-focused translation of the current funnel.</div></div></div><div class="infobox" id="funnelInterpretation">—</div></div>
          </div>
        </section>

        <section class="section" id="page-paper">
          <div class="grid">
            <div class="card span8"><div class="card-head"><div><div class="card-title">Realized paper P/L curves</div><div class="card-desc">USD and USDT use independent panels so absolute P/L is never visually combined or implied to be exactly interchangeable.</div></div></div><div id="equityChart"><div class="kicker" style="margin:2px 0 4px">USD cumulative P/L</div><div class="chart" id="equityUsdChart"></div><div class="kicker" style="margin:10px 0 4px">USDT cumulative P/L</div><div class="chart" id="equityUsdtChart"></div></div></div>
            <div class="card span4"><div class="card-head"><div><div class="card-title">Paper engine</div><div class="card-desc">Authoritative Freqtrade dry-run runtime state.</div></div></div><div id="paperStatus"></div></div>
            <div class="card span12"><div class="card-head"><div><div class="card-title">Open paper trades</div><div class="card-desc">Read directly from Freqtrade SQLite in read-only mode.</div></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Pair</th><th>Signal</th><th>Opened</th><th>Requested entry</th><th>Fill</th><th>Stake</th><th>Currency</th></tr></thead><tbody id="openTrades"></tbody></table></div></div>
            <div class="card span12"><div class="card-head"><div><div class="card-title">Recent closed trades</div><div class="card-desc">Realized dry-run outcomes used by O’Pip learning.</div></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Pair</th><th>Closed</th><th>Entry</th><th>Exit</th><th>Return</th><th>Net P/L</th><th>Exit reason</th></tr></thead><tbody id="closedTrades"></tbody></table></div></div>
          </div>
        </section>

        <section class="section" id="page-signals">
          <div class="grid">
            <div class="card span8"><div class="card-head"><div><div class="card-title">Pattern / stage performance</div><div class="card-desc">Average realized paper return by Early Watch stage and pattern; sample size is shown beside every segment.</div></div></div><div id="patternBars"></div></div>
            <div class="card span4"><div class="card-head"><div><div class="card-title">Signal quality principles</div><div class="card-desc">How to read this panel safely.</div></div></div><div class="infobox">Prefer segments with both positive expectancy and enough mature samples. A high win rate on one or two trades is not evidence of durable edge. These analytics remain observational and cannot alter production ranking or thresholds.</div></div>
          </div>
        </section>

        <section class="section" id="page-journeys">
          <div class="grid"><div class="card span12"><div class="card-head"><div><div class="card-title">Recent intelligence journeys</div><div class="card-desc">One continuous trace from early detection through signal, admission and realized outcome.</div></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Symbol</th><th>Status</th><th>Early stage</th><th>Pattern</th><th>Early alert</th><th>Admission</th><th>Return</th><th>Exit</th><th>Journey</th></tr></thead><tbody id="journeyRows"></tbody></table></div></div></div>
        </section>

        <section class="section" id="page-health">
          <div class="grid">
            <div class="card span3"><div class="metric-title">Journey events</div><div class="value" id="hEvents">—</div><div class="support">Selected window</div></div>
            <div class="card span3"><div class="metric-title">Outbox backlog</div><div class="value" id="hBacklog">—</div><div class="support">Pending evidence rows</div></div>
            <div class="card span3"><div class="metric-title">Dead letters</div><div class="value" id="hDead">—</div><div class="support">Needs review if non-zero</div></div>
            <div class="card span3"><div class="metric-title">Outcome dedup</div><div class="value sm" id="hDedup">—</div><div class="support">Durable integrity control</div></div>
            <div class="card span7"><div class="card-head"><div><div class="card-title">Learning trust controls</div><div class="card-desc">Evidence integrity checks that determine whether dashboard conclusions can be trusted.</div></div></div><div id="healthControls"></div></div>
            <div class="card span5"><div class="card-head"><div><div class="card-title">Known instrumentation gaps</div><div class="card-desc">Shown explicitly instead of hidden behind polished charts.</div></div></div><div id="healthNotes"></div></div>
          </div>
        </section>
        <div class="small-note" id="generatedAt" style="margin-top:16px"></div>
      </div>
    </div>
  </main>
</div>
<script>
let scope='30d', data=null;
const $=id=>document.getElementById(id);
const fmt=n=>n===null||n===undefined?'—':Number(n).toLocaleString();
const pct=n=>n===null||n===undefined?'—':Number(n).toFixed(1)+'%';
const num=n=>n===null||n===undefined?'—':Number(n).toFixed(2);
const money=(n,c='USD')=>n===null||n===undefined?'—':(Number(n)>=0?'+':'-')+Math.abs(Number(n)).toFixed(2)+' '+c;
const esc=s=>String(s??'—').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function clsStatus(s){s=String(s||'').toUpperCase();return s==='OK'||s==='HEALTHY'?'ok':s==='STALE'||s==='PARTIAL'||s.includes('BASELINE')?'attn':s==='NOT_READY'||s==='UNAVAILABLE'||s==='ERROR'?'bad':'attn'}
function metricClass(v,higher=true){if(v===null||v===undefined)return'neutral';if(Number(v)>0)return higher?'good':'bad';if(Number(v)<0)return higher?'bad':'good';return'neutral'}
function setPage(page){
 document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
 document.querySelectorAll('.section').forEach(s=>s.classList.toggle('active',s.id==='page-'+page));
 const meta={
  overview:['Executive overview','Intelligence → signal → paper execution → learning'],
  evolution:['Evolution','Is intelligence improvement translating into better trading?'],
  monitor:['Intelligence monitor','Live source health, scan state and AI evidence'],
  funnel:['Signal funnel','Where opportunity quality advances or drops out'],
  paper:['Paper trades','Authoritative Freqtrade dry-run execution evidence'],
  signals:['Signal intelligence','Patterns, stages and realized paper performance'],
  journeys:['Journeys','Forensic lifecycle from early watch to outcome'],
  health:['Learning health','Can we trust the evidence feeding O’Pip?']
 }[page];
 $('pageTitle').textContent=meta[0];$('pageSub').textContent=meta[1];
 if(data)renderVisibleCharts(page);
}
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>setPage(b.dataset.page));
document.querySelectorAll('[data-scope]').forEach(b=>b.onclick=()=>{scope=b.dataset.scope;document.querySelectorAll('[data-scope]').forEach(x=>x.classList.toggle('active',x===b));load();});
function svgLine(containerId, rows, series, ySuffix='%'){
 const el=$(containerId);if(!el)return;
 const clean=rows.filter(r=>series.some(s=>r[s.key]!==null&&r[s.key]!==undefined));
 if(!clean.length){el.innerHTML='<div class="empty">No mature observations in this window yet.<br/>The chart will appear as evidence accumulates.</div>';return;}
 const W=Math.max(520,el.clientWidth||800),H=Math.max(220,el.clientHeight||300),L=48,R=18,T=18,B=34;
 const vals=[];clean.forEach(r=>series.forEach(s=>{if(r[s.key]!==null&&r[s.key]!==undefined)vals.push(Number(r[s.key]))}));
 let min=Math.min(...vals),max=Math.max(...vals);if(min===max){min-=1;max+=1} if(ySuffix==='%'){min=Math.min(0,min);max=Math.max(100,max)}
 const x=i=>L+(clean.length===1?(W-L-R)/2:i*(W-L-R)/(clean.length-1));
 const y=v=>T+(max-v)*(H-T-B)/(max-min);
 let g='';for(let i=0;i<5;i++){const yy=T+i*(H-T-B)/4;const val=max-i*(max-min)/4;g+=`<line class="gridline" x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}"/><text class="chart-label" x="0" y="${yy+3}">${val.toFixed(ySuffix==='%'?0:1)}${ySuffix}</text>`}
 let paths='';series.forEach((s,si)=>{let d='',started=false,pts='';clean.forEach((r,i)=>{const v=r[s.key];if(v===null||v===undefined){started=false;return}d+=(started?' L ':'M ')+x(i)+' '+y(Number(v));started=true;pts+=`<circle cx="${x(i)}" cy="${y(Number(v))}" r="2.7" class="${si===0?'dot-primary':''}" style="${si===1?'fill:#5b9b7b':si===2?'fill:#b88743':''}"><title>${esc(r.label||'')} · ${Number(v).toFixed(2)}${ySuffix}</title></circle>`});paths+=`<path class="${si===0?'line-primary':si===1?'line-secondary':'line-tertiary'}" d="${d}"/>${pts}`});
 const labels=clean.map((r,i)=>{if(clean.length>8&&i%Math.ceil(clean.length/7)!==0&&i!==clean.length-1)return'';return`<text class="chart-label" text-anchor="middle" x="${x(i)}" y="${H-8}">${esc(r.label||'')}</text>`}).join('');
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}">${g}<line class="axis" x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}"/>${paths}${labels}</svg>`;
}
function bars(containerId, rows, valueKey='avg_return_pct'){
 const el=$(containerId);if(!el)return;if(!rows.length){el.innerHTML='<div class="empty">No mature segment outcomes yet.</div>';return}
 const max=Math.max(...rows.map(r=>Math.abs(Number(r[valueKey]||0))),.01);
 el.innerHTML=rows.map(r=>{const v=Number(r[valueKey]||0),w=Math.min(100,Math.abs(v)/max*100);return`<div class="bar-row"><div title="${esc(r.segment||r.stage)}">${esc(r.segment||r.stage)}</div><div class="bar-track"><div class="bar-fill ${v>=0?'bar-pos':'bar-neg'}" style="width:${w}%"></div></div><div>${v.toFixed(2)}%</div><div class="support">n=${fmt(r.samples??r.count)}</div></div>`}).join('');
}
function countBars(containerId, rows){
 const el=$(containerId);if(!el)return;if(!rows.length){el.innerHTML='<div class="empty">No funnel observations yet.</div>';return}
 const max=Math.max(...rows.map(r=>Number(r.count||0)),1);
 el.innerHTML=rows.map(r=>{const v=Number(r.count||0),w=Math.min(100,v/max*100);return`<div class="bar-row"><div>${esc(r.stage)}</div><div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div><div>${fmt(v)}</div><div class="support">${r.conversion_from_prior_pct===null?'start':pct(r.conversion_from_prior_pct)}</div></div>`}).join('');
}
function renderFunnel(id){
 const rows=data.funnel.stages||[];$(id).innerHTML=rows.map(r=>`<div class="funnel-stage"><div class="funnel-n">${fmt(r.count)}</div><div class="funnel-l">${esc(r.stage)}</div><div class="funnel-c">${r.conversion_from_prior_pct===null?'Start':pct(r.conversion_from_prior_pct)+' of prior'}</div></div>`).join('');
}
function renderHealth(id){
 $(id).innerHTML=(data.intelligence_monitor.sources||[]).map(r=>`<div class="health-row"><span class="status-dot ${clsStatus(r.status)}"></span><b>${esc(r.source)}</b><span class="status-pill">${esc(r.status)}</span><span class="support">${esc(r.detail)}</span></div>`).join('');
}
function renderEvolutionTable(id){
 const rows=data.evolution.metrics||[];$(id).innerHTML=`<table class="comp-table"><thead><tr><th>Metric</th><th style="text-align:right">Prior</th><th style="text-align:right">Recent</th><th style="text-align:right">Δ</th></tr></thead><tbody>${rows.map(r=>{let d=r.delta,good=d===null?null:(r.direction==='higher'?d>0:d<0);return`<tr><td>${esc(r.metric)} ${r.status!=='MEASURED'?'<span class="baseline">Baseline</span>':''}</td><td class="num">${r.prior===null?'—':Number(r.prior).toFixed(1)}</td><td class="num">${r.recent===null?'—':Number(r.recent).toFixed(1)}</td><td class="num delta ${d===null?'flat':good?'up':d===0?'flat':'down'}">${d===null?'—':(d>0?'+':'')+Number(d).toFixed(1)}</td></tr>`}).join('')}</tbody></table>`;
}
function renderFailures(id,limit=12){
 const rows=(data.failures.families_detail||[]).slice(0,limit);$(id).innerHTML=rows.length?rows.map(r=>`<div class="fail-row"><span>${esc(r.family)}</span><span>${fmt(r.prior_7d)}</span><span>${fmt(r.recent_7d)}</span><span class="tag ${esc(r.status)}">${esc(r.status)}</span></div>`).join(''):'<div class="empty">No explicit paper failure events in this window.</div>';
}
function render(){
 const e=data.executive;
 $('kPaper').textContent=e.paper_status||'—';$('kPaper').className='value sm '+(e.paper_status==='OK'?'good':'warn');$('kPaperSub').textContent=(e.open_paper_trades||0)+' open · '+(e.closed_paper_trades||0)+' closed · mode '+e.paper_mode;
 $('kWin').textContent=pct(e.paper_win_rate_pct);$('kWinSub').textContent=(data.paper.trades.closed||[]).length+' recent rows available';$('kEarly').textContent=pct(e.early_watch_to_signal_pct);$('kReturn').textContent=pct(e.avg_return_pct);$('kReturn').className='value '+metricClass(e.avg_return_pct,true);
 $('kFailure').textContent=pct(e.repeated_failure_rate_pct);$('kFailure').className='value '+metricClass(e.repeated_failure_rate_pct,false);$('kHealth').textContent=e.learning_health;$('kHealth').className='value sm '+(e.learning_health==='OK'?'good':'warn');
 renderEvolutionTable('evolutionMini');renderEvolutionTable('evolutionTable');renderFunnel('overviewFunnel');renderFunnel('fullFunnel');renderFailures('failureMini',6);renderFailures('failureFull',12);renderHealth('sourceHealthMini');renderHealth('sourceHealthFull');
 const m=data.intelligence_monitor.market||{},a=data.intelligence_monitor.ai||{};$('mPairs').textContent=fmt(m.pairs_analyzed);$('mShort').textContent=fmt(m.technical_shortlist);$('mRegime').textContent=m.latest_regime||'—';$('mAiCalls').textContent=fmt(a.chief_calls);$('mAiCandidates').textContent=fmt(a.candidates_reviewed);$('mTokens').textContent=fmt(a.total_tokens);$('mWatch').textContent=fmt(m.movement_watch);$('mReady').textContent=fmt(m.movement_ready);$('mConfirmed').textContent=fmt(m.movement_confirmed);
 $('liveCandidates').innerHTML=(data.intelligence_monitor.live_candidates||[]).map(r=>`<tr><td><b>${esc(r.symbol)}</b></td><td>${esc(r.stage)}</td><td>${esc(r.pattern)}</td><td>${num(r.opportunity_score)}</td><td>${num(r.explosion_potential_score)}</td><td>${num(r.tradeability_score)}</td><td>${num(r.relative_strength_score)}</td><td>${num(r.persistence_score)}</td><td>${r.delivered?'Delivered':esc(r.delivery_action)}</td><td>${r.age_minutes===null?'—':Number(r.age_minutes).toFixed(0)+'m'}</td></tr>`).join('')||'<tr><td colspan="10">No active Early Watch observations in the last six hours.</td></tr>';
 countBars('funnelBars',data.funnel.stages||[]);
 $('funnelInterpretation').innerHTML=`Early detection → qualified conversion: <b>${pct(data.funnel.early_to_signal_pct)}</b>.<br/>Paper requested → closed conversion: <b>${pct(data.funnel.requested_to_closed_pct)}</b>.<br/>Closed paper win rate: <b>${pct(data.funnel.closed_win_rate_pct)}</b>.<br/><br/>A falling funnel is not automatically bad: intentional rejection is valuable when rejected setups subsequently underperform. Use the Evolution and Learning Health views before changing thresholds.`;
 const ps=data.paper.status||{}, pnl=ps.realized_pnl_by_currency||{};$('paperStatus').innerHTML=`<div class="metric"><span>Mode</span><b>${esc(data.paper.control.enabled?'ON':'OFF')}</b></div><div class="metric"><span>Worker status</span><b class="${ps.status==='OK'?'good':'warn'}">${esc(ps.status)}</b></div><div class="metric"><span>Open / closed</span><b>${fmt(ps.open_trades)} / ${fmt(ps.closed_trades)}</b></div><div class="metric"><span>USD realized</span><b>${money(pnl.USD||0,'USD')}</b></div><div class="metric"><span>USDT realized</span><b>${money(pnl.USDT||0,'USDT')}</b></div>`;
 $('openTrades').innerHTML=(data.paper.trades.open||[]).map(r=>`<tr><td>${esc(r.pair)}</td><td class="mono">${esc(r.signal_id)}</td><td>${esc(r.open_date)}</td><td>${num(r.requested_entry)}</td><td>${num(r.open_rate)}</td><td>${num(r.stake_amount)}</td><td>${esc(r.currency)}</td></tr>`).join('')||'<tr><td colspan="7">No open paper trades.</td></tr>';
 $('closedTrades').innerHTML=(data.paper.trades.closed||[]).map(r=>`<tr><td>${esc(r.pair)}</td><td>${esc(r.close_date)}</td><td>${num(r.open_rate)}</td><td>${num(r.close_rate)}</td><td class="${metricClass(r.return_pct,true)}">${pct(r.return_pct)}</td><td class="${metricClass(r.net_pnl,true)}">${money(r.net_pnl,r.currency)}</td><td>${esc(r.exit_reason)}</td></tr>`).join('')||'<tr><td colspan="7">No closed paper trades yet.</td></tr>';
 bars('patternBars',data.signal_intelligence.pattern_performance||[]);
 $('journeyRows').innerHTML=(data.journeys||[]).map(r=>`<tr><td><b>${esc(r.symbol)}</b></td><td>${esc(r.status)}</td><td>${esc(r.early_stage)}</td><td>${esc(r.pattern)}</td><td>${r.early_delivered?'Delivered':'No'}</td><td>${esc(r.admission_reason)}</td><td class="${metricClass(r.return_pct,true)}">${pct(r.return_pct)}</td><td>${esc(r.exit_reason)}</td><td class="mono">${esc(r.journey_id)}</td></tr>`).join('')||'<tr><td colspan="9">No intelligence journeys in this window.</td></tr>';
 const h=data.learning_health;$('hEvents').textContent=fmt(h.journey_events);$('hBacklog').textContent=fmt(h.outbox?.backlog_rows);$('hDead').textContent=fmt(h.dead_letter_count);$('hDead').className='value '+(Number(h.dead_letter_count||0)===0?'good':'bad');$('hDedup').textContent=h.freqtrade_dedup||'—';
 $('healthControls').innerHTML=`<div class="metric"><span>Outbox status</span><b>${esc(h.outbox?.status)}</b></div><div class="metric"><span>Processed through line</span><b>${fmt(h.outbox?.processed_through_line)}</b></div><div class="metric"><span>Total outbox rows</span><b>${fmt(h.outbox?.total_rows)}</b></div><div class="metric"><span>Measurement baseline</span><b class="mono">${esc(h.measurement_baseline)}</b></div><div class="metric"><span>Version-tag coverage</span><b>${pct(h.version_attribution?.coverage_pct)}</b></div>`;
 $('healthNotes').innerHTML=(h.notes||[]).map(n=>`<div class="infobox" style="margin-bottom:8px">${esc(n)}</div>`).join('');
 const va=data.evolution.version_attribution||{},v=va.current||{};$('versionReadiness').innerHTML=Object.entries(v).map(([k,val])=>`<div class="metric"><span>${esc(k.replaceAll('_',' '))}</span><b class="mono">${esc(val)}</b></div>`).join('')+`<div class="metric"><span>Tagged evidence coverage</span><b>${pct(va.coverage_pct)}</b></div><div class="metric"><span>Tagged / unversioned</span><b>${fmt(va.tagged_events)} / ${fmt(va.unversioned_events)}</b></div><div class="small-note" style="margin-top:10px">Version attribution begins with the evolution-baseline release. Earlier evidence remains explicitly unversioned rather than being retroactively labeled.</div>`;
 $('generatedAt').textContent='Generated '+new Date(data.generated_at_utc).toLocaleString()+' · Scope '+scope.toUpperCase()+' · Auto-refresh every 60 seconds';
 renderVisibleCharts(document.querySelector('.nav button.active').dataset.page);
}
function renderVisibleCharts(page){
 if(!data)return; if(page==='overview'){svgLine('trendChart',data.trend||[],[{key:'paper_win_rate_pct'},{key:'early_watch_win_rate_pct'}],'%')}
 if(page==='evolution'){svgLine('evolutionTrend',data.trend||[],[{key:'paper_win_rate_pct'},{key:'early_watch_win_rate_pct'}],'%')}
 if(page==='paper'){const rows=data.paper.equity_curve||[];const usd=rows.filter(r=>r.currency==='USD').map(r=>({label:r.label,value:r.cumulative_pnl}));const usdt=rows.filter(r=>r.currency==='USDT').map(r=>({label:r.label,value:r.cumulative_pnl}));svgLine('equityUsdChart',usd,[{key:'value'}],'');svgLine('equityUsdtChart',usdt,[{key:'value'}],'')}
}
async function load(){
 const secret=sessionStorage.getItem('ohmDashboardSecret');if(!secret){$('unlockBox').style.display='block';$('dashboardBody').style.display='none';return}
 try{
  const r=await fetch('/api/analytics/evolution?scope='+scope,{headers:{'X-Webhook-Secret':secret}});
  if(r.status===401){sessionStorage.removeItem('ohmDashboardSecret');throw new Error('Invalid secret. Unlock again.')}
  if(!r.ok)throw new Error('Dashboard request failed: '+r.status);
  data=await r.json();$('unlockBox').style.display='none';$('dashboardBody').style.display='block';$('errorBox').style.display='none';render();
 }catch(e){$('errorBox').textContent=e.message;$('errorBox').style.display='block';if(!sessionStorage.getItem('ohmDashboardSecret'))$('unlockBox').style.display='block'}
}
$('unlockBtn').onclick=()=>{const v=$('secretInput').value.trim();if(v){sessionStorage.setItem('ohmDashboardSecret',v);$('secretInput').value='';load()}};
$('secretInput').addEventListener('keydown',e=>{if(e.key==='Enter')$('unlockBtn').click()});$('refreshBtn').onclick=load;setInterval(load,60000);load();
</script>
</body>
</html>
"""
