"""Bounded, read-only verifier for an allowlisted active release profile.

Bounded-window contract
-----------------------
``MAX_WAIT_SECONDS`` is the verifier's OWN window and is deliberately not
raised. Three rules keep the process inside its own window plus at most ONE
in-flight read:

* the deadline is tested BEFORE every read attempt, so the loop can never start
  a read with no remaining budget (the pre-fix bug: the deadline was tested only
  AFTER a read, so the loop always performed one more full read and slept up to a
  whole poll interval past the deadline);
* the sleep between attempts is clamped to the remaining budget, so the loop
  cannot overshoot by a poll interval;
* a read that exceeds the DECLARED worst-case single-read bound
  (``MAX_SINGLE_READ_SECONDS``) stops the loop immediately with a structured
  ``READ_OVERRUN`` receipt instead of polling again on top of an already
  anomalous read.

Those rules give the enforceable bound

    total wall time <= MAX_WAIT_SECONDS + MAX_SINGLE_READ_SECONDS

because at most one read can be in flight when the deadline passes.
``MAX_SINGLE_READ_SECONDS`` is the documented bound of one bounded canonical
read: a read-only connection over a local SQLite/WAL file, two indexed range
reads of at most 1000 rows, and JSON parsing of those rows. The deploy's outer
containment MUST exceed ``MAX_WAIT_SECONDS + MAX_SINGLE_READ_SECONDS`` by a
margin that also covers container-exec startup, interpreter import and receipt
flush, so the outer watchdog is emergency containment only and never the normal
timeout mechanism.

A normal "matching evidence never arrived" outcome therefore terminates under
the verifier's own control and emits a structured receipt
(``OPIP_RELEASE_RUNTIME_VERIFICATION=FAIL`` plus the last observed evidence
counters).

Read path
---------
The verifier is a canonical-store CONSUMER. ``_read_new_evidence`` uses
``CanonicalWriter.for_reads`` (read-only connection, no store lock), so it never
contends with ``opip-canonical-writer``, the sole writable owner.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
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
REQUIRED_SNAPSHOT_GRID_SECONDS = 60

#: Worst-case wall time that one already-started bounded canonical read can add
#: AFTER this verifier's own deadline expires: the read-only canonical connection
#: uses a 5.0s SQLite timeout and scans a local file with no network. The deploy's
#: outer containment margin must exceed this (plus container-exec startup,
#: interpreter import and receipt flush), so an evidence deficiency surfaces as
#: this verifier's structured FAIL and never as the outer watchdog's ``124``.
#: Enforced by the loop's ``READ_OVERRUN`` guard; ``main()`` is verified against
#: the deploy's outer timeout by the AC-015 receipt/containment tests.
MAX_SINGLE_READ_SECONDS = 10.0

#: The evidence counters that make a FAIL receipt actionable on its own. Each is
#: printed as ``OPIP_RELEASE_<UPPER_SNAKE>=<value>``.
FAILURE_EVIDENCE_KEYS = (
    "feature_snapshot_count",
    "fresh_instrument_count",
    "consecutive_60s_snapshots",
    "feasibility_evidence_count",
    "feasibility_matches_fresh_snapshot",
)

REQUIRED_MODES = (
    "OPIP_FEATURE_BUS_MODE",
    "OPIP_CANONICAL_WRITER_MODE",
    "OPIP_TARGET_SPINE_MODE",
    "OPIP_PAPER_V2_MODE",
    "OPIP_COMMITTEE_MODE",
    "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD",
)


class ReleaseRuntimeVerificationTimeout(TimeoutError):
    """The bounded window expired without proving fresh evidence.

    Carries the last observed evidence diagnostics so an ordinary evidence
    deficiency always produces a structured, machine-readable FAIL receipt
    instead of a bare timeout (or the outer watchdog's exit ``124``).
    """

    stage = "EVIDENCE_TIMEOUT"

    def __init__(
        self,
        message: str,
        *,
        evidence: Mapping[str, Any] | None = None,
        attempts: int = 0,
        observed_seconds: float = 0.0,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence: dict[str, Any] = dict(evidence or {})
        self.attempts = attempts
        self.observed_seconds = observed_seconds
        if stage is not None:
            self.stage = stage


class ReleaseRuntimePostureError(ValueError):
    """Fresh evidence was proven but the live runtime posture was not.

    Carries the proven evidence counters, a stable stage and the deterministic
    reason codes so a posture failure never collapses into an anonymous
    ``ValueError`` with ``OBSERVED_EVIDENCE=NONE``.
    """

    stage = "LIVE_POSTURE"

    def __init__(
        self,
        message: str,
        *,
        reason_codes: Sequence[str] = (),
        evidence: Mapping[str, Any] | None = None,
        protection_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_codes = tuple(str(code) for code in reason_codes)
        self.evidence: dict[str, Any] = dict(evidence or {})
        self.protection_reason = (
            str(protection_reason) if protection_reason not in (None, "") else None
        )


def _receipt_value(value: Any) -> str:
    """Render a receipt value deterministically (JSON-style booleans)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _single_line_receipt_text(value: Any) -> str:
    """Sanitize diagnostic text for a deterministic single-line receipt."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()


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
                gap == timedelta(seconds=REQUIRED_SNAPSHOT_GRID_SECONDS)
                and older.evaluation_grid_seconds == REQUIRED_SNAPSHOT_GRID_SECONDS
                and newer.evaluation_grid_seconds == REQUIRED_SNAPSHOT_GRID_SECONDS
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
        raise ReleaseRuntimePostureError("runtime modes do not match the allowlisted profile")

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
        raise ReleaseRuntimePostureError(
            "target spine is not a recorded inert no-op for the release profile",
            reason_codes=(str(summary.reason or "NONE"), f"MODE={summary.mode}"),
        )

    from app.jobs.report_protection_health import build_observation

    # ONE read-only observation yields BOTH the strict F11 report and the AC-026
    # EVIDENCE_SHADOW readiness projection, so the runtime verifier cannot
    # reconstruct a second, divergent readiness result.
    observation = build_observation()
    protection = observation.report
    shadow = observation.shadow
    strict_healthy = (
        protection.get("state") == "HEALTHY"
        and protection.get("admissions_suspended") is False
    )

    if profile_name == "EVIDENCE_SHADOW":
        # AC-026: for the EVIDENCE_SHADOW profile the release gate is the explicit
        # readiness decision, NOT strict F11. A durable coverage incident whose
        # canonical current predicate is proven, or an external VERIFIED_UNMANAGED
        # holding, keeps strict F11 non-HEALTHY but is advisory for this profile,
        # so it must never cause a false post-mutation rollback. Every genuine
        # current blocker is still encoded in the readiness blockers and fails.
        if not shadow.ready:
            raise ReleaseRuntimePostureError(
                "EVIDENCE_SHADOW readiness is BLOCKED at runtime",
                reason_codes=(
                    shadow.state,
                    *(str(code) for code in shadow.blocking_reason_codes),
                ),
                protection_reason=protection.get("resolution_reason"),
            )
    elif not strict_healthy:
        # Non-EVIDENCE_SHADOW profiles (for example TARGET_PAPER) keep strict F11
        # as the gate; strict protection semantics are unchanged for them.
        raise ReleaseRuntimePostureError(
            "read-only protection health is not HEALTHY",
            reason_codes=(
                str(protection.get("state")),
                *(str(code) for code in protection.get("reason_codes") or ()),
            ),
            protection_reason=protection.get("resolution_reason"),
        )
    return {
        "profile": profile_name,
        "modes": observed,
        # Strict F11 state is preserved for observability; for EVIDENCE_SHADOW it
        # is reported, not enforced.
        "protection": str(protection.get("state") or "UNAVAILABLE"),
        "protection_ready": strict_healthy,
        "shadow_readiness": shadow.state,
        "shadow_blocking_reason_codes": list(shadow.blocking_reason_codes),
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
    started = time.monotonic()
    attempts = 0
    evidence_report: dict[str, Any] | None = None

    def _window_expired() -> ReleaseRuntimeVerificationTimeout:
        return ReleaseRuntimeVerificationTimeout(
            "fresh consecutive snapshots and matching F5 evidence were not "
            f"proven within {timeout_seconds}s",
            evidence=evidence_report,
            attempts=attempts,
            observed_seconds=time.monotonic() - started,
        )

    while True:
        # The deadline is enforced BEFORE any read is started. This is the
        # fix for the production failure: the old loop tested the deadline only
        # AFTER a read, so it always began one more full read -- and then slept
        # up to a whole poll interval -- past its own window, landing on the
        # deploy's outer watchdog as a bare exit 124.
        if deadline - time.monotonic() <= 0:
            raise _window_expired()
        read_started = time.monotonic()
        snapshots, evidence = _read_new_evidence(baseline)
        read_seconds = time.monotonic() - read_started
        attempts += 1
        now = datetime.now(timezone.utc)
        evidence_passed, evidence_report = _new_evidence_is_valid(
            snapshots,
            evidence,
            ready_after=ready_after.astimezone(timezone.utc),
            now=now,
        )
        if evidence_passed:
            try:
                posture = _verify_live_posture(profile_name)
            except ReleaseRuntimePostureError as exc:
                exc.evidence = dict(evidence_report)
                raise
            return {
                "status": "PASS",
                "sha": expected_sha,
                "verified_at": now.isoformat().replace("+00:00", "Z"),
                "evidence": evidence_report,
                **posture,
            }
        # A read past the declared bound invalidates the containment arithmetic,
        # so stop here with an explicit reason instead of stacking more unbounded
        # work on top of it. Checked only after a PASS, so a slow-but-successful
        # read is never reported as a failure.
        if read_seconds > MAX_SINGLE_READ_SECONDS:
            raise ReleaseRuntimeVerificationTimeout(
                "one canonical read exceeded the declared "
                f"{MAX_SINGLE_READ_SECONDS}s bound "
                f"(observed {read_seconds:.3f}s); the bounded window contract "
                "cannot be honoured",
                evidence=evidence_report,
                attempts=attempts,
                observed_seconds=time.monotonic() - started,
                stage="READ_OVERRUN",
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _window_expired()
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def _emit_failure_diagnostics(exc: BaseException) -> None:
    """Print the deterministic FAIL receipt for a bounded verification failure."""
    print("OPIP_RELEASE_RUNTIME_VERIFICATION=FAIL")
    print(f"OPIP_RELEASE_RUNTIME_FAILURE={type(exc).__name__}")
    stage = getattr(exc, "stage", None)
    if isinstance(stage, str) and stage:
        print(f"OPIP_RELEASE_RUNTIME_FAILURE_STAGE={stage}")
    if isinstance(exc, ReleaseRuntimePostureError):
        print(f"OPIP_RELEASE_RUNTIME_FAILURE_REASON={exc}")
        if exc.reason_codes:
            print(f"OPIP_RELEASE_RUNTIME_FAILURE_CODES={','.join(exc.reason_codes)}")
        if exc.protection_reason:
            print(
                "OPIP_RELEASE_RUNTIME_PROTECTION_REASON="
                f"{_single_line_receipt_text(exc.protection_reason)}"
            )
    attempts = getattr(exc, "attempts", None)
    if isinstance(attempts, int) and not isinstance(attempts, bool):
        print(f"OPIP_RELEASE_RUNTIME_ATTEMPTS={attempts}")
    observed = getattr(exc, "observed_seconds", None)
    if isinstance(observed, (int, float)) and not isinstance(observed, bool):
        print(f"OPIP_RELEASE_RUNTIME_OBSERVED_SECONDS={observed:.3f}")
    evidence = getattr(exc, "evidence", None)
    reported = 0
    if isinstance(evidence, Mapping):
        for key in FAILURE_EVIDENCE_KEYS:
            if key in evidence:
                print(f"OPIP_RELEASE_{key.upper()}={_receipt_value(evidence[key])}")
                reported += 1
    if reported == 0:
        print("OPIP_RELEASE_RUNTIME_OBSERVED_EVIDENCE=NONE")


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
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        sqlite3.Error,
        TimeoutError,
    ) as exc:
        _emit_failure_diagnostics(exc)
        sys.exit(1)
    print("OPIP_RELEASE_RUNTIME_VERIFICATION=PASS")
    print(f"OPIP_RELEASE_RUNTIME_SHA={result['sha']}")
    print(f"OPIP_RELEASE_RUNTIME_VERIFIED_AT={result['verified_at']}")
    print(f"OPIP_RELEASE_PROFILE={result['profile']}")
    print(f"OPIP_RELEASE_EVIDENCE_CAPTURE={result['evidence'].get('evidence_capture', 'PASS')}")
    print(f"OPIP_RELEASE_PROTECTION={result['protection']}")
    print(f"OPIP_RELEASE_PROTECTION_READY={_receipt_value(result['protection_ready'])}")
    print(f"OPIP_RELEASE_SHADOW_READINESS={result['shadow_readiness']}")
    if result.get("shadow_blocking_reason_codes"):
        print(
            "OPIP_RELEASE_SHADOW_BLOCKING_REASON_CODES="
            + ",".join(str(code) for code in result["shadow_blocking_reason_codes"])
        )
    print(f"OPIP_RELEASE_TARGET_AUTHORITY={result['target_authority']}")
    for key, value in sorted(result["evidence"].items()):
        print(f"OPIP_RELEASE_{key.upper()}={_receipt_value(value)}")


if __name__ == "__main__":  # pragma: no cover
    main()
