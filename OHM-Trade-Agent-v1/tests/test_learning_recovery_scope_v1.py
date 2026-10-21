from pathlib import Path

import pytest

REQUIRED_SECTIONS = (
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


@pytest.mark.acceptance
def test_active_increment_points_to_existing_contract():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-005: contract criterion."""
    atdd = Path(__file__).resolve().parents[1] / "docs" / "atdd"
    pointer = (atdd / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()
    contract = atdd / "scope-contracts" / f"{pointer}.md"
    assert contract.is_file(), pointer
    text = contract.read_text(encoding="utf-8")
    assert f"INCREMENT:\n{pointer}" in text
    for section in REQUIRED_SECTIONS:
        assert f"{section}:" in text, section
    assert "UNAPPROVED SCOPE CHANGES:\nNONE" in text
