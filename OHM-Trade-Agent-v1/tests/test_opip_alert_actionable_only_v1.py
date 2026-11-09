"""OPIP-ALERT-ACTIONABLE-ONLY-001 acceptance tests.

Increment: ``ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001``.

These tests prove the owner's contract at its original meanings: a heuristic score can never make
a card deliverable, an actionable BUY/SELL is delivered only with a complete validated manual
trade plan, repeated observations cannot duplicate, material changes are identity-linked, a
failed send stays retryable without rejecting the trade, protection alerts cannot be throttled,
evidence-only Early Watch keeps capturing, and every actionable producer routes through one gate.

No production Telegram endpoint is contacted and no production credential is used: transports are
monkeypatched to fail on purpose and all state lives under ``tmp_path``.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.jobs import scan_movers
from app.services import actionable_admission as admission
from app.services import attention_budget
from app.services import chief_alert_notifier as chief
from app.services import movement_discovery_v2 as movement
from app.services import notification_policy as policy
from app.services import qualified_alert_outbox as outbox
from app.services import telegram_delivery as delivery
from app.services import tradevisor_notifier
from app.services.entry_exit_advisor import EntryExitPlan

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    REQUIRED_SECTIONS,
    load_scope_contracts,
    parse_scope_contract,
    read_increment_pointer,
)

INCREMENT = "ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001"
CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT = CONTRACT_DIR / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
APP = APP_ROOT / "app"

pytestmark = pytest.mark.acceptance

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

#: A complete, validated, manual LONG trade plan. Each test removes exactly one thing from it.
#: Facts stay distinct: ``market_liquidity_usd`` is 24h turnover, ``position_capacity_usd`` is the
#: largest order the book can absorb, and ``size_slippage_pct`` is the measured impact.
BASE_EVIDENCE = {
    "instrument": "SOLUSD",
    "venue": "KRAKEN",
    "venue_identity_valid": True,
    "observed_at": NOW - timedelta(minutes=2),
    "expires_at": NOW + timedelta(hours=8),
    "episode_id": "SETUP:1",
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


def _evidence(**overrides: object) -> admission.ActionableEvidence:
    return admission.ActionableEvidence(**{**BASE_EVIDENCE, **overrides})


def _entry_exit_plan(**overrides: object) -> EntryExitPlan:
    plan = {
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
        "reason": "trend continuation with volume confirmation",
        "direction": "LONG",
    }
    plan.update(overrides)
    return EntryExitPlan(**plan)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> dict:
    """A qualified candidate carrying only evidence other components produced."""
    candidate = {
        "symbol": "SOLUSD",
        "direction": "LONG",
        "trade_id": "TRADE-1",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "economic_position_notional": 750.0,
        "liquidity_capacity_ceiling_usd": 5_000.0,
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
    candidate.update(overrides)
    return candidate


def _called_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _watch_signal(symbol: str = "SOLUSD", stage: str = "BREAKOUT_CANDIDATE"):
    """A stand-in for one deep-qualified Early Watch candidate the channel would have carded."""
    return SimpleNamespace(
        symbol=symbol,
        stage=stage,
        alert_eligible=True,
        entry_recommendation="BREAKOUT_ENTRY_POSSIBLE",
        momentum_state="ACCELERATING",
        extended_move=False,
    )


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "opip_actionable_only_alerts": True,
        "opip_early_watch_alerts_enabled": True,
        "price_movement_mode": "shadow",
        "telegram_enabled": True,
        "telegram_bot_token": "not-a-real-token",
        "telegram_chat_id": "0",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _isolate_policy_state(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(policy, "STATE_FILE", tmp_path / "notification_state.json")
    monkeypatch.setattr(policy, "LOCK_FILE", tmp_path / ".notification_state.lock")
    monkeypatch.setattr(attention_budget, "STATE_FILE", tmp_path / "attention_budget_state.json")


# --------------------------------------------------------------------------- #
# AC-001: heuristic Early Watch never reaches the trading channel
# --------------------------------------------------------------------------- #


def test_ac_001_early_watch_with_heuristic_scores_produces_no_proactive_telegram(
    monkeypatch: pytest.MonkeyPatch,
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-001: 68/100 early watch, 28/100 risk, no alert.

    GIVEN an Early Watch observation scored 68/100 with heuristic risk 28/100 and no approved entry
    WHEN the automatic Early Watch policy and the actionable gate are evaluated
    THEN no proactive Telegram delivery is possible, the observation keeps its suppression reason,
    and a heuristic score is not even an input the gate could accept
    """
    assert scan_movers._early_watch_telegram_enabled(_settings()) is False

    watch_like = _evidence(
        entry_style="watch_only",
        valid_now=False,
        entry_trigger_confirmed=False,
        stop_price=None,
        target_1=None,
        target_2=None,
        economics_available=None,
    )
    verdict = admission.evaluate_actionable_admission(evidence=watch_like, now=NOW)
    assert verdict.admitted is False
    assert verdict.condition == admission.CONDITION_TRIGGER
    assert verdict.reason == admission.REASON_NO_ENTRY_TRIGGER
    assert verdict.suppression_reason() == (
        f"ACTIONABLE_GATE:{admission.REASON_NO_ENTRY_TRIGGER}"
    )

    # The heuristic scores the business problem names cannot enter the gate at all.
    fields = set(admission.ActionableEvidence.__dataclass_fields__)
    assert not fields & {"confidence", "score", "risk_score", "win_probability"}

    # The suppressed observation is still recorded, with its reason, when the channel is dark.
    recorded: list[dict] = []
    monkeypatch.setattr(
        scan_movers, "record_telegram_suppression", lambda **kwargs: recorded.append(kwargs)
    )
    signals = [
        _watch_signal(f"COIN{index}USD", stage="EARLY_WATCH") for index in range(3)
    ]
    written = scan_movers._record_suppressed_early_watch(
        signals, observed_at=NOW, reason="ACTIONABLE_ONLY_POLICY"
    )
    assert written == 3
    assert {row["reason"] for row in recorded} == {"ACTIONABLE_ONLY_POLICY"}
    assert {row["alert_family"] for row in recorded} == {"EARLY_MOVER"}


# --------------------------------------------------------------------------- #
# AC-002: a high score without a plan is not an actionable alert
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"entry_trigger_confirmed": False}, admission.REASON_NO_ENTRY_TRIGGER),
        ({"stop_price": None}, admission.REASON_STOP_MISSING),
        ({"target_1": None}, admission.REASON_TARGET_MISSING),
        ({"economics_available": None}, admission.REASON_ECONOMICS_UNAVAILABLE),
    ],
)
def test_ac_002_high_score_without_a_complete_plan_is_never_actionable(
    overrides: dict,
    expected_reason: str,
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-002: 95/100 without entry/stop/target/economics.

    GIVEN a candidate whose only distinction is a high heuristic score
    WHEN any one mandatory plan fact is absent
    THEN the notification is refused with that fact's reason and nothing is delivered
    """
    verdict = admission.evaluate_actionable_admission(
        evidence=_evidence(**overrides), now=NOW
    )
    assert verdict.admitted is False
    assert verdict.reason == expected_reason
    assert verdict.suppressed is True
    assert verdict.evidence_payload()["admission"] == admission.SUPPRESSED


# --------------------------------------------------------------------------- #
# AC-003: a complete plan is admitted once, with a durable identity
# --------------------------------------------------------------------------- #


def test_ac_003_a_complete_plan_is_admitted_once_with_a_durable_identity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-003: one actionable card for one qualified trade.

    GIVEN a fresh BUY that satisfies every mandatory condition
    WHEN the gate admits it and the notification policy reserves delivery
    THEN the verdict carries the plan facts the card must show, and exactly one reservation is
    granted for that identity and fingerprint
    """
    _isolate_policy_state(monkeypatch, tmp_path)
    verdict = admission.evaluate_actionable_admission(evidence=_evidence(), now=NOW)
    assert verdict.admitted is True, verdict.reasons
    assert verdict.suppression_reason() is None
    assert verdict.net_reward_to_risk is not None
    assert verdict.net_reward_to_risk > 1.2
    assert verdict.expires_at is not None and verdict.expires_at > NOW

    first = policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint="fp-complete-plan",
        now=NOW,
    )
    assert first is not None
    assert policy.confirm_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint="fp-complete-plan",
        reservation_token=first,
        now=NOW,
    )
    second = policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint="fp-complete-plan",
        now=NOW + timedelta(seconds=30),
    )
    assert second is None


# --------------------------------------------------------------------------- #
# AC-004: the same episode never duplicates
# --------------------------------------------------------------------------- #


def test_ac_004_repeated_observation_without_material_change_never_duplicates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-004: no duplicate for an unchanged episode.

    GIVEN an already-delivered actionable episode
    WHEN the scanner observes it again without a material change
    THEN the gate refuses it as a duplicate, the material fingerprint is unchanged by scores or
    timestamps alone, and the notification policy grants no second reservation
    """
    _isolate_policy_state(monkeypatch, tmp_path)
    plan = _entry_exit_plan()
    base = _candidate()
    rescored = {**base, "confidence": 95, "profit_rank_score": 99.5}
    assert chief._alert_state_key(base, plan) == chief._alert_state_key(rescored, plan)

    verdict = admission.evaluate_actionable_admission(
        evidence=_evidence(already_delivered=True, material_change=False), now=NOW
    )
    assert verdict.admitted is False
    assert verdict.condition == admission.CONDITION_IDENTITY
    assert verdict.reason == admission.REASON_DUPLICATE_EPISODE

    token = policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint=chief._alert_state_key(base, plan),
        now=NOW,
    )
    assert token is not None
    policy.confirm_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint=chief._alert_state_key(base, plan),
        reservation_token=token,
        now=NOW,
    )
    again = policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint=chief._alert_state_key(rescored, plan),
        now=NOW + timedelta(minutes=5),
    )
    assert again is None


# --------------------------------------------------------------------------- #
# AC-005: a material change is tied to the original identity
# --------------------------------------------------------------------------- #


def test_ac_005_material_change_updates_the_original_identity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-005: material change, same identity.

    GIVEN a delivered actionable plan whose stop and target have since moved
    WHEN the update is evaluated
    THEN the material fingerprint changes while the delivery identity does not, the gate admits
    the update, and the notification policy permits exactly one new emission for it
    """
    _isolate_policy_state(monkeypatch, tmp_path)
    original_plan = _entry_exit_plan()
    moved_plan = _entry_exit_plan(stop_price=97.5, target_1=109.0)
    candidate = _candidate()
    original_key = chief._alert_state_key(candidate, original_plan)
    moved_key = chief._alert_state_key(candidate, moved_plan)
    assert original_key != moved_key
    identity = f"{candidate['direction']}:{candidate['symbol']}"
    assert identity == "LONG:SOLUSD"

    verdict = admission.evaluate_actionable_admission(
        evidence=_evidence(already_delivered=True, material_change=True), now=NOW
    )
    assert verdict.admitted is True, verdict.reasons

    token = policy.reserve_emit(
        identity=identity,
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint=original_key,
        now=NOW,
    )
    assert token is not None
    policy.confirm_emit(
        identity=identity,
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint=original_key,
        reservation_token=token,
        now=NOW,
    )
    update = policy.reserve_emit(
        identity=identity,
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint=moved_key,
        now=NOW + timedelta(minutes=10),
    )
    assert update is not None


# --------------------------------------------------------------------------- #
# AC-006: a failed delivery stays retryable and never rejects the trade
# --------------------------------------------------------------------------- #


def test_ac_006_failed_delivery_is_retryable_and_never_rejects_the_trade(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-006: a transport failure is not a trade rejection.

    GIVEN a qualified setup whose Telegram send fails
    WHEN the delivery ledger and the notification policy are inspected
    THEN the attempt is recorded as SEND_FAILED and not delivered, the reserved capacity is
    released so the still-valid setup can be retried, and the gate still admits that same
    evidence
    """
    _isolate_policy_state(monkeypatch, tmp_path)
    events = tmp_path / "delivery_events.jsonl"
    state = tmp_path / "delivery_state.json"

    def _explode(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("transport down")

    monkeypatch.setattr(delivery, "send_telegram_message_with_id", _explode)
    failed = delivery.send_tracked_telegram(
        bot_token="not-a-real-token",
        chat_id="0",
        message="O'Pip Tradevisor Alerts - BUY SOLUSD",
        identity="QUALIFIED_OPPORTUNITY:TRADE-1",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp-retry",
        generated_at=NOW,
        state_file=state,
        event_file=events,
    )
    assert failed.delivered is False
    assert failed.status == "SEND_FAILED"

    token = policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint="fp-retry",
        now=NOW,
    )
    assert token is not None
    assert policy.release_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        reservation_token=token,
    )
    retry = policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type=policy.NEW_TRADE_EVENT,
        fingerprint="fp-retry",
        now=NOW + timedelta(seconds=20),
    )
    assert retry is not None

    still_valid = admission.evaluate_actionable_admission(
        evidence=_evidence(), now=NOW + timedelta(minutes=1)
    )
    assert still_valid.admitted is True


# --------------------------------------------------------------------------- #
# AC-007: protection alerts cannot be consumed by informational throttling
# --------------------------------------------------------------------------- #


def test_ac_007_critical_protection_bypasses_informational_throttling(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-007: protection is never throttled away.

    GIVEN an exhausted informational attention budget
    WHEN a stop, emergency or actionable-trade notification is evaluated
    THEN it is still allowed, while an informational Early Watch card is refused
    """
    _isolate_policy_state(monkeypatch, tmp_path)
    for _ in range(attention_budget.DEFAULT_MAX_NEW_NONCRITICAL_24H + 4):
        attention_budget.record_new_noncritical(
            kind="EARLY_MOVER", now=NOW, state_file=policy._shared_budget_file()
        )
    assert (
        attention_budget.allow_new_noncritical(
            now=NOW, state_file=policy._shared_budget_file()
        )
        is False
    )
    for event in ("STOP", "EMERGENCY", "POSITION_WARNING", "ACTIONABLE_TRADE"):
        assert policy.should_emit(
            identity=f"PROTECT:{event}", event_type=event, fingerprint=f"fp-{event}", now=NOW
        )
    assert not policy.should_emit(
        identity="EARLY_MOVER:SOLUSD",
        event_type="EARLY_WATCH",
        fingerprint="fp-watch",
        now=NOW,
    )


# --------------------------------------------------------------------------- #
# AC-008: missing, stale or invalid evidence fails closed with a real reason
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"observed_at": NOW - timedelta(hours=6)}, admission.REASON_EVIDENCE_STALE),
        ({"observed_at": None}, admission.REASON_EVIDENCE_TIMESTAMP_MISSING),
        ({"venue": "BINANCE"}, admission.REASON_VENUE_IDENTITY_INVALID),
        ({"venue": None}, admission.REASON_VENUE_IDENTITY_INVALID),
        ({"venue_identity_valid": None}, admission.REASON_VENUE_IDENTITY_INVALID),
        ({"instrument": ""}, admission.REASON_VENUE_IDENTITY_INVALID),
        ({"market_liquidity_usd": None}, admission.REASON_EXECUTION_EVIDENCE_MISSING),
        ({"market_liquidity_usd": 5_000.0}, admission.REASON_LIQUIDITY_INSUFFICIENT),
        ({"position_capacity_usd": None}, admission.REASON_EXECUTION_EVIDENCE_MISSING),
        ({"position_capacity_usd": 500.0}, admission.REASON_CAPACITY_INSUFFICIENT),
        ({"spread_bps": None}, admission.REASON_EXECUTION_EVIDENCE_MISSING),
        ({"spread_bps": 400.0}, admission.REASON_SPREAD_TOO_WIDE),
        ({"size_slippage_pct": 4.0}, admission.REASON_SIZE_SLIPPAGE_EXCESSIVE),
        ({"depth_covered": None}, admission.REASON_DEPTH_INSUFFICIENT),
        ({"order_size_usd": None}, admission.REASON_ORDER_SIZE_UNUSABLE),
        ({"current_price": None}, admission.REASON_ENTRY_PRICE_EVIDENCE_MISSING),
        ({"invalidation": None}, admission.REASON_INVALIDATION_MISSING),
        ({"expires_at": NOW - timedelta(minutes=1)}, admission.REASON_EXPIRY_PASSED),
        ({"target_1": 96.0}, admission.REASON_GEOMETRY_INCOHERENT),
        ({"target_1": 100.2}, admission.REASON_NET_RR_BELOW_POLICY),
        ({"risk_gate_passed": None}, admission.REASON_RISK_GATE_REJECTED),
        (
            {"deterministic_qualified": None},
            admission.REASON_QUALIFICATION_NOT_SATISFIED,
        ),
        ({"ai_override_attempted": True}, admission.REASON_AI_CANNOT_OVERRIDE),
        ({"funded_execution_authorized": True}, admission.REASON_FUNDED_AUTHORITY),
        ({"delivery_in_flight": True}, admission.REASON_DELIVERY_IN_FLIGHT),
    ],
)
def test_ac_008_missing_stale_or_invalid_evidence_fails_closed(
    overrides: dict, expected_reason: str
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-008: no implicit pass, ever.

    GIVEN a plan that satisfies every condition
    WHEN exactly one mandatory fact is missing, stale, invalid or already spent
    THEN the notification fails closed with that specific reason and never becomes admitted
    """
    verdict = admission.evaluate_actionable_admission(
        evidence=_evidence(**overrides), now=NOW
    )
    assert verdict.admitted is False
    assert verdict.reason == expected_reason
    assert verdict.condition is not None
    assert verdict.suppression_reason().startswith("ACTIONABLE_GATE:")


def test_ac_008_a_suppression_is_never_a_trade_rejection():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-008: notification and trade outcomes stay separate.

    GIVEN the admission policy
    WHEN a card is refused
    THEN the refusal carries only notification vocabulary and no trade lifecycle decision
    """
    verdict = admission.evaluate_actionable_admission(
        evidence=_evidence(stop_price=None), now=NOW
    )
    payload = verdict.evidence_payload()
    assert set(payload) == {
        "policy_version",
        "admission",
        "condition",
        "reason",
        "reasons",
        "net_reward_to_risk",
        "net_edge_pct",
        "effective_cost_pct",
        "expires_at",
        "expiry_source",
        "entry_trigger_kind",
    }
    assert payload["admission"] == admission.SUPPRESSED
    assert not {"QUALIFIED", "REJECTED", "ADMITTED_FOR_TRADING"} & {
        str(payload["admission"])
    }


# --------------------------------------------------------------------------- #
# AC-009: Early Watch is evidence-only and keeps capturing
# --------------------------------------------------------------------------- #


def test_ac_009_automatic_early_watch_is_evidence_only_and_capture_continues(
    monkeypatch: pytest.MonkeyPatch,
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-009: silent discovery, no watch Telegram.

    GIVEN the production Compose contract and one unambiguous Early Watch policy
    WHEN the automatic channel is resolved and a broad sweep is suppressed
    THEN the release pins the channel false, the channel cannot run while the actionable-only
    policy governs alerts, observations keep being captured, and suppression recording stays
    bounded
    """
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'OPIP_EARLY_WATCH_ALERTS_ENABLED: "false"' in compose
    # The actionable-only policy itself is pinned by the setting default, not by a Compose value
    # that a stale environment could contradict.
    assert "OPIP_ACTIONABLE_ONLY_ALERTS:" not in compose

    # One unambiguous policy: the explicit opt-in alone is not enough while alerts are gated.
    assert scan_movers._early_watch_telegram_enabled(_settings()) is False
    assert (
        scan_movers._early_watch_telegram_enabled(
            _settings(opip_actionable_only_alerts=False)
        )
        is True
    )
    assert (
        scan_movers._watch_telegram_enabled(
            _settings(opip_actionable_only_alerts=False, price_movement_mode="alert")
        )
        is True
    )

    # Market discovery and learning capture are untouched by the transport decision.
    called = _called_names(APP / "jobs" / "scan_movers.py")
    assert "capture_movement_detections" in called
    assert hasattr(movement, "append_detection_snapshots")

    recorded: list[dict] = []
    monkeypatch.setattr(
        scan_movers, "record_telegram_suppression", lambda **kwargs: recorded.append(kwargs)
    )
    sweep = [_watch_signal(f"COIN{index}USD") for index in range(25)]
    assert scan_movers._record_suppressed_early_watch(
        sweep, observed_at=NOW, reason="ACTIONABLE_ONLY_POLICY", limit=10
    ) == 10
    assert len(recorded) == 10


# --------------------------------------------------------------------------- #
# AC-010: the policy is versioned, configurable and not a profit cutoff
# --------------------------------------------------------------------------- #


def test_ac_010_the_policy_is_versioned_configurable_and_never_a_profit_cutoff():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-010: authorized, versioned, auditable settings.

    GIVEN the actionable-notification settings
    WHEN they are read from configuration and applied to a verdict
    THEN every threshold is a validated setting with a safe default, an unusable value cannot
    fail open, the version is recorded in the audit payload, and no fixed profit amount exists
    """
    from app.core.config import Settings

    fields = Settings.model_fields
    for name in (
        "opip_actionable_policy_version",
        "opip_actionable_max_evidence_age_seconds",
        "opip_actionable_plan_validity_minutes",
        "opip_actionable_min_net_reward_to_risk",
        "opip_actionable_round_trip_fee_pct",
        "opip_actionable_min_market_liquidity_usd",
        "opip_actionable_max_capacity_utilization_pct",
        "opip_actionable_max_spread_bps",
        "opip_actionable_max_size_slippage_pct",
        "opip_actionable_require_fully_covered_depth",
        "opip_actionable_admit_executable_limit_entries",
        "opip_actionable_require_target_2",
    ):
        assert name in fields, name
    assert fields["opip_actionable_min_net_reward_to_risk"].default == 1.0

    configured = admission.ActionableNotificationPolicy.from_settings(
        SimpleNamespace(
            opip_actionable_min_net_reward_to_risk=2.0,
            opip_actionable_max_spread_bps=12.0,
            opip_actionable_min_market_liquidity_usd=500_000.0,
        )
    )
    assert configured.min_net_reward_to_risk == 2.0
    assert configured.max_spread_bps == 12.0
    assert configured.min_market_liquidity_usd == 500_000.0
    stricter = admission.evaluate_actionable_admission(
        evidence=_evidence(spread_bps=20.0), policy=configured, now=NOW
    )
    assert stricter.reason == admission.REASON_SPREAD_TOO_WIDE

    fallback = admission.ActionableNotificationPolicy.from_settings(
        SimpleNamespace(
            opip_actionable_min_net_reward_to_risk="not-a-number",
            opip_actionable_max_spread_bps=float("nan"),
        )
    )
    defaults = admission.ActionableNotificationPolicy()
    assert fallback.min_net_reward_to_risk == defaults.min_net_reward_to_risk
    assert fallback.max_spread_bps == defaults.max_spread_bps

    source = (APP / "services" / "actionable_admission.py").read_text(encoding="utf-8")
    assert "min_net_profit" not in source
    assert "MIN_NET_PROFIT" not in source
    verdict = admission.evaluate_actionable_admission(evidence=_evidence(), now=NOW)
    assert verdict.policy_version == admission.POLICY_VERSION
    assert verdict.evidence_payload()["policy_version"] == admission.POLICY_VERSION


# --------------------------------------------------------------------------- #
# AC-011: every actionable producer uses the one gate
# --------------------------------------------------------------------------- #


def test_ac_011_every_actionable_producer_routes_through_one_gate(
    monkeypatch: pytest.MonkeyPatch,
):
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-011: producer coverage and no bypass.

    GIVEN the native qualified path, its durable retry outbox and the Tradevisor plane
    WHEN each producer is inspected and the direct Early Watch sender is called
    THEN all three call the shared admission gate, the retry path revalidates expiry at send
    time, and the direct watch send is refused with a recorded reason
    """
    for module in (chief, outbox, tradevisor_notifier):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "actionable_admission" in source, module.__name__
    for name in ("chief_alert_notifier.py", "tradevisor_notifier.py"):
        assert "evaluate_actionable_admission" in _called_names(APP / "services" / name)
    # The retry outbox consumes the verdict the gate already produced (one authority, not two)
    # and revalidates expiry at send time.
    assert "ActionableAdmission" in pathlib.Path(outbox.__file__).read_text(encoding="utf-8")
    assert "ACTIONABLE_GATE:EXPIRY_PASSED" in pathlib.Path(outbox.__file__).read_text(
        encoding="utf-8"
    )

    recorded: list[dict] = []
    monkeypatch.setattr(
        movement, "record_telegram_suppression", lambda **kwargs: recorded.append(kwargs)
    )
    signal = SimpleNamespace(
        symbol="SOLUSD",
        stage="BREAKOUT_CANDIDATE",
        fingerprint="fp-direct",
        alert_eligible=True,
    )
    assert movement.send_early_mover_update(signal, bot_token="t", chat_id="c") is False
    assert recorded and recorded[0]["reason"] == (
        "ACTIONABLE_ONLY_POLICY:DIRECT_WATCH_SEND_DISABLED"
    )


# --------------------------------------------------------------------------- #
# AC-012: governance -- contract, traceability and a movable pointer
# --------------------------------------------------------------------------- #


def test_ac_012_governance_contract_traceability_and_movable_pointer():
    """ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001/AC-012: legitimate scope control.

    GIVEN this increment's scope contract and the movable active-increment pointer
    WHEN both are inspected
    THEN the contract declares exactly this increment with every required section, its
    implementation map authorizes the changed paths, every acceptance criterion is traced, and
    the pointer resolves to an existing contract without being owned by this increment
    """
    assert CONTRACT.is_file()
    contract = parse_scope_contract(CONTRACT.read_text(encoding="utf-8"))
    assert contract.increment == INCREMENT
    for section in REQUIRED_SECTIONS:
        assert section in contract.sections, section
    assert contract.sections["UNAPPROVED SCOPE CHANGES"].strip() == "NONE"

    mapped = {path for paths in contract.implementation_map.values() for path in paths}
    for expected in (
        "OHM-Trade-Agent-v1/app/services/actionable_admission.py",
        "OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py",
        "OHM-Trade-Agent-v1/app/services/qualified_alert_outbox.py",
        "OHM-Trade-Agent-v1/app/services/tradevisor_notifier.py",
        "OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py",
        "OHM-Trade-Agent-v1/app/jobs/scan_movers.py",
        "OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py",
        "OHM-Trade-Agent-v1/app/core/config.py",
        "OHM-Trade-Agent-v1/app/services/telegram_delivery.py",
        "OHM-Trade-Agent-v1/app/services/movement_discovery_v2.py",
        "OHM-Trade-Agent-v1/docker-compose.yml",
        "OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py",
        "OHM-Trade-Agent-v1/tests/test_wave9_actionable_only_alerts.py",
    ):
        assert expected in mapped, expected

    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert contract.traceability[ac_id], ac_id

    pointer = read_increment_pointer(POINTER)
    contracts = load_scope_contracts(CONTRACT_DIR)
    assert any(item.increment == pointer for item in contracts)

