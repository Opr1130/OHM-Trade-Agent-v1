INCREMENT:
ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1

OWNER-APPROVED INTENT:
The owner approved two safety repairs and one test-infrastructure repair after the
2026-11-08 audit. FIX-001: the LONG execution-evidence gate in
app/services/target_attainability.py reimplemented "is this a conservative
runtime?" locally and, because it read Settings.app_env whose model default is
"development", it answered "not conservative" whenever APP_ENV was absent. The
canonical authority app/core/runtime_environment.conservative_runtime() defaults to
"unknown" and answers "conservative". The two disagreed in the unsafe direction, so
the gate that exists to reject a LONG carrying no execution evidence could reject
nothing in a deployment that never set APP_ENV. This increment deletes the
duplicate and makes the canonical authority the only answer, so unset, unknown and
production identities all fail closed. FIX-003: APP_ENV was declared as production
only in an unshipped draft template and in no Compose literal or deploy script, so
the production-only hardening in app/core/config.py could be skipped by omission.
This increment pins APP_ENV as a checked-in Compose literal for the core service,
using the same literal-over-env_file mechanism the repository already uses for every
OPIP_* mode pin, so a stale .env.core cannot silently change it. The third repair
makes the acceptance suite hermetic: Settings() requires a WEBHOOK_SECRET boot field
with no default and get_settings() is cached, so a workstation without a local .env
produced 16 spurious failures, one test errored on Windows because it read a UTF-8
HTML file with the platform default codec, and the result depended on collection
order. No retry, no budget change, no threshold change, no new authority, no scoring
change and no activation is authorized here. No deployment, merge, funded trading,
paper admission, learning activation or IRR activation is authorized by this
increment.

ARCHITECTURE REFERENCES:
- app/core/runtime_environment.py: the single runtime-identity authority.
- app/services/target_attainability.py: the LONG execution-evidence gate that duplicated it.
- app/core/config.py: Settings, the required WEBHOOK_SECRET boot field, and the production-only TradingView hardening at the APP_ENV branch.
- docker-compose.yml: the core service's literal mode pins that override env_file.
- docs/release/README.md: literal-over-env_file activation authority and stale-.env safety.
- tests/conftest.py: shared fixtures; the boot field and test identity must be supplied here.
- .github/workflows/pytest.yml: the CI environment (APP_ENV=test) and the production Compose validation step.
- AGENTS.md principles 5 (gates fail closed), 6 (preserve safeguards), 13 (do not weaken tests), 20 (high-impact change discipline).

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the runtime identity is unset, unknown or explicitly production
WHEN: the runtime-identity question is asked
THEN: target_attainability returns exactly the answer of app.core.runtime_environment.conservative_runtime() for every identity, and no local reimplementation of that decision remains.

AC-002:
GIVEN: APP_ENV is absent from the environment and from any .env file
WHEN: a LONG candidate carrying no execution validation is evaluated for target attainability
THEN: the candidate fails qualification with the reason LONG execution evidence is unavailable, and no candidate, score or alert is produced from missing execution evidence.

AC-003:
GIVEN: APP_ENV is present but carries an unrecognised value, including an empty value, mixed case, staging, prod, qa, rehearsal or unknown
WHEN: the runtime-identity question is asked and a LONG with no execution evidence is evaluated
THEN: the runtime is treated as conservative and the candidate is rejected, so an unknown identity can never become a permissive one.

AC-004:
GIVEN: APP_ENV is production in any letter case
WHEN: a LONG candidate carries an unavailable execution status, or a missing spread, or a missing round-trip drag
THEN: each missing-evidence condition produces its own rejection reason and the candidate fails qualification.

AC-005:
GIVEN: APP_ENV is explicitly development, dev, test or testing
WHEN: a structurally valid LONG candidate is evaluated
THEN: missing execution evidence is not the reason for rejection, so developer and test workflows keep their documented behaviour, while excessive spread or excessive drag still rejects in every environment.

AC-006:
GIVEN: the core service in docker-compose.yml
WHEN: its environment is inspected
THEN: APP_ENV is a literal in the service environment block with the value production, and it is declared in the same block that already overrides env_file, so a stale .env.core cannot change, widen or disable it.

AC-007:
GIVEN: a stale .env.core that sets APP_ENV to development
WHEN: the production Compose is rendered and validated
THEN: the rendered core-service value remains production and the validation step fails closed if it does not.

AC-008:
GIVEN: the acceptance suite running on a workstation with no .env and no exported WEBHOOK_SECRET or APP_ENV
WHEN: the suite is collected and run
THEN: the required boot field and the test runtime identity are supplied by the shared fixture module so no test fails or passes because of ambient environment, and no acceptance test reads a repository HTML file without an explicit UTF-8 encoding.

AC-009:
GIVEN: the pinned production identity and the corrected gate
WHEN: the core configuration and the release-profile literals are inspected
THEN: funded trading stays absent, paper v2 stays off, the committee stays off, the Feature Bus stays shadow, and no risk, reward-to-risk, net-profit or capital-fraction default changes.

AC-010:
GIVEN: this increment
WHEN: the scope contract and the increment pointer are read
THEN: docs/atdd/ACTIVE_INCREMENT names this increment and its scope contract exists and declares it.

EXPLICITLY OUT OF SCOPE:
- Any change to MIN_NET_PROFIT, MIN_REWARD_TO_RISK, risk_per_trade_pct, PRODUCTION_MAX_CAPITAL_FRACTION or any other economic or risk default.
- Any change to scoring, ranking, thresholds, the geometry kernel, the advisory policy or the AI router.
- Any retry, budget, provider or model change in the AI router.
- Any activation of paper v2, the Feature Bus authority, committee runtime authority or TARGET_PAPER.
- Any funded, live or exchange order placement, modification, cancellation or confirmation.
- Any change to F11 protection, reservation, portfolio or learning authority planes.
- Any edit to the completed ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1 or ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1 acceptance modules.
- Any new scheduler, service, database, dependency or deployment path.
- Any bulk change of read_text() call sites: only the acceptance modules that read a non-ASCII repository file are corrected.

FROZEN BOUNDARIES:
- Trading authority: unchanged. Funded trading disabled. F11 unchanged.
- Gate direction: this increment may only make a gate stricter, never more permissive.
- Durable evidence: no production store, registry or learning evidence is read, written, migrated or deleted.
- Runtime identity precedence: the Compose literal wins over env_file, exactly as for every existing OPIP_* pin.
- Test semantics: no assertion is relaxed, skipped, xfailed or deselected; fixtures may only gain the configuration a test needs to run deterministically.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_001_target_attainability_returns_the_canonical_answer
AC-001 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_001_target_attainability_returns_the_canonical_answer_when_unset
AC-002 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_002_absent_identity_rejects_missing_execution_evidence
AC-002 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_002_absent_identity_fails_long_qualification
AC-003 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_003_unknown_identity_is_never_permissive
AC-004 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_004_production_rejects_each_missing_evidence_kind
AC-005 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_005_non_production_keeps_documented_developer_behaviour
AC-005 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_005_bad_evidence_still_rejects_in_non_production
AC-006 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_006_core_service_pins_app_env_as_a_literal
AC-007 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_007_ci_proves_the_literal_beats_a_stale_env_core
AC-008 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_008_fixture_module_supplies_the_boot_field_and_test_identity
AC-008 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_008_non_ascii_repository_html_is_read_with_explicit_utf8
AC-009 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_009_no_release_posture_or_economic_default_is_widened
AC-009 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_009_production_identity_closes_the_unauthenticated_legacy_ingress
AC-009 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_009_local_learning_stays_remote_only_under_the_production_identity
AC-010 -> tests/test_opip_runtime_authority_failclosed_v1.py::test_ac_010_contract_declares_the_active_increment

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/target_attainability.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/target_attainability.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/target_attainability.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/target_attainability.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-006 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-007 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-007 -> .github/workflows/pytest.yml
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-008 -> OHM-Trade-Agent-v1/tests/conftest.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-009 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1.md
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_runtime_authority_failclosed_v1.py

DEFERRED DISCOVERIES:
The 2026-11-08 audit could not read the host .env, so it is not known whether the
production deployment already set APP_ENV=production. If it did, the duplicate
authority was latent rather than active; if it did not, the LONG execution-evidence
gate has been inert and missing-evidence LONG candidates could qualify. The
increment is correct in both cases and does not depend on resolving which. Two
further observations are deferred. First, tests/test_target_attainability.py builds
LONG fixtures that carry no spread or drag evidence and therefore only pass when the
runtime is non-conservative; they are not modified here because strengthening
another increment's fixtures would expand this scope, and CI supplies APP_ENV=test
exactly as this increment's fixture module now does. Second, max_open_trades is 2 in
Settings while trade_action_gate forwards max_positions 3; the two are consumed by
different layers and reconciling them is a policy decision, not a defect repair.
31 further test modules call read_text() without an explicit encoding; only the
module that reads a non-ASCII file is corrected here, and the rest are recorded as
latent portability risk.

COMPATIBILITY SURFACE OF THE PINNED PRODUCTION IDENTITY (owner-visible):
The audit enumerated every consumer of the runtime identity before pinning it, and
five of the six consequences are strict hardenings. (1) The LONG
execution-evidence gate becomes fail-closed, which is the purpose of this
increment. (2) app/services/learning_scheduler.py already used the canonical
authority and already documents that a missing identity must be treated as
production, so local learning now correctly returns REMOTE_ONLY instead of running
on the trading host. (3) app/core/config.py requires a strong
TRADINGVIEW_INTERNAL_VERIFICATION_VALUE only when TRADINGVIEW_V2_ENABLED is true,
and a strong OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE only when
OPIP_TRADEVISOR_ENABLED is true; both default to false and neither is set in the
core Compose, so no boot failure arises from this increment. An operator who enables
either with a documented default or a short value will now fail closed at startup
instead of running with a weak secret, and must generate a stronger value first.
(4) app/opip/analytics/runtime.py will report label PRODUCTION rather than
NON_PRODUCTION, which is the truthful report. (5) The material operational
consequence is /webhooks/tradingview: with TRADINGVIEW_V1_ENABLED unset the guard
enabled that route only because a missing APP_ENV made production look like a
development deployment, and it granted that access with no secret at all. Pinning
the identity now returns 404 there. An operator who still feeds signals through the
legacy bridge must explicitly set TRADINGVIEW_V1_ENABLED=true and a
TRADINGVIEW_V1_WEBHOOK_SECRET of at least 32 characters, which is the documented,
separately authenticated path. This is a hardening, but it can stop legacy signal
delivery, so it must be stated in the release receipt and the route must be verified
after deployment.

UNAPPROVED SCOPE CHANGES:
NONE

