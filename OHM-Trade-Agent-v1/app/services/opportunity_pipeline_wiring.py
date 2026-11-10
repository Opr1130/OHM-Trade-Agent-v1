"""Flag-guarded producer wiring for the liquidity/evidence pipeline.

Every entry point here is **default-off** and **exception-safe**: with its flag
unset it does nothing at all, and when enabled a failure is reported and
swallowed so it can never break a scan or a protection cycle.

Deliberate boundaries
---------------------
* This module never decides admission, never sends a notification and never
  changes a verdict. It records evidence and annotations only.
* The F11 stamp is published by the protection phase, not here: the evaluator
  keeps sole observation authority, and consumers read the result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app.services import ai_advisory_isolation as advisory
from app.services import opportunity_evidence as evidence

DEFAULT_EVIDENCE_PATH = Path("/app/data/opportunity_evidence.jsonl")


def evidence_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "opip_opportunity_evidence_enabled", False))


def advisory_isolation_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "opip_ai_advisory_isolation_enabled", False))


def protectin_stamp_publish_enabled(settings: Any) -> bool:
    return bool(
        getattr(settings, "opip_protection_stamp_publish_enabled", False)
    )


def evidence_path(settings: Any) -> Path:
    configured = getattr(settings, "opip_opportunity_evidence_path", None)
    return Path(configured) if configured else DEFAULT_EVIDENCE_PATH


def record_finalist_evidence(
    settings: Any,
    *,
    evidence_path_override: Path | None = None,
    **fields: Any,
) -> bool:
    """Append one point-in-time record when the flag is on. Returns True if written."""
    if not evidence_enabled(settings):
        return False
    fields.setdefault("observed_at_utc", datetime.now(timezone.utc).isoformat())
    try:
        record = evidence.OpportunityEvidence(**fields)
        return evidence.append_record(
            evidence_path_override or evidence_path(settings), record
        )
    except Exception as exc:  # never break a scan for evidence capture
        print(
            "O'Pip opportunity evidence capture skipped:",
            f"{type(exc).__name__}: {exc}",
        )
        return False


def annotate_candidates(
    settings: Any,
    candidates: Sequence[Any],
    *,
    ai_status: str,
    recommendation: str | None = None,
    confidence_score: float | None = None,
) -> tuple[Any, ...]:
    """Attach an advisory annotation without ever filtering a candidate.

    With the flag off the candidates are returned unchanged, so notification
    behaviour is untouched; even with the flag on, nothing is dropped and no
    delivery decision is made here.
    """
    if not advisory_isolation_enabled(settings):
        return tuple(candidates)
    annotation = advisory.annotation_for_status(
        ai_status,
        recommendation=recommendation,
        confidence_score=confidence_score,
    )
    return advisory.attach_advisory(candidates, annotation)


def publish_stamp_if_enabled(
    settings: Any,
    *,
    release_sha: str,
    policy_version: str,
    observed_at: datetime,
    admissions_suspended: bool,
    coverage_complete: bool | None,
    incidents_healthy: bool | None,
    reasons: Sequence[str],
    stamp_path: Path | None = None,
    history_path: Path | None = None,
) -> dict[str, Any] | None:
    """Publish F11's verdict from the authoritative protection phase only."""
    if not protectin_stamp_publish_enabled(settings):
        return None
    from app.services import protection_admission_stamp_io as stamp_io

    try:
        return stamp_io.publish_admission_stamp(
            stamp_path=stamp_path
            or Path(
                getattr(settings, "opip_protection_stamp_path",
                        "/app/data/protection_admission_stamp.json")
            ),
            history_path=history_path
            or Path(
                getattr(settings, "opip_protection_stamp_history_path",
                        "/app/data/protection_admission_stamp_history.json")
            ),
            release_sha=release_sha,
            policy_version=policy_version,
            observed_at=observed_at,
            admissions_suspended=admissions_suspended,
            coverage_complete=coverage_complete,
            incidents_healthy=incidents_healthy,
            reasons=reasons,
            max_freshness_seconds=getattr(
                settings, "opip_protection_stamp_max_freshness_seconds", None
            ),
        )
    except Exception as exc:
        print(
            "O'Pip protection stamp publication skipped:",
            f"{type(exc).__name__}: {exc}",
        )
        return None


__all__ = [
    "DEFAULT_EVIDENCE_PATH",
    "annotate_candidates",
    "advisory_isolation_enabled",
    "evidence_enabled",
    "evidence_path",
    "protectin_stamp_publish_enabled",
    "publish_stamp_if_enabled",
    "record_finalist_evidence",
]
