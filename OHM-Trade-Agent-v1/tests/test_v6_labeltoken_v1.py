"""Acceptance check: the label swap uses the workflow token."""

from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "v6-agent.yml"


@pytest.mark.acceptance
def test_label_swap_uses_workflow_token() -> None:
    """ATDD-OPIP-V6-LABELTOKEN-v1/AC-001"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'export GH_TOKEN="${{ github.token }}"' in text
    assert "-f 'labels[]=built'" in text
