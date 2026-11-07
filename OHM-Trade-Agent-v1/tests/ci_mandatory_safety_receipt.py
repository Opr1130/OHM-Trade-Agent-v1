"""Fail-closed receipt for the mandatory safety suite.

Reads the JUnit XML that the mandatory-safety pytest invocation produced and
refuses to certify anything that was not actually executed and passed. A
skipped, missing, failed or absent test is a hard failure: the release CI step
fails, so the exact-SHA deployment gate cannot certify that commit.

This module is CI orchestration, not application runtime. It is stdlib only and
never touches production state or trading authority.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.ci_release_selection import (  # noqa: E402
    MANDATORY_SAFETY,
    MIN_MANDATORY_NODE_IDS,
    SelectionError,
    select_safety,
)

PASS = "MANDATORY_SAFETY=PASS"
FAILED = "MANDATORY_SAFETY=FAILED"
_PASSING = "passed"
_FAILURE_TAGS = frozenset({"failure", "error"})
_MAX_LISTED = 5


class ReceiptError(RuntimeError):
    """Evidence that cannot certify the mandatory safety suite."""


@dataclass(frozen=True)
class Receipt:
    """Certified executed evidence for the mandatory safety suite."""

    executed: int
    node_ids: int
    categories: int

    def lines(self) -> tuple[str, ...]:
        return (
            PASS,
            f"EXECUTED={self.executed}",
            f"MANDATORY_NODE_IDS={self.node_ids}",
            f"MANDATORY_CATEGORIES={self.categories}",
            "MANDATORY_SKIPPED=0",
        )


def _case_file(case: ET.Element) -> str | None:
    declared = case.get("file")
    if declared:
        return declared.replace("\\", "/")
    classname = case.get("classname") or ""
    if not classname:
        return None
    return classname.replace(".", "/") + ".py"


def _cases(report: Path) -> list[tuple[str | None, str, str]]:
    tree = ET.parse(report)
    cases: list[tuple[str | None, str, str]] = []
    for case in tree.getroot().iter("testcase"):
        outcome = _PASSING
        for child in case:
            if child.tag in _FAILURE_TAGS:
                outcome = child.tag
                break
            if child.tag == "skipped":
                outcome = "skipped"
        cases.append((_case_file(case), case.get("name") or "", outcome))
    return cases


def _node_id_satisfied(node_id: str, cases: list[tuple[str | None, str, str]]) -> bool:
    path, _, test_name = node_id.partition("::")
    for case_file, case_name, outcome in cases:
        if case_file != path:
            continue
        if case_name == test_name or case_name.startswith(test_name + "["):
            if outcome == _PASSING:
                return True
    return False


def _describe(cases: list[tuple[str | None, str, str]], outcome: str) -> str:
    names = [f"{path}::{name}" for path, name, state in cases if state == outcome]
    listed = ", ".join(names[:_MAX_LISTED])
    if len(names) > _MAX_LISTED:
        listed += f", +{len(names) - _MAX_LISTED} more"
    return listed


def _declared_selection(selection: Path | None) -> tuple[str, ...] | None:
    if selection is None:
        return None
    if not selection.is_file():
        raise ReceiptError(f"declared safety selection is missing: {selection}")
    declared = tuple(
        line.strip()
        for line in selection.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not declared:
        raise ReceiptError("declared safety selection is empty")
    return declared


def verify(report: Path, selection: Path | None = None) -> Receipt:
    """Certify the mandatory safety suite or raise :class:`ReceiptError`."""

    if not report.is_file() or report.stat().st_size == 0:
        raise ReceiptError(f"mandatory safety report is missing or empty: {report}")
    try:
        cases = _cases(report)
    except ET.ParseError as exc:
        raise ReceiptError(f"mandatory safety report is not valid JUnit XML: {exc}")
    if not cases:
        raise ReceiptError("mandatory safety report contains no test cases")

    empty_categories = [
        category for category, node_ids in MANDATORY_SAFETY.items() if not node_ids
    ]
    if empty_categories:
        raise ReceiptError(
            "mandatory safety categories are empty: " + ", ".join(empty_categories)
        )
    try:
        expected = select_safety()
    except SelectionError as exc:  # defensive: the suite must always resolve
        raise ReceiptError(f"mandatory safety selection failed: {exc}")
    if not expected:
        raise ReceiptError("mandatory safety selection is empty")
    if len(expected) < MIN_MANDATORY_NODE_IDS:
        raise ReceiptError(
            f"mandatory safety suite shrank to {len(expected)} node ids "
            f"(minimum {MIN_MANDATORY_NODE_IDS})"
        )

    failed = [case for case in cases if case[2] in _FAILURE_TAGS]
    if failed:
        raise ReceiptError(
            "mandatory safety tests failed or errored: " + _describe(cases, failed[0][2])
        )
    skipped = [case for case in cases if case[2] == "skipped"]
    if skipped:
        raise ReceiptError(
            "mandatory safety tests were skipped, not executed: "
            + _describe(cases, "skipped")
        )

    missing = [
        node_id for node_id in expected if not _node_id_satisfied(node_id, cases)
    ]
    if missing:
        listed = ", ".join(missing[:_MAX_LISTED])
        if len(missing) > _MAX_LISTED:
            listed += f", +{len(missing) - _MAX_LISTED} more"
        raise ReceiptError("mandatory safety tests did not execute and pass: " + listed)

    declared = _declared_selection(selection)
    if declared is not None:
        unknown = [target for target in declared if target not in expected]
        if unknown:
            raise ReceiptError(
                "declared safety selection contains non-mandatory targets: "
                + ", ".join(unknown[:_MAX_LISTED])
            )
        not_run = [node_id for node_id in expected if node_id not in declared]
        if not_run:
            raise ReceiptError(
                "declared safety selection omits mandatory targets: "
                + ", ".join(not_run[:_MAX_LISTED])
            )

    if len(cases) < len(expected):
        raise ReceiptError(
            f"executed test cases {len(cases)} are fewer than the "
            f"{len(expected)} mandatory node ids"
        )

    return Receipt(
        executed=len(cases),
        node_ids=len(expected),
        categories=len(MANDATORY_SAFETY),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Certify the mandatory safety suite from its JUnit report.",
    )
    parser.add_argument("--report", required=True)
    parser.add_argument("--selection", default=None)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        receipt = verify(
            Path(args.report),
            Path(args.selection) if args.selection else None,
        )
    except ReceiptError as exc:
        print(FAILED)
        print(f"reason: {exc}", file=sys.stderr)
        return 1
    for line in receipt.lines():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

