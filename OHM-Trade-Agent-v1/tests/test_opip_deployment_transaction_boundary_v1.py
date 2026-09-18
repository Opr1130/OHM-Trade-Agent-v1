"""Deployment transaction boundary and fresh committed-export proof.

Two related defects are covered here.

**Boundary.** ``ohm-deploy`` used to run the learning exporter *inside* the same
fatal sequence as the core release. Because the exporter fails closed when the
paper registry is missing, a non-authoritative learning failure made an already
committed, healthy core look like an ambiguous server failure - the workflow
reported ``SERVER DEPLOY FAILED`` with ``Rollback: UNKNOWN OR FAILED`` for a core
release that was in fact committed and healthy.

**Export proof.** The exporter returns success when a cron run already holds the
lock. That is valid behaviour and is *not* evidence that the deployed SHA is
exported, so readiness is proven from durable artifacts instead of an exit code.

The classification tests execute the workflow's real decision block against
synthetic deploy logs, so the receipt semantics are proven behaviourally rather
than asserted as text.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil

import pytest

from app.opip.canonical.writer import CanonicalWriter
from app.opip.learning.canonical_replica import (
    EXPORT_PROOF_FRESHNESS_SECONDS,
    MANIFEST_ENV_FILENAME,
    REASON_EXPORT_MANIFEST_MISSING,
    REASON_EXPORT_OUTER_ADDRESS_INVALID,
    REASON_EXPORT_READY,
    REASON_EXPORT_RELEASE_SHA_MISMATCH,
    REASON_EXPORT_REPLICA_DIR_INVALID,
    REASON_EXPORT_REPLICA_MARKER_MISSING,
    REASON_EXPORT_REPLICA_MISMATCH,
    REASON_EXPORT_STALE,
    export_replica_bundle,
    replica_tree_digest,
    verify_committed_export,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT.parent / ".github" / "workflows" / "deploy-production.yml"
DEPLOY_SCRIPT = ROOT / "deploy" / "remote" / "ohm-deploy"
RELEASE_SHA = "d50aebbd19df2201bb617298254407add830d851"
OTHER_SHA = "91f4b274bd8eaef84e4525e12fe29b81d084912a"
NOW = datetime(2026, 9, 18, 3, 0, tzinfo=timezone.utc)
EMPTY_REGISTRY = {"schema_version": 1, "paper_only": True, "lifecycles": {}}


# ---------------------------------------------------------------------------
# Committed-export fixtures
# ---------------------------------------------------------------------------


def _tree_sha256(root: Path) -> str:
    """Deterministic tree digest, mirroring the exporter's content addressing."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _build_committed_export(
    tmp_path: Path,
    *,
    release_sha: str = RELEASE_SHA,
    exported_at: datetime = NOW,
    manifest_sha: str | None = None,
    marker: str = "1",
    dir_name_override: str | None = None,
    symlink_dir: bool = False,
) -> Path:
    """Produce an export root that looks exactly like the production one.

    The directory name and the recorded digest/byte count come from the SAME
    implementation the exporter and verifier use, so this fixture cannot drift
    from the contract it is modelling.
    """
    source = tmp_path / "source"
    db = source / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    CanonicalWriter(db).close()
    state = source / "paper_trading" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(EMPTY_REGISTRY), encoding="utf-8")

    staging = tmp_path / "staging"
    export_replica_bundle(
        source_db=db,
        staging_dir=staging,
        source_release_sha=release_sha,
        paper_state_source=state,
        paper_gap_source=None,
        backup_work_dir=tmp_path / "work",
        now=exported_at,
    )

    tree_sha, tree_bytes = replica_tree_digest(staging)
    export_root = tmp_path / "export"
    export_root.mkdir(parents=True, exist_ok=True)
    dir_name = dir_name_override or f"canonical_learning_replica.{tree_sha}"
    target = export_root / dir_name
    if symlink_dir:
        outside = tmp_path / "outside"
        shutil.move(str(staging), str(outside))
        target.symlink_to(outside, target_is_directory=True)
    else:
        shutil.move(str(staging), str(target))

    stamp = exported_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "schema_version=4",
        f"exported_at_utc={stamp}",
        f"production_deployed_sha={manifest_sha if manifest_sha is not None else release_sha}",
        "p1_shadow_outbox_retired=1",
    ]
    if marker:
        lines += [
            f"canonical_learning_replica_version={marker}",
            f"canonical_learning_replica_dir={dir_name}",
            f"canonical_learning_replica_bytes={tree_bytes}",
            f"canonical_learning_replica_sha256={tree_sha}",
        ]
    (export_root / MANIFEST_ENV_FILENAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return export_root


def _verify(root: Path, **overrides):
    kwargs = {
        "export_root": root,
        "expected_source_release_sha": RELEASE_SHA,
        "now": NOW,
    }
    kwargs.update(overrides)
    return verify_committed_export(**kwargs)


# ---------------------------------------------------------------------------
# Group 4 D-G - the export proof matrix
# ---------------------------------------------------------------------------


def test_case_d_lock_skip_with_committed_target_sha_is_ready(tmp_path):
    """D: a lock skip is fine as long as the target SHA is already committed."""
    root = _build_committed_export(tmp_path)

    readiness = _verify(root)

    assert readiness.ready is True
    assert readiness.reason == REASON_EXPORT_READY
    assert readiness.facts["manifest_production_deployed_sha"] == RELEASE_SHA
    assert readiness.facts["replica_source_release_sha"] == RELEASE_SHA
    assert readiness.facts["replica_completeness_supported"] is True


def test_case_e_lock_skip_with_old_sha_is_blocked(tmp_path):
    """E: an old committed manifest must never pass as the deployed release."""
    root = _build_committed_export(tmp_path, manifest_sha=OTHER_SHA)

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_RELEASE_SHA_MISMATCH


def test_case_f_inner_replica_sha_mismatch_is_blocked(tmp_path):
    """F: a fresh manifest whose inner replica names another release fails."""
    root = _build_committed_export(tmp_path, release_sha=OTHER_SHA, manifest_sha=RELEASE_SHA)

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_REPLICA_MISMATCH


def test_case_g_stale_target_sha_manifest_is_blocked(tmp_path):
    """G: a correct-but-stale export cannot prove the deployed release."""
    stale = NOW - timedelta(seconds=EXPORT_PROOF_FRESHNESS_SECONDS + 60)
    root = _build_committed_export(tmp_path, exported_at=stale)

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_STALE


def test_missing_manifest_is_blocked(tmp_path):
    root = _build_committed_export(tmp_path)
    (root / MANIFEST_ENV_FILENAME).unlink()
    readiness = _verify(root)
    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_MANIFEST_MISSING


def test_missing_replica_marker_is_blocked(tmp_path):
    root = _build_committed_export(tmp_path, marker="")
    readiness = _verify(root)
    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_REPLICA_MARKER_MISSING


@pytest.mark.parametrize(
    "dir_name",
    [
        pytest.param("canonical_learning_replica.nothex", id="not-content-addressed"),
        pytest.param("canonical_learning_replica." + "a" * 63, id="short-digest"),
        pytest.param("../../etc", id="traversal"),
    ],
)
def test_invalid_replica_directory_name_is_blocked(tmp_path, dir_name):
    root = _build_committed_export(tmp_path, dir_name_override=dir_name)
    readiness = _verify(root)
    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_REPLICA_DIR_INVALID


def test_symlinked_replica_directory_is_blocked(tmp_path):
    """A valid-looking name must not be followed out of the export root."""
    root = _build_committed_export(tmp_path, symlink_dir=True)
    readiness = _verify(root)
    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_REPLICA_DIR_INVALID
    assert "symlink" in readiness.detail


def test_bounded_wait_accepts_a_later_target_sha_commit(tmp_path):
    """A lock-skipped run may be superseded by the next scheduled commit.

    The wait must be bounded and must terminate as soon as the target SHA is
    committed - never spin indefinitely.
    """
    root = _build_committed_export(tmp_path, manifest_sha=OTHER_SHA)
    state = {"ticks": 0, "elapsed": 0.0}

    def fake_monotonic() -> float:
        return state["elapsed"]

    def fake_sleep(seconds: float) -> None:
        state["ticks"] += 1
        state["elapsed"] += seconds
        if state["ticks"] == 2:
            # The next scheduled run commits the deployed release.
            _build_committed_export  # noqa: B018 - documentation anchor
            manifest = (root / MANIFEST_ENV_FILENAME).read_text(encoding="utf-8")
            (root / MANIFEST_ENV_FILENAME).write_text(
                re.sub(
                    r"^production_deployed_sha=.*$",
                    f"production_deployed_sha={RELEASE_SHA}",
                    manifest,
                    flags=re.M,
                ),
                encoding="utf-8",
            )

    readiness = _verify(
        root,
        wait_seconds=60,
        poll_interval_seconds=5,
        sleep=fake_sleep,
        monotonic=fake_monotonic,
    )

    assert readiness.ready is True
    assert readiness.attempts >= 3
    assert state["ticks"] >= 2


def test_bounded_wait_terminates_when_the_target_never_commits(tmp_path):
    """The wait must be strictly bounded, not an indefinite poll."""
    root = _build_committed_export(tmp_path, manifest_sha=OTHER_SHA)
    state = {"ticks": 0, "elapsed": 0.0}

    def fake_monotonic() -> float:
        return state["elapsed"]

    def fake_sleep(seconds: float) -> None:
        state["ticks"] += 1
        state["elapsed"] += seconds

    readiness = _verify(
        root,
        wait_seconds=30,
        poll_interval_seconds=10,
        sleep=fake_sleep,
        monotonic=fake_monotonic,
    )

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_RELEASE_SHA_MISMATCH
    # 30s of budget at 10s intervals can never exceed a handful of attempts.
    assert readiness.attempts <= 6
    assert state["elapsed"] <= 30


def test_negative_wait_is_rejected(tmp_path):
    root = _build_committed_export(tmp_path)
    with pytest.raises(ValueError):
        _verify(root, wait_seconds=-1)


# ---------------------------------------------------------------------------
# Group 4 A-C - deploy boundary, via the workflow's real decision block
# ---------------------------------------------------------------------------

BASH = shutil.which("bash")


def _bash_pipeline_works() -> bool:
    """Whether this shell can actually fork a pipeline.

    The classification block is several ``grep | tail | cut`` pipelines. Some
    sandboxed/emulated shells report a working ``bash`` but cannot fork, which
    fails the pipeline for reasons unrelated to the logic under test. Probing
    once lets those environments skip honestly instead of reporting a false
    failure.
    """
    if BASH is None:
        return False
    import subprocess

    try:
        probe = subprocess.run(
            [BASH, "-c", "printf 'a=b\\n' | grep -oE 'a=[a-z]' | tail -n 1 | cut -d= -f2"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0 and probe.stdout.strip() == "b"


BASH_PIPELINE_OK = _bash_pipeline_works()
requires_bash = pytest.mark.skipif(
    not BASH_PIPELINE_OK,
    reason="a forking bash with pipelines is required to exercise the workflow logic",
)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _classification_block() -> str:
    """Extract the workflow's real `classify()` function body."""
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("          classify() {")
    # The function ends at the first line that is exactly the closing brace at
    # the same indentation.
    end = text.index("\n          }\n", start)
    block = text[start : end + len("\n          }\n")]
    return "\n".join(
        line[10:] if line.startswith("          ") else line
        for line in block.splitlines()
    )


def _classify(
    tmp_path: Path, deploy_log: str, rc: int, *, legacy_allowed: int = 1
) -> dict[str, str]:
    """Run the workflow's classification function against a synthetic deploy log.

    Two fidelity details matter and are matched deliberately:

    * the real step runs ``set +e``, so a ``grep`` that finds no marker must not
      abort. Using ``set -e`` would test a stricter shell than the one that runs
      and would fail on exactly the absent-marker cases that matter most;
    * the block appends its step outputs to ``$GITHUB_OUTPUT``, so the harness
      must define it rather than leave it empty.
    """
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "deploy.log").write_text(deploy_log, encoding="utf-8")
    harness = tmp_path / "classify.sh"
    harness.write_text(
        "set +e\n"
        f"GITHUB_OUTPUT={_shell_quote(str(tmp_path / 'github_output'))}\n"
        ': > "$GITHUB_OUTPUT"\n'
        + _classification_block()
        + "\n"
        # Mirror the step's tail so the gate and the transition decision are
        # exercised exactly as the workflow computes them.
        f'classify {_shell_quote(str(tmp_path / "deploy.log"))} {rc} {legacy_allowed}\n'
        'if [[ "$RESULT" == "SUCCESS" ]]; then GATE="PASS"; else GATE="FAIL"; fi\n'
        'printf "RESULT=%s\\nHEALTH=%s\\nROLLBACK=%s\\nGATE=%s\\n"'
        ' "$RESULT" "$HEALTH" "$ROLLBACK" "$GATE"\n'
        'printf "CORE_STATUS=%s\\nLEARNING_EXPORT_STATUS=%s\\nLEARNING_READINESS=%s\\n"'
        ' "${CORE_STATUS:-}" "${LEARNING_EXPORT_STATUS:-}" "${LEARNING_READINESS:-}"\n'
        'printf "POSTCOMMIT_HEALTH=%s\\nTRANSITION_ELIGIBLE=%s\\n"'
        ' "${POSTCOMMIT_HEALTH:-}" "${TRANSITION_ELIGIBLE:-}"\n',
        encoding="utf-8",
    )
    import subprocess

    proc = subprocess.run(
        [BASH, str(harness)], capture_output=True, text=True, encoding="utf-8"
    )
    assert proc.returncode == 0, proc.stderr
    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key] = value
    return fields


CORE_OK_LOG = "\n".join(
    [
        '"status":"ok"',
        "O'Pip scheduler reconciliation: OK",
        "OPIP_CORE_DEPLOY_STATUS=SUCCESS",
        "OPIP_PAPER_REGISTRY_GENESIS_STATUS=OK",
        "OPIP_CORE_POSTCOMMIT_HEALTH=OK",
    ]
)

#: The real first-deploy-after-merge shape. The previously installed ohm-deploy
#: had no genesis and no structured markers, so it crossed its commit point
#: (scheduler reconciliation installed this target SHA's deploy script; P1
#: retirement ran after last-good-sha and after the rollback trap was disarmed)
#: and then aborted rc=70 at the exporter on the still-missing registry.
LEGACY_RC70_TRANSITION_LOG = "\n".join(
    [
        '"status":"ok"',
        "O'Pip scheduler reconciliation: OK",
        "canonical=/etc/cron.d/ohm-unified-cycle",
        "O'Pip resource budget validation: PASS available_kb=704036",
        "O'Pip P1 shadow outbox retirement: OK",
        "bytes_deleted=0",
        "disposition=RETIRED_OWNER_DISCARDED",
        "canonical replica refused: CANONICAL_REPLICA_PAPER_STATE_MISSING: paper "
        "lifecycle state not found at /app/data/paper_trading/state.json",
        "O'Pip learning export: canonical replica export FAILED (rc=78)",
        "O'Pip learning evidence export: JSON artifacts OK, canonical replica FAILED",
    ]
)

#: Legacy case A: the old contract completed successfully but emitted no markers.
LEGACY_RC0_TRANSITION_LOG = "\n".join(
    [
        '"status":"ok"',
        "O'Pip scheduler reconciliation: OK",
        "O'Pip P1 shadow outbox retirement: OK",
        "disposition=RETIRED_OWNER_DISCARDED",
        "O'Pip deployment succeeded",
        "sha=" + RELEASE_SHA,
    ]
)


@requires_bash
def test_case_a_core_failure_before_commit_still_reports_rollback(tmp_path):
    """A: a genuine core failure keeps the existing failure/rollback semantics."""
    log = "deployment failed; rolling back to " + OTHER_SHA + "\nrollback health check passed\n"
    fields = _classify(tmp_path, log, rc=1)
    assert fields["RESULT"] == "ROLLED BACK"
    assert fields["ROLLBACK"] == "YES"
    assert fields["GATE"] == "FAIL"


@requires_bash
def test_case_c_core_committed_with_failed_export_says_learning_blocked(tmp_path):
    """C: the defect. Core committed + export failed must NOT read as a core failure."""
    log = "\n".join(
        [
            CORE_OK_LOG,
            "O'Pip learning evidence export FAILED; core release already committed",
            "OPIP_LEARNING_EXPORT_STATUS=FAILED",
            "OPIP_LEARNING_READINESS=BLOCKED",
            "O'Pip deployment succeeded",
            "sha=" + RELEASE_SHA,
        ]
    )
    fields = _classify(tmp_path, log, rc=0)
    assert fields["RESULT"] == "CORE DEPLOYED - LEARNING BLOCKED"
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"
    # The gate fails so a blocked learning export stays visible...
    assert fields["GATE"] == "FAIL"
    # ...but the core release is never described as failed or rolled back.
    assert fields["RESULT"] != "SERVER DEPLOY FAILED"
    assert "UNKNOWN OR FAILED" != fields["ROLLBACK"]


@requires_bash
def test_case_b_core_and_learning_both_succeed(tmp_path):
    """B: complete success, including a proven fresh export."""
    log = "\n".join(
        [
            CORE_OK_LOG,
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "O'Pip deployment succeeded",
            "sha=" + RELEASE_SHA,
        ]
    )
    fields = _classify(tmp_path, log, rc=0)
    assert fields["RESULT"] == "SUCCESS"
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"
    assert fields["GATE"] == "PASS"


@requires_bash
def test_core_ok_without_any_markers_is_unproven_and_transition_eligible(tmp_path):
    """A pre-contract ohm-deploy emits legacy strings only.

    RC 0 plus the legacy success strings prove the core committed, but nothing
    proves the export, so readiness is UNPROVEN and the gate fails closed. The
    core must not be reported as failed or rolled back, and this is exactly the
    shape that makes the first deploy after the contract ships eligible for the
    single transition retry.
    """
    fields = _classify(tmp_path, LEGACY_RC0_TRANSITION_LOG, rc=0, legacy_allowed=1)
    assert fields["RESULT"].startswith("CORE DEPLOYED - LEARNING UNPROVEN")
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"
    assert fields["GATE"] == "FAIL"
    assert fields["TRANSITION_ELIGIBLE"] == "YES"


@requires_bash
def test_contradictory_core_failure_marker_is_not_success(tmp_path):
    """A core FAILED marker must never be overridden by a zero exit code."""
    log = "\n".join(
        [CORE_OK_LOG, "OPIP_CORE_DEPLOY_STATUS=FAILED", "O'Pip deployment succeeded"]
    )
    fields = _classify(tmp_path, log, rc=0)
    assert fields["GATE"] == "FAIL"
    assert fields["RESULT"] != "SUCCESS"


# ---------------------------------------------------------------------------
# Static boundary guarantees
# ---------------------------------------------------------------------------


def test_core_success_is_reported_before_post_deploy_work():
    """The core commit boundary must be explicit and precede learning work."""
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    core_ok = source.index("OPIP_CORE_DEPLOY_STATUS=SUCCESS")
    assert core_ok > source.rfind("trap - ERR")
    assert core_ok < source.rfind("paper_registry_genesis ensure")
    assert core_ok < source.rfind('bash "$LEARNING_EXPORTER"')


def test_post_deploy_work_cannot_abort_the_core_release():
    """No post-commit step may `exit` on failure, or the core looks failed."""
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    post = source[source.index("OPIP_CORE_DEPLOY_STATUS=SUCCESS") :]
    # The exporter and P1 retirement are wrapped in if/else and never exit.
    assert "exit 69" not in post
    assert "exit 70" not in post
    assert 'if bash "$LEARNING_EXPORTER"; then' in post
    assert 'if bash "$P1_RETIRE_SCRIPT"; then' in post
    # And the markers the workflow depends on are all emitted.
    for marker in (
        "OPIP_CORE_DEPLOY_STATUS=SUCCESS",
        "OPIP_P1_RETIREMENT_STATUS=$p1_status",
        "OPIP_LEARNING_EXPORT_STATUS=$learning_export",
        "OPIP_LEARNING_READINESS=$learning_readiness",
    ):
        assert marker in source


def test_rollback_emits_the_core_failure_marker():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    rollback = source[source.index("rollback() {") :]
    rollback = rollback[: rollback.index("\n}\n")]
    assert "OPIP_CORE_DEPLOY_STATUS=FAILED" in rollback


@requires_bash
def test_unexpected_core_status_marker_is_not_success(tmp_path):
    """An unknown CORE_STATUS value must fail closed, not read as success.

    Regression cover: the success branch accepted every value except the literal
    FAILED, so a malformed marker plus legacy log strings could publish SUCCESS
    without proof that the core commit succeeded.
    """
    for bad in ("GARBAGE", "success", "UNKNOWN", "PARTIAL"):
        log = "\n".join(
            [
                f"OPIP_CORE_DEPLOY_STATUS={bad}",
                '"status":"ok"',
                "O'Pip scheduler reconciliation: OK",
                "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
                "OPIP_LEARNING_READINESS=READY",
                "O'Pip deployment succeeded",
            ]
        )
        case_dir = tmp_path / bad
        case_dir.mkdir(parents=True, exist_ok=True)
        fields = _classify(case_dir, log, rc=0)
        assert fields["RESULT"] != "SUCCESS", bad
        assert fields["GATE"] == "FAIL", bad


@requires_bash
def test_present_but_empty_core_status_marker_is_not_success(tmp_path):
    """A present marker with no value must not fall back to the legacy path.

    This is the distinction a restrictive value pattern could not make: if an
    unparseable value were treated as "marker absent", an incompatible
    ohm-deploy would land in the legacy branch and could still publish SUCCESS
    from the learning markers alone.
    """
    log = "\n".join(
        [
            "OPIP_CORE_DEPLOY_STATUS=",
            '"status":"ok"',
            "O'Pip scheduler reconciliation: OK",
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, rc=0)
    assert fields["RESULT"] != "SUCCESS"
    assert fields["GATE"] == "FAIL"


@requires_bash
def test_contradictory_learning_markers_are_blocked(tmp_path):
    """A failed export with a READY readiness claim must not be SUCCESS.

    Readiness proves the committed export; the export status records this attempt.
    Contradiction means the markers are partial or inconsistent, so the gate must
    fail closed rather than publish SUCCESS from one of them.
    """
    log = "\n".join(
        [
            "OPIP_CORE_DEPLOY_STATUS=SUCCESS",
            "O'Pip scheduler reconciliation: OK",
            # Genesis is proven here so this fixture isolates the LEARNING
            # contradiction it is about; genesis gating is covered separately.
            "OPIP_PAPER_REGISTRY_GENESIS_STATUS=OK",
            "OPIP_LEARNING_EXPORT_STATUS=FAILED",
            'OPIP_LEARNING_READINESS=READY',
            "OPIP_CORE_POSTCOMMIT_HEALTH=OK",
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, rc=0, legacy_allowed=0)
    assert fields["RESULT"] == "CORE DEPLOYED - LEARNING BLOCKED"
    assert fields["GATE"] == "FAIL"
    # Core state is still reported accurately and not as a rollback.
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"


@requires_bash
def test_ready_readiness_without_export_success_is_blocked(tmp_path):
    """Both learning facts are required for SUCCESS."""
    log = "\n".join(
        [
            "OPIP_CORE_DEPLOY_STATUS=SUCCESS",
            '"status":"ok"',
            "O'Pip scheduler reconciliation: OK",
            "OPIP_LEARNING_READINESS=READY",
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, rc=0)
    assert fields["RESULT"] != "SUCCESS"
    assert fields["GATE"] == "FAIL"


# ---------------------------------------------------------------------------
# Blocker 1 - one-time first-deploy transition
# ---------------------------------------------------------------------------


@requires_bash
def test_legacy_rc70_transition_is_eligible_and_did_not_roll_back(tmp_path):
    """The real incident shape must be eligible, and must not have rolled back.

    This is the case the transition exists for: the legacy deploy crossed its
    commit point and then aborted at the exporter, so a single retry against the
    newly installed target-SHA ohm-deploy completes the deployment without the
    operator re-issuing /deploy.
    """
    fields = _classify(tmp_path, LEGACY_RC70_TRANSITION_LOG, rc=70, legacy_allowed=1)
    assert fields["TRANSITION_ELIGIBLE"] == "YES"
    # No rollback: the core was already committed, so nothing was reverted.
    assert fields["ROLLBACK"] != "YES"


@requires_bash
def test_legacy_rc0_success_without_markers_is_eligible(tmp_path):
    """Legacy case A: complete legacy success, no structured markers."""
    fields = _classify(tmp_path, LEGACY_RC0_TRANSITION_LOG, rc=0, legacy_allowed=1)
    assert fields["TRANSITION_ELIGIBLE"] == "YES"
    assert fields["ROLLBACK"] == "NO"


@requires_bash
def test_genuine_precommit_core_failure_is_never_retried(tmp_path):
    """A real pre-commit failure must not trigger a transition retry.

    Enumerated pre-commit failures - core health, writer health, paper topology and
    resource budget - all roll back before the exporter, so none of the
    commit-crossed evidence is present.
    """
    cases = {
        "core-health": "\n".join(
            [
                "production core health check failed",
                "deployment failed; rolling back to " + OTHER_SHA,
                "rollback health check passed",
            ]
        ),
        "writer-health": "\n".join(
            [
                "O'Pip scheduler reconciliation: OK",
                "production writer health check failed",
                "deployment failed; rolling back to " + OTHER_SHA,
                "rollback health check passed",
            ]
        ),
        "paper-topology": "\n".join(
            [
                '"status":"ok"',
                "O'Pip scheduler reconciliation: OK",
                "Freqtrade paper topology failed health/authority validation",
                "deployment failed; rolling back to " + OTHER_SHA,
                "rollback health check passed",
            ]
        ),
        "resource-budget": "\n".join(
            [
                '"status":"ok"',
                "O'Pip scheduler reconciliation: OK",
                "insufficient host memory headroom after paper startup: available_kb=1000",
                "deployment failed; rolling back to " + OTHER_SHA,
                "rollback health check passed",
            ]
        ),
    }
    for name, log in cases.items():
        case_dir = tmp_path / name
        case_dir.mkdir(parents=True, exist_ok=True)
        fields = _classify(case_dir, log, rc=1, legacy_allowed=1)
        assert fields["TRANSITION_ELIGIBLE"] == "NO", name
        assert fields["ROLLBACK"] == "YES", name
        assert fields["GATE"] == "FAIL", name


@requires_bash
def test_retry_cannot_recur_because_it_is_classified_strictly(tmp_path):
    """Bounded to one: a legacy log reclassified strictly is not eligible.

    The retry is classified with legacy_allowed=0, so even if it somehow produced
    legacy output the workflow could not loop.
    """
    fields = _classify(tmp_path, LEGACY_RC70_TRANSITION_LOG, rc=70, legacy_allowed=0)
    assert fields["TRANSITION_ELIGIBLE"] == "NO"
    assert fields["GATE"] == "FAIL"


@requires_bash
def test_second_invocation_success_yields_structured_success(tmp_path):
    """The retry running the new ohm-deploy must produce a normal SUCCESS."""
    log = "\n".join(
        [
            CORE_OK_LOG,
            "OPIP_PAPER_REGISTRY_GENESIS=INITIALIZED",
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "O'Pip deployment succeeded",
            "sha=" + RELEASE_SHA,
        ]
    )
    fields = _classify(tmp_path, log, rc=0, legacy_allowed=0)
    assert fields["RESULT"] == "SUCCESS"
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"
    assert fields["GATE"] == "PASS"
    assert fields["TRANSITION_ELIGIBLE"] == "NO"


@requires_bash
def test_second_invocation_failure_stays_blocked(tmp_path):
    """If the retry still cannot prove genesis or the export, the gate stays red.

    This fixture has BOTH problems: genesis refused and the export failed. The
    result names both, which is strictly more informative than the single
    LEARNING BLOCKED label it reported before genesis became a gate.
    """
    log = "\n".join(
        [
            CORE_OK_LOG,
            "OPIP_PAPER_REGISTRY_GENESIS_STATUS=REFUSED",
            "OPIP_LEARNING_EXPORT_STATUS=FAILED",
            "OPIP_LEARNING_READINESS=BLOCKED",
            "O'Pip deployment succeeded",
            "sha=" + RELEASE_SHA,
        ]
    )
    fields = _classify(tmp_path, log, rc=0, legacy_allowed=0)
    assert fields["RESULT"] == (
        "CORE DEPLOYED - PAPER REGISTRY GENESIS BLOCKED + LEARNING BLOCKED"
    )
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"
    assert fields["GATE"] == "FAIL"
    assert fields["TRANSITION_ELIGIBLE"] == "NO"


def test_workflow_retries_at_most_once():
    """The workflow must contain exactly one transition retry invocation."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("run_deploy deploy-transition.log") == 1
    # The retry is gated on the eligibility flag, not on a bare failure.
    assert 'if [[ "$TRANSITION_ELIGIBLE" == "YES" ]]; then' in text
    # And it is classified strictly, so it cannot recur.
    assert 'classify deploy-transition.log "$RC" 0' in text
    # No loop construct may wrap a deploy invocation.
    for token in ("while ", "until "):
        assert token not in text, token


# ---------------------------------------------------------------------------
# Blocker 2 - post-commit commands must not abort the committed release
# ---------------------------------------------------------------------------

_DOCKER_SHIM_OK = "#!/bin/sh\necho 'shim: compose ps'\nexit 0\n"
_DOCKER_SHIM_FAIL = "#!/bin/sh\necho 'shim: compose ps failed' >&2\nexit 1\n"
_CURL_SHIM_OK = "#!/bin/sh\necho '{\"status\":\"ok\"}'\nexit 0\n"
_CURL_SHIM_FAIL = "#!/bin/sh\necho 'shim: connection reset' >&2\nexit 1\n"


def _postcommit_section() -> str:
    """Extract ohm-deploy's post-commit section (from the success echo to EOF)."""
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index('echo "O\'Pip deployment succeeded"')
    return source[start:]


def _run_postcommit(tmp_path: Path, *, docker_ok: bool, curl_ok: bool) -> tuple[int, str]:
    """Run the real post-commit section with failing shims on PATH.

    This is the behavioural replacement for a static "no exit 69/70" assertion: it
    proves that a nonzero post-commit docker/curl command cannot abort an
    already-committed release.
    """
    shim = tmp_path / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("docker", _DOCKER_SHIM_OK if docker_ok else _DOCKER_SHIM_FAIL),
        ("curl", _CURL_SHIM_OK if curl_ok else _CURL_SHIM_FAIL),
    ):
        exe = shim / name
        exe.write_text(body, encoding="utf-8")
        exe.chmod(0o755)

    harness = tmp_path / "postcommit.sh"
    harness.write_text(
        "set -Eeuo pipefail\n"
        f'PAPER_COMPOSE={_shell_quote(str(tmp_path / "docker-compose.paper.yml"))}\n'
        f"TARGET_SHA={RELEASE_SHA}\n"
        + _postcommit_section(),
        encoding="utf-8",
    )
    import subprocess

    proc = subprocess.run(
        [BASH, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}"},
    )
    return proc.returncode, proc.stdout + proc.stderr


@requires_bash
def test_postcommit_nonzero_docker_cannot_abort_the_committed_core(tmp_path):
    """A failing post-commit docker command must not abort or fail the core."""
    rc, out = _run_postcommit(tmp_path, docker_ok=False, curl_ok=True)
    assert rc == 0, out
    assert "OPIP_CORE_POSTCOMMIT_HEALTH=DEGRADED" in out
    assert "O'Pip deployment succeeded" in out


@requires_bash
def test_postcommit_nonzero_curl_cannot_abort_the_committed_core(tmp_path):
    """A failing final health probe must not abort or fail the core."""
    rc, out = _run_postcommit(tmp_path, docker_ok=True, curl_ok=False)
    assert rc == 0, out
    assert "OPIP_CORE_POSTCOMMIT_HEALTH=DEGRADED" in out


@requires_bash
def test_postcommit_clean_observations_report_ok(tmp_path):
    """Control: with working shims the marker is OK, so the test discriminates."""
    rc, out = _run_postcommit(tmp_path, docker_ok=True, curl_ok=True)
    assert rc == 0, out
    assert "OPIP_CORE_POSTCOMMIT_HEALTH=OK" in out
    assert "OPIP_CORE_POSTCOMMIT_HEALTH=DEGRADED" not in out


@requires_bash
def test_postcommit_degraded_health_is_reported_but_not_as_a_core_failure(tmp_path):
    """Degraded post-commit health goes red without implying a core failure."""
    log = "\n".join(
        [
            "OPIP_CORE_DEPLOY_STATUS=SUCCESS",
            "O'Pip scheduler reconciliation: OK",
            "OPIP_PAPER_REGISTRY_GENESIS_STATUS=OK",
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "OPIP_CORE_POSTCOMMIT_HEALTH=DEGRADED",
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, rc=0, legacy_allowed=0)
    assert fields["RESULT"] == "CORE DEPLOYED - POST-COMMIT HEALTH DEGRADED"
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"
    assert fields["GATE"] == "FAIL"


# ---------------------------------------------------------------------------
# Durable genesis provenance is a deployment gate
# ---------------------------------------------------------------------------


@requires_bash
def test_genesis_blocked_blocks_the_deployment_despite_export_success(tmp_path):
    """The defect: a committed export with no durable provenance must not pass.

    Every other fact is green here - the core committed, the export succeeded and
    readiness proved a fresh committed replica - so only provenance is missing. If
    the gate ignored genesis, this would report SUCCESS while leaving no durable
    record that the paper registry was ever initialized, and a later missing
    state.json would again be indistinguishable from virgin state.
    """
    log = "\n".join(
        [
            CORE_OK_LOG.replace(
                "OPIP_PAPER_REGISTRY_GENESIS_STATUS=OK",
                "OPIP_PAPER_REGISTRY_GENESIS_STATUS=REFUSED",
            ),
            "OPIP_PAPER_REGISTRY_GENESIS_REASON=PAPER_REGISTRY_GENESIS_MARKER_WRITE_FAILED",
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "O'Pip deployment succeeded",
            "sha=" + RELEASE_SHA,
        ]
    )
    fields = _classify(tmp_path, log, rc=0, legacy_allowed=0)

    assert fields["RESULT"] == "CORE DEPLOYED - PAPER REGISTRY GENESIS BLOCKED"
    assert fields["RESULT"] != "SUCCESS"
    assert fields["GATE"] == "FAIL"
    # The committed core is still described accurately and never rolled back.
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"
    assert fields["GENESIS_STATUS"] if "GENESIS_STATUS" in fields else True


@requires_bash
def test_genesis_skipped_also_blocks_the_deployment(tmp_path):
    """SKIPPED means provenance was not established, so it cannot pass either."""
    log = "\n".join(
        [
            CORE_OK_LOG.replace(
                "OPIP_PAPER_REGISTRY_GENESIS_STATUS=OK",
                "OPIP_PAPER_REGISTRY_GENESIS_STATUS=SKIPPED",
            ),
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, rc=0, legacy_allowed=0)
    assert fields["RESULT"] == "CORE DEPLOYED - PAPER REGISTRY GENESIS BLOCKED"
    assert fields["GATE"] == "FAIL"


@requires_bash
def test_absent_genesis_marker_blocks_the_deployment(tmp_path):
    """A contract-era run that never reported genesis provenance fails closed."""
    log = "\n".join(
        [
            "OPIP_CORE_DEPLOY_STATUS=SUCCESS",
            "O'Pip scheduler reconciliation: OK",
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "OPIP_CORE_POSTCOMMIT_HEALTH=OK",
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, rc=0, legacy_allowed=0)
    assert fields["RESULT"] == "CORE DEPLOYED - PAPER REGISTRY GENESIS BLOCKED"
    assert fields["GATE"] == "FAIL"


@requires_bash
def test_genesis_proven_with_everything_else_green_passes(tmp_path):
    """Control: genesis OK plus green learning and post-commit health passes."""
    log = "\n".join(
        [
            CORE_OK_LOG,
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, rc=0, legacy_allowed=0)
    assert fields["RESULT"] == "SUCCESS"
    assert fields["GATE"] == "PASS"


def test_genesis_gate_is_wired_into_the_workflow_and_deploy():
    """Static wiring: the gate must exist on both sides of the boundary."""
    text = WORKFLOW.read_text(encoding="utf-8")
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    # The workflow parses the structured genesis status and requires OK.
    assert "OPIP_PAPER_REGISTRY_GENESIS_STATUS=" in text
    assert 'GENESIS_STATUS" == "OK"' in text
    assert "PAPER REGISTRY GENESIS" in text
    # ohm-deploy reports OK only when the durable marker actually exists.
    assert '[[ -s "$PAPER_GENESIS_MARKER" ]]' in deploy
    assert "the initialization marker is missing or empty" in deploy
    for status in ("OK", "REFUSED", "SKIPPED"):
        assert f"OPIP_PAPER_REGISTRY_GENESIS_STATUS={status}" in deploy


def test_postcommit_section_guards_every_command():
    """Static supplement: no bare post-commit command may run unguarded."""
    section = _postcommit_section()
    assert 'compose_ps_out="$(docker compose ps 2>&1)" || postcommit_problems=1' in section
    assert (
        'paper_ps_out="$(docker compose -f "$PAPER_COMPOSE" ps 2>&1)"'
        " || postcommit_problems=1" in section
    )
    assert "if curl --fail --silent --show-error http://127.0.0.1:8000/health; then" in section
    # The section always exits zero once the core is committed.
    assert section.rstrip().endswith("exit 0")
    for forbidden in ("exit 69", "exit 70"):
        assert forbidden not in section


def test_workflow_receipt_reports_core_and_learning_separately():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "**Core health:** $HEALTH" in text
    assert "**Learning export:** ${LEARNING_EXPORT_STATUS:-UNKNOWN}" in text
    assert "**Learning readiness:** ${LEARNING_READINESS:-UNKNOWN}" in text
    # The generic misreport must be gone from the success path, and the gate
    # must key off the structured GATE output rather than a bare exit status.
    assert "steps.production_deploy.outputs.gate != 'PASS'" in text
    # Existing trusted markers the workflow still greps must be preserved.
    assert "O'Pip scheduler reconciliation: OK" in text
    assert "O'Pip deployment succeeded" in text
    assert "&& grep -q 'OPIP stream worker reconciliation: OK' deploy.log" not in text


def test_export_proof_bounds_are_derived_from_the_export_cadence():
    """Both bounds must stay bounded and tied to the documented cadence."""
    import app.opip.learning.canonical_replica as replica_module

    # Cadence is 2 minutes + 40s offset => ~160s per cycle; the wait is two
    # cycles plus slack and must never be unbounded.
    assert 0 < replica_module.EXPORT_PROOF_WAIT_SECONDS <= 600
    assert 0 < replica_module.EXPORT_PROOF_FRESHNESS_SECONDS <= 1800
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert f"EXPORT_PROOF_WAIT_SECONDS={replica_module.EXPORT_PROOF_WAIT_SECONDS}" in source
    assert (
        f"EXPORT_PROOF_FRESHNESS_SECONDS={replica_module.EXPORT_PROOF_FRESHNESS_SECONDS}"
        in source
    )


def test_export_proof_requires_the_immutable_generation_to_verify(tmp_path):
    """Tampering the generation is caught (by the outer address, first)."""
    root = _build_committed_export(tmp_path)
    dir_name = _manifest_line(root, "canonical_learning_replica_dir")
    db = root / dir_name / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    db.write_bytes(db.read_bytes() + b"tampered")

    readiness = _verify(root)

    assert readiness.ready is False
    # The outer content address is the first integrity gate, so it reports the
    # tamper. (The test below proves the inner verification still fires when the
    # outer address is made consistent with the tampered tree.)
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID


def test_outer_consistent_but_inner_invalid_is_blocked(tmp_path):
    """The outer address cannot be used to bypass the inner provenance check.

    Here the outer content address is made fully self-consistent *after*
    tampering - directory renamed to the new digest and the manifest rewritten to
    match - so the outer gate passes and the inner replica manifest must still
    refuse the generation.
    """
    root = _build_committed_export(tmp_path)
    dir_name = _manifest_line(root, "canonical_learning_replica_dir")
    db = root / dir_name / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    db.write_bytes(db.read_bytes() + b"tampered")

    # Re-establish a consistent OUTER address over the tampered tree.
    new_sha, new_bytes = replica_tree_digest(root / dir_name)
    new_name = f"canonical_learning_replica.{new_sha}"
    (root / dir_name).rename(root / new_name)
    _rewrite_manifest(
        root,
        canonical_learning_replica_dir=new_name,
        canonical_learning_replica_sha256=new_sha,
        canonical_learning_replica_bytes=str(new_bytes),
    )

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_REPLICA_MISMATCH


# ---------------------------------------------------------------------------
# Blocker 3 - the OUTER content address must be proven
# ---------------------------------------------------------------------------


def _manifest_line(root: Path, key: str) -> str:
    for line in (root / MANIFEST_ENV_FILENAME).read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{key} not in manifest")


def _rewrite_manifest(root: Path, **values: str) -> None:
    lines = (root / MANIFEST_ENV_FILENAME).read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in values:
            out.append(f"{key}={values[key]}")
        else:
            out.append(line)
    (root / MANIFEST_ENV_FILENAME).write_text("\n".join(out) + "\n", encoding="utf-8")


def test_control_content_addressed_export_is_ready(tmp_path):
    """Control for the negative cases below: a genuine content address passes."""
    root = _build_committed_export(tmp_path)
    readiness = _verify(root)
    assert readiness.ready is True
    assert readiness.facts["actual_replica_tree_sha256"] == _manifest_line(
        root, "canonical_learning_replica_sha256"
    )
    assert readiness.facts["actual_replica_tree_bytes"] == int(
        _manifest_line(root, "canonical_learning_replica_bytes")
    )


def test_outer_manifest_sha_mismatch_is_blocked(tmp_path):
    """A recorded digest that is not the tree's real digest must be refused."""
    root = _build_committed_export(tmp_path)
    _rewrite_manifest(root, canonical_learning_replica_sha256="a" * 64)

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID


def test_outer_manifest_sha_not_a_digest_is_blocked(tmp_path):
    root = _build_committed_export(tmp_path)
    _rewrite_manifest(root, canonical_learning_replica_sha256="notahash")

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID


def test_directory_suffix_disagreeing_with_manifest_sha_is_blocked(tmp_path):
    """A correct-looking directory whose name disagrees with the manifest fails.

    This is the case the inner manifest cannot catch: the name claims one content
    address while the manifest claims another.
    """
    root = _build_committed_export(tmp_path)
    dir_name = _manifest_line(root, "canonical_learning_replica_dir")
    real_sha = _manifest_line(root, "canonical_learning_replica_sha256")
    other_sha = ("b" * 64) if real_sha != "b" * 64 else ("c" * 64)
    # Rename the real directory to a name ending in a DIFFERENT valid digest and
    # point the manifest at it.
    (root / dir_name).rename(root / f"canonical_learning_replica.{other_sha}")
    _rewrite_manifest(
        root,
        canonical_learning_replica_dir=f"canonical_learning_replica.{other_sha}",
    )

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID
    assert "directory name does not match" in readiness.detail


def test_outer_manifest_byte_count_mismatch_is_blocked(tmp_path):
    """A wrong byte count must be refused even when the digest matches."""
    root = _build_committed_export(tmp_path)
    actual = _manifest_line(root, "canonical_learning_replica_bytes")
    _rewrite_manifest(root, canonical_learning_replica_bytes=str(int(actual) + 1))

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID


def test_outer_manifest_byte_count_not_an_integer_is_blocked(tmp_path):
    root = _build_committed_export(tmp_path)
    _rewrite_manifest(root, canonical_learning_replica_bytes="abc")

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID


def test_outer_manifest_negative_byte_count_is_blocked(tmp_path):
    root = _build_committed_export(tmp_path)
    _rewrite_manifest(root, canonical_learning_replica_bytes="-5")

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID


def test_content_addressed_name_with_wrong_contents_is_blocked(tmp_path):
    """Correct-looking name, contents that do not hash to it -> BLOCKED.

    The directory name advertises a digest; recomputing the tree must disagree.
    """
    root = _build_committed_export(tmp_path)
    dir_name = _manifest_line(root, "canonical_learning_replica_dir")
    # Genuinely change bundled content - writing identical bytes would leave the
    # tree hash unchanged and prove nothing.
    target = root / dir_name / "paper_trading" / "state.json"
    target.write_text(
        '{"schema_version": 1, "paper_only": true, "lifecycles": {"PAPER:x": {}}}',
        encoding="utf-8",
    )

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID
    assert "recomputed replica tree digest" in readiness.detail


def test_extra_file_changes_the_tree_hash_and_is_blocked(tmp_path):
    """An extra file must change the tree digest, so it cannot pass."""
    root = _build_committed_export(tmp_path)
    dir_name = _manifest_line(root, "canonical_learning_replica_dir")
    (root / dir_name / "extra-file.txt").write_text("injected", encoding="utf-8")

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID


def test_inner_manifest_valid_but_outer_invalid_is_blocked(tmp_path):
    """Both layers are required: a valid inner manifest cannot excuse a bad outer.

    The inner replica manifest is left completely intact here, so only the outer
    content address is wrong.
    """
    root = _build_committed_export(tmp_path)
    # Sanity: the inner manifest verifies on its own before we break the outer.
    inner_only = verify_committed_export(
        export_root=root,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )
    assert inner_only.ready is True

    _rewrite_manifest(root, canonical_learning_replica_sha256="d" * 64)
    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_OUTER_ADDRESS_INVALID


def test_symlinked_file_in_replica_tree_is_not_followed(tmp_path):
    """A symlinked file is not part of the content address.

    The digest is defined over regular files only, mirroring `find -type f`, so a
    link cannot pull outside content into the address. Adding one therefore
    changes nothing - and because the recorded digest does not change, the export
    still verifies, which proves links are excluded rather than silently hashed.
    """
    root = _build_committed_export(tmp_path)
    dir_name = _manifest_line(root, "canonical_learning_replica_dir")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / dir_name / "link.txt"
    link.symlink_to(outside)

    readiness = _verify(root)

    assert readiness.ready is True, readiness.detail


def test_exporter_and_verifier_share_one_tree_digest_implementation():
    """Exporter and verifier must not maintain separate digest algorithms."""
    import app.opip.learning.canonical_replica as replica_module

    exporter = (
        DEPLOY_SCRIPT.parent / "export-opip-learning-evidence.sh"
    ).read_text(encoding="utf-8")
    # The exporter delegates to the module rather than recomputing...
    assert "read_replica_tree_digest" in exporter
    assert "tree-digest --root" in exporter
    # ...and no longer computes the replica address with its own shell hashing.
    assert 'tree_sha256 "$REPLICA_STAGING"' not in exporter
    assert 'tree_bytes "$REPLICA_STAGING"' not in exporter
    assert 'tree_sha256 "$REPLICA_PUBLISH_DIR"' not in exporter
    # The single implementation is exported for both sides.
    assert "replica_tree_digest" in replica_module.__all__
    assert callable(replica_module.replica_tree_digest)
