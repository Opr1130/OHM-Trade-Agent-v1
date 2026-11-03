INCREMENT:
ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1

OWNER-APPROVED INTENT:
Issue #496 authorizes extending the EXISTING owner-gated O'Pip release controller with one bounded, allowlisted UI-only action for the merged Control Center V2 source at ca06fa71838a5cd2d586dccb8a3fc1b8bbfdd517. The operator approved preparing that UI-only path in a single draft PR. That approval explicitly authorizes widening the forced-command allowlist by exactly two bounded actions (`deploy-ui`, `rollback-ui`), so the prior "exactly two commands" assertions in four pre-existing contract tests are updated to "exactly these four bounded actions" without weakening the no-shell/no-arbitrary-execution invariants. This increment authorizes controller, workflow, overlay-path, contract and test changes only: it does not deploy, does not merge, does not widen trading, scanner, F11, canonical-writer, paper, learning or analytics authority, and does not change production configuration.

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

EXPLICITLY OUT OF SCOPE:
- Any change to funded trading, Kraken order authority, F11, scanner, deterministic ranking, canonical writer, paper/Freqtrade execution, learning exporter, analytics platform or retention.
- Deploying, merging, restarting services, mutating production configuration or issuing `/deploy`, `/deploy-learning` or `/deploy-ui` from this increment.
- Restarting or reconfiguring any container other than `ohm-dashboard-edge`.
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

DEFERRED DISCOVERIES:
- The host runs the pre-merge controller, so one owner console action (bootstrap-release-tree-controller.sh --apply --sha <merged SHA>) installs the extended controller pair before the first `/deploy-ui`; that step performs no deployment.
- Public reachability of 8443/443 from an operator workstation is unproven (observation limitation, not an outage); the controller verifies over loopback with SNI, and public certificate trust is unchanged by this release.
- Live F10 parity, canonical reconciliation and resource measurements remain unverified and are not claimed.

UNAPPROVED SCOPE CHANGES:
NONE

