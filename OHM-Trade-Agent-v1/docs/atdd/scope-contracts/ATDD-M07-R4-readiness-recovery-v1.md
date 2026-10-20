INCREMENT:
ATDD-M07-R4-readiness-recovery-v1

OWNER-APPROVED INTENT:
Close the specific readiness blockers found after PR #429 with one bounded
corrective change: make the L-01 release probe use the already approved
non-interactive privileged read, add an independent L-02 provenance equivalence
check for a deliberately Git-free release extraction, keep remote POSIX paths
correct when the bootstrap wrapper runs on Windows, qualify R3-G2 on a disposable
Linux/Docker runner with explicitly simulated host metadata, and diagnose the
previous native-restore sequence failure against the certified contract. The
increment adds offline tooling, tests and a disposable CI rehearsal only. It
grants no trading authority, changes no application runtime behaviour, contacts
no production host, installs nothing on V2, and authorises no deployment.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `Scope and authority`: funded
  trading, deployment, TARGET_PAPER activation, production configuration and live
  account actions are not authorised. This increment adds none.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `Runtime authority`: no AI or
  wrapper input changes admission, exit, size, stops, risk or probability.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `Evidence position`: verified
  exports and existing capacity only; no analytics database on the trading host
  and no competing deployment path.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `Release profiles`: SAFE_BASELINE,
  EVIDENCE_SHADOW and gated TARGET_PAPER remain the only profiles; no hidden
  environment activation is introduced.
- `deploy/remote/ohm-deploy`: the certified controller whose release layout,
  Compose invocation and image identity the toolkit mirrors.
- `deploy/learning/README.md` `Release compatibility (exact SHA)`: a future core
  cutover still requires a matching owner-gated `/deploy-learning`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the V2 release parent `/srv/opip-v2/releases` is mode 0700 root and the approved
account is unprivileged `opipadmin`
WHEN:
`Invoke-V2Readiness.ps1 -Phase PreBootstrap -Execute -Authorized` checks that the
certified release exists
THEN:
the check uses the existing non-interactive `sudo -n` mechanism, a present and
readable release is PASS, an absent directory and an unreadable directory are
explicit failures, a sudo refusal or a failed remote command is INSUFFICIENT and
can never become PASS, `BatchMode=yes` and `StrictHostKeyChecking=yes` are
unchanged, host identity is still verified against the real metadata id, and no
other successful readiness check is altered
AC-002:
GIVEN:
a release tree installed from the pinned commit without `.git` and without
`release-manifest.sha256`
WHEN:
release provenance is established
THEN:
an expected inventory is built only from a trusted checkout or approved archive
of the pinned commit, the installer's exact inclusion/exclusion policy is
applied, the certified SHA, manifest digest, file count and expected-manifest
provenance are recorded, the actual inventory is recomputed and never trusted by
itself, the full file inventory is compared so missing, added and changed files
are all detected, permitted runtime-only files are distinguished from source
files, a candidate-generated inventory can never authenticate itself, and no
`.git`, manifest or other artifact is written onto the release
AC-003:
GIVEN:
the bootstrap wrapper runs on a Windows operator host while the remote host is
POSIX
WHEN:
it prints the authorised remote sequence for a toolkit root that contains spaces
or a trailing separator
THEN:
the remote toolkit path is a POSIX path with no Windows separators, arguments are
quoted so spaces and embedded single quotes survive `sh` parsing, the wrapper
still never opens a remote shell, no SSH client is invoked by it, and the
existing refusal and receipt behaviour is unchanged
AC-004:
GIVEN:
a disposable GitHub-hosted Ubuntu runner with Docker and no production access
WHEN:
the dedicated R3-G2 job runs the toolkit
THEN:
it builds and identifies the certified image from the pinned commit, provisions
the toolkit from the reviewed commit, prepares, authorises, executes core-only,
performs PostConfiguration checks, exports and verifies a synthetic bundle,
restores the synthetic canonical generation with the real Linux code, proves the
writer gate and starts exactly one writer, refuses a second writer, refuses the
scheduler before HTTPS and Telegram acceptance, takes a WAL-safe snapshot and
compares history, preserves tampered and incomplete evidence, and marks host
identity as SIMULATED while production identity remains UNPROVEN
AC-005:
GIVEN:
a certified canonical generation and a live store
WHEN:
a native restore or a history-epoch advance is performed
THEN:
the new epoch dominates both the snapshot and the replaced live epoch,
`next_local_sequence` resets to 1, existing event coordinates and event count are
preserved, integrity is `ok`, foreign-key violations are 0, a writer gate accepts
only a completed PASS receipt bound to that destination, repeated recovery
advances the epoch again, failed recovery leaves the live store byte-identical,
incomplete generations and sidecar-bearing generations are refused, and an
equal-or-newer live epoch is refused rather than overwritten
AC-006:
GIVEN:
the merged M07 contract and its evidence set
WHEN:
this corrective increment is reviewed
THEN:
the change is bounded to the readiness probe, provenance tooling, POSIX path
handling, disposable rehearsal and restore diagnosis, every changed file maps to
an approved criterion, the previous M07 contract and its acceptance tests remain
intact, no application runtime, profile, risk, execution or deployment control is
changed, and the increment records what remains unproven or owner-gated

EXPLICITLY OUT OF SCOPE:
- No production SSH, no second L-01 live probe, no host credential or `.env`
  read, and no change to the production droplet identity gate.
- No V1 power-on, V1 database access or final V1 export.
- No V2 host change, toolkit installation, `.env` placement, container start,
  restore, rollback, writer or scheduler activation.
- No cloud provisioning, billing change or paid runner.
- No exchange, Telegram, paper or Freqtrade production activity.
- No application runtime, route, dependency, release-profile, risk, sizing or
  execution change.
- No merge and no direct change to `main`.
- No weakening of restore assertions or of the existing owner gates to obtain a
  PASS.

FROZEN BOUNDARIES:
- `bootstrap-v2.sh` `DROPLET_ID`, `verify_identity` and `verify_source` remain
  unchanged; the rehearsal simulates metadata only in an isolated test boundary.
- `restore_from_backup` refusal semantics and `advance_history_epoch_for_restore`
  (`next_local_sequence = 1`, epoch dominance) are unchanged.
- Fixed risk sizing, minimum R:R, release profiles and execution controls are
  untouched.
- `.github/workflows/pytest.yml` is unchanged; required gates keep their
  contracts.
- The completed `ATDD-M07-V2-cutover-toolkit-v1` contract and its tests are
  preserved.
- Activation is the owner's act: moving `docs/atdd/ACTIVE_INCREMENT` is part of
  this increment because the owner explicitly approved this corrective ATM
  increment.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_atdd_m07_r4_readiness_recovery_v1.py::test_ac_001_readiness_reads_the_release_through_the_approved_sudo_path
AC-002 -> tests/test_atdd_m07_r4_readiness_recovery_v1.py::test_ac_002_release_provenance_is_grounded_in_trusted_evidence
AC-003 -> tests/test_atdd_m07_r4_readiness_recovery_v1.py::test_ac_003_remote_paths_stay_posix_and_the_wrapper_never_runs_ssh
AC-004 -> tests/test_atdd_m07_r4_readiness_recovery_v1.py::test_ac_004_the_rehearsal_is_disposable_and_labels_simulated_identity
AC-005 -> tests/test_atdd_m07_r4_readiness_recovery_v1.py::test_ac_005_the_restore_contract_resets_the_sequence_and_preserves_events
AC-006 -> tests/test_atdd_m07_r4_readiness_recovery_v1.py::test_ac_006_the_correction_is_bounded_and_leaves_the_m07_contract_intact

IMPLEMENTATION MAP:
AC-004 -> .github/workflows/m07-cutover-toolkit.yml
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/L2-RELEASE-PROVENANCE.md
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2Bootstrap.ps1
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2Bootstrap.ps1
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/scripts/Invoke-V2Readiness.ps1
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/lib/OpipR1.Common.ps1
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/scripts/lib/OpipR1.Common.ps1
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_release_provenance.py
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r4_readiness_probe.py
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r4_posix_paths.py
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r4_release_provenance.py
AC-005 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r4_restore_semantics.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-M07-R4-readiness-recovery-v1.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_r4_readiness_recovery_v1.py

DEFERRED DISCOVERIES:
- The disposable R3-G2 job is the first Linux execution of the host scripts
  beyond syntax and Compose render. If a step cannot complete, the evidence must
  report the executed subset and keep R3-G2 UNPROVEN rather than claim a PASS.
- The rehearsal identity is SIMULATED by an isolated IMDS stub; the production
  droplet identity remains UNPROVEN by this increment.
- No L-01 or L-02 execution against V2 is authorised here; the corrected probe
  and the provenance comparison require a fresh owner authorisation.
- The Phase 5E V2 resource overlay remains outside this increment.
- The prior PHASE4B ad-hoc script was not preserved, so the restore diagnosis is
  bounded by the recorded failure line and the certified code contract.
- Authorization requires a READY protection preflight. A host without private
  exchange credentials is BLOCKED and the toolkit correctly refuses to authorize
  it; the full prepare→authorize→execute chain therefore requires the real
  production `.env` (L-04). Synthesizing exchange credentials is prohibited, so
  execute, core start, writer activation, scheduler activation and a full
  rollback remain UNPROVEN in the disposable rehearsal.
- `preflight_evaluate` reads `verdict.reason_codes`, while the preflight receipt
  carries its blocking reason codes under `evidence_shadow.blocking_reason_codes`
  and `protection.reason_codes`. The current empty-reason result is fail-closed
  (a blocked host is never acknowledged), so it is recorded as an observation,
  not changed here.

UNAPPROVED SCOPE CHANGES:
NONE

