"""Scan admission, dispatch token and orphan reconciliation (R01/R02/R03).

The core process owns admission. It never executes the scan itself; it writes a
single-use token that the host dispatcher consumes to start the isolated
worker. Protection therefore never waits on acquisition, and no second
scheduler is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping
import tempfile

from app.services.scan_attempt import (
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_OOM_KILLED,
    OUTCOME_PARTIAL,
    OUTCOME_TIMED_OUT,
    ScanAttempt,
    ScanAttemptStore,
)
from app.services.scan_preflight import (
    AdmissionDecision,
    ResourceSnapshot,
    collect_snapshot,
    evaluate,
)


MODE_LEGACY = "legacy_inprocess"
MODE_ISOLATED = "isolated"
MODE_SUSPENDED = "suspended"

REASON_APPROVED = "APPROVED"
REASON_LEGACY_PATH = "LEGACY_INPROCESS"
REASON_SUSPENDED = "SCANNER_SUSPENDED"
REASON_LIVE_ATTEMPT = "PREVIOUS_ATTEMPT_LIVE"

TOKEN_NAME = "admission_token.json"
SCHEMA_VERSION = 1


def _utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def state_dir(settings: Any) -> Path:
    return Path(getattr(settings, "opip_scan_state_dir", "/app/data/scan"))


def store_for(settings: Any) -> ScanAttemptStore:
    return ScanAttemptStore(state_dir(settings))


def execution_mode(settings: Any) -> str:
    mode = str(getattr(settings, "opip_scan_execution_mode", MODE_LEGACY) or "").strip()
    return mode if mode in {MODE_LEGACY, MODE_ISOLATED, MODE_SUSPENDED} else MODE_SUSPENDED


def token_path(settings: Any) -> Path:
    return state_dir(settings) / TOKEN_NAME


def new_attempt_id(now: datetime | None = None) -> str:
    moment = _utc(now)
    return f"SCAN:{moment.strftime('%Y%m%dT%H%M%SZ')}"


def attempt_dir_name(attempt_id: str) -> str:
    """Return a filesystem-safe directory name for an attempt identity.

    Attempt ids carry a ``SCAN:`` prefix for readability. That colon is legal in
    a Linux path but invalid on Windows, so every path component derived from an
    id goes through this sanitiser. Identity is preserved in the ledger; only
    the directory name is normalised.
    """
    safe = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(attempt_id)
    )
    return safe.strip("-.") or "attempt"


def attempt_staging_dir(settings: Any, attempt_id: str) -> Path:
    return state_dir(settings) / "staging" / attempt_dir_name(attempt_id)


@dataclass(frozen=True)
class AdmissionOutcome:
    admitted: bool
    reason_code: str
    attempt: ScanAttempt | None = None
    decision: AdmissionDecision | None = None


def evaluate_admission(
    *,
    settings: Any,
    store: ScanAttemptStore,
    snapshot: ResourceSnapshot | None = None,
    now: datetime | None = None,
) -> AdmissionOutcome:
    """Decide whether a new isolated attempt may be reserved.

    Order matters: a live attempt blocks before any resource question is
    asked, and an expired deadline never authorises a second attempt on its
    own (R02).
    """
    moment = _utc(now)
    if store.is_discovery_blocked(moment):
        # Repeated isolated-workflow failures suspend discovery. They never
        # restore the legacy in-process scanner.
        return AdmissionOutcome(False, REASON_CIRCUIT_OPEN, store.active())
    active = store.active()
    if active is not None:
        return AdmissionOutcome(False, REASON_LIVE_ATTEMPT, active)

    core_snapshot = snapshot or collect_snapshot(data_dir=state_dir(settings).parent)
    decision = evaluate(
        core_snapshot,
        scan_cap_mib=int(getattr(settings, "opip_scan_memory_cap_mib", 224)),
        host_reserve_mib=int(getattr(settings, "opip_scan_host_reserve_mib", 64)),
        core_ceiling_pct=float(
            getattr(settings, "opip_scan_core_memory_ceiling_pct", 70.0)
        ),
    )
    if not decision.admitted:
        return AdmissionOutcome(False, decision.reason_code, None, decision)

    attempt = store.create(
        attempt_id=new_attempt_id(moment),
        deadline_seconds=int(getattr(settings, "opip_scan_deadline_seconds", 300)),
        now=moment,
        evidence={"preflight": decision.snapshot.as_dict()},
    )
    return AdmissionOutcome(True, REASON_APPROVED, attempt, decision)


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                dict(payload),
                handle,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_token(settings: Any, attempt: ScanAttempt, *, now: datetime | None = None) -> Path:
    """Write the single-use dispatch token for ``attempt``."""
    moment = _utc(now)
    ttl = int(getattr(settings, "opip_scan_dispatch_token_seconds", 120))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt.attempt_id,
        "fence": attempt.fence,
        "deadline_at": attempt.deadline_at,
        "issued_at": moment.isoformat(),
        "expires_at": (moment + timedelta(seconds=ttl)).isoformat(),
    }
    path = token_path(settings)
    _write_atomic(path, payload)
    return path


def read_token(settings: Any) -> dict[str, Any] | None:
    path = token_path(settings)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def token_is_actionable(
    token: Mapping[str, Any] | None, *, now: datetime | None = None
) -> bool:
    if not isinstance(token, Mapping):
        return False
    raw = token.get("expires_at")
    if not raw:
        return False
    try:
        expires = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return _utc(now) < expires


def clear_token(settings: Any) -> bool:
    try:
        token_path(settings).unlink()
        return True
    except FileNotFoundError:
        return False


EVIDENCE_NAME = "container_evidence.json"
LAUNCH_NAME = "launch_request.json"
LAUNCH_FAILED_NAME = "launch_failed.json"
EXIT_NAME = "container_exit.json"
QUARANTINE_NAME = "quarantine.json"

REASON_NEVER_LAUNCHED = "NEVER_LAUNCHED"
REASON_CIRCUIT_OPEN = "DISCOVERY_CIRCUIT_OPEN"
REASON_AWAITING_EVIDENCE = "AWAITING_CONTAINER_EVIDENCE"


def attempt_evidence_dir(settings: Any, attempt_id: str) -> Path:
    return state_dir(settings) / "staging" / attempt_dir_name(attempt_id)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def read_launch_evidence(settings: Any, attempt_id: str) -> dict[str, Any] | None:
    """Return the dispatcher's launch *request* record for this attempt."""
    return _read_json(attempt_evidence_dir(settings, attempt_id) / LAUNCH_NAME)


def read_launch_failure(settings: Any, attempt_id: str) -> dict[str, Any] | None:
    """Return the record proving Docker refused to start this attempt."""
    return _read_json(attempt_evidence_dir(settings, attempt_id) / LAUNCH_FAILED_NAME)


def read_exit_evidence(settings: Any, attempt_id: str) -> dict[str, Any] | None:
    """Return the dispatcher's post-launch exit record for this exact attempt."""
    return _read_json(attempt_evidence_dir(settings, attempt_id) / EXIT_NAME)


def read_quarantine(settings: Any, attempt_id: str) -> dict[str, Any] | None:
    """Return the quarantine record for an attempt that could not be killed."""
    return _read_json(attempt_evidence_dir(settings, attempt_id) / QUARANTINE_NAME)


def launch_is_post_dated(
    launch: Mapping[str, Any] | None, exit_evidence: Mapping[str, Any] | None
) -> bool:
    """True only when the exit record is not older than the launch record."""
    if not isinstance(launch, Mapping) or not isinstance(exit_evidence, Mapping):
        return False
    try:
        launched = datetime.fromisoformat(
            str(launch.get("launched_at")).replace("Z", "+00:00")
        )
        captured = datetime.fromisoformat(
            str(exit_evidence.get("captured_at")).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return False
    if launched.tzinfo is None:
        launched = launched.replace(tzinfo=timezone.utc)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    return captured >= launched


def write_container_evidence(
    settings: Any,
    *,
    alive_attempt_ids: Iterable[str],
    observed_at: datetime | None = None,
    source: str = "dispatcher",
) -> Path:
    """Record what the host dispatcher actually saw.

    The core cannot inspect Docker, so container liveness is *observed* by the
    dispatcher and *read* here. Without this evidence the core refuses to
    terminalise an attempt, because absence of evidence is not evidence of
    termination.
    """
    moment = _utc(observed_at)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": moment.isoformat(),
        "source": str(source),
        "alive_attempt_ids": sorted({str(item) for item in alive_attempt_ids}),
    }
    path = state_dir(settings) / EVIDENCE_NAME
    _write_atomic(path, payload)
    return path


def read_container_evidence(settings: Any) -> dict[str, Any] | None:
    path = state_dir(settings) / EVIDENCE_NAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def evidence_is_fresh(
    evidence: Mapping[str, Any] | None,
    *,
    settings: Any,
    now: datetime | None = None,
) -> bool:
    """True only when container evidence is recent enough to act on."""
    if not isinstance(evidence, Mapping):
        return False
    try:
        observed = datetime.fromisoformat(str(evidence.get("observed_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    max_age = int(getattr(settings, "opip_scan_deadline_seconds", 300)) + 120
    return (_utc(now) - observed).total_seconds() <= max_age



def reconcile(
    store: ScanAttemptStore,
    *,
    settings: Any,
    now: datetime | None = None,
) -> list[ScanAttempt]:
    """Close an attempt only on attempt-specific, post-launch disappearance.

    The previous version terminalised from a generic ``alive=[]`` snapshot,
    which is a *pre-launch* observation: an attempt whose container had not yet
    started looked identical to one that had finished. Terminalisation now
    requires the dispatcher's own exit record for this exact attempt, captured
    after its launch. Anything less is ambiguity, and ambiguity withholds.
    """
    moment = _utc(now)
    active = store.active()
    if active is None:
        return []

    attempt_id = active.attempt_id
    exit_evidence = read_exit_evidence(settings, attempt_id)
    launch = read_launch_evidence(settings, attempt_id)

    if exit_evidence is not None:
        if not launch_is_post_dated(launch, exit_evidence):
            # Exit evidence that predates the launch belongs to a previous
            # attempt or a recycled name; it proves nothing about this one.
            return []
        outcome, reason = classify_exit(
            exit_evidence, attempt_evidence_dir(settings, attempt_id)
        )
        return [
            store.terminalize(
                attempt_id,
                outcome,
                reason,
                evidence={"container_exit": exit_evidence},
                now=moment,
            )
        ]

    launch_failure = read_launch_failure(settings, attempt_id)
    if launch_failure is not None:
        # A launch request that Docker refused: the dispatcher observed zero
        # containers for this attempt, so no worker can exist. That is positive
        # evidence, unlike a bare absence of a running container.
        return [
            store.terminalize(
                attempt_id,
                OUTCOME_FAILED,
                "LAUNCH_FAILED",
                evidence={"launch_failure": launch_failure},
                now=moment,
            )
        ]

    if launch is None:
        token = read_token(settings)
        if token is None or not token_is_actionable(token, now=moment):
            # The dispatcher deletes the token when it launches. An expired or
            # absent token without a launch record is positive evidence that
            # this attempt was never started, so no container can exist.
            return [
                store.terminalize(
                    attempt_id,
                    OUTCOME_CANCELLED,
                    REASON_NEVER_LAUNCHED,
                    evidence={"reconciled_at": moment.isoformat()},
                    now=moment,
                )
            ]
        return []

    # Launched, but no exit record yet: the container may still be running.
    return []


def classify_exit(
    exit_evidence: Mapping[str, Any], evidence_dir: Path
) -> tuple[str, str | None]:
    """Map observed container exit state to a terminal outcome."""
    state = exit_evidence.get("state") if isinstance(exit_evidence, Mapping) else None
    if not isinstance(state, Mapping):
        return OUTCOME_FAILED, "EXIT_STATE_UNREADABLE"
    if bool(state.get("OOMKilled")):
        return OUTCOME_OOM_KILLED, "CGROUP_OOM_KILL"
    try:
        code = int(state.get("ExitCode"))
    except (TypeError, ValueError):
        return OUTCOME_FAILED, "EXIT_CODE_UNREADABLE"
    if code == 0:
        from app.services.scan_manifest import ManifestError, is_complete, read_manifest

        try:
            manifest = read_manifest(evidence_dir)
        except ManifestError as exc:
            return OUTCOME_FAILED, f"MANIFEST_{exc.reason}"
        if is_complete(manifest):
            return OUTCOME_COMPLETED, None
        return OUTCOME_PARTIAL, "COVERAGE_INCOMPLETE"
    if code in (137, 143):
        return OUTCOME_TIMED_OUT, "DEADLINE_KILLED"
    return OUTCOME_FAILED, f"CONTAINER_EXIT_{code}"
