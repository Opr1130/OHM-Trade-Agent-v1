"""ATDD - release-tree deployment correction (O'Pip v1.5.0).

The production droplet is a certified release-tree host: no Git metadata at the
source root. The controller must therefore bind a release to an exact commit,
verify the artifact it produces from that commit, install it without touching
`.env` or production data, and restore the retained previous release when
activation fails - without `git reset --hard` and without fabricating history.

These tests drive `deploy/remote/lib/opip_release_tree.sh` through real Git
objects in temporary directories. They assert the controller gates, not the
Docker pipeline (which is unchanged and covered elsewhere).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

APP = pathlib.Path(__file__).resolve().parents[1]
LIB = APP / "deploy" / "remote" / "lib" / "opip_release_tree.sh"

BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = [
    pytest.mark.acceptance,
    pytest.mark.skipif(BASH is None or GIT is None, reason="bash and git are required"),
]


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    base = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": env.get("HOME", "/tmp"),
        "LC_ALL": "C",
    }
    base.update(env)
    return subprocess.run(
        [BASH, "-c", script], env=base, capture_output=True, text=True, check=False
    )


def _git(repo: pathlib.Path, *args: str) -> str:
    out = subprocess.run(
        [GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


class Release:
    """A real Git origin plus the production paths a controller operates on."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.origin = root / "origin.git"
        self.work = root / "work"
        self.cache = root / "cache" / "repo.git"
        self.app_root = root / "opt" / "OHM-Trade-Agent-v1" / "OHM-Trade-Agent-v1"
        self.repo_root = root / "opt" / "OHM-Trade-Agent-v1"
        self.state = root / "var" / "ohm-deploy"
        self.app_root.mkdir(parents=True)
        self.state.mkdir(parents=True)
        (self.app_root / "data" / "opip").mkdir(parents=True)
        (self.app_root / "data" / "opip" / "canonical.sqlite3").write_bytes(b"db-bytes")
        (self.app_root / ".env").write_text("SECRET=not-for-logs\n", encoding="utf-8")

    def commit_release(self, marker: str) -> str:
        if not (self.work / ".git").is_dir():
            subprocess.run([GIT, "init", "--bare", "-q", str(self.origin)], check=True)
            subprocess.run([GIT, "init", "-q", str(self.work)], check=True)
            _git(self.work, "remote", "add", "origin", str(self.origin))
            _git(self.work, "config", "user.email", "t@example.com")
            _git(self.work, "config", "user.name", "t")
        app = self.work / "OHM-Trade-Agent-v1"
        (app / "app").mkdir(parents=True, exist_ok=True)
        (app / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        (app / "app" / "main.py").write_text(f"# {marker}\n", encoding="utf-8")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-q", "-m", marker)
        _git(self.work, "push", "-q", "origin", "HEAD:main")
        return _git(self.work, "rev-parse", "HEAD")

    def clone_cache(self) -> None:
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [GIT, "clone", "--bare", "-q", str(self.origin), str(self.cache)],
            check=True,
        )
        _git(self.cache, "remote", "set-url", "origin", str(self.origin))

    def install_as_live(self, sha: str) -> None:
        """Recreate the certified install plus the owner-bootstrap baseline.

        Mirrors production: the release tree was installed out of band, then the
        bootstrap recorded it as the verified baseline and its rollback vehicle.
        """
        seed = self.root / "seed"
        seed.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            f"git --git-dir={self.cache} archive --format=tar {sha} | tar -xf - -C {seed}",
            shell=True,
            check=True,
        )
        source = seed / "OHM-Trade-Agent-v1"
        for item in source.iterdir():
            target = self.app_root / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        (self.state / "last-good-sha").write_text(f"{sha}\n", encoding="utf-8")
        script = f"""
set -Eeuo pipefail
REPO_ROOT={self.repo_root}
APP_ROOT={self.app_root}
STATE_DIR={self.state}
LAST_GOOD_FILE={self.state}/last-good-sha
TARGET_SHA={sha}
source {LIB}
OPIP_SOURCE_MODE=release-tree
REPO_OWNER=root
GIT=(git --git-dir={self.cache})
rt_snapshot_current "$TARGET_SHA" >/dev/null
rt_write_state "$OPIP_CURRENT_RELEASE" "$TARGET_SHA" unverified-baseline \\
  "$(rt_manifest_digest "$STATE_DIR/releases/$TARGET_SHA/manifest.sha256")" unverified-baseline
"""
        result = _run(script, {})
        assert result.returncode == 0, result.stderr



def _call(release: "Release", sha: str, body: str, env: dict[str, str] | None = None):
    script = f"""
set -Eeuo pipefail
OPIP_SOURCE_CACHE={release.cache}
REPO_ROOT={release.repo_root}
APP_ROOT={release.app_root}
STATE_DIR={release.state}
LAST_GOOD_FILE={release.state}/last-good-sha
TARGET_SHA={sha}
source {LIB}
OPIP_SOURCE_MODE=release-tree
REPO_OWNER=root
GIT=(git --git-dir={release.cache})
{body}
"""
    return _run(script, env or {})


@pytest.fixture()
def release(tmp_path: pathlib.Path) -> "Release":
    rel = Release(tmp_path)
    rel.prev_sha = rel.commit_release("release-one")  # type: ignore[attr-defined]
    rel.clone_cache()
    rel.install_as_live(rel.prev_sha)  # type: ignore[attr-defined]
    return rel


def test_release_tree_mode_is_selected_when_no_git_metadata_exists(release) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-001: a Git-less production root resolves
    to the release-tree model; no Git metadata is created there."""
    result = _call(release, release.prev_sha, "opip_source_init; echo $OPIP_SOURCE_MODE")
    assert result.returncode == 0, result.stderr
    assert "OPIP_SOURCE_MODE=release-tree" in result.stdout
    assert not (release.repo_root / ".git").exists()


def test_exact_commit_artifact_is_staged_and_manifest_verified(release) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-002: the artifact is produced from the
    exact commit and its digest plus manifest are recorded and verified."""
    new_sha = release.commit_release("release-two")
    result = _call(release, new_sha, "source_prepare")
    assert result.returncode == 0, result.stderr
    staged = release.state / "staging" / new_sha
    provenance = (staged / "provenance.env").read_text(encoding="utf-8")
    assert f"sha={new_sha}" in provenance
    assert "tree=" in provenance and "archive_digest=" in provenance
    assert "file_count=2" in provenance
    assert "app/main.py" in (staged / "manifest.sha256").read_text(encoding="utf-8")


def test_foreign_target_is_refused(release) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-003: a commit that is not current
    origin/main is refused with exit 65."""
    new_sha = release.commit_release("release-two")
    result = _call(release, "b" * 40, "source_prepare")
    assert result.returncode == 65
    assert "not current origin/main" in result.stderr
    assert new_sha != "b" * 40


def test_hand_edited_source_is_refused(release) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-003: deviations from the installed
    release refuse deployment with exit 66."""
    new_sha = release.commit_release("release-two")
    (release.app_root / "app" / "main.py").write_text(
        "# hand edited in production\n", encoding="utf-8"
    )
    result = _call(release, new_sha, "source_prepare")
    assert result.returncode == 66
    assert "does not match the installed release" in result.stderr


def test_activation_preserves_env_and_production_data(release) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-004: activation changes source only;
    .env and production data survive byte-for-byte."""
    new_sha = release.commit_release("release-two")
    env_before = (release.app_root / ".env").read_bytes()
    db_before = (release.app_root / "data" / "opip" / "canonical.sqlite3").read_bytes()
    result = _call(
        release,
        new_sha,
        "source_prepare; source_activate_target; echo head=$(source_release_head)",
    )
    assert result.returncode == 0, result.stderr
    assert f"head={new_sha}" in result.stdout
    assert (release.app_root / ".env").read_bytes() == env_before
    assert (
        release.app_root / "data" / "opip" / "canonical.sqlite3"
    ).read_bytes() == db_before
    assert (release.app_root / "app" / "main.py").read_text(encoding="utf-8") == "# release-two\n"


def test_incomplete_staging_refuses_activation(release) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-003: partial staging never reaches the
    live tree."""
    new_sha = release.commit_release("release-two")
    before = (release.app_root / "app" / "main.py").read_text(encoding="utf-8")
    body = (
        f"source_prepare; rm -rf {release.state}/staging/{new_sha}/tree; "
        "source_activate_target"
    )
    result = _call(release, new_sha, body)
    assert result.returncode != 0
    assert "staged release for" in result.stderr
    assert (release.app_root / "app" / "main.py").read_text(encoding="utf-8") == before


def test_rollback_restores_the_retained_previous_release(release) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-005: rollback reinstates the retained
    previous source bytes and leaves production data in place."""
    new_sha = release.commit_release("release-two")
    db_before = (release.app_root / "data" / "opip" / "canonical.sqlite3").read_bytes()
    advance = _call(release, new_sha, "source_prepare; source_activate_target")
    assert advance.returncode == 0, advance.stderr
    rollback = _call(
        release,
        new_sha,
        "PREVIOUS_SHA=$(cat $LAST_GOOD_FILE); source_restore_previous; "
        "echo restored=$(source_release_head)",
    )
    assert rollback.returncode == 0, rollback.stderr
    assert f"restored={release.prev_sha}" in rollback.stdout
    assert (release.app_root / "app" / "main.py").read_text(encoding="utf-8") == "# release-one\n"
    assert (
        release.app_root / "data" / "opip" / "canonical.sqlite3"
    ).read_bytes() == db_before


def test_tampered_retained_release_is_reported_as_failure(release) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-005: a corrupted rollback vehicle must
    never report success."""
    new_sha = release.commit_release("release-two")
    advance = _call(release, new_sha, "source_prepare; source_activate_target")
    assert advance.returncode == 0, advance.stderr
    (release.state / "releases" / release.prev_sha / "source.tar").write_bytes(b"not-a-tar")
    rollback = _call(
        release,
        new_sha,
        "PREVIOUS_SHA=$(cat $LAST_GOOD_FILE); source_restore_previous",
    )
    assert rollback.returncode != 0
    assert "OPIP_RELEASE_RESTORED" not in rollback.stdout


def test_git_worktree_mode_is_still_selected_and_keeps_its_inline_gates(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-006: an existing checkout keeps the
    original controller gates.

    The Git gate bodies stay inline in the controller (the historical droplet's
    behaviour is byte-identical); only the release-tree model is delegated.
    """
    rel = Release(tmp_path)
    sha = rel.commit_release("release-one")
    checkout = rel.root / "checkout"
    subprocess.run([GIT, "clone", "-q", str(rel.origin), str(checkout)], check=True)
    script = f"""
set -Eeuo pipefail
REPO_ROOT={checkout}
APP_ROOT={checkout}/OHM-Trade-Agent-v1
STATE_DIR={rel.state}
LAST_GOOD_FILE={rel.state}/last-good-sha
TARGET_SHA={sha}
source {LIB}
opip_source_init
printf '%s\\n' "$OPIP_SOURCE_MODE"
"""
    ok = _run(script, {})
    assert ok.returncode == 0, ok.stderr
    assert "git-worktree" in ok.stdout

    controller = (APP / "deploy" / "remote" / "ohm-deploy").read_text(encoding="utf-8")
    assert "OPIP_SOURCE_MODE:-git-worktree" in controller
    assert "refusing deploy: tracked production worktree is dirty" in controller
    assert "refusing deploy: untracked files in the application Docker build context" in controller


def test_bootstrap_installs_the_library_at_a_stable_root_location() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-007: the one-time bootstrap provisions
    provenance and installs the provider beside the controller, never a
    deployment, and is not gateway-reachable."""
    bootstrap = (APP / "deploy" / "remote" / "bootstrap-release-tree-controller.sh").read_text(
        encoding="utf-8"
    )
    assert "((" " EUID == 0 ))" in bootstrap
    assert "git clone --bare" in bootstrap
    assert "rt_snapshot_current" in bootstrap
    assert "--check" in bootstrap and "--apply" in bootstrap
    # The provider must land beside the controller, not only in the staging dir.
    assert 'LIBRARY_DST="/usr/local/lib/opip/opip_release_tree.sh"' in bootstrap
    assert 'cp -- "$STAGE_LIB" "$libstaged"' in bootstrap
    assert 'mv -f -- "$libstaged" "$LIBRARY_DST"' in bootstrap
    assert "bash -n \"$LIBRARY_DST\"" in bootstrap
    for forbidden in (
        "docker compose",
        "docker run",
        "/usr/local/sbin/ohm-deploy \"",
        "ohm-deploy-ssh",
        "ssh ",
    ):
        assert forbidden not in bootstrap, forbidden
    gateway = (APP / "deploy" / "remote" / "ohm-deploy-ssh").read_text(encoding="utf-8")
    assert "bootstrap-release-tree-controller" not in gateway


def _preserve_block() -> str:
    text = (APP / "deploy" / "remote" / "reconcile-scheduler.sh").read_text(encoding="utf-8")
    start = text.index("preserve_target_deploy_controller_snapshot() {")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _run_preserve(app_root: pathlib.Path, state_dir: pathlib.Path):
    harness = f"""
set -Eeuo pipefail
export OPIP_DEPLOY_TEST_SEAMS=1
export OPIP_BOOTSTRAP_APP_ROOT={app_root.as_posix()}
export OPIP_BOOTSTRAP_STATE_DIR={state_dir.as_posix()}
{_preserve_block()}
preserve_target_deploy_controller_snapshot
"""
    return _run(harness, {})


CONTROLLER_BODY = "#!/usr/bin/env bash\necho controller\n"


def _seed_snapshot(state: pathlib.Path, name: str, body: str) -> None:
    snapshot = state / name
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "remote-op-ohm-deploy.present").write_text("", encoding="utf-8")
    (snapshot / "remote-op-ohm-deploy").write_text(body, encoding="utf-8")


def _seed_app(app_root: pathlib.Path) -> None:
    remote = app_root / "deploy" / "remote"
    remote.mkdir(parents=True, exist_ok=True)
    (remote / "ohm-deploy").write_text(CONTROLLER_BODY, encoding="utf-8")




def test_reconcile_resolves_release_tree_identity_without_git(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-009: the scheduler reconciliation accepts
    the authenticated release-tree identity when Git metadata is absent, keeps
    the Git identity first, and still refuses when no identity exists."""
    script = (APP / "deploy" / "remote" / "reconcile-scheduler.sh").read_text(encoding="utf-8")
    assert "deploy-controller bootstrap: repository HEAD is not a 40-char SHA" in script
    assert "current-release.env" in script

    gitless_app = tmp_path / "gitless" / "OHM-Trade-Agent-v1"
    _seed_app(gitless_app)
    state = tmp_path / "state"
    state.mkdir()

    # No Git metadata and no recorded identity: still fail closed, same message.
    _seed_snapshot(state, "scheduler-before.NOIDNT", CONTROLLER_BODY)
    absent = _run_preserve(gitless_app, state)
    assert absent.returncode != 0
    assert "repository HEAD is not a 40-char SHA" in absent.stderr

    # The verified activation record supplies the identity on a Git-less host.
    installed = "b" * 40
    (state / "current-release.env").write_text(
        f"schema=1\nsha={installed}\ntree=deadbeef\n", encoding="utf-8"
    )
    from_release = _run_preserve(gitless_app, state)
    assert from_release.returncode == 0, from_release.stderr
    assert "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=NOT_NEEDED" in from_release.stdout
    assert f"OPIP_DEPLOY_CONTROLLER_BOOTSTRAP_SHA={installed}" in from_release.stdout

    # A real worktree still wins over any recorded release identity.
    repo = tmp_path / "repo"
    _seed_app(repo)
    subprocess.run([GIT, "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    head = _git(repo, "rev-parse", "HEAD")
    from_git = _run_preserve(repo, state)
    assert from_git.returncode == 0, from_git.stderr
    assert f"OPIP_DEPLOY_CONTROLLER_BOOTSTRAP_SHA={head}" in from_git.stdout
    assert head != installed


def _library_resolver_block() -> str:
    text = (APP / "deploy" / "remote" / "ohm-deploy").read_text(encoding="utf-8")
    start = text.index("opip_source_library_path() {")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def test_controller_prefers_the_stable_library_and_refuses_when_absent(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-008: the controller resolves the stable
    root-controlled provider first, accepts an in-tree copy, and refuses to run
    when neither exists."""
    controller = (APP / "deploy" / "remote" / "ohm-deploy").read_text(encoding="utf-8")
    assert 'OPIP_STABLE_LIB="/usr/local/lib/opip/opip_release_tree.sh"' in controller
    assert "release-tree source library is not installed" in controller
    assert "is incomplete (missing" in controller
    assert "exit 69" in controller

    block = _library_resolver_block()
    app_root = tmp_path / "opt" / "OHM-Trade-Agent-v1" / "OHM-Trade-Agent-v1"
    intree = app_root / "deploy" / "remote" / "lib" / "opip_release_tree.sh"
    intree.parent.mkdir(parents=True)
    intree.write_text("# in-tree copy\n", encoding="utf-8")
    stable = tmp_path / "usr" / "local" / "lib" / "opip" / "opip_release_tree.sh"

    def resolve(stable_path: pathlib.Path):
        script = f"""
set -Eeuo pipefail
APP_ROOT={app_root.as_posix()}
OPIP_STABLE_LIB={stable_path.as_posix()}
{block}
opip_source_library_path
"""
        return _run(script, {})

    only_intree = resolve(stable)
    assert only_intree.returncode == 0, only_intree.stderr
    assert only_intree.stdout.strip() == intree.as_posix()

    stable.parent.mkdir(parents=True, exist_ok=True)
    stable.write_text("# stable install\n", encoding="utf-8")
    preferred = resolve(stable)
    assert preferred.returncode == 0, preferred.stderr
    assert preferred.stdout.strip() == stable.as_posix()

    stable.unlink()
    intree.unlink()
    absent = resolve(stable)
    assert absent.returncode != 0
    assert absent.stdout.strip() == ""

