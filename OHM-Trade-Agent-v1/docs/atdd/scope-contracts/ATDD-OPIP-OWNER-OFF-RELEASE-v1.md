INCREMENT:
ATDD-OPIP-OWNER-OFF-RELEASE-v1

OWNER-APPROVED INTENT:
Preserve the operator's explicitly disabled paper and learning modes during
production releases, after resource cleanup on 2026-11-12. The existing release
controller unconditionally restarts paper workers on deploy AND rollback, and
its post-commit receipt requires a fresh learning export. The fix accepts only
positively proven owner OFF and cannot claim READY evidence for skipped export.
Keep all independent F11, protection-preflight, writer, scheduler, shadow
verification and no-funded-order safety gates unchanged.

ARCHITECTURE REFERENCES:
- deploy/remote/ohm-deploy: pre-mutation owner paper control proof, paper OFF
  verification on forward and rollback, and explicit learning OFF receipt.
- .github/workflows/deploy-production.yml: fail-closed release classifier with
  mutually exclusive OWNER_OFF_PROVEN versus enabled learning proof.
- app/services/paper_trade_control.py: existing owner control.json schema.
- deploy/remote/export-opip-learning-evidence.sh: default-off real-flag gate.
- tests/test_opip_owner_off_release_v1.py: acceptance and contradictory markers.
- tests/test_opip_deployment_transaction_boundary_v1.py: existing classifier.
- tests/test_release_protection_preflight_v1.py: F11/protection invariant suite.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the owner persisted paper_trading/control.json enabled=false
WHEN: the controller prepares an exact-SHA deployment
THEN: OFF is read from a strict boolean and proven before the rollback trap
or any service mutation; missing/symlinked/unreadable/malformed state refuses

AC-002:
GIVEN: the paper owner control is OFF and paper workers were stopped
WHEN: forward deployment or rollback recreates core and writer
THEN: neither path starts paper; both prove workers stopped and skip absent
paper cgroup assertions while retaining core and writer resource limits; ON
retains the original dry-run health and full hard resource checks

AC-003:
GIVEN: owner learning-export enable flag is absent
WHEN: core release is committed and the GitHub workflow classifies the receipt
THEN: no export or proof query runs, release markers say OWNER_OFF / SKIPPED /
NOT_APPLICABLE, and a release may be reported SUCCESS only with verified paper,
runtime, F11, exact-SHA shadow evidence and core health. Enabled export retains
SUCCESS / READY exact-SHA replica proof; contradictory claims fail closed

EXPLICITLY OUT OF SCOPE:
- Trading, Kraken, real orders, F11 evaluator/authority, manual positions or stops.
- Activating learning, Committee, paper or auto execution.
- Scanner, Telegram, liquidity, confidence, economics or AI changes.
- Changing cron installation allowlist, docker-compose or a runtime scheduler.
- Deploying or restarting production as part of this code PR.

FROZEN BOUNDARIES:
- Owner decision is read from existing persisted data; unproven mode cannot start paper.
- Existing F11 protection preflight precedes the first runtime mutation.
- Forward and rollback both preserve the initial owner paper setting.
- OFF means verified stopped workers, not silently skipped health.
- ON retains strict two-worker dry-run health and cgroup ceilings.
- Learning OFF cannot claim fresh replica evidence; ENABLED still must verify.
- No new funded/real-trading authority; EVIDENCE_SHADOW readiness is not F11 health.
- Installed controller on production may predate these changes: require approved
  bootstrap before deploy or the old controller may restart paper.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_owner_off_release_v1.py::test_owner_paper_control_off_on_and_unknown_are_distinct
AC-002 -> tests/test_opip_owner_off_release_v1.py::test_owner_disabled_paper_never_restarts_in_forward_or_rollback
AC-002 -> tests/test_opip_owner_off_release_v1.py::test_release_controller_shell_syntax_and_non_deploying_ui_path
AC-003 -> tests/test_opip_owner_off_release_v1.py::test_learning_off_release_passes_only_with_verified_paper
AC-003 -> tests/test_opip_owner_off_release_v1.py::test_enabled_learning_still_requires_original_exact_sha_evidence

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_owner_off_release_v1.py
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_owner_off_release_v1.py
AC-003 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_single_writer_feasibility_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_core_env_isolation_v1.py
AC-003 -> .github/workflows/deploy-production.yml
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_owner_off_release_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-OWNER-OFF-RELEASE-v1.md

DEFERRED DISCOVERIES:
- Before rollout, inspect live owner control, export flag, installed controller
  SHA and current F11; bootstrap exact approved controller if needed.
- Verify deploy retains stopped paper, disk idle, scheduled export dark and
  original strict F11 status (UNSAFE admissions still suspended).
- Workflows classify owner OFF without requiring fabricated learning proof.
- Local shell tests not available through connector: mandatory exact-head CI
  before merge and separate production proof are required.

UNAPPROVED SCOPE CHANGES:
NONE
