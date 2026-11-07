"""O'Pip Chief AI integration reliability.

The production incident under test: an unusable provider response reached
``chief_analyst`` as a bare ``ValueError``, was reported as
``CHIEF_UNAVAILABLE`` and then as ``AI_SERVICE_UNAVAILABLE`` in the decision
engine, with no durable record of *which* provider condition caused it. Every
category - empty body, malformed JSON, wrong structure, truncated output,
provider rejection, rate limiting, transport failure - collapsed into one
indistinguishable signal.

Only the provider transport (the external network boundary) is faked here. The
router's response handling, the advisory response contract, the fail-closed
verdict, the funnel attribution and the decision engine are the real
production code.

These tests prove the classification and fail-closed behaviour. They do not
prove profitability, and they do not grant any trading authority: every
assertion here is advisory-only.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.opip_ai_router as router
from app.opip.decision.engine import CandidateEvidence, OPipDecisionEngine
from app.opip.decision.models import GateName, GateStatus, ReasonCode
from app.opip.decision.observer import OPipScanObserver
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services import chief_analyst
from app.services.recommendation_gate import qualified_alerts


#: Every test in this module proves one criterion of
#: ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1 and cites it in its own docstring.
pytestmark = pytest.mark.acceptance


NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------- fixtures ---


def _snapshot(symbol: str = "SOLUSD") -> MarketSnapshot:
    """Return one realistic long-side market snapshot."""
    return MarketSnapshot(
        symbol=symbol,
        last_price=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=97.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.2,
        technical_score=85,
        trend="bullish",
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=symbol,
        primary_quote_currency="USD",
        ticker_last=100.0,
    )


def _target(qualified: bool) -> SimpleNamespace:
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


def _economic(qualified: bool) -> SimpleNamespace:
    return SimpleNamespace(
        qualified=qualified,
        rejection_reason=None if qualified else "economic",
        recommended_capital=2000,
        target_2_net_profit=100 if qualified else 10,
    )


def _install_quality(monkeypatch, mapping) -> None:
    monkeypatch.setattr(
        chief_analyst,
        "build_entry_exit_plan",
        lambda candidate, risk: SimpleNamespace(
            symbol=candidate.symbol, risk_level=risk
        ),
    )

    def target_eval(plan, candidate):
        return _target(mapping[(candidate.symbol, plan.risk_level)][0])

    def economic_eval(plan, capital, **kwargs):
        return _economic(mapping[(plan.symbol, plan.risk_level)][1])

    monkeypatch.setattr(chief_analyst, "evaluate_target_attainability", target_eval)
    monkeypatch.setattr(chief_analyst, "evaluate_economic_quality", economic_eval)


def _eligible_candidate(monkeypatch, symbol: str = "SOLUSD") -> MarketSnapshot:
    """A candidate that passes the deterministic pre-AI screen at both risks."""
    candidate = _snapshot(symbol)
    _install_quality(
        monkeypatch,
        {
            (symbol, "low"): (True, True),
            (symbol, "medium"): (True, True),
        },
    )
    return candidate



class _Transport:
    """Provider boundary stand-in.

    ``handler`` receives ``(client_kwargs, request_kwargs)`` so a test can make
    one provider fail and another succeed, exactly as the router's fallback
    loop sees them.
    """

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0
        self.requests: list[dict] = []

    def __call__(self, **client_kwargs):
        def create(**request_kwargs):
            self.calls += 1
            self.requests.append(request_kwargs)
            return self._handler(client_kwargs, request_kwargs)

        return SimpleNamespace(responses=SimpleNamespace(create=create))


def _returns(response):
    return lambda client_kwargs, request_kwargs: response


def _raises(error: BaseException):
    def handler(client_kwargs, request_kwargs):
        raise error

    return handler


def _usage(*, input_tokens=400, output_tokens=0, reasoning_tokens=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens=output_tokens,
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        total_tokens=input_tokens + output_tokens,
    )


def _response(text, *, status=None, incomplete=None, usage=None):
    response = SimpleNamespace(output_text=text, usage=usage)
    if status is not None:
        response.status = status
    if incomplete is not None:
        response.incomplete_details = SimpleNamespace(reason=incomplete)
    return response


def _payload(top_candidates, *, action=None) -> str:
    return json.dumps(
        {
            "market_view": "neutral",
            "recommended_action": action or ("alert" if top_candidates else "no_trade"),
            "top_candidates": top_candidates,
            "summary": "test review",
        }
    )


def _alert_item(symbol: str = "SOLUSD", **overrides) -> dict:
    item = {
        "symbol": symbol,
        "direction": "LONG",
        "rank": 1,
        "confidence": 95,
        "risk_level": "low",
        "decision": "alert",
        "reason": "clean continuation",
    }
    item.update(overrides)
    return item


def _install_boundaries(monkeypatch, tmp_path, transport) -> tuple:
    """Isolate only the I/O boundaries around the real Chief review."""
    router_usage = tmp_path / "router_usage.jsonl"
    monkeypatch.setattr(router, "ROUTER_USAGE_FILE", router_usage)
    monkeypatch.setattr(router, "ROUTER_USAGE_LOCK", tmp_path / ".router_usage.lock")
    usage_records: list[dict] = []

    def record_usage(**kwargs):
        usage_records.append(kwargs)
        return {
            "model": kwargs["model"],
            "candidate_count": kwargs["candidate_count"],
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }

    monkeypatch.setattr(chief_analyst, "append_usage_record", record_usage)
    monkeypatch.setattr(
        chief_analyst,
        "capture_chief_review_decisions",
        lambda *a, **k: {
            "captured": 0,
            "qualified_alerts_deferred": 0,
            "not_selected": 0,
            "unmatched": 0,
        },
    )
    monkeypatch.setattr(
        chief_analyst, "capture_prefilter_rejection", lambda *a, **k: True
    )
    monkeypatch.setattr(chief_analyst, "get_cached_review", lambda fingerprint: None)
    monkeypatch.setattr(chief_analyst, "store_cached_review", lambda *a, **k: None)
    monkeypatch.setattr(chief_analyst, "OpenAI", transport)
    traces: list[dict] = []
    monkeypatch.setattr(
        chief_analyst,
        "trace_candidate_event",
        lambda **kwargs: traces.append(kwargs) or {},
    )
    for name in (
        "DIGITALOCEAN_INFERENCE_KEY",
        "DIGITALOCEAN_TOKEN",
        "OPIP_AI_PROVIDER_ORDER",
        "OPIP_AI_DIGITALOCEAN_MODEL",
        "OPENAI_MAX_OUTPUT_TOKENS",
        "OPENAI_REASONING_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)
    return router_usage, usage_records, traces


def _router_rows(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _review(candidate, monkeypatch, tmp_path, transport):
    boundaries = _install_boundaries(monkeypatch, tmp_path, transport)
    review = chief_analyst.review_candidates(
        [candidate], "gpt-test", "test-key", 10_000.0
    )
    return review, boundaries


class _ProviderError(Exception):
    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------- AC-001 success ---


def test_ac_001_valid_response_completes_chief_evaluation(monkeypatch, tmp_path):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-001: a valid review completes."""
    candidate = _eligible_candidate(monkeypatch)
    transport = _Transport(
        _returns(
            _response(
                _payload([_alert_item()]),
                status="completed",
                usage=_usage(input_tokens=500, output_tokens=120, reasoning_tokens=40),
            )
        )
    )

    review, _ = _review(candidate, monkeypatch, tmp_path, transport)
    stage = review["opip_stage_evidence"]

    assert transport.calls == 1
    assert stage["invocation_status"] == chief_analyst.CHIEF_SUCCEEDED
    assert stage["failure_type"] is None
    assert stage["failure_code"] is None
    assert stage["provider"] == "openai"
    assert stage["model"] == "gpt-test"
    assert review["recommended_action"] == "alert"
    assert review["chief_api_skipped"] is False
    assert [item["symbol"] for item in review["top_candidates"]] == ["SOLUSD"]
    assert review["opip_ai_route"]["advisory_only"] is True
    assert review["opip_ai_route"]["funded_trade_authority_changed"] is False


# ------------------------------------------------------------ AC-002 empty ---


def test_ac_002_empty_response_is_classified_and_fails_closed(monkeypatch, tmp_path):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-002: an empty body is attributed."""
    candidate = _eligible_candidate(monkeypatch)
    transport = _Transport(_returns(_response("", status="completed", usage=_usage())))

    router_usage, usage_records, _ = _install_boundaries(
        monkeypatch, tmp_path, transport
    )
    review = chief_analyst.review_candidates(
        [candidate], "gpt-test", "test-key", 10_000.0
    )
    stage = review["opip_stage_evidence"]

    assert review["recommended_action"] == "no_trade"
    assert review["top_candidates"] == []
    assert review["chief_failure_code"] == "CHIEF_UNAVAILABLE"
    assert stage["invocation_status"] == chief_analyst.CHIEF_FAILED
    # The historical family is unchanged; the cause is now explicit.
    assert stage["failure_type"] == "ValueError"
    assert stage["failure_code"] == router.AI_FAILURE_EMPTY_RESPONSE
    assert stage["provider_status"] == "completed"
    assert "empty response" in stage["failure_message"]
    assert usage_records == []

    rows = _router_rows(router_usage)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_type"] == "ValueError"
    assert rows[0]["error_code"] == router.AI_FAILURE_EMPTY_RESPONSE
    assert rows[0]["provider"] == "openai"
    assert rows[0]["advisory_only"] is True
    assert rows[0]["funded_trade_authority_changed"] is False


# ------------------------------------------------------- AC-003 malformed ---


def test_ac_003_malformed_json_never_produces_a_recommendation(monkeypatch, tmp_path):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-003: malformed JSON is attributed."""
    candidate = _eligible_candidate(monkeypatch)
    # A body that starts as an alert and is cut off before the JSON closes.
    transport = _Transport(
        _returns(
            _response(
                '{"recommended_action": "alert", "top_candidates": [',
                status="completed",
            )
        )
    )

    review, _ = _review(candidate, monkeypatch, tmp_path, transport)
    stage = review["opip_stage_evidence"]

    assert stage["failure_code"] == router.AI_FAILURE_MALFORMED_JSON
    assert review["recommended_action"] == "no_trade"
    assert review["top_candidates"] == []
    assert qualified_alerts(review) == []
    assert review["chief_failure_code"] == "CHIEF_UNAVAILABLE"


# -------------------------------------------------- AC-004 wrong structure ---


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ("[]", router.AI_FAILURE_JSON_NOT_OBJECT),
        ('"no_trade"', router.AI_FAILURE_JSON_NOT_OBJECT),
        (
            '{"recommended_action": "buy_everything", "top_candidates": []}',
            router.AI_FAILURE_SCHEMA_INVALID,
        ),
        (
            '{"recommended_action": "no_trade", "top_candidates": null}',
            router.AI_FAILURE_SCHEMA_INVALID,
        ),
        (
            '{"recommended_action": "no_trade", "top_candidates": ["SOLUSD"]}',
            router.AI_FAILURE_SCHEMA_INVALID,
        ),
        ('{"market_view": "neutral"}', router.AI_FAILURE_SCHEMA_INVALID),
    ],
)
def test_ac_004_wrong_json_structure_is_rejected_with_its_own_code(
    monkeypatch, tmp_path, body, expected_code
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-004: the response contract is enforced."""
    candidate = _eligible_candidate(monkeypatch)
    transport = _Transport(_returns(_response(body, status="completed")))

    review, _ = _review(candidate, monkeypatch, tmp_path, transport)
    stage = review["opip_stage_evidence"]

    assert stage["failure_code"] == expected_code
    assert review["recommended_action"] == "no_trade"
    assert review["top_candidates"] == []
    assert qualified_alerts(review) == []


# -------------------------------------------------- AC-005 token exhaustion ---


def test_ac_005_reasoning_exhaustion_is_not_mistaken_for_an_empty_answer(
    monkeypatch, tmp_path
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-005: truncation is attributed."""
    candidate = _eligible_candidate(monkeypatch)
    # A reasoning model that spent the whole output budget thinking.
    transport = _Transport(
        _returns(
            _response(
                "",
                status="incomplete",
                incomplete="max_output_tokens",
                usage=_usage(
                    input_tokens=800, output_tokens=1200, reasoning_tokens=1200
                ),
            )
        )
    )

    router_usage, _, _ = _install_boundaries(monkeypatch, tmp_path, transport)
    review = chief_analyst.review_candidates(
        [candidate], "gpt-test", "test-key", 10_000.0
    )
    stage = review["opip_stage_evidence"]

    assert stage["failure_code"] == router.AI_FAILURE_TRUNCATED_OUTPUT
    assert stage["provider_status"] == "incomplete"
    assert stage["incomplete_reason"] == "max_output_tokens"
    assert review["recommended_action"] == "no_trade"

    row = _router_rows(router_usage)[-1]
    # The paid call is still visible: this is what separates "the budget was
    # consumed thinking" from "the provider returned nothing".
    assert row["error_code"] == router.AI_FAILURE_TRUNCATED_OUTPUT
    assert row["provider_status"] == "incomplete"
    assert row["incomplete_reason"] == "max_output_tokens"
    assert row["input_tokens"] == 800
    assert row["output_tokens"] == 1200
    assert row["reasoning_tokens"] == 1200


def test_ac_005_partial_body_from_a_truncated_response_is_never_accepted(
    monkeypatch, tmp_path
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-005: partial output is never a review."""
    candidate = _eligible_candidate(monkeypatch)
    # Syntactically valid but provider-declared incomplete: the review must not
    # be treated as an answer.
    transport = _Transport(
        _returns(
            _response(
                _payload([_alert_item()]),
                status="incomplete",
                incomplete="max_output_tokens",
            )
        )
    )

    review, _ = _review(candidate, monkeypatch, tmp_path, transport)

    assert (
        review["opip_stage_evidence"]["failure_code"]
        == router.AI_FAILURE_TRUNCATED_OUTPUT
    )
    assert review["recommended_action"] == "no_trade"


# ----------------------------------------------------- AC-006 retry policy ---


def test_ac_006_retryable_failure_is_classified_and_not_retried(monkeypatch, tmp_path):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-006: retryable, not retried."""
    candidate = _eligible_candidate(monkeypatch)
    transport = _Transport(
        _raises(_ProviderError("rate limit exceeded", status_code=429))
    )

    router_usage, _, _ = _install_boundaries(monkeypatch, tmp_path, transport)
    review = chief_analyst.review_candidates(
        [candidate], "gpt-test", "test-key", 10_000.0
    )
    stage = review["opip_stage_evidence"]
    attempts = stage["router_attempts"]

    # Exactly one paid call: retries need the classification evidence this
    # release produces before they can be justified.
    assert transport.calls == 1
    assert stage["failure_code"] == router.AI_FAILURE_PROVIDER_RATE_LIMIT
    assert len(attempts) == 1
    assert attempts[0]["provider"] == "openai"
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["error_code"] == router.AI_FAILURE_PROVIDER_RATE_LIMIT
    assert attempts[0]["retryable"] is True
    assert (
        _router_rows(router_usage)[-1]["error_code"]
        == router.AI_FAILURE_PROVIDER_RATE_LIMIT
    )


def test_ac_007_non_retryable_failure_uses_the_configured_fallback(
    monkeypatch, tmp_path
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-007: the fallback is recorded."""
    candidate = _eligible_candidate(monkeypatch)

    def handler(client_kwargs, request_kwargs):
        if client_kwargs.get("base_url"):
            return _response(
                _payload([_alert_item()]),
                status="completed",
                usage=_usage(input_tokens=100, output_tokens=50),
            )
        raise _ProviderError("unsupported parameter: reasoning", status_code=400)

    transport = _Transport(handler)
    router_usage, _, _ = _install_boundaries(monkeypatch, tmp_path, transport)
    monkeypatch.setenv("DIGITALOCEAN_INFERENCE_KEY", "do-test-key")
    monkeypatch.setenv("OPIP_AI_DIGITALOCEAN_MODEL", "kimi-test-model")
    review = chief_analyst.review_candidates(
        [candidate], "gpt-test", "test-key", 10_000.0
    )
    stage = review["opip_stage_evidence"]

    assert transport.calls == 2
    assert stage["invocation_status"] == chief_analyst.CHIEF_SUCCEEDED
    assert stage["provider"] == "digitalocean"
    assert [item["status"] for item in stage["router_attempts"]] == [
        "failed",
        "succeeded",
    ]
    assert (
        stage["router_attempts"][0]["error_code"]
        == router.AI_FAILURE_PROVIDER_REJECTED
    )
    assert stage["router_attempts"][0]["retryable"] is False
    assert review["recommended_action"] == "alert"

    rows = _router_rows(router_usage)
    assert [row["provider"] for row in rows] == ["openai", "digitalocean"]
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_message"].startswith("_ProviderError:")
    assert rows[1]["status"] == "succeeded"


def test_ac_008_failed_chain_is_recorded_when_every_provider_fails(
    monkeypatch, tmp_path
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-008: the failed chain is evidence."""
    candidate = _eligible_candidate(monkeypatch)

    def handler(client_kwargs, request_kwargs):
        if client_kwargs.get("base_url"):
            raise _ProviderError("digitalocean timeout", status_code=504)
        raise _ProviderError("openai timeout", status_code=504)

    transport = _Transport(handler)
    router_usage, _, _ = _install_boundaries(monkeypatch, tmp_path, transport)
    monkeypatch.setenv("DIGITALOCEAN_INFERENCE_KEY", "do-test-key")
    monkeypatch.setenv("OPIP_AI_DIGITALOCEAN_MODEL", "kimi-test-model")
    review = chief_analyst.review_candidates(
        [candidate], "gpt-test", "test-key", 10_000.0
    )
    stage = review["opip_stage_evidence"]

    assert review["recommended_action"] == "no_trade"
    assert review["chief_failure_code"] == "CHIEF_UNAVAILABLE"
    assert stage["failure_code"] == router.AI_FAILURE_PROVIDER_UNAVAILABLE
    assert [item["provider"] for item in stage["router_attempts"]] == [
        "openai",
        "digitalocean",
    ]
    assert all(
        item["error_code"] == router.AI_FAILURE_PROVIDER_UNAVAILABLE
        for item in stage["router_attempts"]
    )
    rows = _router_rows(router_usage)
    assert len(rows) == 2
    assert rows[0]["provider_status"] == 504
    assert rows[1]["provider_status"] == 504



# -------------------------------------------------- AC-009 budget/cost view ---


def test_ac_009_failure_diagnostics_never_carry_credentials(monkeypatch, tmp_path):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-009: diagnostics are redacted."""
    class _LeakyError(Exception):
        status_code = 401

    candidate = _eligible_candidate(monkeypatch)
    transport = _Transport(
        _raises(
            _LeakyError(
                "Incorrect API key provided: sk-abcdef1234567890 for prompt [internal]"
            )
        )
    )

    router_usage, _, traces = _install_boundaries(monkeypatch, tmp_path, transport)
    review = chief_analyst.review_candidates(
        [candidate], "gpt-test", "test-key", 10_000.0
    )
    stage = review["opip_stage_evidence"]

    assert stage["failure_code"] == router.AI_FAILURE_PROVIDER_AUTH
    text = router_usage.read_text(encoding="utf-8")
    assert "sk-abcdef1234567890" not in text
    assert "test-key" not in text
    assert "sk-abcdef1234567890" not in (stage["failure_message"] or "")
    # The candidate trace is durable evidence too, so it gets the same redacted,
    # bounded message.
    assert traces and "sk-abcdef1234567890" not in traces[0]["details"]["detail"]
    # The classification still reports the cause.
    assert router.AI_FAILURE_PROVIDER_AUTH in text


def test_ac_009_usage_record_is_written_only_for_billed_attempts(
    monkeypatch, tmp_path
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-009: billed usage stays visible."""
    candidate = _eligible_candidate(monkeypatch)
    transport = _Transport(
        _returns(
            _response(
                _payload([_alert_item()]),
                status="completed",
                usage=_usage(input_tokens=250, output_tokens=90, reasoning_tokens=30),
            )
        )
    )

    router_usage, usage_records, _ = _install_boundaries(monkeypatch, tmp_path, transport)
    chief_analyst.review_candidates([candidate], "gpt-test", "test-key", 10_000.0)

    rows = _router_rows(router_usage)
    assert len(rows) == 1
    assert rows[0]["status"] == "succeeded"
    assert rows[0]["input_tokens"] == 250
    assert rows[0]["output_tokens"] == 90
    assert rows[0]["reasoning_tokens"] == 30
    # The OpenAI daily-budget ledger is unchanged: one record per billed call.
    assert len(usage_records) == 1
    assert usage_records[0]["candidate_count"] == 1


# ----------------------------------------------------- AC-010 advisory flow ---


def test_ac_010_valid_response_proceeds_to_downstream_qualification(
    monkeypatch, tmp_path
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-010: a valid alert proceeds."""
    candidate = _eligible_candidate(monkeypatch)
    transport = _Transport(
        _returns(
            _response(
                _payload(
                    [
                        _alert_item(),
                        _alert_item(symbol="ADAUSD", rank=2, decision="watch"),
                    ]
                ),
                status="completed",
            )
        )
    )

    review, _ = _review(candidate, monkeypatch, tmp_path, transport)

    alerts = qualified_alerts(review)
    assert [item["symbol"] for item in alerts] == ["SOLUSD"]
    assert alerts[0]["direction"] == "LONG"
    # The independent recommendation gate still refuses WATCH whatever the model
    # returned.
    assert all(item["decision"] == "alert" for item in alerts)
    assert review["opip_ai_route"]["funded_trade_authority_changed"] is False


def test_ac_010_malformed_confidence_never_reaches_qualification(
    monkeypatch, tmp_path
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-010: the gate stays independent."""
    candidate = _eligible_candidate(monkeypatch)
    transport = _Transport(
        _returns(_response(_payload([_alert_item(confidence="high")]), status="completed"))
    )

    review, _ = _review(candidate, monkeypatch, tmp_path, transport)

    assert review["top_candidates"][0]["symbol"] == "SOLUSD"
    assert qualified_alerts(review) == []



# --------------------------------------------- AC-011 incident regression ---


def test_ac_011_production_incident_chain_is_reproduced_and_attributed(
    monkeypatch, tmp_path
):
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-011: the incident chain is attributed.

    Truncated provider output -> CHIEF_UNAVAILABLE -> AI_SERVICE_UNAVAILABLE.
    The verdict is unchanged and fail-closed; what changes is that the cause is
    carried through the whole chain instead of being flattened to ``ValueError``.
    """
    candidate = _eligible_candidate(monkeypatch)
    candidate.execution_validation = ExecutionValidation(
        status="VALID",
        book_coverage_status="COMPLETE",
        warnings=[],
        validation_notional_usd=2_000.0,
        spread_bps=6.0,
        estimated_visible_round_trip_market_drag_pct=0.25,
        recent_trade_status="FRESH",
    )
    transport = _Transport(
        _returns(
            _response(
                "",
                status="incomplete",
                incomplete="max_output_tokens",
                usage=_usage(
                    input_tokens=900, output_tokens=1200, reasoning_tokens=1200
                ),
            )
        )
    )

    review, _ = _review(candidate, monkeypatch, tmp_path, transport)
    assert review["chief_failure_code"] == "CHIEF_UNAVAILABLE"

    observer = OPipScanObserver(
        snapshots=[candidate],
        decision_at=NOW,
        account_equity=10_000.0,
        telemetry_enabled=False,
    )
    observer.register_candidates([candidate])
    observer.record_margin([candidate])
    observer.record_execution([candidate])
    observer.record_ai_stage(review)
    summary = observer.finalize(scan_context={"analyzed": 1})

    assert summary["ai_stage"]["unavailable"] is True
    assert summary["ai_stage"]["failure_type"] == "ValueError"
    assert summary["ai_stage"]["failure_code"] == router.AI_FAILURE_TRUNCATED_OUTPUT
    assert summary["shadow_comparison"]["opip_engine_authoritative"] is False

    engine = OPipDecisionEngine(
        account_equity=10_000.0,
        decision_at=NOW,
        ai_stage=observer.funnel.ai_stage,
    )
    decision = engine.evaluate(
        CandidateEvidence(snapshot=candidate, episode_id="EP:INCIDENT")
    )
    gate = next(
        result
        for result in decision.gate_results
        if result.gate is GateName.AI_INVOCATION
    )

    assert gate.status is GateStatus.FAIL
    assert gate.reason_code is ReasonCode.AI_SERVICE_UNAVAILABLE
    assert gate.metadata["failure_code"] == router.AI_FAILURE_TRUNCATED_OUTPUT
    assert gate.metadata["failure_type"] == "ValueError"
    assert decision.counterfactual_eligible is False


# ------------------------------------------------ AC-012 governance ---


def test_ac_012_scope_contract_declares_the_active_increment():
    """ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1/AC-012: the scope is declared."""
    app_root = Path(__file__).resolve().parents[1]
    pointer = (app_root / "docs" / "atdd" / "ACTIVE_INCREMENT").read_text(
        encoding="utf-8"
    ).strip()
    assert pointer == "ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1"
    contract_path = (
        app_root / "docs" / "atdd" / "scope-contracts" / f"{pointer}.md"
    )
    assert contract_path.is_file()
    contract = contract_path.read_text(encoding="utf-8")
    assert contract.startswith(f"INCREMENT:\n{pointer}")
    for section in (
        "OWNER-APPROVED INTENT:",
        "ARCHITECTURE REFERENCES:",
        "APPROVED ACCEPTANCE CRITERIA:",
        "EXPLICITLY OUT OF SCOPE:",
        "FROZEN BOUNDARIES:",
        "ACCEPTANCE TEST TRACEABILITY:",
        "IMPLEMENTATION MAP:",
        "DEFERRED DISCOVERIES:",
        "UNAPPROVED SCOPE CHANGES:",
    ):
        assert section in contract
    assert contract.rstrip().endswith("NONE")
    # The increment must not claim a production finding it could not evidence.
    assert "No runtime production evidence was retrievable" in contract
