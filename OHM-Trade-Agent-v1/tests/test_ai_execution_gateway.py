from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.ai_gateway.capture_usage import build_usage_record
from tools.ai_gateway.profiles import (
    PROFILES,
    profile_name_from_trigger,
    resolve_from_trigger,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "OHM-Trade-Agent-v1"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "opip-claude-code.yml"


def test_profile_defaults_to_standard() -> None:
    assert profile_name_from_trigger("@claude implement the feature") == "standard"
    profile = resolve_from_trigger("please @CLAUDE implement it")
    assert profile == PROFILES["standard"]


@pytest.mark.parametrize("name", ["cheap", "standard", "deep"])
def test_explicit_profiles_are_bounded(name: str) -> None:
    profile = resolve_from_trigger(f"@claude {name} do the work")
    assert profile.name == name
    assert 1 <= profile.max_turns <= 20
    assert 0 < profile.max_budget_usd <= 3.50


def test_trigger_requires_claude_marker() -> None:
    with pytest.raises(ValueError, match="@claude"):
        resolve_from_trigger("implement this")


def test_usage_capture_reads_result_and_never_needs_prompt(tmp_path: Path) -> None:
    execution = tmp_path / "execution.json"
    execution.write_text(
        json.dumps(
            [
                {
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "cache_read_input_tokens": 80,
                        }
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "total_cost_usd": 0.1234567,
                    "num_turns": 4,
                },
            ]
        ),
        encoding="utf-8",
    )

    record = build_usage_record(
        execution_file=execution,
        profile="cheap",
        provider="anthropic",
        model="haiku",
        max_turns=6,
        max_budget_usd=0.40,
        repository="owner/repo",
        run_id="123",
        event_name="issue_comment",
        actor="owner",
        action_conclusion="success",
    )

    assert record["observed"]["actual_cost_usd"] == 0.123457
    assert record["observed"]["num_turns"] == 4
    assert record["observed"]["input_tokens"] == 100
    assert record["observed"]["output_tokens"] == 20
    assert record["observed"]["cache_read_input_tokens"] == 80
    assert "prompt" not in json.dumps(record).lower()


def test_usage_capture_is_fail_soft_when_execution_file_missing(tmp_path: Path) -> None:
    record = build_usage_record(
        execution_file=tmp_path / "missing.json",
        profile="standard",
        provider="anthropic",
        model="sonnet",
        max_turns=12,
        max_budget_usd=1.50,
        repository="owner/repo",
        run_id="124",
        event_name="issue_comment",
        actor="owner",
        action_conclusion="failure",
    )
    assert record["observed"]["status"] == "execution_file_missing"
    assert record["observed"]["actual_cost_usd"] is None


def test_infrastructure_contract_keeps_claude_out_of_production_runtime() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    requirements = (APP_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "github.actor == github.repository_owner" in workflow
    assert "--max-budget-usd" in workflow
    assert "--max-turns" in workflow
    assert "--exclude-dynamic-system-prompt-sections" in workflow
    assert "--allowedTools" in workflow
    assert "ANTHROPIC_API_KEY" in workflow
    assert "secrets.GITHUB_TOKEN" in workflow
    assert "actions/upload-artifact@v4" in workflow

    assert "ANTHROPIC_API_KEY" not in compose
    assert "anthropic" not in requirements.casefold()
