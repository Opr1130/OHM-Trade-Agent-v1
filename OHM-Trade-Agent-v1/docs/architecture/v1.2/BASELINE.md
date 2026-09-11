# O'Pip Profit Intelligence Platform — Architecture Baseline v1.2

Status: AUTHORIZED TO START PR 1 — Architecture & Validation Contracts. Paper-only. No funded trading authority.

This document is the authorized v1.2 baseline transcribed for the repository. One naming clarification is applied: the previously proposed Profit Intelligence implementation path is **not** referred to by a GitHub pull-request number. GitHub PR #233 is a different artifact and is not dispositioned here.

## 1. Executive architecture decision

The target architecture is approved with changes and is sufficiently settled to begin PR 1 documentation and contract work. PR 2 and later implementation remain blocked until PR 1 locks the required operating, statistical, migration, and authority contracts.

Primary objective: **MAXIMIZE SUSTAINABLE REALIZED NET PROFIT AFTER ALL COSTS**

Optimization is subject to hard constraints on capital, concentration, liquidity, drawdown, data quality, execution realism, operational reliability, and human-controlled strategy promotion. Trading economics and operating economics are measured separately.

## 2. Final architectural principles

1. One authoritative history per fact; projections are rebuildable and do not become separate truth systems.
2. One canonical writer for the operational database in v1; physical database splitting requires measured stress evidence and a bounded architecture decision.
3. Validated observations, feature computation, detector state, opportunity lifecycle, forecasting, selection, execution, protection, learning, and release governance have distinct ownership.
4. Detectors are deterministic/replayable functions of `FeatureSnapshot` + prior `DetectorState` + `evaluation_time`, with no network, disk, database, hidden clock, or global mutable state inside evaluation.
5. Missing evidence is never favorable evidence. The approved selector can abstain with `INSUFFICIENT_EVIDENCE`.
6. Runtime trading logic is 100% deterministic/statistical. LLMs have zero authority over entry, exit, sizing, probability, risk limits, or strategy promotion.
7. Paper execution is technically isolated from funded exchange execution and uses no funded trading credentials or authority.
8. Point-in-time correctness is mandatory; future information cannot affect historical features, forecasts, decisions, or learning labels.
9. Execution realism includes `NO_FILL`, `PARTIAL_FILL`, `FULL_FILL`, `TARGET`, `STOP`, `TIMEOUT`, `RISK_EXIT`, feed gaps, and simulation-fidelity grading.
10. Strategy promotion and resumption require explicit human approval; automatic safety suspension is allowed.
11. Every replacement names the authority it supersedes, the cutover gate, and the deletion/retirement gate.
12. No new infrastructure, ML model, detector family, or AI dependency without measured economic or reliability benefit.

## 3. Final feature set and authority

| ID | Feature | Decision | Objective | Authority |
| --- | --- | --- | --- | --- |
| F1 | Validated Market Observation | MODIFY | Preserve provenance, ingestion order, freshness, and coverage. | Observed market facts |
| F2 | Shared Feature Bus | MODIFY | Bounded and reproducible feature calculation over retained inputs/checkpoints. | Versioned feature values |
| F3 | Stateful Detector Runtime | KEEP | Identify IGNITION transitions from explicit state and feature snapshots. | Claims only |
| F4 | Opportunity Lifecycle | MODIFY | Deduplicate episodes; manage deferrals, deadlines, expiry, and terminal reasons. | Episode lifecycle |
| F5 | Feasibility & Safety | KEEP | Enforce market, data, liquidity, and execution constraints. | Veto / abstention |
| F6 | Forecast Engine | MODIFY | Execution-aware probabilities, returns, uncertainty, and validity horizon. | Forecasts; no allocation |
| F7 | Economic / Portfolio Selector | MODIFY | Allocate constrained paper capital toward net portfolio dollars. | Selection / reservation |
| F8 | Realistic Paper Execution | MODIFY | Simulate executable fills, cash, positions, fees, latency, and exits. | Paper fills / ledger |
| F9 | Outcome & Learning | MODIFY | Produce reproducible labels, calibration, regret, and prospective evidence. | Derived analysis only |
| F10 | Dashboard / Reporting | KEEP | Explain facts, uncertainty, and performance from canonical projections. | Read-only |
| F11 | Safety / Monitoring | MODIFY | Detect failures, preserve protection, suspend unsafe admissions, and manage incidents. | Deterministic suspension |
| F12 | AI Advisory / Research | KEEP | Bounded offline extraction, postmortems, architecture/release review. | No runtime authority |

## 4. Final system architecture

```
KRAKEN / APPROVED MARKET INPUTS
          |
          v
VALIDATED MARKET OBSERVATIONS
          |
          v
SHARED FEATURES + EXPLICIT ROLLING STATE
          |
          v
IGNITION DETECTOR + OPPORTUNITY LIFECYCLE
          |
          v
FEASIBILITY + CALIBRATED FORECASTS
          |
          v
ECONOMIC SELECTION + CAPITAL RESERVATION
          |
          v
REALISTIC PAPER EXECUTION
          |
          v
OUTCOME / LEARNING / SEALED PROSPECTIVE EVALUATION
          |
          v
HUMAN-APPROVED RELEASE

Parallel safety path:
INDEPENDENT POSITION PROTECTION
          |
          v
PRIORITIZED CANONICAL INTENT
          |
          v
ONE CANONICAL WRITER
          |
          v
ONE LOCAL SQLITE HISTORY + TRANSACTIONAL PROJECTIONS
          |
          v
VERIFIED EXPORTS / CONSISTENT BACKUPS
          |
          v
ISOLATED LEARNING NODE
```

These are module/process boundaries inside a small modular application, not a microservice estate.

## 5–20. Contract pointers

Detailed operating contracts for PR 1 are the sibling documents A–K. The following baseline rules remain in force:

- v1 uses one local SQLite WAL database and one canonical writer. **Not implemented in PR 1.**
- IGNITION is the only active detector family in v1.
- Runtime AI has no entry, exit, sizing, probability, risk, or promotion authority.
- The **superseded proposed Profit Intelligence implementation path** is not the foundation of this platform. Useful tests or defect notes may be salvaged later. GitHub PR #233 is not that path and is not dispositioned by this baseline.
- No Redis, Kafka, Kubernetes, vector DB, GPU, third node, or metrics server in v1.
- PR 2 onward is **not** authorized by this baseline alone.

## Authorization

AUTHORIZED TO START PR 1 — ARCHITECTURE & VALIDATION CONTRACTS.

PR 2 onward is NOT authorized by this baseline. PR 1 is documentation/contracts only. O'Pip remains paper-only.

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`SIGNAL QUALITY SQ-01 STARTED = NO`

`FUNDED TRADING ENABLED = NO`
