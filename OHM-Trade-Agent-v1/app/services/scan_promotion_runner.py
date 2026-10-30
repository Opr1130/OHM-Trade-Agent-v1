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
from app.services.scan_supervisor import state_dir, store_for


QUALIFY_LOCK_NAME = ".opip_qualify.lock"

STATUS_NONE = "NONE"
STATUS_COMPLETED = "COMPLETED"
STATUS_REJECTED = "REJECTED"
STATUS_NOOP = "NOOP"


def _utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def qualify_lock_path(settings: Any) -> Path:
    return Path(state_dir(settings)).parent / QUALIFY_LOCK_NAME


def pending_attempt(settings: Any, store: ScanAttemptStore) -> Any | None:
    """Return the attempt awaiting a promotion decision, if any."""
    latest = store.latest()
    if latest is None or not latest.terminal:
        return None
    if latest.outcome not in {"COMPLETED", "PARTIAL"}:
        return None
    decision = read_promotion(settings, latest.attempt_id)
    if decision is not None and str(decision.get("state") or "") in {
        "COMPLETED",
        "REJECTED",
    }:
        return None
    return latest


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
    attempt = pending_attempt(settings, store)
    if attempt is None:
        return {"status": STATUS_NONE}

    def _reject(reason: str) -> dict[str, Any]:
        reject_promotion(settings, attempt.attempt_id, reason_code=reason, now=moment)
        return {"status": STATUS_REJECTED, "reason_code": reason}

    if attempt.outcome == "PARTIAL":
        return _reject("PARTIAL_SCAN_NOT_ACTIONABLE")

    allowed, reason = revalidate_policy(settings, now=moment)
    if not allowed:
        return _reject(reason)

    claimed, claim_reason = claim_promotion(settings, attempt.attempt_id, now=moment)
    if not claimed:
        return {"status": STATUS_NOOP, "reason_code": claim_reason}

    def _work() -> dict[str, Any]:
        try:
            promoted = load_promoted(settings=settings, attempt=attempt, now=moment)
        except PromotionRejected as exc:
            reject_promotion(
                settings, attempt.attempt_id, reason_code=exc.reason, now=moment
            )
            return {"status": STATUS_REJECTED, "reason_code": exc.reason}

        from app.services.scan_result_promotion import to_scan_result

        runner = qualify or _default_qualify
        runner(to_scan_result(promoted))
        complete_promotion(
            settings,
            attempt.attempt_id,
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
