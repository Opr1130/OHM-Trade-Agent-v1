"""Acceptance tests for behavior-preserving, source-level unused import cleanup."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from tests.atdd_scope import (  # noqa: E402
    SCOPE_CHANGE_REQUIRED,
    check_contracts,
    load_acceptance_index,
    load_scope_contracts,
)

pytestmark = pytest.mark.acceptance

TARGETS = (
    ("app/opip/events/identity.py", "timezone"),
    ("app/opip/events/provider_health.py", "timezone"),
    ("app/opip/streaming/telemetry.py", "Any"),
    ("app/services/opportunity_monitor_queue.py", "asdict"),
    ("app/opip/risk/notifier.py", "Any"),
    ("app/services/freqtrade_result_ingest.py", "Iterable"),
)


def test_six_unneeded_import_bindings_absent_and_syntax_valid():
    """ATDD-RUFF-IMPORT-CLEANUP-M01/AC-001: six specific Ruff F401 imports retired."""
    for path, unused in TARGETS:
        source = (APP / path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path)
        imports = {
            (alias.asname or alias.name)
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert unused not in imports, (path, unused)


def test_ruff_change_set_remains_strictly_whitelisted():
    """ATDD-RUFF-IMPORT-CLEANUP-M01/AC-002: unrelated runtime mutations fail closed."""
    contracts = load_scope_contracts(APP / "docs" / "atdd" / "scope-contracts")
    acceptance = load_acceptance_index(APP / "tests")
    allowed = check_contracts(
        contracts,
        acceptance,
        ("OHM-Trade-Agent-v1/app/opip/events/identity.py",),
        active_increment="ATDD-SCANNER-RELIABILITY-M01",
    )
    assert allowed.passed, allowed.reasons
    blocked = check_contracts(
        contracts,
        acceptance,
        (
            "OHM-Trade-Agent-v1/app/opip/events/identity.py",
            "OHM-Trade-Agent-v1/app/opip/canonical/writer.py",
        ),
        active_increment="ATDD-SCANNER-RELIABILITY-M01",
    )
    assert blocked.status == SCOPE_CHANGE_REQUIRED
