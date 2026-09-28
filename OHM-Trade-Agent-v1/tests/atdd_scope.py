"""Parse ATDD scope contracts and stop unapproved scope.

Stdlib only. This module does not import application runtime code.
"""

from __future__ import annotations

import ast
import re
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
_AC_ID = re.compile(r"\bAC-\d{3}\b")
_ARROW = re.compile(r"(AC-\d{3})\s*->\s*(\S+)")
_BEHAVIOR_LABEL = re.compile(r"(?m)^(GIVEN|WHEN|THEN):\s*(?:\S.*)?$")
_INCREMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class ScopeContract:
    increment: str
    sections: dict[str, str]
    acceptance_criteria: dict[str, str]
    traceability: dict[str, tuple[str, ...]]
    implementation_map: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class AcceptanceIndex:
    by_ac: dict[str, frozenset[str]]
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
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    prefix = "OHM-Trade-Agent-v1/"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix) :]
    return normalized


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
    return tuple(parse_scope_contract(path.read_text(encoding="utf-8")) for path in paths)


def load_acceptance_index(tests_dir: Path) -> AcceptanceIndex:
    by_ac: dict[str, set[str]] = {}
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
        for ac_id, nodes in found.items():
            by_ac.setdefault(ac_id, set()).update(nodes)
        unscoped.extend(missing)
    return AcceptanceIndex(
        by_ac={ac_id: frozenset(nodes) for ac_id, nodes in by_ac.items()},
        unscoped_tests=tuple(unscoped),
    )


def acceptance_ids_in_source(
    source: str,
    relative_path: str,
) -> tuple[dict[str, set[str]], list[str]]:
    tree = ast.parse(source)
    found: dict[str, set[str]] = {}
    unscoped: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        if not _has_acceptance_mark(node):
            continue
        node_id = f"{relative_path}::{node.name}"
        docstring = ast.get_docstring(node) or ""
        ac_ids = set(_AC_ID.findall(docstring))
        if not ac_ids:
            unscoped.append(node_id)
            continue
        for ac_id in ac_ids:
            found.setdefault(ac_id, set()).add(node_id)
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
        labels = set(_BEHAVIOR_LABEL.findall(body))
        missing = [label for label in ("GIVEN", "WHEN", "THEN") if label not in labels]
        if missing:
            trace_reasons.append(f"{ac_id} missing {', '.join(missing)}")
        declared = set(contract.traceability.get(ac_id, ()))
        known = set(acceptance.by_ac.get(ac_id, frozenset()))
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
    allow = {
        path
        for paths in contract.implementation_map.values()
        for path in paths
    }
    for changed in changed_files:
        normalized = normalize_repo_path(changed)
        if normalized not in allow:
            scope_reasons.append(
                f"changed file not mapped to an approved AC: {normalized}"
            )
    return _result(scope_reasons, trace_reasons)


def check_contracts(
    contracts: tuple[ScopeContract, ...],
    acceptance: AcceptanceIndex,
    changed_files: tuple[str, ...] = (),
) -> ScopeCheckResult:
    scope_reasons: list[str] = []
    trace_reasons: list[str] = []
    approved: set[str] = set()
    for contract in contracts:
        approved.update(contract.acceptance_criteria)
        result = check_scope(contract, acceptance)
        scope_reasons.extend(
            f"{contract.increment}: {reason}" for reason in result.scope_reasons
        )
        trace_reasons.extend(
            f"{contract.increment}: {reason}" for reason in result.trace_reasons
        )
    for node_id in acceptance.unscoped_tests:
        trace_reasons.append(f"acceptance test cites no AC: {node_id}")
    for ac_id in sorted(set(acceptance.by_ac) - approved):
        scope_reasons.append(f"acceptance test cites unapproved criterion {ac_id}")
    allow: set[str] = set()
    for contract in contracts:
        for paths in contract.implementation_map.values():
            allow.update(paths)
    for changed in changed_files:
        normalized = normalize_repo_path(changed)
        if normalized not in allow:
            scope_reasons.append(
                f"changed file not mapped to an approved AC: {normalized}"
            )
    return _result(scope_reasons, trace_reasons)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    changed = tuple(arg for arg in args if arg.strip())
    app_root = Path(__file__).resolve().parents[1]
    contracts = load_scope_contracts(app_root / "docs" / "atdd" / "scope-contracts")
    acceptance = load_acceptance_index(app_root / "tests")
    result = check_contracts(contracts, acceptance, changed)
    print(result.status)
    for reason in result.reasons:
        print(reason)
    if result.status == PASS:
        return 0
    if result.status == SCOPE_CHANGE_REQUIRED:
        return 2
    return 1


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


def _has_acceptance_mark(node: ast.FunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "acceptance":
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
