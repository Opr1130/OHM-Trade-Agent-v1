# O'Pip Architecture & Validation Contracts v1.2

Status: **PR 2 — canonical writer foundation (mode default off; production shadow activation not authorized).**

Paper-only. No funded trading authority. Runtime adds an independent shadow-capable writer service; default mode remains off.

## Authority statements

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`SIGNAL QUALITY SQ-01 STARTED = NO`

`FUNDED TRADING ENABLED = NO`

## Objective

**MAXIMIZE SUSTAINABLE REALIZED NET PROFIT AFTER ALL COSTS**, subject to hard constraints on capital, concentration, liquidity, drawdown, data quality, execution realism, operational reliability, and human-controlled promotion.

## Source pin

Authoritative production commit: [`808a308cd274d30b55fef47b382c229b761e07df`](SOURCE_PIN.md) (`origin/main`, merged PR 1 architecture contracts).

PR 2 implementation branches from that SHA. It is **not** branched from `fix/discovery-rotation-aware-checkpoint`.

## Owners (PR 1)

| Role | Name |
| --- | --- |
| Business Decision Owner | Ohm Prakash |
| Technical Release Owner | Ohm Prakash |

A later human Technical Release Owner may be designated. PR 1 does not invent a second person.

## Calendar

| Gate | Date |
| --- | --- |
| Implementation start | 2026-09-11 |
| 30-day decision checkpoint | 2026-10-11 |

## Index

| Document | Purpose |
| --- | --- |
| [BASELINE.md](BASELINE.md) | Authorized v1.2 architecture baseline (naming clarification applied) |
| [SOURCE_PIN.md](SOURCE_PIN.md) | Pinned commit and GitHub PR #233 isolation |
| [AUTHORITY_MAP.md](AUTHORITY_MAP.md) | As-built writers, jobs, and consumers at the pin |
| [CONSUMER_CENSUS.md](CONSUMER_CENSUS.md) | Evidence files and who reads them |
| [PR2_CAPTURE_BOUNDARY.md](PR2_CAPTURE_BOUNDARY.md) | Named low-rate transition for PR 2 |
| [A_PAPER_MANDATE.md](A_PAPER_MANDATE.md) | Paper accounts, universe, limits |
| [B_MARKET_DATA_CONTRACT.md](B_MARKET_DATA_CONTRACT.md) | Observation, ordering, grid, gaps |
| [C_CANONICAL_WRITER_CONTRACT.md](C_CANONICAL_WRITER_CONTRACT.md) | One writer, SQLite WAL (not implemented here) |
| [D_DETECTOR_CONTRACT.md](D_DETECTOR_CONTRACT.md) | `evaluate()` purity and IGNITION-only |
| [E_FORECAST_OUTCOME_CONTRACT.md](E_FORECAST_OUTCOME_CONTRACT.md) | Fills, paths, fidelity grades |
| [F_ECONOMIC_PORTFOLIO_CONTRACT.md](F_ECONOMIC_PORTFOLIO_CONTRACT.md) | Net dollars, reservation, no Kelly |
| [G_STATISTICAL_PROTOCOL.md](G_STATISTICAL_PROTOCOL.md) | Endpoint, dependence, sealed evaluation |
| [H_LEARNING_PROMOTION_CONTRACT.md](H_LEARNING_PROMOTION_CONTRACT.md) | Human release, no autonomous policy |
| [I_SAFETY_MONITORING_CONTRACT.md](I_SAFETY_MONITORING_CONTRACT.md) | Independent protection and incidents |
| [J_EXPORT_BACKUP_RECOVERY_CONTRACT.md](J_EXPORT_BACKUP_RECOVERY_CONTRACT.md) | Manifests, RPO/RTO, restore drill |
| [K_LEGACY_DISPOSITION_MIGRATION.md](K_LEGACY_DISPOSITION_MIGRATION.md) | Cleanup, owners, checkpoint |
| [CODING_BOUNDARY_CONTRACT.md](CODING_BOUNDARY_CONTRACT.md) | Planned import/ownership boundaries |
| [CLEANUP_MATRIX.md](CLEANUP_MATRIX.md) | KEEP / ADAPT / FREEZE / RETIRE / DELETE |
| [NUMERIC_DECISIONS.md](NUMERIC_DECISIONS.md) | Ratified N1–N12 |
| [MIGRATION_CALENDAR.md](MIGRATION_CALENDAR.md) | Dates and default-if-no-decision |
| [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md) | PR 1 lock checklist |
| [fixtures/](fixtures/) | Validation examples only |

## Explicit non-goals

Do not implement in PR 1:

- runtime feature-bus logic
- SQLite tables or migrations
- deployments
- production behavior changes
- technical score or Top-8 changes
- trading/risk authority changes
- new paper authority
- merge, close, supersede, or modify GitHub PR #233
- runtime AI
- a new infrastructure service
