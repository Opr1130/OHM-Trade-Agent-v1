"""Tradevisor V2 advisory integration — ATDD acceptance traceability.

Every test here carries ``@pytest.mark.acceptance`` and a docstring citing
``ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-NNN``; that pair is what
``tests/atdd_scope.py`` reads. The criterion text lives in
``docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md`` and the
per-scenario matrix in ``docs/atdd/tradevisor_v2_atdd.md``.

These are contract-level proofs, not a second copy of the scenario suite. The
scenario suite (``tests/test_tradevisor_*_v1.py``) owns the detailed matrix;
this module proves that each approved criterion's observable outcome holds.

Approved policy: F6 cannot qualify a trade without a trusted forecast or an
owner-approved fallback; SWING also needs completed 4H context. ATDD-38 has an
approved noncanonical shadow-only exception, not a tested canonical outage.
ATDD-43 remains structural, not overload, evidence.

No live TradingView, AI, Telegram or exchange call is made.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import tradevisor as routes
from app.core.config import Settings
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services import tradevisor_evaluation as evaluation
from app.services import tradevisor_inbox as inbox
from app.services import tradevisor_learning as learning
from app.services import tradevisor_pipeline as pipeline
from app.services.tradevisor_ingress import TradevisorSignal

TOKEN = "T" * 43
NOW = datetime(2026, 10, 23, 13, 0, 5, tzinfo=timezone.utc)
SHA_A = "a" * 40
SHA_B = "b" * 40

HEADERS = {routes.TRADEVISOR_VERIFICATION_HEADER: "tradevisor-verified-by-trusted-proxy"}


def make_settings(**over) -> Settings:
    values = dict(
        _env_file=None,
        webhook_secret="x" * 12,
        opip_tradevisor_enabled=True,
        opip_tradevisor_webhook_token=TOKEN,
        opip_tradevisor_trusted_proxy_ips="testclient",
        opip_tradevisor_max_attempts=3,
    )
    values.update(over)
    return Settings(**values)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Isolate the durable inbox and give its internals a valid settings object."""
    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: make_settings())
    yield


def enable(monkeypatch, **over) -> Settings:
    """Point the real route at enabled settings."""
    resolved = make_settings(**over)
    monkeypatch.setattr(routes, "get_settings", lambda: resolved)
    routes.reset_rate_limiter_for_tests()
    return resolved


def client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def bar_times() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    bar_open = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return bar_open, now - timedelta(seconds=1)


def _z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def payload(**over) -> dict:
    bar_open, triggered = bar_times()
    body = {
        "schema_version": "tradevisor-ingress-v1",
        "source": "TRADEVISOR",
        "indicator": "TRADEVISOR_V2",
        "signal": "BUY",
        "symbol": "BTCUSD",
        "exchange": "COINBASE",
        "timeframe": "60",
        "signal_price": 67000.5,
        "bar_opened_at": _z(bar_open),
        "triggered_at": _z(triggered),
        "alert_config_version": "tradevisor-1h-buy",
        "ingest_token": TOKEN,
    }
    body.update(over)
    return body


def rows() -> list[dict]:
    registry = inbox.load_json(inbox.INBOX_FILE) or {}
    return list((registry.get("events") or {}).values())


def signal(side: str = "BUY", price: float = 100.0) -> TradevisorSignal:
    return TradevisorSignal(
        dedup_id=f"dedup-{side.lower()}-1",
        signal=side,
        instrument="BTC/USD",
        market="BTCUSD",
        source_exchange="COINBASE",
        source_symbol="BTCUSD",
        timeframe_minutes=60,
        bar_opened_at=NOW - timedelta(minutes=65),
        bar_closed_at=NOW - timedelta(minutes=5),
        triggered_at=NOW - timedelta(seconds=5),
        received_at=NOW,
        signal_price=price,
        alert_config_version=f"tradevisor-1h-{side.lower()}",
    )


APP_ROOT = Path(__file__).resolve().parents[1]


def market(**over) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=95.0,
        ema200=90.0,
        rsi=58.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
        recent_24h_high=112.0,
        recent_24h_low=94.0,
        recent_72h_high=120.0,
        recent_72h_low=88.0,
        momentum_6h_pct=1.0,
        momentum_24h_pct=3.0,
        momentum_72h_pct=7.0,
        rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0,
        rolling_24h_downside_median_pct=8.0,
        rolling_24h_downside_p75_pct=10.0,
        rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0,
        rolling_72h_downside_p75_pct=16.0,
        rolling_72h_downside_p90_pct=20.0,
        ticker_last=100.0,
        execution_validation=ExecutionValidation(
            status="VALID",
            book_coverage_status="COMPLETE",
            warnings=[],
            best_bid=99.95,
            best_ask=100.05,
            mid_price=100.0,
            spread_pct=0.1,
            spread_bps=5.0,
            validation_notional_usd=250.0,
            buy_vwap=100.02,
            sell_vwap=99.98,
            buy_fully_covered=True,
            sell_fully_covered=True,
            estimated_visible_round_trip_market_drag_pct=0.10,
            recent_trade_status="FRESH",
            latest_trade_price=100.0,
            latest_trade_age_seconds=10.0,
            recent_trade_count=20,
        ),
    )
    values.update(over)
    return MarketSnapshot(**values)


# ---------------------------------------------------------------------------
# AC-001 — the integration is inert by default
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_001_default_installation_exposes_nothing(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-001: default-off posture."""
    defaulted = Settings(_env_file=None, webhook_secret="x" * 12)
    assert defaulted.opip_tradevisor_enabled is False

    # A committed environment that switched it on would defeat the default.
    for name in ("docker-compose.yml", "docker-compose.paper.yml"):
        text = (APP_ROOT / name).read_text(encoding="utf-8")
        assert "OPIP_TRADEVISOR_ENABLED" not in text, name

    disabled = make_settings(opip_tradevisor_enabled=False)
    monkeypatch.setattr(routes, "get_settings", lambda: disabled)
    routes.reset_rate_limiter_for_tests()
    response = client().post(
        "/webhooks/tradevisor/v1", json=payload(), headers=HEADERS
    )
    assert response.status_code == 404
    assert rows() == []


# ---------------------------------------------------------------------------
# AC-002 — an authenticated, well-formed alert is durably accepted
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_002_authenticated_buy_and_sell_are_durably_accepted(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-002: durable acceptance."""
    enable(monkeypatch)
    http = client()

    buy = http.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS)
    assert buy.status_code == 202
    assert buy.json()["status"] == "accepted"
    assert buy.json()["trade_authority_changed"] is False

    sell = http.post(
        "/webhooks/tradevisor/v1",
        json=payload(signal="SELL", alert_config_version="tradevisor-1h-sell"),
        headers=HEADERS,
    )
    assert sell.status_code == 202
    assert sell.json()["status"] == "accepted"

    stored = rows()
    assert len(stored) == 2
    # Acceptance means durably recorded, nothing more.
    assert {row["signal"]["signal"] for row in stored} == {"BUY", "SELL"}
    assert all(row["status"] != "EVALUATED" for row in stored)


# ---------------------------------------------------------------------------
# AC-003 — source authentication precedes every schema detail
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_003_unauthenticated_alerts_learn_nothing(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-003: authentication refusals."""
    enable(monkeypatch)
    http = client()

    wrong_stamp = http.post(
        "/webhooks/tradevisor/v1",
        json=payload(),
        headers={routes.TRADEVISOR_VERIFICATION_HEADER: "wrong"},
    )
    assert wrong_stamp.status_code == 404

    bad_credential = http.post(
        "/webhooks/tradevisor/v1",
        json=payload(ingest_token="short"),
        headers=HEADERS,
    )
    assert bad_credential.status_code == 404

    # A missing credential is refused identically: no schema oracle.
    incomplete = payload()
    incomplete.pop("ingest_token")
    assert http.post(
        "/webhooks/tradevisor/v1", json=incomplete, headers=HEADERS
    ).status_code == 404

    assert rows() == []

    accepted = http.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS)
    assert accepted.status_code == 202
    persisted = repr(rows())
    assert TOKEN not in persisted
    assert "ingest_token" not in persisted



# ---------------------------------------------------------------------------
# AC-004 — malformed or out-of-contract payloads are refused
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_004_malformed_and_oversized_payloads_are_refused(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-004: schema and size refusals."""
    enable(monkeypatch)
    http = client()

    unknown_schema = http.post(
        "/webhooks/tradevisor/v1",
        json=payload(schema_version="tradevisor-ingress-v999"),
        headers=HEADERS,
    )
    assert unknown_schema.status_code == 422

    negative_price = http.post(
        "/webhooks/tradevisor/v1", json=payload(signal_price=-1.0), headers=HEADERS
    )
    assert negative_price.status_code == 422

    unknown_pair = http.post(
        "/webhooks/tradevisor/v1", json=payload(symbol="NOSUCHUSD"), headers=HEADERS
    )
    assert unknown_pair.status_code == 422

    oversized = http.post(
        "/webhooks/tradevisor/v1",
        content=b"{" + b"a" * 20000 + b"}",
        headers={**HEADERS, "content-type": "application/json"},
    )
    assert oversized.status_code == 413

    assert rows() == []


# ---------------------------------------------------------------------------
# AC-005 — timing evidence must describe a closed, fresh bar
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_005_unfinished_stale_and_future_events_are_refused(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-005: bar-close and freshness gates."""
    enable(monkeypatch)
    http = client()

    # {{time}} is the bar OPEN, so a trigger inside the bar is an unfinished bar.
    unfinished_open = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    unfinished = http.post(
        "/webhooks/tradevisor/v1",
        # Trigger at the bar open, not at wall clock: inside the last
        # close-tolerance second of an hour a wall-clock trigger is legitimately
        # treated as an already-closed bar (pre-existing flake, made deterministic).
        json=payload(
            bar_opened_at=_z(unfinished_open),
            triggered_at=_z(unfinished_open),
        ),
        headers=HEADERS,
    )
    assert unfinished.status_code == 422

    stale_open = datetime.now(timezone.utc) - timedelta(hours=4)
    stale = http.post(
        "/webhooks/tradevisor/v1",
        json=payload(
            bar_opened_at=_z(stale_open),
            triggered_at=_z(stale_open + timedelta(hours=1)),
        ),
        headers=HEADERS,
    )
    assert stale.status_code == 422

    future_open = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ) - timedelta(hours=2)
    future = http.post(
        "/webhooks/tradevisor/v1",
        json=payload(
            bar_opened_at=_z(future_open),
            triggered_at=_z(datetime.now(timezone.utc) + timedelta(minutes=30)),
        ),
        headers=HEADERS,
    )
    assert future.status_code == 422

    assert rows() == []


# ---------------------------------------------------------------------------
# AC-006 — replay and duplicate delivery collapse to one durable record
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_006_duplicate_delivery_creates_exactly_one_record(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-006: replay collapse."""
    enable(monkeypatch)
    http = client()
    body = payload()

    first = http.post("/webhooks/tradevisor/v1", json=body, headers=HEADERS)
    second = http.post("/webhooks/tradevisor/v1", json=body, headers=HEADERS)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert first.json()["dedup_id"] == second.json()["dedup_id"]
    assert len(rows()) == 1


# ---------------------------------------------------------------------------
# AC-007 — unproven evidence abstains and never produces a plan
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_007_missing_evidence_abstains_without_a_plan():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-007: deterministic abstention."""
    settings = make_settings()

    no_native = evaluation.evaluate_signal(
        signal(),
        now=NOW,
        settings=settings,
        snapshot_provider=lambda _market: ("skip", None, "insufficient history"),
        account_equity=100_000.0,
    )
    assert no_native.decision == evaluation.DECISION_NO_TRADE
    assert no_native.rejection_code == "MARKET_DATA_UNAVAILABLE"
    assert no_native.actionable is False
    assert no_native.plan is None

    # Executable spread/depth is likewise mandatory for an actionable plan.
    no_execution = evaluation.evaluate_signal(
        signal(),
        now=NOW,
        settings=settings,
        snapshot_provider=lambda _market: (
            "ok",
            market(execution_validation=None),
            None,
        ),
        execution_provider=lambda *a, **k: None,
        account_equity=100_000.0,
    )
    assert no_execution.decision == evaluation.DECISION_WATCH
    assert no_execution.rejection_code == "EXECUTION_EVIDENCE_UNAVAILABLE"
    assert no_execution.actionable is False
    assert no_execution.plan is None



# ---------------------------------------------------------------------------
# AC-008 — a SELL alert grants no short and closes no position
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_008_sell_is_evidence_only():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-008: SELL has no short authority."""
    decision = evaluation.evaluate_signal(
        signal("SELL"),
        now=NOW,
        settings=make_settings(),
        snapshot_provider=lambda _market: ("skip", None, "insufficient history"),
        account_equity=100_000.0,
    )
    assert decision.decision == evaluation.DECISION_WATCH
    assert decision.rejection_code == "SELL_EVIDENCE_ONLY_NO_NATIVE_DATA"
    assert decision.actionable is False
    assert decision.plan is None
    semantics = decision.evidence["sell_semantics"]
    assert semantics["short_order"] == "NOT_PERMITTED"
    assert semantics["auto_close_funded_position"] == "NOT_PERFORMED"


# ---------------------------------------------------------------------------
# AC-009 — the source cannot assert its own authority
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_009_source_cannot_assert_authority(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-009: no authority escalation."""
    enable(monkeypatch)
    http = client()

    for field, value in (
        ("risk_dollars", 500.0),
        ("position_size", 10.0),
        ("order_type", "MARKET"),
        ("trade_authority", True),
    ):
        response = http.post(
            "/webhooks/tradevisor/v1", json=payload(**{field: value}), headers=HEADERS
        )
        assert response.status_code == 422, field

    assert rows() == []

    # An evaluated decision still records that it changed no authority.
    decision = evaluation.evaluate_signal(
        signal(),
        now=NOW,
        settings=make_settings(),
        snapshot_provider=lambda _market: ("skip", None, "insufficient history"),
        account_equity=100_000.0,
    )
    assert decision.evidence["trade_authority_changed"] is False
    assert decision.evidence["native_admission"] == "NOT_PERFORMED"


# ---------------------------------------------------------------------------
# AC-010 — a poison payload reaches a terminal, visible disposition
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_010_poison_payload_reaches_a_terminal_disposition():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-010: terminal poison disposition."""

    def explode(_signal):
        raise RuntimeError("boom")

    inbox.enqueue(signal(), now=NOW)
    for attempt in range(3):
        inbox.process_queued_events(
            evaluator=explode, now=NOW + timedelta(minutes=attempt)
        )

    stored = next(iter(inbox.load_json(inbox.INBOX_FILE)["events"].values()))
    assert stored["status"] == inbox.STATUS_POISONED
    assert stored["final_disposition"] == "POISONED"
    # Failures record the exception type only, never message text or a secret.
    assert "boom" not in str(stored.get("failure_reason"))



# ---------------------------------------------------------------------------
# AC-011 — a frozen plan stops being deliverable when it expires
# ---------------------------------------------------------------------------


def _frozen_row() -> dict:
    """Persist one row whose actionable plan was frozen but never delivered."""
    resolved = signal()
    row, _ = inbox.enqueue(resolved, now=NOW)
    inbox.process_queued_events(
        evaluator=lambda _s: {
            "decision": "ENTER_NOW",
            "reason": "qualified",
            "record": {
                "schema_version": "tradevisor-decision-v1",
                "decision": "ENTER_NOW",
                "dedup_id": resolved.dedup_id,
                "market": "BTCUSD",
                "instrument": "BTC/USD",
                "evaluated_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(minutes=90)).isoformat(),
                "plan": {
                    "entry_low": 99.0,
                    "entry_high": 100.0,
                    "stop_price": 96.5,
                    "target_1": 105.5,
                    "target_2": 108.5,
                },
                "allocation": {"recommended_capital": 1000.0, "risk_dollars": 35.0},
                "economics": {
                    "recommended_capital": 2000.0,
                    "target_1_net_profit": 100.0,
                    "target_2_net_profit": 160.0,
                },
            },
            "notification": {"status": "SEND_FAILED", "delivered": False},
        },
        now=NOW,
    )
    return row


class _NeverCalled:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("an expired plan must never be delivered")


@pytest.mark.acceptance
def test_ac_011_expired_plan_is_never_delivered():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-011: delivery expiry."""
    _frozen_row()
    assert len(inbox.undelivered_notification_rows(limit=5, now=NOW)) == 1

    # Past the frozen expiry the plan is terminally undeliverable: it is no
    # longer a retry candidate, so no stale actionable advice can be sent.
    expired_at = NOW + timedelta(hours=4)
    assert inbox.undelivered_notification_rows(limit=5, now=expired_at) == []

    sender = _NeverCalled()
    summary = pipeline.retry_undelivered_notifications(
        settings=make_settings(), now=expired_at, sender=sender
    )
    assert summary["candidates"] == 0
    assert summary["delivered"] == 0
    assert sender.calls == []



# ---------------------------------------------------------------------------
# AC-012 — learning evidence is measured, never promoted, and fails closed
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_012_learning_fails_closed_on_release_drift():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-012: exact-SHA learning gate."""
    drifted = learning.build_learning_report(
        [], deployed_sha=SHA_A, expected_sha=SHA_B
    )
    assert drifted.status == learning.RELEASE_DRIFT
    assert drifted.release_compatibility == learning.RELEASE_DRIFT
    assert drifted.cohorts == {}
    as_dict = drifted.as_dict()
    assert as_dict["policy_promotion"] == "NONE"
    assert as_dict["trade_authority_changed"] is False

    unverified = learning.build_learning_report(
        [], deployed_sha="", expected_sha=SHA_A
    )
    assert unverified.status == learning.RELEASE_UNVERIFIED

    # The re-exported classifier is the learning plane's own function object.
    from app.opip.learning import job_disposition

    assert learning.classify_release_compatibility is (
        job_disposition.classify_release_compatibility
    )
    assert learning.RELEASE_DRIFT == job_disposition.RELEASE_DRIFT


# ---------------------------------------------------------------------------
# AC-013 — the advisory phase cannot interfere with protection
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_013_advisory_phase_is_last_inert_and_contained(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-013: phase independence (structural)."""
    from types import SimpleNamespace

    from app.jobs import run_cycle

    source = (APP_ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    protection = source.index('_cycle_phase("ACTIVE_POSITION_PROTECTION")')
    advisory = source.index('_cycle_phase("TRADEVISOR_ADVISORY")')
    assert protection < advisory, "advisory phase must follow protection"
    assert source.index("_run_tradevisor_fail_open()", advisory) > advisory

    def fail(**_kwargs):
        raise AssertionError("must not run while the ingress is dark")

    # Inert while disabled.
    monkeypatch.setattr(
        run_cycle,
        "get_settings",
        lambda: SimpleNamespace(opip_tradevisor_enabled=False),
    )
    monkeypatch.setattr(inbox, "process_queued_events", fail)
    run_cycle._run_tradevisor_fail_open()

    # A failure is contained: it never aborts the cycle.
    monkeypatch.setattr(
        run_cycle,
        "get_settings",
        lambda: SimpleNamespace(opip_tradevisor_enabled=True),
    )
    monkeypatch.setattr(inbox, "pending_count", lambda: 1)

    def explode(**_kwargs):
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(inbox, "process_queued_events", explode)
    run_cycle._run_tradevisor_fail_open()


# ---------------------------------------------------------------------------
# AC-014 — advisory evidence is classified, consumed and never canonical
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_014_evidence_is_noncanonical_and_has_a_consumer():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-014: noncanonical disposition."""
    status = inbox.inbox_status()
    assert status["evidence_class"] == "ADVISORY_NON_CANONICAL"
    # ATDD-38 deviation: no canonical handoff is implemented, and the store
    # says so rather than implying a handoff exists.
    assert status["canonical_handoff"] == "NOT_PERFORMED"
    assert status["consumption"] == "app.jobs.report_tradevisor_learning"
    assert status["authority"]["trading_authority_changed"] is False
    assert status["authority"]["admission_authority"] is False
    assert status["authority"]["execution_authority"] is False

    # Missing native evidence is still recorded without invented 4H or F6.
    decision = evaluation.evaluate_signal(
        signal(),
        now=NOW,
        settings=make_settings(),
        snapshot_provider=lambda _market: ("skip", None, "insufficient history"),
        account_equity=100_000.0,
    )
    assert decision.evidence["f6_forecast_status"] == "UNAVAILABLE_NO_TRUSTED_MODEL"
    assert decision.evidence["regime_4h"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# AC-015 — owner-approved F6/4H qualification policy, shadow-only learning
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_015_untrusted_f6_and_missing_swing_4h_never_send_actionable_alert():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-015: fail-closed qualification."""
    from app.services import tradevisor_notifier as notifier

    result = evaluation.evaluate_signal(
        signal(),
        now=NOW,
        settings=make_settings(),
        snapshot_provider=lambda _market: ("ok", market(), None),
        account_equity=10_000.0,
        active_trades=[],
    )
    assert result.horizon == evaluation.HORIZON_SWING
    assert result.decision == evaluation.DECISION_WATCH
    assert result.rejection_code == "ADVISORY_EVIDENCE_INCOMPLETE"
    assert result.actionable is False
    assert result.evidence["shadow_proposed_action"] == "ENTER_NOW"
    assert result.evidence["advisory_qualification"]["missing"] == [
        "F6_TRUSTED_FORECAST", "SWING_4H_CLOSED_CONTEXT"
    ]
    assert result.plan is not None  # frozen hypothetical; no execution authority
    assert result.expires_at is None
    assert result.as_record()["execution_authority"] is False

    sends: list[str] = []
    delivery = notifier.notify_tradevisor_decision(
        result,
        settings=make_settings(),
        now=NOW,
        sender=lambda message: sends.append(message),
    )
    assert delivery.status == notifier.STATUS_NOT_ACTIONABLE
    assert sends == []

    # A nonactionable WATCH still contributes a measured *hypothesis* when
    # forward candles become available; no bar before the decision is read.
    frozen_row = {
        "dedup_id": result.dedup_id,
        "decision": result.decision,
        "decision_record": result.as_record(),
    }
    # Candle timestamps must sit on the source's UTC grid, and the window must
    # be fully covered before a definitive measurement is allowed (N-02).
    grid = NOW.replace(minute=0, second=0, microsecond=0)
    outcome = learning.evaluate_outcome(
        frozen_row,
        now=NOW + timedelta(days=2),
        candles_provider=lambda *_: [
            SimpleNamespace(
                open_time=grid + timedelta(hours=1),
                low=result.plan.entry_low,
                high=result.plan.target_1,
                # A measured bar must carry a coherent OHLC set
                # (ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1).
                open=result.plan.entry_low,
                close=result.plan.entry_low,
            )
        ],
        measurement_hours=2,
    )
    assert outcome["status"] == learning.OUTCOME_MEASURED
    assert outcome["measurement_subject"] == "SHADOW_HYPOTHESIS"
    assert outcome["fill_attribution"] == learning.FILL_ATTRIBUTION_NONE

