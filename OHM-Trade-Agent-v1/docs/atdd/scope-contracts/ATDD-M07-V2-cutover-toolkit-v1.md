INCREMENT:
ATDD-M07-V2-cutover-toolkit-v1

OWNER-APPROVED INTENT:
Make the Mission 07 V1 to V2 cutover toolkit an approved, reviewable
release-engineering increment. The toolkit is inert: it verifies a release,
promotes a canonical database, bootstraps or rolls back a host, and records
provenance only when an authorised operator runs it on that host. This increment
authorises the toolkit under `OHM-Trade-Agent-v1/deploy/m07`, one read-only
Linux workflow that proves what the authoring workstation could not (Linux bash
syntax, a real Compose render, the recovery and rollback contracts and the full
offline suite), and one application-test correction: the export lock-ownership
regression must judge a genuinely young holder process instead of the
long-lived pytest process. It changes no production behaviour, grants no trading
authority, activates no writer, scheduler or release profile, and authorises no
deployment. The owner approves this intent by activating the increment in
`docs/atdd/ACTIVE_INCREMENT`; until that act this contract is a proposal and
authorises nothing.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `Scope and authority`: the attached
  package is documentation authority, and funded trading, deployment,
  TARGET_PAPER activation, production configuration and live account actions are
  not authorised by it. This increment adds no such action.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `Runtime authority`: no AI vote,
  narrative or wrapper changes admission, exit, size, stops, risk or
  probability. The toolkit holds no runtime authority and adds no caller.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `Evidence position`: verified
  exports and existing learning capacity only, with no analytics database on the
  trading host, no extra scheduler and no competing deployment. The toolkit adds
  no scheduler and no competing deployment path.
- `docs/PAPER_TRADING_V1.md`: the paper and dry-run planes stay isolated from
  production authority; nothing here connects to them.
- `OHM_P1_PROMOTION_GATES.md`: shadow and evidence paths cannot promote policy
  or gain execution authority.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `Release profiles`: preserve
  SAFE_BASELINE, EVIDENCE_SHADOW and gated TARGET_PAPER, with no hidden
  environment-variable activation and no new profile. This increment adds none
  and the toolkit selects no profile.
- `deploy/remote/ohm-deploy`: the certified controller whose Compose invocation
  and host layout the toolkit must mirror.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the certified controller `deploy/remote/ohm-deploy`, the certified
`docker-compose.yml`, and the toolkit host scripts in one release tree
WHEN:
the bootstrap or rollback renders or starts a Compose project
THEN:
Compose is invoked from `$APP_ROOT` exactly as the controller invokes it, no
`-f` overlay is layered on the core stack by the bootstrap, no owner-staged
`/tmp/opip-v2` input or generated nginx override exists, and every rollback
overlay keeps `docker-compose.yml` as its first file
AC-002:
GIVEN:
a host that has not yet activated the V2 writer or scheduler
WHEN:
the bootstrap prepares, authorises or executes the install
THEN:
neither the writer nor the scheduler starts before a verified recovery receipt
and its writer gate, `activate-writer` and `activate-scheduler` are the only
activation modes, scheduler reconciliation requires live HTTPS and Telegram
acceptance evidence, the legacy paper service is refused, and nothing is cloned
from a remote
AC-003:
GIVEN:
a host running V1 with a WAL-mode canonical database
WHEN:
a data rollback or safe-baseline rollback is prepared and executed
THEN:
the scheduler is frozen first, the canonical writer is then quiesced and proved
stopped before the authoritative snapshot is taken, no competing writer can
advance canonical history during the protected transition, a WAL-consistent
snapshot is preserved with integrity and foreign-key checks passing, the
authoritative post-quiescence history is compared with the approved plan and any
newer epoch, sequence or event count or inconsistent history is refused before
any code or profile switch, a pre-quiescence evidence snapshot may be retained
but never substitutes for that validation, remaining services stop only after
the comparison passes, a raw `cp` is never used as a snapshot, preserved
evidence stays recoverable, and scheduler activation remains separately gated
AC-004:
GIVEN:
the reviewed toolkit on a Linux runner
WHEN:
the dedicated workflow executes
THEN:
it runs with read-only repository permission, checks the delivered bytes are
LF-only, runs Linux-native `bash -n` on both host scripts, renders the certified
Compose file and asserts exactly its four services with no overlay, runs the
recovery and rollback safety contracts and the full offline suite, and it
starts no container, contacts no host and holds no write token
AC-005:
GIVEN:
a checkout on a machine other than the authoring workstation, and an export
lock held by a real kernel flock
WHEN:
the offline suite builds its fixture and the lock-ownership regression observes
the holder
THEN:
the certified tree is resolved from the tree or `OPIP_CERTIFIED_SRC` instead of
a workstation-absolute path, the observed holder PID and ownership belong to a
freshly started child process whose kernel-reported age is below the unchanged
300-second threshold, the verdict is not a stall, and the truth-table coverage
for genuinely aged, unconfirmed and stalled holders is preserved
AC-006:
GIVEN:
the committed Mission 07 evidence set
WHEN:
a reviewer reads the release authority chain
THEN:
the R3 set remains the authority in its declared order, the earlier R1/R2
documents remain present and labelled historical, and the final verdict states
engineering qualification PASS, production readiness HOLD and authorised
deployment NO
AC-007:
GIVEN:
a certified Docker image with an immutable image ID, an authorized bootstrap
receipt, and the certified Compose configuration whose services declare
`build: .` without an explicit `image`
WHEN:
the core or canonical writer is started or recreated
THEN:
the expected image is independently verified and its identity is bound to the
authorization evidence, the effective Compose operation cannot silently build
or select another image, the actual running container image ID equals its
authorized identity, mismatch, missing image, tag drift and stale project images
fail closed before or after startup, no successful activation receipt is issued
for a mismatched image, the writer and scheduler remain unauthorized after
failure, and no second Compose configuration source or unapproved overlay is
introduced
AC-008:
GIVEN:
a V1 source and independent host-identity evidence
WHEN:
an authorized export is requested through the PowerShell wrapper and the Python
export CLI
THEN:
the source identity is validated against trusted host evidence including host
and droplet identity, approved IP where applicable, source generation and
evidence freshness, the host-evidence classification cannot be supplied without
that verification, the verified evidence identity and digest are bound to the
export manifest, default or operator-declared values alone cannot establish
authenticity, missing forged stale or inconsistent evidence is refused, a
successful export verifies and reconciles against its manifest, and
synthetic/fixture provenance stays strictly isolated from real export
authorization
AC-009:
GIVEN:
a certified release installed into the runtime root without `.git`
WHEN:
PostConfiguration readiness is performed
THEN:
the installed release is validated against the actual certified release
manifest with hashes recomputed from installed contents, the certified SHA is
established through trustworthy installation evidence, the check uses exactly
the installer-approved file exclusions, missing corrupted unexpected or altered
source files are refused, runtime data and `.env` follow their distinct
contracts with `.env` contents never printed or recorded, readiness does not
require `.git`, and success demonstrates actual installed-release integrity
AC-010:
GIVEN:
a candidate M07 toolkit and its provisioning source
WHEN:
provisioning is requested
THEN:
the source toolkit is bound to an explicit reviewed Git commit or another
independently trusted source attestation, the source bytes match that recorded
identity, the source tree is complete with no unauthorized changes, a locally
generated manifest alone is not accepted as trusted origin, manifest and
file-integrity checks remain mandatory, destination canonicalization and
destructive-path protections remain mandatory, previously verified installations
survive failed replacement, and no source or destination symlink or overlap can
bypass the containment rules

EXPLICITLY OUT OF SCOPE:
- No production or host deployment, SSH, DNS, TLS, firewall, scheduler
  installation, Telegram delivery or live database migration.
- No writer activation, canonical promotion, restore rehearsal or rollback
  execution on any host.
- No trading, exchange, order, sizing, risk-threshold or release-profile change.
- No application runtime, route, dependency or configuration change.
- No Phase 5E V2 resource overlay.
- No change to `tests/atdd_scope.py`, its required sections, or any required CI
  gate.
- No secret creation, read, rotation or disclosure.

FROZEN BOUNDARIES:
- `deploy/remote/ohm-deploy` and `docker-compose.yml` are unchanged; the toolkit
  mirrors the controller and never layers an overlay on the core stack.
- `.github/workflows/pytest.yml` is unchanged and byte-identical to `main`; the
  required gate set and its contract tests stay intact.
- The 300-second export stall threshold and the 24-hour receipt freshness rule
  are unchanged.
- The toolkit starts no writer and no scheduler; activation remains
  receipt-gated and operator-invoked.
- Earlier ATDD contracts and the R1/R2 evidence documents are preserved; where
  they contradict R3, R3 wins.
- Activation is the owner's act: moving `docs/atdd/ACTIVE_INCREMENT` is not part
  of the implementation of this contract.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_001_compose_is_invoked_exactly_as_the_certified_controller
AC-002 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_002_writer_and_scheduler_activation_are_receipt_gated
AC-003 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_003_rollback_freezes_then_quiesces_then_snapshots_then_compares
AC-004 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_004_linux_ci_qualifies_shell_compose_and_the_offline_suite_read_only
AC-005 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_005_certified_tree_is_portable_and_the_lock_holder_is_genuinely_young
AC-006 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_006_the_r3_evidence_chain_stays_explicitly_non_deployable
AC-007 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_007_runtime_image_identity_is_bound_and_verified
AC-008 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_008_export_requires_verified_host_evidence
AC-009 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_009_readiness_uses_the_installer_manifest_without_git
AC-010 -> tests/test_atdd_m07_cutover_toolkit_v1.py::test_ac_010_toolkit_source_origin_is_independently_verified

IMPLEMENTATION MAP:
AC-004 -> .github/workflows/m07-cutover-toolkit.yml
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/.gitattributes
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/.gitignore
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/00-HF-STATE.json
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/00-R1-STATE.json
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/00-R2-STATE.json
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/01-DEEP-AUDIT-MATRIX.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/01-ROOT-CAUSE-MATRIX.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/02-CHANGED-FILES.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/02-REQUIREMENTS-TRACEABILITY.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/03-FIX-INVENTORY.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/03-REVISED-DEPLOYMENT-PROCEDURE.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/04-NEGATIVE-TESTS.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/04-REVISED-MIGRATION-PROCEDURE.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/05-EXECUTION-LOGS.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/05-REVISED-ROLLBACK-PROCEDURE.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/06-SINGLE-CUTOVER-RUNBOOK.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/06-TARGETED-TEST-RESULTS.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/07-NEGATIVE-PATH-REVIEW.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/07-WAL-SAFE-ROLLBACK.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/08-OWNER-GATES.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/08-R1-FINAL-REPORT.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/09-HF-CHANGED-FILES.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/09-INDEPENDENT-REVIEW.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/10-FINAL-VERDICT.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/10-HF-DEFECT-EVIDENCE.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/11-HF-CUTOVER-PROCEDURE.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/12-HF-LIVE-PREREQUISITES.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/13-HF-INDEPENDENT-REVIEW.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/14-HF-CERTIFICATION-MATRIX.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/15-HF-FINAL-REPORT.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/R3-ARCHITECTURE-TRACEABILITY.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/R3-FINAL-VERDICT.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/R3-GIT-PR-READINESS.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/R3-OWNER-GATES.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/R3-RELEASE-QUALIFICATION.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/R3-SECURITY-REVIEW.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/R3-SOURCE-RECONCILIATION.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/R3-TEST-RESULTS.csv
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/README.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/SOURCE-PIN.json
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/TEST-COMMANDS.md
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/drafts/docker-compose.v2.yml
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/drafts/environment-template.example
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/drafts/nginx-v2.conf
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/drafts/v1-dataset-spec.json
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-OpipDataMigration.ps1
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2Bootstrap.ps1
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2DashboardValidation.ps1
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2Readiness.ps1
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2Rollback.ps1
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2TelegramValidation.ps1
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/lib/OpipR1.Common.ps1
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_archive.py
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_bundle.py
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_layout.py
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_provenance.py
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_receipts.py
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_recovery.py
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_rollback_guard.py
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_rollback_guard.py
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_signals.py
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/telegram_tracked_acceptance.py
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/scripts/v2/rollback-v2.sh
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/v2/rollback-v2.sh
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/conftest.py
AC-005 -> OHM-Trade-Agent-v1/deploy/m07/tests/conftest.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/make_fixture.py
AC-005 -> OHM-Trade-Agent-v1/deploy/m07/tests/make_fixture.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_hf01_archive_safety.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_hf02_recovery_gate.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_hf03_hf04_host_scripts.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_hf06_provenance.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r1_bundle.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r1_operations.py
AC-005 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r1_operations.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r2_audit_fixes.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r3_concurrency_and_compose.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r3_controller_parity.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-M07-V2-cutover-toolkit-v1.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_cutover_toolkit_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_export_diagnostics_readonly_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-007 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_image.py
AC-007 -> OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh
AC-008 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_bundle.py
AC-008 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-OpipDataMigration.ps1
AC-009 -> OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh
AC-009 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2Readiness.ps1
AC-010 -> OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh

DEFERRED DISCOVERIES:
- Linux-native host execution of the scripts (restore, rollback, writer
  activation) is not proven by CI; only bash syntax, a Compose render and
  offline behaviour are proven there.
- Owner gates L-01 to L-10 remain open, including the read-only host probe, the
  final V1 export and one native restore rehearsal.
- The Phase 5E V2 resource overlay is not in this increment, so a V2 host would
  run V1 limits until it is merged.
- The writer gate proves equality with the live database, not authorship of the
  receipt; no signing scheme exists.
- Aging a real process beyond 300 seconds is impractical in CI; the
  stalled-holder classification remains proven by the seam truth table rather
  than by a deliberately old process.

UNAPPROVED SCOPE CHANGES:
NONE
