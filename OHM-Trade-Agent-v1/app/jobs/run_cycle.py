from __future__ import annotations

from pathlib import Path

from app.jobs.monitor_active_trades import main as monitor_active_main
from app.jobs.monitor_pending_setups import main as monitor_pending_main
from app.jobs.scan_opportunities import main as scan_main
from app.services.external_order_review import ExternalOrderReviewSummary, review_external_open_orders
from app.services.kraken_reconciliation import ReconciliationSummary, reconcile_kraken_account
from app.services.learning_scheduler import run_learning_cycle
from app.services.operations_analytics import run_scan_with_telemetry
from app.services.operator_control import get_operator_decision, mark_search_started, search_due
from app.services.registry_io import registry_lock


CYCLE_LOCK_FILE = Path("/app/data/.unified_cycle.lock")


def _run_cycle_once() -> None:
    # Reconcile the exchange first so stale OHM lifecycle state cannot drive
    # monitoring, capacity decisions, or duplicate alerts. Reconciliation is
    # important, but a local lock/storage failure must never prevent the active
    # risk monitor from running later in this cycle.
    try:
        reconciliation = reconcile_kraken_account()
    except Exception as exc:
        reconciliation = ReconciliationSummary(
            status="UNAVAILABLE",
            mode="observe",
            reason=f"reconciliation failed open: {exc}",
        )
    print("OHM Kraken Reconciliation")
    print("Status:", reconciliation.status)
    print("Mode:", reconciliation.mode)
    print("Active checked:", reconciliation.active_checked)
    print("Order intents checked:", reconciliation.order_intents_checked)
    print("Open orders seen:", reconciliation.open_orders_seen)
    print("Fills seen:", reconciliation.fills_seen)
    print("Would close:", len(reconciliation.would_close))
    print("Closed:", len(reconciliation.closed))
    print("Would fill:", len(reconciliation.would_fill))
    print("Filled:", len(reconciliation.filled))
    if reconciliation.reason:
        print("Reconciliation reason:", reconciliation.reason)

    # Unmatched Kraken orders are informational. A failure in this optional
    # review must not block learning, operator-state evaluation, or active risk
    # protection.
    try:
        external_review = review_external_open_orders()
    except Exception as exc:
        external_review = ExternalOrderReviewSummary(
            status="UNAVAILABLE",
            reason=f"external order review failed open: {exc}",
        )
    print("OHM External Order Review")
    print("Status:", external_review.status)
    print("Unmatched orders:", external_review.unmatched_orders_seen)
    print("New reviews:", external_review.new_reviews)
    print("Notifications sent:", external_review.notifications_sent)
    if external_review.reason:
        print("External review reason:", external_review.reason)

    # Learning is telemetry/adaptation, never a dependency for risk protection
    # or scanning. Fail open if local storage/public market observation is not
    # available so production lifecycle behavior is never blocked.
    try:
        learning = run_learning_cycle()
    except Exception as exc:
        learning = {
            "status": "UNAVAILABLE",
            "paid_ai_calls": 0,
            "shadow": {"status": "UNAVAILABLE", "observations_added": 0},
            "profile_refreshed": False,
            "profile_status": "UNAVAILABLE",
            "reason": str(exc),
        }
    shadow = learning.get("shadow") or {}
    print("OHM Self-Learning")
    print("Status:", learning.get("status"))
    print("Paid AI calls:", learning.get("paid_ai_calls", 0))
    print("Shadow status:", shadow.get("status"))
    print("Shadow observations added:", shadow.get("observations_added", 0))
    print("Profile refreshed:", learning.get("profile_refreshed"))
    print("Profile status:", learning.get("profile_status"))
    if learning.get("reason"):
        print("Learning reason:", learning.get("reason"))

    decision = get_operator_decision()
    print("OHM Unified Cycle")
    print("Override mode:", decision.override_mode)
    print("Effective mode:", decision.effective_mode)
    print("Occupied slots:", decision.occupied_slots)
    print("Active trades:", decision.active_trades)
    print("Live order intents:", decision.live_order_intents)
    print("Pending setups:", decision.pending_setups)
    print("Quiet hours:", decision.quiet_hours)
    print("Reason:", decision.reason)

    if decision.effective_mode == "MAINTENANCE":
        print("Maintenance mode: all trading workflows skipped.")
        return

    # Active positions always receive deterministic protection outside
    # MAINTENANCE, including overnight quiet hours.
    monitor_active_main()

    # Pending opportunities are routine discovery/lifecycle noise while the
    # operator is asleep. They resume after 05:00 ET; active risk protection
    # remains live throughout quiet hours.
    if decision.quiet_hours:
        print("Pending setup monitor skipped during quiet hours.")
    else:
        monitor_pending_main()

    # Broad discovery is state/capacity/time gated. This is the only branch
    # that can reach the paid Chief analysis path.
    if decision.effective_mode != "SEARCH":
        print("Broad opportunity scan skipped: effective mode is", decision.effective_mode)
        return
    if not search_due(decision):
        print("Broad opportunity scan skipped: search cadence not due.")
        return

    mark_search_started()
    # Capture the scanner's existing console report into structured telemetry
    # while teeing it unchanged to stdout. Telemetry is fail-open and does not
    # participate in trade decisions.
    run_scan_with_telemetry(scan_main)


def main() -> None:
    # The unified cycle has cross-registry sequencing assumptions. Never allow
    # two scheduled invocations to interleave. This is deliberately
    # non-blocking: if a previous cycle is still running, skip the new one.
    try:
        with registry_lock(CYCLE_LOCK_FILE, timeout=0.0):
            _run_cycle_once()
    except TimeoutError:
        print("OHM Unified Cycle skipped: previous cycle still running.")


if __name__ == "__main__":
    main()
