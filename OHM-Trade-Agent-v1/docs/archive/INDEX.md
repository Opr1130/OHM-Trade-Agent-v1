# O'Pip design notes archive — index

Status: historical record. Date: 2026-11-12. Nothing here is an active contract.

This folder is v6 retirement theme R1 (`docs/V6_AUDIT.md` section 10): the root-level
design notes were moved here, unchanged, so the repository root only carries the
README, the safety contracts and the documents that code or tests read. These notes
are kept as evidence of what was designed and shipped; they grant no runtime
authority and are not a second architecture source.

- Every file below was moved from the `OHM-Trade-Agent-v1/` root, unchanged.
- Their internal relative links still resolve because the related notes moved
  together, and the README link to the Price Movement Radar note now points here.
- The documents that stay at the root, and why, are listed at the bottom of this
  page.

## Archived notes

| Note | Former root path | What it records |
|---|---|---|
| [`OHM_P1_PHASE3C_IMPLEMENTATION.md`](OHM_P1_PHASE3C_IMPLEMENTATION.md) | `OHM_P1_PHASE3C_IMPLEMENTATION.md` | P1 Phase 3C research/shadow foundation implementation (canonical snapshot, dark outbox) |
| [`OHM_P1_TARGET_ARCHITECTURE.md`](OHM_P1_TARGET_ARCHITECTURE.md) | `OHM_P1_TARGET_ARCHITECTURE.md` | P1 intelligence program reconciled target architecture baseline |
| [`OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md`](OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md) | `OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md` | Adaptive intelligence router v1: provider selection at the advisory-review boundary |
| [`OPIP_BUILD0_VERIFICATION.md`](OPIP_BUILD0_VERIFICATION.md) | `OPIP_BUILD0_VERIFICATION.md` | Build 0 production evidence verification report (defaults read from code) |
| [`OPIP_DECISION_ENGINE_5_2A.md`](OPIP_DECISION_ENGINE_5_2A.md) | `OPIP_DECISION_ENGINE_5_2A.md` | Sequence 5 build 5.2A: sustained shadow-equivalence evidence layer |
| [`OPIP_DECISION_ENGINE_V1.md`](OPIP_DECISION_ENGINE_V1.md) | `OPIP_DECISION_ENGINE_V1.md` | Decision Engine build 1: shadow / read-only comparison mode |
| [`OPIP_DECISION_ENGINE_V2.md`](OPIP_DECISION_ENGINE_V2.md) | `OPIP_DECISION_ENGINE_V2.md` | Decision Engine v2 (build 5.1): point-in-time evidence identities and replay |
| [`OPIP_EVENT_INTELLIGENCE_V1.md`](OPIP_EVENT_INTELLIGENCE_V1.md) | `OPIP_EVENT_INTELLIGENCE_V1.md` | Event intelligence foundation v1: shadow/evidence-only event store |
| [`OPIP_REALTIME_MARKET_INTELLIGENCE_4_2.md`](OPIP_REALTIME_MARKET_INTELLIGENCE_4_2.md) | `OPIP_REALTIME_MARKET_INTELLIGENCE_4_2.md` | Sequence 4 build 4.2: asynchronous runtime foundation for public market data |
| [`OPIP_REALTIME_MARKET_INTELLIGENCE_4_3_4_5.md`](OPIP_REALTIME_MARKET_INTELLIGENCE_4_3_4_5.md) | `OPIP_REALTIME_MARKET_INTELLIGENCE_4_3_4_5.md` | Sequence 4 builds 4.3 to 4.5: public derivatives and liquidation evidence |
| [`OPIP_REALTIME_MARKET_INTELLIGENCE_V1.md`](OPIP_REALTIME_MARKET_INTELLIGENCE_V1.md) | `OPIP_REALTIME_MARKET_INTELLIGENCE_V1.md` | Real-time market intelligence build 4.1: cross-venue evidence design |
| [`OPIP_SEQUENCE5_WAVE_A1.md`](OPIP_SEQUENCE5_WAVE_A1.md) | `OPIP_SEQUENCE5_WAVE_A1.md` | Sequence 5 wave A1: exact learning evidence linkage |
| [`OPIP_SEQUENCE5_WAVE_A2.md`](OPIP_SEQUENCE5_WAVE_A2.md) | `OPIP_SEQUENCE5_WAVE_A2.md` | Sequence 5 wave A2: governed learning lifecycle |
| [`OPIP_SEQUENCE5_WAVE_A3.md`](OPIP_SEQUENCE5_WAVE_A3.md) | `OPIP_SEQUENCE5_WAVE_A3.md` | Sequence 5 wave A3: paper and ML data readiness audit |
| [`OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md`](OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md) | `OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md` | Signal quality and trade lifecycle architecture v2 (design for review) |
| [`OPIP_TWO_STAGE_ADVISORY_AI_V1.md`](OPIP_TWO_STAGE_ADVISORY_AI_V1.md) | `OPIP_TWO_STAGE_ADVISORY_AI_V1.md` | Two-stage advisory AI v1 design and its supporting evidence |
| [`PRICE_MOVEMENT_RADAR.md`](PRICE_MOVEMENT_RADAR.md) | `PRICE_MOVEMENT_RADAR.md` | Price Movement Radar v1 design: volatility-expansion classification |
| [`SIGNAL_QUALITY_PHASE2.md`](SIGNAL_QUALITY_PHASE2.md) | `SIGNAL_QUALITY_PHASE2.md` | Signal Quality v1 phase 2: offline historical replay and calibration harness |
| [`SIGNAL_QUALITY_PHASE3B.md`](SIGNAL_QUALITY_PHASE3B.md) | `SIGNAL_QUALITY_PHASE3B.md` | Signal Quality phase 3B: technical structure and chase-risk workstream |
| [`SIGNAL_QUALITY_V1.md`](SIGNAL_QUALITY_V1.md) | `SIGNAL_QUALITY_V1.md` | Signal Quality / explosion detection v1 phase 1 (shipped dark) |

## Notes that stay at the repository root

These are deliberately not archived, because they are still cited by code, by tests
or by the repository guardrails.

| Root note | Why it stays |
|---|---|
| [`README.md`](../../README.md) | Project entry point |
| [`AI_EXECUTION_GATEWAY.md`](../../AI_EXECUTION_GATEWAY.md) | Safety contract named in `AGENTS.md` |
| [`OHM_P1_PROMOTION_GATES.md`](../../OHM_P1_PROMOTION_GATES.md) | Safety / promotion contract named in `AGENTS.md` |
| [`AUTONOMOUS_BUILD_REPORT.md`](../../AUTONOMOUS_BUILD_REPORT.md) | Read by the R5/R6 acceptance tests |
| [`SIGNAL_QUALITY_PHASE3A.md`](../../SIGNAL_QUALITY_PHASE3A.md) | Cited by `app/services/signal_timing_v2.py` and `tests/test_decision_telemetry.py` |
| [`TRADEVISOR_V2.md`](../../TRADEVISOR_V2.md) | Cited by `app/api/tradevisor.py` and `app/core/config.py` |
| [`TRADINGVIEW_WAVE8_2.md`](../../TRADINGVIEW_WAVE8_2.md) | Cited by `app/core/config.py`, the README and `deploy/nginx/` examples |
| [`WAVES_3_7_COMPLETION.md`](../../WAVES_3_7_COMPLETION.md) | Read by `tests/test_waves_3_7_completion_manifest.py` |
| [`WAVE_CHECKPOINTS.md`](../../WAVE_CHECKPOINTS.md) | Read by `tests/test_wave_checkpoints_manifest.py` |
