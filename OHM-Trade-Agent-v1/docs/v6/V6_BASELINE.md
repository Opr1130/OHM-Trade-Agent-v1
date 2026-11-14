# V6 baseline (verified 2026-11-13 against origin/main 88c335b6)

Classification key: IMPLEMENTED, PARTIAL, READY, BLOCKED, OBSOLETE, UNKNOWN. Evidence is a path or PR. Anything not checked is UNKNOWN.

## Slice queue in `docs/V6_SPEC.md`
| Slice | Status | Evidence |
|---|---|---|
| A version stamp module | IMPLEMENTED | issue #533 closed, PR #539; `app/opip/decision/versioning.py` |
| B archive root notes | IMPLEMENTED | issue #534, PR #542; `docs/archive/INDEX.md` |
| C retention dry-run report | IMPLEMENTED | issue #535, PR #543; `app/opip/data_platform/retention_dry_run.py` |
| D stamp qualification records | IMPLEMENTED (wiring not re-verified here) | issue #536, PR #544 |
| E scorecard core, report tool, coverage header | IMPLEMENTED | issues #537, #546, #550; `app/opip/analytics/scorecard_report.py` |
| retention policy table, R2 readiness report, canonical file roll design note | IMPLEMENTED (documents/reports) | issues #547, #553, #554 |
| canonical file roll and shipping | PARTIAL | design note merged; shipper/receiver in draft PR #559 (not activated) |
| data box migration | BLOCKED | owner/infrastructure decision, PostgreSQL migration not implemented |
| Ops Center config/data pages | UNKNOWN | not inspected |
| event intelligence activation | BLOCKED | owner decision |
| Freqtrade results into Scorecard | BLOCKED | paper trading is OFF by owner decision |
| retirement themes R2-R6 | BLOCKED | disable, observe two weeks, then delete rule |
| committee keep-or-retire | BLOCKED | owner decision |

## Mandate workstreams
| Workstream | Status | Evidence / gap |
|---|---|---|
| A policy engine | PARTIAL | `app/core/strategy_policy.py` (scoring + advisory economics, validated); no unified evaluator with reason codes for liquidity/spread/slippage/confidence/backdrop/Early Watch. Simulator parameterises these offline (this PR); production wiring is a D2 decision |
| B private simulator | READY, delivered as draft | `app/opip/simulator/` (this branch); `app/backtesting` exists but is flagged unreachable in `docs/V6_AUDIT.md` |
| C scanner lifecycle | PARTIAL | open PRs #515, #508, #517 address bounds/OOM observability; not re-reviewed here |
| D Early Watch | PARTIAL | #517 open; not re-reviewed here |
| E AI advisory | PARTIAL | router/Chief telemetry exist; output-cap telemetry in draft PR #560; truncation root cause UNVERIFIED (S1 report pending) |
| F canonical evidence | PARTIAL | #516 open; S1/S4 concerns open (see V6_BLOCKERS.md); rotation fix in draft PR #560 |
| G control center | UNKNOWN | `control-center-v2` exists; not inspected |
| H observability | PARTIAL | decision funnel and router telemetry exist |
| I cleanup | READY | `docs/V6_AUDIT.md` lists 105 unreachable modules; retirement rules in V6_SPEC |

Open V6-relevant draft PRs: #558 backup repair, #559 S2 receiver, #560 S1 rotation (this session), #488 strategy admin draft editor, #521 (skipped by owner).
