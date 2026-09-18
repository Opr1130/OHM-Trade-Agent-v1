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
    REASON_EXPORT_READY,
    REASON_EXPORT_RELEASE_SHA_MISMATCH,
    REASON_EXPORT_REPLICA_DIR_INVALID,
    REASON_EXPORT_REPLICA_MARKER_MISSING,
    REASON_EXPORT_REPLICA_MISMATCH,
    REASON_EXPORT_STALE,
    export_replica_bundle,
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
    """Produce an export root that looks exactly like the production one."""
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

    export_root = tmp_path / "export"
    export_root.mkdir(parents=True, exist_ok=True)
    dir_name = dir_name_override or f"canonical_learning_replica.{_tree_sha256(staging)}"
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
            "canonical_learning_replica_bytes=1024",
            f"canonical_learning_replica_sha256={dir_name.rsplit('.', 1)[-1]}",
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
    """Extract the workflow's real deploy-classification shell block."""
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index('          CORE_STATUS="$(grep -oE')
    end = text.index('          exit 0', start)
    block = text[start:end]
    return "\n".join(
        line[10:] if line.startswith("          ") else line
        for line in block.splitlines()
    )


def _classify(tmp_path: Path, deploy_log: str, rc: int) -> dict[str, str]:
    """Run the workflow's classification logic against a synthetic deploy log.

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
        f"RC={rc}\n"
        f"GITHUB_OUTPUT={_shell_quote(str(tmp_path / 'github_output'))}\n"
        ": > \"$GITHUB_OUTPUT\"\n"
        "cd " + _shell_quote(str(tmp_path)) + "\n"
        + _classification_block()
        + '\nprintf "RESULT=%s\\nHEALTH=%s\\nROLLBACK=%s\\nGATE=%s\\n"'
        ' "$RESULT" "$HEALTH" "$ROLLBACK" "$GATE"\n'
        # Echo the step outputs the workflow would publish.
        'grep -E "^(gate|core_status|learning_export_status|learning_readiness)=" '
        '"$GITHUB_OUTPUT" || true\n',
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
def test_core_ok_without_any_markers_is_reported_unproven(tmp_path):
    """A pre-contract ohm-deploy emits legacy strings only.

    RC 0 plus the legacy success strings prove the core committed, but nothing
    proves the export, so readiness is UNPROVEN and the gate fails closed. The
    core must not be reported as failed or rolled back.
    """
    log = "\n".join(
        [
            '"status":"ok"',
            "O'Pip scheduler reconciliation: OK",
            "O'Pip deployment succeeded",
            "sha=" + RELEASE_SHA,
        ]
    )
    fields = _classify(tmp_path, log, rc=0)
    assert fields["RESULT"] == "CORE DEPLOYED - LEARNING UNPROVEN"
    assert fields["HEALTH"] == "OK"
    assert fields["ROLLBACK"] == "NO"
    assert fields["GATE"] == "FAIL"


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
            '"status":"ok"',
            "O'Pip scheduler reconciliation: OK",
            "OPIP_LEARNING_EXPORT_STATUS=FAILED",
            'OPIP_LEARNING_READINESS=READY',
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, rc=0)
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
    """The proof must inspect the generation, not just the manifest text."""
    root = _build_committed_export(tmp_path)
    # Tamper with the referenced generation's database: hash validation must fail.
    dir_name = [
        line.split("=", 1)[1]
        for line in (root / MANIFEST_ENV_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.startswith("canonical_learning_replica_dir=")
    ][0]
    db = root / dir_name / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    db.write_bytes(db.read_bytes() + b"tampered")

    readiness = _verify(root)

    assert readiness.ready is False
    assert readiness.reason == REASON_EXPORT_REPLICA_MISMATCH
