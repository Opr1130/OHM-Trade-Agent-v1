"""ATDD: Stage-2 Independent Research & Risk Review (IRR), shadow-only.

Covers the four reviewed corrections: no synchronous provider I/O on the primary
pipeline, immutable evidence from finally-qualified candidates only, strict
pre-call reservation accounting, and the approved-endpoint restriction. Also
retains the essential negatives (inert by default, credential hygiene,
malformed answers, idempotence, zero authority).
"""

from __future__ import annotations

import ast
import hashlib
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import app.services.independent_research_review as irr
from app.services import chief_analyst
from app.scanner.models import MarketSnapshot


APP_ROOT = Path(__file__).resolve().parents[1]
IRR_PATH = APP_ROOT / "app" / "services" / "independent_research_review.py"
DRAIN_JOB_PATH = APP_ROOT / "app" / "jobs" / "drain_irr_shadow.py"

pytestmark = pytest.mark.acceptance

_ENV_NAMES = (
    "OPIP_IRR_MODE",
    "OPIP_IRR_MODEL",
    "OPIP_IRR_BASE_URL",
    "OPIP_IRR_TIMEOUT_SECONDS",
    "OPIP_IRR_MAX_OUTPUT_TOKENS",
    "OPIP_IRR_REASONING_EFFORT",
    "OPIP_IRR_MAX_CANDIDATES",
    "OPIP_IRR_DRAIN_BATCH",
    "OPIP_IRR_DAILY_CALL_BUDGET",
    "OPIP_IRR_DAILY_TOKEN_BUDGET",
    "OPIP_IRR_DAILY_COST_BUDGET_USD",
    "OPIP_IRR_MAX_CALL_COST_USD",
    "DEEPSEEK_API_KEY",
)


@pytest.fixture(autouse=True)
def isolated_irr(tmp_path, monkeypatch):
    """Keep queue, ledgers and configuration inside the test."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    paths = {
        "QUEUE_FILE": tmp_path / "irr_queue.jsonl",
        "QUEUE_LOCK": tmp_path / ".irr_queue.lock",
        "QUEUE_CHECKPOINT_FILE": tmp_path / "irr_checkpoint.json",
        "QUEUE_CONSUMER_LOCK": tmp_path / ".irr_queue.consumer.lock",
        "EVIDENCE_FILE": tmp_path / "irr_evidence.jsonl",
        "EVIDENCE_LOCK": tmp_path / ".irr_evidence.lock",
        "SPEND_LEDGER_FILE": tmp_path / "irr_spend.jsonl",
        "SPEND_LEDGER_LOCK": tmp_path / ".irr_spend.lock",
        "IDEMPOTENCE_FILE": tmp_path / "irr_idempotence.json",
        "IDEMPOTENCE_LOCK": tmp_path / ".irr_idempotence.lock",
    }
    for name, path in paths.items():
        monkeypatch.setattr(irr, name, path)
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
    on_call=None,
):
    """Build an injected transport that records an exact call log."""
    calls = []

    def post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if on_call is not None:
            on_call(url, kwargs)
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
                if usage is not None
                else {
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


def _alert(*, symbol="SOLUSD", direction="LONG", qualified=True, **overrides):
    alert = {
        "symbol": symbol,
        "direction": direction,
        "rank": 1,
        "decision": "alert",
        "risk_level": "low",
        "confidence": 75,
        "reason": "trend and target quality aligned",
        "technical_score": 88,
        "market_regime": "RISK_ON",
        "market_intelligence": {"status": "OK"},
        "price_movement": {"stage": "CONFIRMED"},
        "execution_evidence": {"status": "OK"},
        "entry_plan": {"valid_now": True, "reward_to_risk_2": 2.4},
        "target_quality_qualified": qualified,
        "economic_qualified": qualified,
        "target_attainability_score": 86,
        "economic_target_2_move_pct": 9.1,
        "economic_validation_net_t2": 31.4,
        "economic_validation_capital": 250.0,
        "economic_position_notional": 1000.0,
        "profit_rank_score": 74.2,
        "opportunity_rank": 1 if qualified else None,
        "liquidity_capacity_status": "ELIGIBLE",
        "liquidity_capacity_ceiling_usd": 50000.0,
    }
    alert.update(overrides)
    return alert


CHIEF_ROUTE = {
    "provider": "openai",
    "model": "gpt-6.1-sol",
    "route_tier": "premium",
    "reasoning_effort": "high",
}


def _enqueue(*alerts, now=None):
    return irr.enqueue_final_qualified(
        alerts=list(alerts) or [_alert()],
        chief_route=CHIEF_ROUTE,
        market_regime={"regime": "RISK_ON"},
        now=now,
    )


def _journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _ledger() -> list[dict]:
    return _journal(irr.SPEND_LEDGER_FILE)


def _append_ledger_row(row: dict) -> None:
    irr.SPEND_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with irr.SPEND_LEDGER_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _reserve_row(*, tokens: int = 2000, cost: float = 0.002, now=None) -> dict:
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    return {
        "record": "RESERVE",
        "reservation_id": "pre-existing",
        "timestamp_utc": stamp,
        "provider": "deepseek",
        "model": "deepseek-flash",
        "reserved_tokens": tokens,
        "reserved_cost_usd": cost,
    }


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


def _chief_output() -> dict:
    return {
        "market_view": "selective longs",
        "recommended_action": "alert",
        "top_candidates": [
            {
                "symbol": "SOLUSD",
                "direction": "LONG",
                "rank": 1,
                "confidence": 75,
                "risk_level": "low",
                "decision": "alert",
                "reason": "trend and target quality aligned",
            }
        ],
        "summary": "one candidate",
    }


def _install_chief(monkeypatch) -> None:
    """Install a deterministic Stage-1 client without touching /app/data."""
    output = _chief_output()

    class Responses:
        def create(self, **_kwargs):
            usage = SimpleNamespace(
                input_tokens=100,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
                output_tokens=30,
                output_tokens_details=SimpleNamespace(reasoning_tokens=5),
                total_tokens=130,
            )
            return SimpleNamespace(output_text=json.dumps(output), usage=usage)

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


def test_chief_review_performs_no_provider_io(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: no DeepSeek I/O on the Chief path."""
    _enable_shadow(monkeypatch)
    _install_chief(monkeypatch)
    _enqueue()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Stage 2 must not call the provider on the Chief path")

    monkeypatch.setattr(irr.httpx, "post", forbidden)

    result = chief_analyst.review_candidates(
        [_snapshot()], "gpt-6.1-sol", "openai-test-key"
    )

    assert result["top_candidates"] == _chief_output()["top_candidates"]
    assert "opip_irr_shadow" not in result


def test_hung_provider_never_delays_the_primary_pipeline(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-003: a hung provider cannot block a scan."""
    _enable_shadow(monkeypatch)
    _install_chief(monkeypatch)
    assert _enqueue()["enqueued"] == 1

    release = threading.Event()
    calls: list[str] = []

    def hung_post(url, **_kwargs):
        calls.append(url)
        release.wait(15)  # simulates a provider that never answers
        raise httpx.ReadTimeout("provider hung")

    worker = threading.Thread(
        target=irr.drain_queue,
        kwargs={"post": hung_post, "batch_limit": 1},
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 10
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls, "worker did not reach the provider"

    started = time.perf_counter()
    review = chief_analyst.review_candidates(
        [_snapshot()], "gpt-6.1-sol", "openai-test-key"
    )
    queued = _enqueue(_alert(symbol="ADAUSD"))
    elapsed = time.perf_counter() - started

    release.set()
    worker.join(timeout=10)

    assert elapsed < 1.0, f"primary pipeline waited {elapsed:.3f}s on the provider"
    assert review["top_candidates"] == _chief_output()["top_candidates"]
    assert queued["status"] == "ENQUEUED"
    assert queued["enqueued"] == 1


def test_producer_enqueues_only_finally_qualified_survivors(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: final qualification gates the queue."""
    _enable_shadow(monkeypatch)

    result = _enqueue(
        _alert(symbol="SOLUSD"),
        _alert(symbol="DOGEUSD", target_quality_qualified=False),
        _alert(symbol="ADAUSD", economic_qualified=False),
        _alert(symbol="XRPUSD", opportunity_rank=None),
    )

    assert result["status"] == "ENQUEUED"
    assert result["enqueued"] == 1
    assert result["skipped_unqualified"] == 3
    rows = _journal(irr.QUEUE_FILE)
    assert [row["symbol"] for row in rows] == ["SOLUSD"]
    qualification = rows[0]["evidence"]["final_qualification"]
    assert qualification["target_quality_qualified"] is True
    assert qualification["economic_qualified"] is True
    assert qualification["portfolio_rank"] == 1


def test_worker_refuses_evidence_without_final_qualification(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: the worker re-checks qualification."""
    _enable_shadow(monkeypatch)
    evidence = irr.build_evidence_copy(
        alert=_alert(qualified=False), chief_route=CHIEF_ROUTE
    )
    irr.QUEUE_FILE.write_text(
        json.dumps(
            {
                "record": "REQUEST",
                "symbol": "SOLUSD",
                "direction": "LONG",
                "evidence": evidence,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    assert result["results"][0]["status"] == irr.STATUS_NOT_QUALIFIED
    assert post.calls == []
    assert _ledger() == []


def test_worker_refuses_queue_evidence_that_does_not_match_its_digest(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: a mutated queue row never spends."""
    _enable_shadow(monkeypatch)
    _enqueue()
    row = _journal(irr.QUEUE_FILE)[0]
    row["evidence"]["deterministic_candidate_evidence"]["technical_score"] = 1
    irr.QUEUE_FILE.write_text(json.dumps(row) + "\n", encoding="utf-8")
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    refused = result["results"][0]
    assert refused["status"] == irr.STATUS_EVIDENCE_MISMATCH
    assert refused["error_type"] == "DIGEST"
    assert post.calls == []
    assert _ledger() == []

    # The same applies when the stored request identity was rewritten.
    irr.QUEUE_FILE.write_text("", encoding="utf-8")
    irr.QUEUE_CHECKPOINT_FILE.unlink(missing_ok=True)
    _enqueue(_alert(symbol="ADAUSD"))
    tampered = _journal(irr.QUEUE_FILE)[-1]
    tampered["request_id"] = "0" * 64
    irr.QUEUE_FILE.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    second = irr.drain_queue(post=post)

    assert second["results"][0]["status"] == irr.STATUS_EVIDENCE_MISMATCH
    assert second["results"][0]["error_type"] == "REQUEST_ID"
    assert post.calls == []
    assert _ledger() == []


def test_expired_queue_request_is_refused_without_spend(monkeypatch):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-006: stale evidence never buys a call."""
    _enable_shadow(monkeypatch)
    now = datetime.now(timezone.utc)
    _enqueue(now=now - timedelta(hours=2))
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post, now=now)

    row = result["results"][0]
    assert row["status"] == irr.STATUS_QUEUE_EXPIRED
    assert row["error_type"] == "EXPIRED"
    assert row["charged_cost_usd"] == 0.0
    assert post.calls == []
    assert _ledger() == []


def test_queue_request_without_a_usable_timestamp_fails_closed(monkeypatch):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-006: unprovable age is refused, not billed."""
    _enable_shadow(monkeypatch)
    _enqueue()
    row = _journal(irr.QUEUE_FILE)[0]
    row.pop("enqueued_at", None)
    irr.QUEUE_FILE.write_text(json.dumps(row) + "\n", encoding="utf-8")
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    assert result["results"][0]["status"] == irr.STATUS_QUEUE_EXPIRED
    assert result["results"][0]["error_type"] == "TIMESTAMP"
    assert post.calls == []
    assert _ledger() == []




def test_evidence_copy_is_immutable_and_digest_verifiable(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: evidence is immutable and digested."""
    _enable_shadow(monkeypatch)
    alert = _alert()
    _enqueue(alert)
    queued = _journal(irr.QUEUE_FILE)[0]

    alert["reason"] = "mutated after enqueue"
    alert["technical_score"] = 1

    transmitted: list[str] = []

    def on_call(_url, kwargs):
        transmitted.append(kwargs["json"]["messages"][1]["content"])

    post = make_post(challenge=_challenge(), on_call=on_call)
    result = irr.drain_queue(post=post)

    assert result["status"] == irr.STATUS_SUCCEEDED
    assert len(transmitted) == 1
    digest = hashlib.sha256(transmitted[0].encode("utf-8")).hexdigest()
    assert digest == queued["evidence_digest"]
    assert irr.evidence_digest(queued["evidence"]) == digest
    assert queued["evidence"]["final_qualification"]["portfolio_rank"] == 1
    assert irr.canonical_json(queued["evidence"]) == transmitted[0]


def test_non_advisory_provider_fields_are_dropped(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-004: authority-shaped fields are dropped."""
    _enable_shadow(monkeypatch)
    _enqueue()
    post = make_post(
        challenge=_challenge(
            action="BUY",
            leverage=25,
            position_size=50000,
            rank=1,
        )
    )

    result = irr.drain_queue(post=post)

    challenge = result["results"][0]["challenge"]
    assert "action" not in challenge
    assert "leverage" not in challenge
    assert challenge["non_advisory_fields_ignored"] == [
        "action",
        "leverage",
        "position_size",
        "rank",
    ]
    assert challenge["authority_changed"] is False
    assert "BUY" not in irr.EVIDENCE_FILE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "candidate",
    [
        "http://api.deepseek.com",
        "https://evil.example",
        "https://api.deepseek.com.evil.example",
        "https://evil.example/api.deepseek.com",
        "https://api.deepseek.com:8443",
        "https://api.deepseek.com@evil.example",
        "https://api.deepseek.com/chat/completions",
        "https://api.deepseek.com/?redirect=evil",
        "https://api.deepseek.com#fragment",
    ],
)
def test_only_the_approved_provider_endpoint_is_accepted(candidate):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-006: arbitrary HTTPS hosts are refused."""
    with pytest.raises(irr.IRRUnapprovedEndpoint):
        irr.approved_base_url(candidate)


def test_approved_origin_is_normalised():
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-006: only the approved origin resolves."""
    assert irr.approved_base_url(None) == irr.APPROVED_BASE_URL
    assert irr.approved_base_url("https://api.deepseek.com/") == irr.APPROVED_BASE_URL
    assert irr.approved_base_url(" https://api.deepseek.com ") == irr.APPROVED_BASE_URL


def test_unapproved_endpoint_makes_no_call(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-006: an unapproved endpoint never runs."""
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("OPIP_IRR_BASE_URL", "https://evil.example")
    _enqueue()
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    assert result["status"] == irr.STATUS_CONFIGURATION_ERROR
    assert post.calls == []
    assert _ledger() == []
    assert result["processed"] == 0


def test_invoke_refuses_a_rogue_config(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-006: the call path re-checks the origin."""
    _enable_shadow(monkeypatch)
    config = replace(irr.resolve_config(), base_url="https://evil.example")
    post = make_post(challenge=_challenge())

    with pytest.raises(irr.IRRUnapprovedEndpoint):
        irr._invoke(config, {"model": "deepseek-flash"}, post=post)
    assert post.calls == []


def test_reservation_is_written_before_the_provider_call(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: spend is reserved before the call."""
    _enable_shadow(monkeypatch)
    _enqueue()
    observed: list[list[str]] = []

    def on_call(_url, _kwargs):
        observed.append([row["record"] for row in _ledger()])

    post = make_post(challenge=_challenge(), on_call=on_call)
    result = irr.drain_queue(post=post)

    assert result["status"] == irr.STATUS_SUCCEEDED
    assert observed == [["RESERVE"]]
    assert [row["record"] for row in _ledger()] == ["RESERVE", "SETTLE"]


def test_concurrent_attempts_cannot_overspend(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: concurrency cannot exceed the budget."""
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("OPIP_IRR_DAILY_CALL_BUDGET", "1")
    _enqueue(_alert(symbol="SOLUSD"), _alert(symbol="ADAUSD"))
    post = make_post(challenge=_challenge())

    results: list[dict] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(irr.drain_queue(post=post, batch_limit=1)),
            daemon=True,
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(post.calls) == 1
    statuses = sorted(result["results"][0]["status"] for result in results)
    assert statuses == [irr.STATUS_BUDGET_EXHAUSTED, irr.STATUS_SUCCEEDED]
    reserves = [row for row in _ledger() if row["record"] == "RESERVE"]
    assert len(reserves) == 1


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"raise_exc": httpx.ConnectTimeout("timed out")}, "TIMEOUT"),
        ({"raise_exc": httpx.ConnectError("refused")}, "TRANSPORT_ERROR"),
        ({"status_code": 500}, "HTTP_ERROR"),
    ],
)
def test_failed_call_is_chargeable(monkeypatch, kwargs, expected):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: a failed call is still charged."""
    _enable_shadow(monkeypatch)
    _enqueue()
    post = make_post(**kwargs)

    result = irr.drain_queue(post=post)

    row = result["results"][0]
    assert row["status"] == irr.STATUS_FAILED
    assert row["error_type"] == expected
    settle = [item for item in _ledger() if item["record"] == "SETTLE"][0]
    assert settle["outcome"] == "CHARGED_FAILURE"
    assert settle["usage_available"] is False
    assert settle["actual_cost_usd"] == row["worst_case_cost_usd"]
    state = irr.spend_state(now=datetime.now(timezone.utc))
    assert state.calls == 1
    assert state.cost_usd == row["worst_case_cost_usd"]


def test_malformed_response_with_usage_is_charged(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: billed tokens are charged even on failure."""
    _enable_shadow(monkeypatch)
    _enqueue()
    post = make_post(
        content="not-json-at-all",
        usage={"prompt_tokens": 5000, "completion_tokens": 900, "total_tokens": 5900},
    )

    result = irr.drain_queue(post=post)

    row = result["results"][0]
    assert row["status"] == irr.STATUS_FAILED
    assert row["error_type"] == "MALFORMED_RESPONSE"
    assert row["usage_available"] is True
    assert row["charged_tokens"] == 5900
    assert row["charged_cost_usd"] == irr.estimate_cost_usd(
        input_tokens=5000, output_tokens=900
    )
    settle = [item for item in _ledger() if item["record"] == "SETTLE"][0]
    assert settle["actual_tokens"] == 5900
    assert settle["outcome"] == "CHARGED_FAILURE"
    state = irr.spend_state(now=datetime.now(timezone.utc))
    assert state.tokens == 5900


def test_failure_without_usage_charges_the_reserved_worst_case(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: missing usage charges the worst case."""
    _enable_shadow(monkeypatch)
    _enqueue()
    post = make_post(challenge=_challenge(), usage={})

    result = irr.drain_queue(post=post)

    row = result["results"][0]
    assert row["status"] == irr.STATUS_SUCCEEDED
    assert row["usage_available"] is False
    assert row["charged_tokens"] == row["worst_case_tokens"]
    assert row["charged_cost_usd"] == row["worst_case_cost_usd"]
    settle = [item for item in _ledger() if item["record"] == "SETTLE"][0]
    assert settle["usage_available"] is False
    assert settle["actual_tokens"] == row["worst_case_tokens"]


def test_malformed_spend_ledger_fails_closed(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: a corrupt ledger blocks spending."""
    _enable_shadow(monkeypatch)
    _enqueue()
    irr.SPEND_LEDGER_FILE.write_text('{"record": "RESERVE"\n', encoding="utf-8")
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    row = result["results"][0]
    assert row["status"] == irr.STATUS_SPEND_UNVERIFIABLE
    assert "ledger" in row["skipped_reason"]
    assert post.calls == []
    assert row["charged_cost_usd"] == 0.0


def test_spend_persistence_failure_fails_closed(monkeypatch, isolated_irr):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: unwritable spend state blocks spending."""
    _enable_shadow(monkeypatch)
    directory = isolated_irr / "spend_dir"
    directory.mkdir()
    monkeypatch.setattr(irr, "SPEND_LEDGER_FILE", directory)
    _enqueue()
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    assert result["results"][0]["status"] == irr.STATUS_SPEND_UNVERIFIABLE
    assert post.calls == []


def test_daily_cost_budget_blocks_before_spending(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: an exhausted budget refuses the call."""
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("OPIP_IRR_DAILY_COST_BUDGET_USD", "0.01")
    _append_ledger_row(_reserve_row(tokens=100, cost=0.02))
    _enqueue()
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    row = result["results"][0]
    assert row["status"] == irr.STATUS_BUDGET_EXHAUSTED
    assert "cost budget" in row["skipped_reason"]
    assert post.calls == []


def test_repeat_evidence_is_not_charged_twice(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-005: identical evidence replays for free."""
    _enable_shadow(monkeypatch)
    _enqueue()
    _enqueue()
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post, batch_limit=2)

    statuses = [row["status"] for row in result["results"]]
    assert statuses == [irr.STATUS_SUCCEEDED, irr.STATUS_IDEMPOTENT_REPLAY]
    assert len(post.calls) == 1
    assert result["results"][1]["charged_cost_usd"] == 0.0
    assert len([row for row in _ledger() if row["record"] == "SETTLE"]) == 1


def test_disabled_is_inert_and_spends_nothing(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-007: the default mode is inert."""
    post = make_post(challenge=_challenge())

    assert _enqueue()["status"] == irr.STATUS_DISABLED
    assert not irr.QUEUE_FILE.exists()
    assert irr.drain_queue(post=post)["status"] == irr.STATUS_DISABLED
    assert post.calls == []
    assert _ledger() == []


def test_missing_credential_leaves_the_queue_unprocessed(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-007: no credential, no call, no loss."""
    monkeypatch.setenv("OPIP_IRR_MODE", "shadow")
    assert _enqueue()["enqueued"] == 1
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    assert result["status"] == irr.STATUS_MISSING_CREDENTIAL
    assert post.calls == []
    assert _ledger() == []
    assert not irr.QUEUE_CHECKPOINT_FILE.exists()


def test_evidence_journal_is_auditable_and_carries_no_credential(monkeypatch):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-007: audit rows carry no credential."""
    _enable_shadow(monkeypatch)
    _enqueue()
    post = make_post(challenge=_challenge())

    result = irr.drain_queue(post=post)

    assert result["status"] == irr.STATUS_SUCCEEDED
    text = irr.EVIDENCE_FILE.read_text(encoding="utf-8")
    assert "test-deepseek-key" not in text
    assert "Bearer" not in text
    record = _journal(irr.EVIDENCE_FILE)[0]
    assert record["stage"] == irr.STAGE
    assert record["provider"] == "deepseek"
    assert record["model"] == "deepseek-flash"
    assert record["prompt_version"] == irr.PROMPT_VERSION
    assert record["shadow_only"] is True
    assert record["advisory_only"] is True
    assert record["authority_changed"] is False
    assert record["verdict"] == "caution"
    assert record["chief_route"]["model"] == "gpt-6.1-sol"
    assert record["evidence_digest"] == irr.evidence_digest(record["evidence"])


@pytest.mark.parametrize("path", [IRR_PATH, DRAIN_JOB_PATH])
def test_stage2_modules_import_no_authority_module(path):
    """ATDD-TWO-STAGE-ADVISORY-AI-v1/AC-007: no authority import is reachable."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
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






