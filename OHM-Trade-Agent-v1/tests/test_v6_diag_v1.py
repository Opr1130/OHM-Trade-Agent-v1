"""Acceptance check: advance.py logs the state it decides on."""

from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "v6_agent" / "advance.py"


@pytest.mark.acceptance
def test_advance_prints_what_it_sees() -> None:
    """ATDD-OPIP-V6-DIAG-v1/AC-001"""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "queued=" in text
    assert "open_v6_prs=" in text
