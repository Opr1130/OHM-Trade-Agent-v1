from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.opip_ai_router as router
from app.services.chief_runtime_guard import build_chief_fingerprint


def _payload(*, count: int, score: int = 70, movement: str | None = None) -> dict:
    rows = []
    for index in range(count):
        row = {
            "symbol": f"COIN{index}USD",
            "direction": "LONG",
            "technical_score": score,
        }
        if movement:
            row["price_movement_intelligence"] = {"stage": movement}
        rows.append(row)
    return {"candidate_count": count, "candidates": rows}


def _clear_router_env(monkeypatch) -> None:
    names = [
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
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_small_finalist_set_escalates_reasoning_without_requiring_new_model(
    monkeypatch,
):
    _clear_router_env(monkeypatch)

    plan = router.plan_chief_route(
        _payload(count=2, score=70),
        default_model="gpt-5.6",
        openai_api_key="test-openai-key",
    )

    assert plan.route_tier == "premium"
    assert plan.route_reason == "finalist_count<=3"
    assert len(plan.targets) == 1
    target = plan.targets[0]
    assert target.provider == "openai"
    assert target.model == "gpt-5.6"
    assert target.reasoning_effort == "high"
    assert target.send_reasoning is True


def test_standard_route_preserves_medium_reasoning(monkeypatch):
    _clear_router_env(monkeypatch)

    plan = router.plan_chief_route(
        _payload(count=5, score=70),
        default_model="gpt-5.6",
        openai_api_key="test-openai-key",
    )

    assert plan.route_tier == "standard"
    assert plan.route_reason == "standard_finalist_review"
    assert plan.targets[0].provider == "openai"
    assert plan.targets[0].reasoning_effort == "medium"


def test_high_quality_or_confirmed_movement_escalates_large_finalist_set(
    monkeypatch,
):
    _clear_router_env(monkeypatch)

    score_plan = router.plan_chief_route(
        _payload(count=5, score=90),
        default_model="gpt-5.6",
        openai_api_key="test-openai-key",
    )
    assert score_plan.route_tier == "premium"
    assert score_plan.route_reason == "technical_score>=82"

    movement_plan = router.plan_chief_route(
        _payload(count=5, score=70, movement="CONFIRMED"),
        default_model="gpt-5.6",
        openai_api_key="test-openai-key",
    )
    assert movement_plan.route_tier == "premium"
    assert movement_plan.route_reason == "price_movement=CONFIRMED"


def test_digitalocean_can_be_primary_without_code_change(monkeypatch):
    _clear_router_env(monkeypatch)
    monkeypatch.setenv("OPIP_AI_PROVIDER_ORDER", "digitalocean,openai")
    monkeypatch.setenv("DIGITALOCEAN_INFERENCE_KEY", "do-test-key")
    monkeypatch.setenv("OPIP_AI_DIGITALOCEAN_MODEL", "kimi-test-model")

    plan = router.plan_chief_route(
        _payload(count=5, score=70),
        default_model="gpt-5.6",
        openai_api_key="openai-test-key",
    )

    assert [target.provider for target in plan.targets] == [
        "digitalocean",
        "openai",
    ]
    do_target = plan.targets[0]
    assert do_target.model == "kimi-test-model"
    assert do_target.base_url == "https://inference.do-ai.run/v1"
    assert do_target.send_reasoning is False


def test_openai_failure_falls_back_to_digitalocean(monkeypatch, tmp_path):
    _clear_router_env(monkeypatch)
    monkeypatch.setenv("DIGITALOCEAN_INFERENCE_KEY", "do-test-key")
    monkeypatch.setenv("OPIP_AI_DIGITALOCEAN_MODEL", "deepseek-test-model")
    monkeypatch.setattr(router, "ROUTER_USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(router, "ROUTER_USAGE_LOCK", tmp_path / ".usage.lock")

    plan = router.plan_chief_route(
        _payload(count=5, score=70),
        default_model="gpt-5.6",
        openai_api_key="openai-test-key",
    )
    calls = []

    def invoke(target, **_kwargs):
        calls.append(target.provider)
        if target.provider == "openai":
            raise RuntimeError("simulated provider outage")
        return (
            '{"market_view":"","recommended_action":"no_trade",'
            '"top_candidates":[],"summary":"ok"}',
            SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
        )

    monkeypatch.setattr(router, "_invoke_target", invoke)

    result = router.invoke_chief_review(
        plan,
        system_prompt="system",
        request_payload=_payload(count=5, score=70),
        max_output_tokens=800,
    )

    assert calls == ["openai", "digitalocean"]
    assert result.provider == "digitalocean"
    assert result.model == "deepseek-test-model"
    assert [attempt["status"] for attempt in result.attempts] == [
        "failed",
        "succeeded",
    ]
    evidence = result.route_evidence()
    assert evidence["advisory_only"] is True
    assert evidence["funded_trade_authority_changed"] is False


def test_openai_budget_can_remove_only_openai_from_route(monkeypatch):
    _clear_router_env(monkeypatch)
    monkeypatch.setenv("DIGITALOCEAN_INFERENCE_KEY", "do-test-key")
    monkeypatch.setenv("OPIP_AI_DIGITALOCEAN_MODEL", "kimi-test-model")

    plan = router.plan_chief_route(
        _payload(count=5, score=70),
        default_model="gpt-5.6",
        openai_api_key="openai-test-key",
    ).without_provider("openai")

    assert [target.provider for target in plan.targets] == ["digitalocean"]


def test_single_provider_failure_preserves_original_error_type(
    monkeypatch, tmp_path
):
    _clear_router_env(monkeypatch)
    monkeypatch.setattr(router, "ROUTER_USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(router, "ROUTER_USAGE_LOCK", tmp_path / ".usage.lock")

    plan = router.plan_chief_route(
        _payload(count=5, score=70),
        default_model="gpt-5.6",
        openai_api_key="openai-test-key",
    )

    def fail(_target, **_kwargs):
        raise RuntimeError("simulated connection refused")

    monkeypatch.setattr(router, "_invoke_target", fail)

    with pytest.raises(RuntimeError, match="simulated connection refused"):
        router.invoke_chief_review(
            plan,
            system_prompt="system",
            request_payload=_payload(count=5, score=70),
            max_output_tokens=800,
        )


def test_router_fails_closed_when_no_provider_is_configured(monkeypatch):
    _clear_router_env(monkeypatch)

    plan = router.plan_chief_route(
        _payload(count=5, score=70),
        default_model="gpt-5.6",
        openai_api_key=None,
    )
    assert plan.targets == ()

    with pytest.raises(router.AIProviderUnavailable):
        router.invoke_chief_review(
            plan,
            system_prompt="system",
            request_payload=_payload(count=5, score=70),
            max_output_tokens=800,
        )


def test_chief_cache_fingerprint_is_partitioned_by_ai_route():
    payload = _payload(count=2, score=90)
    openai = build_chief_fingerprint(
        payload,
        route_key="premium|openai:gpt-5.6:high",
    )
    digitalocean = build_chief_fingerprint(
        payload,
        route_key="premium|digitalocean:kimi-test:none",
    )

    assert openai != digitalocean


def test_router_has_no_exchange_order_position_or_telegram_imports():
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "opip_ai_router.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = ("kraken", "order", "position", "telegram", "execution")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.lower() for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [str(node.module or "").lower()]
        else:
            continue
        for name in names:
            assert not any(fragment in name for fragment in forbidden), name
