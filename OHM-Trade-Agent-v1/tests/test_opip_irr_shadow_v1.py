"""ATDD: Stage-2 Independent Research & Risk Review (IRR), shadow-only.

Covers routing and the positive path, missing credentials, malformed provider
answers, API failures, budget exhaustion, idempotence, immutable evidence and
zero authority over the Chief conclusion.
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import app.services.independent_research_review as irr
from app.services import chief_analyst
from app.scanner.models import MarketSnapshot


pytestmark = pytest.mark.acceptance


IRR_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "independent_research_review.py"
)

_ENV_NAMES = (
    "OPIP_IRR_MODE",
    "OPIP_IRR_MODEL",
    "OPIP_IRR_BASE_URL",
    "OPIP_IRR_TIMEOUT_SECONDS",
    "OPIP_IRR_MAX_OUTPUT_TOKENS",
    "OPIP_IRR_REASONING_EFFORT",
    "OPIP_IRR_MAX_CANDIDATES",
    "OPIP_IRR_DAILY_CALL_BUDGET",
    "OPIP_IRR_DAILY_TOKEN_BUDGET",
    "OPIP_IRR_DAILY_COST_BUDGET_USD",
    "OPIP_IRR_MAX_CALL_COST_USD",
    "DEEPSEEK_API_KEY",
)


@pytest.fixture(autouse=True)
def isolated_irr(tmp_path, monkeypatch):
    """Keep journals, idempotence state and configuration inside the test."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(irr, "EVIDENCE_FILE", tmp_path / "irr_evidence.jsonl")
    monkeypatch.setattr(irr, "EVIDENCE_LOCK", tmp_path / ".irr_evidence.lock")
    monkeypatch.setattr(irr, "IDEMPOTENCE_FILE", tmp_path / "irr_idempotence.json")
    monkeypatch.setattr(irr, "IDEMPOTENCE_LOCK", tmp_path / ".irr_idempotence.lock")
    return tmp_path


def _enable_shadow(monkeypatch) -> None:
    monkeypatch.setenv("OPIP_IRR_MODE", "shadow")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")


class FakeResponse:
    def __init__(self, *, payload=None, status_code=200, body_only=False):
        self.status_code = status_code
        self._payload = payload
        self._body_only = body_only

    def json(self):
        if self._body_only:
            raise ValueError("not json")
        return self._payload


def make_post(
    *,
    challenge=None,
    content=None,
    payload=None,
    status_code=200,
    raise_exc=None,
    usage=None,
):
    """Build an injected transport that records an exact call log."""
    calls = []

    def post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if raise_exc is not None:
            raise raise_exc
        if status_code != 200:
            return FakeResponse(payload={"error": "x"}, status_code=status_code)
        if payload is not None:
            return FakeResponse(payload=payload)
        body = json.dumps(challenge) if content is None else content
        return FakeResponse(
            payload={
                "choices": [{"message": {"content": body}}],
                "usage": usage
                or {
                    "prompt_tokens": 900,
                    "completion_tokens": 120,
                    "total_tokens": 1020,
                },
            }
        )

    post.calls = calls
    return post


def _challenge(**overrides):
    challenge = {
        "symbol": "SOLUSD",
        "direction": "LONG",
        "verdict": "caution",
        "reasons": ["RSI extension limits remaining upside"],
        "cited_evidence": ["rsi", "momentum_24h_pct"],
        "conditions": ["a volume expansion would support continuation"],
    }
    challenge.update(overrides)
    return challenge


def _chief_review(
    *,
    decision="alert",
    risk_level="low",
    confidence=75,
    symbol="SOLUSD",
    direction="LONG",
):
    return {
        "market_view": "selective longs",
        "recommended_action": "alert",
        "top_candidates": [
            {
                "symbol": symbol,
                "direction": direction,
                "rank": 1,
                "confidence": confidence,
                "risk_level": risk_level,
                "decision": decision,
                "reason": "trend and target quality aligned",
            }
        ],
        "summary": "one candidate",
        "opip_ai_route": {
            "provider": "openai",
            "model": "gpt-6.1-sol",
            "route_tier": "premium",
            "route_reason": "finalist_count<=3",
            "reasoning_effort": "high",
        },
    }


def _request_payload(*, symbol="SOLUSD", direction="LONG"):
    return {
        "candidate_count": 1,
        "market_regime_context": {"ohm_breadth": {"regime": "RISK_ON"}},
        "candidates": [
            {
                "symbol": symbol,
                "direction": direction,
                "technical_score": 88,
                "rsi": 71.4,
                "trend": "bullish",
                "momentum_24h_pct": 6.2,
                "execution_evidence": {"status": "OK"},
                "price_movement_intelligence": {"stage": "CONFIRMED"},
            }
        ],
    }


def _journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _append_spend(path: Path, *, cost: float, calls: int = 1, now: datetime) -> None:
    rows = []
    for _ in range(calls):
        rows.append(
            json.dumps(
                {
                    "timestamp_utc": now.isoformat(),
                    "stage": irr.STAGE,
                    "provider": irr.PROVIDER,
                    "status": irr.STATUS_SUCCEEDED,
                    "total_tokens": 1000,
                    "estimated_cost_usd": cost,
                }
            )
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Routing, selection and zero-authority posture
# --------------------------------------------------------------------------


def test_shadow_review_is_off_by_default_and_spends_nothing():
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: default off, zero spend."""
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_DISABLED
    assert post.calls == []
    assert result["stage"] == irr.STAGE
    assert result["shadow_only"] is True
    assert result["advisory_only"] is True
    assert result["authority_changed"] is False
    assert result["reviews"] == []


def test_only_off_and_shadow_modes_are_accepted(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: unknown modes are refused."""
    monkeypatch.setenv("OPIP_IRR_MODE", "active")
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_INVALID_MODE
    assert post.calls == []


def test_missing_credential_makes_no_call_and_writes_no_evidence(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: a missing key never calls out."""
    monkeypatch.setenv("OPIP_IRR_MODE", "shadow")
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_MISSING_CREDENTIAL
    assert "DEEPSEEK_API_KEY" in result["skipped_reason"]
    assert post.calls == []
    assert _journal(irr.EVIDENCE_FILE) == []


def test_unverified_model_id_is_refused(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: undocumented ids are refused."""
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("OPIP_IRR_MODEL", "deepseek-v4-flash")
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_UNVERIFIED_MODEL
    assert post.calls == []


def test_unqualified_chief_items_are_not_sent(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: only qualified items may be sent."""
    _enable_shadow(monkeypatch)
    post = make_post(challenge=_challenge())

    for review in (
        _chief_review(decision="watch"),
        _chief_review(risk_level="high"),
        _chief_review(confidence=150),
    ):
        result = irr.run_independent_shadow_review(
            review=review, request_payload=_request_payload(), post=post
        )
        assert result["status"] == irr.STATUS_NO_ELIGIBLE_RECOMMENDATIONS

    assert post.calls == []



# --------------------------------------------------------------------------
# Positive path: documented provider request, attribution, bounded response
# --------------------------------------------------------------------------


def test_qualified_recommendation_uses_documented_deepseek_contract(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: documented provider contract."""
    _enable_shadow(monkeypatch)
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_SUCCEEDED, result
    assert len(post.calls) == 1
    call = post.calls[0]

    assert call["url"] == "https://api.deepseek.com/chat/completions"
    body = call["json"]
    assert body["model"] == "deepseek-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 700
    assert body["reasoning_effort"] == "high"
    assert body["stream"] is False
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    # Documented JSON-output precondition: the word "json" plus an example.
    assert "json" in body["messages"][0]["content"].lower()
    assert '"verdict"' in body["messages"][0]["content"]

    timeout = call["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 20.0
    assert timeout.connect <= 5.0
    assert call["headers"]["Authorization"] == "Bearer test-deepseek-key"

    row = result["reviews"][0]
    assert row["status"] == irr.STATUS_SUCCEEDED
    assert row["verdict"] == "caution"
    assert row["reasons"] == ["RSI extension limits remaining upside"]
    assert row["challenge"]["cited_evidence"] == ["rsi", "momentum_24h_pct"]
    assert row["input_tokens"] == 900
    assert row["output_tokens"] == 120
    assert row["total_tokens"] == 1020
    assert row["estimated_cost_usd"] == irr.estimate_cost_usd(
        input_tokens=900, output_tokens=120
    )
    assert row["chief_route"]["model"] == "gpt-6.1-sol"

    evidence = row["evidence"]
    assert evidence["symbol"] == "SOLUSD"
    assert evidence["deterministic_candidate_evidence"]["technical_score"] == 88
    assert evidence["chief_conclusion"]["decision"] == "alert"
    assert evidence["authority"] == "advisory_only"


def test_evidence_journal_is_auditable_and_carries_no_credential(
    monkeypatch,
):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: auditable, credential-free evidence."""
    _enable_shadow(monkeypatch)
    post = make_post(challenge=_challenge())

    irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    text = irr.EVIDENCE_FILE.read_text(encoding="utf-8")
    assert "test-deepseek-key" not in text
    assert "Bearer" not in text

    rows = _journal(irr.EVIDENCE_FILE)
    assert len(rows) == 1
    record = rows[0]
    assert record["stage"] == irr.STAGE
    assert record["provider"] == "deepseek"
    assert record["model"] == "deepseek-flash"
    assert record["prompt_version"] == irr.PROMPT_VERSION
    assert record["shadow_only"] is True
    assert record["advisory_only"] is True
    assert record["authority_changed"] is False
    assert record["funded_trade_authority_changed"] is False
    assert record["verdict"] == "caution"
    assert record["evidence_digest"] == row_digest(record)


def row_digest(record) -> str:
    """Recompute the digest from the persisted immutable evidence copy."""
    return hashlib.sha256(
        irr.canonical_json(record["evidence"]).encode("utf-8")
    ).hexdigest()


def test_evidence_copy_is_immutable_and_matches_the_transmitted_bytes(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: the evidence copy is immutable."""
    _enable_shadow(monkeypatch)
    post = make_post(challenge=_challenge())
    review = _chief_review()
    payload = _request_payload()

    irr.run_independent_shadow_review(
        review=review, request_payload=payload, post=post
    )
    transmitted = post.calls[0]["json"]["messages"][1]["content"]
    record = _journal(irr.EVIDENCE_FILE)[0]

    assert record["evidence_digest"] == hashlib.sha256(
        transmitted.encode("utf-8")
    ).hexdigest()
    assert irr.canonical_json(record["evidence"]) == transmitted

    # Mutating the live inputs afterwards cannot change what was reviewed.
    review["top_candidates"][0]["decision"] = "reject"
    review["top_candidates"][0]["reason"] = "mutated after the call"
    payload["candidates"][0]["technical_score"] = 1
    assert irr.canonical_json(record["evidence"]) == transmitted
    assert row_digest(record) == record["evidence_digest"]


def test_non_advisory_provider_fields_are_dropped(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: authority-shaped fields are dropped."""
    _enable_shadow(monkeypatch)
    post = make_post(
        challenge=_challenge(
            action="BUY",
            recommended_action="alert",
            leverage=25,
            position_size=50000,
            rank=1,
        )
    )

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    challenge = result["reviews"][0]["challenge"]
    assert "action" not in challenge
    assert "leverage" not in challenge
    assert "position_size" not in challenge
    assert challenge["non_advisory_fields_ignored"] == [
        "action",
        "leverage",
        "position_size",
        "rank",
        "recommended_action",
    ]
    assert challenge["authority_changed"] is False

    record = _journal(irr.EVIDENCE_FILE)[0]
    assert "BUY" not in json.dumps(record)



# --------------------------------------------------------------------------
# Negative paths: malformed answers, provider failures, budget, idempotence
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response_kwargs",
    [
        {"content": "not-json-at-all"},
        {"content": json.dumps({"verdict": "buy", "reasons": ["x"]})},
        {"content": json.dumps({"verdict": "agree", "reasons": []})},
        {"content": json.dumps({"verdict": "agree"})},
        {"content": ""},
        {"content": json.dumps([1, 2, 3])},
        {
            "content": json.dumps(
                {"verdict": "agree", "reasons": ["x"], "symbol": "BTCUSD"}
            )
        },
        {"content": json.dumps({"verdict": "agree", "reasons": ["x"], "direction": "SHORT"})},
        {"payload": {"choices": []}},
        {"payload": {"choices": [{"message": {}}]}},
        {"payload": {"choices": [{"message": {"content": "{}"}}], "usage": {}}},
    ],
)
def test_malformed_provider_answer_fails_closed(monkeypatch, response_kwargs):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: malformed answers fail closed."""
    _enable_shadow(monkeypatch)
    post = make_post(**response_kwargs)

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_FAILED
    row = result["reviews"][0]
    assert row["status"] == irr.STATUS_FAILED
    assert row["error_type"] == "MALFORMED_RESPONSE"
    assert "verdict" not in row
    record = _journal(irr.EVIDENCE_FILE)[0]
    assert record["status"] == irr.STATUS_FAILED
    assert record["estimated_cost_usd"] == 0.0


def test_non_json_provider_body_fails_closed(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: a non-JSON body fails closed."""
    _enable_shadow(monkeypatch)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return FakeResponse(body_only=True)

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_FAILED
    assert result["reviews"][0]["error_type"] == "MALFORMED_RESPONSE"
    assert calls == ["https://api.deepseek.com/chat/completions"]


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectTimeout("timed out"), "TIMEOUT"),
        (httpx.ReadTimeout("read timed out"), "TIMEOUT"),
        (httpx.ConnectError("refused"), "TRANSPORT_ERROR"),
    ],
)
def test_provider_transport_failure_is_contained(monkeypatch, exc, expected):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: transport failures are contained."""
    _enable_shadow(monkeypatch)
    post = make_post(raise_exc=exc)

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_FAILED
    assert result["reviews"][0]["error_type"] == expected
    assert len(post.calls) == 1  # one attempt, never retried


def test_provider_http_error_is_recorded_without_a_body(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: an HTTP error records status only."""
    _enable_shadow(monkeypatch)
    post = make_post(status_code=500)

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_FAILED
    assert result["reviews"][0]["error_type"] == "HTTP_ERROR"
    text = irr.EVIDENCE_FILE.read_text(encoding="utf-8")
    # The provider error body is never persisted or logged.
    assert '"error"' not in text



def test_daily_cost_budget_blocks_further_spend(monkeypatch, isolated_irr):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: the daily cost ceiling is enforced."""
    _enable_shadow(monkeypatch)
    _append_spend(
        isolated_irr / "irr_evidence.jsonl",
        cost=1.25,
        now=datetime.now(timezone.utc),
    )
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_BUDGET_EXHAUSTED
    assert "cost budget" in result["reviews"][0]["skipped_reason"]
    assert post.calls == []


def test_daily_call_budget_blocks_further_spend(monkeypatch, isolated_irr):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: the daily call ceiling is enforced."""
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("OPIP_IRR_DAILY_CALL_BUDGET", "1")
    _append_spend(
        isolated_irr / "irr_evidence.jsonl",
        cost=0.001,
        calls=1,
        now=datetime.now(timezone.utc),
    )
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_BUDGET_EXHAUSTED
    assert "call budget" in result["reviews"][0]["skipped_reason"]
    assert post.calls == []


def test_unreadable_usage_journal_fails_closed(monkeypatch, isolated_irr):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: unprovable spend blocks the call."""
    _enable_shadow(monkeypatch)
    directory = isolated_irr / "irr_evidence_dir"
    directory.mkdir()
    monkeypatch.setattr(irr, "EVIDENCE_FILE", directory)
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_FAILED
    assert result["reviews"][0]["error_type"] == "USAGE_UNAVAILABLE"
    assert post.calls == []


def test_per_call_cost_limit_blocks_an_expensive_request(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: the per-call ceiling is enforced."""
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("OPIP_IRR_MAX_CALL_COST_USD", "0.0001")
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert result["status"] == irr.STATUS_CALL_COST_LIMIT
    assert result["reviews"][0]["worst_case_cost_usd"] > 0.0001
    assert post.calls == []


def test_identical_evidence_is_reviewed_once(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: identical evidence replays freely."""
    _enable_shadow(monkeypatch)
    post = make_post(challenge=_challenge())

    first = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )
    second = irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )

    assert first["status"] == irr.STATUS_SUCCEEDED
    assert second["status"] == irr.STATUS_SUCCEEDED
    assert second["reviews"][0]["status"] == irr.STATUS_IDEMPOTENT_REPLAY
    assert second["reviews"][0]["verdict"] == "caution"
    assert second["reviews"][0]["estimated_cost_usd"] == 0.0
    assert len(post.calls) == 1
    rows = _journal(irr.EVIDENCE_FILE)
    assert [row["status"] for row in rows] == [
        irr.STATUS_SUCCEEDED,
        irr.STATUS_IDEMPOTENT_REPLAY,
    ]


def test_changed_evidence_is_a_new_review(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: changed evidence is re-reviewed."""
    _enable_shadow(monkeypatch)
    post = make_post(challenge=_challenge())

    irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=_request_payload(), post=post
    )
    changed = _request_payload()
    changed["candidates"][0]["technical_score"] = 91
    irr.run_independent_shadow_review(
        review=_chief_review(), request_payload=changed, post=post
    )

    assert len(post.calls) == 2
    digests = [row["evidence_digest"] for row in _journal(irr.EVIDENCE_FILE)]
    assert len(set(digests)) == 2


def test_only_the_top_eligible_recommendations_are_reviewed(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: Stage 2 stays selective."""
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("OPIP_IRR_MAX_CANDIDATES", "1")
    review = _chief_review()
    review["top_candidates"] = [
        {
            "symbol": "SOLUSD",
            "direction": "LONG",
            "rank": 1,
            "confidence": 80,
            "risk_level": "low",
            "decision": "alert",
            "reason": "first",
        },
        {
            "symbol": "ADAUSD",
            "direction": "LONG",
            "rank": 2,
            "confidence": 78,
            "risk_level": "low",
            "decision": "alert",
            "reason": "second",
        },
    ]
    post = make_post(challenge=_challenge())

    result = irr.run_independent_shadow_review(
        review=review, request_payload=_request_payload(), post=post
    )

    assert result["recommendation_count"] == 1
    assert result["reviews"][0]["symbol"] == "SOLUSD"
    assert len(post.calls) == 1



# --------------------------------------------------------------------------
# Chief-review integration: additive evidence, zero authority
# --------------------------------------------------------------------------


def _snapshot(symbol="SOLUSD") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        last_price=100,
        ema20=99,
        ema50=98,
        ema200=97,
        rsi=71.4,
        macd_line=1,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2,
        atr_pct=2,
        volume_ratio=1.2,
        technical_score=88,
        trend="bullish",
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=symbol,
        primary_quote_currency="USD",
        ticker_last=100,
    )


def _install_chief(monkeypatch, output: dict, *, external=None):
    """Install a deterministic Stage-1 client without touching /app/data."""
    calls = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            usage = SimpleNamespace(
                input_tokens=100,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
                output_tokens=30,
                output_tokens_details=SimpleNamespace(reasoning_tokens=5),
                total_tokens=130,
            )
            text = json.dumps(output) if isinstance(output, dict) else output
            return SimpleNamespace(output_text=text, usage=usage)

    responses = Responses()
    monkeypatch.setattr(
        chief_analyst,
        "OpenAI",
        lambda api_key=None, **kwargs: SimpleNamespace(responses=responses),
    )
    monkeypatch.setattr(
        chief_analyst,
        "append_usage_record",
        lambda **kwargs: {
            "model": kwargs["model"],
            "candidate_count": kwargs["candidate_count"],
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 30,
            "reasoning_tokens": 5,
            "total_tokens": 130,
        },
    )
    monkeypatch.setattr(
        chief_analyst,
        "capture_chief_review_decisions",
        lambda *args, **kwargs: {"captured": 0},
    )
    if external is not None:
        monkeypatch.setattr(
            chief_analyst,
            "run_independent_shadow_review",
            lambda **kwargs: external(**kwargs),
        )
    return calls


def _chief_output(decision="alert", risk_level="low", confidence=75) -> dict:
    return {
        "market_view": "selective longs",
        "recommended_action": "alert",
        "top_candidates": [
            {
                "symbol": "SOLUSD",
                "direction": "LONG",
                "rank": 1,
                "confidence": confidence,
                "risk_level": risk_level,
                "decision": decision,
                "reason": "trend and target quality aligned",
            }
        ],
        "summary": "one candidate",
    }



def test_stage2_runs_after_the_chief_conclusion_and_never_changes_it(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: additive evidence only."""
    _enable_shadow(monkeypatch)
    _install_chief(monkeypatch, _chief_output())
    post = make_post(challenge=_challenge())
    real = irr.run_independent_shadow_review
    monkeypatch.setattr(
        chief_analyst,
        "run_independent_shadow_review",
        lambda **kwargs: real(post=post, **kwargs),
    )

    result = chief_analyst.review_candidates(
        [_snapshot()], "gpt-6.1-sol", "openai-test-key"
    )

    assert result["top_candidates"] == _chief_output()["top_candidates"]
    assert result["recommended_action"] == "alert"
    assert result["market_view"] == "selective longs"
    assert result["summary"] == "one candidate"

    shadow = result["opip_irr_shadow"]
    assert shadow["status"] == irr.STATUS_SUCCEEDED
    assert shadow["shadow_only"] is True
    assert shadow["advisory_only"] is True
    assert shadow["authority_changed"] is False
    assert shadow["reviews"][0]["verdict"] == "caution"
    assert len(post.calls) == 1
    assert post.calls[0]["json"]["model"] == "deepseek-flash"


def test_stage2_failure_leaves_the_chief_conclusion_intact(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: a Stage-2 failure changes nothing."""
    _enable_shadow(monkeypatch)
    _install_chief(monkeypatch, _chief_output())
    post = make_post(raise_exc=httpx.ConnectError("provider down"))
    real = irr.run_independent_shadow_review
    monkeypatch.setattr(
        chief_analyst,
        "run_independent_shadow_review",
        lambda **kwargs: real(post=post, **kwargs),
    )

    result = chief_analyst.review_candidates(
        [_snapshot()], "gpt-6.1-sol", "openai-test-key"
    )

    assert result["top_candidates"] == _chief_output()["top_candidates"]
    assert result["opip_irr_shadow"]["status"] == irr.STATUS_FAILED
    assert result["opip_irr_shadow"]["reviews"][0]["error_type"] == (
        "TRANSPORT_ERROR"
    )


def test_stage2_unexpected_error_is_contained(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: an unexpected defect is contained."""
    _enable_shadow(monkeypatch)
    _install_chief(monkeypatch, _chief_output())

    def boom(**_kwargs):
        raise RuntimeError("unexpected stage-2 defect")

    monkeypatch.setattr(chief_analyst, "run_independent_shadow_review", boom)

    result = chief_analyst.review_candidates(
        [_snapshot()], "gpt-6.1-sol", "openai-test-key"
    )

    assert result["top_candidates"] == _chief_output()["top_candidates"]
    shadow = result["opip_irr_shadow"]
    assert shadow["status"] == "FAILED"
    assert shadow["error_type"] == "RuntimeError"
    assert shadow["authority_changed"] is False


def test_stage2_is_disabled_when_the_mode_is_off(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: an off mode is inert."""
    _install_chief(monkeypatch, _chief_output())
    post = make_post(challenge=_challenge())
    real = irr.run_independent_shadow_review
    monkeypatch.setattr(
        chief_analyst,
        "run_independent_shadow_review",
        lambda **kwargs: real(post=post, **kwargs),
    )

    result = chief_analyst.review_candidates(
        [_snapshot()], "gpt-6.1-sol", "openai-test-key"
    )

    assert result["opip_irr_shadow"]["status"] == irr.STATUS_DISABLED
    assert post.calls == []


def test_stage2_module_imports_no_authority_module():
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: no authority import is reachable."""
    tree = ast.parse(IRR_PATH.read_text(encoding="utf-8"))
    forbidden = (
        "kraken",
        "order",
        "position",
        "telegram",
        "execution",
        "risk",
        "rank",
        "alert",
        "paper",
        "canonical",
        "committee",
        "learning",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.lower() for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [str(node.module or "").lower()]
        else:
            continue
        for name in names:
            assert not any(fragment in name for fragment in forbidden), name
