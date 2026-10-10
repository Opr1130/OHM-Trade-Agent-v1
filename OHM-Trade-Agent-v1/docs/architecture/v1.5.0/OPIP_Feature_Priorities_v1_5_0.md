# O’Pip feature priorities v1.5.0

9 October 2026 • Documentation and planning only • No deployment or activation

Order: R4 closure → R5 contract freeze → A Eye → B Horizons → C Capital → D Portfolio/rotation → E Autonomous paper → F Learning → separate TARGET_PAPER decision. R4 closure was not verified in this task.

**KEEP** preserves ownership and controls. **EXTEND** adds within an existing owner. **REVISIT** requires reconciliation/migration decisions. **NEW** is a new logical capability using existing infrastructure. Categories are not completion claims. P0 = prerequisite/safety gate; P1 = ordered R5 delivery; P2 = supporting work.

| Order | Priority | ID | Category | Feature | Closure evidence |
|---|---|---|---|---|---|
| 0 | P0 | GOV | KEEP | Canonical writer evidence and release authority | No duplicate authority; replay and release-profile checks |
| 0 | P0 | R4 | KEEP | R4 quality gate and evidence closure | Frozen real evidence and alert-to-paper trace |
| 1 | P0 | FREEZE | NEW | R5 architecture and acceptance contracts | Owners and parameter/evidence contracts ratified |
| 2 | P1 | F1 | EXTEND | Validated Market Observation | Coverage freshness gap and trigger provenance |
| 2 | P1 | EYE | NEW | Continuous Market Eye attention layer | Bounded event attention with no trade authority |
| 2 | P1 | F2 | EXTEND | Shared Feature Bus | Retained snapshots and schedule/checkpoint replay |
| 2 | P1 | SCHED | REVISIT | Scheduler and duplicate observation paths | One deduplicated path and recovery parity |
| 3 | P1 | F3 | KEEP | Stateful Detector Runtime IGNITION | Pure deterministic evaluation; no second detector |
| 3 | P1 | F4 | EXTEND | Opportunity Lifecycle | One episode clock and immutable thesis lineage |
| 3 | P1 | HORIZON | NEW | Tactical Swing Position classification | No silent reclassification; unsupported horizons abstain |
| 3–4 | P1 | F5 | EXTEND | Feasibility and Safety | Horizon liquidity/capacity and missingness vetoes |
| 3–4 | P1 | F6 | EXTEND | Forecast Engine by horizon | Proper scores and uncertainty; no ordinal probability |
| 1 schema; 7 proof | P0/P1 | F9 | EXTEND | Outcome and Learning | One label history; full populations; prospective uplift |
| 4 | P1 | CAPITAL | NEW | Capital Intelligence and capital-time view | Cash partitions reconcile; occupancy not double counted |
| 5 | P1 | F7 | EXTEND | Economic and Portfolio Selector | One planner/allocator; atomic reservation versus cash |
| 5 | P1 | ROTATE | NEW | Portfolio plan and capital rotation | Hold/switch cost comparison; second leg requalified |
| 6 | P1 | F8 | EXTEND | Realistic Paper Execution and autonomous portfolio | Portfolio fills cash exits recovery and net economics |
| 2–7 | P2 | F10 | EXTEND | Dashboard and Reporting | Shared semantics; cash/horizon/plan drill-down |
| 0; gate 6/8 | P0 | F11 | KEEP | Independent Safety and Monitoring | Protection survives Eye planner AI and scheduler loss |
| Parallel retained R6 | P2 | F12 | KEEP | AI Advisory and Research | Prospective trust/cost/weakness evidence only |
| 1 census; 8 retirement | P0/P2 | OVERLAP | REVISIT | Duplicate outcomes clocks and legacy engines | One owner per fact; stop/archive/rollback before deletion |
| 8 | P0 gate | CUTOVER | REVISIT | TARGET_PAPER readiness and cutover order | Exact release owner approval and all gates PASS |

The older roadmap R5 outcome-consolidation/cockpit work is retained within F9/F10 (tracking alias R5-LEGACY-OUTCOMES-COCKPIT); R6 Committee proof and R7 governed retirement remain. Expectation schemas and evidence capture start before R5-A; R5-F evaluates matured outcomes.

The CSV adds dependencies, status, owner, evidence link and separate architecture/code/wiring/runtime/production/user-value fields. Owners are unassigned; non-architecture dimensions are NOT_ASSESSED. No 25–35% estimate is treated as measured progress.

Next concrete step: reconcile the existing R4 evidence, then freeze R5-A acceptance scope and the shared expectation/identity contracts. This list grants no implementation, release, TARGET_PAPER or funded authority.
