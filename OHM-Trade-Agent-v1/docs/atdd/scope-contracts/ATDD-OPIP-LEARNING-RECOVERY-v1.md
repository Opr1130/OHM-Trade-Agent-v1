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

AC-006:
The reconciler cannot overwrite a newer release marker.
GIVEN: the production controller may deploy while reconciliation is running
WHEN: reconciliation collects evidence, validates or publishes
THEN: it holds the same non-blocking `/var/lock/ohm-deploy.lock`, refuses a busy lock, refuses a conflicting marker, and never replaces a newer release identity.

AC-007:
Live runtime authority must match the approved release posture.
GIVEN: a healthy, correctly labelled core image
WHEN: its running environment has an unsafe, missing or contradictory authority mode
THEN: reconciliation refuses even if historical acceptance receipts still describe the approved posture.

AC-008:
Authoritative publication requires durable provenance first.
GIVEN: all checks pass and `--apply` is requested
WHEN: the provenance receipt cannot be written, fsynced, or the marker publication is interrupted
THEN: no authoritative marker is published, the pending or failed state remains durably recorded, and historical receipts remain unchanged.

AC-009:
Recovery binds production identity separately from tooling identity.
GIVEN: production runs an authenticated SHA earlier than merged recovery tooling
WHEN: an owner invokes the recovery-only deployment path
THEN: tooling must equal current main with successful exact-SHA CI, production must equal the authenticated export manifest and be its ancestor, and the worker records both identities without enabling timers.

AC-010:
The reader receives only minimum read and traversal permissions.
GIVEN: existing export directories, nested files, symlinks and repeated configuration
WHEN: reader permissions are normalized
THEN: nested directories are group-traversable, files are group-readable, symlinked roots are refused, symlink targets are never modified, and no group or world write bit is granted.

AC-011:
Canonical lineages are explicitly separated and archived without mutation.
GIVEN: an old 220,721-event canonical replica lineage and a V2 fresh-genesis lineage
WHEN: history is preserved or archived
THEN: the lineages are never merged or relabelled, archival is copy-only with checksummed manifests and append-only receipts, and consumers report lineage-separated counts.

AC-012:
Recovery configuration changes are transactional and never roll back committed evidence.
GIVEN: recovery replaces the environment, runner scripts and unit definitions before one-shot acceptance
WHEN: any installation, sync, capture, outcomes or receipt stage fails
THEN: verified backups restore configuration only while no new replica generation is active; after generation commit the configuration is preserved, timers remain disabled, diagnostics and an append-only failure receipt are durable, and retry cannot create duplicate active workers.

AC-013:
A new lineage may not be synchronized before the legacy generation is archived.
GIVEN: the outgoing replica generation belongs to LINEAGE-A-LEGACY
WHEN: recovery is about to synchronize a new-lineage generation
THEN: a verified copy-only archive with complete file hashes, durable receipt and adequate disk capacity must already exist; missing, incomplete, corrupted or interrupted archival refuses recovery and never deletes or overwrites the source.

AC-014:
Cross-SHA recovery tooling is compatibility-certified immediately before mutation.
GIVEN: worker code is built from a newer tooling SHA while release identity remains the production SHA
WHEN: any learning consumer, schema, canonical-replica or production-evidence contract differs
THEN: every change must match an owner-approved exact-SHA-pair exception and verified tooling hashes, and current main plus exact-SHA CI are revalidated immediately before the remote mutation; otherwise recovery refuses.

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
AC-006 -> tests/test_release_provenance_reconcile_v1.py::test_busy_deploy_lock_is_detected
AC-006 -> tests/test_release_provenance_reconcile_v1.py::test_reconcile_refuses_when_controller_lock_is_held
AC-006 -> tests/test_release_provenance_reconcile_v1.py::test_newer_marker_written_after_validation_is_never_overwritten
AC-007 -> tests/test_release_provenance_reconcile_v1.py::test_live_runtime_authority_override_is_refused
AC-007 -> tests/test_release_provenance_reconcile_v1.py::test_missing_runtime_authority_is_refused
AC-007 -> tests/test_release_provenance_reconcile_v1.py::test_truthy_funded_authority_key_is_refused
AC-007 -> tests/test_release_provenance_reconcile_v1.py::test_core_writer_mode_must_stay_shadow
AC-008 -> tests/test_release_provenance_reconcile_v1.py::test_receipt_write_failure_blocks_publication
AC-008 -> tests/test_release_provenance_reconcile_v1.py::test_publication_failure_leaves_durable_pending_provenance
AC-008 -> tests/test_release_provenance_reconcile_v1.py::test_interrupted_replace_never_publishes_marker
AC-008 -> tests/test_release_provenance_reconcile_v1.py::test_receipt_is_fsynced_before_marker_replace
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_valid_recovery_binding_passes_every_check
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_manifest_must_bind_the_production_sha
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_arbitrary_old_tooling_sha_is_refused
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_normal_deploy_identity_equality_is_refused
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_tooling_ci_must_be_successful
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_non_ancestor_compare_is_refused
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_recovery_requires_timers_already_disabled
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_recovery_workflow_is_owner_gated_and_separate
AC-009 -> tests/test_learning_recovery_binding_v1.py::test_recovery_script_binds_authenticated_manifest_and_never_enables_timers
AC-010 -> tests/test_learning_reader_permissions_v1.py::test_repeat_execution_is_idempotent
AC-010 -> tests/test_learning_reader_permissions_v1.py::test_symlink_inside_export_is_not_followed
AC-010 -> tests/test_learning_reader_permissions_v1.py::test_symlinked_export_root_is_refused
AC-010 -> tests/test_learning_reader_permissions_v1.py::test_normalized_tree_has_no_group_or_world_write_bits
AC-011 -> tests/test_learning_lineage_policy_v1.py::test_canonical_lineage_boundary_and_archival_policy_is_explicit
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_snapshot_restore_round_trip_and_missing_file_removal
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_tampered_backup_refuses_restore
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_failure_policy_distinguishes_config_from_committed_evidence
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_generation_commit_boundary
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_receipt_is_append_only
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_verified_archive_passes_and_preserves_source
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_missing_archive_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_incomplete_archive_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_corrupted_archive_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_extra_archive_file_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_wrong_lineage_or_source_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_missing_receipt_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_interrupted_copy_leaves_no_verified_archive_or_receipt
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_insufficient_disk_refuses_before_copy
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_no_critical_changes_pass
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_unreviewed_critical_change_is_refused
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_exact_pair_exception_is_accepted_but_wrong_pair_is_not
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_tooling_hash_mismatch_is_refused
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_unreviewed_change_list_in_manifest_is_refused
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_recovery_workflow_revalidates_main_ci_and_ships_compatibility
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_recovery_script_requires_compatibility_transaction_and_archive

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
AC-006 -> OHM-Trade-Agent-v1/app/services/release_provenance_reconcile.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_release_provenance_reconcile_v1.py
AC-007 -> OHM-Trade-Agent-v1/app/services/release_provenance_reconcile.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_release_provenance_reconcile_v1.py
AC-008 -> OHM-Trade-Agent-v1/app/services/release_provenance_reconcile.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_release_provenance_reconcile_v1.py
AC-009 -> OHM-Trade-Agent-v1/app/services/learning_recovery_binding.py
AC-009 -> .github/workflows/deploy-learning-recovery.yml
AC-009 -> OHM-Trade-Agent-v1/deploy/learning/run-recovery-learning-deploy.sh
AC-009 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_binding_v1.py
AC-010 -> OHM-Trade-Agent-v1/deploy/remote/opip-learning-reader-permissions.sh
AC-010 -> OHM-Trade-Agent-v1/tests/test_learning_reader_permissions_v1.py
AC-011 -> OHM-Trade-Agent-v1/docs/learning/CANONICAL_LINEAGE_BOUNDARY_V1.md
AC-011 -> OHM-Trade-Agent-v1/tests/test_learning_lineage_policy_v1.py
AC-012 -> OHM-Trade-Agent-v1/app/services/learning_recovery_transaction.py
AC-012 -> OHM-Trade-Agent-v1/deploy/learning/run-recovery-learning-deploy.sh
AC-012 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_transaction_v1.py
AC-013 -> OHM-Trade-Agent-v1/app/services/learning_lineage_archive.py
AC-013 -> OHM-Trade-Agent-v1/docs/learning/CANONICAL_LINEAGE_BOUNDARY_V1.md
AC-013 -> OHM-Trade-Agent-v1/deploy/learning/run-recovery-learning-deploy.sh
AC-013 -> OHM-Trade-Agent-v1/tests/test_learning_lineage_archive_v1.py
AC-014 -> OHM-Trade-Agent-v1/app/services/learning_recovery_compatibility.py
AC-014 -> OHM-Trade-Agent-v1/docs/learning/RECOVERY_COMPATIBILITY_EXCEPTIONS.json
AC-014 -> .github/workflows/deploy-learning-recovery.yml
AC-014 -> OHM-Trade-Agent-v1/deploy/learning/run-recovery-learning-deploy.sh
AC-014 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_compatibility_v1.py

DEFERRED DISCOVERIES:
- The production export tree contains historical generations from the previous canonical store (220,721 events) while current production runs a fresh genesis (8,819 events at capture). Retention and archival review remain owner work.
- Learning timers are disabled during recovery and require one-shot acceptance before re-enabling.

UNAPPROVED SCOPE CHANGES:
NONE

