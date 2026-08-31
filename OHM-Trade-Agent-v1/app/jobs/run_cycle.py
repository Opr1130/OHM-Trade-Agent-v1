from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.jobs.monitor_active_trades import main as monitor_active_main
from app.jobs.monitor_pending_setups import main as monitor_pending_main
from app.jobs.scan_movers import main as scan_movers_main
from app.jobs.scan_opportunities import main as scan_main
from app.services.active_trade_monitor_runner import _notify_monitor_degraded
from app.services.external_order_review import ExternalOrderReviewSummary, review_external_open_orders
from app.services.kraken_reconciliation import ReconciliationSummary, reconcile_kraken_account
from app.services.learning_scheduler import run_learning_cycle
from app.services.operations_analytics import run_scan_with_telemetry
from app.services.operator_control import get_operator_decision, mark_search_started, search_due
from app.services.registry_io import load_json, registry_lock, save_json_atomic


CYCLE_LOCK_FILE = Path("/app/data/.unified_cycle.lock")
EARLY_WATCH_STATE_FILE = Path("/app/data/early_watch_scheduler_state.json")
EARLY_WATCH_LOCK_FILE = EARLY_WATCH_STATE_FILE.parent / ".early_watch_scheduler.lock"


def _parse_scheduler_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _run_early_watch_if_due(*, settings, quiet_hours: bool) -> None:
    """Run Early Watch on its configured cadence after real risk protection.

    Crypto markets are continuous. Quiet hours suppress lower-priority pending
    and broad/paid discovery, but must not create an overnight blind spot in
    the selective Early Watch Telegram channel.
    """
    if quiet_hours:
        print("OHM Early Watch remains active during quiet hours.")

    now = datetime.now(timezone.utc)
    interval_seconds = max(
        30,
        int(getattr(settings, "signal_quality_scan_interval_seconds", 600)),
    )
    try:
        with registry_lock(EARLY_WATCH_LOCK_FILE):
            state = load_json(EARLY_WATCH_STATE_FILE)
            last = _parse_scheduler_time(state.get("last_started_at"))
            if last is not None and (now - last).total_seconds() < interval_seconds:
                print("OHM Early Watch skipped: cadence not due.")
                return
            state["last_started_at"] = now.isoformat()
            state["interval_seconds"] = interval_seconds
            save_json_atomic(EARLY_WATCH_STATE_FILE, state)
    except Exception as exc:
        print(
            "OHM Early Watch scheduler unavailable; production unaffected:",
            f"{type(exc).__name__}: {exc}",
        )
        return

    try:
        scan_movers_main()
    except Exception as exc:
        print(
            "OHM Early Watch failed open; production unaffected:",
            f"{type(exc).__name__}: {exc}",
        )


def _run_paper_monitor_fail_open() -> None:
    """Run paper simulation only after live protection has completed."""
    try:
        from app.services.paper_trade_engine import PaperTradeConfig
        from app.services.paper_trade_monitor import run_paper_trade_monitor

        settings = get_settings()
        summary = run_paper_trade_monitor(PaperTradeConfig.from_settings(settings))
    except Exception as exc:
        print(
            "OHM Paper Trade v1 unavailable; production unaffected:",
            f"{type(exc).__name__}: {exc}",
        )
        return

    print("OHM Paper Trade v1")
    print("Control enabled:", summary.control_enabled)
    print("Tracked:", summary.tracked)
    print("Checked:", summary.checked)
    print("Opened:", summary.opened)
    print("TP1 hits:", summary.tp1_hits)
    print("Closed:", summary.closed)
    print("Cancelled:", summary.cancelled)
    print("Unresolved:", summary.unresolved)
    print("Failures:", len(summary.failures))
    for failure in summary.failures[:3]:
        print("PAPER FAILURE:", failure)


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
            reason=f"reconciliation failed open: {type(exc).__name__}: {exc}",
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
            reason=f"external order review failed open: {type(exc).__name__}: {exc}",
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
            "price_movement": {"status": "UNAVAILABLE", "observations_added": 0},
            "profile_refreshed": False,
            "profile_status": "UNAVAILABLE",
            "reason": str(exc),
        }
    shadow = learning.get("shadow") or {}
    movement_learning = learning.get("price_movement") or {}
    print("OHM Self-Learning")
    print("Status:", learning.get("status"))
    print("Paid AI calls:", learning.get("paid_ai_calls", 0))
    print("Shadow status:", shadow.get("status"))
    print("Shadow observations added:", shadow.get("observations_added", 0))
    print("Movement learning status:", movement_learning.get("status"))
    print("Movement observations added:", movement_learning.get("observations_added", 0))
    print("Profile refreshed:", learning.get("profile_refreshed"))
    print("Profile status:", learning.get("profile_status"))
    if learning.get("reason"):
        print("Learning reason:", learning.get("reason"))

    # Operator/capacity state reads span several registries. If any of them are
    # quarantined or temporarily unavailable, do not let that abort the cycle
    # before active positions receive their independent risk monitor pass.
    try:
        decision = get_operator_decision()
    except Exception as exc:
        reason = f"operator/capacity state unavailable: {type(exc).__name__}: {exc}"
        print("OHM Unified Cycle degraded:", reason)
        try:
            _notify_monitor_degraded(settings=get_settings(), reason=reason, identity="UNIFIED_CYCLE_STATE")
        except Exception as notify_exc:
            print("OHM degradation alert failed:", notify_exc)
        monitor_active_main()
        print("Discovery/pending workflows skipped until operator state is readable.")
        return

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

    # Early Watch runs 24/7 after real active/pending protection and on its own
    # cadence. Alert governance/dedup controls noise during quiet hours. Its
    # failure cannot suppress the production opportunity scanner.
    _run_early_watch_if_due(
        settings=get_settings(),
        quiet_hours=decision.quiet_hours,
    )

    # Shadow paper simulation is lower priority than every real lifecycle
    # workflow. Authoritative Freqtrade runs in a separate Compose stack.
    _run_paper_monitor_fail_open()

    # Broad discovery is state/capacity/time gated. This is the only branch
    # that can reach the paid Chief analysis path.
    if decision.effective_mode != "SEARCH":
        print("Broad opportunity scan skipped: effective mode is", decision.effective_mode)
        return
    if not search_due(decision):
        print("Broad opportunity scan skipped: search cadence not due.")
        return

    # Optional candidate evidence is processed only after active-trade and
    # pending-setup protection has completed, and only immediately before a
    # due native scan. The small bounded batch and Kraken timeout cap prevent
    # the bridge from delaying risk protection or monopolizing a cycle.
    settings = get_settings()
    if settings.tradingview_v2_enabled:
        try:
            from app.services.tradingview_inbox import process_queued_events

            tradingview_summary = process_queued_events()
        except Exception as exc:
            tradingview_summary = {"status": "UNAVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}
            print("TradingView v2 inbox processing failed open:", tradingview_summary["reason"])
        else:
            print("OHM TradingView v2 Inbox")
            print("Checked:", tradingview_summary.get("checked", 0))
            print("Processed:", tradingview_summary.get("processed", 0))
            print("Qualified:", tradingview_summary.get("qualified", 0))
            print("Rejected:", tradingview_summary.get("rejected", 0))
            print("Retried:", tradingview_summary.get("retried", 0))
            print("Poisoned:", tradingview_summary.get("poisoned", 0))

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
