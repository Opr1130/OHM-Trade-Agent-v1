"""Acceptance checks for the v6 canonical file roll design note.

Documentation-only increment: these tests read files and parse this increment's
scope contract. They import no application runtime module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import parse_scope_contract  # noqa: E402

INCREMENT = "ATDD-OPIP-V6-CANONICAL-FILE-ROLL-v1"
NOTE = APP_ROOT / "docs" / "architecture" / "CANONICAL_FILE_ROLL.md"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"

EXPECTED_MAP = frozenset(
    {
        "OHM-Trade-Agent-v1/docs/architecture/CANONICAL_FILE_ROLL.md",
        "OHM-Trade-Agent-v1/tests/test_v6_canonical_file_roll_v1.py",
        f"OHM-Trade-Agent-v1/docs/atdd/scope-contracts/{INCREMENT}.md",
        "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
    }
)

NON_DOCUMENTATION_PREFIXES = (
    "OHM-Trade-Agent-v1/app/",
    "OHM-Trade-Agent-v1/deploy/",
    "OHM-Trade-Agent-v1/freqtrade/",
    "OHM-Trade-Agent-v1/control-center-v2/",
    "OHM-Trade-Agent-v1/tools/v6_agent/",
    "OHM-Trade-Agent-v1/docker-compose",
    "OHM-Trade-Agent-v1/Dockerfile",
    ".github/",
)


@pytest.mark.acceptance
def test_note_covers_roll_shipping_hash_retention_readers_and_open_questions() -> None:
    """ATDD-OPIP-V6-CANONICAL-FILE-ROLL-v1/AC-001: complete design coverage.

    GIVEN the v6 audit section 9 facts about the single 5.5 GB canonical file
    WHEN the canonical file roll design note is added
    THEN it covers rolling, shipping, hash verification, the 14-day retention and
    deletion rule, the reader changes and the open questions, and names the slices
    the design still needs
    """
    text = NOTE.read_text(encoding="utf-8")
    for heading in (
        "## 3. Rolling",
        "## 4. Shipping to the data box",
        "## 5. Hash verification",
        "## 6. Retention",
        "## 7. Reader changes",
        "## 9. Open questions for the owner",
        "## 10. Slices this design needs",
    ):
        assert heading in text, heading
    for required in (
        "opip_canonical_v1_",
        "single-writer lock",
        "14-day",
        "SHA-256",
        "receipt",
        "active file",
        "reader",
        "unchecked",
    ):
        assert required in text, required
    assert "design only" in text.lower()
    assert "no Kraken order" in text


@pytest.mark.acceptance
def test_increment_is_documentation_only_and_changes_nothing_else() -> None:
    """ATDD-OPIP-V6-CANONICAL-FILE-ROLL-v1/AC-002: nothing else changes.

    GIVEN the repository and this increment's approved scope contract
    WHEN the implementation map and the runtime sources are inspected
    THEN the only authorized paths are the design note, the acceptance test, the
    contract and the active-increment pointer, no runtime, deploy, compose or
    workflow path is authorized, and no application module reads the note
    """
    contract = parse_scope_contract(CONTRACT.read_text(encoding="utf-8"))
    assert contract.increment == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"].strip() == "NONE"

    mapped = {
        path for paths in contract.implementation_map.values() for path in paths
    }
    assert mapped == set(EXPECTED_MAP)
    for path in mapped:
        assert (REPO_ROOT / path).is_file(), path
        assert not path.startswith(NON_DOCUMENTATION_PREFIXES), path

    offenders: list[str] = []
    for source in sorted((APP_ROOT / "app").rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        if "CANONICAL_FILE_ROLL" in source.read_text(
            encoding="utf-8", errors="ignore"
        ):
            offenders.append(source.relative_to(APP_ROOT).as_posix())
    assert offenders == []

    # The pointer is movable, so this increment must not own or pin it.
    pointer = POINTER.read_text(encoding="utf-8").strip()
    assert (CONTRACT_DIR / f"{pointer}.md").is_file(), pointer
