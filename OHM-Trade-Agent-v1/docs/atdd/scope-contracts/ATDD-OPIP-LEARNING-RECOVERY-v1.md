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

AC-015:
Legacy derived state and replaced inputs are preserved before a lineage transition.
GIVEN: a V2 input filename collides with a legacy sidecar or ledger whose offsets belong to LINEAGE-A-LEGACY
WHEN: sync replaces inputs or a consumer opens its sidecar
THEN: known derived artifacts and replaced inputs are copied to a verified, append-only namespace before replacement; SQLite state is snapshotted with the backup API; only derived active state is retired after a fresh VERIFIED receipt and manifest/payload re-verification; inputs remain; mixed legacy-derived/V2-input archives carry separate artifact-level provenance; and an unbound or mismatched legacy sidecar fails closed.

AC-016:
Recovery binding receipts exist before they are referenced and never fabricate success.
GIVEN: a recovery transaction creates a binding path and then fails at any stage
WHEN: the environment is updated or the terminal receipt is written
THEN: a durable PREPARED intent exists before `OPIP_LEARNING_RECOVERY_BINDING` references it, CERTIFIED and BLOCKED terminals are separate O_EXCL records, failure leaves a truthful BLOCKED or pending state, and a prior missing binding is materialized only as BLOCKED from existing failure evidence.

AC-017:
Cross-SHA compatibility approval is external, exact and owner-authenticated.
GIVEN: the tooling commit changes one or more critical consumer paths
WHEN: recovery compatibility is evaluated
THEN: approval is an owner-authored attestation record created after the exact tooling commit exists, bound to the exact production/tooling SHA pair, changed-path set, per-path hashes, canonical diff digest, owner identity, GitHub record URL, timestamp, rationale and exact-SHA CI evidence; a missing, stale, malformed, wildcard or mismatched attestation fails closed; and the workflow validates it before any remote mutation.

AC-018:
A later authenticated owner revocation of exact cross-SHA recovery approval always refuses.
GIVEN: an owner-authenticated attestation authorizes an exact production/tooling pair
WHEN: the owner later revokes it, posts a later applicable event that is invalid, or posts a fresh re-approval
THEN: authorization is resolved from authenticated owner events ordered by timestamp and comment identity with latest-applicable-wins, so a revocation or a later invalid event refuses without resurrecting an older approval, a fresh valid re-approval restores authorization, non-owner comments never authorize or revoke, unscoped events apply only to the evaluated pair, missing timestamps and unorderable ties fail closed, and the workflow re-resolves authorization immediately before the first irreversible mutation.

AC-019:
The cross-SHA critical-file inventory and canonical digest are deterministic across platforms and trees.
GIVEN: the tooling commit is inspected on any platform or staged into a release tree without Git metadata
WHEN: compatibility is prepared, hashed and verified
THEN: every key is a Git-style POSIX path derived from the exact tracked tree, untracked generated artifacts never participate, tracked executables keep participating regardless of filename, duplicate normalized paths and symlink substitution are refused, every changed critical path must be hashed, and the canonical digest is identical on Windows and Linux.

AC-020:
Learning recovery CI must resolve exact-SHA successful workflow runs without false refusal or false admission.
GIVEN: main has an immutable tooling SHA with required pytest, quality-security and Semgrep workflows, including workflow-dispatch or push runs
WHEN: the initial and pre-mutation recovery gates query GitHub Actions CI
THEN: they query the repository-level GitHub Actions runs on main (avoiding unreliable workflow-scoped head_sha query filters), independently filter returned exact SHA/branch/workflow path, select the newest run and attempt, require completed/success, refuse missing, stale, failed, pending, wrong-SHA, wrong-workflow, wrong-branch or API-error results, and retain the protected environment and owner-attestation gates.

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
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_file_sync_failure_aborts_snapshot
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_directory_sync_failure_aborts_snapshot
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_insufficient_storage_aborts_snapshot
AC-012 -> tests/test_learning_recovery_transaction_v1.py::test_restore_sync_failure_is_not_suppressed
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_verified_archive_passes_and_preserves_source
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_missing_archive_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_incomplete_archive_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_corrupted_archive_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_extra_archive_file_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_wrong_lineage_or_source_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_missing_receipt_is_refused
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_interrupted_copy_leaves_no_verified_archive_or_receipt
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_insufficient_disk_refuses_before_copy
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_file_sync_failure_prevents_verified_archive
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_directory_sync_failure_prevents_verified_archive
AC-013 -> tests/test_learning_lineage_archive_v1.py::test_receipt_sync_failure_discards_unverified_archive
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_no_critical_changes_pass
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_unreviewed_critical_change_is_refused
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_exact_pair_exception_is_accepted_but_wrong_pair_is_not
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_tooling_hash_mismatch_is_refused
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_unreviewed_change_list_in_manifest_is_refused
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_recovery_workflow_revalidates_main_ci_and_ships_compatibility
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_recovery_script_requires_compatibility_transaction_and_archive
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_critical_scope_covers_actual_learning_job_entrypoints
AC-014 -> tests/test_learning_recovery_compatibility_v1.py::test_actual_outcomes_consumer_change_fails_closed_without_exception
AC-015 -> tests/test_learning_derived_state_v1.py::test_legacy_offsets_are_archived_before_v2_inputs_are_used
AC-015 -> tests/test_learning_derived_state_v1.py::test_new_consumer_state_is_empty_and_bound_to_v2
AC-015 -> tests/test_learning_derived_state_v1.py::test_unbound_legacy_sidecar_fails_closed
AC-015 -> tests/test_learning_derived_state_v1.py::test_empty_new_sidecar_is_bound_and_mismatch_is_refused
AC-015 -> tests/test_learning_derived_state_v1.py::test_interrupted_archive_is_not_verified_and_preserves_source
AC-015 -> tests/test_learning_derived_state_v1.py::test_corrupted_archive_payload_is_not_verified
AC-015 -> tests/test_learning_derived_state_v1.py::test_equal_size_different_content_is_recorded_with_hash
AC-015 -> tests/test_learning_derived_state_v1.py::test_insufficient_disk_refuses_before_copy
AC-015 -> tests/test_learning_derived_state_v1.py::test_partial_archive_destination_is_refused
AC-015 -> tests/test_learning_derived_state_v1.py::test_corrupted_archive_payload_is_not_verified
AC-015 -> tests/test_learning_derived_state_v1.py::test_phase3c_open_refuses_legacy_offset_under_v2_marker
AC-015 -> tests/test_learning_derived_state_v1.py::test_sidecar_identity_compares_lineage_and_source
AC-015 -> tests/test_learning_derived_state_v1.py::test_routine_generation_rotation_does_not_invalidate_v2_sidecars
AC-015 -> tests/test_learning_derived_state_v1.py::test_generation_rotation_requires_verified_manifest
AC-015 -> tests/test_learning_derived_state_v1.py::test_generation_rotation_cannot_change_release_identity
AC-015 -> tests/test_learning_derived_state_v1.py::test_sidecar_stable_generation_mismatch_fails_closed
AC-015 -> tests/test_learning_derived_state_v1.py::test_checkpoint_anchor_validation_accepts_current_input
AC-015 -> tests/test_learning_derived_state_v1.py::test_checkpoint_anchor_validation_rejects_truncation
AC-015 -> tests/test_learning_derived_state_v1.py::test_mixed_lineage_archive_requires_input_provenance
AC-015 -> tests/test_learning_derived_state_v1.py::test_mixed_lineage_receipt_separates_derived_and_input_provenance
AC-015 -> tests/test_learning_derived_state_v1.py::test_existing_archive_is_freshly_verified_before_retirement
AC-015 -> tests/test_learning_derived_state_v1.py::test_existing_archive_retry_is_idempotent
AC-015 -> tests/test_learning_derived_state_cli_v1.py::test_fresh_lineage_transition_cli_archives_verified_outgoing_inputs
AC-015 -> tests/test_learning_derived_state_cli_v1.py::test_partially_committed_resume_cli_preserves_v2_inputs
AC-015 -> tests/test_learning_derived_state_cli_v1.py::test_cli_refuses_unauthenticated_or_missing_input_provenance
AC-015 -> tests/test_learning_derived_state_v1.py::test_retry_refuses_changed_active_derived_state
AC-015 -> tests/test_learning_derived_state_v1.py::test_schema_migration_markers_do_not_block_first_lineage_bind
AC-015 -> tests/test_learning_derived_state_v1.py::test_schema_markers_with_real_rows_still_refuse
AC-015 -> tests/test_learning_derived_state_v1.py::test_phase3c_open_binds_after_migration_markers
AC-015 -> tests/test_screening_stream_frame_v1.py::test_archive_to_archive_transition
AC-015 -> tests/test_screening_stream_frame_v1.py::test_archive_to_hot_transition
AC-015 -> tests/test_screening_stream_frame_v1.py::test_checkpoint_at_end_is_idempotent
AC-015 -> tests/test_screening_stream_frame_v1.py::test_unframed_legacy_offset_cannot_be_promoted
AC-015 -> tests/test_screening_stream_frame_v1.py::test_pruned_checkpoint_fails_closed
AC-015 -> tests/test_screening_stream_frame_v1.py::test_missing_segment_fails_closed
AC-015 -> tests/test_screening_stream_frame_v1.py::test_overlapping_segments_fail_closed
AC-015 -> tests/test_screening_stream_frame_v1.py::test_corrupt_archive_fails_before_consume
AC-015 -> tests/test_screening_stream_frame_v1.py::test_anchor_mismatch_refuses_rebaseline
AC-015 -> tests/test_screening_stream_frame_v1.py::test_checkpoint_must_be_complete_record
AC-015 -> tests/test_screening_stream_frame_v1.py::test_checkpoint_beyond_end_fails
AC-015 -> tests/test_screening_stream_frame_v1.py::test_stable_identity_not_derived_from_mutable_contents

AC-016 -> tests/test_learning_recovery_receipt_v1.py::test_intent_is_prepared_before_terminal_and_is_not_edited
AC-016 -> tests/test_learning_recovery_receipt_v1.py::test_blocked_terminal_never_asserts_success
AC-016 -> tests/test_learning_recovery_receipt_v1.py::test_duplicate_intent_is_idempotent_but_foreign_transaction_is_refused
AC-016 -> tests/test_learning_recovery_receipt_v1.py::test_missing_binding_is_materialized_as_blocked_from_failure
AC-016 -> tests/test_learning_recovery_receipt_v1.py::test_recovery_script_creates_intent_before_binding_reference
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_exact_owner_attestation_is_accepted
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_attestation_rejects_wrong_pair_path_digest_and_owner
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_attestation_rejects_stale_and_missing_ci
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_attested_manifest_without_attestation_is_refused
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_comment_selection_uses_owner_and_latest_valid_attestation
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_recovery_workflow_authenticates_issue64_attestation
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_paginates_more_than_600_comments_and_accepts_final_owner_attestation
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_pagination_failure_returns_no_partial_admission
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_incomplete_pagination_refuses_partial_results
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_pagination_rejects_malformed_pages_and_missing_token
AC-017 -> tests/test_learning_recovery_attestation_v1.py::test_recovery_workflow_refuses_exact_sha_ci_failure
AC-017 -> tests/test_learning_recovery_attestation_cli_v1.py::test_verify_manifest_cli_accepts_valid_attestation
AC-017 -> tests/test_learning_recovery_attestation_cli_v1.py::test_verify_manifest_cli_rejects_missing_attestation
AC-017 -> tests/test_learning_recovery_attestation_cli_v1.py::test_verify_manifest_cli_rejects_tampered_attestation
AC-017 -> tests/test_learning_recovery_attestation_cli_v1.py::test_fetch_comments_cli_writes_every_page_as_one_array
AC-015 -> tests/test_learning_recovery_receipt_v1.py::test_sync_preserves_before_replacing_and_marks_after_commit
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t01_valid_exact_owner_attestation_is_accepted
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t02_wrong_actor_is_rejected
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t02b_owner_identity_inside_body_is_not_trusted
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t12_original_revocation_fail_open_reproduction_now_holds
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t03_structured_revocation_supersedes_approval
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t03b_plaintext_revocation_is_honoured
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t04_non_owner_revocation_cannot_modify_authorization
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t05_unrelated_owner_comment_does_not_revoke
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t06_revocation_for_another_pair_does_not_affect_this_pair
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t07_malformed_newer_applicable_event_fails_closed_without_fallback
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t07b_malformed_newer_revocation_fails_closed
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t07c_invalid_newer_approval_does_not_fall_back_to_valid_older
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t08_fresh_reapproval_after_revocation_restores_authorization
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t09_timestamp_ambiguity_refuses_authorization
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t09b_identical_timestamps_are_ordered_by_comment_id
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t09c_missing_timestamp_fails_closed
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t09d_missing_authenticated_record_identity_is_refused
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t23_exactness_overrides_are_refused
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_t26_ci_failure_is_refused
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_wrong_path_hash_is_refused
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_broader_path_scope_is_refused
AC-018 -> tests/test_learning_recovery_authorization_state_v1.py::test_backward_compatible_approval_only_history_still_passes
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t14_inventory_keys_are_posix_on_every_platform
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t14b_tracked_inventory_matches_release_tree_walk
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t15_path_traversal_keys_are_refused
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t16_duplicate_normalized_paths_are_rejected
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t17_generated_artifacts_do_not_change_the_inventory
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t18_missing_tracked_file_is_refused
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t19_tampered_tracked_file_is_refused
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t20_symlinked_critical_path_is_refused
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t21_release_tree_without_git_still_verifies
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_t22_changed_critical_scope_cannot_be_silently_dropped
AC-019 -> tests/test_learning_recovery_inventory_determinism_v1.py::test_inventory_is_stable_across_repeated_prepare_calls
AC-018 -> tests/test_learning_recovery_authorization_boundary_v1.py::test_t13_authorization_revoked_between_check_and_mutation_blocks
AC-018 -> tests/test_learning_recovery_authorization_boundary_v1.py::test_t13b_workflow_revalidates_authorization_before_mutation
AC-018 -> tests/test_learning_recovery_authorization_boundary_v1.py::test_t27_main_movement_is_guarded_before_mutation
AC-009 -> tests/test_learning_recovery_authorization_boundary_v1.py::test_t28_concurrent_recovery_cannot_overlap
AC-009 -> tests/test_learning_recovery_authorization_boundary_v1.py::test_t29_protected_environment_remains_enforced
AC-009 -> tests/test_learning_recovery_authorization_boundary_v1.py::test_t30_recovery_never_enables_learning_timers
AC-009 -> tests/test_learning_recovery_authorization_boundary_v1.py::test_t31_no_core_deployment_path_is_reachable
AC-009 -> tests/test_learning_recovery_authorization_boundary_v1.py::test_t32_no_authority_flags_are_touched


AC-020 -> tests/test_learning_recovery_ci_discovery_v1.py::test_exact_sha_green_ci_accepted_through_workflow_api
AC-020 -> tests/test_learning_recovery_ci_discovery_v1.py::test_exact_sha_ci_fail_closed_on_invalid_observation
AC-020 -> tests/test_learning_recovery_ci_discovery_v1.py::test_ci_lookup_api_error_fails_closed
AC-020 -> tests/test_learning_recovery_ci_discovery_v1.py::test_gate_queries_workflow_identity_sha_and_branch_in_both_steps
AC-020 -> tests/test_learning_recovery_ci_discovery_v1.py::test_workflow_path_with_git_ref_suffix_is_valid
AC-020 -> tests/test_learning_recovery_ci_discovery_v1.py::test_d4_successful_runs_survive_many_unrelated_workflow_results
AC-020 -> tests/test_learning_recovery_ci_discovery_v1.py::test_d4_same_run_newer_attempt_must_not_resurrect_prior_success

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
AC-012 -> OHM-Trade-Agent-v1/app/services/fs_durability.py
AC-012 -> OHM-Trade-Agent-v1/deploy/learning/run-recovery-learning-deploy.sh
AC-012 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_transaction_v1.py
AC-013 -> OHM-Trade-Agent-v1/app/services/learning_lineage_archive.py
AC-013 -> OHM-Trade-Agent-v1/app/services/fs_durability.py
AC-013 -> OHM-Trade-Agent-v1/docs/learning/CANONICAL_LINEAGE_BOUNDARY_V1.md
AC-013 -> OHM-Trade-Agent-v1/deploy/learning/run-recovery-learning-deploy.sh
AC-013 -> OHM-Trade-Agent-v1/tests/test_learning_lineage_archive_v1.py
AC-014 -> OHM-Trade-Agent-v1/app/services/learning_recovery_compatibility.py
AC-014 -> OHM-Trade-Agent-v1/docs/learning/RECOVERY_COMPATIBILITY_EXCEPTIONS.json
AC-014 -> .github/workflows/deploy-learning-recovery.yml
AC-014 -> OHM-Trade-Agent-v1/deploy/learning/run-recovery-learning-deploy.sh
AC-014 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_compatibility_v1.py
AC-015 -> OHM-Trade-Agent-v1/app/services/learning_derived_state.py
AC-015 -> OHM-Trade-Agent-v1/deploy/learning/opip-learning-sync.sh
AC-015 -> OHM-Trade-Agent-v1/deploy/learning/README.md
AC-015 -> OHM-Trade-Agent-v1/docs/learning/DERIVED_STATE_LINEAGE_TRANSITION_V1.md
AC-015 -> OHM-Trade-Agent-v1/app/jobs/build_phase3c_forward_outcomes.py
AC-015 -> OHM-Trade-Agent-v1/app/opip/discovery/maturation.py
AC-015 -> OHM-Trade-Agent-v1/app/services/opportunity_accountability.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_learning_derived_state_v1.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_learning_derived_state_cli_v1.py
AC-016 -> OHM-Trade-Agent-v1/app/services/learning_recovery_receipt.py
AC-016 -> OHM-Trade-Agent-v1/app/services/learning_recovery_attestation.py
AC-016 -> .github/workflows/deploy-learning-recovery.yml
AC-016 -> OHM-Trade-Agent-v1/deploy/learning/run-recovery-learning-deploy.sh
AC-016 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_receipt_v1.py
AC-016 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_attestation_v1.py
AC-017 -> OHM-Trade-Agent-v1/app/services/learning_recovery_attestation.py
AC-017 -> .github/workflows/deploy-learning-recovery.yml
AC-017 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_attestation_v1.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_attestation_cli_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_authorization_boundary_v1.py
AC-018 -> OHM-Trade-Agent-v1/app/services/learning_recovery_attestation.py
AC-018 -> .github/workflows/deploy-learning-recovery.yml
AC-018 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_authorization_state_v1.py
AC-018 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_authorization_boundary_v1.py
AC-019 -> OHM-Trade-Agent-v1/app/services/learning_recovery_compatibility.py
AC-019 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_inventory_determinism_v1.py


AC-020 -> .github/workflows/deploy-learning-recovery.yml
AC-020 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_ci_discovery_v1.py
AC-020 -> OHM-Trade-Agent-v1/tests/test_learning_recovery_attestation_v1.py
AC-020 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-LEARNING-RECOVERY-v1.md

DEFERRED DISCOVERIES:
- The production export tree contains historical generations from the previous canonical store (220,721 events) while current production runs a fresh genesis (8,819 events at capture). Retention and archival review remain owner work.
- Learning timers are disabled during recovery and require one-shot acceptance before re-enabling.

UNAPPROVED SCOPE CHANGES:
NONE

