"""Acceptance checks for the v6 build loop smoke-test increment."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    PASS,
    check_contracts,
    load_acceptance_index,
    load_scope_contracts,
    parse_scope_contract,
)

INCREMENT = "ATDD-OPIP-V6-SMOKE-v1"
SMOKE_DOC = APP_ROOT / "docs" / "v6" / "SMOKE_TEST.md"
CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT_PATH = CONTRACT_DIR / f"{INCREMENT}.md"
SLICE_PATHS = (
    "OHM-Trade-Agent-v1/docs/v6/SMOKE_TEST.md",
    "OHM-Trade-Agent-v1/tests/test_v6_smoke_v1.py",
    "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
    "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SMOKE-v1.md",
)


@pytest.mark.acceptance
def test_smoke_doc_contains_the_single_line() -> None:
    """ATDD-OPIP-V6-SMOKE-v1/AC-001"""
    text = SMOKE_DOC.read_text(encoding="utf-8")
    assert text.splitlines() == ["v6 build loop smoke test"]


@pytest.mark.acceptance
def test_smoke_scope_contract_maps_every_changed_file() -> None:
    """ATDD-OPIP-V6-SMOKE-v1/AC-002"""
    contract = parse_scope_contract(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract.increment == INCREMENT
    mapped = {
        path for paths in contract.implementation_map.values() for path in paths
    }
    for changed in SLICE_PATHS:
        assert changed in mapped, changed
    result = check_contracts(
        load_scope_contracts(CONTRACT_DIR),
        load_acceptance_index(APP_ROOT / "tests"),
        SLICE_PATHS,
        active_increment=INCREMENT,
    )
    assert result.status == PASS, result.reasons
