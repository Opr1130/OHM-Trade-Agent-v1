"""Composition root for one Tradevisor observation.

Wires the existing owners in one place and nothing more:

    validate (ingress) -> evaluate (F5/F7/R4) -> notify (policy + transport)

The durable record is written by ``tradevisor_inbox`` after this returns, so a
crash mid-composition leaves the row retryable rather than half-recorded.

There is deliberately no selector, no scheduler, no exchange client and no
canonical writer import here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.tradevisor_evaluation import evaluate_signal
from app.services.tradevisor_ingress import TradevisorSignal, signal_from_record
from app.services.tradevisor_notifier import notify_tradevisor_decision

DISPOSITION_EVALUATED = "EVALUATED"
DISPOSITION_REJECTED = "REJECTED"


def rebuild_signal(row: dict) -> TradevisorSignal:
    """Rebuild the validated signal from its durable row."""
    return signal_from_record(row.get("signal") or {})


def _active_trades():
    """Real open exposure, or None when it cannot be read.

    None is recorded as ``EXPOSURE_NOT_EVALUATED`` rather than treated as an
    empty portfolio, so an unreadable registry can never license a trade.
    """
    try:
        from app.services.active_trade_registry import get_active_trades

        trades = get_active_trades()
    except Exception:  # noqa: BLE001 - unreadable exposure is not zero exposure
        return None
    return list(trades) if isinstance(trades, (list, tuple, dict)) else None


def evaluate_and_notify(
    signal: TradevisorSignal,
    *,
    settings,
    now: datetime | None = None,
    snapshot_provider=None,
    execution_provider=None,
    sender=None,
    account_equity: float | None = None,
    active_trades=None,
    deliver: bool = True,
) -> dict:
    """Evaluate one validated signal and (optionally) deliver its alert."""
    evaluated_at = now or datetime.now(timezone.utc)
    trades = _active_trades() if active_trades is None else list(active_trades)

    decision = evaluate_signal(
        signal,
        now=evaluated_at,
        settings=settings,
        snapshot_provider=snapshot_provider,
        execution_provider=execution_provider,
        account_equity=account_equity,
        active_trades=trades,
    )

    notification = None
    if deliver and decision.actionable:
        notification = notify_tradevisor_decision(
            decision, settings=settings, now=evaluated_at, sender=sender
        ).as_dict()

    return {
        "decision": decision.decision,
        "reason": decision.reason,
        "disposition": DISPOSITION_EVALUATED,
        "record": decision.as_record(),
        "notification": notification,
    }


def retry_undelivered_notifications(
    *,
    settings,
    now: datetime | None = None,
    limit: int = 2,
    snapshot_provider=None,
    execution_provider=None,
    sender=None,
) -> dict:
    """Re-attempt alerts the transport previously refused.

    The stored plan is deliberately NOT re-sent. A retry re-evaluates the
    original signal against fresh native evidence, so what is delivered is
    always a currently-valid recommendation; if the opportunity no longer
    qualifies, nothing is sent and the row records that outcome. This makes a
    stale actionable plan impossible to resurrect.
    """
    from app.services import tradevisor_inbox as inbox

    retried_at = now or datetime.now(timezone.utc)
    summary = {
        "candidates": 0,
        "delivered": 0,
        "still_undelivered": 0,
        "not_actionable": 0,
        "unreadable": 0,
    }
    try:
        rows = inbox.undelivered_notification_rows(limit=limit, now=retried_at)
    except Exception:  # noqa: BLE001 - a read failure must not abort the cycle
        return summary

    for row in rows:
        summary["candidates"] += 1
        try:
            signal = rebuild_signal(row)
        except Exception:  # noqa: BLE001 - unreadable rows are left untouched
            signal = None
        if signal is None:
            # Counted rather than silently dropped: the row itself is left
            # untouched so the lease reclaim path, not this retry pass, owns it.
            summary["unreadable"] += 1
            continue
        result = evaluate_and_notify(
            signal,
            settings=settings,
            now=retried_at,
            snapshot_provider=snapshot_provider,
            execution_provider=execution_provider,
            sender=sender,
            deliver=True,
        )
        notification = result.get("notification")
        if notification is None:
            outcome = {
                "status": "RETRY_NOT_ACTIONABLE",
                "delivered": False,
                "decision": result.get("decision"),
            }
            summary["not_actionable"] += 1
        else:
            outcome = dict(notification)
            if outcome.get("delivered"):
                summary["delivered"] += 1
            else:
                summary["still_undelivered"] += 1
        inbox.record_notification_retry(
            str(row.get("dedup_id")),
            outcome=outcome,
            now=retried_at,
        )
    return summary
