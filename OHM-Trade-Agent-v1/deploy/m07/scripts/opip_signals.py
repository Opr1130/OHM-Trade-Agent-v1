"""R1 signal evidence: transport is not delivery, and delivery is not a signal.

Separates:
  * transport probe  - Telegram accepted a message,
  * tracked delivery - the certified delivery record exists with an id,
  * deduplication    - repeated alerts do not produce distinct message ids,
  * pipeline state   - healthy/degraded/failed/no-actionable vs delivered.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def evaluate_telegram_evidence(*, transport_probe: dict, events: list[dict]) -> dict:
    """Fail unless a TRACKED delivery record accompanies the transport success."""
    checks: list[dict] = []
    transport_ok = bool(transport_probe.get("ok")) and transport_probe.get("message_id") is not None
    checks.append({
        "id": "TRANSPORT_ACCEPTED",
        "status": "PASS" if transport_ok else "FAIL",
        "detail": f"message_id={transport_probe.get('message_id')} error={transport_probe.get('error')}",
    })

    delivered = [e for e in events if str(e.get("status")).upper() == "DELIVERED"]
    tracked = [e for e in delivered if e.get("message_id") not in (None, "", 0)]
    checks.append({
        "id": "TRACKED_RECEIPT",
        "status": "PASS" if tracked else "FAIL",
        "detail": f"delivered={len(delivered)} tracked={len(tracked)}",
    })
    untracked = [e for e in delivered if e.get("message_id") in (None, "", 0)]
    checks.append({
        "id": "EVERY_DELIVERY_HAS_MESSAGE_ID",
        "status": "PASS" if not untracked else "FAIL",
        "detail": f"delivered_without_id={len(untracked)}",
    })

    failed = [c for c in checks if c["status"] == "FAIL"]
    if transport_ok and not tracked:
        verdict, reason = "FAIL", "transport succeeded but no tracked delivery receipt exists"
    elif failed:
        verdict, reason = "FAIL", "transport failed or a delivery row lacks a message id"
    else:
        verdict, reason = "PASS", "tracked delivery receipt present"
    return {
        "evaluated_at_utc": _utc(),
        "verdict": verdict,
        "reason": reason,
        "actionable_signal_delivered": verdict == "PASS",
        "transport_only": transport_ok and not tracked,
        "checks": checks,
    }


def evaluate_dedupe(*, identity: str, id_before, id_after, expected_duplicate: bool = False) -> dict:
    """The canonical message id for one identity must not drift without a new send."""
    if id_before in (None, 0) or id_after in (None, 0):
        return {"verdict": "FAIL", "reason": "no stable canonical message id", "identity": identity,
                "id_before": id_before, "id_after": id_after}
    if expected_duplicate:
        ok = id_before != id_after
        reason = "a second distinct id is visible (duplicate detected)" if ok else "duplicate was not detectable"
    else:
        ok = id_before == id_after
        reason = "canonical id stable across reads" if ok else "canonical id drifted without a new send"
    return {"verdict": "PASS" if ok else "FAIL", "reason": reason, "identity": identity,
            "id_before": id_before, "id_after": id_after}


def classify_pipeline_state(
    *,
    api_healthy: bool,
    scheduler_ran: bool,
    market_data_acquired: bool,
    candidates_evaluated: bool,
    qualified_actionable: int,
    delivered: int,
) -> dict:
    """A healthy API never implies B-H; each stage is reported separately."""
    stages = {
        "A_api_healthy": bool(api_healthy),
        "B_scheduler_executed": bool(scheduler_ran),
        "C_market_data_acquired": bool(market_data_acquired),
        "D_candidates_evaluated": bool(candidates_evaluated),
        "E_qualified": qualified_actionable > 0,
        "F_routed": qualified_actionable > 0 and delivered > 0,
        "G_transport": delivered > 0,
        "H_acknowledged": delivered > 0,
    }
    if not api_healthy or not scheduler_ran:
        state = "SIGNAL_PIPELINE_FAILED"
    elif not market_data_acquired or not candidates_evaluated:
        state = "SIGNAL_PIPELINE_DEGRADED"
    elif qualified_actionable == 0:
        state = "SIGNAL_PIPELINE_HEALTHY_NO_ACTIONABLE_SIGNAL"
    elif delivered > 0:
        state = "ACTIONABLE_SIGNAL_DELIVERED"
    else:
        state = "SIGNAL_PIPELINE_DEGRADED"
    return {"state": state, "stages": stages,
            "note": "no Telegram message does not imply a failed scanner; see stages B-F"}
