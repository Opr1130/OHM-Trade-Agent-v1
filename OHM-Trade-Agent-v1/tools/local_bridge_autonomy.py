"""Optional bounded autonomy. OWNER policy -> isolated tests -> draft PR -> review.

No merge/deploy/force push. Never execute candidate code on the Windows host.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from tools import local_agent_bridge as b

DOCKER_HOST = "npipe:////./pipe/dockerDesktopLinuxEngine"
POLICY_KEYS = {"schema", "repo", "increment", "branch", "base_head", "contract_sha256",
               "authority_sha256", "registered_tasks", "max_tasks", "test_image", "test_command",
               "test_timeout_seconds", "required_checks"}


def policy_file(path, root):
    b.require(path.is_absolute() and not path.resolve().is_relative_to(root.resolve()), "POLICY_LOCATION")
    raw = b.read_bytes(path)
    policy = b.strict_json(raw.decode("utf-8-sig"))
    b.fields(policy, POLICY_KEYS)
    b.require(type(policy["schema"]) is int and policy["schema"] == 1 and policy["repo"] == b.REPO,
              "POLICY_REPOSITORY")
    b.require(type(policy["max_tasks"]) is int and 1 <= policy["max_tasks"] <= 10, "POLICY_BUDGET")
    b.require(type(policy["base_head"]) is str and b.HEAD.fullmatch(policy["base_head"]), "POLICY_BASE")
    b.require(type(policy["test_image"]) is str and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}", policy["test_image"]), "IMAGE_DIGEST_REQUIRED")
    command = policy["test_command"]
    b.require(type(command) is list and 1 <= len(command) <= 30 and
              all(type(arg) is str and arg and "\0" not in arg and len(arg) <= 500 for arg in command),
              "REGISTERED_TEST_COMMAND_REQUIRED")
    b.require(type(policy["test_timeout_seconds"]) is int and
              1 <= policy["test_timeout_seconds"] <= 900, "TEST_TIMEOUT")
    checks = policy["required_checks"]
    b.require(type(checks) is list and 2 <= len(checks) <= 30 and
              all(type(c) is str and c and len(c) <= 100 for c in checks) and
              # The protected `main` contexts are structural, not policy-removable.
              set(b.PROTECTED_REQUIRED_CHECKS).issubset(checks) and
              len(set(checks)) == len(checks),
              "REQUIRED_CHECKS_MISSING")
    templates = policy["registered_tasks"]
    b.require(type(templates) is list and 1 <= len(templates) <= 20, "TASK_REGISTRY_REQUIRED")
    for template in templates:
        b.fields(template, {"instructions", "files"})
        b.require(type(template["instructions"]) is str and 0 < len(template["instructions"]) <= 12000,
                  "REGISTERED_INSTRUCTIONS_REQUIRED")
        b.require(type(template["files"]) is list and 0 < len(template["files"]) <= b.MAX_FILES,
                  "REGISTERED_FILES_REQUIRED")
        for name in template["files"]:
            b.safe_path(name)
    return policy, b.digest(raw)


def authorize_policy(snapshot, policy_hash, now=None):
    owner, _, comments = snapshot
    now = now or datetime.now(timezone.utc)
    decisions = []
    for comment in comments:
        if comment.get("user", {}).get("id") != owner:
            continue
        body = comment.get("body", "") or ""
        for command in ("/opip-authorize-increment", "/opip-revoke-increment"):
            if body.startswith(command + "\n"):
                value = b.envelope(comment, command)
                b.require(type(value) is dict, "INVALID_POLICY_APPROVAL")
                if value.get("policy_sha256") == policy_hash:
                    b.immutable(comment)
                    decisions.append((comment["id"], command, value, comment))
    b.require(bool(decisions), "OWNER_POLICY_APPROVAL_REQUIRED")
    _, command, value, comment = max(decisions, key=lambda d: d[0])
    b.require(command == "/opip-authorize-increment", "OWNER_POLICY_REVOKED")
    b.fields(value, {"policy_sha256", "expires_at", "architecture_clear"})
    b.require(value["architecture_clear"] is True, "ARCHITECTURE_CONFLICT")
    created, expires = b.timestamp(comment["created_at"]), b.timestamp(value["expires_at"])
    b.require(created <= now < expires and (expires - created).total_seconds() <= 7 * 86400,
              "POLICY_EXPIRED_OR_FUTURE")


def admit_task(policy, task, _task_hash):
    for key in ("increment", "branch", "contract_sha256", "authority_sha256"):
        b.require(task[key] == policy[key], "TASK_OUTSIDE_REGISTERED_POLICY")
    b.require({"instructions": task["instructions"], "files": task["files"]} in policy["registered_tasks"],
              "TASK_OUTSIDE_REGISTERED_POLICY")


def gh_write(path, body, state, reviewer=False):
    binary = shutil.which("gh")
    b.require(binary is not None, "GH_MISSING")
    env = dict(os.environ)
    if reviewer:
        b.require(bool(env.get("OPIP_BRIDGE_REVIEW_TOKEN")), "REVIEWER_CREDENTIAL_REQUIRED")
        env["GH_TOKEN"] = env["OPIP_BRIDGE_REVIEW_TOKEN"]
    # Structured file input, never shell interpolation; no token on argv or in files.
    with tempfile.TemporaryDirectory(prefix="opip-request-", dir=state) as name:
        request = Path(name) / "body.json"
        request.write_text(json.dumps(body), encoding="utf-8")
        return b.strict_json(b.host_run([binary, "api", "--hostname", "github.com", "--method", "POST",
                                        f"repos/{b.REPO}/{path}", "--input", str(request)], env=env))


def reviewer_id():
    binary = shutil.which("gh")
    b.require(binary is not None and bool(os.environ.get("OPIP_BRIDGE_REVIEW_TOKEN")),
              "REVIEWER_CREDENTIAL_REQUIRED")
    env = dict(os.environ, GH_TOKEN=os.environ["OPIP_BRIDGE_REVIEW_TOKEN"])
    user = b.strict_json(b.host_run([binary, "api", "--hostname", "github.com", "--method", "GET", "user"], env=env))
    b.require(b.integer(user.get("id")), "INVALID_REVIEWER")
    return user["id"]


def copied_source(root, destination, files):
    tracked = b.git(root, "ls-files", "-z").split("\0")
    names = sorted(set(filter(None, tracked)) | set(files))
    total = 0
    for name in names:
        # No administrative state, generated files, credentials or host configuration.
        parts = Path(name).parts
        if any(part in {".git", "__pycache__", ".env"} or
               part.startswith(".env.") and not part.endswith(".example") for part in parts):
            continue
        b.require(not re.search(r"(?i)(\.pem$|\.key$|id_rsa$|id_ed25519$)", name),
                  "SNAPSHOT_CREDENTIAL_PATH")
        source = b.file_at(root, name)
        b.require(source.is_file(), "SNAPSHOT_FILE_MISSING")
        size = source.stat().st_size
        total += size
        b.require(size <= 20 * b.MAX_BYTES and total <= 100 * b.MAX_BYTES, "SNAPSHOT_LIMIT")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def docker_args(binary, image, snapshot, name, command):
    return [binary, "--host", DOCKER_HOST, "create", "--name", name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "1g",
            "--cpus", "2", "--user", "65534:65534", "--tmpfs", "/tmp:rw,nosuid,size=128m",
            "--mount", f"type=bind,source={snapshot},destination=/workspace,readonly",
            "--workdir", "/workspace/OHM-Trade-Agent-v1", "--env", "HOME=/tmp",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "APP_ENV=test",
            "--entrypoint", command[0], image, *command[1:]]


def isolated_tests(config, policy, task):
    binary = shutil.which("docker")
    b.require(binary is not None and os.name == "nt", "LOCAL_DOCKER_DESKTOP_REQUIRED")
    prefix = [binary, "--host", DOCKER_HOST]
    b.require(b.host_run([*prefix, "info", "--format", "{{.OSType}}" ]) == "linux", "LINUX_CONTAINER_REQUIRED")
    b.host_run([*prefix, "image", "inspect", policy["test_image"]])  # Never pull/install.
    state, root = Path(config["state_dir"]), Path(config["worktree"])
    with tempfile.TemporaryDirectory(prefix="opip-test-", dir=state) as name:
        snapshot = Path(name) / "source"
        snapshot.mkdir()
        copied_source(root, snapshot, task["files"])
        container_name = "opip-bridge-" + uuid.uuid4().hex
        container = b.host_run(docker_args(binary, policy["test_image"], snapshot, container_name,
                                          policy["test_command"]))
        b.require(re.fullmatch(r"[0-9a-f]{64}", container), "INVALID_CONTAINER_ID")
        try:
            with tempfile.TemporaryFile(dir=state) as output:
                process = subprocess.Popen([*prefix, "start", "--attach", container],
                                           stdin=subprocess.DEVNULL, stdout=output,
                                           stderr=subprocess.STDOUT, shell=False)
                try:
                    deadline = time.monotonic() + policy["test_timeout_seconds"]
                    while process.poll() is None:
                        b.require(time.monotonic() < deadline and
                                  os.fstat(output.fileno()).st_size <= 10 * b.MAX_BYTES,
                                  "TEST_TIMEOUT_OR_OUTPUT_LIMIT")
                        time.sleep(0.1)
                    b.require(process.returncode == 0, "TESTS_FAILED")
                    b.require(os.fstat(output.fileno()).st_size <= 10 * b.MAX_BYTES, "TEST_OUTPUT_LIMIT")
                    output.seek(0)
                    return {"image": policy["test_image"], "output_sha256": b.digest(output.read()),
                            "command": policy["test_command"], "exit_code": 0}
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
        finally:
            # Only the exact container ID this invocation created; never volume/image cleanup.
            b.host_run([*prefix, "rm", "--force", container])


def review_verdict(value):
    b.fields(value, {"verdict", "findings"})
    b.require(value["verdict"] in {"APPROVE", "REQUEST_CHANGES"} and
              type(value["findings"]) is list and len(value["findings"]) <= 30 and
              all(type(f) is str and 0 < len(f) <= 2000 for f in value["findings"]), "INVALID_REVIEW")
    b.require(value["verdict"] != "APPROVE" or not value["findings"], "CONTRADICTORY_REVIEW")
    return value["verdict"]


def check_gate(checks, statuses, required):
    latest = {}
    for check in sorted(checks, key=lambda c: c["id"]):
        latest[check["name"]] = check.get("conclusion") if check.get("status") == "completed" else "pending"
    # Avoid collisions between status and check-run names granting success.
    combined = {}
    for status in sorted(statuses, key=lambda s: s["id"]):
        combined[status["context"]] = status["state"]
    values = []
    for name in required:
        records = [v[name] for v in (latest, combined) if name in v]
        if not records:
            values.append("pending")
        else:
            values.extend(records)
    if any(v in {"failure", "error", "cancelled", "timed_out", "action_required", "stale",
                 "startup_failure"} for v in values):
        return "REQUEST_CHANGES"
    return "APPROVE" if values and all(v == "success" for v in values) else "WAITING_CI"


def required_check_names(policy):
    """Protected contexts plus any additional policy-required names.

    A policy may add checks; it can never remove a protected context, and a
    missing protected context is always evaluated as pending, never as satisfied.
    """
    return tuple(sorted(set(policy["required_checks"]) | set(b.PROTECTED_REQUIRED_CHECKS)))


def all_pages(api, path, key=None):
    result = []
    for page in range(1, 21):
        value = api.get(f"{path}?per_page=100&page={page}")
        batch = value[key] if key else value
        b.require(type(batch) is list, "INVALID_CHECK_RESPONSE")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise b.Stop("CHECK_PAGE_LIMIT")


def pr_identity(pr, branch, sha):
    b.require(pr.get("state") == "open" and not pr.get("merged") and
              pr.get("draft") is True and pr.get("auto_merge") is None and
              pr.get("head", {}).get("sha") == sha and pr["head"].get("ref") == branch and
              pr["head"].get("repo", {}).get("full_name") == b.REPO and
              pr.get("base", {}).get("ref") == "main" and
              pr["base"].get("repo", {}).get("full_name") == b.REPO, "PR_HEAD_MISMATCH")


def phase(connection, comment_id, policy_hash, value, details):
    with connection:
        connection.execute("INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?)",
                           (comment_id, policy_hash, value, json.dumps(details)))
        connection.execute("UPDATE receipts SET status=? WHERE task=?", (value, comment_id))


def publish(config, task, context, api):
    root, state = Path(config["worktree"]), Path(config["state_dir"])
    existing = api.get(f"pulls?state=open&head=Opr1130:{task['branch']}&base=main")
    b.require(type(existing) is list and len(existing) <= 1, "AMBIGUOUS_PR")
    if existing:
        pr_identity(existing[0], task["branch"], task["head"])
    b.require(b.git(root, "rev-parse", "HEAD") == task["head"] and
              b.git(root, "symbolic-ref", "--short", "HEAD") == task["branch"], "PRECOMMIT_DRIFT")
    changed = set(filter(None, b.git(root, "diff", "--name-only", "HEAD").splitlines()))
    changed |= set(filter(None, b.git(root, "ls-files", "--others", "--exclude-standard").splitlines()))
    b.require(changed and changed.issubset(task["files"]), "PRECOMMIT_SCOPE_DRIFT")
    for name in task["files"]:
        b.require((root / name).read_bytes() == context[name], "PRECOMMIT_CONTENT_DRIFT")
    binary = shutil.which("git")
    with tempfile.TemporaryDirectory(prefix="opip-empty-hooks-", dir=state) as hooks:
        prefix = [binary, "-c", f"core.hooksPath={hooks}", "-c", "commit.gpgsign=false",
                  "-c", "core.autocrlf=false"]
        b.host_run([*prefix, "add", "--", *sorted(changed)], root)
        b.require(set(b.git(root, "diff", "--cached", "--name-only").splitlines()) == changed,
                  "STAGED_SCOPE_DRIFT")
        for name in changed:
            data = context[name]
            expected = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data,
                                    usedforsecurity=False).hexdigest()
            b.require(b.git(root, "rev-parse", ":" + name) == expected, "STAGED_CONTENT_DRIFT")
        b.host_run([*prefix, "commit", "-m", f"Implement approved {task['increment']} engineering task"], root)
        sha = b.git(root, "rev-parse", "HEAD")
        b.require(b.git(root, "log", "-1", "--format=%P") == task["head"] and
                  b.git(root, "symbolic-ref", "--short", "HEAD") == task["branch"], "COMMIT_DRIFT")
        b.require(b.git(root, "remote", "get-url", "origin") in
                  {f"https://github.com/{b.REPO}.git", f"git@github.com:{b.REPO}.git"}, "WRONG_REMOTE")
        b.require(b.git(root, "remote", "get-url", "--push", "--all", "origin") in
                  {f"https://github.com/{b.REPO}.git", f"git@github.com:{b.REPO}.git"}, "WRONG_PUSH_REMOTE")
        # Explicit destination, ordinary fast-forward push, never force or default refspecs.
        b.host_run([*prefix, "push", "origin", f"HEAD:refs/heads/{task['branch']}"], root)
    existing = api.get(f"pulls?state=open&head=Opr1130:{task['branch']}&base=main")
    b.require(type(existing) is list and len(existing) <= 1, "AMBIGUOUS_PR")
    if existing:
        pr = existing[0]
    else:
        pr = gh_write("pulls", {"title": f"O'Pip {task['increment']} engineering task",
                      "head": task["branch"], "base": "main", "draft": True,
                      "body": "Bounded autonomous implementation under an OWNER-approved ATDD increment. "
                              "Local isolated tests and a separate fresh Cursor review passed before publication. "
                              "Exact-HEAD GitHub checks and separate-identity review remain required. "
                              "No merge, deployment or trading authority is granted."}, state)
    pr_identity(pr, task["branch"], sha)
    return {"sha": sha, "pr": pr["number"], "url": pr["html_url"]}


def autonomous(config, policy_path, issue, comment_id, execute=False, api=None,
               status_api=None):
    state, root = Path(config["state_dir"]), Path(config["worktree"])
    policy, policy_hash = policy_file(policy_path, root)
    api = api or b.GitHub()
    gate_names = required_check_names(policy)

    def note(status_name, task=None, reason=None):
        """Success-path announcement: a failed GitHub write stops the task."""
        return b.announce(status_api, comment_id, status_name, task, reason_code=reason)

    def note_failure(status_name, task=None, reason=None):
        """Failure-path announcement: never masks the original reason."""
        return b.safe_announce(status_api, comment_id, status_name, task, reason_code=reason)

    task = None
    with b.run_lock(state):
        connection = b.receipt_db(state) if execute else None
        try:
            if connection:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("CREATE TABLE IF NOT EXISTS runs "
                                   "(task INTEGER PRIMARY KEY, policy TEXT, phase TEXT, details TEXT)")
                connection.commit()

            def authorized():
                snapshot = api.snapshot(issue)
                # Pipeline uses an issue as the stable control plane while PR HEAD advances.
                b.require("head" not in snapshot[1], "AUTONOMY_REQUIRES_CONTROL_ISSUE")
                authorize_policy(snapshot, policy_hash)
                return b.control(snapshot, comment_id, config["dispatch_ids"],
                                 admit=lambda task, hashed: admit_task(policy, task, hashed))

            task, task_hash = authorized()
            b.require(b.git(root, "config", "--type=bool", "--default=false", "--get", "core.autocrlf") == "false",
                      "AUTONOMY_REQUIRES_BYTE_STABLE_CHECKOUT")
            record = connection.execute("SELECT policy, phase, details FROM runs WHERE task=?",
                                        (comment_id,)).fetchone() if connection else None
            b.host_run([shutil.which("git"), "merge-base", "--is-ancestor", policy["base_head"], "HEAD"], root)
            if record:
                b.require(record[0] == policy_hash and record[1] == "PUBLISHED_WAITING_CI", "ALREADY_ATTEMPTED")
                details = json.loads(record[2])
                resumed = dict(task, head=details["sha"])
                b.repository(root, resumed)
                b.require(details["task_sha256"] == task_hash, "TASK_CHANGED")
                note("pushed", task)
            else:
                context, contract = b.repository(root, task)
                if not execute:
                    b.status(state, comment_id, "AUTONOMOUS_DRY_RUN_VALID")
                    return
                b.require(config["enable_execution"] is True, "EXECUTION_DISABLED")
                b.require(connection.execute("SELECT COUNT(*) FROM runs WHERE policy=?",
                                              (policy_hash,)).fetchone()[0] < policy["max_tasks"], "POLICY_BUDGET_EXHAUSTED")
                reviewer_id()  # Establish separate review credentials before spending on Cursor.
                note("accepted", task)
                b.claim(connection, comment_id, task_hash)
                details = {"task_sha256": task_hash}
                phase(connection, comment_id, policy_hash, "STARTED", details)
                b.status(state, comment_id, "AUTONOMOUS_TASK_APPROVED")
                note("running", task)
                proposal = b.cursor(config, task, context, contract)
                authorized()
                b.require(b.repository(root, task)[0] == context, "CONTEXT_CHANGED")
                edits = b.proposal_edits(proposal, root, task, context)
                phase(connection, comment_id, policy_hash, "APPLYING", details)
                b.apply_edits(edits)
                note("testing", task)
                candidate = {name: (root / name).read_bytes() for name in task["files"]}
                evidence = isolated_tests(config, policy, task)
                review_context = {name: {"before": context[name]["content"],
                                        "after": b.text_content(data)} for name, data in candidate.items()}
                review_context["verification_evidence"] = evidence
                review = b.cursor(config, task, review_context, contract, review=True)
                verdict = review_verdict(review)
                b.text_content(json.dumps(review).encode("utf-8"))
                details["review"] = review
                phase(connection, comment_id, policy_hash, "REVIEWED", details)
                b.require(verdict == "APPROVE", "LOCAL_REVIEW_REQUEST_CHANGES")
                authorized()
                details["tests"] = evidence
                phase(connection, comment_id, policy_hash, "PUBLISHING", details)
                published = publish(config, task, candidate, api)
                details.update(published)
                phase(connection, comment_id, policy_hash, "PUBLISHED_WAITING_CI", details)
                note("pushed", dict(task, head=details["sha"]))
            pr = api.get(f"pulls/{details['pr']}")
            pr_identity(pr, task["branch"], details["sha"])
            reviewer = reviewer_id()
            b.require(b.integer(pr.get("user", {}).get("id")) and
                      reviewer != pr["user"]["id"], "SELF_APPROVAL_FORBIDDEN")
            checks = all_pages(api, f"commits/{details['sha']}/check-runs", "check_runs")
            statuses = all_pages(api, f"commits/{details['sha']}/statuses")
            verdict = check_gate(checks, statuses, gate_names)
            if verdict == "WAITING_CI":
                note("waiting_ci", dict(task, head=details["sha"]))
                b.status(state, comment_id, verdict)
                return
            authorized()
            pr_identity(api.get(f"pulls/{details['pr']}"), task["branch"], details["sha"])
            phase(connection, comment_id, policy_hash, "REVIEW_SUBMITTING", details)
            result = gh_write(f"pulls/{details['pr']}/reviews", {
                "commit_id": details["sha"], "event": verdict,
                "body": f"Automated engineering review for exact commit {details['sha']}. "
                        f"Required-check disposition: {verdict}. Local isolated tests and independent "
                        "Cursor review passed before publication. This is not OWNER merge/deployment approval."
            }, state, reviewer=True)
            b.require(result.get("commit_id") == details["sha"] and result.get("state") ==
                      {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}[verdict], "REVIEW_RESULT_AMBIGUOUS")
            phase(connection, comment_id, policy_hash, verdict, details)
            note("approved" if verdict == "APPROVE" else "request_changes",
                 dict(task, head=details["sha"]), reason=verdict)
            b.status(state, comment_id, "GITHUB_" + verdict)
        except BaseException as exc:
            # Never retry an ambiguous push, commit, PR creation, review or partial write.
            # A failed read in the explicit CI-wait phase can safely be retried later.
            code = str(exc) if isinstance(exc, b.Stop) else "FAILED_OWNER_RECOVERY"
            if connection:
                row = connection.execute("SELECT phase, details FROM runs WHERE task=?", (comment_id,)).fetchone()
                if row and row[0] in {"STARTED", "APPLYING", "REVIEWED", "PUBLISHING", "REVIEW_SUBMITTING"}:
                    phase(connection, comment_id, policy_hash, "FAILED_OWNER_RECOVERY", json.loads(row[1]))
            b.status(state, comment_id, code)
            if task is not None:
                note_failure("blocked" if code in b.BLOCKED_REASONS else "failed",
                             task, reason=code)
            raise
        finally:
            if connection:
                connection.close()
