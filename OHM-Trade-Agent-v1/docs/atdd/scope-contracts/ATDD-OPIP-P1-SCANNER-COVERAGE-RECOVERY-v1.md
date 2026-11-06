INCREMENT:
ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1

OWNER-APPROVED INTENT:

Repair the P1 production defect at baseline `fed71e3c`: one legitimately
ineligible candidate (a newly listed Kraken pair that cannot yet supply the
required 200 hourly candles — production instance KORUSD, 39/200 candles,
liquidity rank 49) made every isolated acquisition attempt incomplete
(`analyzed=199`, `skipped=1`), so every attempt was classified PARTIAL and every
promotion was rejected with PARTIAL_SCAN_NOT_ACTIONABLE. Acquisition gains
bounded, deterministic replacement from the ranked candidate pool the universe
builder already acquires, and the excluded candidate's identity and reason are
persisted instead of being discarded with the counters. Completeness must be
earned from valid data: no counter is redefined, no gate is weakened, and no
trading, AI, learning or execution authority changes.

ARCHITECTURE REFERENCES:

- AGENTS.md principle 20 (high-impact change disclosure) and principles 5, 6, 9
- CLAUDE.md (trading safety, minimal-change engineering)
- docs/atdd/scope-contracts/ATDD-SCANNER-RELIABILITY-M01.md (attempt ledger,
  manifest/sentinel integrity, promotion seam, resource bounds)
- docs/atdd/scope-contracts/ATDD-SCANNER-V2-EVIDENCE-M01.md (V2 evidence gate)
- docs/engineering/OPIP_SCANNER_RELIABILITY_M01.md
- docs/CONTROL_CENTER_V2_ARCHITECTURE.md (worker_status projection)

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
200 distinct eligible candidates whose market data all validates
WHEN:
the isolated scanner evaluates the ranked universe
THEN:
200 valid snapshots are produced, no replacement is evaluated, and the attempt
is complete

AC-002:
GIVEN:
a ranked universe with at least 201 eligible assets where candidate rank 49
supplies only 39 hourly candles and candidate rank 201 supplies valid data
WHEN:
the scanner evaluates the universe
THEN:
rank 49 is excluded with its recorded reason, rank 201 is evaluated as its
replacement, 200 distinct valid snapshots are produced and the attempt is
complete

AC-003:
GIVEN:
several consecutive candidates are legitimately ineligible
WHEN:
acquisition evaluates replacements
THEN:
each exclusion is recorded and evaluation continues in liquidity-rank order
without counting any invalid candidate as valid

AC-004:
GIVEN:
the candidate pool or the replacement budget cannot supply the target
WHEN:
acquisition terminates
THEN:
the attempt remains incomplete, the shortfall is explicit and promotion stays
non-actionable

AC-005:
GIVEN:
a candidate fails market-data validation
WHEN:
the scanner evaluates it
THEN:
it is classified as data rejected with its reason, contributes no snapshot, and
a replacement is considered when permitted

AC-006:
GIVEN:
an attempt attempted 201 candidates and produced 200 valid snapshots
WHEN:
coverage and exclusion evidence are persisted
THEN:
the achieved target, the total attempts and every exclusion with its reason are
readable, bounded, and never reported downstream as a coverage gap

AC-007:
GIVEN:
a historical schema-version-1 manifest and a recovered schema-version-2 manifest
WHEN:
completeness is evaluated
THEN:
the historical manifest keeps its original verdict, the recovered manifest is
complete only when the target is met by distinct underlying assets and the
counters reconcile, and inconsistent accounting fails closed

AC-008:
GIVEN:
a complete recovered manifest and a corrupted one
WHEN:
promotion validates the attempt
THEN:
the verified manifest is consumable, the corrupted manifest is rejected, and a
PARTIAL attempt stays non-actionable

AC-009:
GIVEN:
a completed attempt whose evidence records one replaced exclusion
WHEN:
an authenticated operator reads the scanner diagnostics
THEN:
the achieved coverage, the excluded pair and its reason are visible, and the
promotion outcome is presented as its own dimension

AC-010:
GIVEN:
no readable scanner evidence exists
WHEN:
the operator opens scanner diagnostics
THEN:
an explicit unavailable or no-evidence state is shown and no healthy or complete
result is fabricated

AC-011:
GIVEN:
the current operator and trading restrictions
WHEN:
the corrected scanner executes
THEN:
it does not alter operator mode, does not enable learning, grants no funded
authority and keeps the legitimate 200-candle eligibility gate

AC-012:
GIVEN:
a replacement budget of zero, one, twenty-five or one hundred
WHEN:
every replacement candidate is ineligible
THEN:
no more candidates are attempted than the configured budget permits

AC-013:
GIVEN:
an operator-configured replacement budget in the owner `.env`, and an isolated
`opip-scan-worker` that declares its environment explicitly and inherits no
environment file
WHEN:
the production Compose configuration is resolved and the worker starts
THEN:
the configured non-secret budget reaches the worker unchanged (an explicit `0`
disables replacement and is never replaced by the default), the documented
default of 25 applies only when the variable is unset or empty, an out-of-range
or unparseable value is rejected by the existing settings validation rather than
silently defaulted, and no exchange, AI or notification credential is introduced
into the acquisition container

EXPLICITLY OUT OF SCOPE:

- Any change to V2 scoring, thresholds, risk sizing, minimum reward-to-risk,
  margin or liquidity rules, fees, slippage or notification eligibility.
- Any partial-promotion policy: a shortfall remains non-actionable.
- Operator-mode changes, learning activation, funded or paper trading activation.
- Redesign of scanner scheduling, admission, the attempt ledger or the
  promotion control plane.
- New databases, queues, services, schedulers or configuration frameworks.
- Backfilling historical scan evidence.
- Production deployment, restart or configuration change.

FROZEN BOUNDARIES:

- The isolated worker stays acquisition-only: no AI, notification, canonical
  writer or execution import, and no promotion or trading authority.
- `is_complete()` keeps SHA-256, sentinel, row-count and identity/fence checks and
  may only become stricter; it is never relaxed to tolerate missing evaluation.
- Fixed risk sizing, minimum R:R, position verification and every execution
  safeguard are untouched.
- Replacement is bounded by `opip_scan_replacement_candidates` (default 25,
  maximum 100) and by the existing attempt deadline and concurrency ceiling.
- No additional Kraken AssetPairs or Ticker request is introduced: replacement
  candidates come from the already-ranked pool.
- Durable stores remain append-only; no existing artifact is migrated or deleted.

ACCEPTANCE TEST TRACEABILITY:

AC-001 -> tests/test_opip_scan_coverage_recovery_v1.py::test_complete_acquisition_needs_no_replacement
AC-002 -> tests/test_opip_scan_coverage_recovery_v1.py::test_short_history_candidate_is_replaced_and_scan_completes
AC-002 -> tests/test_opip_scan_coverage_recovery_v1.py::test_worker_persists_a_complete_recovered_attempt
AC-003 -> tests/test_opip_scan_coverage_recovery_v1.py::test_repeated_invalid_replacements_are_recorded_and_order_preserved
AC-004 -> tests/test_opip_scan_coverage_recovery_v1.py::test_exhausted_candidate_pool_stays_incomplete
AC-005 -> tests/test_opip_scan_coverage_recovery_v1.py::test_data_quality_rejection_is_recorded_and_replaced
AC-005 -> tests/test_opip_scan_coverage_recovery_v1.py::test_real_provider_gate_classifies_short_history_as_a_skip
AC-006 -> tests/test_opip_scan_coverage_recovery_v1.py::test_coverage_evidence_keeps_totals_and_recovered_exclusions
AC-006 -> tests/test_opip_scan_coverage_recovery_v1.py::test_exclusion_evidence_is_bounded_and_reports_truncation
AC-006 -> tests/test_opip_scan_coverage_recovery_v1.py::test_qualification_adapter_reports_coverage_gaps_not_observations
AC-007 -> tests/test_opip_scan_coverage_recovery_v1.py::test_legacy_manifest_keeps_its_original_interpretation
AC-007 -> tests/test_opip_scan_coverage_recovery_v1.py::test_recovered_coverage_is_complete_but_inconsistent_coverage_is_not
AC-008 -> tests/test_opip_scan_coverage_recovery_v1.py::test_recovered_manifest_is_consumable_and_corruption_still_fails
AC-008 -> tests/test_opip_scan_coverage_recovery_v1.py::test_partial_attempt_is_still_not_actionable
AC-009 -> tests/test_opip_scan_coverage_recovery_v1.py::test_control_center_scanner_view_exposes_coverage_and_exclusions
AC-010 -> tests/test_opip_scan_coverage_recovery_v1.py::test_missing_scanner_evidence_is_never_reported_as_healthy
AC-011 -> tests/test_opip_scan_coverage_recovery_v1.py::test_scanner_acquisition_keeps_no_notification_ai_or_execution_authority
AC-012 -> tests/test_opip_scan_coverage_recovery_v1.py::test_replacement_attempts_are_bounded
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_worker_service_declares_the_bounded_replacement_budget
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_resolved_worker_environment_carries_the_configured_value
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_resolved_worker_environment_defaults_when_the_budget_is_unset
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_scanner_enforces_the_configured_budget
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_explicit_zero_disables_replacement
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_invalid_budget_is_rejected_by_validation
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_invalid_budget_prevents_settings_from_resolving
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_worker_never_starts_acquisition_with_an_invalid_budget
AC-013 -> tests/test_opip_scan_replacement_budget_propagation_v1.py::test_missing_setting_uses_the_documented_default

IMPLEMENTATION MAP:

AC-002 -> OHM-Trade-Agent-v1/app/scanner/universe.py
AC-001 -> OHM-Trade-Agent-v1/app/scanner/universe.py
AC-002 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-003 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-004 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-005 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-012 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-012 -> OHM-Trade-Agent-v1/app/core/config.py
AC-013 -> OHM-Trade-Agent-v1/app/core/config.py
AC-013 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-013 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_scan_replacement_budget_propagation_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/jobs/scan_worker.py
AC-010 -> OHM-Trade-Agent-v1/app/jobs/scan_worker.py
AC-007 -> OHM-Trade-Agent-v1/app/services/scan_manifest.py
AC-008 -> OHM-Trade-Agent-v1/app/services/scan_manifest.py
AC-006 -> OHM-Trade-Agent-v1/app/services/scan_result_promotion.py
AC-008 -> OHM-Trade-Agent-v1/app/services/scan_result_promotion.py
AC-009 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-009 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-009 -> OHM-Trade-Agent-v1/control-center-v2/test/api.test.mjs
AC-010 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_scan_coverage_recovery_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_remediation_v1.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_scan_remediation_v1.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1.md
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:

- Exact historical attribution of the 2026-11-04/06 exclusions was unavailable
  before this change because `worker_status.json` persisted counters only; the
  corrected evidence now records pair, rank, status and reason, so the next
  naturally scheduled attempts establish attribution without further inference.
- The untreated operational question (independent of this defect): the unified
  cycle reports `effective mode is MONITOR`, which suppresses broad discovery and
  gates promotion, while isolated attempts were observed being dispatched. That
  contradiction requires read-only production log review and a separately
  owner-gated operator-mode decision; no mode is changed here.
- KORUSD reaches the 200-candle requirement around 2026-11-12T22:00Z, so the
  blockage would also self-clear; the structural defect (one ineligible candidate
  blocking every scan for up to ~8.3 days, and recurring on every new listing) is
  what this increment fixes.

- Adjacent observation, deliberately NOT changed here: the same container also
  declares no `OPIP_SCAN_MAX_WORKERS` or `OPIP_SCAN_BATCH_ASSETS`, so the isolated
  worker runs those tightening-only ceilings at their application defaults (4 and
  50). That predates this increment, cannot widen concurrency or memory above the
  documented ceilings, and propagating them is a separate, separately approved
  change.
- The Compose-resolution assertions in
  `tests/test_opip_scan_replacement_budget_propagation_v1.py` skip on a host with
  no Compose CLI, and are bounded (one probe per session, two resolved
  invocations: an explicit `0` and unset). The declaration assertion runs
  everywhere, and CI (Linux, with Compose) executes the resolved cases.

UNAPPROVED SCOPE CHANGES:
NONE
