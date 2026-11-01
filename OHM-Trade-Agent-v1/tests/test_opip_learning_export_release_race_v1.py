"""Release-identity race in the production learning export.

**The defect.** ``export-opip-learning-evidence.sh`` captured the production
release SHA early, then committed ``manifest.env`` only after the artifact copy
and the canonical replica export - tens of seconds to minutes later. For that
whole window the exporting run holds its own export lock, but ``ohm-deploy`` does
not participate in it: it publishes ``/var/lib/ohm-deploy/last-good-sha`` at its
own commit point. A deploy that flipped that receipt mid-generation therefore
received a *fresh, internally consistent* export bound to the **previous**
release.

That is not a staleness failure and not corruption, so the deploy-side gate could
not be satisfied by waiting: ``_evaluate_committed_export`` returns at its first
predicate (release SHA) before freshness is even computed, and reported
``LEARNING_EXPORT_RELEASE_SHA_MISMATCH`` for a 281-second-old manifest inside a
600-second bound. The deploy's own export attempt is normally lock-skipped in that
same window, so the bounded wait had nothing valid to converge on.

**The correction.** The generation identity is captured once (as before), and
revalidated against the receipt at the commit point, immediately before the
atomic rename. If it moved, the generation is *discarded*, never relabelled:
rewriting the SHA into evidence built for another release would mint a provenance
claim the artifacts cannot support. The committed manifest is left untouched so
the previous valid generation stays authoritative, the unreferenced replica
directory is removed so retention stays bounded, and the run fails closed.

The static half of this suite runs everywhere; the behavioural half executes the
real revalidation function and is POSIX-only, matching the diagnostics suite.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "deploy" / "remote" / "export-opip-learning-evidence.sh"
DEPLOY_SCRIPT = ROOT / "deploy" / "remote" / "ohm-deploy"

GENERATION_SHA = "83d087619c9990a616cfb75c793558efbb7a675d"
DEPLOYED_SHA = "20b9f394f1bcf896705f28663d34cc92a49156f4"

#: Behavioural tests execute the real function; they need bash and POSIX
#: coreutils, so they skip on Windows (the repository's shell-suite convention).
pytestmark_posix = pytest.mark.skipif(
    os.name == "nt", reason="POSIX-only: needs bash and POSIX coreutils"
)


def _script() -> str:
    return EXPORTER.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    """Extract one shell function definition from the exporter, closing brace included."""
    source = _script()
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start)
    return source[start : end + 2]


# ---------------------------------------------------------------------------
# One reader for the release identity
# ---------------------------------------------------------------------------


def test_generation_identity_has_exactly_one_reader():
    """Both the attestation and the generation identity must agree on the release.

    A second inline ``cat`` of the receipt is how the two drift apart, so exactly
    one read site is allowed and it must be the helper.
    """
    source = _script()
    assert source.count("last-good-sha") == 1
    assert 'RELEASE_RECEIPT="/var/lib/ohm-deploy/last-good-sha"' in source
    helper = _function_body("read_authoritative_release_sha")
    assert 'cat "$RELEASE_RECEIPT"' in helper
    assert "^[0-9a-f]{40}$" in helper
    # No git fallback: an unattributable export must stay unattributable.
    assert "rev-parse" not in source
    assert "git " not in source
    # Every consumer goes through the helper.
    assert source.count("read_authoritative_release_sha)") >= 2


def test_generation_identity_is_captured_before_the_replica_is_built():
    """The replica's inner manifest must be bound to the captured identity.

    If the capture moved after the replica export the two layers could disagree,
    which the deploy gate rejects as a replica provenance mismatch.
    """
    source = _script()
    capture = 'production_deployed_sha="$(read_authoritative_release_sha)"'
    assert capture in source
    assert source.index(capture) < source.index('--release-sha "$production_deployed_sha"')
    # The captured value is what the outer manifest records.
    assert "printf 'production_deployed_sha=%s\\n' \"$production_deployed_sha\"" in source


# ---------------------------------------------------------------------------
# The commit-point revalidation
# ---------------------------------------------------------------------------


def test_revalidation_is_the_last_step_before_the_manifest_commit():
    """The unvalidated window must be a single rename, not a whole code block."""
    source = _script()
    call = 'commit_export_or_discard "$production_deployed_sha" "$manifest_tmp"'
    commit = 'mv -f -- "$manifest_tmp" "$EXPORT_MANIFEST"'
    assert call in source
    assert commit in source
    assert source.count(commit) == 1
    assert source.index(call) < source.index(commit)

    between = source[source.index(call) + len(call) : source.index(commit)]
    # Only the fail-closed guard may sit between revalidation and the rename.
    assert "|| revalidation_rc=$?" in between
    assert "exit 75" in between
    for forbidden in ("chown", "chmod", "sha256sum", "cp ", "date ", "printf"):
        assert forbidden not in between, f"work re-entered the unvalidated window: {forbidden}"


def test_revalidation_reads_the_receipt_again_at_the_commit_point():
    body = _function_body("commit_export_or_discard")
    # Re-read, not reuse: reusing the captured value would validate nothing.
    assert 'commit_sha="$(read_authoritative_release_sha)"' in body
    assert '[[ "$commit_sha" == "$generation_sha" ]]' in body
    assert "return 0" in body
    assert "return 75" in body


def test_release_change_fails_closed_without_publishing_a_manifest():
    """The mismatch path must publish nothing and leave the commit marker alone."""
    body = _function_body("commit_export_or_discard")
    # It never writes or renames the manifest...
    assert '> "$EXPORT_MANIFEST"' not in body
    assert "mv " not in body
    assert "manifest.env" not in body
    # ...it removes the unpublished temporary manifest instead.
    assert 'rm -f -- "$manifest_tmp"' in body
    assert "discarding uncommitted export" in body

    source = _script()
    assert "if (( revalidation_rc != 0 )); then" in source
    assert source.count("exit 75") == 1


def test_discard_never_deletes_the_committed_generation():
    """A reused content-addressed directory may be the live generation.

    Removing it would destroy the last valid evidence - the exact opposite of the
    guarantee this change is supposed to add.
    """
    body = _function_body("commit_export_or_discard")
    assert "REPLICA_CREATED_THIS_RUN" in body
    assert '"$committed_dir" != "$REPLICA_PUBLISH_NAME"' in body
    guard = body.index("REPLICA_CREATED_THIS_RUN")
    removal = body.index('rm -rf -- "$REPLICA_PUBLISH_DIR"')
    assert guard < removal, "the published replica is removed before any ownership check"
    assert body.count('rm -rf -- "$REPLICA_PUBLISH_DIR"') == 1

    source = _script()
    # Ownership is only ever claimed on the branch that performed the move.
    assert source.count("REPLICA_CREATED_THIS_RUN=1") == 1
    moved = source.index('mv -- "$REPLICA_STAGING" "$REPLICA_PUBLISH_DIR"')
    assert moved < source.index("REPLICA_CREATED_THIS_RUN=1")
    # The committed directory is read, never written, to decide ownership.
    assert '-r "$EXPORT_MANIFEST"' in body


def test_manifest_publication_stays_atomic_and_never_rewritten():
    source = _script()
    assert 'manifest_tmp="$EXPORT_ROOT/.manifest.env.tmp.$$"' in source
    assert source.count('mv -f -- "$manifest_tmp" "$EXPORT_MANIFEST"') == 1
    # Never edit committed evidence in place (no SHA substitution, no truncation).
    assert "sed -i" not in source
    assert '>"$EXPORT_MANIFEST"' not in source
    assert '>>"$EXPORT_MANIFEST"' not in source
    # The previous generation is read to make the discard decision.
    assert '-r "$EXPORT_MANIFEST"' in source


# ---------------------------------------------------------------------------
# Untouched behaviour: locks, lock-skip, retention
# ---------------------------------------------------------------------------


def test_legitimate_lock_skip_and_locks_are_preserved():
    """A concurrent cron run legitimately wins the lock; that must still succeed."""
    source = _script()
    assert 'exec 9>"$TRIGGER_LOCK"' in source
    assert "flock -n 9" in source
    assert "O'Pip learning export already active; skipping" in source
    assert "flock -x 8" in source
    # The skip path still returns success before any revalidation exists.
    skip_start = source.index("flock -n 9")
    assert source.index("exit 0", skip_start) < source.index("commit_export_or_discard() {")
    # Retention still prunes only after the commit marker moved.
    assert source.index('mv -f -- "$manifest_tmp" "$EXPORT_MANIFEST"') < source.index(
        'find "$EXPORT_ROOT" -mindepth 1 -maxdepth 1 -type d'
    )


# ---------------------------------------------------------------------------
# The deploy-side wait must survive a skip followed by a regeneration
# ---------------------------------------------------------------------------

#: Export cadence: every 2 minutes plus a 40s offset => ~160s per cycle.
EXPORT_CYCLE_SECONDS = 160


def test_proof_wait_covers_a_lock_skip_followed_by_regeneration():
    """One lock-skipped attempt plus one regeneration is a legitimate outcome."""
    import app.opip.learning.canonical_replica as replica_module

    wait = replica_module.EXPORT_PROOF_WAIT_SECONDS
    # release of the in-flight run + next scheduled start + one regeneration
    assert wait >= 3 * EXPORT_CYCLE_SECONDS, (
        f"wait {wait}s cannot cover a skip plus a regeneration"
    )
    # Still strictly bounded, and inside the gateway's 600s keepalive budget.
    assert wait <= 600
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert f"EXPORT_PROOF_WAIT_SECONDS={wait}" in source
    assert (
        f"EXPORT_PROOF_FRESHNESS_SECONDS={replica_module.EXPORT_PROOF_FRESHNESS_SECONDS}"
        in source
    )


def test_proof_freshness_bound_is_unchanged():
    """The wait may grow; the freshness a committed export must meet may not."""
    import app.opip.learning.canonical_replica as replica_module

    assert replica_module.EXPORT_PROOF_FRESHNESS_SECONDS == 600


# ---------------------------------------------------------------------------
# Behavioural: execute the real revalidation function
# ---------------------------------------------------------------------------


def _fixture(tmp_path: Path, *, receipt_sha: str | None) -> dict[str, Path]:
    """Lay out an export root shaped like the production one.

    Two content-addressed generations are published: ``committed_dir`` is the one
    the committed manifest references, ``new_dir`` is the unpublished one this run
    produced.
    """
    export_root = tmp_path / "export"
    committed_dir = export_root / ("canonical_learning_replica." + "a" * 64)
    new_dir = export_root / ("canonical_learning_replica." + "b" * 64)
    staging = export_root / ".canonical_learning_replica.staging.4242"
    for directory in (committed_dir, new_dir, staging):
        directory.mkdir(parents=True)
    manifest = export_root / "manifest.env"
    manifest.write_text(
        "schema_version=4\n"
        "exported_at_utc=2026-11-01T04:44:44Z\n"
        f"production_deployed_sha={GENERATION_SHA}\n"
        "canonical_learning_replica_version=1\n"
        f"canonical_learning_replica_dir={committed_dir.name}\n",
        encoding="utf-8",
    )
    manifest_tmp = export_root / ".manifest.env.tmp.4242"
    manifest_tmp.write_text("schema_version=4\n", encoding="utf-8")

    receipt = tmp_path / "last-good-sha"
    if receipt_sha is not None:
        receipt.write_text(receipt_sha + "\n", encoding="utf-8")

    return {
        "export_root": export_root,
        "committed_dir": committed_dir,
        "new_dir": new_dir,
        "staging": staging,
        "manifest": manifest,
        "manifest_tmp": manifest_tmp,
        "receipt": receipt,
    }


def _run_revalidation(
    tmp_path: Path, fx: dict, *, generation_sha: str, created_this_run: bool
) -> int:
    """Execute the real function against the fixture and return its exit code."""
    prelude = "\n".join(
        [
            "set -Eeuo pipefail",
            f'EXPORT_ROOT="{fx["export_root"]}"',
            f'EXPORT_MANIFEST="{fx["manifest"]}"',
            f'RELEASE_RECEIPT="{fx["receipt"]}"',
            f'REPLICA_STAGING="{fx["staging"]}"',
            f'REPLICA_PUBLISH_NAME="{fx["new_dir"].name}"',
            f'REPLICA_PUBLISH_DIR="{fx["new_dir"]}"',
            'REPLICA_CREATED_THIS_RUN="%s"' % ("1" if created_this_run else ""),
            "",
        ]
    )
    tail = "\n".join(
        ["rc=0", 'commit_export_or_discard "$1" "$2" || rc=$?', 'echo "RC=$rc"', ""]
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        prelude
        + _function_body("read_authoritative_release_sha")
        + "\n\n"
        + _function_body("commit_export_or_discard")
        + "\n\n"
        + tail,
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(harness), generation_sha, str(fx["manifest_tmp"])],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RC="))
    return int(line.partition("=")[2])


@pytestmark_posix
def test_release_change_discards_the_uncommitted_generation(tmp_path):
    """The production failure, reproduced at the commit point.

    A fresh, complete generation built for the previous release must not be
    published, and the previous valid evidence must survive untouched.
    """
    fx = _fixture(tmp_path, receipt_sha=DEPLOYED_SHA)
    before = fx["manifest"].read_bytes()

    rc = _run_revalidation(
        tmp_path, fx, generation_sha=GENERATION_SHA, created_this_run=True
    )

    assert rc == 75
    # Nothing was committed...
    assert fx["manifest"].read_bytes() == before
    assert not fx["manifest_tmp"].exists()
    # ...the unpublished generation is gone so retention stays bounded...
    assert not fx["new_dir"].exists()
    assert not fx["staging"].exists()
    # ...and the committed generation is exactly where the manifest says it is.
    assert fx["committed_dir"].is_dir()
    manifest = fx["manifest"].read_text(encoding="utf-8")
    assert f"canonical_learning_replica_dir={fx['committed_dir'].name}" in manifest
    assert f"production_deployed_sha={GENERATION_SHA}" in manifest


@pytestmark_posix
def test_unchanged_release_commits_untouched(tmp_path):
    fx = _fixture(tmp_path, receipt_sha=GENERATION_SHA)
    before = fx["manifest"].read_bytes()

    rc = _run_revalidation(
        tmp_path, fx, generation_sha=GENERATION_SHA, created_this_run=True
    )

    assert rc == 0
    # The caller's rename is now safe, and nothing this run produced was removed.
    assert fx["manifest_tmp"].exists()
    assert fx["new_dir"].is_dir()
    assert fx["manifest"].read_bytes() == before


@pytestmark_posix
def test_reused_content_addressed_directory_is_never_deleted(tmp_path):
    """A content-address hit must not let the discard path delete live evidence."""
    fx = _fixture(tmp_path, receipt_sha=DEPLOYED_SHA)
    # This run reused an existing directory instead of publishing its own, and
    # the committed manifest happens to reference that same directory.
    rewritten = fx["manifest"].read_text(encoding="utf-8").replace(
        fx["committed_dir"].name, fx["new_dir"].name
    )
    fx["manifest"].write_text(rewritten, encoding="utf-8")

    rc = _run_revalidation(
        tmp_path, fx, generation_sha=GENERATION_SHA, created_this_run=False
    )

    assert rc == 75
    assert fx["new_dir"].is_dir(), "the commit path must not delete live evidence"


@pytestmark_posix
def test_vanished_receipt_fails_closed(tmp_path):
    """A receipt that disappears mid-generation is a change, not a pass."""
    fx = _fixture(tmp_path, receipt_sha=None)
    before = fx["manifest"].read_bytes()

    rc = _run_revalidation(
        tmp_path, fx, generation_sha=GENERATION_SHA, created_this_run=True
    )

    assert rc == 75
    assert fx["manifest"].read_bytes() == before
