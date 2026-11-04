"""Runtime evidence for the persistent global header.

Each field is read from a named artifact and reports the artifact it came from.
Anything that cannot be read is ``UNAVAILABLE`` or ``UNVERIFIED`` with a reason -
never a default healthy value.

Two of these fields genuinely have no source on this host in this increment and
say so instead of being invented:

* **Learning Droplet CPU/RAM** needs a cross-host telemetry channel that does not
  exist yet. The trading host cannot read the droplet's cgroup.
* **F11 state** has no persisted runtime artifact; only the deploy log carries
  the strict verdict. ``OPIP_F11_STATE_PATH`` is the integration point, and while
  it is absent the header states ``UNVERIFIED``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

STATUS_MEASURED = "MEASURED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_STALE = "STALE"

#: The only F11 values the platform's protection contracts define. Anything else
#: is rendered as an unknown value and is never interpreted as healthy.
F11_CANONICAL_VALUES: tuple[str, ...] = ("ARMED", "TRIGGERED_HALT", "OVERRIDE_SHADOW")

REASON_NO_F11_ARTIFACT = "NO_F11_STATE_ARTIFACT"
REASON_NO_RELEASE_ARTIFACT = "NO_RELEASE_STATE_ARTIFACT"
REASON_NO_LEARNING_TELEMETRY = "NO_LEARNING_DROPLET_TELEMETRY_CHANNEL"
REASON_NO_SCANNER_INDEX = "NO_SCAN_ATTEMPT_INDEX"


def _utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(observed_at: Any, *, now: datetime | None = None) -> float | None:
    parsed = _parse(observed_at)
    if parsed is None:
        return None
    return max(0.0, (_utc(now) - parsed).total_seconds())


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None



def read_release(settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The deployed revision, from the release marker or the container env."""
    configured = str(getattr(settings, "opip_release_state_path", "") or "").strip()
    path = Path(configured or "/app/data/opip/release/runtime.json")
    document = _read_json(path)
    if document is not None:
        sha = str(document.get("deployed_sha") or document.get("sha") or "").strip()
        if sha:
            return {
                "status": STATUS_MEASURED,
                "deployed_sha": sha,
                "source": str(path),
                "observed_at_utc": document.get("deployed_at_utc")
                or document.get("recorded_at_utc"),
                "environment": document.get("environment"),
            }
    env_sha = str(os.environ.get("OPIP_DEPLOYED_SHA", "") or "").strip()
    if env_sha:
        return {
            "status": STATUS_MEASURED,
            "deployed_sha": env_sha,
            "source": "ENV:OPIP_DEPLOYED_SHA",
            "observed_at_utc": None,
            "environment": os.environ.get("APP_ENV"),
        }
    return {
        "status": STATUS_UNAVAILABLE,
        "reason_code": REASON_NO_RELEASE_ARTIFACT,
        "source": str(path),
        "deployed_sha": None,
    }


def read_protection(settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """F11 protection state, or an explicit statement that it is unverified."""
    configured = str(getattr(settings, "opip_f11_state_path", "") or "").strip()
    path = Path(configured or "/app/data/opip/protection/protection_state.json")
    document = _read_json(path)
    if document is None:
        return {
            "status": STATUS_UNVERIFIED,
            "reason_code": REASON_NO_F11_ARTIFACT,
            "state": None,
            "canonical_values": list(F11_CANONICAL_VALUES),
            "source": str(path),
            "observed_at_utc": None,
            "age_seconds": None,
            "admissions": None,
        }
    raw_state = (
        str(document.get("state") or document.get("f11_state") or "").strip().upper()
    )
    canonical = raw_state in F11_CANONICAL_VALUES
    return {
        "status": STATUS_MEASURED,
        "state": raw_state or None,
        "canonical": canonical,
        "unknown_state": None if canonical else (raw_state or "UNRECORDED"),
        "canonical_values": list(F11_CANONICAL_VALUES),
        "reason_code": document.get("reason_code"),
        "source": str(path),
        "observed_at_utc": document.get("observed_at_utc"),
        "age_seconds": age_seconds(document.get("observed_at_utc"), now=now),
        "admissions": document.get("new_admissions_allowed"),
    }


def scanner_heartbeat(settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Latest observed scan attempt, read from the scanner's own state."""
    from app.opip.analytics.sources import scan_state_dir

    root = scan_state_dir(settings)
    index = _read_json(root / "scan_attempt_index.json")
    attempts = index.get("attempts") if isinstance(index, dict) else None
    latest: dict[str, Any] | None = None
    if isinstance(attempts, dict) and attempts:
        candidates = [row for row in attempts.values() if isinstance(row, dict)]
        if candidates:
            latest = max(candidates, key=lambda row: str(row.get("created_at") or ""))
    if latest is None:
        return {
            "status": STATUS_UNAVAILABLE,
            "reason_code": REASON_NO_SCANNER_INDEX,
            "source": str(root / "scan_attempt_index.json"),
            "attempt_id": None,
            "observed_at_utc": None,
            "age_seconds": None,
        }
    observed = latest.get("finished_at") or latest.get("created_at")
    return {
        "status": STATUS_MEASURED,
        "attempt_id": latest.get("attempt_id"),
        "state": latest.get("state"),
        "acquisition_outcome": latest.get("outcome"),
        "reason_code": latest.get("reason_code"),
        "fence": latest.get("fence"),
        "observed_at_utc": observed,
        "age_seconds": age_seconds(observed, now=now),
        "source": str(root / "scan_attempt_index.json"),
        "discovery_blocked_until": (
            index.get("discovery_blocked_until") if isinstance(index, dict) else None
        ),
        "consecutive_failures": (
            index.get("consecutive_failures") if isinstance(index, dict) else None
        ),
    }


def host_resources(*, now: datetime | None = None) -> dict[str, Any]:
    """Trading-host resource pressure, read from /proc and the cgroup.

    Reuses the scanner's own preflight collector so the Control Center and the
    scanner admission gate cannot disagree about how loaded the host is. A read
    failure is reported, not smoothed over.
    """
    try:
        from app.services.scan_preflight import collect_snapshot
    except Exception as exc:  # noqa: BLE001 - a missing collector is a fact
        return {
            "status": STATUS_UNAVAILABLE,
            "reason_code": f"RESOURCE_COLLECTOR_UNAVAILABLE_{type(exc).__name__.upper()}",
        }
    try:
        snapshot = collect_snapshot()
        payload = snapshot.as_dict()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": STATUS_UNAVAILABLE,
            "reason_code": f"RESOURCE_READ_FAILED_{type(exc).__name__.upper()}",
        }
    measured = bool(payload) and not payload.get("read_errors")
    return {
        "status": STATUS_MEASURED if measured else STATUS_UNAVAILABLE,
        "reason_code": None if measured else "RESOURCE_PARTIAL_READ",
        "host_available_mib": payload.get("host_available_mib"),
        "core_current_mib": payload.get("core_current_mib"),
        "core_max_mib": payload.get("core_max_mib"),
        "core_utilization_pct": payload.get("core_utilization_pct"),
        "cpu_pressure_avg10": payload.get("cpu_pressure_avg10"),
        "memory_pressure_avg10": payload.get("memory_pressure_avg10"),
        "disk_free_mib": payload.get("disk_free_mib"),
        "read_errors": payload.get("read_errors"),
        "scope": "TRADING_HOST",
    }


def learning_droplet_telemetry(settings: Any) -> dict[str, Any]:
    """Learning Droplet CPU/RAM, over the approved telemetry artifact if present.

    There is no cross-host telemetry channel today, so the honest answer is
    ``UNAVAILABLE``. A future collector publishes this JSON and nothing else in
    the Control Center changes.
    """
    configured = str(
        getattr(settings, "opip_learning_telemetry_path", "") or ""
    ).strip()
    path = Path(configured or "/app/data/opip/learning/telemetry.json")
    document = _read_json(path)
    if document is None:
        return {
            "status": STATUS_UNAVAILABLE,
            "reason_code": REASON_NO_LEARNING_TELEMETRY,
            "source": str(path),
            "cpu_pct": None,
            "memory_used_mib": None,
            "memory_total_mib": None,
        }
    return {
        "status": STATUS_MEASURED,
        "source": str(path),
        "observed_at_utc": document.get("observed_at_utc"),
        "cpu_pct": document.get("cpu_pct"),
        "memory_used_mib": document.get("memory_used_mib"),
        "memory_total_mib": document.get("memory_total_mib"),
    }




def config_version(settings: Any) -> dict[str, Any]:
    """The effective advisory configuration identity, read from its own source.

    This deliberately does not read any staging table: a staged draft has no
    effect on runtime, so it must never be shown as the effective version.
    """
    try:
        from app.core.strategy_policy import (
            ACTIVE_POLICY_PATH,
            load_active_strategy_policy,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": STATUS_UNAVAILABLE,
            "reason_code": f"CONFIG_READER_UNAVAILABLE_{type(exc).__name__.upper()}",
            "digest": None,
        }
    try:
        installed = Path(ACTIVE_POLICY_PATH).is_file()
        policy = load_active_strategy_policy()
    except Exception as exc:  # noqa: BLE001 - a malformed active profile is fatal
        return {
            "status": STATUS_UNVERIFIED,
            "reason_code": f"CONFIG_UNREADABLE_{type(exc).__name__.upper()}",
            "digest": None,
        }
    return {
        "status": STATUS_MEASURED,
        "digest": policy.digest,
        "profile_id": policy.profile_id,
        "authority": "ACTIVE_ADVISORY_PROFILE" if installed else "BUNDLED_DEFAULT",
        "source": (
            str(ACTIVE_POLICY_PATH) if installed else "app/core/strategy_policy.json"
        ),
    }


def environment_identity(settings: Any) -> dict[str, Any]:
    """Name the deployment environment only when the deployment names itself."""
    raw = str(getattr(settings, "app_env", "") or "").strip()
    profile = str(os.environ.get("OPIP_RELEASE_PROFILE", "") or "").strip() or None
    normalised = raw.lower()
    if normalised in {"production", "prod"}:
        label, status = "PRODUCTION", STATUS_MEASURED
    elif "shadow" in normalised:
        label, status = "SHADOW", STATUS_MEASURED
    elif normalised in {"", "development", "dev", "test", "local"}:
        label, status = "NON_PRODUCTION", STATUS_MEASURED
    else:
        label, status = "UNVERIFIED", STATUS_UNVERIFIED
    return {
        "status": status,
        "label": label,
        "raw": raw or None,
        "release_profile": profile,
        "source": "ENV:APP_ENV",
    }


def runtime_evidence(settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Everything the persistent header needs, each field self-describing."""
    moment = _utc(now)
    return {
        "generated_at_utc": moment.isoformat(),
        "environment": environment_identity(settings),
        "release": read_release(settings, now=moment),
        "protection": read_protection(settings, now=moment),
        "scanner": scanner_heartbeat(settings, now=moment),
        "resources": host_resources(now=moment),
        "learning": learning_droplet_telemetry(settings),
        "config_version": config_version(settings),
        "trading_authority": {
            "funded_orders": False,
            "source": "ARCHITECTURE_INVARIANT",
            "detail": "no funded execution path is exposed by this surface",
        },
    }
