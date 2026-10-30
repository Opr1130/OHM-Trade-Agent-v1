"""Promotion plus qualification, executed outside the unified-cycle lock.

Qualification includes AI review, market validation and notification delivery.
Those are potentially long-running and must never be able to delay the next
protection cycle, so they run as a detached job that holds its own lock. The
cycle's only remaining involvement is that it authorises an acquisition attempt.

The job is idempotent and crash-recoverable: a durable claim is written before
any work, a completed claim suppresses a second business outcome, and an expired
claim (its owner died) may be retaken exactly once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.registry_io import registry_lock
from app.services.scan_attempt import ScanAttemptStore
from app.services.scan_result_promotion import (
    PromotionRejected,
    claim_promotion,
    complete_promotion,
    load_promoted,
    read_promotion,
    reject_promotion,
    revalidate_policy,
)
from app.services.scan_supervisor import (
    MODE_ISOLATED,
    execution_mode,
    state_dir,
    store_for,
)


QUALIFY_LOCK_NAME = ".opip_qualify.lock"

STATUS_NONE = "NONE"
STATUS_COMPLETED = "COMPLETED"
STATUS_REJECTED = "REJECTED"
STATUS_NOOP = "NOOP"
STATUS_BLOCKED = "BLOCKED"


def _utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def qualify_lock_path(settings: Any) -> Path:
    return Path(state_dir(settings)).parent / QUALIFY_LOCK_NAME


def pending_attempt(settings: Any, store: ScanAttemptStore) -> Any | None:
    """Return the oldest consumable attempt awaiting a promotion decision.

    Deliberately not "the latest attempt": a completed scan can be followed by a
    RESERVED, RUNNING or SKIPPED record, and its result must still be consumed.
    """
    for attempt in store.pending_results():
        decision = read_promotion(settings, attempt.attempt_id)
        if decision is not None and str(decision.get("state") or "") in {
            "COMPLETED",
            "REJECTED",
        }:
            continue
        return attempt
    return None


def core_headroom_ok(settings: Any) -> tuple[bool, str]:
    """Refuse to start qualification when the core cgroup is already tight.

    Qualification shares the protection container's cgroup. Starting a heavy AI
    review and notification burst when that cgroup is near its ceiling would put
    protection itself at risk, so it is deferred to a later tick instead.
    """
    from app.services.scan_preflight import collect_snapshot

    try:
        snapshot = collect_snapshot(data_dir=state_dir(settings).parent)
    except Exception:  # noqa: BLE001 - unreadable headroom is not permission
        return False, "CORE_HEADROOM_UNKNOWN"
    utilisation = snapshot.core_utilization_pct
    ceiling = float(getattr(settings, "opip_scan_core_memory_ceiling_pct", 70.0))
    if utilisation is None:
        return False, "CORE_HEADROOM_UNKNOWN"
    if utilisation > ceiling:
        return False, "CORE_CGROUP_PRESSURE"
    return True, "OK"


def _default_qualify(scan_result: Any) -> Any:
    from app.jobs.scan_opportunities import main as scan_main
    from app.services.operations_analytics import run_scan_with_telemetry

    return run_scan_with_telemetry(lambda: scan_main(scan_override=scan_result))


def run_pending_promotion(
    settings: Any,
    *,
    now: datetime | None = None,
    qualify: Callable[[Any], Any] | None = None,
    hold_lock: bool = True,
) -> dict[str, Any]:
    """Promote and qualify one acquired scan, at most once per attempt."""
    moment = _utc(now)
    store = store_for(settings)
    if execution_mode(settings) != MODE_ISOLATED:
        # Isolated execution is disabled: there is nothing to promote, and the
        # detached job must not be doing work in that configuration.
        return {"status": STATUS_NONE, "reason_code": "ISOLATED_DISABLED"}

    attempt = pending_attempt(settings, store)
    if attempt is None:
        return {"status": STATUS_NONE}

    def _reject(reason: str, token: str | None = None) -> dict[str, Any]:
        reject_promotion(
            settings,
            attempt.attempt_id,
            reason_code=reason,
            claim_token=token,
            now=moment,
        )
        return {"status": STATUS_REJECTED, "reason_code": reason}

    if attempt.outcome == "PARTIAL":
        return _reject("PARTIAL_SCAN_NOT_ACTIONABLE")

    allowed, reason = revalidate_policy(settings, now=moment)
    if not allowed:
        return _reject(reason)

    room, room_reason = core_headroom_ok(settings)
    if not room:
        # Defer without claiming, so the attempt stays eligible next tick and no
        # lease is burned by a qualification we never started.
        return {"status": STATUS_BLOCKED, "reason_code": room_reason}

    claimed, claim_reason, token = claim_promotion(settings, attempt.attempt_id, now=moment)
    if not claimed:
        return {"status": STATUS_NOOP, "reason_code": claim_reason}

    def _work() -> dict[str, Any]:
        try:
            promoted = load_promoted(settings=settings, attempt=attempt, now=moment)
        except PromotionRejected as exc:
            return _reject(exc.reason, token)

        from app.services.scan_result_promotion import to_scan_result

        runner = qualify or _default_qualify
        runner(to_scan_result(promoted))
        complete_promotion(
            settings,
            attempt.attempt_id,
            claim_token=token,
            degraded_fields=promoted.degraded_fields,
            now=moment,
        )
        return {
            "status": STATUS_COMPLETED,
            "attempt_id": attempt.attempt_id,
            "snapshots": len(promoted.snapshots),
            "degraded_fields": list(promoted.degraded_fields),
            "claim": claim_reason,
        }

    if not hold_lock:
        return _work()
    with registry_lock(qualify_lock_path(settings)):
        return _work()


def main() -> int:
    """CLI entry point for the detached qualification stage."""
    from app.core.config import get_settings

    settings = get_settings()
    result = run_pending_promotion(settings)
    print(
        "O'Pip promotion:",
        result.get("status"),
        result.get("attempt_id", "-"),
        result.get("reason_code", "-"),
    )
    return 0 if result.get("status") in {STATUS_NONE, STATUS_COMPLETED, STATUS_NOOP} else 0


if __name__ == "__main__":
    raise SystemExit(main())
