from __future__ import annotations

from pathlib import Path

import pytest

from tools.ai_gateway.gemini_profiles import PROFILES, resolve_profile
from tools.ai_gateway.run_gemini_review import (
    INTERACTIONS_ENDPOINT,
    build_payload,
    extract_output_text,
    usage_summary,
)


def test_gemini_default_trigger_routes_to_quick_review() -> None:
    profile = resolve_profile("@gemini please review this")
    assert profile.name == "quick-review"
    assert profile.model == "gemini-3.7-flash"
    assert profile.thinking_level == "medium"


@pytest.mark.parametrize(
    ("trigger", "profile_name", "model"),
    [
        ("@gemini quick-review", "quick-review", "gemini-3.7-flash"),
        ("@gemini architecture-review", "architecture-review", "gemini-3.1-pro-preview"),
        ("@gemini ml-audit", "ml-audit", "gemini-3.1-pro-preview"),
        ("@gemini security-review", "security-review", "gemini-3.1-pro-preview"),
    ],
)
def test_gemini_named_profiles(trigger: str, profile_name: str, model: str) -> None:
    profile = resolve_profile(trigger)
    assert profile.name == profile_name
    assert profile.model == model


def test_gemini_unknown_trigger_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_profile("please review this")


def test_payload_is_read_only_and_has_no_tools() -> None:
    payload = build_payload(
        model="gemini-3.7-flash",
        system_instruction="review only",
        review_input="diff data",
        thinking_level="medium",
        max_output_tokens=8192,
    )
    assert payload["store"] is False
    assert "tools" not in payload
    assert payload["generation_config"]["thinking_level"] == "medium"
    assert payload["generation_config"]["max_output_tokens"] == 8192


def test_extract_output_text_uses_last_model_output() -> None:
    body = {
        "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "old"}]},
            {"type": "user_input", "content": [{"type": "text", "text": "ignored"}]},
            {
                "type": "model_output",
                "content": [
                    {"type": "text", "text": "PASS"},
                    {"type": "text", "text": "No findings."},
                ],
            },
        ]
    }
    assert extract_output_text(body) == "PASS\nNo findings."


def test_usage_summary_keeps_only_non_sensitive_counts() -> None:
    body = {
        "usage": {
            "total_input_tokens": 100,
            "total_output_tokens": 20,
            "total_thought_tokens": 30,
            "total_cached_tokens": 5,
            "total_tool_use_tokens": 0,
            "total_tokens": 150,
            "prompt": "must not escape",
        }
    }
    summary = usage_summary(body)
    assert summary["total_tokens"] == 150
    assert "prompt" not in summary


def test_current_interactions_api_is_used() -> None:
    assert INTERACTIONS_ENDPOINT.endswith("/v1beta/interactions")


def test_profile_limits_are_bounded() -> None:
    assert PROFILES["quick-review"].max_diff_bytes <= 200_000
    assert PROFILES["architecture-review"].max_diff_bytes <= 350_000
    assert PROFILES["ml-audit"].max_output_tokens <= 16_384
    assert PROFILES["security-review"].max_diff_bytes <= 250_000


def test_workflow_has_owner_only_read_only_repository_gate() -> None:
    app_root = Path(__file__).resolve().parents[1]
    repo_root = app_root.parent
    workflow = (repo_root / ".github/workflows/opip-gemini-review.yml").read_text(
        encoding="utf-8"
    )

    assert "github.actor == github.repository_owner" in workflow
    assert "contents: read" in workflow
    assert "pull-requests: read" in workflow
    assert "issues: write" in workflow
    assert "contents: write" not in workflow
    assert "GEMINI_API_KEY" in workflow
    assert "@gemini" in workflow
    assert "opip-gemini-usage-" in workflow
