import json
from types import SimpleNamespace

import pytest

from app.scanner.models import MarketSnapshot
from app.services import chief_analyst, openai_usage_telemetry


def snapshot(symbol="SOLUSD") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        last_price=100,
        ema20=99,
        ema50=98,
        ema200=97,
        rsi=55,
        macd_line=1,
        macd_signal=.5,
        macd_histogram=.5,
        atr=2,
        atr_pct=2,
        volume_ratio=1.2,
        technical_score=85,
        trend="bullish",
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=symbol,
        primary_quote_currency="USD",
        ticker_last=100,
    )


def target(qualified: bool):
    return SimpleNamespace(
        qualified=qualified,
        attainability_score=80 if qualified else 30,
        warnings=[],
        rejection_reasons=[] if qualified else ["target"],
        target_2_atr_multiple=5.25,
        clearance_to_24h_resistance_pct=1.0,
        clearance_to_72h_resistance_pct=1.0,
        momentum_context="test",
    )


def economic(qualified: bool):
    return SimpleNamespace(
        qualified=qualified,
        rejection_reason=None if qualified else "economic",
        recommended_capital=2000,
        target_2_net_profit=100 if qualified else 10,
    )


def install_quality(monkeypatch, mapping):
    monkeypatch.setattr(
        chief_analyst,
        "build_entry_exit_plan",
        lambda candidate, risk: SimpleNamespace(symbol=candidate.symbol, risk_level=risk),
    )

    def target_eval(plan, candidate):
        return target(mapping[(candidate.symbol, plan.risk_level)][0])

    def economic_eval(plan, capital):
        return economic(mapping[(plan.symbol, plan.risk_level)][1])

    monkeypatch.setattr(chief_analyst, "evaluate_target_attainability", target_eval)
    monkeypatch.setattr(chief_analyst, "evaluate_economic_quality", economic_eval)


class Responses:
    def __init__(self):
        self.calls = 0
        self.kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        usage = SimpleNamespace(
            input_tokens=100,
            input_tokens_details=SimpleNamespace(cached_tokens=20),
            output_tokens=30,
            output_tokens_details=SimpleNamespace(reasoning_tokens=10),
            total_tokens=130,
        )
        return SimpleNamespace(
            output_text=json.dumps({
                "market_view": "",
                "recommended_action": "no_trade",
                "top_candidates": [],
                "summary": "",
            }),
            usage=usage,
        )


def install_client(monkeypatch):
    responses = Responses()
    monkeypatch.setattr(
        chief_analyst,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )
    monkeypatch.setattr(
        chief_analyst,
        "append_usage_record",
        lambda **kwargs: {
            "model": kwargs["model"],
            "candidate_count": kwargs["candidate_count"],
            "input_tokens": 100,
            "cached_input_tokens": 20,
            "output_tokens": 30,
            "reasoning_tokens": 10,
            "total_tokens": 130,
        },
    )
    return responses


def test_zero_viable_finalists_make_zero_openai_calls(monkeypatch):
    c = snapshot()
    install_quality(monkeypatch, {
        (c.symbol, "low"): (False, True),
        (c.symbol, "medium"): (True, False),
    })
    responses = install_client(monkeypatch)
    result = chief_analyst.review_candidates([c], "model", "key", 2000)
    assert responses.calls == 0
    assert result["recommended_action"] == "no_trade"
    assert result["chief_api_skipped"] is True


def test_candidate_passing_only_low_risk_remains_eligible(monkeypatch):
    c = snapshot()
    install_quality(monkeypatch, {
        (c.symbol, "low"): (True, True),
        (c.symbol, "medium"): (False, True),
    })
    responses = install_client(monkeypatch)
    result = chief_analyst.review_candidates([c], "model", "key", 2000)
    assert responses.calls == 1
    assert result["chief_eligible_candidates"] == 1


def test_candidate_passing_only_medium_risk_remains_eligible(monkeypatch):
    c = snapshot()
    install_quality(monkeypatch, {
        (c.symbol, "low"): (False, True),
        (c.symbol, "medium"): (True, True),
    })
    responses = install_client(monkeypatch)
    chief_analyst.review_candidates([c], "model", "key", 2000)
    assert responses.calls == 1


def test_only_viable_candidates_are_sent(monkeypatch):
    viable = snapshot("SOLUSD")
    rejected = snapshot("DOGEUSD")
    install_quality(monkeypatch, {
        (viable.symbol, "low"): (True, True),
        (viable.symbol, "medium"): (True, True),
        (rejected.symbol, "low"): (False, True),
        (rejected.symbol, "medium"): (False, True),
    })
    responses = install_client(monkeypatch)
    chief_analyst.review_candidates([viable, rejected], "model", "key", 2000)
    payload = json.loads(responses.kwargs["input"][1]["content"])
    assert responses.calls == 1
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["symbol"] == "SOLUSD"


def test_reasoning_effort_and_output_cap_are_configurable(monkeypatch):
    c = snapshot()
    install_quality(monkeypatch, {
        (c.symbol, "low"): (True, True),
        (c.symbol, "medium"): (True, True),
    })
    responses = install_client(monkeypatch)
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "800")
    chief_analyst.review_candidates([c], "model", "key", 2000)
    assert responses.kwargs["reasoning"] == {"effort": "low"}
    assert responses.kwargs["max_output_tokens"] == 800


def test_invalid_reasoning_effort_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "extreme")
    with pytest.raises(ValueError):
        chief_analyst._reasoning_effort()


def test_invalid_output_cap_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "100")
    with pytest.raises(ValueError):
        chief_analyst._max_output_tokens()


def test_usage_telemetry_contains_only_usage_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(
        openai_usage_telemetry,
        "USAGE_FILE",
        tmp_path / "openai_usage.jsonl",
    )
    monkeypatch.setattr(
        openai_usage_telemetry,
        "LOCK_FILE",
        tmp_path / ".openai_usage.lock",
    )
    usage = SimpleNamespace(
        input_tokens=100,
        input_tokens_details=SimpleNamespace(cached_tokens=25),
        output_tokens=20,
        output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        total_tokens=120,
    )
    record = openai_usage_telemetry.append_usage_record(
        model="model",
        reasoning_effort="medium",
        candidate_count=2,
        usage=usage,
    )
    assert record["input_tokens"] == 100
    assert record["cached_input_tokens"] == 25
    assert record["reasoning_tokens"] == 5
    text = (tmp_path / "openai_usage.jsonl").read_text()
    assert "api_key" not in text.lower()
    assert "prompt" not in text.lower()
    assert "secret" not in text.lower()


def test_no_account_equity_preserves_existing_single_call_behavior(monkeypatch):
    c = snapshot()
    responses = install_client(monkeypatch)
    chief_analyst.review_candidates([c], "model", "key")
    assert responses.calls == 1


def test_market_context_does_not_drive_prefilter(monkeypatch):
    c = snapshot()
    install_quality(monkeypatch, {
        (c.symbol, "low"): (True, True),
        (c.symbol, "medium"): (True, True),
    })
    responses = install_client(monkeypatch)
    risk_off = SimpleNamespace(regime="RISK_OFF")
    chief_analyst.review_candidates(
        [c], "model", "key", 2000, market_regime_context=risk_off
    )
    assert responses.calls == 1
