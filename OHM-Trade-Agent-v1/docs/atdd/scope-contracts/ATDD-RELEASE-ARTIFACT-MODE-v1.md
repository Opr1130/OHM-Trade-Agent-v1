INCREMENT:
ATDD-RELEASE-ARTIFACT-MODE-v1

OWNER-APPROVED INTENT:
Make the O'Pip forward-deploy and rollback path independent of production Git metadata and of a host source rebuild, using private GHCR image digests, an atomic versioned release receipt, and a checksummed release bundle for host-executed inputs. Artifact mode is default-off; the legacy Git path is preserved verbatim. This increment authorizes only the deploy-controller, release-tooling, Compose overlay, ATDD/CI and governance paths listed in the implementation map. It creates no trading, learning, paper, funded or exchange authority and changes no runtime behaviour while artifact mode is off.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/deploy/remote/ohm-deploy: the single existing controller; artifact mode is an explicit, default-off branch inside it.
- OHM-Trade-Agent-v1/deploy/release/release_artifact.py: receipt, bundle, registry-digest and host-script contract (stdlib only).
- OHM-Trade-Agent-v1/docker-compose.artifact.yml: digest-pinned overlay with `pull_policy: never`.
- AGENTS.md and CLAUDE.md: smallest change, no production/trading authority, no new infrastructure.
- OHM-Trade-Agent-v1/docs/atdd/README.md: scope control, movable ACTIVE_INCREMENT pointer.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the release receipt contract
WHEN:
a receipt is written, loaded or evaluated for rollback
THEN:
it is atomic, versioned, monotonic, digest-pinned for both services, and reports rollback ineligibility (`BLOCKED_STORAGE`) whenever either image is not registry-attestable, never promoting a baseline to GOOD

AC-002:
GIVEN:
the ohm-deploy controller and the artifact overlay
WHEN:
artifact mode is required on a host without Git metadata
THEN:
admission, forward apply and rollback use digest-pinned images with no checkout, no source rebuild and no implicit pull, the legacy Git path is unchanged when the mode is off, and an unprovable rollback aborts instead of fabricating identity

AC-003:
GIVEN:
host-executed operational inputs
WHEN:
the release bundle is verified and a host script is resolved
THEN:
every allowlisted script and Compose input is checksum-verified, path-traversal and non-allowlisted names are refused, and on-host Compose inputs must match the bundle before use

AC-004:
GIVEN:
the Linux ATDD and rehearsal workflows
WHEN:
they run on a GitHub-hosted Ubuntu runner
THEN:
the artifact contract, the combined base+artifact Compose configuration and the executable artifact-mode transaction (apply, health failure, rollback without rebuild, interruption, stale receipt, missing artifact) are exercised without production access

AC-005:
GIVEN:
the controller bootstrap
WHEN:
the deploy helper is installed
THEN:
`release_artifact.py` lands in its protected location with verified identity and idempotent, rollback-safe installation, version-coupled to the controller, and installing tooling alone does not enable artifact mode

AC-006:
GIVEN:
private GHCR publication
WHEN:
an image is published
THEN:
only a protected, owner-controlled event may publish, `packages: write` is job-scoped and never exposed to pull-request code, images remain private, and no production artifact is published without approval

EXPLICITLY OUT OF SCOPE:
- Production or staging deployment, Git repair on a host, and any runtime configuration change
- Trading, paper, funded, exchange-execution or learning activation of any kind
- Merging the pull request, changing branch protection or rulesets
- Replacing the existing controller, adding a second scheduler, host, droplet, cluster or database
- Promoting the current a26-app/c196-writer combination to GOOD
- Destructive cleanup or data migration

FROZEN BOUNDARIES:
- Forced SSH command, deployment lock, release profile, canonical-writer and F11 independence, evidence-producer quiescence, scheduler snapshot/restore, health and resource gates, runtime verifier, learning exact-release protections and the post-deploy boundary are unchanged.
- Artifact mode defaults to off; the legacy path stays byte-identical.
- No receipt, receipt field or test may assert a verified rollback baseline that was not observed.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_release_artifact_receipt_v1.py::test_registry_verified_pair_is_rollback_eligible
AC-002 -> tests/test_opip_release_artifact_receipt_v1.py::test_controller_rollback_has_no_rebuild_in_artifact_mode
AC-003 -> tests/test_opip_release_artifact_receipt_v1.py::test_onhost_config_drift_is_refused
AC-004 -> tests/test_opip_release_artifact_receipt_v1.py::test_artifact_overlay_pins_images_and_forbids_pull
AC-005 -> tests/test_opip_release_artifact_receipt_v1.py::test_bootstrap_installs_release_helper_without_enabling_artifact_mode
AC-006 -> tests/test_opip_release_artifact_receipt_v1.py::test_ghcr_publication_is_protected_and_private

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-ARTIFACT-MODE-v1.md
AC-001 -> OHM-Trade-Agent-v1/deploy/release/release_artifact.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_release_artifact_receipt_v1.py
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-002 -> OHM-Trade-Agent-v1/docker-compose.artifact.yml
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_release_artifact_receipt_v1.py
AC-003 -> OHM-Trade-Agent-v1/deploy/release/release_artifact.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_release_artifact_receipt_v1.py
AC-004 -> OHM-Trade-Agent-v1/deploy/release/atdd/run-artifact-atdd.sh
AC-004 -> OHM-Trade-Agent-v1/deploy/release/atdd/rehearsal.sh
AC-004 -> .github/workflows/release-artifact-atdd.yml
AC-004 -> .github/workflows/release-artifact-rehearsal.yml
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_release_artifact_receipt_v1.py
AC-005 -> OHM-Trade-Agent-v1/deploy/remote/bootstrap-deploy-controller-only.sh
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_release_artifact_receipt_v1.py
AC-006 -> .github/workflows/release-artifact-publish.yml
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_release_artifact_receipt_v1.py

DEFERRED DISCOVERIES:
- Off-host retention depends on the private GHCR packages this increment wires up; until a real publication and a documented no-prune policy exist, rollback eligibility correctly reports BLOCKED_STORAGE.
- A production artifact-mode deploy additionally needs the operator-gated recovery proof recorded in the closeout report; it is not authorized here.

UNAPPROVED SCOPE CHANGES:
NONE
