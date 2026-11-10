# OPIP-LEARN-FRESH-001 — Phase 7 / Slice 0 Source Contract Readiness

**Disposition: STOP — STATIC SOURCE CONTRACTS PARTIALLY PROVEN; NOT READY TO IMPLEMENT/ACTIVATE.**
**Scope:** Read-only repository verification and tests-first readiness. No production data was accessed; no source, deploy, learning job, model or trading settings were changed.

## Source baseline

- Main SHA at inspection: `1d5290e0d47545ab0b6ade0f8b77e1dc41fc676e`.
- A clean tests-only branch was created from that SHA for Slice 0.
- The older experimental branch `feat/fresh-evidence-shadow-20261110` remains **unapproved** and must not be merged or treated as a tests-first implementation.
- Source inspection is NOT proof that the files are currently populated or that export/sync/learning timers are healthy in production.

## Source-to-contract matrix

| Population / event | Existing producer and record contract | Data path / transfer evidence | Static verdict |
| --- | --- | --- | --- |
| All Stage-0 screened venue instruments, including rejected/omitted from shortlist | `app/opip/decision/screening.py::ScreeningEvaluation`; `app/jobs/scan_opportunities.py::_persist_broad_screening_fail_open` | `opip/qualification/screening_evaluations.jsonl` and checksummed archives; listed in `deploy/remote/export-opip-learning-evidence.sh` | SOURCE EXISTS; completeness unproven |
| Finalist decisions, ordered gate reasons and qualification outcomes | `app/opip/decision/funnel.py::QualificationFunnel.funnel_events`; `observer.py::finalize` | `opip/qualification/funnel_events.jsonl` and archives; export listed | SOURCE EXISTS; completeness unproven |
| Per-scan cohort totals and Chief AI stage status/model | `app/opip/decision/summary.py::build_scan_summary` | `opip/qualification/scan_summaries.jsonl` and archives; export listed | SOURCE EXISTS; count/invariant proof pending |
| Individual Chief review outcomes, non-selection and prefilter | `observer.py::_record_ai_outcome`, `chief_learning_capture.py`, `candidate_trace.py` | Derived from funnel/trace when present; shadow learning JSON not in export | PARTIAL; reconcile per-candidate coverage |
| Candidate trace and rejection detail | `app/services/candidate_trace.py::trace_candidate_event` | `candidate_trace.jsonl`; export listed | SOURCE EXISTS; writes fail-soft, coverage unproven |
| Qualified signal journey | `app/services/intelligence_journey.py` | `intelligence_learning/events.jsonl`; export listed | SOURCE EXISTS; linkage completeness unproven |
| Telegram delivery/suppression attempts | `app/services/telegram_delivery.py` | `telegram_delivery_events.jsonl`; export listed | SOURCE EXISTS |
| Tradevisor received signals, frozen decisions and withheld events | `app/services/tradevisor_inbox.py` | `tradevisor_v1_inbox.json`; **not listed** in existing copy-only export | BLOCKER S0-01 |
| Recommendation plans and observed target/stop outcomes | `app/services/trade_outcome_registry.py` | `trade_outcomes.json`; **not listed** in existing copy-only export | BLOCKER S0-02 |
| Per-record proof of production V2 scorer | `scan_opportunities.py` uses configured V2 selector; `ScreeningEvaluation.to_dict()` emits legacy strategy label `OPIP-STRATEGY-V1` | No explicit `production_scoring_version=v2` on finalized screening row in inspected source | BLOCKER S0-03 |
| Accurate source population/completeness in *live* runtime | Requires counts per scan, accepted + rejected + unavailable, archive coverage, export health, exact-SHA and known evidence gaps | Not inspectable from repository source alone | BLOCKER S0-04 |

### Notes on bounded scope and existing governance

- `ScreeningEvaluation` carries `observed_at`, `scan_id`, `scanner_type`, `venue_instrument_id`, `outcome`, directional scores and metadata. `PENDING_FINALIZATION` is not a terminal canonical screening result.
- `QualificationFunnel.funnel_events()` emits `candidate_id`, `decision`, `gate_results`, `signal_id` and terminal attribution. `build_scan_summary` includes `ai_stage`, `funnel` counters and `invariant_holds`.
- Producer paths are fail-soft to protect production; consequently source-code declarations alone cannot establish ≥99.9% live population coverage.
- Qualification HOT files are bounded and have separate archives: any future ingestion cannot rely on the latest HOT files alone.
- The canonical remote learning export is manifest schema 4 and explicitly deletes/omits `p1_shadow_outbox.jsonl`. Do not revive this retired artifact.
- `tradevisor_v1_inbox.json` is a mutable projection with frozen `decision_record` rows; a future copy must be consistent and versioned. It must not be handled as if it were an append-only JSONL source.
- A Telegram delivery event does not prove Tradevisor decision contents, that a trade executed, or that an alert preceded a manual entry.
- A generic strategy-version label is not trustworthy V2 production attribution. A separate verified release/config/scorer identity could satisfy this contract if explicitly tied to each source generation; no mechanism is assumed.

## Tests-first manifest

| Test node | Requirements | Expected baseline result |
| --- | --- | --- |
| `test_s0_001_qualification_sources_are_exported_without_reactivating_p1` | FR-001/005, CT-001/002, EC-091 | GREEN (static) |
| `test_s0_002_screening_row_contains_finalized_rejection_identity` | FR-001/005, EC-102 | GREEN expected, execution unverified |
| `test_s0_003_funnel_and_summary_preserve_rejections_and_ai_stage` | FR-001/003, CT-002/003 | GREEN expected, execution unverified |
| `test_s0_004_delivery_ledger_is_a_distinct_exported_source` | FR-004 | GREEN expected, execution unverified |
| `test_s0_005_tradevisor_frozen_decisions_have_verified_transfer_coverage` | FR-004, FR-005 | **RED — S0-01** |
| `test_s0_006_manual_advisory_outcome_source_has_verified_transfer_coverage` | FR-007/008/016 | **RED — S0-02** |
| `test_s0_007_v2_screening_row_proves_production_scorer_identity` | FR-001/005/006 | **RED — S0-03** |
| `test_s0_008_existing_export_has_retired_p1_and_source_lineage_guards` | BR-005/010 | GREEN (static) |

Tests live in `tests/test_opip_fresh_evidence_s0_source_contracts.py`. **No test results are asserted.** Repository-connected source inspection establishes expected contract gaps; running the tests on the exact branch is a separate follow-up. Red tests should be retained until a compliant evidence-only implementation and tests-first approval.

Focused test command (CI/authorized local checkout, **not production**):

```bash
cd OHM-Trade-Agent-v1
python -m pytest -q -rs tests/test_opip_fresh_evidence_s0_source_contracts.py
```

Do not add these known-red tests to mandatory release CI or initiate production deployment before addressing and validating their missing contracts.

## Slice 0 gate

**STOP / NOT READY FOR SLICE 1.** Before proceeding:

1. Prove live per-scan V2 population coverage, including BROAD_SEARCH rejections, AI non-selection and actual records written versus expected. Include archive segments, dead letters, missing/truncated files and the exact source SHA.
2. Choose an approved read-only verified export route for Tradevisor frozen decisions and recommendation/outcome records without changing the canonical writer or restarting trading.
3. Specify and test the authoritative production V2 attribution for every included decision, independent of the legacy `STRATEGY_VERSION` label.
4. Verify copy-only export/sync status and worker resource headroom from read-only runtime diagnostics; do not infer health from scripts alone.
5. Receive owner approval for the next slice; no funded trading, training, model promotion, legacy P1 reactivation, merge or deployment.

**No modifications to production source, deploy scripts, environment files, services or CI workflows in Slice 0.**
