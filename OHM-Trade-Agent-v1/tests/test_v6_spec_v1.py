"""Acceptance check for the v6 spec document."""

from __future__ import annotations

from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parents[1] / "docs" / "V6_SPEC.md"


@pytest.mark.acceptance
def test_spec_has_required_sections() -> None:
    """ATDD-OPIP-V6-SPEC-v1/AC-001"""
    text = SPEC.read_text(encoding="utf-8")
    for heading in (
        "Operating model",
        "Owner decisions in force",
        "Slice rules",
        "First slices",
        "Safety boundary",
    ):
        assert heading in text
