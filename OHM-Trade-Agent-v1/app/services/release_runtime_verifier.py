"""Bounded, read-only verifier for an allowlisted active release profile."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.opip.canonical.paths import db_path
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.features import FeatureSnapshot
from app.opip.fev_evidence_event import reconstruct_feasibility_evidence_recorded_payload
from app.opip.features.committed_snapshot_reader import feature_snapshot_from_payload
from app.services.release_profiles import resolve_release_profile

MAX_WAIT_SECONDS = 360
POLL_INTERVAL_SECONDS = 10
MAX_EVIDENCE_AGE = timedelta(seconds=180)
MAX_FEV_SOURCE_AGE = timedelta(seconds=120)
REQUIRED_MODES = (
    "OPIP_FEATURE_BUS_MODE",
    "OPIP_CANONICAL_WRITER_MODE",
    "OPIP_TARGET_SPINE_MODE",
    "OPIP_PAPER_V2_MODE",
    "OPIP_COMMITTEE_MODE",
)


def _aware_utc(value: str, *, name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _cursor(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(part, int) or isinstance(part, bool) for part in value)
    ):
        raise ValueError("baseline cursors must be null or integer pairs")
    return int(value[0]), int(value[1])


def _new_evidence_is_valid(
    snapshots: Sequence[FeatureSnapshot],
    evidence_records: Sequence[Any],
    *,
    ready_after: datetime,
    now: datetime,
) -> tuple[bool, dict[str, Any]]:
    """Require fresh consecutive snapshots and matching prospective F5 evidence."""
    eligible: dict[str, list[FeatureSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        cutoff = snapshot.evaluation_cutoff.astimezone(timezone.utc)
        if cutoff <= ready_after or cutoff > now or now - cutoff > MAX_EVIDENCE_AGE:
            continue
        eligible[snapshot.instrument_version_id].append(snapshot)

    cadence_pair: tuple[FeatureSnapshot, FeatureSnapshot] | None = None
    for rows in eligible.values():
        ordered = sorted(rows, key=lambda item: item.evaluation_cutoff)
        for older, newer in zip(ordered, ordered[1:]):
            gap = newer.evaluation_cutoff - older.evaluation_cutoff
            if (
                gap == timedelta(seconds=older.evaluation_grid_seconds)
                and older.evaluation_grid_seconds == newer.evaluation_grid_seconds
            ):
                cadence_pair = (older, newer)
                break
        if cadence_pair is not None:
            break

    paired_snapshot_ids = (
        {
            cadence_pair[0].snapshot_id,
            cadence_pair[1].snapshot_id,
        }
        if cadence_pair is not None
        else set()
    )
    matched_fev = False
    for evidence in evidence_records:
        evaluation_time = evidence.evaluation_time.astimezone(timezone.utc)
        source_cutoff = evidence.source_cutoff.astimezone(timezone.utc)
        if (
            evidence.instrument_version_id
            and source_cutoff <= evaluation_time
            and source_cutoff <= now
            and now - source_cutoff <= MAX_FEV_SOURCE_AGE
            and any(
                snapshot.snapshot_id in paired_snapshot_ids
                and snapshot.instrument_version_id == evidence.instrument_version_id
                and snapshot.evaluation_cutoff == evaluation_time
                for rows in eligible.values()
                for snapshot in rows
            )
        ):
            matched_fev = True
            break

    passed = cadence_pair is not None and matched_fev
    return passed, {
        "feature_snapshot_count": len(snapshots),
        "fresh_instrument_count": len(eligible),
        "consecutive_60s_snapshots": cadence_pair is not None,
        "feasibility_evidence_count": len(evidence_records),
        "feasibility_matches_fresh_snapshot": matched_fev,
    }


def _read_new_evidence(
    baseline: Mapping[str, Any],
) -> tuple[list[FeatureSnapshot], list[Any]]:
    feature_cursor = _cursor(baseline.get("feature_snapshot_cursor"))
    fev_cursor = _cursor(baseline.get("feasibility_evidence_cursor"))
    reader = CanonicalWriter.for_reads(db_path())
    try:
        feature_rows, _ = reader.read_feature_snapshot_rows(after=feature_cursor, limit=1000)
        fev_rows, _ = reader.read_feasibility_evidence_records(after=fev_cursor, limit=1000)
    finally:
        reader.close()

    snapshots: list[FeatureSnapshot] = []
    for row in feature_rows:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("feature snapshot payload is not an object")
        snapshots.append(feature_snapshot_from_payload(payload))

    evidence: list[Any] = []
    for raw in fev_rows:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("feasibility evidence payload is not an object")
        _, record = reconstruct_feasibility_evidence_recorded_payload(payload)
        evidence.append(record)
    return snapshots, evidence


def _verify_live_posture(profile_name: str) -> dict[str, Any]:
    profile = resolve_release_profile(profile_name)
    if profile.get("status") != "ACTIVE":
        raise ValueError("release profile is not active")
    expected = profile["allowed_modes"]
    observed = {key: os.environ.get(key, "") for key in REQUIRED_MODES}
    if any(observed[key] != expected[key] for key in REQUIRED_MODES):
        raise ValueError("runtime modes do not match the allowlisted profile")

    from app.services import target_spine_cycle
    from app.core.config import get_settings

    settings = get_settings()
    summary = target_spine_cycle.run_target_spine_cycle(settings=settings)
    expected_spine = expected["OPIP_TARGET_SPINE_MODE"]
    expected_reason = (
        target_spine_cycle.REASON_NO_SNAPSHOT_SOURCE
        if expected_spine == "shadow"
        else target_spine_cycle.REASON_MODE_OFF
    )
    if (
        summary.mode != expected_spine
        or not summary.inert
        or summary.handoffs_built != 0
        or summary.errors != 0
        or summary.reason != expected_reason
    ):
        raise ValueError("target spine is not a recorded inert no-op for the release profile")

    from app.jobs.report_protection_health import build_report

    protection = build_report()
    if protection.get("state") != "HEALTHY" or protection.get("admissions_suspended") is not False:
        raise ValueError("read-only protection health is not HEALTHY")
    return {
        "profile": profile_name,
        "modes": observed,
        "protection": "HEALTHY",
        "target_authority": "ABSENT",
    }


def verify_release_runtime(
    *,
    expected_sha: str,
    baseline: Mapping[str, Any],
    ready_after: datetime,
    timeout_seconds: int = MAX_WAIT_SECONDS,
) -> dict[str, Any]:
    """Verify the selected active profile, bounded by six minutes."""
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("expected SHA must be a full lowercase commit SHA")
    if timeout_seconds < 1 or timeout_seconds > MAX_WAIT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 1 and {MAX_WAIT_SECONDS}")
    if ready_after.tzinfo is None or ready_after.utcoffset() is None:
        raise ValueError("ready_after must be timezone-aware")
    if not isinstance(baseline, Mapping):
        raise ValueError("baseline must be an object")
    profile_name = os.environ.get("OPIP_RELEASE_PROFILE", "")
    profile = resolve_release_profile(profile_name)
    if profile.get("status") != "ACTIVE":
        raise ValueError("release profile is not active")

    if profile_name != "EVIDENCE_SHADOW":
        raise ValueError("runtime verification permits only EVIDENCE_SHADOW; SAFE_BASELINE is rollback-only")

    deadline = time.monotonic() + timeout_seconds
    while True:
        snapshots, evidence = _read_new_evidence(baseline)
        now = datetime.now(timezone.utc)
        evidence_passed, evidence_report = _new_evidence_is_valid(
            snapshots,
            evidence,
            ready_after=ready_after.astimezone(timezone.utc),
            now=now,
        )
        if evidence_passed:
            posture = _verify_live_posture(profile_name)
            return {
                "status": "PASS",
                "sha": expected_sha,
                "verified_at": now.isoformat().replace("+00:00", "Z"),
                "evidence": evidence_report,
                **posture,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("fresh consecutive snapshots and matching F5 evidence were not proven")
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--baseline-json", required=True)
    parser.add_argument("--ready-after", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=MAX_WAIT_SECONDS)
    args = parser.parse_args()
    try:
        baseline = json.loads(args.baseline_json)
        if not isinstance(baseline, dict):
            raise ValueError("baseline JSON must be an object")
        result = verify_release_runtime(
            expected_sha=args.expected_sha,
            baseline=baseline,
            ready_after=_aware_utc(args.ready_after, name="ready-after"),
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, RuntimeError, TypeError, ValueError, TimeoutError) as exc:
        print("OPIP_RELEASE_RUNTIME_VERIFICATION=FAIL")
        print(f"OPIP_RELEASE_RUNTIME_FAILURE={type(exc).__name__}")
        sys.exit(1)
    print("OPIP_RELEASE_RUNTIME_VERIFICATION=PASS")
    print(f"OPIP_RELEASE_RUNTIME_SHA={result['sha']}")
    print(f"OPIP_RELEASE_RUNTIME_VERIFIED_AT={result['verified_at']}")
    print(f"OPIP_RELEASE_PROFILE={result['profile']}")
    print(f"OPIP_RELEASE_EVIDENCE_CAPTURE={result['evidence'].get('evidence_capture', 'PASS')}")
    print(f"OPIP_RELEASE_PROTECTION={result['protection']}")
    print(f"OPIP_RELEASE_TARGET_AUTHORITY={result['target_authority']}")
    for key, value in sorted(result["evidence"].items()):
        print(f"OPIP_RELEASE_{key.upper()}={value}")


if __name__ == "__main__":  # pragma: no cover
    main()
