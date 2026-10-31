"""ATDD: Stage-1 Chief Analyst contract on the documented ``gpt-6.1-sol``.

These are compatibility assertions over the *existing* router and client
request shape. They add no behaviour: they prove the model id O'Pip is
configured with is reachable through the documented Responses API parameters,
that reasoning effort stays inside the documented set, that only OpenAI can be
used when the operator disables DigitalOcean, and that malformed or empty
answers still fail closed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.services.opip_ai_router as router


pytestmark = pytest.mark.acceptance


#: Documented reasoning levels for gpt-6.1-sol (OpenAI model catalogue).
DOCUMENTED_REASONING_LEVELS = {"low", "medium", "high", "xhigh", "max"}
#: O'Pip deliberately restricts itself to the conservative documented subset.
OPIP_REASONING_LEVELS = {"low", "medium", "high"}

ROUTER_ENV_NAMES = (
    "OPIP_AI_ROUTER_ENABLED",
    "OPIP_AI_ADAPTIVE_ROUTER_ENABLED",
    "OPIP_AI_PROVIDER_ORDER",
    "OPIP_AI_PREMIUM_MAX_CANDIDATES",
    "OPIP_AI_PREMIUM_TECHNICAL_SCORE",
    "OPIP_AI_PREMIUM_ON_CONFIRMED_MOVEMENT",
    "OPIP_AI_OPENAI_MODEL",
    "OPIP_AI_OPENAI_PREMIUM_MODEL",
    "OPIP_AI_OPENAI_PREMIUM_REASONING_EFFORT",
    "OPENAI_REASONING_EFFORT",
    "DIGITALOCEAN_INFERENCE_KEY",
    "DIGITALOCEAN_TOKEN",
    "OPIP_AI_DIGITALOCEAN_MODEL",
    "OPIP_AI_DIGITALOCEAN_PREMIUM_MODEL",
    "OPIP_AI_DIGITALOCEAN_BASE_URL",
    "OPIP_AI_DIGITALOCEAN_SEND_REASONING",
    "OPIP_AI_DIGITALOCEAN_REASONING_EFFORT",
)


@pytest.fixture(autouse=True)
def isolated_router_env(monkeypatch, tmp_path):
    for name in ROUTER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(router, "ROUTER_USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(router, "ROUTER_USAGE_LOCK", tmp_path / ".usage.lock")
    return tmp_path


def _payload(count: int = 2, score: int = 88) -> dict:
    return {
        "candidate_count": count,
        "candidates": [
            {"symbol": f"COIN{index}USD", "direction": "LONG", "technical_score": score}
            for index in range(count)
        ],
    }


def _valid_review_json() -> str:
    return json.dumps(
        {
            "market_view": "",
            "recommended_action": "no_trade",
            "top_candidates": [],
            "summary": "ok",
        }
    )


class _CaptureClient:
    def __init__(self, output_text: str, calls: list):
        self._output_text = output_text
        self._calls = calls
        self.responses = self

    def create(self, **kwargs):
        self._calls.append(kwargs)
        return SimpleNamespace(output_text=self._output_text, usage=None)


def test_gpt_6_1_sol_is_routed_with_documented_reasoning_effort():
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-001: the documented model is preserved."""
    plan = router.plan_chief_route(
        _payload(),
        default_model="gpt-6.1-sol",
        openai_api_key="openai-test-key",
    )

    assert plan.route_tier == "premium"
    assert [target.provider for target in plan.targets] == ["openai"]
    target = plan.targets[0]
    assert target.model == "gpt-6.1-sol"
    assert target.base_url is None
    assert target.send_reasoning is True
    assert target.reasoning_effort in DOCUMENTED_REASONING_LEVELS
    assert target.reasoning_effort in OPIP_REASONING_LEVELS


def test_responses_request_uses_documented_parameters():
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-001: Responses API parameters are bounded."""
    calls: list = []
    plan = router.plan_chief_route(
        _payload(),
        default_model="gpt-6.1-sol",
        openai_api_key="openai-test-key",
    )

    result = router.invoke_chief_review(
        plan,
        system_prompt="Return json only",
        request_payload=_payload(),
        max_output_tokens=1200,
        client_factory=lambda **kwargs: _CaptureClient(_valid_review_json(), calls),
    )

    assert result.provider == "openai"
    assert result.model == "gpt-6.1-sol"
    assert len(calls) == 1
    request = calls[0]
    assert request["model"] == "gpt-6.1-sol"
    assert request["max_output_tokens"] == 1200
    assert request["max_output_tokens"] <= 4096  # conservative O'Pip cap
    assert [item["role"] for item in request["input"]] == ["system", "user"]
    assert request["reasoning"]["effort"] in DOCUMENTED_REASONING_LEVELS
    # The user turn is the canonical JSON request payload.
    assert json.loads(request["input"][1]["content"])["candidate_count"] == 2


@pytest.mark.parametrize(
    "output_text",
    [
        "",  # empty
        "{truncated",  # malformed
        "[1, 2, 3]",  # JSON but not an object
        '"just a string"',
    ],
)
def test_malformed_or_empty_chief_answer_fails_closed(output_text):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-001: malformed answers fail closed."""
    plan = router.plan_chief_route(
        _payload(),
        default_model="gpt-6.1-sol",
        openai_api_key="openai-test-key",
    )

    with pytest.raises(ValueError):
        router.invoke_chief_review(
            plan,
            system_prompt="Return json only",
            request_payload=_payload(),
            max_output_tokens=1200,
            client_factory=lambda **kwargs: _CaptureClient(output_text, []),
        )


def test_openai_only_posture_holds_when_no_digitalocean_credential_exists(
    monkeypatch,
):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-002: OpenAI-only, no DigitalOcean target."""
    plan = router.plan_chief_route(
        _payload(),
        default_model="gpt-6.1-sol",
        openai_api_key="openai-test-key",
    )

    assert [target.provider for target in plan.targets] == ["openai"]
    assert plan.without_provider("openai").targets == ()


def test_provider_order_is_a_priority_list_not_an_allowlist(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-002: the provider-order gap is recorded."""
    monkeypatch.setenv("OPIP_AI_PROVIDER_ORDER", "openai")
    monkeypatch.setenv("DIGITALOCEAN_INFERENCE_KEY", "do-test-key")
    monkeypatch.setenv("OPIP_AI_DIGITALOCEAN_MODEL", "some-model")

    plan = router.plan_chief_route(
        _payload(),
        default_model="gpt-6.1-sol",
        openai_api_key="openai-test-key",
    )

    assert [target.provider for target in plan.targets] == [
        "openai",
        "digitalocean",
    ]


def test_openai_budget_block_fails_closed_without_a_digitalocean_credential():
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-002: a blocked OpenAI budget fails closed."""
    plan = router.plan_chief_route(
        _payload(),
        default_model="gpt-6.1-sol",
        openai_api_key="openai-test-key",
    ).without_provider("openai", reason_suffix="openai_budget_blocked")

    assert plan.targets == ()
    with pytest.raises(router.AIProviderUnavailable):
        router.invoke_chief_review(
            plan,
            system_prompt="Return json only",
            request_payload=_payload(),
            max_output_tokens=1200,
        )

