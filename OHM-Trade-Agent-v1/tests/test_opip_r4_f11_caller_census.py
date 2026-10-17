"""ATDD-R4-F11 caller-census remediation: the protective-mutation census.

The census helper lives in the precutover protection test module and decides
whether a source file reaches an approved protective mutator. It must recognise
every statically identifiable call form, and it must stay closed for values that
merely share a mutator's name and for dynamic resolution, which cannot be proven
statically.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    parse_scope_contract,
    read_increment_pointer,
)
from tests.test_opip_f11_precutover_protection import (  # noqa: E402
    _source_calls_tokens,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R4-F11-caller-census-remediation"
CONTRACT_PATH = (
    APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
)
TOKENS = frozenset({"admit_paper_opportunity", "trigger_paper_protection_action"})


def _detects(source: str) -> bool:
    return _source_calls_tokens(source, TOKENS)


def test_ac_001_the_census_detects_aliased_imports_and_scheduled_callbacks():
    """ATDD-R4-F11-caller-census-remediation/AC-001: aliased imports and scheduled callbacks are detected."""

    assert _detects(
        "from app.canonical import admit_paper_opportunity as ap\nap(request)\n"
    )
    assert _detects(
        "import app.services.paper_v2_protection_runtime as rt\n"
        "rt.trigger_paper_protection_action(request)\n"
    )
    assert _detects("scheduler.add_job(admit_paper_opportunity, 'interval')\n")
    assert _detects("run(callback=trigger_paper_protection_action)\n")


def test_ac_001_the_census_stays_closed_for_dynamic_and_same_name_controls():
    """ATDD-R4-F11-caller-census-remediation/AC-001: dynamic resolution and same-name controls stay outside the census."""

    assert not _detects("getattr(runtime, 'admit_paper_opportunity')(request)\n")
    assert not _detects("admit_paper_opportunity_flag = True\n")
    assert not _detects("record(admit_paper_opportunity_note)\n")
    assert not _detects("client.other_method(trigger_paper_protection_action_note)\n")


def test_ac_002_the_increment_is_test_only_and_pointer_consistent():
    """ATDD-R4-F11-caller-census-remediation/AC-002: the increment is test-only and self-consistent."""

    contract = parse_scope_contract(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract.increment == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert read_increment_pointer() == INCREMENT
    assert set(contract.traceability) == set(contract.acceptance_criteria)
    assert set(contract.implementation_map) == set(contract.acceptance_criteria)

    mapped = {path for paths in contract.implementation_map.values() for path in paths}
    assert not [path for path in mapped if "/app/" in path], mapped
    missing = sorted(path for path in mapped if not (APP_ROOT.parent / path).exists())
    assert missing == [], missing
