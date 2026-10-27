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
AC-007 -> OHM-Trade-Agent-v1/deploy/remote/bootstrap-release-tree-controller.sh
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_release_tree_controller_v1.py

DEFERRED DISCOVERIES:
- The bootstrap records the running release as an `unverified-baseline` tree because the production host has no external manifest for it; the first release-tree activation replaces that record with a verified one.
- `rsync`'s size+mtime quick check silently skipped a same-size, same-mtime file during the isolated self-test, so activation pins `--checksum`.
- Deploying a SHA still requires the owner-gated workflow; this increment performs no deployment.

UNAPPROVED SCOPE CHANGES:
NONE

