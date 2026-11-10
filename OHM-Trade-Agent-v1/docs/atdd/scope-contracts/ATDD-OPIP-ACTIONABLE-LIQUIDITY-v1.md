INCREMENT:
ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1

OWNER-APPROVED INTENT:
Implement, behind default-off guards, the verified-liquidity, calibrated-probability,
protection-admission and rolling-24h-cap notification conditions, plus the corrected depth
measurement that makes them measurable. No notification authority is widened: every guard can only
refuse, and all of them ship disabled so the deployed policy is unchanged. The existing
`docs/atdd/ACTIVE_INCREMENT` pointer belongs to another workstream and is deliberately NOT modified;
this increment is validated by passing its name explicitly to the scope checker.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/services/liquidity_verification.py: bounded multi-observation stability.
- OHM-Trade-Agent-v1/app/services/opportunity_evidence.py: prospective decision-time evidence.
- OHM-Trade-Agent-v1/app/services/ai_advisory_isolation.py: AI advisory separated from evidence.
- OHM-Trade-Agent-v1/app/scanner/execution_validation.py: tri-state verified band depth.
- OHM-Trade-Agent-v1/app/exchanges/kraken.py: clamped deep-book read for finalists only.
- OHM-Trade-Agent-v1/app/services/kraken_transport.py: Depth TTL entry.
- OHM-Trade-Agent-v1/app/services/actionable_admission.py: guarded conditions C3b/C5b/C6b/C7b.
- OHM-Trade-Agent-v1/app/services/protection_health.py: F11 admission stamp publish/read contract.
- OHM-Trade-Agent-v1/app/core/config.py: versioned guard settings, all default off.

APPROVED ACCEPTANCE CRITERIA:

AC-113:
GIVEN: three independent depth observations for the same verified venue and instrument
WHEN: qualifying weaker-side depth does not persist across every observation
THEN: the candidate is refused as VANISHING and cannot become actionable

AC-114:
GIVEN: a completed finalist evaluation
WHEN: a prospective evidence record is written
THEN: it carries only decision-time fields, is idempotent on its key, and is rejected if it
     carries any future-derived outcome field

AC-115:
GIVEN: the authoritative protection observation
WHEN: the F11 admission stamp is published
THEN: publication is atomic, replay-protected, and its freshness bound is the configured maximum
     tightened by the derived p99 - never widened by a small sample

AC-116:
GIVEN: an AI timeout, budget exhaustion or provider failure
WHEN: deterministic candidates exist
THEN: every deterministic candidate is preserved with an explicit advisory reason code, and the
     AI opinion cannot admit anything

AC-101:
GIVEN: verified weaker-side depth of exactly 600,000.00 inside a 0.5% band
WHEN: the depth verdict is computed
THEN: the verdict is PASS and the comparison is inclusive

AC-102:
GIVEN: depth evidence that is partial and below threshold without proven coverage
WHEN: the depth verdict is computed
THEN: the verdict is UNKNOWN, never FAIL, and never zero

AC-103:
GIVEN: observed depth at or above threshold with UNPROVEN coverage
WHEN: the depth verdict is computed
THEN: the verdict is PASS, because the observation is a subset of the truth

AC-104:
GIVEN: any exhaustive accumulation over band levels
WHEN: depth is summed
THEN: the arithmetic is exact decimal, so a mathematically exact threshold sum passes

AC-105:
GIVEN: the notification-admission guards are left at their defaults
WHEN: any plan is evaluated
THEN: the verdict is identical to the shipped policy, because every guard adds no refusal

AC-106:
GIVEN: a complete, fully eligible synthetic plan and every guard enabled
WHEN: admission is evaluated
THEN: the plan is ADMITTED exactly once, proving the gate is not vacuously closed

AC-107:
GIVEN: a calibrated probability of 0.8999 or an uncalibrated status
WHEN: admission is evaluated with the probability guard enabled
THEN: the notification is refused with an attributable reason

AC-108:
GIVEN: a protection stamp that is missing, stale, contradictory, regressed or unhealthy
WHEN: admission is evaluated with the protection guard enabled
THEN: the notification is refused and protection delivery is unaffected

AC-109:
GIVEN: two new-entry admissions already counted in the rolling window
WHEN: a third is evaluated
THEN: it is refused, and an unknown window count also refuses

AC-110:
GIVEN: the F11 freshness bound
WHEN: fewer than twenty successful observations exist
THEN: the bound is unproven and every consumer fails closed

AC-111:
GIVEN: a depth request above the venue ceiling
WHEN: the adapter issues it
THEN: the count is clamped and returned coverage is what the caller verifies

AC-112:
GIVEN: the depth and execution-feasibility contracts
WHEN: both are evaluated on one book
THEN: they remain separate verdicts and neither substitutes for the other

EXPLICITLY OUT OF SCOPE:
- Lowering, approximating or replacing the 0.90 calibrated-probability requirement.
- Fabricating a calibration artifact of any kind.
- Replacing the $600K/$500K weaker-side band requirement with a simulated market order or VWAP.
- Changing F11 computation, incident ownership, funded authority or protection delivery.
- Adding a full-universe deep-book poll, a new service, datastore, scheduler or worker.
- Wiring the protection stamp publisher into the production cycle in this increment.
- Moving or rewriting `docs/atdd/ACTIVE_INCREMENT`, or any other increment's contract.
- Any merge, deployment, activation, Telegram delivery, billable AI call, learning activation,
  Paper V2 activation or funded trading.
- Deleting, truncating or rewriting historical delivery or canonical evidence.

FROZEN BOUNDARIES:
- No guard may widen notification authority; all ship disabled.
- No change to F11 computation, incident ownership, funded authority or protection delivery.
- No change to `docs/atdd/ACTIVE_INCREMENT` or another increment's contract.
- No new service, datastore, scheduler or background worker.
- No merge, deployment, activation, Telegram send or AI call in this increment.

ACCEPTANCE TEST TRACEABILITY:
AC-101 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_a2_exactly_six_hundred_thousand_passes_inclusive
AC-102 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_d2_partial_evidence_below_threshold_is_unknown_not_fail
AC-103 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_d3_partial_evidence_at_or_above_threshold_is_pass
AC-104 -> tests/test_opip_actionable_liquidity_v1.py::test_ec01_exact_decimal_arithmetic_at_the_boundary
AC-105 -> tests/test_opip_actionable_liquidity_v1.py::test_feature_disabled_guards_change_nothing
AC-106 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_h1_a_fully_eligible_plan_is_admitted_exactly_once
AC-107 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_g1_below_ninety_percent_is_refused
AC-108 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_f1_suspended_protection_refuses
AC-109 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_i1_rolling_cap_refuses_a_third_entry
AC-110 -> tests/test_opip_protection_stamp_v1.py::test_f11_below_min_samples_is_unproven
AC-111 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_b1_depth_request_is_clamped_and_returns_parsed_book
AC-112 -> tests/test_opip_actionable_liquidity_v1.py::test_ac_c5_depth_and_execution_feasibility_are_independent_contracts
AC-113 -> tests/test_opip_liquidity_pipeline_v1.py::test_p5_vanishing_liquidity_cannot_qualify
AC-114 -> tests/test_opip_liquidity_pipeline_v1.py::test_p1_a_finalist_evaluation_produces_a_valid_record
AC-115 -> tests/test_opip_liquidity_pipeline_v1.py::test_p2_derived_bound_tightens_but_never_widens
AC-116 -> tests/test_opip_liquidity_pipeline_v1.py::test_p6_ai_failure_preserves_every_deterministic_candidate

IMPLEMENTATION MAP:
AC-101 -> OHM-Trade-Agent-v1/app/scanner/execution_validation.py
AC-101 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-102 -> OHM-Trade-Agent-v1/app/scanner/execution_validation.py
AC-102 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-103 -> OHM-Trade-Agent-v1/app/scanner/execution_validation.py
AC-103 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-104 -> OHM-Trade-Agent-v1/app/scanner/execution_validation.py
AC-104 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-105 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-105 -> OHM-Trade-Agent-v1/app/core/config.py
AC-105 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-106 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-106 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-107 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-107 -> OHM-Trade-Agent-v1/app/core/config.py
AC-107 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-108 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-108 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-108 -> OHM-Trade-Agent-v1/tests/test_opip_protection_stamp_v1.py
AC-109 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-109 -> OHM-Trade-Agent-v1/app/core/config.py
AC-109 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-110 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-110 -> OHM-Trade-Agent-v1/tests/test_opip_protection_stamp_v1.py
AC-111 -> OHM-Trade-Agent-v1/app/exchanges/kraken.py
AC-111 -> OHM-Trade-Agent-v1/app/services/kraken_transport.py
AC-111 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-112 -> OHM-Trade-Agent-v1/app/scanner/execution_validation.py
AC-112 -> OHM-Trade-Agent-v1/tests/test_opip_actionable_liquidity_v1.py
AC-101 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1.md
AC-112 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1.md

AC-113 -> OHM-Trade-Agent-v1/app/services/liquidity_verification.py
AC-113 -> OHM-Trade-Agent-v1/tests/test_opip_liquidity_pipeline_v1.py
AC-114 -> OHM-Trade-Agent-v1/app/services/opportunity_evidence.py
AC-114 -> OHM-Trade-Agent-v1/tests/test_opip_liquidity_pipeline_v1.py
AC-115 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-115 -> OHM-Trade-Agent-v1/tests/test_opip_liquidity_pipeline_v1.py
AC-116 -> OHM-Trade-Agent-v1/app/services/ai_advisory_isolation.py
AC-116 -> OHM-Trade-Agent-v1/tests/test_opip_liquidity_pipeline_v1.py
AC-115 -> OHM-Trade-Agent-v1/app/services/protection_admission_stamp_io.py
AC-116 -> OHM-Trade-Agent-v1/app/services/opportunity_pipeline_wiring.py
AC-116 -> OHM-Trade-Agent-v1/tests/test_opip_opportunity_wiring_v1.py
AC-101 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
DEFERRED DISCOVERIES:
- The AI-dependency correction (P6) and the depth-based monitoring classification remain designed
  but unwired; they are explicitly inactive and require their own authorisation.
- The protection stamp publisher needs a production sample of successful observation intervals
  before its derived bound can be relied on.

UNAPPROVED SCOPE CHANGES:
NONE
