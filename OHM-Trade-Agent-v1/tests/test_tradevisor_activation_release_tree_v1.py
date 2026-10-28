"""ATDD-RELEASE-TREE-DEPLOY-v1/AC-011 - Tradevisor activation Gate 4.

The production Trading droplet is a certified release-tree host: there is no Git
checkout, so activation's release gate previously refused with
"production git checkout missing". Gate 4 must instead prove the approved SHA
from controller-recorded evidence - the active release record, the installed
source manifest, the exact-commit artifact provenance and a retained rollback
vehicle - while a Git checkout keeps its historical validation unchanged.

Every negative case below mutates exactly one piece of evidence and must be
refused.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

APP = pathlib.Path(__file__).resolve().parents[1]
ACTIVATION = APP / "deploy" / "tradevisor" / "activate-tradevisor-advisory.sh"
BASH = shutil.which("bash")

pytestmark = [
    pytest.mark.acceptance,
    pytest.mark.skipif(BASH is None, reason="bash is required"),
]

APPROVED = "1" * 40
PREVIOUS = "2" * 40
TREE = "a" * 40
ARCHIVE = "b" * 64

STUBS = """
set -Eeuo pipefail
step() { :; }
pass() { printf 'PASS  %s\\n' "$*"; }
info() { printf 'INFO  %s\\n' "$*"; }
warn() { printf 'WARN  %s\\n' "$*" >&2; }
die()  { printf 'FAIL  %s\\n' "$*" >&2; exit 1; }
compose() { return 0; }
image_revision() { printf '%s' "$SHA"; }
"""


def _gate4_functions() -> str:
    text = ACTIVATION.read_text(encoding="utf-8")
    start = text.index("record_field() {")
    end = text.index("# --- Gate 5")
    return text[start:end]


def _run(root: pathlib.Path, body: str) -> subprocess.CompletedProcess:
    script = f"""
{STUBS}
source {root.as_posix()}/gate4-functions.sh
REPO_ROOT={root.as_posix()}/repo
APP_ROOT={root.as_posix()}/repo/OHM-Trade-Agent-v1
STATE_DIR={root.as_posix()}
LAST_GOOD_FILE={root.as_posix()}/last-good-sha
CURRENT_RELEASE_FILE={root.as_posix()}/current-release.env
RELEASES_DIR={root.as_posix()}/releases
STAGING_DIR={root.as_posix()}/staging
CORE_SERVICE=ohm-trade-agent
WRITER_SERVICE=opip-canonical-writer
SHA={APPROVED}
{body}
"""
    return subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, check=False
    )


def _sha256(path: pathlib.Path) -> str:
    return subprocess.run(
        [BASH, "-c", f"sha256sum {path.as_posix()}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]


@pytest.fixture()
def evidence(tmp_path: pathlib.Path) -> pathlib.Path:
    """A Git-less production evidence layout that Gate 4 must accept."""
    (tmp_path / "gate4-functions.sh").write_text(_gate4_functions(), encoding="utf-8")
    app = tmp_path / "repo" / "OHM-Trade-Agent-v1"
    (app / "deploy").mkdir(parents=True)
    (app / "deploy" / "one.txt").write_text("x\n", encoding="utf-8")
    (app / ".env").write_text("SECRET=never-logged\n", encoding="utf-8")
    (app / "data" / "opip").mkdir(parents=True)
    (app / "data" / "opip" / "canonical.sqlite3").write_bytes(b"db")

    listing = subprocess.run(
        [BASH, "-c", f"cd {app.as_posix()} && sha256sum deploy/one.txt"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    manifest = tmp_path / "releases" / APPROVED / "manifest.sha256"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(listing, encoding="utf-8")
    digest = _sha256(manifest)

    (tmp_path / "releases" / APPROVED / "release.env").write_text(
        f"sha={APPROVED}\ntree={TREE}\nmanifest_digest={digest}\ninstalled_at=x\n",
        encoding="utf-8",
    )
    (tmp_path / "current-release.env").write_text(
        f"schema=1\nsha={APPROVED}\ntree={TREE}\nmanifest_digest={digest}\n"
        f"archive_digest={ARCHIVE}\ninstalled_at=x\n",
        encoding="utf-8",
    )
    provenance = tmp_path / "staging" / APPROVED / "provenance.env"
    provenance.parent.mkdir(parents=True)
    provenance.write_text(
        f"schema=1\nsha={APPROVED}\ntree={TREE}\narchive_digest={ARCHIVE}\n"
        f"manifest_digest={digest}\nfile_count=1\n",
        encoding="utf-8",
    )
    (tmp_path / "last-good-sha").write_text(f"{APPROVED}\n", encoding="utf-8")

    previous = tmp_path / "releases" / PREVIOUS
    previous.mkdir(parents=True)
    (previous / "manifest.sha256").write_text(listing, encoding="utf-8")
    (previous / "release.env").write_text(
        f"sha={PREVIOUS}\nmanifest_digest={_sha256(previous / 'manifest.sha256')}\n"
        "installed_at=x\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            BASH,
            "-c",
            f"cd {app.as_posix()} && tar -cf {(previous / 'source.tar').as_posix()} "
            "deploy/one.txt",
        ],
        check=True,
    )
    return tmp_path


def test_release_tree_evidence_is_accepted(evidence: pathlib.Path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-011: a Git-less host with authentic
    controller evidence passes Gate 4."""
    assert not (evidence / "repo" / ".git").exists()
    result = _run(evidence, "verify_release_and_recovery")
    assert result.returncode == 0, result.stderr
    assert f"controller release record names the approved SHA {APPROVED}" in result.stdout
    assert "installed release manifest matches the controller record" in result.stdout
    assert "exact-commit artifact provenance matches the installed release" in result.stdout
    assert "installed application source matches the release manifest" in result.stdout
    assert f"retained recovery artifact verified for previous release {PREVIOUS}" in result.stdout
    assert "application and writer images carry the same approved revision" in result.stdout


@pytest.mark.parametrize(
    ("name", "mutation", "expected"),
    (
        (
            "record_sha",
            f"sed -i 's/^sha=.*/sha={'9' * 40}/' $STATE_DIR/current-release.env",
            "is not the approved SHA",
        ),
        (
            "record_tree",
            "sed -i 's/^tree=.*/tree=not-a-tree/' $STATE_DIR/current-release.env",
            "no source tree identity",
        ),
        (
            "last_good",
            f"printf '%s\\n' {'8' * 40} > $LAST_GOOD_FILE",
            "last-good-sha",
        ),
        (
            "manifest",
            "printf 'x\\n' >> $RELEASES_DIR/$SHA/manifest.sha256",
            "does not match the controller record",
        ),
        (
            "release_record",
            f"sed -i 's/^sha=.*/sha={'7' * 40}/' $RELEASES_DIR/$SHA/release.env",
            "installed release record SHA",
        ),
        (
            "live_source",
            "printf 'z\\n' > $APP_ROOT/deploy/one.txt",
            "does not match the release manifest",
        ),
        (
            "live_extra_file",
            "printf 'z\\n' > $APP_ROOT/deploy/extra.txt",
            "files absent from the release manifest",
        ),
        (
            "provenance_missing",
            "rm -f $STAGING_DIR/$SHA/provenance.env",
            "artifact provenance missing",
        ),
        (
            "provenance_tree",
            f"sed -i 's/^tree=.*/tree={'6' * 40}/' $STAGING_DIR/$SHA/provenance.env",
            "artifact provenance tree",
        ),
        (
            "provenance_count",
            "sed -i 's/^file_count=.*/file_count=9/' $STAGING_DIR/$SHA/provenance.env",
            "does not match the installed manifest",
        ),
        (
            "recovery_manifest",
            f"printf 'x\\n' >> $RELEASES_DIR/{PREVIOUS}/manifest.sha256",
            "retained recovery manifest for",
        ),
        (
            "recovery_archive",
            f"printf 'not-a-tar' > $RELEASES_DIR/{PREVIOUS}/source.tar",
            "not a valid archive",
        ),
        (
            "recovery_missing",
            f"rm -rf $RELEASES_DIR/{PREVIOUS}",
            "no retained recovery artifact",
        ),
    ),
)
def test_tampered_or_missing_evidence_is_refused(
    evidence: pathlib.Path, name: str, mutation: str, expected: str
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-011: one broken fact refuses activation."""
    mutated = _run(evidence, mutation)
    assert mutated.returncode == 0, mutated.stderr
    result = _run(evidence, "verify_release_and_recovery")
    assert result.returncode != 0, name
    assert expected in result.stderr, f"{name}: {result.stderr}"


def test_git_worktree_path_keeps_its_historical_validation() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-011: a Git checkout is unchanged."""
    text = ACTIVATION.read_text(encoding="utf-8")
    git_branch = text[text.index("verify_release_git_worktree() {"):text.index("verify_release_tree() {")]
    for literal in (
        'die "production git checkout missing at $REPO_ROOT"',
        "rev-parse HEAD",
        'cat-file -e "${SHA}^{commit}"',
        "status --porcelain --untracked-files=no",
        "tracked modifications",
        "last-good-sha",
        'rev-parse "${SHA}^"',
    ):
        assert literal in git_branch, literal
    # Dispatch stays explicit and fail-closed on both models.
    dispatcher = text[text.index("verify_release_and_recovery() {"):]
    assert 'if [[ -d "$REPO_ROOT/.git" ]]; then' in dispatcher
    assert "verify_release_git_worktree" in dispatcher
    assert "verify_release_tree" in dispatcher


# ---------------------------------------------------------------------------
# Runtime-only exclusion parity with the controller.
#
# The reported activation failure: the deployment controller generates
# `.env.core` (the filtered core environment the Compose service mounts) and
# correctly keeps it out of release provenance, while `source_listing()` did not
# exclude it. The live-source comparison then read a legitimate controller file
# as an unreleased source file and refused activation on a false integrity
# error. These pin the parity and the behaviour it protects.
# ---------------------------------------------------------------------------

LIBRARY = APP / "deploy" / "remote" / "lib" / "opip_release_tree.sh"
_NOT_EXCLUSION = re.compile(r"-not\s+-(path|name)\s+'([^']+)'")
_BASH_UNAVAILABLE_CODES = {3221225794, -1073741502, 3221225477}


def _function_body(path: pathlib.Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _exclusions(function_body: str) -> frozenset[tuple[str, str]]:
    return frozenset(_NOT_EXCLUSION.findall(function_body))


def _skip_if_bash_unusable(result: subprocess.CompletedProcess) -> None:
    text = f"{result.stderr}{result.stdout}"
    if result.returncode in _BASH_UNAVAILABLE_CODES or (
        result.returncode != 0
        and ("fork: Resource temporarily unavailable" in text or "dofork" in text)
    ):
        pytest.skip("bash cannot run reliably in this environment")


def test_source_listing_runtime_only_exclusions_match_the_controller() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-011: the activation gate must exclude
    exactly the runtime-only paths the deployment controller excludes, so a
    controller-generated `.env.core` can never be read as unreleased source and
    any future drift between the two lists fails here."""

    gate = _exclusions(_function_body(ACTIVATION, "source_listing"))
    controller = _exclusions(_function_body(LIBRARY, "rt_list_tree"))

    assert gate == controller
    assert ("path", "./.env.core") in gate


def test_source_listing_keeps_unexpected_files_and_drops_runtime_only_paths(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-011: `.env.core`, `.env`, production data
    and byte-code caches stay out of the live-source listing, while a genuinely
    unexpected file is still listed - so it is still refused as "files absent
    from the release manifest" rather than silently ignored."""

    tree = tmp_path / "app"
    (tree / "deploy").mkdir(parents=True)
    (tree / "deploy" / "one.txt").write_text("x\n", encoding="utf-8")
    (tree / "deploy" / "unexpected.txt").write_text("z\n", encoding="utf-8")
    (tree / ".env").write_text("SECRET=never-logged\n", encoding="utf-8")
    (tree / ".env.core").write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\n", encoding="utf-8"
    )
    (tree / "data" / "opip").mkdir(parents=True)
    (tree / "data" / "opip" / "canonical.sqlite3").write_bytes(b"db")
    (tree / "__pycache__").mkdir()
    (tree / "__pycache__" / "module.pyc").write_bytes(b"\x00")

    script = (
        "set -Eeuo pipefail\n"
        + _function_body(ACTIVATION, "source_listing")
        + f"\nsource_listing {tree.as_posix()}\n"
    )
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, check=False
    )
    _skip_if_bash_unusable(result)
    assert result.returncode == 0, result.stderr

    paths = {
        line.split("  ", 1)[1]
        for line in result.stdout.splitlines()
        if line.strip()
    }
    assert "deploy/one.txt" in paths
    # A real unrecorded file is still compared, so the controller's "files
    # absent from the release manifest" refusal keeps working.
    assert "deploy/unexpected.txt" in paths
    assert ".env" not in paths
    assert ".env.core" not in paths
    assert not any(path.startswith("data/") for path in paths)
    assert not any(
        "__pycache__" in path or path.endswith(".pyc") for path in paths
    )


def test_runtime_only_core_env_is_accepted_by_the_release_tree_gate(
    evidence: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-011: with a legitimate controller-generated
    `.env.core` present, Gate 4 still passes, and every manifest/hash/path
    verification between the record, the manifest, the provenance and the live
    source stays enforced (the negative cases above are unchanged)."""

    app = evidence / "repo" / "OHM-Trade-Agent-v1"
    (app / ".env.core").write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\n", encoding="utf-8"
    )

    result = _run(evidence, "verify_release_and_recovery")
    _skip_if_bash_unusable(result)

    assert result.returncode == 0, result.stderr
    assert "installed application source matches the release manifest" in result.stdout
    assert "files absent from the release manifest" not in result.stderr
