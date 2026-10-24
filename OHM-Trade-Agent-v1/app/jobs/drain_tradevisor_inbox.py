"""Drain the Tradevisor advisory inbox (operator/scheduler entry point).

Non-authoritative: this command evaluates received Tradevisor alerts against
existing O'Pip owners and may deliver advisory alerts. It never places,
confirms, cancels or sizes an order, and it admits nothing into native
selection. It is inert unless ``OPIP_TRADEVISOR_ENABLED`` is true.
"""
from app.core.config import get_settings
from app.services.tradevisor_inbox import pending_count, process_queued_events
from app.services.tradevisor_observation import (
    observations_enabled,
    pending_observations,
    send_observation_digest,
)
from app.services.tradevisor_pipeline import evaluate_and_notify

MAX_BATCHES_PER_RUN = 4


def main() -> None:
    settings = get_settings()
    print("===== OHM TRADEVISOR V1 ADVISORY DRAIN =====")
    print("Configured:", bool(getattr(settings, "opip_tradevisor_enabled", False)))
    if not bool(getattr(settings, "opip_tradevisor_enabled", False)):
        print("Ingress disabled; nothing to do.")
        print("Trade authority changed: False")
        return

    total = {
        "checked": 0,
        "processed": 0,
        "rejected": 0,
        "retried": 0,
        "poisoned": 0,
        "reclaimed": 0,
        "qualified": 0,
    }
    for _ in range(MAX_BATCHES_PER_RUN):
        result = process_queued_events(
            evaluator=lambda signal: evaluate_and_notify(signal, settings=settings)
        )
        for key in total:
            total[key] += int(result.get(key, 0) or 0)
        if int(result.get("checked", 0) or 0) == 0:
            break
    for key, value in total.items():
        print(f"{key}: {value}")
    # Advisory observations are re-derived from durable rows, so a crash or a
    # refused send between settlement and delivery is recoverable next run. The
    # recovery scan is skipped entirely while the observation class is off.
    if observations_enabled(settings):
        digest = send_observation_digest(
            pending_observations(settings=settings), settings=settings
        )
        print(
            f"observations: {digest.status} ({digest.observed} observed, "
            f"{digest.suppressed_bounded} bounded)"
        )
    else:
        print("observations: DISABLED (observation class off; no inbox scan)")
    print("Pending after run:", pending_count())
    print("Trade authority changed: False")
    print("Execution authority: False")


if __name__ == "__main__":
    main()
