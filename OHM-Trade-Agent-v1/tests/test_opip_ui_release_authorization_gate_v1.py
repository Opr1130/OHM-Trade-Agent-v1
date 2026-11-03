"""ATDD: behavioural proof of the owner-gated UI release authorization.

The defect this file exists for is a *boolean precedence* defect: in a GitHub
expression ``&&`` binds tighter than ``||``, so the shipped gate

    a && b && c && d && startsWith(body, '/deploy-ui ') || startsWith(body, '/rollback-ui ')

authorised ``/rollback-ui`` on its own - bypassing the owner identity, the
approval issue number, the author association and the repository-owner check.

Asserting that the file contains the word ``OWNER`` cannot detect that. So this
module extracts the real ``if:`` scalar from the workflow and EVALUATES it
against synthetic event contexts with a small deterministic expression
evaluator, and it extracts and RUNS the real approval-comment resolver so the
SHA and command contract is exercised, not described. No third-party YAML or
expression library is used or added.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

from tests.test_opip_canonical_single_writer_feasibility_v1 import (
    _bash,
    _is_fork_failure,
)

def _bash_ready() -> bool:
    """True only when this bash can run a real script and return its output.

    The Windows shell in this repository is sometimes a partially working
    Git-for-Windows stub: it is discoverable, ``-c`` works, and a real script
    path exits 127 with no output. The probe therefore executes a *script file*,
    which is exactly what the behavioural tests do, so those cases skip here and
    the Linux runner in CI provides the authoritative execution.
    """
    bash = _bash()
    if bash is None:
        return False
    try:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "probe.sh"
            # Command substitution forks a subshell, which is the operation that
            # actually fails in a resource-starved Windows shell.
            script.write_text(
                "value=\"$(printf 'OPIP_BASH_OK')\"\nprintf '%s\\n' \"$value\"\n",
                encoding="utf-8",
                newline="\n",
            )
            proc = subprocess.run(
                [bash, script.as_posix()],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        proc.returncode == 0
        and proc.stdout.strip() == "OPIP_BASH_OK"
        and not _is_fork_failure(proc)
    )


BASH_READY = _bash_ready()
requires_bash = pytest.mark.skipif(not BASH_READY, reason="no reliable bash in this environment")

INCREMENT = "ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1"
REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github/workflows/deploy-production.yml"
GATEWAY = REPO / "OHM-Trade-Agent-v1/deploy/remote/ohm-deploy-ssh"

OWNER = "Opr1130"
REPOSITORY = "Opr1130/OHM-Trade-Agent-v1"
SHA = "0123456789abcdef0123456789abcdef01234567"

# The exact expression this correction removed. Kept verbatim so the regression
# is proven, not described.
HISTORICAL_GATE = (
    "github.event_name == 'issue_comment' && "
    "github.event.issue.number == 64 && "
    "github.event.comment.user.login == github.repository_owner && "
    "github.event.comment.author_association == 'OWNER' && "
    "startsWith(github.event.comment.body, '/deploy-ui ') || "
    "startsWith(github.event.comment.body, '/rollback-ui ')"
)

pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------------
# Dependency-free extraction of the real workflow scalars.
# ---------------------------------------------------------------------------


def _lines() -> list[str]:
    return WORKFLOW.read_text(encoding="utf-8").splitlines()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _folded_scalar(lines: list[str], start: int) -> str:
    """Read a ``>-`` block scalar beginning at ``start`` (0-based, the header)."""
    header = lines[start]
    assert ">" in header, header
    indent = _indent(header)
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and _indent(line) <= indent:
            break
        body.append(line.strip())
    return " ".join(part for part in body if part)


def job_if(job: str) -> str:
    lines = _lines()
    marker = f"  {job}:"
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == marker:
            start = index
            break
    assert start is not None, f"job {job} not found"
    for offset in range(start + 1, len(lines)):
        stripped = lines[offset].strip()
        if stripped.startswith("if:"):
            inline = stripped[len("if:") :].strip()
            if inline in (">-", ">", "|", "|-"):
                return _folded_scalar(lines, offset)
            return inline
        if stripped and not stripped.startswith("#") and _indent(lines[offset]) <= 2:
            break
    raise AssertionError(f"job {job} has no if condition")


def step_run(step_name: str) -> str:
    lines = _lines()
    for index, line in enumerate(lines):
        if line.strip() != f"- name: {step_name}":
            continue
        for offset in range(index + 1, len(lines)):
            if lines[offset].strip() == "run: |":
                indent = _indent(lines[offset])
                body: list[str] = []
                for line in lines[offset + 1 :]:
                    if line.strip() and _indent(line) <= indent:
                        break
                    body.append(line)
                return textwrap.dedent("\n".join(body))
        raise AssertionError(f"step {step_name} has no run block")
    raise AssertionError(f"step {step_name} not found")


# ---------------------------------------------------------------------------
# A minimal GitHub-expression evaluator: exactly the operators the gate uses.
# ---------------------------------------------------------------------------

_OPERATORS = ("&&", "||", "==", "!=", "(", ")", ",")


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "'":
            end = index + 1
            while end < len(text) and text[end] != "'":
                end += 1
            tokens.append(text[index : end + 1])
            index = end + 1
            continue
        matched = False
        for operator in _OPERATORS:
            if text.startswith(operator, index):
                tokens.append(operator)
                index += len(operator)
                matched = True
                break
        if matched:
            continue
        end = index
        while end < len(text) and (text[end].isalnum() or text[end] in "._-"):
            end += 1
        assert end > index, f"cannot tokenize at {text[index:index + 20]!r}"
        tokens.append(text[index:end])
        index = end
    return tokens


def _resolve(token: str, context: dict[str, object]) -> object:
    if token.startswith("'"):
        return token[1:-1]
    if token == "true":
        return True
    if token == "false":
        return False
    if token == "null":
        return None
    if token.replace(".", "", 1).isdigit():
        return float(token) if "." in token else int(token)
    return context.get(token)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return value != ""


class _Evaluator:
    """Recursive descent over the real GitHub operator precedence.

    ``||`` is the loosest binding operator, then ``&&``, then comparisons - the
    exact rule the historical gate violated. ``&&``/``||`` return an operand
    (JavaScript semantics) instead of a boolean, so a precedence mistake surfaces
    as a truthy value rather than being masked by coercion.
    """

    def __init__(self, tokens: list[str], context: dict[str, object]) -> None:
        self.tokens = tokens
        self.position = 0
        self.context = context

    def peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> str:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def parse(self) -> object:
        value = self.parse_or()
        assert self.peek() is None, f"trailing tokens: {self.tokens[self.position:]}"
        return value

    def parse_or(self) -> object:
        value = self.parse_and()
        while self.peek() == "||":
            self.take()
            right = self.parse_and()
            value = value if _truthy(value) else right
        return value

    def parse_and(self) -> object:
        value = self.parse_comparison()
        while self.peek() == "&&":
            self.take()
            right = self.parse_comparison()
            value = right if _truthy(value) else value
        return value

    def parse_comparison(self) -> object:
        value = self.parse_unary()
        while self.peek() in ("==", "!="):
            operator = self.take()
            right = self.parse_unary()
            equal = value == right
            value = equal if operator == "==" else not equal
        return value

    def parse_unary(self) -> object:
        if self.peek() == "!":
            self.take()
            return not _truthy(self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> object:
        token = self.take()
        if token == "(":
            value = self.parse_or()
            assert self.take() == ")", "unbalanced parenthesis"
            return value
        if self.peek() == "(":
            self.take()
            arguments: list[object] = []
            if self.peek() != ")":
                arguments.append(self.parse_or())
                while self.peek() == ",":
                    self.take()
                    arguments.append(self.parse_or())
            assert self.take() == ")", "unbalanced call"
            return _call(token, arguments)
        return _resolve(token, self.context)


def _call(name: str, arguments: list[object]) -> object:
    if name == "startsWith":
        assert len(arguments) == 2, arguments
        left, right = arguments
        return str(left if left is not None else "").startswith(
            str(right if right is not None else "")
        )
    raise AssertionError(f"unsupported function in the workflow gate: {name}")


def evaluate(expression: str, context: dict[str, object]) -> object:
    return _Evaluator(_tokenize(expression), context).parse()


def context(
    *,
    event_name: str = "issue_comment",
    issue_number: object = 64,
    login: str = OWNER,
    association: str = "OWNER",
    body: str = f"/deploy-ui {SHA}",
    repository_owner: str = OWNER,
) -> dict[str, object]:
    return {
        "github.event_name": event_name,
        "github.event.issue.number": issue_number,
        "github.event.comment.user.login": login,
        "github.event.comment.author_association": association,
        "github.event.comment.body": body,
        "github.repository_owner": repository_owner,
        "github.repository": REPOSITORY,
    }


NEGATIVE_CONTEXTS: tuple[tuple[str, dict[str, object]], ...] = (
    ("non-owner /deploy-ui", context(login="attacker", association="NONE")),
    ("non-owner /rollback-ui", context(
        login="attacker", association="NONE", body=f"/rollback-ui {SHA}")),
    ("owner comment on the wrong issue", context(issue_number=63)),
    ("owner comment on a pull request number", context(issue_number=64, event_name="push")),
    ("unrelated text containing a command", context(
        body=f"please run /deploy-ui {SHA} for me")),
    ("indented command", context(body=f"  /deploy-ui {SHA}")),
    ("association MEMBER but login matches nobody", context(
        login="contributor", association="MEMBER")),
    ("deploy-learning through the UI gate", context(body=f"/deploy-learning {SHA}")),
    ("bare deploy through the UI gate", context(body=f"/deploy {SHA}")),
    ("empty body", context(body="")),
)

POSITIVE_CONTEXTS: tuple[tuple[str, dict[str, object]], ...] = (
    ("owner /deploy-ui", context()),
    ("owner /rollback-ui", context(body=f"/rollback-ui {SHA}")),
)


def test_ac_007_the_shipped_gate_requires_every_predicate():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-007: no bounded action bypasses a predicate."""
    expression = job_if("deploy_ui")
    for label, ctx in POSITIVE_CONTEXTS:
        assert _truthy(evaluate(expression, ctx)), label
    for label, ctx in NEGATIVE_CONTEXTS:
        assert not _truthy(evaluate(expression, ctx)), label


def test_ac_007_rollback_is_not_authorized_by_operator_precedence():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-007: the historical grouping is proven unsafe and fixed."""
    historical = HISTORICAL_GATE
    shipped = job_if("deploy_ui")
    stranger_rollback = context(
        login="attacker",
        association="NONE",
        body=f"/rollback-ui {SHA}",
        issue_number=1,
        repository_owner="someone-else",
    )
    # Proof of the defect: the old expression admitted a stranger's rollback.
    assert _truthy(evaluate(historical, stranger_rollback))
    # Proof of the fix: the shipped expression admits nothing of the kind.
    assert not _truthy(evaluate(shipped, stranger_rollback))
    for label, ctx in NEGATIVE_CONTEXTS:
        assert not _truthy(evaluate(shipped, ctx)), label


def test_ac_007_spoofed_repository_context_is_not_authorized():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-007: another account cannot satisfy the owner predicate."""
    expression = job_if("deploy_ui")
    for ctx in (
        context(login="Opr1130", association="OWNER", repository_owner="fork-owner"),
        context(login="Opr1130", association="CONTRIBUTOR"),
        context(login="Opr1130", association="FIRST_TIME_CONTRIBUTOR"),
        context(login="Opr1130", association=""),
        context(login="", association="OWNER"),
    ):
        assert not _truthy(evaluate(expression, ctx))


# ---------------------------------------------------------------------------
# The approval-comment contract, executed rather than described.
# ---------------------------------------------------------------------------

RESOLVER_STEP = "Resolve the exact approved main SHA for the UI release"

BAD_BODIES: tuple[str, ...] = (
    "",
    "   ",
    f"/deploy-ui",
    f"/deploy-ui ",
    f" /deploy-ui {SHA}",
    f"/deploy-ui {SHA[:39]}",
    f"/deploy-ui {SHA}0",
    f"/deploy-ui {SHA.upper()}",
    f"/deploy-ui {SHA} && id",
    f"/deploy-ui {SHA} ; rm -rf /",
    f"/deploy-ui $(id)",
    f"/deploy-ui `id`",
    f"/deploy-ui {SHA}\n/dashboard",
    f"deploy-ui {SHA}",
    f"/deploy {SHA}",
    f"/deploy-ui --sha {SHA}",
    f"/rollback-ui",
    f"/rollback-ui {'z' * 40}",
)


def _run_step(script: str, body: str, tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], str]:
    # A file, not `-c`: a long multi-line `-c` argument can be silently dropped
    # by some bash builds and heredocs parse more reliably from a file.
    path = tmp_path / "resolver.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    proc = subprocess.run(
        [_bash() or "bash", path.as_posix()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=tmp_path,
        env={**os.environ, "COMMENT_BODY": body, "GITHUB_OUTPUT": output.as_posix()},
    )
    return proc, output.read_text(encoding="utf-8")


@requires_bash
def test_ac_007_approval_comment_must_be_an_exact_bounded_command(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-007: only an exact command with an exact SHA resolves."""
    script = step_run(RESOLVER_STEP)
    for body in BAD_BODIES:
        proc, output = _run_step(script, body, tmp_path)
        assert proc.returncode == 64, (body, proc.stdout, proc.stderr)
        assert output == "", (body, output)

    for action in ("deploy-ui", "rollback-ui"):
        proc, output = _run_step(script, f"/{action} {SHA}", tmp_path)
        assert proc.returncode == 0, (action, proc.stderr)
        assert f"action={action}" in output
        assert f"sha={SHA}" in output


def test_ac_007_approval_comment_resolver_never_interpolates():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-007: the comment body only ever reaches a regex."""
    script = step_run(RESOLVER_STEP)
    assert "eval" not in script
    assert '"$GITHUB_OUTPUT"' in script
    # The body is read from an environment variable, never spliced into the
    # script text by the workflow engine.
    assert '"$COMMENT_BODY" =~' in script
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "COMMENT_BODY: ${{ github.event.comment.body }}" in workflow
    # No step ever spells the comment body into a shell command line.
    for line in workflow.splitlines():
        if "${{ github.event.comment.body }}" in line:
            assert line.strip().startswith("COMMENT_BODY:"), line


def test_ac_007_the_release_verdict_is_not_a_boolean_expression():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-007: the pass/fail verdict is a shell case."""
    text = WORKFLOW.read_text(encoding="utf-8")
    verdict = step_run("Fail unless the UI-only release gate passed")
    assert 'case "${UI_ACTION:-}"' in verdict
    assert "deploy-ui) EXPECTED=DEPLOYED" in verdict
    assert "rollback-ui) EXPECTED=ROLLED_BACK" in verdict
    assert "exit 1" in verdict
    # The step must run even when earlier steps failed, otherwise a refused
    # action could leave the run green.
    assert "      - name: Fail unless the UI-only release gate passed\n        if: always()" in text


# ---------------------------------------------------------------------------
# The forced-command gateway, executed rather than described.
# ---------------------------------------------------------------------------

GATEWAY_ACCEPTED: tuple[tuple[str, str], ...] = (
    (f"deploy {SHA}", f" {SHA}"),
    (f"deploy-ui {SHA}", f" ui-deploy {SHA}"),
    (f"rollback-ui {SHA}", f" rollback-ui {SHA}"),
    ("diagnose-learning", " diagnose-learning"),
)

GATEWAY_REFUSED: tuple[str, ...] = (
    "",
    "id",
    "sh",
    f"bash -c 'deploy-ui {SHA}'",
    f"deploy-ui {SHA} && id",
    f"deploy-ui {SHA} ; id",
    f"deploy-ui {SHA}\nid",
    f" deploy-ui {SHA}",
    f"deploy-ui {SHA[:39]}",
    f"deploy-ui {SHA}0",
    f"deploy-ui {SHA.upper()}",
    "deploy-ui",
    "rollback-ui",
    f"deploy-learning {SHA}",
    f"deploy-ui --sha {SHA}",
    f"deploy-ui $(id)",
    "diagnose-learning extra",
    f"deploy {SHA}; id",
)


def _gateway_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    log = tmp_path / "sudo.log"
    shim = tmp_path / "bin"
    shim.mkdir()
    sudo = shim / "sudo"
    sudo.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$SUDO_LOG\"\nexit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    sudo.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}", "SUDO_LOG": str(log)}
    return env, log


@requires_bash
def test_ac_007_gateway_forwards_only_the_bounded_allowlist(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-007: the gateway is an allowlist, not a shell."""
    env, log = _gateway_env(tmp_path)
    for command, expected in GATEWAY_ACCEPTED:
        log.write_text("", encoding="utf-8")
        proc = subprocess.run(
            [_bash() or "bash", GATEWAY.as_posix()],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            env={**env, "SSH_ORIGINAL_COMMAND": command},
        )
        assert proc.returncode == 0, (command, proc.stderr)
        assert log.read_text(encoding="utf-8").strip() == f"/usr/local/sbin/ohm-deploy{expected}", command


@requires_bash
def test_ac_007_gateway_refuses_everything_else_without_executing(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-007: refused input never reaches sudo."""
    env, log = _gateway_env(tmp_path)
    for command in GATEWAY_REFUSED:
        log.write_text("", encoding="utf-8")
        proc = subprocess.run(
            [_bash() or "bash", GATEWAY.as_posix()],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            env={**env, "SSH_ORIGINAL_COMMAND": command},
        )
        assert proc.returncode == 64, (command, proc.stdout, proc.stderr)
        assert "refusing command" in proc.stderr, command
        assert log.read_text(encoding="utf-8") == "", command
