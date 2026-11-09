"""Focused regressions for the six release-blocking adapter/notification defects.

Increment: ``ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001``.

Each test pins one defect found in architectural review of the actionable-only gate:

1. market 24h liquidity and the position-capacity ceiling are different facts with different tests;
2. no synthetic freshness or venue validation inside the gate;
3. an executable limit entry is distinct from an unconfirmed future setup;
4. a legacy outbox row without an admitted verdict fails closed instead of being delivered blind;
5. every emitted actionable card states entry, stop, targets, invalidation, expiry and *net* R/R;
6. execution depth and the cost model are direction-specific and counted once.

No production endpoint is contacted; transports and registries are stubbed.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.scanner.execution_validation import ExecutionValidation
from app.services import actionable_admission as admission
from app.services import actionable_card
from app.services import capital_efficiency_ranking
from app.services import chief_alert_notifier as chief
from app.services import qualified_alert_outbox as outbox
from app.services import tradevisor_notifier
from app.services.entry_exit_advisor import EntryExitPlan

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

INCREMENT = "ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001"
APP = APP_ROOT / "app"

pytestmark = pytest.mark.acceptance
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

BASE = {
    "instrument": "SOLUSD",
    "venue": "KRAKEN",
    "venue_identity_valid": True,
    "observed_at": NOW - timedelta(minutes=2),
    "expires_at": NOW + timedelta(hours=8),
    "direction": "LONG",
    "entry_style": "enter_now",
    "valid_now": True,
    "current_price": 100.0,
    "entry_price": 100.0,
    "entry_low": 99.5,
    "entry_high": 100.5,
    "chase_limit": 101.0,
    "stop_price": 97.0,
    "target_1": 108.0,
    "target_2": 112.0,
    "invalidation": "close below 97.00",
    "market_liquidity_usd": 2_400_000.0,
    "position_capacity_usd": 5_000.0,
    "order_size_usd": 750.0,
    "spread_bps": 8.0,
    "size_slippage_pct": 0.25,
    "depth_covered": True,
    "economics_available": True,
    "deterministic_qualified": True,
    "risk_gate_passed": True,
}


def _ev(**overrides: object) -> admission.ActionableEvidence:
    return admission.ActionableEvidence(**{**BASE, **overrides})


def _plan(**overrides: object) -> EntryExitPlan:
    payload = {
        "symbol": "SOLUSD",
        "valid_now": True,
        "entry_style": "enter_now",
        "entry_low": 99.5,
        "entry_high": 100.5,
        "chase_limit": 101.0,
        "stop_price": 97.0,
        "target_1": 108.0,
        "target_2": 112.0,
        "reward_to_risk_1": 2.6,
        "reward_to_risk_2": 3.6,
        "risk_level": "MEDIUM",
        "reason": "trend continuation",
        "direction": "LONG",
    }
    payload.update(overrides)
    return EntryExitPlan(**payload)  # type: ignore[arg-type]


def _called_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


# --------------------------------------------------------------------------- #
# 1. Market liquidity vs position-capacity ceiling
# --------------------------------------------------------------------------- #


def test_a_deep_market_with_a_small_capacity_ceiling_is_refused_by_capacity_only():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    

    A deep market cannot excuse an order that is too large for the visible book.

    GIVEN 24h turnover far above the market floor and a capacity ceiling below the order
    WHEN the gate runs
    THEN it refuses with the capacity reason, not the liquidity reason
    """

    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(market_liquidity_usd=50_000_000.0, position_capacity_usd=300.0), now=NOW
    )
    assert verdict.admitted is False
    assert verdict.reason == admission.REASON_CAPACITY_INSUFFICIENT


def test_a_large_capacity_ceiling_cannot_excuse_a_thin_market():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    A large book in a market nobody trades is still a thin market.

    GIVEN a capacity ceiling far above the order and 24h turnover below the market floor
    WHEN the gate runs
    THEN it refuses with the market-liquidity reason
    """

    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(market_liquidity_usd=50_000.0, position_capacity_usd=5_000_000.0), now=NOW
    )
    assert verdict.admitted is False
    assert verdict.reason == admission.REASON_LIQUIDITY_INSUFFICIENT


def test_capacity_is_compared_with_the_order_size_and_the_configured_utilization():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    The capacity test is order-size feasibility, bounded by configured utilization.

    GIVEN capacity equal to the order, and then capacity only twice the order
    WHEN utilization allows 100% and then only 50%
    THEN the order passes the first and is refused by the second
    """

    full = admission.ActionableNotificationPolicy()
    assert full.max_capacity_utilization_pct == 100.0
    assert admission.evaluate_actionable_admission(
        evidence=_ev(position_capacity_usd=750.0), now=NOW
    ).admitted

    half = admission.ActionableNotificationPolicy(max_capacity_utilization_pct=50.0)
    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(position_capacity_usd=1000.0), policy=half, now=NOW
    )
    assert verdict.reason == admission.REASON_CAPACITY_INSUFFICIENT


def test_the_gate_no_longer_exposes_an_aliased_liquidity_field():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    The aliasing that let a capacity ceiling satisfy a market threshold is gone.

    GIVEN the evidence contract and the capacity owner
    WHEN they are inspected
    THEN there is no single ``liquidity_usd`` field, and the ceiling formula has one owner that
    takes the smaller of the turnover and book-depth limits
    """

    fields = set(admission.ActionableEvidence.__dataclass_fields__)
    assert "liquidity_usd" not in fields
    assert {"market_liquidity_usd", "position_capacity_usd"} <= fields

    ceiling = capital_efficiency_ranking.position_capacity_ceiling_usd
    assert ceiling(
        liquidity_24h_usd=1_000_000.0, bid_depth_050_usd=40_000.0, ask_depth_050_usd=40_000.0
    ) == pytest.approx(1000.0)  # 10 bps of turnover is the binding limit
    assert ceiling(
        liquidity_24h_usd=50_000_000.0, bid_depth_050_usd=4_000.0, ask_depth_050_usd=9_000.0
    ) == pytest.approx(400.0)  # the smaller depth side is binding
    assert (
        ceiling(liquidity_24h_usd=None, bid_depth_050_usd=None, ask_depth_050_usd=None) is None
    )
    assert "position_capacity_ceiling_usd" in _called_names(
        APP / "services" / "capital_efficiency_ranking.py"
    )


# --------------------------------------------------------------------------- #
# 2. No synthetic freshness, no synthetic venue validation
# --------------------------------------------------------------------------- #


def test_absent_evidence_timestamp_fails_closed_instead_of_using_the_clock():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    The gate must not treat "no timestamp" as "fresh right now".

    GIVEN a candidate whose producer supplied no evidence timestamp
    WHEN the adapter maps it and the gate evaluates it
    THEN the timestamp stays absent and the notification is refused
    """

    candidate = {"symbol": "SOLUSD", "venue": "KRAKEN", "venue_identity_valid": True}
    evidence = admission.evidence_from_trade_plan(candidate, _plan())
    assert evidence.observed_at is None
    verdict = admission.evaluate_actionable_admission(evidence=evidence, now=NOW)
    assert verdict.admitted is False
    assert verdict.reason == admission.REASON_EVIDENCE_TIMESTAMP_MISSING


def test_venue_identity_is_never_synthesised_by_the_adapter():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    A pair symbol alone is not proof of venue identity.

    GIVEN candidates that omit the venue, or supply a venue without its upstream verdict
    WHEN the adapter maps them
    THEN the gate refuses both, and no default venue appears
    """

    without_venue = admission.evidence_from_trade_plan({"symbol": "SOLUSD"}, _plan())
    assert without_venue.venue is None
    assert without_venue.venue_identity_valid is None
    assert (
        admission.evaluate_actionable_admission(evidence=without_venue, now=NOW).reason
        == admission.REASON_VENUE_IDENTITY_INVALID
    )

    unverified = admission.evidence_from_trade_plan(
        {"symbol": "SOLUSD", "venue": "KRAKEN"}, _plan()
    )
    assert unverified.venue == "KRAKEN"
    assert unverified.venue_identity_valid is None
    assert (
        admission.evaluate_actionable_admission(evidence=unverified, now=NOW).reason
        == admission.REASON_VENUE_IDENTITY_INVALID
    )


def test_producers_supply_venue_and_identity_from_the_exchange_that_own_them():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    Both actionable producers read venue identity from an authoritative upstream source.

    GIVEN the native scan and the Tradevisor evaluation
    WHEN their evidence is inspected
    THEN the venue comes from the exchange identity/adapter module, not a literal invented by the
    consumer
    """

    native = (APP / "jobs" / "scan_opportunities.py").read_text(encoding="utf-8")
    assert "VENUE_ID" in native
    assert "split_canonical_pair" in native

    evaluation = (APP / "services" / "tradevisor_evaluation.py").read_text(encoding="utf-8")
    assert "EXECUTION_VENUE" in evaluation
    assert '"venue_identity_valid": split_canonical_pair' in evaluation

    notifier = (APP / "services" / "tradevisor_notifier.py").read_text(encoding="utf-8")
    adapter = notifier.split("def _tradevisor_admission_evidence", 1)[1].split(
        "def notify_tradevisor_decision", 1
    )[0]
    assert '"KRAKEN"' not in adapter


# --------------------------------------------------------------------------- #
# 3. Executable limit entry vs unconfirmed future setup
# --------------------------------------------------------------------------- #


def test_a_placeable_limit_entry_is_admitted_and_classified_as_executable():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    A limit the operator can place now is actionable.

    GIVEN a qualified pullback plan whose quote is still inside the executable window
    WHEN the gate runs
    THEN it is admitted and labelled as an executable limit entry
    """

    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(valid_now=False, entry_style="wait_for_pullback", current_price=100.4),
        now=NOW,
    )
    assert verdict.admitted is True, verdict.reasons
    assert verdict.entry_trigger_kind == admission.ENTRY_TRIGGER_LIMIT


def test_a_limit_beyond_the_chase_boundary_is_an_unconfirmed_future_setup():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    Price has already run away from the plan: there is nothing executable.

    GIVEN a pullback plan whose quote is beyond the do-not-chase boundary
    WHEN the gate runs
    THEN it is refused as too extended, never admitted as an entry
    """

    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(valid_now=False, entry_style="wait_for_pullback", current_price=102.5),
        now=NOW,
    )
    assert verdict.admitted is False
    assert verdict.reason == admission.REASON_TOO_EXTENDED
    assert verdict.entry_trigger_kind == admission.ENTRY_TRIGGER_UNCONFIRMED


def test_an_authoritative_unconfirmed_trigger_refuses_even_with_a_valid_zone():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    An upstream "trigger not confirmed" verdict outranks a well-formed zone.

    GIVEN a plan with a complete zone and an authoritative unconfirmed trigger
    WHEN the gate runs
    THEN it refuses with the no-trigger reason
    """

    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(entry_trigger_confirmed=False), now=NOW
    )
    assert verdict.admitted is False
    assert verdict.reason == admission.REASON_NO_ENTRY_TRIGGER


def test_a_market_entry_outside_its_zone_is_not_confirmed():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    ENTER NOW means the quote is inside the approved zone.

    GIVEN a market-entry plan whose quote sits between the zone and the chase boundary
    WHEN the gate runs
    THEN it is refused as not feasible rather than admitted
    """

    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(current_price=100.8), now=NOW
    )
    assert verdict.admitted is False
    assert verdict.reason == admission.REASON_ENTRY_NOT_FEASIBLE


def test_short_limit_entries_use_the_mirrored_executable_window():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    SHORT entries mirror the LONG rule around the zone and the chase boundary.

    GIVEN a SHORT pullback plan quoted inside its executable window, and then below the chase
    boundary
    WHEN the gate runs
    THEN the first is executable and the second is too extended
    """

    short = {
        "direction": "SHORT",
        "valid_now": False,
        "entry_style": "wait_for_rebound",
        "entry_low": 100.0,
        "entry_high": 101.0,
        "chase_limit": 98.5,
        "stop_price": 104.0,
        "target_1": 92.0,
        "target_2": 88.0,
        "invalidation": "close above 104.00",
    }
    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(current_price=99.5, **short), now=NOW
    )
    assert verdict.admitted is True, verdict.reasons
    assert verdict.entry_trigger_kind == admission.ENTRY_TRIGGER_LIMIT

    too_extended = admission.evaluate_actionable_admission(
        evidence=_ev(current_price=98.0, **short), now=NOW
    )
    assert too_extended.reason == admission.REASON_TOO_EXTENDED


# --------------------------------------------------------------------------- #
# 4. Legacy outbox rows fail closed
# --------------------------------------------------------------------------- #


def _seed_outbox_row(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    admission_payload: dict | None,
    plan_expires_at: str | None,
) -> pathlib.Path:
    from dataclasses import asdict

    state = tmp_path / "outbox.json"
    monkeypatch.setattr(outbox, "OUTBOX_FILE", state)
    monkeypatch.setattr(
        outbox, "get_pending_setup_record", lambda trade_id: {"status": "waiting"}
    )
    # Tracking is a separate concern from notification admission; stub it so the row reaches the
    # admission/expiry revalidation this test is about.
    monkeypatch.setattr(outbox, "register_reconciliation_intent", lambda **kwargs: None)
    row = {
        "schema_version": 1,
        "trade_id": "LEGACY-1",
        "queued_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "reason": "DELIVERY_PENDING",
        "message": "trade_id=LEGACY-1",
        "action": "ENTER_NOW",
        "direction": "LONG",
        "identity": "QUALIFIED_OPPORTUNITY:LEGACY-1",
        "policy_identity": "LONG:SOLUSD",
        "fingerprint": "fp-legacy",
        "symbol": "SOLUSD",
        "plan": asdict(_plan()),
        "tracking_candidate": {"economic_qualified": True, "recommended_capital": 750.0},
        "leverage": 1.0,
        "journey_id": None,
        "signal_id": None,
    }
    if admission_payload is not None:
        row["admission"] = admission_payload
    if plan_expires_at is not None:
        row["plan_expires_at"] = plan_expires_at
    state.write_text(json.dumps({"LEGACY-1": row}), encoding="utf-8")
    return state


def test_legacy_outbox_row_without_an_admitted_verdict_fails_closed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    A pre-policy retry row must not be delivered on trust.

    GIVEN a queued actionable row that carries no admission verdict
    WHEN the retry drain runs
    THEN nothing is sent, the row is retired and the refusal is recorded with its reason
    """

    state = _seed_outbox_row(
        tmp_path, monkeypatch, admission_payload=None, plan_expires_at=None
    )
    suppressed: list[dict] = []
    monkeypatch.setattr(
        outbox, "record_telegram_suppression", lambda **kwargs: suppressed.append(kwargs)
    )

    def _must_not_send(**_kwargs):
        raise AssertionError("a row without an admitted verdict must never be delivered")

    monkeypatch.setattr(outbox, "send_tracked_telegram", _must_not_send)

    delivered, pending = outbox.retry_qualified_alerts(bot_token="token", chat_id="chat")
    assert delivered == 0
    assert pending == 0
    assert json.loads(state.read_text(encoding="utf-8")) == {}
    assert suppressed and suppressed[0]["reason"] == (
        "ACTIONABLE_GATE:MISSING_ADMISSION_EVIDENCE"
    )


def test_outbox_row_with_a_suppressed_verdict_is_not_delivered(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    A row whose stored verdict is not ADMITTED is retired, not delivered.

    GIVEN a queued row whose admission payload records a suppression
    WHEN the retry drain runs
    THEN nothing is sent and the row is retired
    """

    state = _seed_outbox_row(
        tmp_path,
        monkeypatch,
        admission_payload={"admission": "SUPPRESSED", "reason": "NO_ENTRY_TRIGGER"},
        plan_expires_at=(NOW + timedelta(hours=2)).isoformat(),
    )
    suppressed: list[dict] = []
    monkeypatch.setattr(
        outbox, "record_telegram_suppression", lambda **kwargs: suppressed.append(kwargs)
    )

    def _must_not_send(**_kwargs):
        raise AssertionError("a suppressed verdict must never be delivered")

    monkeypatch.setattr(outbox, "send_tracked_telegram", _must_not_send)
    delivered, _pending = outbox.retry_qualified_alerts(bot_token="token", chat_id="chat")
    assert delivered == 0
    assert json.loads(state.read_text(encoding="utf-8")) == {}
    assert suppressed and suppressed[0]["reason"] == (
        "ACTIONABLE_GATE:MISSING_ADMISSION_EVIDENCE"
    )


def test_outbox_row_admitted_then_expired_is_retired_with_the_expiry_reason(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    Send-time revalidation still refuses an admitted-but-expired plan.

    GIVEN an admitted row whose plan expiry has passed
    WHEN the retry drain runs
    THEN nothing is sent and the expiry reason is recorded
    """

    state = _seed_outbox_row(
        tmp_path,
        monkeypatch,
        admission_payload={"admission": "ADMITTED"},
        plan_expires_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    )
    suppressed: list[dict] = []
    monkeypatch.setattr(
        outbox, "record_telegram_suppression", lambda **kwargs: suppressed.append(kwargs)
    )

    def _must_not_send(**_kwargs):
        raise AssertionError("an expired plan must never be delivered late")

    monkeypatch.setattr(outbox, "send_tracked_telegram", _must_not_send)
    delivered, _pending = outbox.retry_qualified_alerts(bot_token="token", chat_id="chat")
    assert delivered == 0
    assert json.loads(state.read_text(encoding="utf-8")) == {}
    assert suppressed and suppressed[0]["reason"] == "ACTIONABLE_GATE:EXPIRY_PASSED"


# --------------------------------------------------------------------------- #
# 5. Every emitted actionable card is complete
# --------------------------------------------------------------------------- #


def _run_send_trade_plan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate: dict | None = None,
) -> dict:
    """Drive the real native producer with only the transport/registry edges stubbed."""
    captured: dict = {}
    monkeypatch.setattr(chief, "get_pending_setup_by_trade_id", lambda trade_id: None)
    monkeypatch.setattr(
        chief, "add_pending_setup", lambda setup: SimpleNamespace(trade_id="TRADE-1")
    )
    monkeypatch.setattr(chief, "record_recommendation", lambda **kwargs: None)
    monkeypatch.setattr(chief, "should_send_trade_plan", lambda *args: True)
    monkeypatch.setattr(chief, "accepted_delivery_message_id", lambda **kwargs: None)
    monkeypatch.setattr(chief, "_register_reconciliation_intent", lambda **kwargs: None)
    monkeypatch.setattr(chief, "queued_alert_fingerprint", lambda trade_id: None)
    monkeypatch.setattr(chief, "reserve_emit", lambda **kwargs: "reservation-token")
    monkeypatch.setattr(chief, "confirm_emit", lambda **kwargs: True)
    monkeypatch.setattr(chief, "release_emit", lambda **kwargs: True)
    monkeypatch.setattr(
        chief,
        "record_telegram_suppression",
        lambda **kwargs: captured.setdefault("suppression", kwargs),
    )
    monkeypatch.setattr(chief, "queue_qualified_alert", lambda **kwargs: None)

    def _send(**kwargs):
        captured["message"] = kwargs["message"]
        return SimpleNamespace(delivered=True, message_id=7)

    monkeypatch.setattr(chief, "send_tracked_telegram", _send)
    payload = candidate if candidate is not None else {
        "symbol": "SOLUSD",
        "direction": "LONG",
        "trade_id": "TRADE-1",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "economic_position_notional": 750.0,
        "evidence_observed_at": (NOW - timedelta(minutes=2)).isoformat(),
        "execution_evidence": {
            "venue": "KRAKEN",
            "venue_identity_valid": True,
            "market_liquidity_usd": 2_400_000.0,
            "position_capacity_usd": 5_000.0,
            "spread_bps": 8.0,
            "size_slippage_pct": 0.25,
            "depth_covered": True,
            "current_price": 100.0,
        },
    }
    captured["delivered"] = chief.send_trade_plan(
        candidate=payload,
        plan=_plan(),
        summary="summary",
        bot_token="token",
        chat_id="chat",
        observed_at=NOW,
    )
    captured["candidate"] = payload
    captured["plan"] = _plan()
    return captured


def test_the_emitted_actionable_card_states_every_mandatory_plan_element(
    monkeypatch: pytest.MonkeyPatch,
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    The delivered card is the contract: a complete plan or nothing.

    GIVEN a fully admitted BUY
    WHEN the card is rendered and sent
    THEN it contains the entry zone, stop-loss, targets, invalidation, expiry, the setup identity,
    the manual-execution statement and the *net* reward-to-risk the gate admitted
    """

    captured = _run_send_trade_plan(monkeypatch)
    assert captured["delivered"] is True
    message = captured["message"]
    assert actionable_card.missing_card_elements(message) == ()
    assert "net" not in message.split("Net R/R")[0].lower().split("target 1")[-1]

    verdict = admission.evaluate_actionable_admission(
        evidence=admission.evidence_from_trade_plan(
            captured["candidate"],
            captured["plan"],
            execution=captured["candidate"]["execution_evidence"],
            observed_at=NOW,
        ),
        now=NOW,
    )
    assert verdict.admitted is True
    assert f"Net R/R after {verdict.effective_cost_pct:.2f}%" in message
    assert f"{verdict.net_reward_to_risk:.2f}:1" in message
    assert "Stop-loss: 97" in message
    assert "Invalidation: close below 97" in message
    assert "Setup ID: QUALIFIED_OPPORTUNITY:TRADE-1" in message
    assert "KRAKEN" in message and "SOLUSD" in message


def test_an_incomplete_card_is_refused_rather_than_delivered(
    monkeypatch: pytest.MonkeyPatch,
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    A card that loses a mandatory element cannot ship.

    GIVEN a renderer that fails to emit the plan block
    WHEN the producer tries to send the card
    THEN nothing is delivered and the incompleteness is recorded with the missing labels
    """

    monkeypatch.setattr(chief, "render_actionable_block", lambda **kwargs: "")
    captured = _run_send_trade_plan(monkeypatch)
    assert captured["delivered"] is False
    assert "message" not in captured
    reason = captured["suppression"]["reason"]
    assert reason.startswith("ACTIONABLE_CARD_INCOMPLETE:")
    assert "Net R/R" in reason


def test_the_tradevisor_delivery_appends_the_same_mandatory_block(
    monkeypatch: pytest.MonkeyPatch,
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    The Tradevisor plane delivers the same contract, not only the native path.

    GIVEN a Tradevisor actionable decision whose evidence is admissible
    WHEN the notifier delivers it
    THEN the delivered message carries the shared block with the setup identity, invalidation,
    expiry, net R/R and the manual-execution statement
    """

    from tests import test_tradevisor_notifier_v1 as tv

    captured: dict = {}
    decision = tv.decision()
    monkeypatch.setattr(tradevisor_notifier, "should_emit", lambda **kwargs: True)
    monkeypatch.setattr(tradevisor_notifier, "reserve_emit", lambda **kwargs: "reservation")
    monkeypatch.setattr(tradevisor_notifier, "confirm_emit", lambda **kwargs: True)
    monkeypatch.setattr(tradevisor_notifier, "release_emit", lambda **kwargs: True)
    monkeypatch.setattr(
        tradevisor_notifier, "native_alert_recently_emitted", lambda *a, **k: False
    )

    def _sender(**kwargs):
        captured["message"] = kwargs["message"]
        return SimpleNamespace(delivered=True, message_id=11)

    outcome = tradevisor_notifier.notify_tradevisor_decision(
        decision,
        settings=SimpleNamespace(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        ),
        now=decision.evaluated_at,
        sender=_sender,
    )
    assert outcome.status == tradevisor_notifier.STATUS_DELIVERED
    message = captured["message"]
    assert actionable_card.missing_card_elements(message) == ()
    assert "Setup ID:" in message
    assert "MANUAL ONLY" in message


# --------------------------------------------------------------------------- #
# 6. Direction-specific depth and a single, consistent cost model
# --------------------------------------------------------------------------- #


def _snapshot(**overrides: object) -> SimpleNamespace:
    execution = ExecutionValidation(
        status="VALID",
        book_coverage_status="PARTIAL",
        warnings=[],
        spread_bps=8.0,
        spread_pct=0.08,
        mid_price=100.0,
        validation_notional_usd=250.0,
        buy_fully_covered=True,
        sell_fully_covered=False,
        estimated_visible_round_trip_market_drag_pct=0.10,
        estimated_visible_short_round_trip_market_drag_pct=0.60,
    )
    values = {
        "last_price": 100.0,
        "combined_24h_liquidity_usd": 2_400_000.0,
        "execution_validation": execution,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_long_and_short_read_their_own_depth_leg_and_measured_impact():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    A LONG buys into the asks; a SHORT sells into the bids.

    GIVEN a book whose ask side covers the order and whose bid side does not
    WHEN execution evidence is extracted for each direction
    THEN the LONG sees covered depth with the long impact and the SHORT sees uncovered depth with
    the short impact
    """

    long_evidence = admission.execution_evidence_from_snapshot(
        _snapshot(), direction="LONG", order_notional_usd=750.0
    )
    assert long_evidence["depth_covered"] is True
    assert long_evidence["size_slippage_pct"] == pytest.approx(0.10)

    short_evidence = admission.execution_evidence_from_snapshot(
        _snapshot(), direction="SHORT", order_notional_usd=750.0
    )
    assert short_evidence["depth_covered"] is False
    assert short_evidence["size_slippage_pct"] == pytest.approx(0.60)


def test_a_short_plan_is_refused_when_only_the_ask_side_is_covered():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    Direction-specific depth is enforced, not averaged away.

    GIVEN the same book and a SHORT plan whose expected value depends on the bid side
    WHEN the gate runs
    THEN it refuses for insufficient depth
    """

    evidence = admission.execution_evidence_from_snapshot(
        _snapshot(), direction="SHORT", order_notional_usd=750.0
    )
    # The producer adds the authoritative venue identity to the execution evidence.
    evidence["venue"] = "KRAKEN"
    evidence["venue_identity_valid"] = True
    candidate = {
        "symbol": "SOLUSD",
        "direction": "SHORT",
        "venue": evidence.get("venue"),
        "venue_identity_valid": evidence.get("venue_identity_valid"),
        "current_price": evidence["current_price"],
        "market_liquidity_usd": evidence["market_liquidity_usd"],
        "position_capacity_usd": 5_000.0,
        "economic_position_notional": 750.0,
        "economic_qualified": True,
        "action_gate_allowed": True,
        "evidence_observed_at": (NOW - timedelta(minutes=2)).isoformat(),
    }
    admitted = admission.evidence_from_trade_plan(
        candidate,
        _plan(
            direction="SHORT",
            entry_low=100.0,
            entry_high=101.0,
            chase_limit=98.5,
            stop_price=104.0,
            target_1=92.0,
            target_2=88.0,
            reason="close above 104.00",
        ),
        execution=evidence,
        observed_at=NOW,
    )
    verdict = admission.evaluate_actionable_admission(evidence=admitted, now=NOW)
    assert verdict.admitted is False
    assert verdict.reason == admission.REASON_DEPTH_INSUFFICIENT


def test_the_cost_model_counts_fees_and_measured_impact_exactly_once():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    One cost number drives both the verdict and the card.

    GIVEN assumed round-trip fees and a measured directional impact
    WHEN the gate evaluates economics
    THEN the applied cost is their sum, the net R:R uses exactly that number, and the rendered
    card states the same figures
    """

    verdict = admission.evaluate_actionable_admission(evidence=_ev(), now=NOW)
    assert verdict.admitted is True
    expected_cost = 0.52 + 0.25
    assert verdict.effective_cost_pct == pytest.approx(expected_cost)
    expected_rr = admission.cost_adjusted_reward_to_risk(
        entry=100.0, stop=97.0, target=108.0, round_trip_cost_pct=expected_cost
    )
    assert verdict.net_reward_to_risk == pytest.approx(expected_rr)

    block = actionable_card.render_actionable_block(
        admission=verdict,
        plan=_plan(),
        setup_id="QUALIFIED_OPPORTUNITY:TRADE-1",
        direction="LONG",
        venue="KRAKEN",
        instrument="SOLUSD",
        observed_at=NOW,
    )
    assert f"{expected_cost:.2f}%" in block
    assert f"{expected_rr:.2f}:1" in block


def test_an_absent_measured_impact_makes_the_net_economics_uncomputable():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-013.

    The gate never substitutes an assumed impact for a missing measurement.

    GIVEN evidence with no measured directional impact
    WHEN the cost model is asked for the round-trip cost
    THEN it returns None, so the economics condition cannot silently pass
    """

    policy = admission.ActionableNotificationPolicy()
    assert admission.effective_round_trip_cost_pct(_ev(size_slippage_pct=None), policy) is None
    verdict = admission.evaluate_actionable_admission(
        evidence=_ev(size_slippage_pct=None), now=NOW
    )
    assert verdict.admitted is False
    assert verdict.reason == admission.REASON_EXECUTION_EVIDENCE_MISSING
