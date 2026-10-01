INCREMENT:
ATDD-R4-F8-paper-v2-cutover-readiness

OWNER-APPROVED INTENT:
This is the R4-A paper-v2 cutover-readiness and independent-protection increment. It proves readiness for a later explicit authority cutover without performing that cutover. It adds one bounded, fail-closed cutover-readiness evidence probe and aggregate verdict, and it moves Paper-v2 protection onto the unified cycle's protection phase so it no longer depends on opportunity discovery. It does NOT set `OPIP_PAPER_V2_MODE=active`, does NOT wire F7 as the live admission authority, does NOT activate the Feature Bus or Committee, does NOT retire any legacy paper path, and does NOT touch funded or exchange-order authority.

STARTING SHA. `origin/main` = `12e7428639e989de06a7579953c8a95efb5e5bbe`. If `origin/main` moves, the branch is inspected; if R4, F7, the frozen comparator, protection, drain or the ATDD scope is affected it is reconciled before merge.

LIVE CURRENT AUTHORITY (unchanged by this increment). Legacy Top-8 plus profit-ranking remains the live admission authority. Freqtrade dry-run remains the paper engine when Paper v2 is not requested, and Paper v1 remains the shadow simulator. Paper v2 is `IMPLEMENTED_NOT_ACTIVE`; its live mode is `UNKNOWN_NEEDS_EVIDENCE` and the repository default is `off`. F7 (`app/opip/portfolio_selector.py`) is dormant shadow code and is not wired into `run_cycle` or `scan_opportunities`. Paper v2 has no short engine and no pending-entry state machine.

KEY LAW — READINESS IS NOT ACTIVATION. The aggregate verdict reports whether independently verifiable technical gates are satisfied. Owner activation prerequisites (setting the mode active and wiring the selector as the admission source) are reported separately and are never counted as technical blockers. A `READY_FOR_OWNER_ACTIVATION` verdict never selects Paper v2 and never mutates canonical evidence.

KEY LAW — MISSING EVIDENCE IS NEVER FAVORABLE EVIDENCE. Every observation fails closed: an unreadable mode is not the default, an unreadable drain is not empty, an unreadable protection projection is not "nothing to protect", and a missing universe metadata gate is never read as present.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (spine): "Feasibility and calibrated statistical forecasts -> economic selector -> deterministic risk checks and atomic capital reservation -> realistic paper execution."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3: "Missing evidence is never favorable."
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R4: cutover stays blocked until the R3 selector is the admission source being papered, `OPIP_PAPER_V2_MODE=active`, legacy drain is READY, the protection sweep is healthy before new admissions, and universe metadata is present; "Paper v2 protection today runs inside the scan, not on the one-minute protection slot. R4 must show protection still runs when discovery is down."
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` F8 row: "Paper v2 has no pending-limit state machine and no short engine."; F11 row: "Paper v2 protection sweep runs only inside the opportunity scan." and BLOCKERS "Paper v2 sweep is not on the one-minute slot."
- `docs/architecture/v1.2/A_PAPER_MANDATE.md` and `docs/architecture/v1.2/F_ECONOMIC_PORTFOLIO_CONTRACT.md`: paper is simulated cash P&L after fees; realistic paper execution is the F8 authority.
- Reused rather than duplicated: `app/services/freqtrade_result_ingest.py`, `app/services/freqtrade_signal_bridge.py`, `app/services/paper_trade_control.py`, `app/services/paper_trade_registry.py` (drain sources); `app/services/paper_v2_protection_runtime.py` (`run_protection_sweep`); `app/services/paper_v2_scan_router.py` (`SUPPORTED_DIRECTION`, the fail-closed universe gate); `app/opip/contracts/portfolio.py` (the frozen F7 handoff contract).
- `docs/atdd/scope-contracts/ATDD-R3-F7-economic-portfolio-selector.md`: F7 is frozen and is not modified by this increment.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: a completed increment must not permanently own the global pointer.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the bounded live-evidence probe
WHEN:
the Paper-v2 mode is observed
THEN:
a value is evidence only when read from an explicit settings source rather than from a field default, a missing, defaulted or malformed value fails closed as UNAVAILABLE rather than the default, and the report never dumps the environment or a secret

AC-002:
GIVEN:
the legacy drain evaluator and the two legacy paper subsystems
WHEN:
the legacy state is read
THEN:
zero exposure in both subsystems resolves to READY, an open Freqtrade trade, an outstanding Freqtrade signal, a Paper-v1 pending entry or a Paper-v1 open position each resolves to DRAINING, and an unreadable source resolves to UNAVAILABLE

AC-003:
GIVEN:
the unified cycle protection phase
WHEN:
the cycle runs
THEN:
Paper-v2 protection is invoked in the protection phase before discovery, it still runs when discovery is skipped, when the scanner throws, when operator state is unreadable, and when the active-position monitor raises, and a protection failure never aborts the cycle

AC-004:
GIVEN:
the opportunity scan and the Paper-v2 protection sweep
WHEN:
protection is ordered against admission and repeated
THEN:
in the scan, protection precedes admission routing, and a repeated sweep is byte-identical and writes nothing

AC-005:
GIVEN:
the universe-metadata admission gate
WHEN:
universe metadata is absent or observed empty
THEN:
the gate is reported as enforced fail-closed, an empty observation resolves to UNAVAILABLE with a machine-readable reason, and no second inferred AssetPairs lookup is introduced

AC-006:
GIVEN:
the Paper-v2 direction coverage
WHEN:
coverage is reported
THEN:
LONG is covered, SHORT is not, the gap carries the machine-readable reason SHORT_AUTHORITY_MISSING, a missing LONG coverage is reported as LONG_AUTHORITY_MISSING rather than as no gap, and the aggregate verdict is NOT_READY

AC-007:
GIVEN:
the pending-entry mandate
WHEN:
it is reported
THEN:
the current mandate is documented as immediate-only with no pending lifecycle, and an unknown or unsupported pending requirement fails closed as a blocker

AC-008:
GIVEN:
the frozen F7 portfolio selector and the Paper-v2 handoff
WHEN:
the handoff contract is inspected
THEN:
the frozen F7 records expose every field the handoff needs, F7 is not yet the live admission source, and neither `run_cycle` nor `scan_opportunities` imports F7

AC-009:
GIVEN:
the new readiness modules
WHEN:
their imports and assignments are audited
THEN:
they activate no Paper-v2 mode, Feature Bus, Committee or exchange surface, add no exchange credentials, and introduce no funded order API

AC-010:
GIVEN:
the activation switch and the legacy authority
WHEN:
rollback is inspected
THEN:
the default remains off, a rollback path is reported available, and an unprovable default blocks readiness

AC-011:
GIVEN:
typed cutover evidence
WHEN:
the aggregate verdict is derived
THEN:
healthy technical evidence resolves to READY, a missing short authority resolves to NOT_READY with SHORT_AUTHORITY_MISSING, an unprovable starting equity blocks the drain rather than being defaulted, and unreadable evidence fails closed to NOT_READY with the exact reason codes

AC-012:
GIVEN:
an inactive mode and the baseline failure set
WHEN:
readiness is evaluated and the baseline is triaged
THEN:
an inactive mode and an inactive selector admission are activation prerequisites rather than technical blockers, computing readiness writes no activation switch, and the R4 baseline triage is recorded as durable evidence classifying environment/platform-only, unrelated-harness and production-relevant families

AC-013:
GIVEN:
the readiness report job
WHEN:
it runs
THEN:
it prints one machine-readable readiness report and activates nothing

AC-014:
GIVEN:
the protection-work projection
WHEN:
readiness assesses protection health
THEN:
a positive exposure with no committed protection plan or in a non-armable state is reported UNSAFE and blocks readiness, a terminal or flat exposure is healthy, and no lifecycle work is advanced

EXPLICITLY OUT OF SCOPE:
- Setting `OPIP_PAPER_V2_MODE=active` or any other activation
- Wiring F7 as the live admission authority or retiring the legacy selector, Top-8 gate or profit-ranking comparator
- Activating the Feature Bus, the Committee, or any AI runtime authority
- Funded trading, live exchange execution, Kraken order placement, modification, cancellation or confirmation
- Retirement, deletion or re-wiring of Paper-v1, Freqtrade dry-run or any legacy paper path
- A Paper-v2 short engine and a Paper-v2 pending-limit state machine
- A second authority resolver, a second paper engine, a second scheduler, an F7 canonical writer, or any new persistence store
- Modifying architecture documents, the v1.4.3 DOCX, the frozen F3/F4/F5/F6/F7 contracts, the ATDD checker, workflows, or `pyproject.toml`
- Weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
- `OPIP_PAPER_V2_MODE` remains `off` by default and `opip_paper_v2_mode` stays `off` in the repository default.
- `OPIP_FEATURE_BUS_MODE` remains `off`; the Committee remains shadow-only.
- Production `run_cycle` and `scan_opportunities` do not call F7.
- Legacy admission, ranking and the Freqtrade/Paper-v1 paper authorities remain live and unchanged.
- The canonical writer remains the single domain write path; Paper-v2 protection only advances already-authorized work.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled; paper execution stays isolated from funded order endpoints.
- A normal push of this feature branch and its review, merge and dormant deploy are permitted as recorded by the owner.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_001_default_is_not_live_evidence
AC-001 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_001_malformed_state_fails_closed
AC-001 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_001_missing_state_is_unavailable
AC-001 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_001_repository_default_is_not_live_mode_evidence
AC-001 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_001_report_bounds_drain_reason_text
AC-001 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_001_report_contains_no_environment_or_secrets
AC-002 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_002_drain_draining_while_any_obligation_remains
AC-002 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_002_drain_ready_when_legacy_empty
AC-002 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_002_drain_unavailable_when_unreadable
AC-003 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_003_cycle_protection_is_fail_open
AC-003 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_003_cycle_runs_protection_before_discovery
AC-003 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_003_protection_runs_when_discovery_is_skipped
AC-003 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_003_protection_runs_when_discovery_throws
AC-003 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_003_protection_runs_when_operator_state_is_unreadable
AC-003 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_003_protection_runs_when_active_monitor_throws
AC-004 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_004_protection_sweep_is_idempotent_and_read_only
AC-004 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_004_scan_protection_precedes_admission
AC-005 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_005_universe_metadata_gate_fails_closed
AC-006 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_006_short_has_no_authoritative_engine
AC-006 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_006_long_coverage_gap_is_reported
AC-007 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_007_pending_mandate_is_documented_immediate_only
AC-007 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_007_unsupported_pending_mandate_fails_closed
AC-008 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_008_f7_is_not_the_admission_authority
AC-008 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_008_selector_handoff_fields_compatible
AC-009 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_009_new_code_activates_no_authority
AC-009 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_009_no_funded_or_exchange_credentials_added
AC-010 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_010_rollback_path_remains
AC-011 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_011_unreadable_evidence_fails_closed
AC-011 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_011_verdict_not_ready_when_short_authority_missing
AC-011 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_011_verdict_ready_when_all_technical_gates_pass
AC-011 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_011_unavailable_equity_fails_closed
AC-012 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_012_baseline_triage_recorded
AC-012 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_012_mode_inactive_is_a_prerequisite_not_a_blocker
AC-012 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_012_readiness_never_activates
AC-013 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_013_readiness_job_is_read_only
AC-014 -> tests/test_opip_r4_f8_cutover_readiness.py::test_ac_014_unsafe_exposure_blocks_protection_readiness

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-002 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-003 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-003 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-004 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-005 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-006 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-007 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-008 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-009 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-009 -> OHM-Trade-Agent-v1/app/jobs/report_paper_v2_cutover_readiness.py
AC-010 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-011 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R4-F8-BASELINE-TRIAGE.md
AC-012 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-013 -> OHM-Trade-Agent-v1/app/jobs/report_paper_v2_cutover_readiness.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F8-paper-v2-cutover-readiness.md
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r4_f8_cutover_readiness.py
AC-014 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py

DEFERRED DISCOVERIES:
- A Paper-v2 short engine is required for full cutover unless the owner ratifies a LONG-only paper mandate; this increment reports the gap and implements neither.
- The PCAND -> OPIPC candidate-identity bridge between F7 and the Paper-v2 handoff is required for R4-B wiring and is not implemented here.
- Live production Paper-v2 mode and Paper-v1 `control.json` remain `UNKNOWN_NEEDS_EVIDENCE` until the dormant deploy emits the bounded readiness probe.
- Whether the current immediate-only paper mandate should later gain pending-limit parity is a future owner decision; readiness reports the documented mandate and fails closed on an unknown one.

UNAPPROVED SCOPE CHANGES:
NONE
