INCREMENT:
ATDD-OPIP-LEARNING-RECOVERY-v1

OWNER-APPROVED INTENT:
Restore the O'Pip learning evidence plane without redeploying the healthy
production core or restarting its canonical writer. The principal architect
approved a bounded recovery: stabilize failing learning timers, restore the
least-privilege evidence reader, and reconstruct missing core release provenance
through an append-only, evidence-bound mechanism. No trading authority, Paper V2,
Committee, funded execution, or recurring AI inference may change.

ARCHITECTURE REFERENCES:
- docs/architecture/OPIP_CORE_RELEASE_PROVENANCE_RECONCILIATION.md
- docs/learning/README.md (learning plane contract)
- AGENTS.md principles 7, 8, 9, 11, 12, 20, 21

APPROVED ACCEPTANCE CRITERIA:
AC-001:
A reader account created after the export tree already exists can read nested artifacts.
GIVEN: a production export tree whose nested directories were created 0700 before the reader group existed
WHEN: configure-opip-learning-reader.sh provisions the least-privilege reader
THEN: nested export directories become group-traversable, export files become group-readable, hidden staging remains exporter-owned, and write access remains root-only.

AC-002:
Provenance reconciliation refuses on any evidence disagreement.
GIVEN: the immutable core image, image revision label, LIVE_ACCEPTANCE, CORE-UPGRADE qualification, release-tree checksums, canonical writer identity, authority profile, or canonical database continuity
WHEN: any single item disagrees or cannot be verified
THEN: reconciliation refuses, publishes no last-good-sha, and writes only an append-only rejection receipt.

AC-003:
Successful reconciliation is append-only and idempotent.
GIVEN: every provenance check passes and the existing last-good-sha is absent or already equal to the expected SHA
WHEN: reconciliation runs with --apply
THEN: it atomically publishes the exact 40-character SHA with 0600 permissions, appends an O_EXCL reconciliation receipt, never rewrites historical receipts, and never overwrites a conflicting value.

AC-004:
CI validates the reader configuration scripts.
GIVEN: the reader configuration contract or its permission helper changes
WHEN: the repository test workflow runs
THEN: both scripts are included in the deployment shell syntax gate.

AC-005:
The active ATDD pointer resolves to this increment contract.
GIVEN: the recovery increment is authorized
WHEN: the scope control pointer is read
THEN: it names an existing contract file that declares this increment and all required sections are present.

EXPLICITLY OUT OF SCOPE:
- Deploying, rebuilding, or restarting production containers or systemd units.
- Executing the provenance reconciler against production; that remains owner-gated.
- Re-enabling learning timers or running capture/outcomes.
- Any trading authority, risk gate, execution safeguard, Paper V2, Committee, funded trading, or recurring AI inference change.
- Editing historical receipts or hand-writing last-good-sha.

FROZEN BOUNDARIES:
- Canonical writer image and container identity must remain unchanged.
- Canonical production database remains the sole economic authority and is read only.
- The exporter consumer contract (`last-good-sha` = one 40-hex line) is unchanged.
- No new scheduler, deployment plane, database, queue, or droplet.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_learning_reader_permissions_v1.py::test_pre_existing_nested_export_dirs_become_reader_traversable
AC-001 -> tests/test_learning_reader_permissions_v1.py::test_permission_normalization_requires_explicit_arguments
AC-001 -> tests/test_learning_reader_permissions_v1.py::test_reader_configuration_contract_invokes_permission_normalization
AC-002 -> tests/test_release_provenance_reconcile_v1.py::test_complete_evidence_passes_every_check
AC-002 -> tests/test_release_provenance_reconcile_v1.py::test_canonical_writer_drift_is_refused
AC-002 -> tests/test_release_provenance_reconcile_v1.py::test_authority_widening_is_refused
AC-002 -> tests/test_release_provenance_reconcile_v1.py::test_database_integrity_and_continuity_fail_closed
AC-002 -> tests/test_release_provenance_reconcile_v1.py::test_release_manifest_mismatch_is_refused
AC-002 -> tests/test_release_provenance_reconcile_v1.py::test_refused_apply_writes_rejection_and_never_publishes
AC-002 -> tests/test_release_provenance_reconcile_v1.py::test_writer_drift_blocks_publication
AC-003 -> tests/test_release_provenance_reconcile_v1.py::test_dry_run_passes_without_writing_anything
AC-003 -> tests/test_release_provenance_reconcile_v1.py::test_apply_publishes_exact_sha_and_append_only_receipt
AC-003 -> tests/test_release_provenance_reconcile_v1.py::test_idempotent_apply_does_not_change_identical_sha
AC-003 -> tests/test_release_provenance_reconcile_v1.py::test_conflicting_last_good_sha_is_never_overwritten
AC-004 -> tests/test_learning_reader_permissions_v1.py::test_ci_syntax_gate_covers_reader_scripts
AC-005 -> tests/test_learning_recovery_scope_v1.py::test_active_increment_points_to_existing_contract

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/configure-opip-learning-reader.sh
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/opip-learning-reader-permissions.sh
AC-001 -> OHM-Trade-Agent-v1/tests/test_learning_reader_permissions_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/release_provenance_reconcile.py
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_CORE_RELEASE_PROVENANCE_RECONCILIATION.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_release_provenance_reconcile_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/release_provenance_reconcile.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_release_provenance_reconcile_v1.py
AC-004 -> .github/workflows/pytest.yml
AC-004 -> OHM-Trade-Agent-v1/tests/test_learning_reader_permissions_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-LEARNING-RECOVERY-v1.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_scope_v1.py

DEFERRED DISCOVERIES:
- The production export tree contains historical generations from the previous canonical store (220,721 events) while current production runs a fresh genesis (8,819 events at capture). Retention and archival review remain owner work.
- Learning timers are disabled during recovery and require one-shot acceptance before re-enabling.

UNAPPROVED SCOPE CHANGES:
NONE

