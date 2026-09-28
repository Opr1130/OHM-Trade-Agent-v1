# O’Pip recovery roadmap

Historical audit base: `a416be0a068dc58543a4b6cd254d5c42fcaf4c96`

Current reconciled code/production baseline: `facf8e369e1251697bf9799bc9b1c575a9cdc3ec`

Architecture: v1.4.3, 22 September 2026. See `docs/architecture/v1.4.3/SOURCE.md`.

This roadmap does not authorize implementation, activation, merge, or deploy. It records the phase order; each phase starts only after owner review.

## Why this order

The live cycle is `app.jobs.run_cycle` → protection → `scan_opportunities` → Top-8 technical gate → profit-ranking comparator → Freqtrade dry-run paper. That legacy path remains the code path `run_cycle` executes.

The identity of the *live* paper authority is not fully observed. The Freqtrade dry-run containers are healthy and the code routes to them when Paper v2 is not requested, but the live `OPIP_PAPER_V2_MODE` value and Paper v1 `control.json` are unobserved. Treat the live paper authority as `UNKNOWN_NEEDS_EVIDENCE` until those values are actually observed; do not select an engine from defaults and container health.

The v1.4.3 spine is FeatureSnapshot → IGNITION detector → opportunity lifecycle → feasibility → forecast → constrained portfolio selection → realistic paper. F3 and F6 are missing. F4 is fragmented. F5 and F7 are legacy gates and a ranking score. The Feature Bus that F3 requires is implemented and pinned off, and its R2 shadow parity/replay evidence is now accepted.

Paper v2 is implemented and inactive. Turning it on now would paper the legacy selector. The Committee package is present, shadow-only, and has no path into `run_cycle`. Its last activation proof had an inactive timer and zero role results, on a SHA older than this core. Dashboard surfaces do not share one semantic model and cannot show detector, forecast, or Committee facts that the runtime does not emit.

Signal Quality Trade Lifecycle v2 (`OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md`) is a design for review. It refines F1–F7 and F11. Its proposed trees `app/opip/opportunity/`, `app/opip/protection/`, and `app/opip/calibration/` are absent. Those phases stay input to this roadmap. They are not a second architecture and must not become a second implementation of the Feature Bus, detector, forecast, selector, protection, or calibration.

## Sequence

### R2 — Feature Bus shadow proof (COMPLETE)

Prove the existing bus. Do not build another feature calculator.

Status: **COMPLETE, merged, deployed.** PR #284 merged to `main` as `facf8e369e1251697bf9799bc9b1c575a9cdc3ec`; exact pre-merge head `b26dab8d58116c5560e5bbed73ce95f0c814fbeb`.

Actual completed exit evidence:

- Deterministic point-in-time shadow replay and parity for the existing Feature Bus, sealed under the R2 increment `ATDD-R2-feature-bus-shadow-parity`. A frozen evidence envelope replays more than once to a byte-identical `FeatureSnapshot`, and the parity report describes the sealed snapshot values rather than recomputing a competing Feature Bus value.
- Replay fails closed on invalid or inconsistent instrument, observation identity, schema, type, aggregate domain value, revision or content identity, ambiguous revision rank, facts not visible at the declared instant, inputs beyond the declared consumed watermark, and corrupt or missing retained/restart state.
- Retained `RollingState`, reference-identity binding, restart-state (`cold start`/`warm`/`checkpoint recovery`) reproduction, and typed retained values are all validated rather than defaulted or inferred.
- Exact-head `test` and `atdd scope` CI PASS; 0 valid unresolved non-outdated review blockers at merge.
- `OPIP_FEATURE_BUS_MODE` remained `off` throughout, and `run_cycle` still does not call the Feature Bus. The Feature Bus has **no production runtime authority**.
- Production deploy run `36473910247` for `facf8e369e1251697bf9799bc9b1c575a9cdc3ec` SUCCESS.

### R3 — F3 through F7 on that evidence (NEXT, not started)

One chain, in this order, each naming the legacy path it will replace. R3 is shadow/evidence-first: it adds no production runtime authority, activates no Paper v2 cutover, and deletes no legacy path.

1. IGNITION pure detector runtime `evaluate(FeatureSnapshot, DetectorState, evaluation_time)`, in shadow. The explosion-phase string is not this detector.
2. One opportunity lifecycle for defer, deadline, expiry, and terminal reason. Fold watch, radar, pending, and signal-quality episode clocks into it only after a consumer census.
3. One feasibility seam that calls the existing vetoes and can abstain with `INSUFFICIENT_EVIDENCE`.
4. One calibrated forecast owner for probability, expected return, uncertainty, and validity horizon. Scores and Committee confidence stay out of this owner.
5. One constrained economic/portfolio selector for portfolio net dollars against cash and the frozen profit-ranking comparator. Top-8 remains live until that comparison exists.

No approved allocation, no Paper v2 activation, and no legacy deletion in R3. The Feature Bus mode stays `off` during R3 unless a separate owner approval changes it.

### R4 — Paper v2 cutover proof

Use the existing Paper v2 package. Do not write a third paper engine.

Cutover stays blocked until all of these are true:

- R3 selector is the admission source being papered
- `OPIP_PAPER_V2_MODE=active`
- Legacy drain is `READY`: Freqtrade dry-run OK, zero open Freqtrade trades, zero outstanding Freqtrade signals, zero Paper v1 pending entries, zero Paper v1 open positions
- Protection sweep is healthy before new admissions
- Universe metadata is present

Paper v2 protection today runs inside the scan, not on the one-minute protection slot. R4 must show protection still runs when discovery is down before Paper v1 or Freqtrade can be retired as the paper engine.

### R5 — Outcome consolidation and the v1.4.3 dashboard

Do not add another outcome engine. Name one writer per fact. Keep Phase3C, discovery outcomes, the trade-outcome journal, Opportunity Accountability, and Profit Intelligence as projections until each fact has one owner.

Then one semantic read model for the eight v1.4.3 pages, including the Section 21 Committee panels. Reuse B/C-4 paper lineage, Grafana freshness, and the Profit Intelligence library. Retire the legacy `/dashboard` only after fixture reconciliation.

### R6 — Committee shadow evidence

No new Committee feature. The package already has roles, transports, weakness types, trust report, economics, and matched-baseline arms.

Required before any influence review:

- Committee release SHA equals the core SHA under test
- Timer or oneshot policy is an explicit owner decision. The 2026-09-25 proof left the timer disabled.
- Real prospective cases, not mocks
- Durable weakness findings. The current registry is in-process and is not a JSONL stream.
- Trust report emitted from those cases, with unknown cost left unknown
- Still no import from `app/jobs` or `app/api`

### R7 — Technical cutover and legacy retirement

One paper authority. Then stop legacy writers. Deletion is a later owner decision after the retirement ledger gates.

## Operations item that is not the next code increment

Core `facf8e36` was deployed on 2026-09-28 (run `36473910247`). The last successful learning-worker install found in Actions is `ef8b23aa` on 2026-09-22.

A matching `/deploy-learning` for `facf8e36` is an owner control-plane action. It is required before learning capture is expected to be healthy. It does not replace R3.

## Single next implementation increment

R3 only: F3 IGNITION pure detector runtime first, then the opportunity lifecycle, then the feasibility seam, then the calibrated forecast owner, then the constrained economic/portfolio selector — shadow/evidence-first.

Do not start a later phase, enable Paper v2, enable the Committee timer, activate the Feature Bus, or redesign the dashboard in that increment.

## Signal Quality v2 disposition

| v2 phase | Roadmap home |
| --- | --- |
| Phase 0 freeze | This audit. Do not start a parallel tree. |
| Phases 1–2 queue and continuation | R3 lifecycle and detector. Do not add `app/opip/opportunity/` beside the bus. |
| Phase 3 entry and paper | R4, after the selector exists |
| Phase 4 global ranker | R3 selector. Do not add a second allocator. |
| Phase 5 protection dual-run | R4, extending the active-trade monitor and Paper v2 sweep |
| Phase 6 alert cutover | After R3 disposition text exists. v1.4.2 wording stays a renderer. |
| Phase 7 calibration | R3 forecast plus R5 labels. Do not add `app/opip/calibration/` until the forecast owner is named. |
