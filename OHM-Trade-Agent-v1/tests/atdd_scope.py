"""Parse ATDD scope contracts and stop unapproved scope.

Stdlib only. This module does not import application runtime code.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REQUIRED_SECTIONS: tuple[str, ...] = (
    "INCREMENT",
    "OWNER-APPROVED INTENT",
    "ARCHITECTURE REFERENCES",
    "APPROVED ACCEPTANCE CRITERIA",
    "EXPLICITLY OUT OF SCOPE",
    "FROZEN BOUNDARIES",
    "ACCEPTANCE TEST TRACEABILITY",
    "IMPLEMENTATION MAP",
    "DEFERRED DISCOVERIES",
    "UNAPPROVED SCOPE CHANGES",
)

PASS = "PASS"
SCOPE_CHANGE_REQUIRED = "SCOPE_CHANGE_REQUIRED"
TRACEABILITY_GAP = "TRACEABILITY_GAP"

_AC_HEADING = re.compile(r"(AC-\d{3}):")
_SCOPED_AC = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9._-]*)/(AC-\d{3})\b")
_ARROW = re.compile(r"(AC-\d{3})\s*->\s*(\S+)")
_LABEL = re.compile(r"^(GIVEN|WHEN|THEN):\s*(.*)$")
_INCREMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_MISSING_BASE = "0" * 40

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
ACTIVE_INCREMENT_PATH = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"


@dataclass(frozen=True)
class ScopeContract:
    increment: str
    sections: dict[str, str]
    acceptance_criteria: dict[str, str]
    traceability: dict[str, tuple[str, ...]]
    implementation_map: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class AcceptanceIndex:
    by_ac: dict[tuple[str, str], frozenset[str]]
    unscoped_tests: tuple[str, ...]


@dataclass(frozen=True)
class ScopeCheckResult:
    status: str
    reasons: tuple[str, ...]
    scope_reasons: tuple[str, ...] = ()
    trace_reasons: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == PASS


def normalize_repo_path(path: str) -> str:
    """Return one repository-root path.

    Git diffs and implementation-map entries already use this namespace.
    A leading ``./`` and Windows separators are normalized. The
    ``OHM-Trade-Agent-v1/`` prefix is preserved, so an application file and a
    wrapper-root file with the same suffix stay distinct. ``..`` and other
    escaping forms are rejected.
    """
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        raise ValueError(f"path escapes the repository: {path}")
    parts: list[str] = []
    for part in normalized.split("/"):
        if part == ".":
            continue
        if part == "":
            if parts:
                raise ValueError(f"path escapes the repository: {path}")
            continue
        if part == "..":
            raise ValueError(f"path escapes the repository: {path}")
        parts.append(part)
    if not parts:
        raise ValueError(f"path escapes the repository: {path}")
    return "/".join(parts)


def parse_scope_contract(text: str) -> ScopeContract:
    sections = _section_map(text)
    for name, body in sections.items():
        if body == "":
            raise ValueError(f"empty section: {name}")
    increment = sections["INCREMENT"]
    if "\n" in increment or _INCREMENT.fullmatch(increment) is None:
        raise ValueError("INCREMENT must be a single identifier")
    criteria = _parse_criteria(sections["APPROVED ACCEPTANCE CRITERIA"])
    traceability = _parse_arrows(
        sections["ACCEPTANCE TEST TRACEABILITY"],
        kind="traceability",
    )
    implementation_map = _parse_arrows(
        sections["IMPLEMENTATION MAP"],
        kind="implementation",
    )
    return ScopeContract(
        increment=increment,
        sections=sections,
        acceptance_criteria=criteria,
        traceability=traceability,
        implementation_map=implementation_map,
    )


def load_scope_contracts(directory: Path) -> tuple[ScopeContract, ...]:
    paths = sorted(path for path in directory.glob("*.md") if path.is_file())
    if not paths:
        raise ValueError(f"no scope contracts in {directory}")
    contracts = tuple(
        parse_scope_contract(path.read_text(encoding="utf-8")) for path in paths
    )
    increments = [contract.increment for contract in contracts]
    if len(increments) != len(set(increments)):
        raise ValueError("active increment is ambiguous")
    return contracts


def read_increment_pointer(path: Path = ACTIVE_INCREMENT_PATH) -> str:
    if not path.is_file():
        raise ValueError("active increment pointer is missing")
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) != 1 or _INCREMENT.fullmatch(lines[0]) is None:
        raise ValueError("active increment pointer is ambiguous")
    return lines[0]


def load_acceptance_index(tests_dir: Path) -> AcceptanceIndex:
    by_ac: dict[tuple[str, str], set[str]] = {}
    unscoped: list[str] = []
    app_root = tests_dir.parent
    for path in sorted(tests_dir.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(app_root).as_posix()
        found, missing = acceptance_ids_in_source(
            path.read_text(encoding="utf-8"),
            relative,
        )
        for key, nodes in found.items():
            by_ac.setdefault(key, set()).update(nodes)
        unscoped.extend(missing)
    return AcceptanceIndex(
        by_ac={key: frozenset(nodes) for key, nodes in by_ac.items()},
        unscoped_tests=tuple(unscoped),
    )


def acceptance_ids_in_source(
    source: str,
    relative_path: str,
) -> tuple[dict[tuple[str, str], set[str]], list[str]]:
    tree = ast.parse(source)
    module_mark = _pytestmark_in_body(tree.body)
    found: dict[tuple[str, str], set[str]] = {}
    unscoped: list[str] = []

    def visit(body: list[ast.stmt], class_names: list[str], inherited: bool) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                own = _decorators_mark(stmt.decorator_list) or _pytestmark_in_body(
                    stmt.body
                )
                visit(stmt.body, class_names + [stmt.name], inherited or own)
                continue
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not stmt.name.startswith("test_"):
                continue
            if not (inherited or _decorators_mark(stmt.decorator_list)):
                continue
            node_id = "::".join([relative_path, *class_names, stmt.name])
            docstring = ast.get_docstring(stmt) or ""
            keys = set(_SCOPED_AC.findall(docstring))
            if not keys:
                unscoped.append(node_id)
                continue
            for key in keys:
                found.setdefault(key, set()).add(node_id)

    visit(tree.body, [], module_mark)
    return found, unscoped


def check_scope(
    contract: ScopeContract,
    acceptance: AcceptanceIndex,
    changed_files: tuple[str, ...] = (),
) -> ScopeCheckResult:
    scope_reasons: list[str] = []
    trace_reasons: list[str] = []
    if contract.sections["UNAPPROVED SCOPE CHANGES"] != "NONE":
        scope_reasons.append("UNAPPROVED SCOPE CHANGES is not NONE")
    for ac_id, body in contract.acceptance_criteria.items():
        for problem in behavior_label_problems(body):
            trace_reasons.append(f"{ac_id} {problem}")
        declared = set(contract.traceability.get(ac_id, ()))
        known = set(acceptance.by_ac.get((contract.increment, ac_id), frozenset()))
        if not declared:
            trace_reasons.append(f"{ac_id} has no acceptance test trace")
        elif declared != known:
            trace_reasons.append(
                f"{ac_id} traceability does not match acceptance tests"
            )
        if ac_id not in contract.implementation_map:
            trace_reasons.append(f"{ac_id} has no implementation map entry")
    for ac_id in contract.traceability:
        if ac_id not in contract.acceptance_criteria:
            scope_reasons.append(f"trace cites unapproved {ac_id}")
    for ac_id in contract.implementation_map:
        if ac_id not in contract.acceptance_criteria:
            scope_reasons.append(f"implementation map cites unapproved {ac_id}")
    _authorize_changed_files(changed_files, _allow_paths(contract), scope_reasons)
    return _result(scope_reasons, trace_reasons)


def check_contracts(
    contracts: tuple[ScopeContract, ...],
    acceptance: AcceptanceIndex,
    changed_files: tuple[str, ...] | None = None,
    *,
    active_increment: str,
) -> ScopeCheckResult:
    scope_reasons: list[str] = []
    trace_reasons: list[str] = []
    selected = _select_active(contracts, active_increment)
    if isinstance(selected, str):
        return _result([selected], [])
    approved: set[tuple[str, str]] = set()
    for contract in contracts:
        approved.update(
            (contract.increment, ac_id) for ac_id in contract.acceptance_criteria
        )
        result = check_scope(contract, acceptance)
        scope_reasons.extend(
            f"{contract.increment}: {reason}" for reason in result.scope_reasons
        )
        trace_reasons.extend(
            f"{contract.increment}: {reason}" for reason in result.trace_reasons
        )
    for node_id in acceptance.unscoped_tests:
        trace_reasons.append(f"acceptance test cites no AC: {node_id}")
    for key in sorted(set(acceptance.by_ac) - approved):
        scope_reasons.append(
            f"acceptance test cites unapproved criterion {key[0]}/{key[1]}"
        )
    if changed_files is None:
        scope_reasons.append("changed-file set was not provided")
    else:
        _authorize_changed_files(changed_files, _allow_paths(selected), scope_reasons)
    return _result(scope_reasons, trace_reasons)


def behavior_label_problems(body: str) -> tuple[str, ...]:
    blocks: list[tuple[str, str]] = []
    label: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        match = _LABEL.match(line.strip())
        if match:
            if label is not None:
                blocks.append((label, "\n".join(buf).strip()))
            label = match.group(1)
            rest = match.group(2).strip()
            buf = [rest] if rest else []
            continue
        if label is not None:
            buf.append(line)
    if label is not None:
        blocks.append((label, "\n".join(buf).strip()))
    present = {name for name, _text in blocks}
    problems: list[str] = []
    missing = [name for name in ("GIVEN", "WHEN", "THEN") if name not in present]
    if missing:
        problems.append("missing " + ", ".join(missing))
    empty = [name for name, text in blocks if text == ""]
    if empty:
        problems.append("empty " + ", ".join(dict.fromkeys(empty)))
    return tuple(problems)


def parse_name_status(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")):
            if len(parts) != 3:
                raise ValueError(f"invalid rename status: {raw}")
            paths.extend((parts[1], parts[2]))
            continue
        if len(parts) != 2:
            raise ValueError(f"invalid name status: {raw}")
        paths.append(parts[1])
    return tuple(paths)


def git_changed_paths(repo_root: Path, base: str) -> tuple[str, ...]:
    if not base or base.startswith("-") or base == _MISSING_BASE:
        raise ValueError("changed-file base is missing")
    completed = subprocess.run(
        ["git", "diff", "--name-status", "--find-renames", f"{base}...HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ValueError(f"git diff failed: {detail}")
    return parse_name_status(completed.stdout)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(list(sys.argv[1:] if argv is None else argv))
    except ValueError as exc:
        print(SCOPE_CHANGE_REQUIRED)
        print(str(exc))
        return 2


def _main(args: list[str]) -> int:
    explicit: str | None = None
    git_base: str | None = None
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--increment":
            explicit = _take_option(args, index)
            index += 2
            continue
        if arg == "--git-base":
            git_base = _take_option(args, index)
            index += 2
            continue
        if arg.startswith("--"):
            raise ValueError(f"unknown argument: {arg}")
        positional.append(arg)
        index += 1
    pointer = read_increment_pointer()
    if explicit is not None and explicit != pointer:
        raise ValueError("active increment pointer does not match --increment")
    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    acceptance = load_acceptance_index(APP_ROOT / "tests")
    changed: tuple[str, ...] | None
    if git_base is None and not positional:
        changed = None
    else:
        paths = list(positional)
        if git_base is not None:
            paths.extend(git_changed_paths(REPO_ROOT, git_base))
        changed = tuple(paths)
    result = check_contracts(
        contracts,
        acceptance,
        changed,
        active_increment=explicit or pointer,
    )
    print(result.status)
    for reason in result.reasons:
        print(reason)
    if result.status == PASS:
        return 0
    if result.status == SCOPE_CHANGE_REQUIRED:
        return 2
    return 1


def _take_option(args: list[str], index: int) -> str:
    if index + 1 >= len(args) or args[index + 1].startswith("--"):
        raise ValueError(f"{args[index]} requires a value")
    return args[index + 1]


def _select_active(
    contracts: tuple[ScopeContract, ...],
    active_increment: str,
) -> ScopeContract | str:
    if not active_increment or _INCREMENT.fullmatch(active_increment) is None:
        return "active increment is missing"
    matches = [
        contract for contract in contracts if contract.increment == active_increment
    ]
    if len(matches) != 1:
        return f"active increment is unknown or ambiguous: {active_increment}"
    return matches[0]


def _authorize_changed_files(
    changed_files: tuple[str, ...],
    allow: set[str],
    scope_reasons: list[str],
) -> None:
    for changed in changed_files:
        try:
            normalized = normalize_repo_path(changed)
        except ValueError as exc:
            scope_reasons.append(str(exc))
            continue
        if normalized not in allow:
            scope_reasons.append(
                f"changed file not mapped to an approved AC: {normalized}"
            )


def _allow_paths(contract: ScopeContract) -> set[str]:
    return {
        path
        for paths in contract.implementation_map.values()
        for path in paths
    }


def _result(scope_reasons: list[str], trace_reasons: list[str]) -> ScopeCheckResult:
    if scope_reasons:
        return ScopeCheckResult(
            status=SCOPE_CHANGE_REQUIRED,
            reasons=tuple(scope_reasons + trace_reasons),
            scope_reasons=tuple(scope_reasons),
            trace_reasons=tuple(trace_reasons),
        )
    if trace_reasons:
        return ScopeCheckResult(
            status=TRACEABILITY_GAP,
            reasons=tuple(trace_reasons),
            scope_reasons=(),
            trace_reasons=tuple(trace_reasons),
        )
    return ScopeCheckResult(status=PASS, reasons=())


def _section_map(text: str) -> dict[str, str]:
    headings = {f"{name}:" for name in REQUIRED_SECTIONS}
    found: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped in headings:
            name = stripped[:-1]
            if name in found:
                raise ValueError(f"duplicate section: {name}")
            current = name
            found[name] = []
            order.append(name)
            continue
        if current is None:
            if stripped == "":
                continue
            raise ValueError(f"text before first section: {stripped}")
        found[current].append(raw.rstrip())
    missing = [name for name in REQUIRED_SECTIONS if name not in found]
    if missing:
        raise ValueError("missing sections: " + ", ".join(missing))
    if order != list(REQUIRED_SECTIONS):
        raise ValueError("section order mismatch")
    return {name: "\n".join(lines).strip() for name, lines in found.items()}


def _parse_criteria(body: str) -> dict[str, str]:
    criteria: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        match = _AC_HEADING.fullmatch(line.strip())
        if match is not None:
            ac_id = match.group(1)
            if ac_id in criteria:
                raise ValueError(f"duplicate criterion: {ac_id}")
            current = ac_id
            criteria[ac_id] = []
            continue
        if current is None:
            if line.strip() == "":
                continue
            raise ValueError("criterion text outside an AC block")
        criteria[current].append(line.rstrip())
    if not criteria:
        raise ValueError("no acceptance criteria")
    return {ac_id: "\n".join(lines).strip() for ac_id, lines in criteria.items()}


def _parse_arrows(body: str, *, kind: str) -> dict[str, tuple[str, ...]]:
    mapping: dict[str, list[str]] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == "":
            continue
        match = _ARROW.fullmatch(stripped)
        if match is None:
            raise ValueError(f"invalid {kind} line: {stripped}")
        ac_id, target = match.group(1), match.group(2)
        if kind == "implementation":
            target = normalize_repo_path(target)
        mapping.setdefault(ac_id, []).append(target)
    return {ac_id: tuple(targets) for ac_id, targets in mapping.items()}


def _decorators_mark(decorators: list[ast.expr]) -> bool:
    marked = False
    for decorator in decorators:
        expr = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(expr, ast.Name):
            if "acceptance" in expr.id.lower():
                raise ValueError(f"unsupported decorator alias: {expr.id}")
            continue
        if isinstance(expr, ast.Attribute) and expr.attr == "acceptance":
            marked = True
    return marked


def _pytestmark_in_body(body: list[ast.stmt]) -> bool:
    found = False
    for stmt in body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == "pytestmark":
                raise ValueError("unsupported annotated pytestmark")
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in stmt.targets):
            continue
        found = found or _value_has_acceptance(stmt.value)
    return found


def _value_has_acceptance(expr: ast.AST) -> bool:
    value = expr.func if isinstance(expr, ast.Call) else expr
    if isinstance(value, ast.Attribute):
        return value.attr == "acceptance"
    if isinstance(value, (ast.List, ast.Tuple)):
        return any(_value_has_acceptance(item) for item in value.elts)
    if isinstance(value, ast.Name):
        raise ValueError(f"unsupported pytestmark alias: {value.id}")
    raise ValueError("unsupported acceptance marker form")


if __name__ == "__main__":
    raise SystemExit(main())
