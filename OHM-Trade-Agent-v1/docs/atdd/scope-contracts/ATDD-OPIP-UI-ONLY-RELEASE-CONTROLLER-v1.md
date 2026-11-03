INCREMENT:
ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1

OWNER-APPROVED INTENT:
Issue #496 authorizes extending the EXISTING owner-gated O'Pip release controller with one bounded, allowlisted UI-only action for the merged Control Center V2 source at ca06fa71838a5cd2d586dccb8a3fc1b8bbfdd517. The operator approved preparing that UI-only path in a single draft PR. That approval explicitly authorizes widening the forced-command allowlist by exactly two bounded actions (`deploy-ui`, `rollback-ui`), so the prior "exactly two commands" assertions in four pre-existing contract tests are updated to "exactly these four bounded actions" without weakening the no-shell/no-arbitrary-execution invariants. This increment authorizes controller, workflow, overlay-path, contract and test changes only: it does not deploy, does not merge, does not widen trading, scanner, F11, canonical-writer, paper, learning or analytics authority, and does not change production configuration. A follow-up owner review of the draft identified four genuine release-blocking defects plus two artifact-state defects; correcting them (AC-006 through AC-010: first-use component bootstrap, authorization precedence, exact previous-edge restoration including V2-to-V2, an independent 443 ingress check, and immutable retained artifacts with unique atomic receipts) is within this same increment because each correction is required for the UI-only path to be safe at all. No authority is widened by any of them.

ARCHITECTURE REFERENCES:
- deploy/remote/ohm-deploy and deploy/remote/lib/opip_release_tree.sh: the single release controller, its deploy lock, verified exact-SHA staging and receipts.
- deploy/remote/ohm-deploy-ssh: the forced-command gateway; the only remote execution surface.
- .github/workflows/deploy-production.yml: owner-only approval issue (#64), exact main SHA and green-CI gates.
- deploy/control-center/README.md and deploy/control-center/nginx/v2.conf: the reviewed opt-in V2 edge overlay.
- docs/atdd/scope-contracts/ATDD-OPIP-CONTROL-CENTER-V2-v1.md: the prior increment that produced the merged V2 source.
- tests/atdd_scope.py: the unmodified file-mapping and acceptance-check authority.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the existing forced-command SSH gateway, the installed release controller and the owner-only approval issue
WHEN:
the repository owner comments `/deploy-ui <40-hex>` or `/rollback-ui <40-hex>`
THEN:
the request traverses the same key, forced command, deploy lock, exact-main-SHA and green-CI gates as `/deploy`, forwards only those two allowlisted actions, and the controller hands off to the UI-only library and exits before any core, writer, scanner, F11, paper, learning or analytics path can run.

AC-002:
GIVEN:
a merged commit whose Nginx base config carries the `/dashboard/v2` include while the running core application is an older commit
WHEN:
the UI-only action stages that commit
THEN:
the candidate base config, V2 snippet and static bundle come only from the manifest-verified staged tree, the overlay mounts that tree by absolute path with a SHA-pinned image, and Nginx syntax is validated with the real certificate mount before any container is recreated.

AC-003:
GIVEN:
a staging, compose-graph, Nginx-syntax, budget, runtime-input or rollback-vehicle gate that cannot be proven
WHEN:
the UI-only action runs its preflight
THEN:
it refuses with a named blocker, performs no build and no container mutation, leaves the live release unchanged, and binds every Compose invocation to one explicit service without `--remove-orphans`.

AC-004:
GIVEN:
a post-cutover check that fails, or an owner-requested UI rollback
WHEN:
the gate does not pass
THEN:
the previously captured edge container is restored from the tracked base Compose file with the overlay omitted, the legacy dashboard is re-verified, the failure and rollback outcome are recorded in an exact-SHA receipt, and a rollback targeting a SHA that is not the recorded active UI release is refused without mutation.

AC-005:
GIVEN:
the released edge container
WHEN:
the UI-only release completes
THEN:
`/dashboard`, its redirect and the 443 Tradevisor vhost remain unchanged, `/dashboard/v2/` serves the reviewed bundle, every `/api/opip/*` path stays method-allowlisted and refuses unauthenticated access, the memory/CPU/pid caps and the two published ports are unchanged, and an immutable per-attempt receipt plus an append-only history record the outcome.

AC-006:
GIVEN:
a production host whose deployed release predates the controller, gateway and library corrections, so `deploy-ui` is neither forwarded nor loadable
WHEN:
the owner runs the one-time first-use bootstrap as root against a committed SHA that equals current `origin/main`
THEN:
the bootstrap materializes all four components from that exact commit, validates each before installing, installs them atomically with a bounded backup of the previous set, verifies bytes/mode/ownership/behaviour afterwards, leaves the forced-command wiring and every service, scheduler, credential and data path untouched, reports SUCCESS or NOT_NEEDED with an auditable receipt, restores the previous set (or reports an unproven restore) on any failure, and refuses a missing asset, a non-main SHA or a key wired to a different gateway.

AC-007:
GIVEN:
the owner-only approval issue and the forced-command gateway
WHEN:
the owner comments `/deploy-ui <40-hex>` or `/rollback-ui <40-hex>`
THEN:
both bounded actions must satisfy EVERY authorization predicate - issue_comment event, the designated approval issue, the repository owner identity, the OWNER association and a recognized command prefix - with evaluation proven behaviourally rather than by keyword presence, only an exact command followed by exactly one lowercase 40-hex SHA resolves, the release verdict is a shell decision rather than a boolean expression, and the gateway forwards only its allowlisted exact-SHA actions while refusing everything else without executing anything.

AC-008:
GIVEN:
the edge container captured read-only before cutover, including a V2 edge and its retained release tree
WHEN:
a post-cutover gate fails, or the owner requests a rollback of the recorded active UI release
THEN:
the ACTUAL previously running edge is recreated from its captured identity - the V2 overlay plus the previous V2 image and tree for a V2-to-V2 restore, the tracked base file for a legacy edge - the recreated container's image identity must match the captured one, the recorded active state is rewritten to agree with the running edge, and a missing or digest-inconsistent rollback artifact refuses the cutover before any mutation instead of being discovered afterwards.

AC-009:
GIVEN:
the dashboard on port 8443 and the Tradevisor webhook vhost on port 443 of the same container
WHEN:
the controller verifies the edge after cutover, after a restore, and before cutover
THEN:
the two surfaces are probed independently on their own ports with GET-only requests, the pre-cutover 443 answer is captured as a baseline, a listener that answered before cutover and answers nothing afterwards fails the release even when 8443 is perfect, a vhost that is dark before and after is reported as its pre-existing posture rather than as a failure, and no synthetic webhook POST is ever sent.

AC-010:
GIVEN:
a staged, manifest-verified candidate tree and the release that is currently mounted by the live edge
WHEN:
the UI-only release runs, is repeated for the same SHA, or is interrupted
THEN:
the mounted Nginx configuration and bundle come from an immutable, digest-verified, retained per-release copy that the staging provider's delete-and-re-extract cannot disturb, a repeat deploy reuses that copy untouched, retained rollback releases are never garbage-collected by a later attempt, and every attempt produces exactly one uniquely named, atomically written receipt plus an append-only history line whose recorded state matches the running edge.

EXPLICITLY OUT OF SCOPE:
- Any change to funded trading, Kraken order authority, F11, scanner, deterministic ranking, canonical writer, paper/Freqtrade execution, learning exporter, analytics platform or retention.
- Deploying, merging, restarting services, mutating production configuration or issuing `/deploy`, `/deploy-learning` or `/deploy-ui` from this increment.
- Restarting or reconfiguring any container other than `ohm-dashboard-edge`.
- Rewriting `authorized_keys`, `sudoers`, the scheduler or cron from the bootstrap; the bootstrap verifies the existing wiring read-only and refuses a mismatch.
- F10 eight-page parity, canonical count/PnL reconciliation, latency budgets or public certificate verification claims.
- Activating analytics retention or any new data plane.

FROZEN BOUNDARIES:
Trading authority stays absent; the core, writer, scanner, F11, paper, learning and analytics paths are unreachable from the UI-only branch; `.env` and `.env.core` remain the core deploy's authority and are never written here; production data and evidence are neither read destructively nor deleted; ports, networks, credentials and resource caps are unchanged; the forced command exposes no shell; no second control plane, scheduler or credentials are introduced.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_ui_only_release_controller_v1.py::test_forced_command_exposes_only_bounded_ui_release_actions
AC-001 -> tests/test_opip_ui_only_release_controller_v1.py::test_controller_ui_hand_off_exits_before_any_core_path
AC-001 -> tests/test_opip_ui_only_release_controller_v1.py::test_owner_gate_requires_exact_main_sha_and_green_ci
AC-002 -> tests/test_opip_ui_only_release_controller_v1.py::test_overlay_pins_only_the_verified_candidate_tree
AC-003 -> tests/test_opip_ui_only_release_controller_v1.py::test_failed_preflight_blocks_cutover_without_mutation
AC-004 -> tests/test_opip_ui_only_release_controller_v1.py::test_post_cutover_failure_restores_the_previous_edge
AC-004 -> tests/test_opip_ui_only_release_controller_v1.py::test_rollback_refuses_a_sha_that_is_not_the_active_release
AC-005 -> tests/test_opip_ui_only_release_controller_v1.py::test_successful_cutover_recreates_only_the_edge_and_records_a_receipt
AC-005 -> tests/test_opip_ui_only_release_controller_v1.py::test_legacy_surface_isolation_and_receipts_are_preserved
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_bootstrap_installs_the_complete_controller_set
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_ui_release_is_unreachable_from_the_old_gateway
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_bootstrap_makes_ui_release_reachable_and_keeps_the_allowlist
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_missing_component_refuses_without_partial_install
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_repeated_bootstrap_is_idempotent
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_wrong_mode_is_corrected_not_short_circuited
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_installation_failure_restores_the_previous_set
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_installation_and_restore_failure_is_unproven
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_refuses_a_sha_that_is_not_origin_main
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_gateway_wiring_mismatch_is_refused
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_gateway_wiring_is_verified_never_rewritten
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_check_mode_reports_without_changing_anything
AC-006 -> tests/test_opip_ui_release_controller_bootstrap_v1.py::test_ac_006_bootstrap_touches_no_forbidden_control_plane
AC-007 -> tests/test_opip_ui_release_authorization_gate_v1.py::test_ac_007_the_shipped_gate_requires_every_predicate
AC-007 -> tests/test_opip_ui_release_authorization_gate_v1.py::test_ac_007_rollback_is_not_authorized_by_operator_precedence
AC-007 -> tests/test_opip_ui_release_authorization_gate_v1.py::test_ac_007_spoofed_repository_context_is_not_authorized
AC-007 -> tests/test_opip_ui_release_authorization_gate_v1.py::test_ac_007_approval_comment_must_be_an_exact_bounded_command
AC-007 -> tests/test_opip_ui_release_authorization_gate_v1.py::test_ac_007_approval_comment_resolver_never_interpolates
AC-007 -> tests/test_opip_ui_release_authorization_gate_v1.py::test_ac_007_the_release_verdict_is_not_a_boolean_expression
AC-007 -> tests/test_opip_ui_release_authorization_gate_v1.py::test_ac_007_gateway_forwards_only_the_bounded_allowlist
AC-007 -> tests/test_opip_ui_release_authorization_gate_v1.py::test_ac_007_gateway_refuses_everything_else_without_executing
AC-008 -> tests/test_opip_ui_only_release_controller_v1.py::test_ac_008_v2_to_v2_rollback_restores_the_previous_v2_release
AC-008 -> tests/test_opip_ui_only_release_controller_v1.py::test_ac_008_corrupt_rollback_artifact_refuses_the_cutover
AC-009 -> tests/test_opip_ui_only_release_controller_v1.py::test_ac_009_broken_443_cannot_be_hidden_by_a_healthy_8443
AC-009 -> tests/test_opip_ui_only_release_controller_v1.py::test_ac_009_a_443_that_is_dark_before_and_after_is_not_a_regression
AC-010 -> tests/test_opip_ui_only_release_controller_v1.py::test_ac_010_repeated_deploy_never_disturbs_the_live_mount
AC-010 -> tests/test_opip_ui_only_release_controller_v1.py::test_ac_010_receipts_are_unique_and_never_partially_written

IMPLEMENTATION MAP:
AC-001 -> .github/workflows/deploy-production.yml
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy-ssh
AC-002 -> .github/workflows/pytest.yml
AC-002 -> OHM-Trade-Agent-v1/deploy/control-center/docker-compose.v2.yml
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_ui_release.sh
AC-003 -> OHM-Trade-Agent-v1/deploy/remote/README.md
AC-003 -> OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh
AC-004 -> OHM-Trade-Agent-v1/deploy/control-center/README.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_ui_only_release_controller_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_deploy_controller_bootstrap_only.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_release_protection_preflight_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_export_diagnostics_readonly_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r4_f8a_readiness_observability.py
AC-006 -> OHM-Trade-Agent-v1/deploy/remote/bootstrap-ui-release-controller.sh
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_ui_release_controller_bootstrap_v1.py
AC-007 -> .github/workflows/deploy-production.yml
AC-007 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy-ssh
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_ui_release_authorization_gate_v1.py
AC-008 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_ui_release.sh
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_ui_only_release_controller_v1.py
AC-009 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_ui_release.sh
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_ui_only_release_controller_v1.py
AC-010 -> OHM-Trade-Agent-v1/deploy/remote/lib/opip_ui_release.sh
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_ui_only_release_controller_v1.py

DEFERRED DISCOVERIES:
- The host runs the pre-merge controller set, so one owner console action (bootstrap-ui-release-controller.sh --apply --sha <merged SHA>) installs all four components before the first `/deploy-ui`; that step performs no deployment and no container mutation.
- Public reachability of 8443/443 from an operator workstation is unproven (observation limitation, not an outage); the controller verifies over loopback with SNI, and public certificate trust is unchanged by this release.
- Live F10 parity, canonical reconciliation and resource measurements remain unverified and are not claimed.
- The 443 Tradevisor vhost is dark on the host whenever no rendered verification value is provisioned; the controller records that as a baseline and fails only on a CHANGE, so a pre-existing dark vhost is reported rather than certified as healthy ingress.

UNAPPROVED SCOPE CHANGES:
NONE

