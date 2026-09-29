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
              "cursor_timeout_seconds": 1}
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
            assert "--force" not in argv and "--yolo" not in argv
            assert argv[1:] == ["--print", "--mode", "ask", "--sandbox", "enabled", "--output-format", "json"]
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
def test_contract_and_runbook():
    """ATDD-BRIDGE-v1/AC-007: contract, detailed Windows instructions and frozen architecture remain traceable."""
    contract = b.parse_scope_contract((b.APP / "docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md").read_text())
    assert b.check_scope(contract, b.load_acceptance_index(b.APP / "tests")).passed
    doc = (b.APP / "docs/engineering/LOCAL_AGENT_BRIDGE_V1.md").read_text(encoding="utf-8")
    for term in ("PowerShell", "APPLIED_UNTESTED", "OWNER", "recovery", "--dry-run", "--execute", "Edge cases"):
        assert term in doc
    pinned = b.APP / "docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx"
    assert b.digest(pinned.read_bytes()) == "ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83"
