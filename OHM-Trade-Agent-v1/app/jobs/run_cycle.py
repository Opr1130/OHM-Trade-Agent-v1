from __future__ import annotations

from app.jobs.monitor_active_trades import main as monitor_active_main
from app.jobs.monitor_pending_setups import main as monitor_pending_main
from app.jobs.scan_opportunities import main as scan_main
from app.services.operator_control import get_operator_decision, mark_search_started, search_due


def main() -> None:
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
    scan_main()


if __name__ == "__main__":
    main()
