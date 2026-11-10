"""Filesystem adapter for the F11 protection admission stamp.

Why this module exists
----------------------
``app.services.protection_health`` is the F11 evaluator and must stay **pure and
read-only**: the F11 pre-cutover contract asserts that it contains no write
surface at all. Publication therefore lives here, in a narrowly scoped I/O
adapter, so the evaluator's purity contract is preserved rather than weakened.

What it may do
--------------
* read a bounded history of *successful* observation timestamps;
* write the stamp and the history atomically (temp file + ``os.replace``).

What it may not do
------------------
* it never evaluates protection, never reads exposures, never observes the
  incident store and never decides suspension. It receives F11's verdict as
  arguments and only serialises it.
* the published stamp cannot grant authority F11 did not grant: it can only ever
  refuse, and consumers fail closed on a missing, stale, corrupt or unproven
  document.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app.services.protection_health import (
    STAMP_ABSOLUTE_CEILING_SECONDS,
    STAMP_HISTORY_LIMIT,
    build_admission_stamp_payload,
    derive_freshness_bound,
)

STAMP_MAX_FRESHNESS_ENV = "OPIP_PROTECTION_STAMP_MAX_FRESHNESS_SECONDS"
STAMP_DEFAULT_MAX_FRESHNESS_SECONDS = 300.0


def load_stamp_history(path: Path) -> list[datetime]:
    """Read the bounded history ring. A corrupt file yields an empty ring."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    moments: list[datetime] = []
    for value in (raw.get("observations") or [])[-STAMP_HISTORY_LIMIT:]:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            continue
        if moment.tzinfo is None or moment.utcoffset() is None:
            continue
        moments.append(moment.astimezone(timezone.utc))
    return moments


def atomic_write_json(path: Path, payload: Any) -> None:
    """Publish atomically so a reader never observes a partial document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)


def publish_admission_stamp(
    *,
    stamp_path: Path,
    history_path: Path,
    release_sha: str,
    policy_version: str,
    observed_at: datetime,
    admissions_suspended: bool,
    coverage_complete: bool | None,
    incidents_healthy: bool | None,
    reasons: Sequence[str],
    max_freshness_seconds: float | None = None,
) -> dict[str, Any]:
    """Publish F11's verdict. The configured maximum is the safety authority.

    The derived p99 may only TIGHTEN that maximum, never widen it, so a small
    sample cannot silently extend the window in which a stale verdict looks
    authoritative. While fewer than ``STAMP_MIN_SAMPLES`` successful
    observations exist the stamp advertises the real (insufficient) count so
    every consumer fails closed.
    """
    limit = float(
        max_freshness_seconds
        if max_freshness_seconds is not None
        else STAMP_DEFAULT_MAX_FRESHNESS_SECONDS
    )
    if not math.isfinite(limit) or limit <= 0:
        limit = STAMP_DEFAULT_MAX_FRESHNESS_SECONDS
    limit = min(limit, STAMP_ABSOLUTE_CEILING_SECONDS)

    history = load_stamp_history(history_path)
    successful = (
        coverage_complete is True and incidents_healthy is True and not reasons
    )
    if successful:
        history.append(observed_at.astimezone(timezone.utc))
    history = sorted(history)[-STAMP_HISTORY_LIMIT:]

    derived = derive_freshness_bound(history)
    bound = limit if derived is None else min(limit, derived)

    payload = build_admission_stamp_payload(
        release_sha=release_sha,
        policy_version=policy_version,
        observed_at=observed_at,
        sequence=_next_sequence(history),
        admissions_suspended=admissions_suspended,
        coverage_complete=coverage_complete,
        incidents_healthy=incidents_healthy,
        reasons=reasons,
        freshness_bound_s=bound,
        sample_count=len(history),
    )
    atomic_write_json(stamp_path, payload)
    atomic_write_json(
        history_path,
        {"observations": [moment.isoformat() for moment in history]},
    )
    return payload


def _next_sequence(history: Sequence[datetime]) -> int:
    """Monotonic replay protection derived from the observation ring."""
    if not history:
        return 1
    return int(history[-1].timestamp() * 1000)


__all__ = [
    "STAMP_DEFAULT_MAX_FRESHNESS_SECONDS",
    "STAMP_MAX_FRESHNESS_ENV",
    "atomic_write_json",
    "load_stamp_history",
    "publish_admission_stamp",
]
