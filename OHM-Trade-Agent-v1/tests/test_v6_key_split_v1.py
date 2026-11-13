"""Acceptance check for the dedicated v6 DeepSeek credential."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "v6-agent.yml"


@pytest.mark.acceptance
def test_v6_agent_uses_only_the_v6_key() -> None:
    """ATDD-OPIP-V6-KEY-v1/AC-901"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.V6_DEEPSEEK_API_KEY" in text
    assert not re.search(r"secrets\.DEEPSEEK_API_KEY", text)
