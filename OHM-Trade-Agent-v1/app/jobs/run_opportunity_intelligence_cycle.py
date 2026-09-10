"""Run bounded forward-outcome maturation plus opportunity accountability.

This job executes only on the isolated learning worker. Both stages are
measurement-only and have no network access or trading authority.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.jobs.build_discovery_forward_outcomes import (
    build_discovery_outcomes_bounded,
)
from app.jobs.build_phase3c_forward_outcomes import (
    acknowledge_accountability_outcomes,
    advance_accountability_handoff_backfill,
    build_outcomes_bounded,
    pending_accountability_outcomes,
)
from app.opip.learning.job_disposition import (
    CONSUMED_EMPTY,
    CONSUMED_OK,
    write_consumption_summary,
)
from app.opip.learning.replica_archive_repair import (
    reconcile_qualification_replica_archives,
)
from app.services.opportunity_accountability import (
    build_incremental_from_outcomes,
    resolved_accountability_outcomes,
)

# Learning worker mounts DATA_ROOT at /app/data.
_DEFAULT_DATA_ROOT = Path("/app/data")


def _replica_archive_repair_enabled() -> bool:
    return os.getenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def main() -> None:
    data_root = _DEFAULT_DATA_ROOT
    replica_archive_repair: dict[str, str] = {}
    if _replica_archive_repair_enabled() and data_root.is_dir():
        # The production export remains copy-only. Repair only the isolated
        # replica after sync has validated the complete exported archive tree.
        replica_archive_repair = reconcile_qualification_replica_archives(data_root)

    # Exactly one bounded legacy handoff migration batch per logical cycle.
    # Must not run inside every SQLite open (pending/ack/maturation), or
    # historical enqueue outpaces terminal retirement under a coverage epoch.
    # Invalid coverage epoch fails closed, but deferred until after pending
    # drain + durable consumption summary so operational evidence remains.
    handoff_backfill: dict = {}
    backfill_error: Exception | None = None
    try:
        handoff_backfill = advance_accountability_handoff_backfill(
            data_root=data_root if data_root.is_dir() else None,
        )
    except Exception as exc:
        backfill_error = exc
        handoff_backfill = {
            "error": str(exc),
            "batch_rows": 0,
            "enqueued_handoff": 0,
            "terminalized_coverage_discontinuity": 0,
            "skipped_without_cursor": 0,
            "complete": False,
            "already_complete": False,
        }

    # Drain any durable handoff left by an interrupted prior cycle before
    # maturing more snapshots. This bounds backlog growth and gives
    # accountability at-least-once delivery semantics.
    outcomes = pending_accountability_outcomes()
    replayed_handoff = bool(outcomes)
    newly_evaluated = 0
    if not outcomes:
        evaluated = build_outcomes_bounded()
        newly_evaluated = len(evaluated)
        outcomes = pending_accountability_outcomes()

    # A failed accountability replay remains unacknowledged, but it must not
    # starve current outcome maturation. Preserve the accountability exception
    # and run one bounded current maturation pass before re-raising it.
    summary = {}
    resolved = []
    acknowledged = 0
    accountability_error: Exception | None = None
    try:
        summary = build_incremental_from_outcomes(outcomes, replica_mode=True)
        resolved = resolved_accountability_outcomes(outcomes)
        acknowledged = acknowledge_accountability_outcomes(resolved)
    except Exception as exc:
        accountability_error = exc

    if replayed_handoff:
        try:
            evaluated = build_outcomes_bounded()
            newly_evaluated += len(evaluated)
        except Exception as maturation_error:
            if accountability_error is not None:
                raise accountability_error from maturation_error
            raise

    discovery_summary: dict = {}
    try:
        discovery_summary = build_discovery_outcomes_bounded(
            screening_path=data_root / "opip" / "qualification" / "screening_evaluations.jsonl",
            observation_path=data_root / "full_market_observations.jsonl",
            output_dir=data_root / "opip" / "discovery",
        )
    except Exception as exc:
        discovery_summary = {
            "error": str(exc),
            "evaluated": 0,
            "measurement_only": True,
            "trade_authority_changed": False,
        }

    pending_after = pending_accountability_outcomes()
    terminalized_backfill = int(
        (handoff_backfill or {}).get("terminalized_coverage_discontinuity") or 0
    )
    # Retirement-only cycles that persist UNRESOLVED_COVERAGE_DISCONTINUITY via
    # bounded backfill are real consumption work, not empty no-ops.
    discovery_evaluated = int((discovery_summary or {}).get("evaluated") or 0)
    empty = (
        newly_evaluated == 0
        and not outcomes
        and not pending_after
        and terminalized_backfill == 0
        and backfill_error is None
        and discovery_evaluated == 0
    )
    disposition = CONSUMED_EMPTY if empty else CONSUMED_OK
    payload = {
        "status": "OK" if backfill_error is None and accountability_error is None else "ERROR",
        "new_outcomes_evaluated": newly_evaluated,
        "discovery_outcomes": discovery_summary,
        "accountability_handoff_rows": len(outcomes),
        "accountability_handoff_resolved": len(resolved),
        "accountability_handoff_acknowledged": acknowledged,
        "accountability_pending_count": len(pending_after),
        "accountability_handoff_backfill": handoff_backfill,
        "replayed_handoff": replayed_handoff,
        "replica_archive_repair": replica_archive_repair,
        "population": summary.get("population", {}),
        "opportunity_capture_rate_pct": summary.get(
            "opportunity_capture_rate_pct"
        ),
        "measurement_only": True,
        "trade_authority_changed": False,
        "policy_change_authorized": False,
    }
    if data_root.is_dir():
        write_consumption_summary(
            data_root, job="outcomes", disposition=disposition, payload=payload
        )

    print(json.dumps(payload, sort_keys=True))

    if backfill_error is not None:
        if accountability_error is not None:
            raise backfill_error from accountability_error
        raise backfill_error
    if accountability_error is not None:
        raise accountability_error


if __name__ == "__main__":
    main()
