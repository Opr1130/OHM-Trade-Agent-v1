INCREMENT:
ATDD-RELEASE-PIPELINE-v1

OWNER-APPROVED INTENT:
This is the OWNER-authorized Release Pipeline v1 increment. It introduces an allowlisted release-profile contract (`SAFE_BASELINE`, `EVIDENCE_SHADOW`, future `TARGET_PAPER`) as the explicit, fail-closed activation authority for the production evidence plane, and it records the bounded OWNER governance supersession that permits `EVIDENCE_SHADOW` to place the Feature Bus into `shadow`.

The OWNER explicitly authorizes `OPIP_FEATURE_BUS_MODE=shadow` ONLY under the selected, validated `EVIDENCE_SHADOW` release profile. This supersedes the earlier repository-current posture assertion (`OPIP_FEATURE_BUS_MODE=off`) that older increments froze because those increments were not authorized to activate it. The supersession is narrow: it does not change any safety requirement, and `TARGET_PAPER` (Paper-v2) remains BLOCKED.

Do not edit history. The older contract statements that the Feature Bus remained `off` at their increment are preserved as historical scope; the superseded *current-runtime* prohibition is replaced by the release-profile control plane, and the acceptance tests that wrongly converted historical scope into a permanent prohibition are corrected to prove both facts.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx` (authority, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`) and `docs/architecture/v1.4.3/ARCHITECTURE.md`.
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R3: "The Feature Bus mode stays `off` during R3 unless a separate owner approval changes it." This increment is that separate OWNER approval, bounded to `EVIDENCE_SHADOW`.
- `docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md` AC-016/AC-020/AC-021: the bounded, dual-gated, non-authoritative SHADOW capture machinery this profile authorizes.
- `docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md` AC-014: the no-weakening governance control amended narrowly here.
- `docs/architecture/OPIP_F6_OWNER_ENABLEMENT_PACKETS.md` Packet A.
- `docs/architecture/v1.2/A_PAPER_MANDATE.md` and `CODING_BOUNDARY_CONTRACT.md`: paper-only authority, no funded execution.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the allowlisted release-profile contract
WHEN:
the profiles are enumerated and resolved
THEN:
the profiles are exactly the allowlist `SAFE_BASELINE`, `EVIDENCE_SHADOW` and `TARGET_PAPER`; `SAFE_BASELINE` resolves to the non-authoritative baseline modes (Feature Bus `off`, canonical writer `off`, target spine `off`, Paper-v2 `off`, Committee `off`); `EVIDENCE_SHADOW` resolves to exactly Feature Bus `shadow`, canonical writer `shadow`, target spine `shadow`, Paper-v2 `off` and Committee `off`; `TARGET_PAPER` is present but BLOCKED (it may not be selected or validated as ready); an unknown, malformed, differently-cased or whitespace-padded profile name fails closed; and the contract grants no funded, exchange, order, Committee or Telegram authority

AC-002:
GIVEN:
the selected release profile and the repository-controlled production compose
WHEN:
the profile is applied and the architecture gate is evaluated
THEN:
the deploy resolves the selected profile into the exact fixed modes of its allowlist entry (a profile-to-environment resolver returning only the profile's declared keys), the core service `environment` block carries those exact literal modes including `OPIP_PAPER_V2_MODE=off` and `OPIP_COMMITTEE_MODE=off` so `env_file: .env` cannot override them, the gate PASSES only when the observed runtime posture equals the selected profile's allowed modes and FAILS CLOSED otherwise, and a stale or free-form `.env` value cannot elevate `SAFE_BASELINE` to `EVIDENCE_SHADOW`, inject an unexpected `OPIP_*` mode key, or change any mode

AC-003:
GIVEN:
the older increments that froze the Feature Bus `off` and this later OWNER authorization
WHEN:
the historical contracts and the supersession are inspected
THEN:
the older contracts still record their historical `off` posture and their historical intent (nothing is rewritten to claim they authorized activation), a later OWNER-authorized supersession contract records the bounded `EVIDENCE_SHADOW` authorization, the current repository posture is governed by the release profile rather than by a permanent prohibition, and the supersession changes no safety requirement (one canonical writer/history, one Feature Bus, one F3-F7 spine, no second scheduler/evidence store/calibration system, point-in-time correctness, bounded capture, protection independence, stale `.env` cannot activate, legacy sole new-entry authority, Paper-v2 off, target spine non-authoritative)

AC-004:
GIVEN:
the lifecycle/no-weakening governance guards (the bridge AC-014 guard and the movable-pointer scope-control guard)
WHEN:
they are audited against the supersession
THEN:
neither guard converts an historical runtime literal into a permanent prohibition, both still prove that a completed increment remains identifiable through its own scope contract and that its substantive isolation, authority and feature-bus assertions remain present in its acceptance module, they record that a later OWNER-approved increment may supersede an old runtime posture only with explicit owner provenance and its own acceptance tests, and a mutation/adversarial check proves that an unapproved deletion or weakening of the isolation assertions still fails

AC-005:
GIVEN:
the release architecture gate and its receipt
WHEN:
a release posture is evaluated for a profile
THEN:
the gate fails closed on any missing, malformed or inconsistent profile or runtime posture, reports a deterministic verdict and a concise receipt (profile, new-entry authority, funded authority, Paper-v2 and Committee state, protection posture, and each named check), asserts `PAPER_V2_REMAINS_OFF`, `COMMITTEE_RUNTIME_AUTHORITY_ABSENT`, `FUNDED_AUTHORITY_ABSENT`, `FUNDED_CREDENTIAL_PATH_ABSENT_FROM_PAPER` and `PROTECTION_INDEPENDENT`, keeps `NEW_ENTRY_AUTHORITY=LEGACY_ONLY` for `SAFE_BASELINE` and `EVIDENCE_SHADOW`, and grants no authority and performs no write

AC-006:
GIVEN:
the activated evidence plane
WHEN:
the runtime posture is inspected
THEN:
`OPIP_PAPER_V2_MODE` and `OPIP_COMMITTEE_MODE` are explicitly pinned to `off` in the core service, the legacy path remains the sole new-entry paper authority, the target spine is shadow and non-authoritative, the bounded capture cron entries remain the scheduler mechanism and are never invoked from inside the protected unified cycle, the rollback posture is `SAFE_BASELINE` (the rollback Compose override deterministically disables capture even when the previous code SHA has a different profile marker), and no funded/live, exchange, order, margin or Committee authority is introduced

AC-007:
GIVEN:
a pull request or a commit pushed to `main`
WHEN:
the release CI workflow evaluates the commit
THEN:
it runs the exact allowlisted `EVIDENCE_SHADOW` architecture evaluator, emits `ARCHITECTURE_GATE`, `PROFILE`, `NEW_ENTRY_AUTHORITY`, `FUNDED_AUTHORITY`, `PAPER_V2`, `COMMITTEE_MODE`, `PROTECTION`, `FEATURE_BUS`, `CANONICAL_WRITER` and `TARGET_SPINE`, retains a bounded receipt, and a successful main run produces an exact-SHA release-candidate receipt only after pytest, ATDD scope, architecture and security gates pass; the candidate does not deploy

AC-008:
GIVEN:
an exact-SHA release candidate and the existing issue #64 deployment control plane
WHEN:
the owner requests `/deploy-profile <PROFILE> <40-char-sha>`
THEN:
only the repository OWNER on issue #64 (or repository-owner-only manual dispatch) is accepted; `EVIDENCE_SHADOW` is the only deployable profile, `SAFE_BASELINE` is rollback-only, and `TARGET_PAPER` remains blocked; the SHA equals the current `main`; the exact-SHA pytest/ATDD/security/architecture workflow is green; the checked-out SHA's Compose profile marker and literal modes match the requested profile; the host refuses untracked or unexpected ignored files in the application build context, and Docker excludes secrets and generated caches; approval is auditable in the workflow receipt; and deployment uses only the existing forced-command `deploy <sha>` path, with the legacy `/deploy <sha>` spelling subject to identical `EVIDENCE_SHADOW` gates

AC-009:
GIVEN:
an owner-approved exact-SHA EVIDENCE_SHADOW deployment
WHEN:
the candidate services and scheduler have started
THEN:
the host records read-only canonical cursors before deployment, verifies the running container image label and fixed mode literals against the approved SHA, and invokes a read-only verifier bounded to 360 seconds; that verifier requires two fresh consecutive 60-second snapshots created after candidate readiness, at least one matching prospective F5 record whose source cutoff is not later than its evaluation time, HEALTHY read-only protection, and the target spine's inert no-source/no-handoff posture; the host also proves each unified/capture scheduler entry occurs once and retains flock/timeout bounds; any missing or malformed evidence fails before the core commit point and enters the existing rollback transaction

AC-010:
GIVEN:
the runtime verifier or a pre-commit deploy assertion fails
WHEN:
the existing `ohm-deploy` rollback runs
THEN:
it restores the previous code SHA but overlays explicit SAFE_BASELINE literals for Feature Bus, writer, target spine, Paper-v2 and Committee; core, writer and paper topology health must pass and the running core's effective mode literals must be re-read before the host emits `OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS`; otherwise rollback remains unproven and the workflow fails closed; the deployment receipt cannot report runtime success without the exact approved SHA, runtime marker, capture, protection, scheduler and rollback dispositions

EXPLICITLY OUT OF SCOPE:
- Activating TARGET_PAPER, Paper-v2, the Committee, or any funded/live/exchange/order authority
- Deleting, rewriting or rescoping the historical ATDD increments that recorded the Feature Bus `off`
- A second scheduler, evidence store, Feature Bus, calibrator, canonical writer or F3-F7 spine
- Replacing the compose pin with an unconstrained `${...}` expansion that a stale `.env` could drive
- Modifying the v1.4.3 authority DOCX
- Weakening the bridge no-weakening protection into a generic relaxation

FROZEN BOUNDARIES:
Release profile as the activation authority. The only mechanism that may place the Feature Bus into `shadow` is the explicit selection and validation of the `EVIDENCE_SHADOW` release profile. Free-form `.env` mode values are never the activation authority, and the core-service modes are literals that override `env_file: .env`. `SAFE_BASELINE` remains the rollback posture and resolves the Feature Bus `off`.

History preserved. The supersession edits only *current-runtime* assertions. Every historical contract statement that the Feature Bus remained `off` at that increment is preserved and labelled as history; no historical intent is rewritten.

Bounded authorization. `EVIDENCE_SHADOW` authorizes exactly Feature Bus `shadow` + canonical writer `shadow` + target spine `shadow` + Paper-v2 `off` + Committee `off`. It grants no new-entry, reservation, order, exchange, funded, margin, Committee, dashboard or Telegram authority, and it does not authorize `TARGET_PAPER`. The bounded SHADOW capture remains dual-gated (Feature Bus AND writer exactly `shadow`) and never runs inside the protected unified cycle.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_release_pipeline_v1.py::test_ac_001_profile_allowlist_and_exact_modes
AC-001 -> tests/test_opip_release_pipeline_v1.py::test_ac_001_unknown_profile_fails_closed
AC-001 -> tests/test_release_runtime_verifier.py::test_runtime_verification_rejects_blocked_target_paper
AC-002 -> tests/test_opip_release_pipeline_v1.py::test_ac_002_profile_resolves_compose_and_gate_passes
AC-002 -> tests/test_opip_release_pipeline_v1.py::test_ac_002_stale_env_cannot_elevate_baseline
AC-002 -> tests/test_opip_release_pipeline_v1.py::test_ac_002_arbitrary_and_injection_overrides_fail
AC-003 -> tests/test_opip_release_pipeline_v1.py::test_ac_003_history_preserved_and_superseded
AC-004 -> tests/test_opip_release_pipeline_v1.py::test_ac_004_bridge_guard_allows_authorized_supersession
AC-004 -> tests/test_opip_release_pipeline_v1.py::test_ac_004_bridge_guard_still_fails_unapproved_weakening
AC-005 -> tests/test_opip_release_pipeline_v1.py::test_ac_005_gate_fails_closed_and_receipt
AC-006 -> tests/test_opip_release_pipeline_v1.py::test_ac_006_paper_v2_off_legacy_sole_authority
AC-007 -> tests/test_opip_release_pipeline_v1.py::test_ac_007_ci_gate_and_main_candidate_are_non_deploying
AC-008 -> tests/test_opip_release_pipeline_v1.py::test_ac_008_profile_approval_is_owner_only_exact_sha_and_gated
AC-008 -> tests/test_release_runtime_verifier.py::test_safe_baseline_is_not_a_deploy_candidate
AC-009 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_requires_consecutive_fresh_snapshots_and_matching_fev
AC-009 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_rejects_backfill_gaps_and_late_source_cutoffs
AC-009 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_rejects_stale_snapshots_and_unmatched_fev
AC-009 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_rejects_future_timestamps
AC-009 -> tests/test_opip_release_pipeline_v1.py::test_ac_009_runtime_verifier_precedes_commit_and_rolls_back_to_baseline
AC-009 -> tests/test_opip_release_pipeline_v1.py::test_ac_009_deployment_receipt_requires_runtime_verifier_and_baseline_rollback
AC-010 -> tests/test_opip_release_pipeline_v1.py::test_ac_010_rollback_success_requires_verified_safe_baseline_modes

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_release_profiles.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/release/README.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-002 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-002 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/release/README.md
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F6_OWNER_ENABLEMENT_PACKETS.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-006 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_current_runtime_posture.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_shadow_activation_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f7_economic_portfolio_selector.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_spine_composition.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_spine_contract_closure.py
AC-006 -> .github/copilot-instructions.md
AC-006 -> AGENTS.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-007 -> .github/workflows/pytest.yml
AC-007 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-008 -> .github/workflows/deploy-production.yml
AC-008 -> .github/workflows/pytest.yml
AC-008 -> OHM-Trade-Agent-v1/.dockerignore
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-009 -> OHM-Trade-Agent-v1/app/services/release_runtime_verifier.py
AC-009 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-009 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-009 -> OHM-Trade-Agent-v1/Dockerfile
AC-009 -> OHM-Trade-Agent-v1/tests/test_release_runtime_verifier.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_deployment_transaction_boundary_v1.py
AC-010 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-010 -> .github/workflows/deploy-production.yml

DEFERRED DISCOVERIES:
- `TARGET_PAPER` remains BLOCKED. Activating it (Paper-v2) requires the AC-011 comparator evidence, F11 protection READY, legacy drain READY and explicit OWNER approval, and is a separate OWNER increment; this contract does not authorize it.
- Production runtime evidence has not been observed in this coding session. The verifier is implemented as a deployment gate, but only an owner-authorized run on production can produce its runtime receipt; a CI candidate is not runtime proof.

UNAPPROVED SCOPE CHANGES:
NONE
