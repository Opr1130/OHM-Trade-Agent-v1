# O’Pip recovery roadmap

Audit SHA: `a416be0a068dc58543a4b6cd254d5c42fcaf4c96`

Architecture: v1.4.3, 22 September 2026. See `docs/architecture/v1.4.3/SOURCE.md`.

This roadmap does not authorize implementation, activation, merge, or deploy. The next increment starts only after owner review of this audit.

## Why this order

The live cycle is `app.jobs.run_cycle` → protection → `scan_opportunities` → Top-8 technical gate → profit-ranking comparator → Freqtrade dry-run paper. That path is authoritative today.

The v1.4.3 spine is FeatureSnapshot → IGNITION detector → opportunity lifecycle → feasibility → forecast → constrained portfolio selection → realistic paper. F3 and F6 are missing. F4 is fragmented. F5 and F7 are legacy gates and a ranking score. The Feature Bus that F3 requires is implemented and pinned off.

Paper v2 is implemented and inactive. Turning it on now would paper the legacy selector. The Committee package is present, shadow-only, and has no path into `run_cycle`. Its last activation proof had an inactive timer and zero role results, on a SHA older than this core. Dashboard surfaces do not share one semantic model and cannot show detector, forecast, or Committee facts that the runtime does not emit.

Signal Quality Trade Lifecycle v2 (`OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md`) is a design for review. It refines F1–F7 and F11. Its proposed trees `app/opip/opportunity/`, `app/opip/protection/`, and `app/opip/calibration/` are absent. Those phases stay input to this roadmap. They are not a second architecture and must not become a second implementation of the Feature Bus, detector, forecast, selector, protection, or calibration.

## Sequence

### R2 — Feature Bus shadow proof

Prove the existing bus. Do not build another feature calculator.

Exit evidence:

- Parity against `app.indicators.technical` on a frozen input set, using `app/opip/features/parity.py`
- Replay from checkpoints equals stored snapshots, using `app/opip/features/replay.py`
- Capture remains dual-gated on `OPIP_FEATURE_BUS_MODE=shadow` and `OPIP_CANONICAL_WRITER_MODE=shadow`
- The production compose pin stays `off` until that evidence is accepted
- `run_cycle` still does not call the pilot

### R3 — F3 through F7 on that evidence

One chain, in this order, each naming the legacy path it will replace:

1. IGNITION `evaluate(FeatureSnapshot, DetectorState, evaluation_time)` in shadow. The explosion-phase string is not this detector.
2. One opportunity lifecycle for defer, deadline, expiry, and terminal reason. Fold watch, radar, pending, and signal-quality episode clocks into it only after a consumer census.
3. One feasibility seam that calls the existing vetoes and can abstain with `INSUFFICIENT_EVIDENCE`.
4. A forecast owner for probability, expected return, uncertainty, and validity horizon. Scores and Committee confidence stay out of this owner.
5. A constrained selector for portfolio net dollars against cash and the frozen profit-ranking comparator. Top-8 remains live until that comparison exists.

No approved allocation and no Paper v2 activation in this slice.

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

Core `a416be0a` was deployed on 2026-09-27. The last successful learning-worker install found in Actions is `ef8b23aa` on 2026-09-22. Analytics last succeeded at that same older SHA. Committee shadow was proved at `86d5290b` on 2026-09-25.

A matching `/deploy-learning` for `a416be0a` is an owner control-plane action. It is required before learning capture is expected to be healthy. It does not replace R2.

## Single next implementation increment

R2 only: shadow parity and replay proof for the existing Feature Bus, with the production pin left off.

Do not start R3, enable Paper v2, enable the Committee timer, or redesign the dashboard in that increment.

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
