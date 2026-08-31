from pathlib import Path
from typing import Any

from app.services.asset_display_identity import display_asset_text, display_market_label
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.kraken_reconciliation import (
    reconciliation_enabled,
    reconciliation_mode,
)
from app.services.notification_policy import (
    confirm_emit,
    release_emit,
    reserve_emit,
    should_emit,
)
from app.services.order_intent_registry import (
    OrderIntent,
    get_order_intent,
    register_order_intent,
)
from app.services.pending_setup_registry import (
    PendingSetup,
    add_pending_setup,
    get_pending_setup_by_trade_id,
    get_pending_setup_record,
    terminalize_pending_setup,
)
from app.services.price_movement_radar import attach_actionable_plan
from app.services.qualified_alert_outbox import queue_qualified_alert
from app.services.qualified_trade_tracking import (
    ReconciliationIdentityMismatch,
    ReconciliationTrackingDisabled,
    register_reconciliation_intent,
)
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_delivery import (
    accepted_delivery_message_id,
    record_telegram_not_eligible,
    record_telegram_suppression,
    send_tracked_telegram,
)
from app.services.trade_outcome_registry import record_recommendation


STATE_FILE = Path("/app/data/alert_state.json")
STATE_LOCK_FILE = STATE_FILE.parent / ".alert_state.lock"


def _pretty_entry_style(value: str) -> str:
    return value.replace("_", " ").title()


def _load_state() -> dict[str, str]:
    with registry_lock(STATE_LOCK_FILE):
        return {str(key): str(value) for key, value in load_json(STATE_FILE).items()}


def _save_state(state: dict[str, str]) -> None:
    with registry_lock(STATE_LOCK_FILE):
        save_json_atomic(STATE_FILE, state)


def _action_type(plan: EntryExitPlan) -> str | None:
    if plan.valid_now:
        return "ENTER_NOW"
    if plan.entry_style in {"wait_for_pullback", "wait_for_rebound"}:
        return "PLACE_LIMIT"
    return None


def _round_price(value: float) -> str:
    return f"{float(value):.6g}"


def _alert_state_key(candidate: dict[str, Any], plan: EntryExitPlan) -> str:
    action = _action_type(plan) or "SILENT"
    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    return ":".join(
        [
            direction,
            action,
            plan.risk_level,
            _round_price(plan.entry_low),
            _round_price(plan.entry_high),
            _round_price(plan.stop_price),
            _round_price(plan.target_1),
            _round_price(plan.target_2),
        ]
    )


def format_trade_plan(candidate: dict[str, Any], plan: EntryExitPlan, summary: str) -> str:
    action = _action_type(plan)
    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    if action == "ENTER_NOW":
        headline = f"🔥 OPPORTUNITY — {direction} — ENTER NOW"
        action_text = "Action: ENTRY CONDITIONS VALID\nUse the approved entry zone and respect the chase boundary."
    elif action == "PLACE_LIMIT":
        headline = f"🎯 OPPORTUNITY — {direction} — PLACE LIMIT"
        action_text = (
            "Action: SET LIMIT ENTRY\n"
            + (
                "Setup is qualified, but price is extended downward. Wait for the approved rebound/retest."
                if direction == "SHORT"
                else "Setup is qualified, but price is extended. Wait for the approved pullback."
            )
        )
    else:
        raise ValueError("Cannot format non-actionable trade plan")

    quote_note = ""
    if candidate.get("primary_quote_currency") == "USDT" and not candidate.get("secondary_pair"):
        quote_note = "Quote: USDT (USDT availability required; no automatic USD conversion)\n"

    ranking_note = ""
    if candidate.get("opportunity_rank") is not None:
        ranking_note += f"Global Opportunity Rank: #{candidate['opportunity_rank']}\n"
    if candidate.get("capital_efficiency_score") is not None:
        ranking_note += (
            f"Capital Efficiency Score: "
            f"{float(candidate['capital_efficiency_score']):.2f}/100\n"
        )
    if candidate.get("profit_rank") is not None:
        if candidate.get("opportunity_rank") is None:
            ranking_note += f"Opportunity Rank: #{candidate['profit_rank']}\n"
        else:
            ranking_note += f"Quality Rank: #{candidate['profit_rank']}\n"
    if candidate.get("profit_rank_score") is not None:
        if candidate.get("opportunity_rank") is None:
            ranking_note += (
                f"Profit Rank Score: "
                f"{float(candidate['profit_rank_score']):.2f}/100\n"
            )
        else:
            ranking_note += (
                f"Base Quality Score: "
                f"{float(candidate['profit_rank_score']):.2f}/100\n"
            )
    if candidate.get("hold_proxy_hours") is not None:
        ranking_note += (
            f"Hold Proxy: ~{float(candidate['hold_proxy_hours']):.1f}h "
            "(deterministic, not forecast probability)\n"
        )

    intelligence_note = ""
    if candidate.get("continuation_score") is not None:
        intelligence_note += (
            f"Continuation: {int(candidate['continuation_score'])}/100 "
            f"({candidate.get('continuation_decision', 'UNKNOWN')})\n"
        )
    if candidate.get("entry_quality_score") is not None:
        intelligence_note += (
            f"Entry Quality: {int(candidate['entry_quality_score'])}/100 "
            f"({candidate.get('entry_quality_decision', 'UNKNOWN')})\n"
        )
    if candidate.get("exhaustion_state") is not None:
        intelligence_note += f"Exhaustion: {candidate['exhaustion_state']}\n"
    if candidate.get("recommended_capital") is not None:
        intelligence_note += f"Recommended Capital: ${float(candidate['recommended_capital']):,.2f}\n"
    if candidate.get("recommended_risk_dollars") is not None:
        intelligence_note += f"Estimated Stop Risk: ${float(candidate['recommended_risk_dollars']):,.2f}\n"
    if candidate.get("projected_net_edge_pct") is not None:
        intelligence_note += f"Projected Net Edge: {float(candidate['projected_net_edge_pct']):.2f}%\n"
    if candidate.get("calibration_status") is not None:
        intelligence_note += (
            f"Calibration: {candidate['calibration_status']} "
            f"({float(candidate.get('calibration_multiplier') or 1.0):.2f}x sizing only)\n"
        )

    movement_note = ""
    movement = candidate.get("price_movement")
    if isinstance(movement, dict) and movement.get("actionable") is True:
        movement_note = (
            "\n⚡ PRICE MOVEMENT INTELLIGENCE\n"
            f"Class: {movement.get('signal_class', 'PRICE_MOVEMENT')} / "
            f"{movement.get('subtype', 'VOLATILITY_EXPANSION')}\n"
            f"Stage: {movement.get('stage', 'CONFIRMED')} | "
            f"Readiness: {movement.get('readiness_score', 0)}/100 "
            "(not probability)\n"
            f"Timeframes: detect {movement.get('detection_timeframe', 'N/A')} | "
            f"confirm {movement.get('confirmation_timeframe', 'N/A')} | "
            f"regime {movement.get('regime_timeframe', '4H')}\n"
            f"Expected Expansion: {movement.get('expected_move_low_atr', 0)}-"
            f"{movement.get('expected_move_high_atr', 0)} ATR "
            f"({movement.get('expected_move_low_pct', 0)}%-"
            f"{movement.get('expected_move_high_pct', 0)}%)\n"
            f"Expected Window: {movement.get('expected_window_primary', 'N/A')} "
            f"then {movement.get('expected_window_secondary', 'N/A')}\n"
            f"Entry Plan Expires: {movement.get('setup_expires_at', 'N/A')}\n"
        )

    margin_note = ""
    if direction == "SHORT":
        margin_note = (
            f"Margin: Kraken US retail eligible | Validation leverage: {float(candidate.get('margin_leverage') or 2.0):.1f}x\n"
            "Execution: MANUAL ONLY — verify live Kraken margin/opening/rollover fees before entry\n"
        )

    fill_tracking_note = ""
    if candidate.get("economic_qualified") is True:
        fill_tracking_note = (
            "Fill Tracking: read-only Kraken reconciliation; a monitoring-degraded alert is sent if position verification is unavailable\n"
        )

    chase_label = "Do Not Chase Below" if direction == "SHORT" else "Do Not Chase Above"
    base_asset = str(candidate.get("underlying_asset") or plan.symbol)
    primary_pair = str(candidate.get("primary_pair") or plan.symbol)
    asset_text = display_asset_text(
        plan.symbol,
        base_asset=base_asset,
        pair=primary_pair,
    )
    market_label = display_market_label(
        plan.symbol,
        base_asset=base_asset,
        pair=primary_pair,
    )
    return (
        f"{headline}\n\n{action_text}\n\n"
        f"Asset: {asset_text}\n"
        f"Market: {market_label}\n"
        f"Direction: {direction}\n"
        f"{quote_note}{margin_note}{ranking_note}{intelligence_note}{movement_note}{fill_tracking_note}"
        f"Risk: {plan.risk_level.upper()}\n"
        f"Setup Score: {int(candidate.get('confidence', 0))}/100 (not probability)\n"
        f"Technical Decision: {str(candidate.get('decision', '')).upper()}\n\n"
        f"📍 ENTRY PLAN\nEntry Zone: {plan.entry_low} - {plan.entry_high}\n"
        f"{chase_label}: {plan.chase_limit}\nStop: {plan.stop_price}\n\n"
        f"🎯 TARGETS\nTarget 1: {plan.target_1} ({plan.reward_to_risk_1}:1 R/R)\n"
        f"Target 2: {plan.target_2} ({plan.reward_to_risk_2}:1 R/R)\n\n"
        f"Entry Style: {_pretty_entry_style(plan.entry_style)}\n\n"
        f"Reason:\n{plan.reason}\n\nChief Analyst:\n{candidate.get('reason', '')}\n\n"
        f"Market Summary:\n{summary}"
    )


def should_send_trade_plan(candidate: dict[str, Any], plan: EntryExitPlan) -> bool:
    action = _action_type(plan)
    if action is None:
        return False
    current_key = _alert_state_key(candidate, plan)
    try:
        state = _load_state()
    except (OSError, TimeoutError, RegistryIOError):
        # This file is only a convenience dedup cache. Qualified actionable
        # trades must not disappear because it is unreadable; the atomic
        # notification policy and delivery ledger remain authoritative.
        state = {}
    if state.get(plan.symbol) == current_key:
        return False
    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    return should_emit(
        identity=f"{direction}:{plan.symbol}",
        event_type="ACTIONABLE_TRADE",
        fingerprint=current_key,
    )


def _register_reconciliation_intent(
    *,
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    action: str,
    direction: str,
    leverage: float,
    trade_id: str,
) -> None:
    """Compatibility wrapper around the canonical tracking boundary."""
    register_reconciliation_intent(
        candidate=candidate,
        plan=plan,
        action=action,
        direction=direction,
        leverage=leverage,
        trade_id=trade_id,
        reconciliation_is_enabled=reconciliation_enabled(),
        reconciliation_mode_value=reconciliation_mode(),
    )


def send_trade_plan(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    summary: str,
    bot_token: str,
    chat_id: str,
) -> bool:
    action = _action_type(plan)
    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    identity = f"QUALIFIED_OPPORTUNITY:{candidate.get('trade_id') or plan.symbol}"
    initial_fingerprint = _alert_state_key(candidate, plan)
    if action is None:
        record_telegram_not_eligible(
            identity=identity,
            alert_family="QUALIFIED_OPPORTUNITY",
            event_type="NOT_ACTIONABLE",
            fingerprint=initial_fingerprint,
            reason="NO_ACTIONABLE_ENTRY_PLAN",
            symbol=plan.symbol,
            journey_id=candidate.get("journey_id"),
            signal_id=candidate.get("signal_id"),
            trade_id=candidate.get("trade_id"),
        )
        return False
    if not should_send_trade_plan(candidate, plan):
        record_telegram_suppression(
            identity=identity,
            alert_family="QUALIFIED_OPPORTUNITY",
            event_type=action,
            fingerprint=initial_fingerprint,
            reason="DEDUP_OR_NOTIFICATION_POLICY",
            symbol=plan.symbol,
            journey_id=candidate.get("journey_id"),
            signal_id=candidate.get("signal_id"),
            trade_id=candidate.get("trade_id"),
        )
        return False
    if candidate.get("action_gate_evaluated") is not True:
        record_telegram_not_eligible(
            identity=identity,
            alert_family="QUALIFIED_OPPORTUNITY",
            event_type=action,
            fingerprint=initial_fingerprint,
            reason="ACTION_GATE_NOT_EVALUATED",
            symbol=plan.symbol,
            journey_id=candidate.get("journey_id"),
            signal_id=candidate.get("signal_id"),
            trade_id=candidate.get("trade_id"),
        )
        return False
    action_allowed = candidate.get("action_gate_allowed") is True
    if not action_allowed:
        record_telegram_not_eligible(
            identity=identity,
            alert_family="QUALIFIED_OPPORTUNITY",
            event_type=action,
            fingerprint=initial_fingerprint,
            reason="PORTFOLIO_OR_ALLOCATION_GUARDRAIL",
            symbol=plan.symbol,
            journey_id=candidate.get("journey_id"),
            signal_id=candidate.get("signal_id"),
            trade_id=candidate.get("trade_id"),
        )
        return False

    movement = attach_actionable_plan(
        candidate.get("price_movement"),
        plan=plan,
        action=action,
    )
    if movement is not None:
        candidate["price_movement"] = movement

    leverage = float(candidate.get("margin_leverage") or (2.0 if direction == "SHORT" else 1.0))
    message = format_trade_plan(candidate=candidate, plan=plan, summary=summary)

    # A materially new plan must receive a new immutable lifecycle id. Never
    # reuse another waiting setup merely because the symbol is the same.
    trade_id = str(candidate.get("trade_id") or "")
    setup = get_pending_setup_by_trade_id(trade_id) if trade_id else None
    if setup is None:
        setup = add_pending_setup(
            PendingSetup(
                symbol=plan.symbol,
                entry_low=plan.entry_low,
                entry_high=plan.entry_high,
                chase_limit=plan.chase_limit,
                stop_price=plan.stop_price,
                target_1=plan.target_1,
                target_2=plan.target_2,
                risk_level=plan.risk_level,
                confidence=int(candidate.get("confidence", 0)),
                confirmation_price=(plan.entry_low if direction == "SHORT" else plan.entry_high),
                direction=direction,
                margin_leverage=leverage,
                trade_id=trade_id,
            )
        )
        trade_id = setup.trade_id
    if trade_id:
        candidate["trade_id"] = trade_id

    record_recommendation(
        trade_id=trade_id,
        candidate=candidate,
        plan=plan,
        action=action,
    )
    key = _alert_state_key(candidate, plan)
    identity = f"QUALIFIED_OPPORTUNITY:{trade_id or plan.symbol}"

    if candidate.get("economic_qualified") is True:
        if not trade_id:
            return False
        try:
            _register_reconciliation_intent(
                candidate=candidate,
                plan=plan,
                action=action,
                direction=direction,
                leverage=leverage,
                trade_id=trade_id,
            )
        except ReconciliationTrackingDisabled:
            terminalize_pending_setup(trade_id, "tracking_disabled")
            record_telegram_suppression(
                identity=identity,
                alert_family="QUALIFIED_OPPORTUNITY",
                event_type=action,
                fingerprint=key,
                reason="RECONCILIATION_NOT_APPLY_TERMINAL",
                symbol=plan.symbol,
                journey_id=candidate.get("journey_id"),
                signal_id=candidate.get("signal_id"),
                trade_id=trade_id,
            )
            return False
        except ReconciliationIdentityMismatch as exc:
            transitioned = False
            lifecycle_after_status = "waiting"
            try:
                transitioned = terminalize_pending_setup(
                    trade_id,
                    "tracking_failed",
                )
                lifecycle_after = get_pending_setup_record(trade_id)
                lifecycle_after_status = str(
                    (lifecycle_after or {}).get("status") or ""
                )
            except Exception as transition_exc:
                print(
                    "O'Pip reconciliation-mismatch terminalization failed:",
                    f"trade_id={trade_id}",
                    f"{type(transition_exc).__name__}: {transition_exc}",
                )

            if not transitioned and lifecycle_after_status == "waiting":
                try:
                    queue_qualified_alert(
                        trade_id=trade_id,
                        message=message,
                        candidate=candidate,
                        plan=plan,
                        action=action,
                        direction=direction,
                        identity=identity,
                        fingerprint=key,
                        reason=(
                            "TRACKING_IDENTITY_MISMATCH_TERMINALIZATION_PENDING:"
                            f"{type(exc).__name__}"
                        ),
                    )
                except Exception as queue_exc:
                    print(
                        "O'Pip terminalization-pending queueing failed:",
                        f"trade_id={trade_id}",
                        f"{type(queue_exc).__name__}: {queue_exc}",
                    )

            record_telegram_suppression(
                identity=identity,
                alert_family="QUALIFIED_OPPORTUNITY",
                event_type=action,
                fingerprint=key,
                reason=(
                    "TRACKING_IDENTITY_MISMATCH_TERMINAL"
                    if transitioned or lifecycle_after_status != "waiting"
                    else "TRACKING_IDENTITY_MISMATCH_TERMINALIZATION_PENDING"
                ),
                symbol=plan.symbol,
                journey_id=candidate.get("journey_id"),
                signal_id=candidate.get("signal_id"),
                trade_id=trade_id,
            )
            return False
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            try:
                queue_qualified_alert(
                    trade_id=trade_id,
                    message=message,
                    candidate=candidate,
                    plan=plan,
                    action=action,
                    direction=direction,
                    identity=identity,
                    fingerprint=key,
                    reason=f"TRACKING_PENDING:{type(exc).__name__}",
                )
            except Exception as queue_exc:
                print(
                    "O'Pip qualified alert queueing failed:",
                    f"trade_id={trade_id}",
                    f"{type(queue_exc).__name__}: {queue_exc}",
                )
                record_telegram_suppression(
                    identity=identity,
                    alert_family="QUALIFIED_OPPORTUNITY",
                    event_type=action,
                    fingerprint=key,
                    reason="TRACKING_PENDING_UNQUEUEABLE",
                    symbol=plan.symbol,
                    journey_id=candidate.get("journey_id"),
                    signal_id=candidate.get("signal_id"),
                    trade_id=trade_id,
                )
            # Tracking failure is operational/transport state, not rejection.
            return False

    accepted_message_id = accepted_delivery_message_id(
        identity=identity,
        event_type=action,
        fingerprint=key,
    )
    if accepted_message_id is not None:
        try:
            with registry_lock(STATE_LOCK_FILE):
                state = {str(k): str(v) for k, v in load_json(STATE_FILE).items()}
                state[plan.symbol] = key
                save_json_atomic(STATE_FILE, state)
        except (OSError, TimeoutError, RegistryIOError):
            pass
        return True

    reservation = reserve_emit(
        identity=f"{direction}:{plan.symbol}",
        event_type="ACTIONABLE_TRADE",
        fingerprint=key,
    )
    if reservation is None:
        record_telegram_suppression(
            identity=identity,
            alert_family="QUALIFIED_OPPORTUNITY",
            event_type=action,
            fingerprint=key,
            reason="ATOMIC_NOTIFICATION_POLICY",
            symbol=plan.symbol,
            journey_id=candidate.get("journey_id"),
            signal_id=candidate.get("signal_id"),
            trade_id=trade_id,
        )
        return False

    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=message,
        identity=identity,
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type=action,
        fingerprint=key,
        symbol=plan.symbol,
        journey_id=candidate.get("journey_id"),
        signal_id=candidate.get("signal_id"),
        trade_id=trade_id,
    )
    if delivery.delivered:
        try:
            with registry_lock(STATE_LOCK_FILE):
                state = {str(k): str(v) for k, v in load_json(STATE_FILE).items()}
                state[plan.symbol] = key
                save_json_atomic(STATE_FILE, state)
        except (OSError, TimeoutError, RegistryIOError):
            pass
        confirm_emit(
            identity=f"{direction}:{plan.symbol}",
            event_type="ACTIONABLE_TRADE",
            fingerprint=key,
            reservation_token=reservation,
        )
        return True

    release_emit(
        identity=f"{direction}:{plan.symbol}",
        event_type="ACTIONABLE_TRADE",
        reservation_token=reservation,
    )
    if trade_id:
        try:
            queue_qualified_alert(
                trade_id=trade_id,
                message=message,
                candidate=candidate,
                plan=plan,
                action=action,
                direction=direction,
                identity=identity,
                fingerprint=key,
                reason="DELIVERY_PENDING",
            )
        except Exception as queue_exc:
            print(
                "O'Pip qualified alert queueing failed:",
                f"trade_id={trade_id}",
                f"{type(queue_exc).__name__}: {queue_exc}",
            )
            record_telegram_suppression(
                identity=identity,
                alert_family="QUALIFIED_OPPORTUNITY",
                event_type=action,
                fingerprint=key,
                reason="DELIVERY_PENDING_UNQUEUEABLE",
                symbol=plan.symbol,
                journey_id=candidate.get("journey_id"),
                signal_id=candidate.get("signal_id"),
                trade_id=trade_id,
            )
    return False