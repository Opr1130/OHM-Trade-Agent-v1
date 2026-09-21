"""Alert v2 presentation contract, taxonomy separation and runner integration.

Covers:

* decision-first presentation (action near the top, no redundant prose, no
  invented precision, no legacy branding),
* frozen notification taxonomy separation (SYSTEM vs SIGNAL vs PROTECTION vs
  EXTERNAL vs COMMAND),
* protection-event independence from system-incident flood control,
* end-to-end runner behaviour: 60 degraded cycles -> one human alert while every
  occurrence stays auditable in the delivery ledger,
* authority boundaries (no new order/cancel/modify/risk/paper-v2 capability).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import (
    active_trade_monitor_runner as runner,
    alert_taxonomy as taxonomy,
    alert_v2_format as fmt,
    kraken_health as health,
    notification_policy,
    system_incidents as incidents,
)
from app.services.pending_setup_monitor import PendingSetupMonitorResult
from app.services.pending_setup_registry import PendingSetup

ROOT = Path(__file__).resolve().parents[1]

PRICING_REASON = (
    "USD/stable-quote pricing unavailable for held assets: "
    "ADA.S,ETH2.S,SEI.B,SUI.B,TAO.B"
)
CONNECTIVITY_REASON = (
    "Kraken-first exposure resolution failed: "
    "KrakenTransportError: ConnectError: connection refused"
)
PRICING_KEY = "SYSTEM_HEALTH:KRAKEN:HELD_ASSET_PRICING"
PUBLIC_KEY = "SYSTEM_HEALTH:KRAKEN:PUBLIC_CONNECTIVITY"

CONNECTIVITY_CLASS = incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE
PRICING_CLASS = incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED
OPERATOR_CLASS = incidents.SystemIncidentClass.UNIFIED_CYCLE_OPERATOR_STATE
PUBLIC_SCOPE = incidents.SystemIncidentScope.KRAKEN_PUBLIC
PRICING_SCOPE = incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING
OPERATOR_SCOPE = incidents.SystemIncidentScope.UNIFIED_CYCLE
AUTH_PRICING = incidents.RecoveryAuthority.PRICING_COVERAGE
AUTH_PUBLIC = incidents.RecoveryAuthority.PUBLIC_PROBE

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

SIGNAL_FAMILIES = (
    "EARLY_MOVER",
    "BROAD_WATCH",
    "QUALIFIED_OPPORTUNITY",
    "PRICE_MOVEMENT",
    "PENDING_SETUP",
    "TRADING_SIGNAL",
)


def _settings():
    return SimpleNamespace(telegram_bot_token="token", telegram_chat_id="chat")


def _fail_public_probe(monkeypatch) -> None:
    """Make the public connectivity recovery probe fail deterministically."""

    def failing_probe():
        def probe():
            raise RuntimeError("ConnectError: connection refused")

        return probe

    monkeypatch.setattr(runner, "public_connectivity_probe", failing_probe)


def _fast_probe(monkeypatch) -> None:
    """Inject a no-op sleeper so recovery cycles are instant and deterministic."""

    import functools

    real = runner.KrakenScopeProbe
    monkeypatch.setattr(
        runner,
        "KrakenScopeProbe",
        functools.partial(real, sleeper=lambda _: None),
    )


def _resolution(*, coverage_complete: bool, reason: str = ""):
    from app.services.kraken_exposure_resolver import ExposureResolution

    return ExposureResolution(
        exposures=(),
        coverage_complete=coverage_complete,
        reason=reason,
    )


def _pricing(state, **kwargs):
    return incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        reason=PRICING_REASON,
        state_file=state,
        **kwargs,
    )


def _deliver(state, decision, message_id=1):
    return incidents.confirm_incident_notification(
        decision=decision, message_id=message_id, state_file=state
    )


def _observe(state, klass, scope, reason, **kwargs):
    return incidents.observe_degradation(
        incident_class=klass, scope=scope, reason=reason, state_file=state, **kwargs
    )


def _failed_cycle(state, klass, scope, reason, **kwargs):
    return incidents.record_failed_recovery_cycle(
        incident_class=klass, scope=scope, reason=reason, state_file=state, **kwargs
    )


def _open_connectivity_incident(state, *, cycles=7, when=NOW):
    """Drive a public connectivity incident to exactly one OPEN notification.

    Mirrors the real monitor: each cycle records the observation *and* the failed
    recovery probe, so occurrence count and failure count both advance.
    """

    decision = None
    for _ in range(cycles):
        _observe(state, CONNECTIVITY_CLASS, PUBLIC_SCOPE, CONNECTIVITY_REASON, now=when)
        decision = _failed_cycle(
            state, CONNECTIVITY_CLASS, PUBLIC_SCOPE, CONNECTIVITY_REASON, now=when
        )
    assert decision is not None
    assert decision.action == incidents.ACTION_NOTIFY_OPEN
    _deliver(state, decision)
    return decision


def _row(state: Path, key: str) -> dict | None:
    payload = json.loads(Path(state).read_text(encoding="utf-8"))
    return payload.get("incidents", {}).get(key)


def _ledger_backed_runner(monkeypatch, event_file: Path, state_file: Path) -> list[dict]:
    """Point runner delivery at a tmp ledger and capture recorded rows."""

    from app.services import telegram_delivery as delivery

    monkeypatch.setattr(delivery, "send_telegram_message_with_id", lambda *a, **k: 909)

    def send(**kwargs):
        return delivery.send_tracked_telegram(
            **kwargs, state_file=state_file, event_file=event_file
        )

    def suppress(**kwargs):
        return delivery.record_telegram_suppression(
            **kwargs, state_file=state_file, event_file=event_file
        )

    def not_eligible(**kwargs):
        return delivery.record_telegram_not_eligible(
            **kwargs, state_file=state_file, event_file=event_file
        )

    monkeypatch.setattr(runner, "send_tracked_telegram", send)
    monkeypatch.setattr(runner, "record_telegram_suppression", suppress)
    monkeypatch.setattr(runner, "record_telegram_not_eligible", not_eligible)

    def rows() -> list[dict]:
        if not event_file.exists():
            return []
        return [
            json.loads(line)
            for line in event_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return rows


# ===========================================================================
# TRADE / PROTECTION INDEPENDENCE
# ===========================================================================


@pytest.mark.parametrize(
    "event_type",
    ["STOP", "T1", "T2", "CLOSED", "EMERGENCY", "EXIT_NOW", "POSITION_WARNING"],
)
def test_protection_events_remain_independently_deliverable(
    event_type, tmp_path, monkeypatch
):
    """54-57. STOP/TARGET/EMERGENCY/POSITION_WARNING remain deliverable."""

    monkeypatch.setattr(
        notification_policy, "STATE_FILE", tmp_path / "notification.json"
    )
    monkeypatch.setattr(
        notification_policy, "LOCK_FILE", tmp_path / ".notification.lock"
    )
    monkeypatch.setattr(
        notification_policy, "allow_new_noncritical", lambda **kwargs: True
    )
    monkeypatch.setattr(
        notification_policy, "record_new_noncritical", lambda **kwargs: None
    )

    # Open and notify a system incident first.
    opened = _pricing(tmp_path / "incidents.json")
    assert opened.should_notify is True

    assert event_type in notification_policy.CRITICAL_EVENTS
    assert notification_policy.should_emit(
        identity=f"ACTIVE_TRADE:{event_type}",
        event_type=event_type,
        fingerprint=f"{event_type}:fingerprint",
    )


def test_system_health_incident_cannot_consume_protection_attention(
    tmp_path, monkeypatch
):
    """58. system-health incident does not consume/suppress critical protection."""

    monkeypatch.setattr(
        notification_policy, "STATE_FILE", tmp_path / "notification.json"
    )
    monkeypatch.setattr(
        notification_policy, "LOCK_FILE", tmp_path / ".notification.lock"
    )

    state = tmp_path / "incidents.json"
    for _ in range(60):
        _pricing(state)

    # The protection path never consults incident state.
    assert notification_policy.should_emit(
        identity="ACTIVE_TRADE:STOP",
        event_type="STOP",
        fingerprint="stop:1",
    )
    assert "ACTIONABLE_TRADE" in notification_policy.CRITICAL_EVENTS


def test_protection_alert_paths_do_not_import_incident_governance():
    """Protection ownership stays with the existing notifiers."""

    for name in (
        "trade_monitor_notifier.py",
        "emergency_alert_notifier.py",
        "pending_setup_notifier.py",
    ):
        source = (ROOT / "app" / "services" / name).read_text(encoding="utf-8")
        assert "system_incidents" not in source
        assert "observe_degradation" not in source


# ===========================================================================
# TAXONOMY
# ===========================================================================


def test_system_health_is_a_distinct_family():
    """59. SYSTEM event is never emitted with SIGNAL/PRICE_MOVEMENT family."""

    assert taxonomy.SYSTEM_HEALTH_FAMILY == "SYSTEM_HEALTH"
    assert taxonomy.is_system_family(taxonomy.SYSTEM_HEALTH_FAMILY)
    assert taxonomy.alert_class_for_family(taxonomy.SYSTEM_HEALTH_FAMILY) is (
        taxonomy.AlertClass.SYSTEM
    )

    for family in SIGNAL_FAMILIES:
        assert taxonomy.is_signal_opportunity_family(family)
        assert not taxonomy.is_system_family(family)
    assert not taxonomy.is_signal_opportunity_family(taxonomy.SYSTEM_HEALTH_FAMILY)


def test_system_event_is_not_counted_as_opportunity(tmp_path):
    """60. system event is not counted as opportunity/signal event."""

    state = tmp_path / "incidents.json"
    decision = _pricing(state)
    stored = _row(state, decision.incident_key)

    assert stored is not None
    assert stored["alert_family"] == taxonomy.SYSTEM_HEALTH_FAMILY
    assert stored["alert_family"] not in taxonomy.SIGNAL_OPPORTUNITY_FAMILIES
    assert taxonomy.is_signal_opportunity_family(stored["alert_family"]) is False


def test_external_order_review_stays_external_operator():
    """61. external order review remains EXTERNAL/OPERATOR."""

    assert taxonomy.alert_class_for_family("EXTERNAL_ORDER_REVIEW") is (
        taxonomy.AlertClass.EXTERNAL_OPERATOR
    )
    assert taxonomy.is_external_operator_family("EXTERNAL_ORDER_REVIEW")
    assert not taxonomy.is_system_family("EXTERNAL_ORDER_REVIEW")
    assert not taxonomy.is_signal_opportunity_family("EXTERNAL_ORDER_REVIEW")


def test_external_unmanaged_order_is_not_a_strategy_result():
    """62. external unmanaged order does not become an O'Pip strategy result."""

    from app.services.asset_display_identity import display_market_label
    from app.services.external_order_review import _format_review

    message = _format_review(
        order_id="OABCDEFGH12345678",
        pair="FIDAUSD",
        side="SELL",
        limit_price=0.02121,
        current_price=0.02121,
        volume=49540.14649,
        opened_at="2026-09-01T00:00:00+00:00",
    )

    assert message.splitlines()[0] == (
        f"📌 EXTERNAL ORDER — {display_market_label('FIDAUSD')}"
    )
    assert "Action: NO ACTION" in message
    assert "Status: EXTERNAL / UNMANAGED" in message
    assert "one-time review only" in message
    # Not a system failure, not a signal, not a strategy result.
    assert "SYSTEM FAILURE" not in message
    assert "signal" not in message.lower()
    assert "qualified" not in message.lower()
    assert "opportunity" not in message.lower()
    assert taxonomy.contains_legacy_user_facing_branding(message) is False


def test_automatic_alert_surfaces_carry_no_legacy_ohm_branding():
    """63. automatic Alert-v2 titles/messages do not use legacy "OHM" branding."""

    rendered: list[str] = []

    for incident_class in incidents.SystemIncidentClass:
        title, action = fmt.title_and_action(incident_class.value)
        rendered.extend([title, action])
        assert fmt.canonical_why(incident_class.value)

    from app.services.emergency_alert_notifier import format_emergency_message
    from app.services.pending_setup_notifier import format_pending_setup_message
    from app.services.trade_monitor_notifier import format_monitor_message

    trade = SimpleNamespace(
        symbol="SOLUSD",
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.0,
        risk_level="medium",
        direction="LONG",
        status="active",
        trade_id="T-1",
    )

    rendered.append(
        format_emergency_message(
            trade,
            SimpleNamespace(
                severity="critical",
                current_price=96.0,
                stop_distance_pct=1.0,
                change_5m_pct=-2.0,
                change_15m_pct=-3.0,
                reasons=["stop breached"],
            ),
        )
    )

    setup = PendingSetup(
        symbol="VVVUSD",
        entry_low=10.0,
        entry_high=10.5,
        chase_limit=10.8,
        stop_price=9.0,
        target_1=11.5,
        target_2=12.5,
        risk_level="medium",
        confidence=75,
        trade_id="T-2",
    )
    for status in ("ENTRY_ZONE_REACHED", "INVALIDATED", "TOO_EXTENDED", "OTHER"):
        rendered.append(
            format_pending_setup_message(
                setup, PendingSetupMonitorResult("VVVUSD", status, 10.2, "reason")
            )
        )

    rendered.append(
        format_monitor_message(
            trade,
            SimpleNamespace(
                action="WARNING",
                current_price=98.0,
                unrealized_pct=-2.0,
                net_pnl_pct=-2.0,
                reasons=["Price lost EMA20"],
            ),
        )
    )

    for text in rendered:
        assert taxonomy.contains_legacy_user_facing_branding(text) is False, text


def test_emergency_alert_is_decision_first():
    from app.services.asset_display_identity import display_market_label
    from app.services.emergency_alert_notifier import format_emergency_message

    trade = SimpleNamespace(
        symbol="SOLUSD",
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.0,
        direction="LONG",
    )
    message = format_emergency_message(
        trade,
        SimpleNamespace(
            severity="critical",
            current_price=96.0,
            stop_distance_pct=1.0,
            change_5m_pct=-2.0,
            change_15m_pct=-3.0,
            reasons=["stop breached"],
        ),
    )
    lines = message.splitlines()
    assert lines[0] == f"🛑 EMERGENCY RISK — {display_market_label('SOLUSD')}"
    assert lines[1] == "Action: CLOSE / REDUCE NOW"
    assert "Why: stop breached" in message
    assert message.rstrip().endswith("No order was placed or changed.")


# ===========================================================================
# PRESENTATION
# ===========================================================================


def test_decision_and_action_appear_near_top_of_alert(tmp_path):
    """64. decision/action is near top of alert."""

    state = tmp_path / "incidents.json"
    decision = _open_connectivity_incident(state)
    message = fmt.format_system_incident_message(decision)
    lines = message.splitlines()

    assert lines[0] == "🛑 SYSTEM FAILURE — KRAKEN CONNECTIVITY"
    assert lines[1] == "Action: CHECK KRAKEN / NETWORK"
    assert "State: UNAVAILABLE" in message
    assert "Recovery: automatic recovery failed 7 consecutive attempts" in message
    assert "Impact: " in message
    assert "Why: " in message
    assert "O'Pip continues attempting recovery automatically." in message
    assert message.rstrip().endswith("No order was placed or changed.")


def test_pricing_alert_does_not_claim_connectivity_failure(tmp_path):
    """17 (acceptance). pricing action text must match the actual failure."""

    state = tmp_path / "incidents.json"
    decision = _pricing(state)
    message = fmt.format_system_incident_message(decision)

    assert message.splitlines()[0] == "⚠️ SYSTEM DEGRADED — HELD-ASSET PRICING"
    assert "Action: CHECK MARKET-PRICE COVERAGE" in message
    assert "Unpriced: ADA.S, ETH2.S, SEI.B, SUI.B, TAO.B" in message
    assert "impact" in message.lower()
    assert "connectivity" not in message.lower()
    assert "KRAKEN" not in message
    assert message.rstrip().endswith("No order was placed or changed.")


def test_system_incident_is_never_rendered_as_signal_or_trade_alert(tmp_path):
    """5 (acceptance). system incidents are not signal/trade alerts."""

    pricing_state = tmp_path / "pricing.json"
    pricing_decision = _pricing(pricing_state)

    connectivity_state = tmp_path / "connectivity.json"
    connectivity_decision = _open_connectivity_incident(connectivity_state)

    read_only_state = tmp_path / "read-only.json"
    read_only_reason = (
        "Kraken account state unavailable: ConnectError: connection refused"
    )
    for _ in range(7):
        read_only_decision = _failed_cycle(
            read_only_state,
            incidents.SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY,
            incidents.SystemIncidentScope.KRAKEN_READ_ONLY,
            read_only_reason,
        )

    for decision in (pricing_decision, connectivity_decision, read_only_decision):
        assert decision.should_notify is True
        message = fmt.format_system_incident_message(decision).lower()
        for forbidden in (
            "movement watch",
            "qualified opportunity",
            "signal",
            "ready",
            "trade alert",
            "enter now",
            "place limit",
        ):
            assert forbidden not in message, forbidden


def test_recovery_alert_reports_outage_and_failed_cycles(tmp_path):
    state = tmp_path / "incidents.json"
    _open_connectivity_incident(state)

    recovered = incidents.observe_recovery(
        incident_class=CONNECTIVITY_CLASS,
        scope=PUBLIC_SCOPE,
        evidence_source=AUTH_PUBLIC,
        now=NOW.replace(hour=13, minute=4),
        state_file=state,
    )
    message = fmt.format_system_incident_message(
        recovered,
        outage_seconds=fmt.outage_seconds_between(
            recovered.first_seen_at, recovered.recovered_at
        ),
    )

    assert message.splitlines()[0] == "✅ SYSTEM RECOVERED — KRAKEN CONNECTIVITY"
    assert "Action: NO ACTION" in message
    assert "State: HEALTHY" in message
    assert "Failed recovery cycles: 7" in message
    assert "Outage duration: 1h 04m" in message
    assert "Occurrences while degraded: 7" in message
    assert "No order was placed or changed." in message


def test_reliable_movement_range_displays_correctly():
    """65. reliable movement range displays correctly."""

    from app.services.price_movement_notifier import format_price_movement_message

    message = format_price_movement_message(
        {
            "stage": "READY",
            "symbol": "KASUSD",
            "readiness_score": 70,
            "expected_move_low_pct": 10,
            "expected_move_high_pct": 25,
            "reasons": ["1h momentum is positive"],
        }
    )
    assert "Potential: +10% to +25%" in message
    assert "heuristic fallback" not in message


def test_missing_movement_range_is_disclosed_not_fabricated():
    """66-67. unavailable/unreliable range is not fabricated; heuristic is labelled."""

    from app.services.price_movement_notifier import format_price_movement_message

    message = format_price_movement_message(
        {
            "stage": "READY",
            "symbol": "KASUSD",
            "readiness_score": 70,
            "reasons": ["1h momentum is positive"],
        }
    )
    assert "heuristic fallback; not market-derived" in message


def test_inverted_or_zero_movement_range_is_not_presented_as_evidence():
    from app.services.price_movement_notifier import format_price_movement_message

    message = format_price_movement_message(
        {
            "stage": "READY",
            "symbol": "KASUSD",
            "readiness_score": 70,
            "expected_move_low_pct": 25,
            "expected_move_high_pct": 10,
            "reasons": ["1h momentum is positive"],
        }
    )
    assert "heuristic fallback; not market-derived" in message


def test_heuristic_confidence_remains_labelled_non_probabilistic():
    """68. heuristic confidence remains labelled non-probabilistic."""

    # The caveat must be preserved wherever it already exists today; it must
    # never be silently dropped or upgraded into a calibrated probability.
    setup = PendingSetup(
        symbol="VVVUSD",
        entry_low=10.0,
        entry_high=10.5,
        chase_limit=10.8,
        stop_price=9.0,
        target_1=11.5,
        target_2=12.5,
        risk_level="medium",
        confidence=75,
        trade_id="T-70",
    )
    from app.services.pending_setup_notifier import format_pending_setup_message

    setup_message = format_pending_setup_message(
        setup, PendingSetupMonitorResult("VVVUSD", "ENTRY_ZONE_REACHED", 10.2, "reason")
    )
    assert "not probability" in setup_message

    from app.services.compact_alerts import format_watch_alert

    watch_message = format_watch_alert(
        symbol="KASUSD",
        potential_low_pct=10,
        potential_high_pct=25,
        confidence_pct=70,
        risk_pct=30,
        downside_pct=9,
        reason="1h momentum is positive",
        action="WATCH FOR PULLBACK",
        title="OPPORTUNITY",
    )
    # A heuristic score is presented as a score, never as a probability.
    assert "Confidence score: 70/100" in watch_message
    assert "% chance" not in watch_message.lower()
    assert "probability" not in watch_message.lower()

    from app.services.telegram_notifier import format_trade_alert

    trade_message = format_trade_alert(
        SimpleNamespace(
            symbol="KASUSD",
            side="LONG",
            price=100.0,
            target_price=120.0,
            stop_price=95.0,
        ),
        SimpleNamespace(
            symbol="KASUSD",
            final_score=70.0,
            summary="qualified setup",
            action="enter_now",
        ),
    )
    assert "not probability" in trade_message


def test_paper_read_only_and_no_order_wording_is_preserved(tmp_path):
    """69. PAPER/read-only/no-order wording is preserved where relevant."""

    state = tmp_path / "incidents.json"
    message = fmt.format_system_incident_message(_pricing(state))
    assert "No order was placed or changed." in message

    from app.services.asset_display_identity import display_market_label
    from app.services.pending_setup_notifier import format_pending_setup_message

    setup = PendingSetup(
        symbol="VVVUSD",
        entry_low=10.0,
        entry_high=10.5,
        chase_limit=10.8,
        stop_price=9.0,
        target_1=11.5,
        target_2=12.5,
        risk_level="medium",
        confidence=75,
        trade_id="T-9",
    )
    invalid = format_pending_setup_message(
        setup, PendingSetupMonitorResult("VVVUSD", "INVALIDATED", 8.9, "stop breached")
    )
    assert "Cancel any open Kraken order" in invalid
    assert "read-only Kraken key" in invalid
    assert "No order was placed or changed." in invalid
    assert invalid.splitlines()[0] == (
        f"🔴 SETUP INVALID — {display_market_label('VVVUSD')}"
    )
    assert invalid.splitlines()[1].startswith("Action: ")


# ===========================================================================
# SECURITY / AUTHORITY
# ===========================================================================


def test_no_new_order_cancel_modify_or_risk_capability():
    """70-73. no new order placement/cancel/modify/risk-setting capability."""

    forbidden = (
        "add_order",
        "place_order",
        "create_order",
        "cancel_order",
        "modify_order",
        "cancel_all",
        "steamroller",
        "set_leverage",
        "risk_per_trade",
        "MIN_REWARD_TO_RISK",
    )
    for name in (
        "alert_taxonomy.py",
        "alert_v2_format.py",
        "kraken_health.py",
        "system_incidents.py",
    ):
        source = (ROOT / "app" / "services" / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name} must not reference {token}"


def test_new_modules_do_not_reach_trading_paper_or_execution_planes():
    """74-75. no funded/live authority and no Paper-v2 activation."""

    forbidden_imports = (
        "paper_v2",
        "paper_trade_engine",
        "capital_allocation",
        "portfolio_risk",
        "order_intent_registry",
        "active_trade_registry",
        "trade_action_gate",
        "execution",
    )
    for name in (
        "alert_taxonomy.py",
        "alert_v2_format.py",
        "kraken_health.py",
        "system_incidents.py",
    ):
        source = (ROOT / "app" / "services" / name).read_text(encoding="utf-8")
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if line.startswith("import ") or line.startswith("from ")
        ]
        for line in import_lines:
            for token in forbidden_imports:
                assert token not in line, f"{name} must not import {token}: {line}"


def test_incident_persistence_contains_no_secrets(tmp_path):
    """76. incident persistence contains no secrets."""

    state = tmp_path / "incidents.json"
    secret = "9999-secret-token-abcdef"
    incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        reason=CONNECTIVITY_REASON,
        metadata={"bot_token": secret, "chat_id": secret, "api_key": secret},
        state_file=state,
    )

    raw = state.read_text(encoding="utf-8")
    assert secret not in raw
    assert "bot_token" not in raw
    assert "api_key" not in raw


# ===========================================================================
# RUNNER INTEGRATION
# ===========================================================================


def test_runner_sixty_degraded_cycles_send_one_human_alert(tmp_path, monkeypatch):
    """21. 60+ degraded occurrences do not create 60+ human notifications."""

    incident_state = tmp_path / "incidents.json"
    ledger = tmp_path / "events.jsonl"
    delivery_state = tmp_path / "delivery.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    rows = _ledger_backed_runner(monkeypatch, ledger, delivery_state)

    resolution = _resolution(coverage_complete=False, reason=PRICING_REASON)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    for _ in range(60):
        runner.run_active_trade_monitor()

    delivered = [row for row in rows() if row["status"] == "DELIVERED"]
    assert len(delivered) == 1
    assert delivered[0]["alert_family"] == "SYSTEM_HEALTH"
    assert delivered[0]["event_type"] == "HELD_ASSET_PRICING_DEGRADED"
    assert delivered[0]["identity"] == "SYSTEM_HEALTH:KRAKEN:HELD_ASSET_PRICING"

    stored = _row(incident_state, PRICING_KEY)
    assert stored["occurrence_count"] == 60
    assert stored["suppressed_notification_count"] == 59
    assert stored["opened_notification_at"] is not None


def test_runner_underlying_occurrences_remain_auditable(tmp_path, monkeypatch):
    """22. proof occurrences remain observable/auditable."""

    incident_state = tmp_path / "incidents.json"
    ledger = tmp_path / "events.jsonl"
    delivery_state = tmp_path / "delivery.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    rows = _ledger_backed_runner(monkeypatch, ledger, delivery_state)

    resolution = _resolution(coverage_complete=False, reason=PRICING_REASON)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    for _ in range(5):
        runner.run_active_trade_monitor()

    statuses = [row["status"] for row in rows()]
    assert statuses.count("DELIVERED") == 1
    assert statuses.count("SUPPRESSED") == 4
    assert all(row["alert_family"] == "SYSTEM_HEALTH" for row in rows())

    # And the durable incident carries the full occurrence evidence.
    stored = _row(incident_state, PRICING_KEY)
    assert stored["occurrence_count"] == 5
    assert stored["first_seen_at"]
    assert stored["last_seen_at"]
    assert stored["latest_reason"].startswith("USD/stable-quote pricing unavailable")


def test_runner_system_failure_is_not_a_signal_alert(tmp_path, monkeypatch):
    """20. proof system failures are not signal alerts."""

    incident_state = tmp_path / "incidents.json"
    ledger = tmp_path / "events.jsonl"
    delivery_state = tmp_path / "delivery.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    rows = _ledger_backed_runner(monkeypatch, ledger, delivery_state)

    resolution = _resolution(coverage_complete=False, reason=CONNECTIVITY_REASON)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )
    _fail_public_probe(monkeypatch)
    _fast_probe(monkeypatch)

    for _ in range(7):
        runner.run_active_trade_monitor()

    delivered = [row for row in rows() if row["status"] == "DELIVERED"]
    assert len(delivered) == 1
    row = delivered[0]
    assert row["alert_family"] == "SYSTEM_HEALTH"
    assert row["event_type"] == "KRAKEN_CONNECTIVITY_UNAVAILABLE"
    assert row["alert_family"] not in taxonomy.SIGNAL_OPPORTUNITY_FAMILIES
    assert row["alert_family"] not in ("PRICE_MOVEMENT", "EARLY_MOVER", "BROAD_WATCH")


def test_runner_connectivity_notifies_on_cycle_seven_only(tmp_path, monkeypatch):
    """11-12 (acceptance). failures 1-6 are silent to Telegram; #7 notifies once."""

    incident_state = tmp_path / "incidents.json"
    sent: list[str] = []
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs["message"])
        or SimpleNamespace(delivered=True, message_id=len(sent)),
    )
    _fail_public_probe(monkeypatch)
    _fast_probe(monkeypatch)

    resolution = _resolution(coverage_complete=False, reason=CONNECTIVITY_REASON)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    for _ in range(6):
        runner.run_active_trade_monitor()
    assert sent == []

    runner.run_active_trade_monitor()
    assert len(sent) == 1
    assert sent[0].startswith("🛑 SYSTEM FAILURE — KRAKEN CONNECTIVITY")

    for _ in range(6):
        runner.run_active_trade_monitor()
    assert len(sent) == 1

    row = incident_state.read_text(encoding="utf-8")
    assert '"consecutive_recovery_failures": 13' in row


def test_runner_runs_probe_while_scope_is_actively_degraded(tmp_path, monkeypatch):
    """2. active degraded connectivity DOES run the matching recovery probe."""

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=1),
    )

    probe_calls: list[str] = []

    def counting_probe():
        def probe():
            probe_calls.append("public")
            raise RuntimeError("ConnectError: connection refused")

        return probe

    monkeypatch.setattr(runner, "public_connectivity_probe", counting_probe)
    _fast_probe(monkeypatch)

    resolution = _resolution(coverage_complete=False, reason=CONNECTIVITY_REASON)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    for _ in range(9):
        runner.run_active_trade_monitor()

    # The probe ran on every cycle, including while the outage was still active.
    # Each recovery *cycle* makes up to KRAKEN_RECOVERY_MAX_ATTEMPTS attempts, so
    # the attempt count is a multiple of the cycle count.
    assert probe_calls
    assert len(probe_calls) == 9 * health.KRAKEN_RECOVERY_MAX_ATTEMPTS
    payload = json.loads(incident_state.read_text(encoding="utf-8"))
    row = payload["incidents"]["SYSTEM_HEALTH:KRAKEN:PUBLIC_CONNECTIVITY"]
    assert row["consecutive_recovery_failures"] == 9
    assert row["recovery_cycles_attempted"] == 9
    assert row["occurrence_count"] == 9


def test_runner_resolver_exception_path_still_runs_one_recovery_cycle(
    tmp_path, monkeypatch
):
    """8. resolver-exception path still performs one bounded matching recovery cycle."""

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=1),
    )

    probe_calls: list[str] = []

    def counting_probe():
        def probe():
            probe_calls.append("public")
            raise RuntimeError("ConnectError: connection refused")

        return probe

    monkeypatch.setattr(runner, "public_connectivity_probe", counting_probe)
    _fast_probe(monkeypatch)

    def exploding_resolver(**kwargs):
        raise RuntimeError(
            "ConnectError: connection refused while resolving exposure"
        )

    monkeypatch.setattr(runner, "KrakenExposureResolver", exploding_resolver)

    summary = runner.run_active_trade_monitor()

    # The early-return path must not skip the required recovery cycle: one cycle
    # ran (with its bounded internal attempts), not zero.
    assert probe_calls
    assert summary.checked == 0

    payload = json.loads(incident_state.read_text(encoding="utf-8"))
    row = payload["incidents"]["SYSTEM_HEALTH:KRAKEN:PUBLIC_CONNECTIVITY"]
    assert row["consecutive_recovery_failures"] == 1
    assert row["recovery_cycles_attempted"] == 1


def test_runner_coverage_cannot_close_operator_state_incident(tmp_path, monkeypatch):
    """10-12 (integration). coverage cannot close an unowned scope."""

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)

    sent: list[str] = []
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs["message"])
        or SimpleNamespace(delivered=True, message_id=1),
    )

    # run_cycle-style operator-state failure, then a fully healthy coverage cycle.
    opened = _observe(
        incident_state,
        OPERATOR_CLASS,
        OPERATOR_SCOPE,
        "operator/capacity state unavailable: RuntimeError: corrupt registry",
    )
    assert opened.should_notify is True
    assert _deliver(incident_state, opened) is True

    resolution = _resolution(coverage_complete=True)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    for _ in range(3):
        runner.run_active_trade_monitor()

    # No false RECOVERED for a scope this monitor cannot prove healthy.
    assert sent == []
    payload = json.loads(incident_state.read_text(encoding="utf-8"))
    row = payload["incidents"]["SYSTEM_HEALTH:UNIFIED_CYCLE:OPERATOR_STATE"]
    assert row["state"] == incidents.STATE_OPEN
    assert row["recovered_at"] is None

    # The owning producer can close it.
    closed = incidents.observe_recovery(
        incident_class=OPERATOR_CLASS,
        scope=OPERATOR_SCOPE,
        evidence_source=incidents.RecoveryAuthority.OPERATOR_STATE,
        state_file=incident_state,
    )
    assert closed.action == incidents.ACTION_NOTIFY_RECOVERY


def test_reconcile_routes_rate_limit_to_its_own_probe_not_coverage(
    tmp_path, monkeypatch
):
    """Coverage cannot close a rate-limit incident; its own probe is used instead."""

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)

    sent: list[str] = []
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs["message"])
        or SimpleNamespace(delivered=True, message_id=len(sent)),
    )

    # Open a rate-limit incident (notifies immediately: no 7-cycle rule).
    opened = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_RATE_LIMITED,
        scope=incidents.SystemIncidentScope.KRAKEN_RATE_LIMIT,
        reason="Kraken public HTTP 429 for Ticker",
        state_file=incident_state,
    )
    assert opened.action == incidents.ACTION_NOTIFY_OPEN
    assert runner._deliver_system_incident_decision(
        settings=_settings(), decision=opened
    ) is True
    assert len(sent) == 1

    rate_limit_probes: list[str] = []

    def failing_rate_limit_probe():
        def probe():
            rate_limit_probes.append("rate-limit")
            raise RuntimeError("Kraken public HTTP 429 for Time")

        return probe

    monkeypatch.setattr(
        runner, "rate_limit_cleared_probe", failing_rate_limit_probe
    )
    _fast_probe(monkeypatch)
    # If coverage were (wrongly) used, this scope would close on the first sweep.
    for _ in range(3):
        failures: list[str] = []
        runner._reconcile_system_incident_recovery(
            settings=_settings(),
            coverage_complete=True,
            degraded_scopes=set(),
            failures=failures,
        )

    row = json.loads(incident_state.read_text(encoding="utf-8"))["incidents"][
        "SYSTEM_HEALTH:KRAKEN:RATE_LIMIT"
    ]
    assert row["state"] == incidents.STATE_OPEN
    assert row["recovered_at"] is None
    assert row["consecutive_recovery_failures"] == 0
    # The dedicated probe is what ran: 3 sweeps x up to KRAKEN_RECOVERY_MAX_ATTEMPTS
    # attempts inside each single recovery cycle.
    assert len(rate_limit_probes) == 3 * health.KRAKEN_RECOVERY_MAX_ATTEMPTS
    # No spurious SYSTEM RECOVERED was emitted.
    assert len(sent) == 1


def test_runner_does_not_probe_producer_owned_scopes(tmp_path, monkeypatch):

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)

    _observe(
        incident_state,
        OPERATOR_CLASS,
        OPERATOR_SCOPE,
        "operator/capacity state unavailable: RuntimeError: corrupt registry",
    )
    _observe(
        incident_state,
        incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE,
        incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH,
        "Kraken private credentials are not configured",
    )

    probes: list[str] = []
    monkeypatch.setattr(
        runner, "public_connectivity_probe", lambda: (lambda: probes.append("public"))
    )
    monkeypatch.setattr(
        runner,
        "read_only_connectivity_probe",
        lambda: (lambda: probes.append("read_only")),
    )

    failures: list[str] = []
    runner._reconcile_system_incident_recovery(
        settings=_settings(),
        coverage_complete=True,
        degraded_scopes=set(),
        failures=failures,
    )

    assert probes == []
    assert failures == []


def test_runner_notification_failure_does_not_mark_incident_notified(
    tmp_path, monkeypatch
):
    """18 / 49 (integration). failed delivery leaves the incident unreported."""

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=False, message_id=None),
    )

    resolution = _resolution(coverage_complete=False, reason=PRICING_REASON)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    runner.run_active_trade_monitor()

    stored = _row(incident_state, PRICING_KEY)
    assert stored["opened_notification_at"] is None
    assert stored["state"] == incidents.STATE_OPEN
    assert stored["notification_state"] != incidents.NOTIFICATION_OPEN_NOTIFIED


def test_runner_recovery_emits_exactly_one_recovered(tmp_path, monkeypatch):
    """14 (acceptance). recovered notification is exactly once."""

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)

    sent: list[str] = []
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs["message"])
        or SimpleNamespace(delivered=True, message_id=len(sent)),
    )

    degraded = _resolution(coverage_complete=False, reason=PRICING_REASON)
    healthy = _resolution(coverage_complete=True)
    current = {"resolution": degraded}
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: current["resolution"]),
    )

    runner.run_active_trade_monitor()
    assert len(sent) == 1

    current["resolution"] = healthy
    runner.run_active_trade_monitor()
    runner.run_active_trade_monitor()
    runner.run_active_trade_monitor()

    assert len(sent) == 2
    assert sent[1].splitlines()[0] == "✅ SYSTEM RECOVERED — HELD-ASSET PRICING"
    assert "Action: NO ACTION" in sent[1]


def test_runner_recovery_uses_matching_scope_probe(tmp_path, monkeypatch):
    """32-33 (integration). connectivity recovery probes the failed scope only."""

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=1),
    )
    _open_connectivity_incident(incident_state)

    probes: list[str] = []

    def failing(name):
        def probe():
            probes.append(name)
            raise RuntimeError("ConnectError: connection refused")

        return probe

    monkeypatch.setattr(runner, "public_connectivity_probe", lambda: failing("public"))
    monkeypatch.setattr(
        runner, "read_only_connectivity_probe", lambda: failing("read_only")
    )
    _fast_probe(monkeypatch)

    failures: list[str] = []
    runner._reconcile_system_incident_recovery(
        settings=_settings(),
        coverage_complete=False,
        degraded_scopes=set(),
        failures=failures,
    )

    # Only the public scope was probed, once per recovery cycle.
    assert probes
    assert set(probes) == {"public"}
    assert failures == []


def test_runner_does_not_close_scope_that_failed_this_cycle(tmp_path, monkeypatch):
    """Recovery is never claimed for a scope that failed in the same cycle."""

    incident_state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=1),
    )

    resolution = _resolution(coverage_complete=False, reason=PRICING_REASON)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    runner.run_active_trade_monitor()

    assert _row(incident_state, PRICING_KEY)["state"] != incidents.STATE_RECOVERED


def test_runner_never_sends_when_telegram_is_not_configured(tmp_path, monkeypatch):
    incident_state = tmp_path / "incidents.json"
    unconfigured = SimpleNamespace(telegram_bot_token="", telegram_chat_id="")
    monkeypatch.setattr(incidents, "STATE_FILE", incident_state)
    monkeypatch.setattr(runner, "get_settings", lambda: unconfigured)
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    recorded: list[str] = []
    monkeypatch.setattr(
        runner,
        "record_telegram_not_eligible",
        lambda **kwargs: recorded.append(kwargs["reason"]),
    )

    assert (
        runner._notify_monitor_degraded(settings=unconfigured, reason=PRICING_REASON)
        is False
    )
    assert recorded == ["TELEGRAM_NOT_CONFIGURED"]
    # The occurrence was still recorded durably.
    assert _row(incident_state, PRICING_KEY)["occurrence_count"] == 1


def test_runner_unmanaged_holding_alert_is_brand_free(tmp_path, monkeypatch):
    from app.services.kraken_exposure_resolver import ResolvedExposure

    sent: list[str] = []
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs["message"])
        or SimpleNamespace(delivered=True, message_id=1),
    )
    monkeypatch.setattr(runner, "should_emit", lambda **kwargs: True)

    exposure = ResolvedExposure(
        status="VERIFIED_UNMANAGED",
        symbol="SOLUSD",
        direction="LONG",
        observed_quantity=2.0,
        notional_usd=200.0,
        reason="unmanaged",
    )
    assert runner._notify_unmanaged_holding(settings=_settings(), exposure=exposure) is True
    assert sent
    assert taxonomy.contains_legacy_user_facing_branding(sent[0]) is False
    assert "No order was placed or changed." in sent[0]


def test_runner_keeps_protection_working_when_incident_storage_is_broken(
    tmp_path, monkeypatch
):
    """21 (safety). incident-storage failure must not stop protection."""

    from app.services.kraken_exposure_resolver import ResolvedExposure

    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(
        runner,
        "observe_degradation",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("incident store broken")),
    )
    monkeypatch.setattr(runner, "send_tracked_telegram", lambda **kwargs: SimpleNamespace(delivered=False, message_id=None))

    managed = SimpleNamespace(
        symbol="SOLUSD",
        entry_price=1.0,
        stop_price=0.9,
        target_1=1.2,
        target_2=1.4,
        risk_level="medium",
        status="active",
        trade_id="T-1",
        direction="LONG",
        margin_leverage=1.0,
        capital=100.0,
    )
    resolution = _resolution(coverage_complete=False, reason=PRICING_REASON)
    object.__setattr__(
        resolution,
        "exposures",
        (
            ResolvedExposure(
                status="VERIFIED_MANAGED",
                symbol="SOLUSD",
                direction="LONG",
                observed_quantity=1.0,
                reason="managed",
                trade=managed,
            ),
        ),
    )
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    checked: list[str] = []
    monkeypatch.setattr(
        runner,
        "monitor_trade",
        lambda trade: checked.append(trade.symbol)
        or SimpleNamespace(
            action="HOLD",
            current_price=1.0,
            unrealized_pct=0.0,
            net_pnl_pct=0.0,
            reasons=[],
        ),
    )
    monkeypatch.setattr(
        runner, "update_active_observation", lambda trade, price: {"mfe_pct": 0.0}
    )
    monkeypatch.setattr(
        runner, "refine_protection_action", lambda trade, result, observation: result
    )
    monkeypatch.setattr(runner, "send_monitor_update", lambda **kwargs: False)
    monkeypatch.setattr(
        runner, "detect_emergency_move", lambda trade: SimpleNamespace(triggered=False)
    )
    monkeypatch.setattr(runner, "send_emergency_alert", lambda **kwargs: False)

    summary = runner.run_active_trade_monitor()

    assert checked == ["SOLUSD"]
    assert summary.checked == 1
    assert any("degraded-monitor notification failed" in item for item in summary.failures)
