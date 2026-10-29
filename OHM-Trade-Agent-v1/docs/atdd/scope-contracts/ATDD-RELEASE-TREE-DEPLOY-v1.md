INCREMENT:
ATDD-RELEASE-TREE-DEPLOY-v1

OWNER-APPROVED INTENT:
The production Trading droplet is a certified release-tree host: `/opt/OHM-Trade-Agent-v1`
carries the running source installed out of band from the certified release tree and has
no Git metadata, while the approved GitHub control plane assumes a Git worktree and died
at `stat -c %U /opt/OHM-Trade-Agent-v1/.git` before any gate could run. This increment
adds the smallest source-model correction: when no worktree exists, the controller binds
a release to an exact commit through a root-owned object cache cloned from the canonical
origin, records the commit, tree, artifact digest, manifest digest and install identity as
separate facts, stages the target outside the application root, activates it by a
content-verified synchronization that never touches `.env` or `data/`, and restores the
retained previous release on failure instead of running `git reset --hard`. No Git history
is fabricated and no `.git` is created at the production root. A Git droplet keeps its
original inline gates byte-for-byte. One root-only owner bootstrap, unreachable through
the forced-command gateway and performing no deployment, provisions the object cache and
the verified baseline.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/deploy/remote/ohm-deploy: the owner-gated controller; selects the source model and keeps the Git gates inline.
- OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh: the release-tree source provider (provenance, staging, activation, rollback vehicle).
- OHM-Trade-Agent-v1/deploy/remote/bootstrap-release-tree-controller.sh: the one-time root-only bootstrap; provisions provenance, performs no deployment.
- OHM-Trade-Agent-v1/deploy/remote/ohm-deploy-ssh: the forced-command gateway, deliberately unchanged at two commands.
- OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh: the certified release-tree install convention whose exclusions are reused.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a production root with no `.git` and a root-owned object cache cloned from the canonical origin
WHEN:
the controller resolves its source model
THEN:
it selects the release-tree model, creates no Git metadata at the production root, and refuses to start when neither a worktree nor a cache exists

AC-002:
GIVEN:
an exact 40-hex target that equals current origin/main
WHEN:
the release-tree source is prepared
THEN:
the artifact is produced from that commit, `provenance.env` records the commit, tree, archive digest, manifest digest and file count, every extracted file matches its sha256 manifest, and any unrecorded file in the tree is refused

AC-003:
GIVEN:
an unapproved or damaged release input
WHEN:
the controller prepares or activates it
THEN:
a target that is not current origin/main exits 65, a source tree that deviates from the installed release exits 66, and an incomplete staging directory fails before the live tree is touched

AC-004:
GIVEN:
a staged release that will replace the running source
WHEN:
activation runs
THEN:
only source content is synchronized, `.env` keeps its ownership, mode and bytes, `data/` including the canonical database is untouched, and byte-code caches are neither deleted nor trusted

AC-005:
GIVEN:
a failed or aborted activation
WHEN:
the controller rolls back
THEN:
the retained previous release's real source bytes are restored and re-verified against their manifest, and a missing or corrupted rollback vehicle is reported as failure rather than success

AC-006:
GIVEN:
a historical Git droplet with a real worktree
WHEN:
the controller runs
THEN:
the original inline gates (origin/main equality, dirty worktree, untracked build context) and the `worktree add --detach` candidate materialization are still present and byte-identical, so the AC-024 protection-preflight boundary is unchanged

AC-007:
GIVEN:
an owner who must provision the corrected controller once
WHEN:
the bootstrap runs
THEN:
it requires root, refuses to deploy anything (no container, no `/deploy`, no gateway path), creates the object cache from the canonical origin, records the running release as the verified baseline and rollback vehicle, installs the release-tree provider at `/usr/local/lib/opip/opip_release_tree.sh` and the controller atomically, and writes a receipt

AC-008:
GIVEN:
a host whose running release predates the release-tree source provider
WHEN:
the controller initialises
THEN:
it resolves the provider from the stable root-controlled location first, accepts an in-tree copy when the release ships one, and refuses with exit 69 before any deployment gate when neither exists or the provider lacks a required entry point

AC-009:
GIVEN:
a Git-less certified release-tree host whose scheduler reconciliation must prove the deployed identity
WHEN:
the controller-preservation guard resolves the repository HEAD
THEN:
it keeps the Git worktree HEAD first, otherwise uses the controller-recorded identity of the active release (`current-release.env`, written only by a manifest-verified activation) and only when it is a 40-hex SHA, never consults rollback state, and otherwise still fails closed with the existing message

AC-010:
GIVEN:
a canonical store whose per-type backlog is large and a consumer that pages it with an exclusive `(history_epoch, local_sequence)` cursor under a declared single-read bound
WHEN:
the release runtime verifier reads one page of feature snapshots or feasibility evidence
THEN:
the page is served by an ordering index as a range seek that returns only its page, no temp B-tree is built, the canonical order and the exclusive cursor semantics are unchanged, and the single-read bound is not raised

AC-011:
GIVEN:
a certified Git-less release-tree host whose approved release was installed by the controller, and a Git droplet that still has a checkout
WHEN:
the Tradevisor activation gate proves the approved SHA, provenance and recovery artifacts
THEN:
a Git checkout keeps its historical validation unchanged, while a Git-less host must agree on the active release record, the installed source manifest digest, the exact-commit artifact provenance and file count, the live source tree byte-for-byte with no unrecorded file, and a retained rollback vehicle whose manifest digest matches and whose archive is valid; any missing, mismatched or tampered fact refuses activation, and the application and writer image revisions must both still be the approved SHA

AC-012:
GIVEN:
an owner `.env` that carries funded exchange credentials, and a core service that must never inherit them
WHEN:
the deploy controller activates a release
THEN:
it derives a filtered core environment from `.env` that excludes every name in `release_profiles.FUNDED_CREDENTIAL_KEYS`, keeps every other setting, is root-owned mode 0600, produced atomically and idempotently without printing any value, is regenerated on rotation and on rollback, refuses to proceed when the source is missing or symlinked, and the core service consumes only that filtered file; the owner's `.env` and all credentials remain intact, the Tradevisor credential-isolation gate is unchanged, and the operator `/positions` and `/orders` views degrade to a bounded message when no credential is present
AND the filtered file exists before ANY Compose operation that resolves the core service: it is generated for the release installed before activation (ahead of the pre-activation `docker compose exec` evidence-baseline read, which is the first Compose call), regenerated after activation and before the build/recreate, and generated at the start of the rollback path BEFORE the producer-quiescence proof, so a deploy that fails before the file is generated still reaches a provable quiescence instead of aborting as `PRODUCER_QUIESCENCE_UNPROVEN`; a source provider that cannot provide the generator refuses at start-up, before activation and before any Compose call, naming the owner-operated controller bootstrap as the remedy; the file is runtime-only (`rt_list_tree`/`rt_snapshot_current` exclude it) so it can never make the installed release or its rollback vehicle fail verification, while an unrecorded or modified source file still fails closed; and the source provider is installed from the deployed release by scheduler reconciliation on the same path as the controller, so it can never be one release behind it

AC-013:
GIVEN:
a candidate release whose read-only protection preflight must execute the candidate's own evaluator against current production data, and a Docker daemon that may no longer hold the exact image ID the running core container was created from
WHEN:
the deploy controller prepares the preflight container
THEN:
it executes only an image whose identity and runtime compatibility are both proven: the running core container's own image when the daemon still has it, otherwise the reference that container was created with, resolved to a fully content-addressed image ID and admitted only when the image's `org.opencontainers.image.revision` label names a real commit in the trusted source cache and that commit's `Dockerfile` and `requirements.txt` are byte-identical to the candidate's, because the image supplies only the interpreter and installed dependencies while the candidate code and production data are bind-mounted read-only; the content-addressed ID (never a mutable tag) is what runs, so a concurrent retag cannot change the runtime; the resolution outcome, image ID, source revision, source class and an allowlisted failure reason are emitted as bounded provenance markers that carry no environment value or credential; and when no image can be proven the release is refused before any mutation with `OPIP_PROTECTION_PREFLIGHT=FAIL`, `OPIP_PROTECTION_PREFLIGHT_ABORT=REFUSED_BEFORE_MUTATION` and `OPIP_PRODUCTION_MUTATION=NOT_STARTED`, never by substituting an arbitrary image and never by weakening, skipping or reordering the protection evaluation or its F11 evidence

AC-014:
GIVEN:
a release-tree core whose filtered environment removes the funded Kraken names, so F11 protection runtime has no credential, while the preflight still read the owner's unfiltered `.env` and therefore disagreed with the runtime; and a durable `KRAKEN:READ_ONLY_AUTH` incident whose `AUTH_CONFIG` recovery cannot be obtained from a release that the same incident blocks
WHEN:
the release is prepared, the deployed core starts, and the incident is cleared
THEN:
exactly one bounded read-only credential may cross the isolation boundary - the dedicated protection key `OPIP-V2-PROTECTION-READONLY` delivered only under `OPIP_F11_KRAKEN_READONLY_API_KEY` and `OPIP_F11_KRAKEN_READONLY_API_SECRET`, carried whole or refused whole by the existing atomic, root-owned mode 0600 generator, with every funded name still removed and the owner's `.env` never modified; the read-only client resolves that pair ahead of the legacy names, refuses a half pair instead of completing it from another name, and keeps the legacy pair only as the SAFE_BASELINE/Git-worktree path; the client parses `GetApiKeyInfo` in both documented spellings (`api_key_name`/`apiKeyName`, `last_used`/`lastUsed`, `ip_allowlist`/`ipAllowlist`), refuses conflicting representations, and F11's own consumers accept the credential only when the reported key name is exactly `OPIP-V2-PROTECTION-READONLY`, the reported scopes are exactly `query-funds` and `query-open-trades`, and the IP allowlist is exactly the confirmed Trading droplet (`178.128.147.125` or its equivalent single-host `/32` representation), refusing unknown, trading, funding, ledger, closed-trade, export or WebSocket scopes and never falling back to funded credentials
AND the verification job reports `PASS` only when, in one invocation, the key identity AND its exact scopes AND the confirmed allowlist AND `Balance` AND `OpenPositions` all succeed; the identity audit is reported separately, and the process exits 0 if and only if the whole proof passed, so a `PASS` marker from a job that then failed a read can never be accepted and no caller may mask the exit status with `|| true`
AND the preflight derives its environment file from the CANDIDATE core service: a core that declares `.env.core` is given exactly that controller-generated file and the release is REFUSED before mutation when it is missing, with no fallback to the owner's unfiltered `.env`; the owner's `.env` is used only for a genuinely verified legacy compose shape whose core service itself declares it; the preflight records the verified credential's one-way fingerprint, the recreated core must reproduce the same fingerprint and the same dedicated-pair provenance or the release fails, and missing or mismatched proof fails closed
AND `EVIDENCE_SHADOW` REQUIRES a verified read-only protection credential: an absent credential refuses the release in the preflight, before any mutation, and the credential-less posture remains an explicitly `SAFE_BASELINE`-only exception that can never be borrowed as an `EVIDENCE_SHADOW` success; the Tradevisor activation gate applies the same decision, refuses a half pair, and does all of it before any ingress is enabled
AND the durable `KRAKEN:READ_ONLY_AUTH` incident - `AUTH_CONFIG`-owned, not monitor-owned - is closed only by an owner-run operation that first proves the credential identity, both read-only queries and the matching incident row through the existing `system_incidents.observe_recovery` authority, refuses on any failure leaving the row `OPEN`/`CHANGED` with admissions suspended, is idempotent when already recovered, and can never close an unrelated scope; because the in-release recovery operation cannot exist in the release that the incident blocks, one exact owner-executable command exists that performs the same proof and the same single transition against the CURRENTLY RUNNING release, executes the running core's own content-addressed image only after proving that exact image is present locally (a pruned or removed image is refused by name, never substituted with an unverified one), mounts production data read-only for the plan and read-write only for the owner-approved apply, receives only the two dedicated names (never the owner's whole `.env`), never deploys, restarts, activates or trades, and the owner order that makes it usable is the bootstrap of the exact merged SHA's release-tree controller and provider library BEFORE any deployment, because the preflight and the running-core proof decide through that library and a library that predates it refuses at start-up

AC-015:
GIVEN:
the existing production dashboard edge service is rendered by Docker Compose with a shell startup script, and the Tradevisor activation gate must replace the running edge configuration without touching trading or learning authority
WHEN:
Compose resolves the edge entrypoint and an owner-approved Tradevisor activation attempts to introduce the webhook virtual host
THEN:
every shell-local variable used to validate the edge verification value, certificate and template output survives Compose interpolation literally (using doubled dollar signs), and the activation re-creates the edge container when its vhost is absent or stale, independently verifies the rendered vhost, preserves a healthy already-current edge idempotently, and uses the same re-create behavior for rollback; the dashboard must remain reachable and failure must refuse activation rather than claiming success

EXPLICITLY OUT OF SCOPE:
- Converting the production source root into a Git worktree, or creating any `.git` there.
- Fabricating commits, refs, trees or a previous-good SHA.
- Changing the forced-command gateway beyond its existing two commands.
- Any trading-logic, risk-gate, notification, scheduler or release-profile change.
- Enabling learning, Paper-v2, Committee, Tradevisor signal evaluation or funded execution.
- Running a deployment, or recreating any container, as part of this change.

FROZEN BOUNDARIES:
- Exactly one canonical writer; activation never starts a second one.
- EVIDENCE_SHADOW profile and its architecture gate are unchanged.
- Production data, `.env`, canonical database and evidence stay external to the release artifact.
- `last-good-sha` remains the rollback identity and is only written by the existing success path.
- The deploy lock, protection preflight and runtime verifier ordering are unchanged.

OWNER ACTION ORDER (F11 read-only protection credential):
The order is load-bearing; each step is a precondition of the next.
A. Securely create and configure the dedicated F11 read-only pair (`OPIP-V2-PROTECTION-READONLY`:
   exactly `query-funds` + `query-open-trades`, IP allowlist exactly `178.128.147.125`/32) in the
   owner's `.env` as `OPIP_F11_KRAKEN_READONLY_API_KEY` / `OPIP_F11_KRAKEN_READONLY_API_SECRET`.
   Funded credentials are untouched; the pair is never printed by any controller.
B. Run the owner-approved, evidence-backed incident recovery (plan first, then apply) against the
   CURRENTLY RUNNING release. It refuses unless a fresh identity/scopes/allowlist check, `Balance`
   and `OpenPositions` all succeed, and it closes only `KRAKEN:READ_ONLY_AUTH` through the existing
   `AUTH_CONFIG` authority. Without this step the EVIDENCE_SHADOW readiness gate still reports
   `BLOCK_INCIDENT_OPEN` and every preflight refuses before mutation - correctly.
C. Merge the corrected controller only after the exact-head CI run for the final PR head passes.
D. Install the exact merged SHA's controller and provider library, BEFORE any deployment:
     sudo -n bash /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1/deploy/remote/bootstrap-release-tree-controller.sh \
       --apply --sha <EXACT_MERGED_MAIN_SHA>
   That existing bootstrap materializes `/usr/local/sbin/ohm-deploy` and
   `/usr/local/lib/opip/opip_release_tree.sh` from that exact commit in the trusted object cache.
   It is required now: the preflight, the running-core proof and the Tradevisor gate decide through
   the provider library (`opip_core_env_mode`, `opip_f11_credential_state`,
   `opip_f11_fingerprint_parity`, `opip_f11_release_credential_gate`), and a library that predates
   them refuses the deploy at start-up (exit 69) before activation. The bootstrap performs no
   deployment: no container is started, stopped or recreated, and `.env`/`data/` are untouched.
E. Approve deployment of the exact merged `main` SHA through the existing owner-gated workflow.
F. Validate F11 in the running core and the Tradevisor activation gate in read-only mode.
G. Activate Tradevisor only with a separate explicit owner approval.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_release_tree_controller_v1.py::test_release_tree_mode_is_selected_when_no_git_metadata_exists
AC-002 -> tests/test_opip_release_tree_controller_v1.py::test_exact_commit_artifact_is_staged_and_manifest_verified
AC-003 -> tests/test_opip_release_tree_controller_v1.py::test_foreign_target_is_refused
AC-003 -> tests/test_opip_release_tree_controller_v1.py::test_hand_edited_source_is_refused
AC-003 -> tests/test_opip_release_tree_controller_v1.py::test_incomplete_staging_refuses_activation
AC-004 -> tests/test_opip_release_tree_controller_v1.py::test_activation_preserves_env_and_production_data
AC-005 -> tests/test_opip_release_tree_controller_v1.py::test_rollback_restores_the_retained_previous_release
AC-005 -> tests/test_opip_release_tree_controller_v1.py::test_tampered_retained_release_is_reported_as_failure
AC-006 -> tests/test_opip_release_tree_controller_v1.py::test_git_worktree_mode_is_still_selected_and_keeps_its_inline_gates
AC-007 -> tests/test_opip_release_tree_controller_v1.py::test_bootstrap_installs_the_library_at_a_stable_root_location
AC-008 -> tests/test_opip_release_tree_controller_v1.py::test_controller_prefers_the_stable_library_and_refuses_when_absent
AC-009 -> tests/test_opip_release_tree_controller_v1.py::test_reconcile_resolves_release_tree_identity_without_git
AC-010 -> tests/test_opip_canonical_cursor_read_plan_v1.py::test_canonical_cursor_page_is_an_index_seek_not_a_full_sort
AC-010 -> tests/test_opip_canonical_cursor_read_plan_v1.py::test_the_index_preserves_canonical_order_and_cursor_semantics
AC-011 -> tests/test_tradevisor_activation_release_tree_v1.py::test_release_tree_evidence_is_accepted
AC-011 -> tests/test_tradevisor_activation_release_tree_v1.py::test_tampered_or_missing_evidence_is_refused
AC-011 -> tests/test_tradevisor_activation_release_tree_v1.py::test_git_worktree_path_keeps_its_historical_validation
AC-011 -> tests/test_tradevisor_activation_release_tree_v1.py::test_source_listing_runtime_only_exclusions_match_the_controller
AC-011 -> tests/test_tradevisor_activation_release_tree_v1.py::test_source_listing_keeps_unexpected_files_and_drops_runtime_only_paths
AC-011 -> tests/test_tradevisor_activation_release_tree_v1.py::test_runtime_only_core_env_is_accepted_by_the_release_tree_gate
AC-015 -> tests/test_tradevisor_production_edge_v1.py::test_ac_015_compose_preserves_edge_shell_variables_after_interpolation
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_funded_credential_names_match_the_python_authority
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_generator_removes_funded_names_and_keeps_the_rest
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_generator_is_idempotent_and_follows_rotation
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_missing_or_symlinked_source_fails_closed
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_core_compose_consumes_only_the_filtered_environment
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_controller_generates_before_every_compose_operation_and_on_both_failure_paths
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_core_env_exists_before_the_first_compose_call_that_resolves_the_core_service
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_the_pre_incident_order_fails_exactly_as_reported
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_rollback_generates_the_core_env_before_the_producer_quiescence_proof
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_rollback_without_the_generation_gate_reproduces_the_reported_abort
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_rollback_stays_fail_closed_when_the_core_env_cannot_be_generated
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_runtime_core_env_is_not_part_of_the_release_manifest
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_controller_refuses_before_activation_when_the_library_cannot_generate
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_scheduler_reconciliation_keeps_the_source_provider_in_lockstep
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_bootstrap_preserves_a_proven_activation_record
AC-013 -> tests/test_release_protection_preflight_v1.py::test_ac_013_running_container_image_is_used_when_it_is_present
AC-013 -> tests/test_release_protection_preflight_v1.py::test_ac_013_absent_running_image_falls_back_to_a_verified_reference
AC-013 -> tests/test_release_protection_preflight_v1.py::test_ac_013_incompatible_image_is_refused_and_never_substituted
AC-013 -> tests/test_release_protection_preflight_v1.py::test_ac_013_no_usable_image_is_refused
AC-013 -> tests/test_release_protection_preflight_v1.py::test_ac_013_preflight_refuses_before_mutation_when_no_image_is_verified
AC-013 -> tests/test_release_protection_preflight_v1.py::test_ac_013_image_markers_are_bounded_and_value_free
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_the_edited_remote_operators_parse
AC-012 -> tests/test_tradevisor_core_env_isolation_v1.py::test_positions_fails_safe_when_the_credential_is_absent
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_resolver_prefers_the_dedicated_pair_over_the_legacy_pair
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_partial_protection_pair_is_refused_and_never_completed
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_legacy_pair_is_used_only_when_no_protection_pair_exists
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_no_credential_configuration_degrades_without_raising
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_partial_pair_disables_the_client_with_the_reason
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_live_snake_case_key_info_is_parsed
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_camel_case_key_info_remains_supported
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_conflicting_key_name_representations_are_refused
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_requires_the_exact_key_name
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_requires_a_reported_key_name
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_accepts_the_two_permission_set
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_refuses_extra_read_only_scopes
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_refuses_a_missing_required_scope
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_refuses_trading_and_funding_permissions
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_requires_the_confirmed_allowlist
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_refuses_a_wider_or_foreign_allowlist
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_refuses_an_unreported_allowlist
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_identity_refuses_malformed_permissions
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_revoked_or_failed_key_info_is_never_verified
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_f11_consumers_use_the_protection_gate
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_f11_permissions_do_not_include_history_ledger_or_export
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_generator_carries_the_pair_and_still_excludes_funded_names
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_protection_credential_names_match_the_python_authority
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_generator_refuses_a_partial_pair
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_generator_never_prints_a_value
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_deploy_proves_the_credential_in_the_running_core
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_tradevisor_gate_verifies_the_exception_and_refuses_a_half_pair
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_recovery_requires_matching_evidence_and_is_idempotent
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_recovery_refuses_when_the_proof_fails
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_recovery_only_closes_its_own_incident_key
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_fingerprint_is_stable_and_value_free
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_core_env_mode_is_read_from_the_candidate_compose
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_preflight_never_falls_back_to_the_owner_env
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_credential_state_never_verifies_a_masked_or_partial_result
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_fingerprint_parity_requires_two_matching_well_formed_values
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_release_credential_gate_refuses_absent_for_evidence_shadow
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_tradevisor_gate_decision_matches_the_deploy_controller_decision
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_verifier_passes_only_when_identity_balance_and_positions_all_hold
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_verifier_never_reports_pass_when_balance_fails
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_verifier_never_reports_pass_when_open_positions_fail
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_verifier_reports_absent_without_any_credential
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_verifier_reports_a_half_pair_as_invalid_not_absent
AC-014 -> tests/test_f11_readonly_credential_exception_v1.py::test_verifier_refuses_a_key_that_is_not_the_dedicated_protection_key
AC-014 -> tests/test_release_protection_preflight_v1.py::test_ac_014_evidence_shadow_refuses_an_absent_credential_before_mutation
AC-014 -> tests/test_release_protection_preflight_v1.py::test_ac_014_preflight_refuses_a_masked_credential_verification
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_recovers_only_the_matching_incident_from_fresh_proof
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_fingerprint_matches_the_release_fingerprint_helper
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_plan_mode_writes_nothing
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_refuses_when_the_proof_fails
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_is_idempotent_when_the_row_is_already_recovered
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_never_closes_an_unrelated_incident
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_refuses_a_row_that_is_not_this_incident_identity
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_refuses_unreadable_durable_evidence
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_verdict_never_reads_a_partial_result_as_success
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_recovery_script_is_syntactically_valid
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_recovery_probe_only_receives_the_dedicated_pair
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_recovery_refuses_to_run_without_root
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_recovery_requires_the_running_image_to_be_present_locally
AC-014 -> tests/test_f11_incident_recovery_predeploy_v1.py::test_probe_data_mount_is_read_only_for_plan_and_writable_only_for_apply

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-TREE-DEPLOY-v1.md
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh
AC-003 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh
AC-004 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh
AC-005 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh
AC-006 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-008 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-009 -> OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/canonical/schema.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_cursor_read_plan_v1.py
AC-011 -> OHM-Trade-Agent-v1/deploy/tradevisor/activate-tradevisor-advisory.sh
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_activation_release_tree_v1.py
AC-015 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-015 -> OHM-Trade-Agent-v1/deploy/tradevisor/activate-tradevisor-advisory.sh
AC-015 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-012 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh
AC-012 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-012 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-012 -> OHM-Trade-Agent-v1/app/services/telegram_command_center.py
AC-012 -> .github/workflows/pytest.yml
AC-012 -> .github/workflows/freqtrade-contract.yml
AC-012 -> .github/workflows/m07-cutover-toolkit.yml
AC-012 -> OHM-Trade-Agent-v1/tests/test_tradevisor_core_env_isolation_v1.py
AC-012 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r3_concurrency_and_compose.py
AC-012 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r3_controller_parity.py
AC-007 -> OHM-Trade-Agent-v1/deploy/remote/bootstrap-release-tree-controller.sh
AC-012 -> OHM-Trade-Agent-v1/deploy/remote/bootstrap-release-tree-controller.sh
AC-012 -> OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh
AC-013 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-013 -> OHM-Trade-Agent-v1/tests/test_release_protection_preflight_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-014 -> OHM-Trade-Agent-v1/app/exchanges/kraken_protection_credentials.py
AC-014 -> OHM-Trade-Agent-v1/app/exchanges/kraken_private.py
AC-014 -> OHM-Trade-Agent-v1/app/services/kraken_credential_audit.py
AC-014 -> OHM-Trade-Agent-v1/app/services/kraken_exposure_resolver.py
AC-014 -> OHM-Trade-Agent-v1/app/services/kraken_position_verification.py
AC-014 -> OHM-Trade-Agent-v1/app/jobs/verify_f11_readonly_credential.py
AC-014 -> OHM-Trade-Agent-v1/app/jobs/recover_f11_readonly_auth_incident.py
AC-014 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh
AC-014 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-014 -> OHM-Trade-Agent-v1/deploy/tradevisor/activate-tradevisor-advisory.sh
AC-014 -> OHM-Trade-Agent-v1/tests/test_f11_readonly_credential_exception_v1.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_kraken_balance_identity_v1.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_kraken_exposure_resolver_r1_1.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_kraken_position_verification.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_release_protection_preflight_v1.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_wave9_position_protection.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_wave9_review_fixes.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_validation_hardening_critical_v1.py
AC-014 -> OHM-Trade-Agent-v1/deploy/remote/recover-f11-readonly-auth-incident.sh
AC-014 -> OHM-Trade-Agent-v1/tests/test_f11_incident_recovery_predeploy_v1.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-TREE-DEPLOY-v1.md

DEFERRED DISCOVERIES:
- The bootstrap records the running release as an `unverified-baseline` tree because the production host has no external manifest for it; the first release-tree activation replaces that record with a verified one.
- `rsync`'s size+mtime quick check silently skipped a same-size, same-mtime file during the isolated self-test, so activation pins `--checksum`.
- Deploying a SHA still requires the owner-gated workflow; this increment performs no deployment.

UNAPPROVED SCOPE CHANGES:
NONE

