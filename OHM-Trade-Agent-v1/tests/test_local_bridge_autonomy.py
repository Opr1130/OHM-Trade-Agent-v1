"""Registered autonomy, sandbox, publication and review authority tests."""
from __future__ import annotations

import copy
import json

import pytest

from tools import local_agent_bridge as b
from tools import local_bridge_autonomy as a
from tests.test_local_agent_bridge import NOW, PATH, comment, fixture_task, make_repo, snapshot


def policy():
    task = fixture_task()
    return {"schema": 1, "repo": b.REPO, "increment": task["increment"], "branch": task["branch"],
            "base_head": task["head"], "contract_sha256": task["contract_sha256"],
            "authority_sha256": task["authority_sha256"],
            "registered_tasks": [{"instructions": task["instructions"], "files": task["files"]}],
            "max_tasks": 3, "test_image": "opip-tests@sha256:" + "d" * 64,
            "test_command": ["python", "-B", "-m", "pytest", "-q", "tests/test_example.py"],
            "test_timeout_seconds": 300,
            "required_checks": ["test", "atdd scope", "semgrep/ci"]}


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["valid", "wrong_hash", "missing", "revoke", "edited", "expired", "conflict",
    "instructions", "files", "branch", "contract", "budget", "floating_image", "missing_check", "unknown_key",
    "malformed_noise"])
def test_registered_policy(tmp_path, case):
    """ATDD-BRIDGE-v1/AC-008: only immutable OWNER policy and exact registered work authorize autonomy."""
    value = policy()
    if case == "budget":
        value["max_tasks"] = 0
    elif case == "floating_image":
        value["test_image"] = "python:latest"
    elif case == "missing_check":
        value["required_checks"] = ["test"]
    elif case == "unknown_key":
        value["shell"] = "unsafe"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    if case in {"budget", "floating_image", "missing_check", "unknown_key"}:
        with pytest.raises(b.Stop):
            a.policy_file(path, tmp_path / "worktree")
        return
    value, hashed = a.policy_file(path, tmp_path / "worktree")
    approval = {"policy_sha256": "0" * 64 if case == "wrong_hash" else hashed,
                "expires_at": "2026-09-28T11:00:00Z" if case == "expired" else "2026-09-29T11:00:00Z",
                "architecture_clear": case != "conflict"}
    snap = snapshot()
    snap[2].append(comment(20, "/opip-authorize-increment\n" + json.dumps(approval)))
    if case == "missing":
        snap[2].pop()
    elif case == "revoke":
        snap[2].insert(0, comment(21, '/opip-revoke-increment\n' + json.dumps({"policy_sha256": hashed})))
    elif case == "edited":
        snap[2][-1]["updated_at"] = "2026-09-28T11:01:00Z"
    elif case == "malformed_noise":
        # Unattributable malformed policy decisions must not poison a valid approval.
        snap[2].insert(0, comment(22, "/opip-authorize-increment\n{ not json"))
        snap[2].insert(1, comment(23, '/opip-authorize-increment\n["not", "an", "object"]'))
    task = fixture_task()
    if case in {"instructions", "files", "branch", "contract"}:
        key = "contract_sha256" if case == "contract" else case
        task[key] = [b.PREFIX + "tools/unapproved.py"] if case == "files" else "unapproved change"
    if case in {"valid", "malformed_noise"}:
        a.authorize_policy(snap, hashed, NOW)
        a.admit_task(value, task, "unused")
    else:
        with pytest.raises(b.Stop):
            a.authorize_policy(snap, hashed, NOW)
            a.admit_task(value, task, "unused")


@pytest.mark.acceptance
def test_isolated_verification(tmp_path):
    """ATDD-BRIDGE-v1/AC-009: tests see a copied read-only source tree with bounded local container authority."""
    root, task = make_repo(tmp_path)
    export = tmp_path / "export"
    export.mkdir()
    a.copied_source(root, export, task["files"])
    assert (export / PATH).read_bytes() == b"before\n"
    assert not (export / ".git").exists()
    argv = a.docker_args("docker.exe", policy()["test_image"], export, "opip-bridge-test",
                         policy()["test_command"])
    for option, value in [("--host", a.DOCKER_HOST), ("--network", "none"), ("--cap-drop", "ALL"),
                           ("--user", "65534:65534"), ("--memory", "1g"), ("--pids-limit", "128")]:
        assert argv[argv.index(option) + 1] == value
    assert "--read-only" in argv and "--privileged" not in argv
    assert argv[argv.index("--mount") + 1].endswith("destination=/workspace,readonly")
    assert "--entrypoint" in argv
    assert all("TOKEN" not in arg and "KEY=" not in arg and "docker.sock" not in arg for arg in argv)


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["pass", "pending", "missing", "failure", "cancelled", "neutral", "rerun",
                                  "status_conflict", "stale_head", "fork", "closed", "ready_pr", "auto_merge", "local_finding"])
def test_review_gate(case):
    """ATDD-BRIDGE-v1/AC-010: APPROVE requires exact current PR identity and passing required checks."""
    checks = [{"id": 1, "name": "test", "status": "completed", "conclusion": "success"},
              {"id": 2, "name": "atdd scope", "status": "completed", "conclusion": "success"}]
    statuses = []
    pr = {"state": "open", "draft": True, "head": {"sha": "a" * 40, "ref": "feature/bridge-example", "repo": {"full_name": b.REPO}},
          "base": {"ref": "main", "repo": {"full_name": b.REPO}}}
    if case == "pending":
        checks[0]["status"] = "in_progress"
    elif case == "missing":
        checks.pop()
    elif case in {"failure", "cancelled", "neutral"}:
        checks[0]["conclusion"] = case
    elif case == "rerun":
        checks.insert(0, {"id": 3, "name": "test", "status": "in_progress", "conclusion": None})
    elif case == "status_conflict":
        statuses = [{"id": 99, "context": "test", "state": "failure"}]
    elif case == "stale_head":
        pr["head"]["sha"] = "b" * 40
    elif case == "fork":
        pr["head"]["repo"]["full_name"] = "fork/repo"
    elif case == "closed":
        pr["state"] = "closed"
    elif case == "ready_pr":
        pr["draft"] = False
    elif case == "auto_merge":
        pr["auto_merge"] = {"enabled_by": {"id": 1}}
    if case in {"stale_head", "fork", "closed", "ready_pr", "auto_merge"}:
        with pytest.raises(b.Stop, match="PR_HEAD_MISMATCH"):
            a.pr_identity(pr, "feature/bridge-example", "a" * 40)
    elif case == "local_finding":
        with pytest.raises(b.Stop, match="CONTRADICTORY_REVIEW"):
            a.review_verdict({"verdict": "APPROVE", "findings": ["Bug found"]})
    else:
        a.pr_identity(pr, "feature/bridge-example", "a" * 40)
        expected = "APPROVE" if case == "pass" else (
            "REQUEST_CHANGES" if case in {"failure", "cancelled", "status_conflict"} else "WAITING_CI")
        assert a.check_gate(checks, statuses, ["test", "atdd scope"]) == expected


@pytest.mark.acceptance
@pytest.mark.parametrize("case", ["policy_missing_semgrep", "policy_cannot_remove", "missing_semgrep",
                                  "semgrep_pending", "semgrep_failure", "semgrep_neutral", "all_pass",
                                  "advisory_failure_ignored", "circleci_error_ignored",
                                  "conflicting_legacy_status", "unrelated_success"])
def test_protected_required_checks(tmp_path, case):
    """ATDD-BRIDGE-v1/AC-013: test, atdd scope and semgrep/ci are structurally required."""
    value = policy()
    path = tmp_path / "policy.json"

    if case == "policy_missing_semgrep":
        value["required_checks"] = ["test", "atdd scope"]
        path.write_text(json.dumps(value))
        with pytest.raises(b.Stop, match="REQUIRED_CHECKS_MISSING"):
            a.policy_file(path, tmp_path / "worktree")
        return

    if case == "policy_cannot_remove":
        value["required_checks"] = ["test", "atdd scope", "semgrep/ci", "custom-lint"]
        path.write_text(json.dumps(value))
        loaded, _ = a.policy_file(path, tmp_path / "worktree")
        names = a.required_check_names(loaded)
        assert set(b.PROTECTED_REQUIRED_CHECKS).issubset(names)
        assert "custom-lint" in names
        return

    path.write_text(json.dumps(value))
    loaded, _ = a.policy_file(path, tmp_path / "worktree")
    names = a.required_check_names(loaded)
    assert set(b.PROTECTED_REQUIRED_CHECKS) == {"test", "atdd scope", "semgrep/ci"}

    def check(name, identifier, conclusion="success", status="completed"):
        return {"id": identifier, "name": name, "status": status, "conclusion": conclusion}

    passing = [check("test", 1), check("atdd scope", 2), check("semgrep/ci", 3)]
    matrix = {
        "missing_semgrep": (passing[:2], []),
        "semgrep_pending": ([check("test", 1), check("atdd scope", 2),
                             check("semgrep/ci", 3, None, "in_progress")], []),
        "semgrep_failure": ([check("test", 1), check("atdd scope", 2),
                             check("semgrep/ci", 3, "failure")], []),
        "semgrep_neutral": ([check("test", 1), check("atdd scope", 2),
                             check("semgrep/ci", 3, "neutral")], []),
        "all_pass": (passing, []),
        "advisory_failure_ignored": (passing + [check("ruff (advisory)", 4, "failure"),
                                                check("bandit (advisory)", 5, "failure")], []),
        "circleci_error_ignored": (passing + [check("CircleCI Pipeline", 6, "failure")], []),
        "conflicting_legacy_status": (passing, [{"id": 90, "context": "semgrep/ci", "state": "failure"}]),
        "unrelated_success": ([check("test", 1), check("totally-other", 2)], []),
    }
    expected = {
        "missing_semgrep": "WAITING_CI",
        "semgrep_pending": "WAITING_CI",
        "semgrep_failure": "REQUEST_CHANGES",
        "semgrep_neutral": "WAITING_CI",
        "all_pass": "APPROVE",
        "advisory_failure_ignored": "APPROVE",
        "circleci_error_ignored": "APPROVE",
        "conflicting_legacy_status": "REQUEST_CHANGES",
        "unrelated_success": "WAITING_CI",
    }
    checks, statuses = matrix[case]
    assert a.check_gate(checks, statuses, names) == expected[case]


def test_publish_ordinary_push_only(tmp_path, monkeypatch):
    root, task = make_repo(tmp_path)
    b.git(root, "config", "user.name", "Bridge Test")
    b.git(root, "config", "user.email", "bridge@example.invalid")
    (root / PATH).write_bytes(b"after\n")
    state = tmp_path / "state"
    state.mkdir()
    real = b.host_run
    calls = []
    def host(args, cwd=None, env=None):
        calls.append(args)
        if "push" in args:
            return ""
        return real(args, cwd, env)
    monkeypatch.setattr(b, "host_run", host)
    class API:
        def get(self, path):
            return [{"state": "open", "draft": True, "head": {"sha": b.git(root, "rev-parse", "HEAD"),
                     "ref": task["branch"], "repo": {"full_name": b.REPO}},
                     "base": {"ref": "main", "repo": {"full_name": b.REPO}},
                     "number": 42, "html_url": "https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/42"}]
    result = a.publish({"worktree": str(root), "state_dir": str(state)}, task, {PATH: b"after\n"}, API())
    assert result["sha"] != task["head"]
    pushes = [call for call in calls if "push" in call]
    assert len(pushes) == 1
    assert pushes[0][-3:] == ["push", "origin", "HEAD:refs/heads/feature/bridge-example"]
    assert not any(arg.startswith("--force") or arg in {"merge", "deploy"} for call in calls for arg in call)


@pytest.mark.parametrize("case", ["approve", "wait", "self_review", "resume", "review_ambiguous", "tests_fail"])
def test_autonomous_pipeline(tmp_path, monkeypatch, case):
    root, task = make_repo(tmp_path)
    state = tmp_path / "state"
    cfg = {"worktree": str(root), "state_dir": str(state), "dispatch_ids": [], "enable_execution": True}
    value = policy()
    for key in ("contract_sha256", "authority_sha256"):
        value[key] = task[key]
    value["base_head"] = task["head"]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    hashed = b.digest(path.read_bytes())
    snap = snapshot(task)
    snap[2].append(comment(20, "/opip-authorize-increment\n" + json.dumps({
        "policy_sha256": hashed, "expires_at": "2026-09-29T11:00:00Z", "architecture_clear": True})))
    # Remove per-task approval: the registered OWNER policy must be sufficient.
    snap[2][:] = [c for c in snap[2] if c["id"] != 11]
    class API:
        def snapshot(self, issue):
            return copy.deepcopy(snap)
        def get(self, path):
            return {"state": "open", "draft": True, "user": {"id": 2},
                    "head": {"sha": task["head"], "ref": task["branch"], "repo": {"full_name": b.REPO}},
                    "base": {"ref": "main", "repo": {"full_name": b.REPO}}}
    original_policy, original_control = a.authorize_policy, b.control
    monkeypatch.setattr(a, "authorize_policy", lambda s, h: original_policy(s, h, NOW))
    monkeypatch.setattr(b, "control", lambda s, cid, ids, admit=None: original_control(s, cid, ids, NOW, admit))
    monkeypatch.setattr(b, "GitHub", API)
    monkeypatch.setattr(a, "reviewer_id", lambda: 2 if case == "self_review" else 3)
    model_calls = []
    def cursor(config, task, context, contract, review=False):
        model_calls.append(review)
        if review:
            return {"verdict": "APPROVE", "findings": []}
        return {"conflict": False, "edits": [{"path": PATH, "before_sha256": b.digest(b"before\n"), "content": "after\n"}]}
    monkeypatch.setattr(b, "cursor", cursor)
    def tests(*args):
        if case == "tests_fail":
            raise b.Stop("TESTS_FAILED")
        return {"exit_code": 0}
    monkeypatch.setattr(a, "isolated_tests", tests)
    def publish(*args):
        # Synthetic fixture restores contents so the resumed read-only preflight is clean.
        (root / PATH).write_bytes(b"before\n")
        return {"sha": task["head"], "pr": 42, "url": "https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/42"}
    monkeypatch.setattr(a, "publish", publish)
    waiting = [case in {"wait", "resume"}]

    def pages(api, route, key=None):
        if not key:
            return []
        return [{"id": i, "name": name, "status": "in_progress" if waiting[0] else "completed",
                 "conclusion": "success"}
                for i, name in enumerate(["test", "atdd scope", "semgrep/ci"])]

    monkeypatch.setattr(a, "all_pages", pages)
    posted = []
    def post(route, body, state, reviewer=False):
        posted.append((route, body, reviewer))
        return {"commit_id": task["head"], "state": "PENDING" if case == "review_ambiguous" else "APPROVED"}
    monkeypatch.setattr(a, "gh_write", post)
    if case in {"self_review", "review_ambiguous", "tests_fail"}:
        with pytest.raises(b.Stop):
            a.autonomous(cfg, path, 1, 10, True)
    else:
        a.autonomous(cfg, path, 1, 10, True)
        if case == "resume":
            assert not posted
            waiting[0] = False
            a.autonomous(cfg, path, 1, 10, True)
        if case != "wait":
            assert posted[0][1]["event"] == "APPROVE" and posted[0][2] is True
            assert posted[0][1]["commit_id"] == task["head"]
            with pytest.raises(b.Stop, match="ALREADY_ATTEMPTED"):
                a.autonomous(cfg, path, 1, 10, True)
            with b.receipt_db(state) as connection:
                assert connection.execute("SELECT status FROM receipts WHERE task=10").fetchone()[0] == "APPROVE"
            assert len(posted) == 1
        assert model_calls == [False, True]
    if case == "tests_fail":
        assert not posted and model_calls == [False]


@pytest.mark.acceptance
@pytest.mark.parametrize("outcome", ["pending", "green", "failure"])
def test_waiting_ci_resume_through_watch(tmp_path, monkeypatch, outcome):
    """ATDD-BRIDGE-v1/AC-011: a published draft PR stays resumable without re-coding."""
    root, task = make_repo(tmp_path)
    state = tmp_path / "state"
    cfg = {"worktree": str(root), "state_dir": str(state), "dispatch_ids": [],
           "enable_execution": True}
    value = policy()
    for key in ("contract_sha256", "authority_sha256"):
        value[key] = task[key]
    value["base_head"] = task["head"]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    hashed = b.digest(path.read_bytes())
    snap = snapshot(task)
    snap[2].append(comment(20, "/opip-authorize-increment\n" + json.dumps({
        "policy_sha256": hashed, "expires_at": "2026-09-29T11:00:00Z",
        "architecture_clear": True})))
    snap[2][:] = [c for c in snap[2] if c["id"] != 11]
    comments = snap[2]

    class API:
        def snapshot(self, issue):
            return copy.deepcopy(snap)

        def get(self, route):
            return {"state": "open", "draft": True, "user": {"id": 2},
                    "head": {"sha": task["head"], "ref": task["branch"],
                             "repo": {"full_name": b.REPO}},
                    "base": {"ref": "main", "repo": {"full_name": b.REPO}},
                    "number": 42, "html_url": "https://example.invalid/pull/42"}

    monkeypatch.setattr(b, "GitHub", API)
    original_policy, original_control = a.authorize_policy, b.control
    monkeypatch.setattr(a, "authorize_policy", lambda s, h: original_policy(s, h, NOW))
    monkeypatch.setattr(b, "control",
                        lambda s, cid, ids, admit=None: original_control(s, cid, ids, NOW, admit))
    monkeypatch.setattr(a, "reviewer_id", lambda: 3)

    model_calls, test_calls, publish_calls, posted = [], [], [], []

    def cursor(config, task, context, contract, review=False):
        model_calls.append(review)
        if review:
            return {"verdict": "APPROVE", "findings": []}
        return {"conflict": False, "edits": [{"path": PATH, "before_sha256":
                b.digest(b"before\n"), "content": "after\n"}]}

    monkeypatch.setattr(b, "cursor", cursor)

    def tests(*args):
        test_calls.append(1)
        return {"exit_code": 0}

    monkeypatch.setattr(a, "isolated_tests", tests)

    def publish(*args):
        publish_calls.append(1)
        (root / PATH).write_bytes(b"before\n")
        return {"sha": task["head"], "pr": 42, "url": "https://example.invalid/pull/42"}

    monkeypatch.setattr(a, "publish", publish)

    # The pipeline runs for real; CI is pending on the first pass, then varies.
    ci_ready = [False]

    def pages(api, route, key=None):
        if not key:
            return []
        if not ci_ready[0]:
            return [{"id": i, "name": name, "status": "in_progress", "conclusion": None}
                    for i, name in enumerate(b.PROTECTED_REQUIRED_CHECKS)]
        conclusion = {"pending": None, "green": "success", "failure": "failure"}[outcome]
        status = "in_progress" if outcome == "pending" else "completed"
        return [{"id": i, "name": name, "status": status, "conclusion": conclusion}
                for i, name in enumerate(b.PROTECTED_REQUIRED_CHECKS)]

    monkeypatch.setattr(a, "all_pages", pages)

    def post(route, body, state_, reviewer=False):
        posted.append(body)
        return {"commit_id": task["head"], "state":
                {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}[body["event"]]}

    monkeypatch.setattr(a, "gh_write", post)

    # (a) First watch pass publishes the draft PR and reaches WAITING_CI.
    assert b.poll_once(cfg, 1, execute=True, policy_path=path, api=API()) == [10]
    assert publish_calls == [1]
    assert model_calls == [False, True]
    assert test_calls == [1]
    assert b.receipt_states(state)[10] == "PUBLISHED_WAITING_CI"

    # (b) The task remains eligible for a CI-only resume.
    assert b.resumable_tasks(state) == {10}
    assert 10 not in b.attempted_tasks(state)
    decided = {str(t) for t in b.attempted_tasks(state)}
    assert b.task_candidates(comments, {1}, decided) == [10]

    if outcome == "pending":
        # A still-pending CI result neither re-codes nor terminates the task.
        assert b.poll_once(cfg, 1, execute=True, policy_path=path, api=API()) == [10]
        assert model_calls == [False, True] and test_calls == [1] and publish_calls == [1]
        assert posted == []
        assert b.resumable_tasks(state) == {10}
        return

    # (c)/(d) A terminal CI result resumes this task and submits the review.
    ci_ready[0] = True
    assert b.poll_once(cfg, 1, execute=True, policy_path=path, api=API()) == [10]
    expected_event = {"green": "APPROVE", "failure": "REQUEST_CHANGES"}[outcome]
    assert [body["event"] for body in posted] == [expected_event]
    assert posted[0]["commit_id"] == task["head"]
    assert b.receipt_states(state)[10] == expected_event

    # (e) Resume did not re-code, re-test, re-commit or re-push.
    assert model_calls == [False, True]
    assert test_calls == [1]
    assert publish_calls == [1]

    # (f) A terminal task is no longer rediscovered.
    assert b.poll_once(cfg, 1, execute=True, policy_path=path, api=API()) == []
    assert b.resumable_tasks(state) == set()
    assert 10 in b.attempted_tasks(state)
    assert model_calls == [False, True] and publish_calls == [1]
