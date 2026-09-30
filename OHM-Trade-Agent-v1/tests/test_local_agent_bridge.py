"""ATDD acceptance and adversarial tests; no live GitHub/Cursor calls."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools import local_agent_bridge as b

NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
PATH = b.PREFIX + "docs/engineering/example.md"
# The authenticated GitHub identity that owns bridge status comments in tests.
STATUS_AUTHOR = 1


def fixture_task():
    return {"schema": 1, "repo": b.REPO, "increment": "ATDD-EXAMPLE",
            "branch": "feature/bridge-example", "head": "a" * 40,
            "contract_sha256": "b" * 64, "authority_sha256": "c" * 64,
            "files": [PATH], "instructions": "Correct the spelling in the approved file."}


def comment(identifier, body, author=1):
    return {"id": identifier, "body": body, "user": {"id": author},
            "created_at": "2026-09-28T11:00:00Z", "updated_at": "2026-09-28T11:00:00Z"}


def snapshot(task=None):
    c = comment(10, "/opip-task\n" + json.dumps(task or fixture_task()))
    approval = {"task_comment_id": 10, "task_sha256": b.digest(c["body"].encode()),
                "expires_at": "2026-09-28T13:00:00Z", "architecture_clear": True}
    return 1, {"state": "open"}, [c, comment(11, "/opip-approve\n" + json.dumps(approval))]


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["valid", "unapproved", "wrong_author", "edited_task", "edited_approval",
    "hash", "expired", "future", "long_expiry", "conflict", "revoke", "reapprove", "foreign_repo",
    "unknown_key", "bool_schema", "main", "detached", "short_head", "missing_task", "duplicate_task",
    "duplicate_files", "inline_comment", "fork", "pr_stale", "pr_valid", "unknown_owner_noise"])
def test_control_plane(case):
    """ATDD-BRIDGE-v1/AC-001: only current exact authenticated approval authorizes a task."""
    snap = snapshot()
    comments = snap[2]
    approved = {"valid", "reapprove", "pr_valid", "unknown_owner_noise"}
    if case == "unapproved":
        comments.pop()
    elif case == "wrong_author":
        comments[0]["user"]["id"] = 999
    elif case.startswith("edited_"):
        comments[0 if case == "edited_task" else 1]["updated_at"] = "2026-09-28T11:01:00Z"
    elif case in {"hash", "expired", "future", "long_expiry", "conflict"}:
        approval = b.envelope(comments[1], "/opip-approve")
        if case == "hash":
            approval["task_sha256"] = "d" * 64
        elif case == "conflict":
            approval["architecture_clear"] = False
        else:
            approval["expires_at"] = {"expired": "2026-09-28T12:00:00Z",
                                       "future": "2026-09-29T13:00:00Z",
                                       "long_expiry": "2026-10-01T13:00:00Z"}[case]
        comments[1]["body"] = "/opip-approve\n" + json.dumps(approval)
    elif case in {"revoke", "reapprove"}:
        comments.insert(0, comment(12, '/opip-revoke\n{"task_comment_id":10}'))
        if case == "reapprove":
            latest = copy.deepcopy(comments[-1])
            latest["id"] = 13
            comments.insert(0, latest)
    elif case in {"foreign_repo", "unknown_key", "bool_schema", "main", "detached", "short_head", "duplicate_files"}:
        task = fixture_task()
        key, value = {"foreign_repo": ("repo", "someone/else"), "unknown_key": ("shell", "do bad things"),
                      "bool_schema": ("schema", True), "main": ("branch", "main"),
                      "detached": ("branch", "HEAD"), "short_head": ("head", "abc"),
                      "duplicate_files": ("files", [PATH, PATH])}[case]
        task[key] = value
        snap = snapshot(task)
    elif case == "missing_task":
        comments.pop(0)
    elif case == "duplicate_task":
        comments.append(copy.deepcopy(comments[0]))
    elif case == "inline_comment":
        comments[0]["body"] = "```json\n" + comments[0]["body"] + "\n```"
    elif case in {"fork", "pr_stale", "pr_valid"}:
        snap[1].update({"head": {"repo": {"full_name": "fork/repo" if case == "fork" else b.REPO},
                                "sha": "d" * 40 if case == "pr_stale" else "a" * 40,
                                "ref": "feature/bridge-example"},
                        "base": {"repo": {"full_name": b.REPO}, "ref": "main"}})
    elif case == "unknown_owner_noise":
        comments.append(comment(99, '/opip-revoke\n{"task_comment_id":10}', author=999))
    if case in approved:
        assert b.control(snap, 10, [], NOW)[0] == fixture_task()
    else:
        with pytest.raises(b.Stop):
            b.control(snap, 10, [], NOW)


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', 'NaN', 'Infinity', '{', 'x' * (b.MAX_BYTES + 1)],
                         ids=["duplicate", "nan", "infinity", "malformed", "oversized"])
def test_strict_json(raw):
    with pytest.raises(b.Stop):
        b.strict_json(raw)


@pytest.mark.parametrize("case", ["valid", "closed", "locked", "wrong_repo", "organization", "pages",
                                  "page_limit", "malformed", "api_failure"])
def test_github_snapshot(monkeypatch, case):
    api = b.GitHub()
    calls = []
    def get(path):
        calls.append(path)
        if case == "api_failure":
            raise b.Stop("HOST_COMMAND_FAILED")
        if path == "":
            return {"full_name": "wrong/repo" if case == "wrong_repo" else b.REPO,
                    "owner": {"id": 1, "type": "Organization" if case == "organization" else "User"}}
        if path == "issues/1":
            return {"state": "closed" if case == "closed" else "open", "locked": case == "locked"}
        if case == "malformed":
            return {"bad": "not an array"}
        if case == "page_limit" or case == "pages" and path.endswith("page=1"):
            return [comment(i + 1, "noise") for i in range(100)]
        return snapshot()[2]
    monkeypatch.setattr(api, "get", get)
    if case in {"valid", "pages"}:
        owner, _, comments = api.snapshot(1)
        assert owner == 1 and len(comments) == (102 if case == "pages" else 2)
    else:
        with pytest.raises(b.Stop):
            api.snapshot(1)
    if case == "page_limit":
        assert len(calls) == 22


def test_fixed_github_get(monkeypatch):
    monkeypatch.setattr(b.shutil, "which", lambda name: "C:/trusted/gh.exe")
    calls = []
    def host(args):
        calls.append(args)
        return '{}'
    monkeypatch.setattr(b, "host_run", host)
    b.GitHub().get("issues/123")
    assert calls == [["C:/trusted/gh.exe", "api", "--hostname", "github.com", "--method", "GET",
                      "repos/Opr1130/OHM-Trade-Agent-v1/issues/123"]]


def test_host_failure_redaction(monkeypatch):
    class Result:
        returncode = 1
        stdout = stderr = "synthetic-secret-in-error"
    monkeypatch.setattr(b.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(b.Stop, match="^HOST_COMMAND_FAILED$"):
        b.host_run(["fixed.exe"])


@pytest.mark.parametrize("case", ["valid", "in_tree_state", "ancestor_state", "in_tree_config", "bad_boolean",
                                  "bad_timeout", "bad_dispatch", "relative", "unknown_key"])
def test_local_config(tmp_path, case):
    root = tmp_path / "tree"
    root.mkdir()
    config = {"worktree": str(root), "state_dir": str(tmp_path / "state"), "dispatch_ids": [],
              "enable_execution": False, "cursor_executable": "not-enabled.exe",
              "cursor_sha256": "not-enabled", "cursor_timeout_seconds": 300}
    path = tmp_path / "config.json"
    if case == "in_tree_state":
        config["state_dir"] = str(root / "state")
    elif case == "ancestor_state":
        config["state_dir"] = str(tmp_path)
    elif case == "in_tree_config":
        path = root / "config.json"
    elif case == "bad_boolean":
        config["enable_execution"] = "false"
    elif case == "bad_timeout":
        config["cursor_timeout_seconds"] = True
    elif case == "bad_dispatch":
        config["dispatch_ids"] = [True]
    elif case == "relative":
        config["worktree"] = "relative/path"
    elif case == "unknown_key":
        config["shell"] = "do not execute"
    path.write_text(json.dumps(config))
    if case == "valid":
        assert b.config_file(path)["enable_execution"] is False
    else:
        with pytest.raises(b.Stop):
            b.config_file(path)


def test_file_alias_and_reparse(tmp_path, monkeypatch):
    target = tmp_path / "Example.md"
    target.write_text("safe")
    with pytest.raises(b.Stop, match="PATH_ALIAS"):
        b.file_at(tmp_path, "example.md")
    real = Path.lstat
    def attributes(path, *args, **kwargs):
        if path == target:
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400, st_nlink=1)
        return real(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", attributes)
    with pytest.raises(b.Stop, match="LINK_PATH"):
        b.file_at(tmp_path, "Example.md")


@pytest.mark.parametrize("path", ["../escape.py", "C:/temp/file.py", "//host/share/file.py",
    b.PREFIX + "tools/../app/a.py", b.PREFIX + "tools/a.py:ads", b.PREFIX + "tools/a.py.",
    b.PREFIX + "tools/CON.py", b.PREFIX + "tools/COM1.md", b.PREFIX + "tools/a\\b.py",
    b.PREFIX + "tools/a//b.py", b.PREFIX + "tools/.env", b.PREFIX + "tools/API_KEY.txt",
    b.PREFIX + "app/a.py", b.PREFIX + "deploy/a.py", ".github/workflows/a.yml",
    b.PREFIX + "docs/architecture/a.md", b.PREFIX + "docs/atdd/ACTIVE_INCREMENT",
    b.SELF, b.CHECKER, b.PREFIX + "tools/tool.exe", b.PREFIX + "tools/a~1.py",
    b.PREFIX + "tools/__init__.py", b.PREFIX + "tools/ai_gateway/profiles.py",
    b.PREFIX + "tools/opip_platform_backup.py", b.PREFIX + "tools/opip_platform_restore_verify.py",
    b.PREFIX + "tools/bridge_tasks/__init__.py", b.PREFIX + "tools/bridge_tasks/conftest.py",
    b.PREFIX + "tests/__init__.py", b.PREFIX + "tests/conftest.py",
    b.PREFIX + "tests/nested/conftest.py", b.PREFIX + "tests/nested/test_escape.py",
    b.PREFIX + "tests/test_escape/helper.py"])
def test_unsafe_paths(path):
    with pytest.raises(b.Stop):
        b.safe_path(path)


@pytest.mark.parametrize("path", [
    b.PREFIX + "tools/bridge_tasks/format_report.py",
    b.PREFIX + "tests/test_bridge_task.py",
    b.PREFIX + "docs/engineering/bridge-task.md",
])
def test_explicit_editable_namespaces(path):
    assert b.safe_path(path) == path


def make_repo(tmp_path):
    common, root = tmp_path / "common", tmp_path / "tree"
    common.mkdir()
    def command(cwd, *args):
        subprocess.run(["git", "-c", "user.name=Bridge Test", "-c", "user.email=bridge@example.invalid",
                        *args], cwd=cwd, check=True, capture_output=True)
    command(common, "init", "-b", "main")
    command(common, "config", "core.autocrlf", "false")
    files = {"AGENTS.md": "Engineering only.", "CLAUDE.md": "No runtime.", b.CHECKER: "# pinned checker\n",
             b.PREFIX + "docs/architecture/pin.md": "Frozen.", b.ACTIVE: "ATDD-EXAMPLE\n",
             PATH: "before\n"}
    contract = """INCREMENT:
ATDD-EXAMPLE
OWNER-APPROVED INTENT:
Engineering spelling fix only.
ARCHITECTURE REFERENCES:
AGENTS.md
APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a misspelling
WHEN:
fixed
THEN:
correct spelling
EXPLICITLY OUT OF SCOPE:
Everything else.
FROZEN BOUNDARIES:
No runtime or credentials.
ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_example.py::test_example
IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/example.md
DEFERRED DISCOVERIES:
None.
UNAPPROVED SCOPE CHANGES:
NONE
"""
    contract_path = b.PREFIX + "docs/atdd/scope-contracts/ATDD-EXAMPLE.md"
    files[contract_path] = contract
    files[b.PREFIX + "tests/test_example.py"] = ('import pytest\n@pytest.mark.acceptance\n'
        'def test_example():\n    """ATDD-EXAMPLE/AC-001: spelling."""\n    assert True\n')
    for name, value in files.items():
        target = common / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value.encode("utf-8"))
    command(common, "add", ".")
    command(common, "commit", "-m", "synthetic fixture")
    command(common, "remote", "add", "origin", f"https://github.com/{b.REPO}.git")
    command(common, "worktree", "add", "-b", "feature/bridge-example", str(root))
    task = fixture_task()
    task.update(head=b.git(root, "rev-parse", "HEAD"), authority_sha256=b.authority_hash(root),
                contract_sha256=b.digest((root / contract_path).read_bytes()))
    return root, task


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["valid", "dirty", "ignored", "head", "branch", "remote", "authority",
                                      "contract", "inactive", "scope", "hardlink", "alias", "primary"])
def test_repository_and_scope(tmp_path, case):
    """ATDD-BRIDGE-v1/AC-002: repository and scope drift fail before dispatch."""
    root, task = make_repo(tmp_path)
    if case in {"dirty", "ignored"}:
        (root / ("unexpected.txt" if case == "dirty" else ".env")).write_text("unexpected")
    elif case in {"head", "branch", "authority", "contract"}:
        key = {"head": "head", "branch": "branch", "authority": "authority_sha256", "contract": "contract_sha256"}[case]
        task[key] = "feature/other" if case == "branch" else "d" * (40 if case == "head" else 64)
    elif case == "remote":
        b.git(root, "remote", "set-url", "origin", "https://github.com/fork/repo.git")
    elif case == "inactive":
        task["increment"] = "ATDD-NOT-ACTIVE"
    elif case == "scope":
        task["files"] = [b.PREFIX + "tools/unapproved.py"]
    elif case == "hardlink":
        os.link(root / PATH, tmp_path / "external.md")
    elif case == "alias":
        task["files"] = [PATH.replace("example.md", "EXAMPLE.md")]
    elif case == "primary":
        root = tmp_path / "common"
    if case == "valid":
        assert b.repository(root, task)[0][PATH]["content"] == "before\n"
    else:
        with pytest.raises(b.Stop):
            b.repository(root, task)


class API:
    def __init__(self, snap):
        self.snap = snap
    def snapshot(self, issue):
        return copy.deepcopy(self.snap)


def run_fixture(tmp_path, monkeypatch):
    root, task = make_repo(tmp_path)
    config = {"worktree": str(root), "state_dir": str(tmp_path / "state"), "dispatch_ids": [],
              "enable_execution": True}
    original = b.control
    monkeypatch.setattr(b, "control", lambda snap, cid, ids: original(snap, cid, ids, NOW))
    proposal = {"conflict": False, "edits": [{"path": PATH,
                 "before_sha256": b.digest(b"before\n"), "content": "after\n"}]}
    return root, config, API(snapshot(task)), proposal


@pytest.mark.acceptance
def test_dry_run(tmp_path, monkeypatch):
    """ATDD-BRIDGE-v1/AC-003: dry run does not invoke, claim, edit or post."""
    root, config, api, _ = run_fixture(tmp_path, monkeypatch)
    def forbidden(*args):
        pytest.fail("dry-run called agent")
    b.run(config, 1, 10, api=api, agent=forbidden)
    assert (root / PATH).read_bytes() == b"before\n"
    assert not (Path(config["state_dir"]) / "receipts.sqlite3").exists()
    assert "DRY_RUN_VALID" in (Path(config["state_dir"]) / "status.jsonl").read_text()


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["environment", "disabled", "missing_exe", "batch_wrapper", "hash_drift",
                                  "success", "bad_result", "nonzero", "timeout"])
def test_cursor_boundary(tmp_path, monkeypatch, case):
    """ATDD-BRIDGE-v1/AC-004: tool-denied, pinned, bounded, credential-separated Cursor call."""
    monkeypatch.setenv("CURSOR_API_KEY", "synthetic-cursor-auth")
    for key in ("GH_TOKEN", "KRAKEN_API_KEY", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "PATH"):
        monkeypatch.setenv(key, "synthetic-must-not-inherit")
    env = b.cursor_environment(tmp_path)
    assert all(k not in env for k in ("GH_TOKEN", "KRAKEN_API_KEY", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "PATH"))
    assert env["USERPROFILE"] == str(tmp_path)
    if case == "environment":
        monkeypatch.delenv("CURSOR_API_KEY")
        with pytest.raises(b.Stop, match="CURSOR_AUTH_MISSING"):
            b.cursor_environment(tmp_path)
        return
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"synthetic executable; never run")
    config = {"enable_execution": case != "disabled", "cursor_executable": str(exe),
              "cursor_sha256": b.digest(exe.read_bytes()), "state_dir": str(tmp_path),
              "worktree": str(tmp_path / "tree"),
              "cursor_timeout_seconds": 1}
    # AC-004/AC-016 scratch-trust proof: --trust is reachable ONLY for the
    # bridge-created disposable scratch, never the worktree, the state directory
    # itself, an arbitrary or nested directory, or a borrowed name. Each rejection
    # uses a fresh independent fixture so no guard can pass for a neighbour's reason.
    if case == "success":
        worktree = tmp_path / "tree"
        worktree.mkdir(exist_ok=True)
        base = [str(exe), "--print", "--mode", "ask", "--sandbox", "enabled",
                "--output-format", "json"]

        def make_scratch(parent, name="opip-cursor-proof"):
            scratch = Path(parent) / name
            (scratch / "config").mkdir(parents=True, exist_ok=True)
            (scratch / "config" / "cli-config.json").write_text("{}", encoding="utf-8")
            (scratch / ".cursor").mkdir(exist_ok=True)
            (scratch / ".cursor" / "cli.json").write_text("{}", encoding="utf-8")
            return scratch

        def entries_of(path):
            return {item.name for item in Path(path).iterdir()}

        good = make_scratch(tmp_path)
        ok = b.scratch_trust_argv(good, [str(exe)], config, base)
        assert ok == base + ["--trust"] and ok.count("--trust") == 1
        assert ok[ok.index("--sandbox") + 1] == "enabled"
        assert b.DENY == ["Shell(*)", "Read(*)", "Write(*)", "WebFetch(*)"]
        for blocked in ("--force", "--yolo", "--approve-mcps"):
            assert blocked not in ok

        # Double grant refused while the valid scratch is otherwise still valid.
        assert entries_of(good) == {"config", ".cursor"}
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(good, [str(exe)], config, ok)
        assert entries_of(good) == {"config", ".cursor"}

        # Worktree relationship refused with an otherwise-valid direct-child scratch.
        # state_dir and worktree are normally disjoint, so the worktree guard is
        # exercised by pointing config.worktree at the state directory itself.
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(good, [str(exe)], dict(config, worktree=str(tmp_path)), base)
        # A scratch physically inside the worktree is also refused.
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(make_scratch(worktree), [str(exe)], config, base)

        # Borrowed name refused even though the directory exists with valid config.
        borrowed = make_scratch(tmp_path, "not-bridge-made")
        assert (borrowed / "config" / "cli-config.json").is_file()
        assert (borrowed / ".cursor" / "cli.json").is_file()
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(borrowed, [str(exe)], config, base)

        # state_dir itself refused.
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(tmp_path, [str(exe)], config, base)

        # Outside-state scratch refused.
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(make_scratch(outside), [str(exe)], config, base)

        # Extra content refused.
        extra = make_scratch(tmp_path, "opip-cursor-extra")
        (extra / "prompt.json").write_text("{}", encoding="utf-8")
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(extra, [str(exe)], config, base)

        # Nested scratch built under a SEPARATE parent; the valid scratch is untouched.
        separate_parent = make_scratch(tmp_path, "opip-cursor-outer")
        nested = make_scratch(separate_parent, "inner")
        assert (nested / "config" / "cli-config.json").is_file()
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(nested, [str(exe)], config, base)
        assert entries_of(good) == {"config", ".cursor"}

        # Missing configuration anchor refused.
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(
                good, [str(exe)],
                {k: v for k, v in config.items() if k != "worktree"}, base)

        # Symlink/reparse scratch refused.
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path, "is_symlink",
            lambda self: True if Path(self) == good else real_is_symlink(self))
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(good, [str(exe)], config, base)
        monkeypatch.setattr(Path, "is_symlink", real_is_symlink)
    if case == "missing_exe":
        exe.unlink()
    elif case == "batch_wrapper":
        exe = tmp_path / "agent.cmd"
        exe.write_text("echo unsafe wrapper")
        config["cursor_executable"] = str(exe)
    elif case == "hash_drift":
        config["cursor_sha256"] = "0" * 64
    calls = []
    class Process:
        returncode = 0
        pid = 42
        killed = False
        def __init__(self, argv, **kw):
            calls.append(argv)
            assert argv[1:] == ["--print", "--mode", "ask", "--sandbox", "enabled",
                                "--output-format", "json", "--trust"]
            for blocked in ("--force", "--yolo", "--approve-mcps"):
                assert blocked not in argv, blocked
            assert argv.count("--trust") == 1
            # cwd is exactly the verified disposable scratch for this invocation.
            assert kw["shell"] is False and kw["env"] == b.cursor_environment(kw["cwd"])
            policy = json.loads((kw["cwd"] / ".cursor/cli.json").read_text())
            assert policy["permissions"] == {"allow": [], "deny": b.DENY}
            assert json.loads(kw["stdin"].read())["task"] == fixture_task()
            result = {"type": "result", "subtype": "success", "is_error": False,
                      "result": '{"conflict":false,"edits":[]}'}
            if case == "bad_result":
                result["is_error"] = True
            kw["stdout"].write(json.dumps(result).encode())
            kw["stdout"].flush()
            if case == "nonzero":
                self.returncode = 1
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def poll(self):
            return None if case == "timeout" and not self.killed else self.returncode
        def kill(self):
            self.killed = True
        def wait(self, timeout=None):
            self.killed = True
            return self.returncode
    monkeypatch.setattr(b.subprocess, "Popen", Process)
    monkeypatch.setattr(b.subprocess, "run", lambda *a, **k: None)
    if case == "success":
        assert b.cursor(config, fixture_task(), {}, "contract")["conflict"] is False
    else:
        with pytest.raises(b.Stop):
            b.cursor(config, fixture_task(), {}, "contract")
    assert bool(calls) == (case in {"success", "bad_result", "nonzero", "timeout"})


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["success", "runtime_drift", "wrong_layout", "wrapper", "missing_entrypoint", "link"])
def test_cursor_packaged_windows_runtime(tmp_path, monkeypatch, case):
    """ATDD-BRIDGE-v1/AC-016: official Windows package runs pinned node+index, never wrappers."""
    monkeypatch.setenv("CURSOR_API_KEY", "synthetic-cursor-auth")
    runtime = tmp_path / "2026.09.28-64d2043"
    runtime.mkdir()
    node = runtime / "node.exe"
    entrypoint = runtime / "index.js"
    chunk = runtime / "1124.index.js"
    node.write_bytes(b"synthetic node")
    entrypoint.write_text("require('./1124.index.js')", encoding="utf-8")
    chunk.write_text("module.exports = {}", encoding="utf-8")
    (runtime / "cursorsandbox.exe").write_bytes(b"synthetic sandbox")
    running = runtime / ".running"
    running.mkdir()
    (running / "transient").write_text("one", encoding="utf-8")

    runtime_hash = b.cursor_runtime_digest(runtime)
    # The vendor's transient marker is deliberately the only excluded subtree.
    (running / "transient").write_text("two", encoding="utf-8")
    assert b.cursor_runtime_digest(runtime) == runtime_hash

    config = {
        "enable_execution": True,
        "cursor_executable": str(node),
        "cursor_sha256": b.digest(node.read_bytes()),
        "cursor_runtime_root": str(runtime),
        "cursor_runtime_sha256": runtime_hash,
        "state_dir": str(tmp_path),
        "worktree": str(tmp_path / "tree"),
        "cursor_timeout_seconds": 1,
    }
    if case == "runtime_drift":
        chunk.write_text("module.exports = {changed:true}", encoding="utf-8")
    elif case == "wrong_layout":
        other = tmp_path / "node.exe"
        other.write_bytes(node.read_bytes())
        config["cursor_executable"] = str(other)
        config["cursor_sha256"] = b.digest(other.read_bytes())
    elif case == "wrapper":
        wrapper = tmp_path / "agent.cmd"
        wrapper.write_text("@echo off", encoding="utf-8")
        config["cursor_executable"] = str(wrapper)
        config["cursor_sha256"] = b.digest(wrapper.read_bytes())
    elif case == "missing_entrypoint":
        entrypoint.unlink()

    if case == "link":
        real_lstat = Path.lstat
        def linked_lstat(self, *args, **kwargs):
            info = real_lstat(self, *args, **kwargs)
            if self == chunk:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=b.stat.S_IFLNK, st_size=info.st_size,
                                       st_file_attributes=0)
            return info
        monkeypatch.setattr(Path, "lstat", linked_lstat)

    calls = []
    class Process:
        returncode = 0
        pid = 42
        def __init__(self, argv, **kw):
            calls.append(argv)
            assert argv[:2] == [str(node), str(entrypoint)]
            assert argv[2:] == ["--print", "--mode", "ask", "--sandbox", "enabled",
                                "--output-format", "json", "--trust"]
            assert argv.count("--trust") == 1
            for blocked in ("--force", "--yolo", "--approve-mcps"):
                assert blocked not in argv, blocked
            assert kw["shell"] is False
            assert kw["env"]["CURSOR_INVOKED_AS"] == "agent.cmd"
            assert "PATH" not in kw["env"] and "GH_TOKEN" not in kw["env"]
            result = {"type": "result", "subtype": "success", "is_error": False,
                      "result": '{"conflict":false,"edits":[]}' }
            kw["stdout"].write(json.dumps(result).encode())
            kw["stdout"].flush()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def poll(self):
            return self.returncode
        def kill(self):
            self.returncode = -9
        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(b.subprocess, "Popen", Process)
    monkeypatch.setattr(b.subprocess, "run", lambda *a, **k: None)
    if case == "success":
        assert b.cursor(config, fixture_task(), {}, "contract")["conflict"] is False
    else:
        with pytest.raises(b.Stop):
            b.cursor(config, fixture_task(), {}, "contract")
    assert bool(calls) == (case == "success")

    if case == "success":
        config_file = tmp_path / "packaged.json"
        local = dict(config, worktree=str(tmp_path / "tree"), state_dir=str(tmp_path / "state"),
                     dispatch_ids=[])
        (tmp_path / "tree").mkdir()
        config_file.write_text(json.dumps(local), encoding="utf-8")
        assert b.config_file(config_file)["cursor_runtime_sha256"] == runtime_hash
        local.pop("cursor_runtime_sha256")
        config_file.write_text(json.dumps(local), encoding="utf-8")
        with pytest.raises(b.Stop, match="INVALID_SCHEMA"):
            b.config_file(config_file)


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["valid", "conflict", "extra", "duplicate", "delete", "binary", "hash", "changed",
                                  "outside", "secret", "partial", "add"])
def test_proposal_boundary(tmp_path, case, monkeypatch):
    """ATDD-BRIDGE-v1/AC-005: validate every edit before applying; preserve partial evidence."""
    root, task = make_repo(tmp_path)
    context, _ = b.repository(root, task)
    proposal = {"conflict": False, "edits": [{"path": PATH, "content": "after\n",
                                              "before_sha256": b.digest(b"before\n")}]}
    edit = proposal["edits"][0]
    if case == "conflict":
        proposal["conflict"] = True
    elif case == "extra":
        proposal["shell"] = "unsafe"
    elif case == "duplicate":
        proposal["edits"].append(copy.deepcopy(edit))
    elif case == "delete":
        edit["content"] = None
    elif case == "binary":
        edit["content"] = "\0"
    elif case == "hash":
        edit["before_sha256"] = "0" * 64
    elif case == "changed":
        (root / PATH).write_bytes(b"concurrent change")
    elif case == "outside":
        edit["path"] = b.PREFIX + "tools/unapproved.py"
    elif case == "secret":
        edit["content"] = "-----BEGIN RSA PRIVATE KEY-----"
    elif case == "add":
        (root / PATH).unlink()
        edit["before_sha256"] = None
        context[PATH]["before_sha256"] = None
    if case in {"valid", "partial", "add"}:
        edits = b.proposal_edits(proposal, root, task, context)
        if case == "partial":
            target2 = root / b.PREFIX / "docs/engineering/new.md"
            edits.append((target2, b"new", None))
            original = os.replace
            def fail_second(src, dst):
                if dst == target2:
                    raise OSError("simulated disk full")
                return original(src, dst)
            monkeypatch.setattr(os, "replace", fail_second)
            with pytest.raises(OSError):
                b.apply_edits(edits)
            assert not target2.exists()
        else:
            b.apply_edits(edits)
        assert (root / PATH).read_bytes() == b"after\n"
    else:
        before = (root / PATH).read_bytes()
        with pytest.raises(b.Stop):
            b.proposal_edits(proposal, root, task, context)
        assert (root / PATH).read_bytes() == before


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["success", "duplicate", "lock", "agent_failure", "revoke", "dirty", "interrupt", "receipt_corrupt"])
def test_receipts_and_failures(tmp_path, monkeypatch, case):
    """ATDD-BRIDGE-v1/AC-006: durable at-most-one attempt and exclusive lock survive ambiguity."""
    root, config, api, proposal = run_fixture(tmp_path, monkeypatch)
    state = Path(config["state_dir"])
    calls = []
    def agent(*args):
        calls.append(1)
        if case == "agent_failure":
            raise b.Stop("CURSOR_FAILED")
        if case == "interrupt":
            raise KeyboardInterrupt
        if case == "revoke":
            api.snap[2].append(comment(12, '/opip-revoke\n{"task_comment_id":10}'))
        if case == "dirty":
            (root / "concurrent.txt").write_text("preserve")
        return proposal
    if case == "lock":
        with b.run_lock(state):
            with pytest.raises(b.Stop, match="RUN_LOCKED"):
                b.run(config, 1, 10, True, api, agent)
        assert not calls
        return
    if case == "receipt_corrupt":
        state.mkdir()
        (state / "receipts.sqlite3").write_bytes(b"not sqlite")
        with pytest.raises(b.sqlite3.DatabaseError):
            b.run(config, 1, 10, True, api, agent)
        assert not calls
        return
    if case in {"success", "duplicate"}:
        b.run(config, 1, 10, True, api, agent)
        assert (root / PATH).read_bytes() == b"after\n"
        if case == "duplicate":
            # Restore only this synthetic fixture to exercise receipt gating itself.
            (root / PATH).write_bytes(b"before\n")
            with pytest.raises(b.Stop, match="ALREADY_ATTEMPTED"):
                b.run(config, 1, 10, True, api, agent)
        expected = "APPLIED_UNTESTED"
    else:
        with pytest.raises((b.Stop, KeyboardInterrupt)):
            b.run(config, 1, 10, True, api, agent)
        assert (root / PATH).read_bytes() == b"before\n"
        expected = "FAILED_OWNER_RECOVERY"
    with b.receipt_db(state) as connection:
        assert connection.execute("SELECT status FROM receipts WHERE task=10").fetchone()[0] == expected
        with pytest.raises(b.Stop, match="ALREADY_ATTEMPTED"):
            b.claim(connection, 10, "different digest cannot retry")
    assert len(calls) == 1
    assert not (state / "run.lock").exists()
    log = (state / "status.jsonl").read_text()
    assert fixture_task()["instructions"] not in log
    assert "before\n" not in log


@pytest.mark.acceptance
def test_continuous_discovery(tmp_path, monkeypatch):
    """ATDD-BRIDGE-v1/AC-011: one control issue, ordered discovery, no replay after restart."""
    state = tmp_path / "state"
    config = {"worktree": str(tmp_path / "tree"), "state_dir": str(state),
              "dispatch_ids": [], "enable_execution": True}

    def task_comment(identifier, author=1, edited=False):
        item = comment(identifier, "/opip-task\n" + json.dumps(fixture_task()), author=author)
        if edited:
            item["updated_at"] = "2026-09-28T11:01:00Z"
        return item

    def status_comment(identifier, marker_task):
        return status_comment_for(identifier, marker_task)

    noise = comment(11, "just chatting")
    ordered = [task_comment(13), noise, task_comment(10), status_comment(12, 10)]
    snap = (1, {"state": "open"}, ordered)

    # Only exact /opip-task envelopes from allowed authors are candidates, in id order.
    assert b.task_candidates(ordered, {1}, set()) == [10, 13]
    assert b.task_candidates([status_comment(12, 10)], {1}, set()) == []
    assert b.task_candidates([task_comment(10, author=999)], {1}, set()) == []
    assert b.task_candidates([comment(9, "/opip-task-ish\n{}")], {1}, set()) == []

    calls = []
    in_flight = []

    def fake_run(config_, issue, comment_id, execute=False, api=None, agent=None, status_api=None):
        assert not in_flight, "tasks must run one at a time"
        in_flight.append(comment_id)
        calls.append(comment_id)
        try:
            if comment_id == 13:
                raise b.Stop("OWNER_REVOKED")
        finally:
            in_flight.pop()

    real_run = b.run
    monkeypatch.setattr(b, "run", fake_run)
    handled = b.poll_once(config, 1, execute=True, api=SnapAPI(snap))
    assert calls == [10, 13]
    assert handled == [10]
    assert in_flight == []
    # A restart does not replay the handled task or the rejected one.
    calls.clear()
    assert b.poll_once(config, 1, execute=True, api=SnapAPI(snap)) == []
    assert calls == []

    # A durable receipt alone is enough to prevent replay.
    receipt_state = tmp_path / "receipts"
    receipt_state.mkdir()
    with b.receipt_db(receipt_state) as connection:
        b.claim(connection, 10, "digest")
    calls.clear()
    b.poll_once(dict(config, state_dir=str(receipt_state)), 1, execute=True, api=SnapAPI(snap))
    assert 10 not in calls

    # A transient GitHub read failure consumes nothing.
    transient = tmp_path / "transient"
    with pytest.raises(b.Stop, match="HOST_COMMAND_FAILED"):
        b.poll_once(dict(config, state_dir=str(transient)), 1, execute=True,
                    api=SnapAPI(snap, failure="HOST_COMMAND_FAILED"))
    assert not b.discovery_path(transient).exists()

    # Edited and revoked tasks are refused before Cursor, through the real control path.
    root, task = make_repo(tmp_path)
    original = b.control
    monkeypatch.setattr(b, "control", lambda snap_, cid, ids: original(snap_, cid, ids, NOW))
    monkeypatch.setattr(b, "run", real_run)

    def forbidden_agent(*_args):
        pytest.fail("Cursor must not run for an ineligible task")

    base = snapshot(task)
    edited = copy.deepcopy(base)
    edited[2][0]["updated_at"] = "2026-09-28T11:01:00Z"
    edited_config = {"worktree": str(root), "state_dir": str(tmp_path / "edited"),
                     "dispatch_ids": [], "enable_execution": True}
    assert b.poll_once(edited_config, 1, execute=True, api=SnapAPI(edited),
                       agent=forbidden_agent) == []

    revoked = copy.deepcopy(base)
    revoked[2].insert(0, comment(12, '/opip-revoke\n{"task_comment_id":10}'))
    revoked_config = {"worktree": str(root), "state_dir": str(tmp_path / "revoked"),
                      "dispatch_ids": [], "enable_execution": True}
    assert b.poll_once(revoked_config, 1, execute=True, api=SnapAPI(revoked),
                       agent=forbidden_agent) == []
    assert (root / PATH).read_bytes() == b"before\n"

    # Explicit one-shot --task-comment mode still works, and watch rejects it.
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "worktree": str(tmp_path / "wt"), "state_dir": str(tmp_path / "st"),
        "dispatch_ids": [], "enable_execution": False, "cursor_executable": "none.exe",
        "cursor_sha256": "none", "cursor_timeout_seconds": 300}))
    explicit = []
    monkeypatch.setattr(b, "run", lambda config_, issue, comment_id, execute=False,
                        status_api=None: explicit.append((issue, comment_id, execute)))
    assert b.main(["--config", str(config_path), "--issue", "7", "--task-comment", "10",
                   "--dry-run"]) == 0
    assert explicit == [(7, 10, False)]
    assert b.main(["--config", str(config_path), "--issue", "7", "--watch",
                   "--task-comment", "10"]) == 2

    # Bounded polling interval and clean interruption.
    with pytest.raises(b.Stop, match="POLL_INTERVAL_OUT_OF_RANGE"):
        b.watch(config, 1, api=SnapAPI(snap), state=tmp_path / "w0", polls=1,
                poll_seconds=5, sleep=lambda _s: None)
    assert b.main(["--config", str(config_path), "--issue", "7", "--watch", "--dry-run",
                   "--poll-seconds", "5"]) == 2

    monkeypatch.setattr(b, "poll_once", lambda *a, **k: [])
    watch_state = tmp_path / "w1"
    assert b.watch(config, 1, state=watch_state, polls=1, poll_seconds=20,
                   sleep=lambda _s: None) == 1
    assert not (watch_state / "watch.lock").exists()

    def interrupt(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        b.watch(config, 1, state=tmp_path / "w2", polls=None, poll_seconds=20, sleep=interrupt)
    assert not (tmp_path / "w2" / "watch.lock").exists()


class SnapAPI:
    def __init__(self, snap, failure=None):
        self.snap = snap
        self.failure = failure
        self.calls = 0

    def snapshot(self, issue):
        self.calls += 1
        if self.failure:
            raise b.Stop(self.failure)
        return copy.deepcopy(self.snap)


@pytest.mark.acceptance
def test_github_status_reporting(tmp_path, monkeypatch):
    """ATDD-BRIDGE-v1/AC-012: one bounded, redacted, task-linked status comment."""
    class Writer:
        def __init__(self, failure=None):
            self.calls = []
            self.failure = failure
            self.counter = 500

        def __call__(self, method, path, payload):
            self.calls.append((method, path, payload))
            if self.failure:
                raise b.Stop(self.failure)
            if method == "POST":
                self.counter += 1
                return {"id": self.counter}
            return {"id": 4242}

    writer = Writer()
    status = b.GitHubStatus(11, api=SnapAPI((1, {"state": "open"}, [])), writer=writer,
                            state_dir=tmp_path, identity=lambda: STATUS_AUTHOR)

    status.announce(10, "accepted", branch="feature/bridge-example", head_sha="a" * 40,
                    increment="ATDD-EXAMPLE")
    method, path, payload = writer.calls[-1]
    assert method == "POST" and path == "issues/11/comments"
    body = payload["body"]
    assert body.startswith(b.status_marker(10))
    assert len(body.encode("utf-8")) <= b.MAX_STATUS_BODY
    record = json.loads(body.split("\n", 1)[1])
    assert set(record) == {"schema", "task_comment_id", "state", "branch", "head_sha",
                           "increment", "reason_code", "updated_at"}
    assert record["schema"] == "opip-local-agent-status/v1"
    assert record["task_comment_id"] == 10 and record["state"] == "accepted"
    assert record["increment"] == "ATDD-EXAMPLE" and record["reason_code"] is None
    assert fixture_task()["instructions"] not in body
    assert "C:\\" not in body and "ghp_" not in body and "\n" not in record["state"]
    created = len(writer.calls)

    # Later states update the same comment instead of adding new ones.
    status.announce(10, "running", branch="feature/bridge-example", head_sha="a" * 40,
                    increment="ATDD-EXAMPLE")
    status.announce(10, "applied", branch="feature/bridge-example", head_sha="a" * 40,
                    increment="ATDD-EXAMPLE")
    assert len(writer.calls) == created + 2
    assert [call[0] for call in writer.calls[created:]] == ["PATCH", "PATCH"]
    assert writer.calls[-1][1] == f"issues/comments/{writer.counter}"
    assert sum(1 for call in writer.calls if call[0] == "POST") == 1

    # Status is linked to the exact task, so a second task gets its own comment.
    status.announce(100, "accepted", branch="feature/bridge-example", head_sha="",
                    increment="ATDD-EXAMPLE")
    assert writer.calls[-1][0] == "POST"
    assert writer.calls[-1][2]["body"].startswith(b.status_marker(100))
    assert not writer.calls[-1][2]["body"].startswith(b.status_marker(10))
    assert sum(1 for call in writer.calls if call[0] == "POST") == 2

    # A restart reuses the existing linkage instead of creating a duplicate.
    existing = [status_comment_for(77, 10)]
    restart_writer = Writer()
    restart = b.GitHubStatus(11, api=SnapAPI((1, {"state": "open"}, existing)),
                             writer=restart_writer, state_dir=tmp_path,
                             identity=lambda: STATUS_AUTHOR)
    restart.announce(10, "applied", branch="feature/bridge-example", head_sha="a" * 40,
                     increment="ATDD-EXAMPLE", comments=existing)
    assert [call[0] for call in restart_writer.calls] == ["PATCH"]
    assert restart_writer.calls[0][1] == "issues/comments/77"

    # Only fixed reason codes and bounded validated fields are accepted.
    with pytest.raises(b.Stop):
        b.status_payload(10, "accepted", reason_code="leak C:\\Users\\owner")
    with pytest.raises(b.Stop):
        b.status_payload(10, "not-a-real-state")
    with pytest.raises(b.Stop):
        b.status_payload(10, "accepted", branch="feature/" + "x" * 200)
    with pytest.raises(b.Stop):
        b.status_payload(10, "accepted", head_sha="not-a-sha")

    # A GitHub write failure fails closed.
    failing = b.GitHubStatus(11, api=SnapAPI((1, {"state": "open"}, [])),
                             writer=Writer("HOST_COMMAND_FAILED"), state_dir=tmp_path,
                             identity=lambda: STATUS_AUTHOR)
    with pytest.raises(b.Stop, match="STATUS_WRITE_FAILED"):
        failing.announce(10, "applied", branch="feature/bridge-example", head_sha="a" * 40,
                         increment="ATDD-EXAMPLE")

    # Bridge status comments are never discovered as tasks.
    assert b.task_candidates([status_comment_for(10, 10)], {1}, set()) == []

    # In the live path a failed status write never becomes an applied success.
    scratch = tmp_path / "run"
    scratch.mkdir()
    root, config, api, proposal = run_fixture(scratch, monkeypatch)
    blocked = b.GitHubStatus(1, api=api, writer=Writer("HOST_COMMAND_FAILED"),
                             state_dir=Path(config["state_dir"]),
                             identity=lambda: STATUS_AUTHOR)
    with pytest.raises(b.Stop, match="STATUS_WRITE_FAILED"):
        b.run(config, 1, 10, True, api, lambda *a: proposal, status_api=blocked)
    assert (root / PATH).read_bytes() == b"before\n"


def status_comment_for(identifier, marker_task, author=STATUS_AUTHOR):
    payload = b.status_payload(marker_task, "accepted", branch="feature/bridge-example",
                               increment="ATDD-EXAMPLE")
    return comment(identifier,
                   b.status_marker(marker_task) + "\n" + json.dumps(payload, sort_keys=True),
                   author=author)


@pytest.mark.acceptance
def test_status_comment_identity_is_verified(tmp_path):
    """ATDD-BRIDGE-v1/AC-012: only the authenticated status writer may own bridge status."""
    ATTACKER = 999

    class Writer:
        def __init__(self, failure=None):
            self.calls = []
            self.failure = failure
            self.counter = 700

        def __call__(self, method, path, payload):
            self.calls.append((method, path, payload))
            if self.failure:
                raise b.Stop(self.failure)
            if method == "POST":
                self.counter += 1
                return {"id": self.counter}
            return {"id": 4242}

    def make(writer, comments=(), identity=lambda: STATUS_AUTHOR):
        return b.GitHubStatus(11, api=SnapAPI((1, {"state": "open"}, list(comments))),
                              writer=writer, state_dir=tmp_path, identity=identity)

    # (a) An attacker marker that appears before any bridge comment is ignored.
    attacker = status_comment_for(50, 10, author=ATTACKER)
    writer = Writer()
    make(writer, [attacker]).announce(10, "accepted", branch="feature/bridge-example",
                                      head_sha="a" * 40, increment="ATDD-EXAMPLE",
                                      comments=[attacker])
    # (b) The bridge therefore creates its own comment instead of touching the attacker's.
    assert [call[0] for call in writer.calls] == ["POST"]
    assert writer.calls[0][2]["body"].startswith(b.status_marker(10))
    bridge_id = writer.counter
    assert bridge_id != attacker["id"]

    # (d)/(e) An attacker marker after the bridge comment is ignored: the bridge still
    # updates its own comment and never PATCHes the attacker's.
    after = [status_comment_for(bridge_id, 10), status_comment_for(90, 10, author=ATTACKER)]
    restart_writer = Writer()
    make(restart_writer, after).announce(10, "applied", branch="feature/bridge-example",
                                         head_sha="a" * 40, increment="ATDD-EXAMPLE",
                                         comments=after)
    assert [call[0] for call in restart_writer.calls] == ["PATCH"]
    assert restart_writer.calls[0][1] == f"issues/comments/{bridge_id}"
    assert all("90" not in call[1] for call in restart_writer.calls)

    # (c) A restart locates only the bridge-authored marker even when the attacker's
    # marker has a lower id and would otherwise be found first.
    ordered = [status_comment_for(20, 10, author=ATTACKER), status_comment_for(88, 10)]
    located_writer = Writer()
    make(located_writer, ordered).announce(10, "applied", branch="feature/bridge-example",
                                           head_sha="a" * 40, increment="ATDD-EXAMPLE",
                                           comments=ordered)
    assert [call[0] for call in located_writer.calls] == ["PATCH"]
    assert located_writer.calls[0][1] == "issues/comments/88"

    # A foreign marker for a different task never collides with this task's marker.
    assert b.status_comment_id([status_comment_for(30, 99, author=ATTACKER)], 10,
                               STATUS_AUTHOR) is None
    assert b.status_comment_id([status_comment_for(30, 10, author=ATTACKER)], 10,
                               STATUS_AUTHOR) is None
    assert b.status_comment_id([status_comment_for(30, 10)], 10, STATUS_AUTHOR) == 30

    # (f) An authenticated identity lookup failure fails closed: no POST, no PATCH.
    def broken_identity():
        raise b.Stop("STATUS_IDENTITY_UNKNOWN")

    with pytest.raises(b.Stop, match="STATUS_IDENTITY_UNKNOWN"):
        make(Writer(), identity=broken_identity).status_identity()
    failing_writer = Writer()
    with pytest.raises(b.Stop, match="STATUS_WRITE_FAILED"):
        make(failing_writer, identity=broken_identity).announce(
            10, "applied", branch="feature/bridge-example", head_sha="a" * 40,
            increment="ATDD-EXAMPLE")
    assert failing_writer.calls == []

    # An unauthenticated/missing identity is never trusted as ownership.
    with pytest.raises(b.Stop, match="STATUS_IDENTITY_UNKNOWN"):
        b.status_comment_id([status_comment_for(30, 10)], 10, None)


@pytest.mark.acceptance
def test_malformed_task_envelope_is_permanent():
    """ATDD-BRIDGE-v1/AC-001: a malformed task body is permanent; unattributable decisions are skipped."""
    # A malformed or oversized task body never becomes valid on a later poll.
    for bad in ("{ not json", "x" * (b.MAX_BYTES + 1)):
        snap = (1, {"state": "open"}, [comment(10, "/opip-task\n" + bad)])
        with pytest.raises(b.Stop, match="^INVALID_TASK_ENVELOPE$"):
            b.control(snap, 10, [], NOW)

    # A parseable but structurally wrong body is a permanent schema refusal too.
    for body in ('/opip-task\n"just a string"', "/opip-task\n[1,2,3]",
                 "/opip-task\n" + json.dumps({"schema": 1})):
        snap = (1, {"state": "open"}, [comment(10, body)])
        with pytest.raises(b.Stop) as error:
            b.control(snap, 10, [], NOW)
        assert str(error.value) not in b.RETRYABLE_REASONS

    # A malformed OWNER decision is unattributable: it never authorizes anything and
    # must never permanently poison the task under evaluation.
    base = snapshot()
    base[2][1]["body"] = "/opip-approve\n{ not json"
    with pytest.raises(b.Stop, match="^OWNER_APPROVAL_REQUIRED$"):
        b.control(base, 10, [], NOW)
    assert "OWNER_APPROVAL_REQUIRED" in b.RETRYABLE_REASONS

    # A malformed approval for another task (99) leaves valid approved task 10 executable.
    noise = snapshot()
    noise[2].insert(0, comment(5, "/opip-approve\n{ not json"))
    noise[2].insert(1, comment(6, '/opip-approve\n["not", "an", "object"]'))
    noise[2].insert(2, comment(7, '/opip-approve\n{"task_comment_id": 99, "task_sha256": "%s"}' % ("e" * 64)))
    assert b.control(noise, 10, [], NOW)[0] == fixture_task()

    # A structurally invalid decision bound to THIS task still fails closed.
    bound = snapshot()
    bound[2][1]["body"] = '/opip-approve\n{"task_comment_id": 10}'
    with pytest.raises(b.Stop):
        b.control(bound, 10, [], NOW)

    # strict_json keeps its transient code, so GitHub transport parsing stays retryable.
    with pytest.raises(b.Stop, match="^INVALID_JSON$"):
        b.strict_json("{ not json")
    assert "INVALID_JSON" in b.RETRYABLE_REASONS and "OVERSIZED_JSON" in b.RETRYABLE_REASONS
    assert not (b.PERMANENT_ENVELOPE_REASONS & b.RETRYABLE_REASONS)
    assert b.INVALID_TASK_ENVELOPE not in b.RETRYABLE_REASONS
    assert b.INVALID_DECISION_ENVELOPE not in b.RETRYABLE_REASONS
    # The envelope translation only rewrites parse codes, not other refusals.
    assert b.envelope(comment(10, "/opip-task\n{}"), "/opip-task") == {}


@pytest.mark.acceptance
def test_permanent_malformed_task_is_not_retried(tmp_path, monkeypatch):
    """ATDD-BRIDGE-v1/AC-011: a permanently malformed task is rejected once and never retried."""
    state = tmp_path / "state"
    config = {"worktree": str(tmp_path / "tree"), "state_dir": str(state),
              "dispatch_ids": [], "enable_execution": True}

    malformed = comment(10, "/opip-task\n{ not json")
    valid = comment(20, "/opip-task\n" + json.dumps(fixture_task()), author=1)
    snap = (1, {"state": "open"}, [malformed, valid])
    seen = []
    permanent = {10: b.INVALID_TASK_ENVELOPE, 30: b.INVALID_TASK_ENVELOPE}

    def fake_run(config_, issue, comment_id, execute=False, api=None, agent=None,
                 status_api=None):
        seen.append(comment_id)
        if comment_id in permanent:
            raise b.Stop(permanent[comment_id])

    monkeypatch.setattr(b, "run", fake_run)

    # (e) The malformed task is refused once; the valid later task is still processed.
    assert b.poll_once(config, 1, execute=True, api=SnapAPI(snap)) == [20]
    assert seen == [10, 20]

    # (a)/(c) A later poll does not reconsider the permanently malformed comment.
    seen.clear()
    assert b.poll_once(config, 1, execute=True, api=SnapAPI(snap)) == []
    assert 10 not in seen
    disposition = b.load_discovery(b.discovery_path(state))["dispositions"]["10"]
    assert disposition["state"] == "REJECTED"
    assert disposition["reason"] == b.INVALID_TASK_ENVELOPE

    # (d) Genuine transient GitHub/transport failures remain retryable and consume nothing.
    transient = tmp_path / "transient"
    transient_config = dict(config, state_dir=str(transient))
    with pytest.raises(b.Stop, match="HOST_COMMAND_FAILED"):
        b.poll_once(transient_config, 1, execute=True,
                    api=SnapAPI(snap, failure="HOST_COMMAND_FAILED"))
    assert not b.discovery_path(transient).exists()

    # (b) An oversized task envelope is likewise permanent, not retried.
    huge = tmp_path / "huge"
    huge_config = dict(config, state_dir=str(huge))
    oversized = (1, {"state": "open"},
                 [comment(30, "/opip-task\n" + "x" * (b.MAX_BYTES + 1))])
    b.poll_once(huge_config, 1, execute=True, api=SnapAPI(oversized))
    huge_disposition = b.load_discovery(b.discovery_path(huge))["dispositions"]["30"]
    assert huge_disposition["state"] == "REJECTED"
    assert huge_disposition["reason"] == b.INVALID_TASK_ENVELOPE


@pytest.mark.acceptance
def test_completed_increment_pointer_is_not_pinned():
    """ATDD-BRIDGE-v1/AC-014: a completed increment must not freeze the movable ATDD pointer."""
    r3_test = b.APP / "tests" / "test_opip_r3_f3_ignition_detector.py"
    source = r3_test.read_text(encoding="utf-8")

    # The stale lifecycle pin is gone: this test no longer reads or asserts the pointer.
    assert "ACTIVE_INCREMENT" not in source

    # The completed increment stays historically identifiable through its own contract.
    contract = (b.APP / "docs" / "atdd" / "scope-contracts"
                / "ATDD-R3-F3-ignition-implementation.md")
    assert contract.is_file()
    lines = contract.read_text(encoding="utf-8").splitlines()
    assert lines[0].strip() == "INCREMENT:"
    assert lines[1].strip() == "ATDD-R3-F3-ignition-implementation"

    # No substantive isolation, feature-bus or authority assertion was weakened.
    for required in (
        'OPIP_FEATURE_BUS_MODE: "off"',
        "FORBIDDEN_MODULE_PREFIXES",
        "FORBIDDEN_IMPORT_ROOTS",
        "AUTHORITY_TOKENS",
        "run_cycle.py",
        "run_feature_bus_pilot.py",
        "assert imported_modules(source).isdisjoint(",
        'assert "opip.detectors" not in text',
        "SCOPE_CONTRACT_PATH.is_file()",
    ):
        assert required in source, required


@pytest.mark.acceptance
def test_bridge_smoke_target_is_authorized():
    """ATDD-BRIDGE-v1/AC-015: one harmless engineering document is the bounded activation target."""
    target = b.PREFIX + "docs/engineering/bridge-smoke-test.md"

    # The smoke target is allowed by the bridge's existing engineering-doc boundary.
    assert b.safe_path(target) == target

    # It is also explicitly authorized by the active ATDD implementation map.
    contract = b.parse_scope_contract(
        (b.APP / "docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md").read_text(encoding="utf-8")
    )
    assert target in contract.implementation_map["AC-015"]

    # The file is intentionally inert development documentation, not executable authority.
    smoke = b.APP / "docs/engineering/bridge-smoke-test.md"
    assert smoke.is_file()
    text = smoke.read_text(encoding="utf-8")
    assert "development-only" in text
    assert "no runtime, trading, deployment, merge, or production authority" in text


@pytest.mark.parametrize("case", ["bridge_scratch", "worktree", "arbitrary", "state_dir_itself",
                                  "symlink_scratch", "outside_state_dir", "nested", "borrowed_name",
                                  "extra_content", "missing_config_key", "link_in_scratch"])
def test_scratch_trust_boundary(tmp_path, monkeypatch, case):
    """Focused coverage: --trust is granted only to the bridge-created scratch.

    AC-004 acceptance proof for the same behaviour lives inside test_cursor_boundary.
    """
    monkeypatch.setenv("CURSOR_API_KEY", "synthetic-cursor-auth")
    state = tmp_path / "state"
    state.mkdir()
    worktree = tmp_path / "tree"
    worktree.mkdir()
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"synthetic executable; never run")
    config = {"enable_execution": True, "cursor_executable": str(exe),
              "cursor_sha256": b.digest(exe.read_bytes()),
              "state_dir": str(state), "worktree": str(worktree),
              "cursor_timeout_seconds": 1}
    launch = [str(exe)]
    base = [str(exe), "--print", "--mode", "ask", "--sandbox", "enabled",
            "--output-format", "json"]

    # A genuine bridge-shaped scratch: direct child of state_dir, private config only.
    def bridge_scratch(root, name="opip-cursor-abc123"):
        scratch = Path(root) / name
        (scratch / "config").mkdir(parents=True)
        (scratch / "config" / "cli-config.json").write_text(json.dumps({
            "version": 1, "editor.vimMode": False,
            "permissions": {"allow": [], "deny": b.DENY}}), encoding="utf-8")
        (scratch / ".cursor").mkdir()
        (scratch / ".cursor" / "cli.json").write_text(json.dumps({
            "permissions": {"allow": [], "deny": b.DENY}}), encoding="utf-8")
        return scratch

    scratch = bridge_scratch(state)
    target = scratch
    if case == "worktree":
        target = bridge_scratch(worktree, "opip-cursor-worktree")
    elif case == "arbitrary":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        target = bridge_scratch(elsewhere)
    elif case == "state_dir_itself":
        target = state
    elif case == "outside_state_dir":
        outside = tmp_path / "outside"
        outside.mkdir()
        target = bridge_scratch(outside)
    elif case == "nested":
        # Valid config, valid name, but nested rather than a direct child of state_dir.
        outer = bridge_scratch(state, "opip-cursor-outer")
        target = bridge_scratch(outer, "inner")
    elif case == "borrowed_name":
        target = bridge_scratch(state, "not-bridge-made")
    elif case == "extra_content":
        (scratch / "prompt.json").write_text("{}", encoding="utf-8")
    elif case == "missing_config_key":
        config.pop("worktree")
    elif case == "link_in_scratch":
        real_lstat = Path.lstat
        victim = scratch / "config" / "cli-config.json"

        def linked(self, *args, **kwargs):
            info = real_lstat(self, *args, **kwargs)
            if self == victim:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size,
                                       st_file_attributes=0x400, st_nlink=1)
            return info

        monkeypatch.setattr(Path, "lstat", linked)

    if case == "symlink_scratch":
        real_is_symlink = Path.is_symlink

        def symlinked(self):
            return True if self == target else real_is_symlink(self)

        monkeypatch.setattr(Path, "is_symlink", symlinked)

    if case == "bridge_scratch":
        argv = b.scratch_trust_argv(target, launch, config, base)
        assert argv == base + ["--trust"]
        assert argv.count("--trust") == 1
        # sandbox stays on, DENY unchanged, no prohibited bypasses
        assert argv[argv.index("--sandbox") + 1] == "enabled"
        for blocked in ("--force", "--yolo", "--approve-mcps"):
            assert blocked not in argv
        assert b.DENY == ["Shell(*)", "Read(*)", "Write(*)", "WebFetch(*)"]
        # a second application of the flag is refused
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(target, launch, config, argv)
    else:
        with pytest.raises(b.Stop, match="SCRATCH_TRUST_DENIED"):
            b.scratch_trust_argv(target, launch, config, base)


def test_scratch_trust_requires_completed_launch_pin(tmp_path, monkeypatch):
    """Focused coverage: --trust never precedes a validated packaged runtime pin.

    AC-016 acceptance proof for the same behaviour lives inside
    test_cursor_packaged_windows_runtime.
    """
    monkeypatch.setenv("CURSOR_API_KEY", "synthetic-cursor-auth")
    state = tmp_path / "state"
    state.mkdir()
    runtime = tmp_path / "2026.09.28-64d2043"
    runtime.mkdir()
    node = runtime / "node.exe"
    entry = runtime / "index.js"
    node.write_bytes(b"synthetic node")
    entry.write_text("module.exports = {}", encoding="utf-8")
    (runtime / "cursorsandbox.exe").write_bytes(b"synthetic sandbox")
    config = {"enable_execution": True, "cursor_executable": str(node),
              "cursor_sha256": b.digest(node.read_bytes()),
              "cursor_runtime_root": str(runtime),
              "cursor_runtime_sha256": b.cursor_runtime_digest(runtime),
              "state_dir": str(state), "worktree": str(tmp_path / "tree"),
              "cursor_timeout_seconds": 1}
    scratch = state / "opip-cursor-pkg"
    (scratch / "config").mkdir(parents=True)
    (scratch / "config" / "cli-config.json").write_text("{}", encoding="utf-8")
    (scratch / ".cursor").mkdir()
    (scratch / ".cursor" / "cli.json").write_text("{}", encoding="utf-8")

    launch, invoked_as = b.cursor_launch(config)
    assert launch == [str(node), str(entry)]
    assert invoked_as == "agent.cmd"
    base = launch + ["--print", "--mode", "ask", "--sandbox", "enabled", "--output-format", "json"]
    assert b.scratch_trust_argv(scratch, launch, config, base) == base + ["--trust"]

    # A drifted runtime cannot reach the --trust grant at all.
    (runtime / "index.js").write_text("module.exports = {changed:true}", encoding="utf-8")
    with pytest.raises(b.Stop):
        b.cursor_launch(config)

    # Execution disabled still short-circuits before any trust evaluation.
    off = dict(config, enable_execution=False)
    with pytest.raises(b.Stop, match="EXECUTION_DISABLED"):
        b.scratch_trust_argv(scratch, launch, off, base)


@pytest.mark.acceptance
def test_contract_and_runbook():
    """ATDD-BRIDGE-v1/AC-007: contract, detailed Windows instructions and frozen architecture remain traceable."""
    contract = b.parse_scope_contract((b.APP / "docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md").read_text())
    assert b.check_scope(contract, b.load_acceptance_index(b.APP / "tests")).passed
    doc = (b.APP / "docs/engineering/LOCAL_AGENT_BRIDGE_V1.md").read_text(encoding="utf-8")
    for term in ("PowerShell", "APPLIED_UNTESTED", "OWNER", "recovery", "--dry-run", "--execute", "Edge cases"):
        assert term in doc
    pinned = b.APP / "docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx"
    assert b.digest(pinned.read_bytes()) == "ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83"
