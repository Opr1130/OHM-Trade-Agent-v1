"""Durable learning-job consumption dispositions (measurement-only).

Every eligible learning job invocation must reach an explicit terminal
disposition. Silent capacity skips and release-drift blocks are defects if
they leave no durable record. Dispositions do not authorize trading or
policy changes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Terminal dispositions for job invocations (not live trading enums).
CONSUMED_OK = "CONSUMED_OK"
CONSUMED_EMPTY = "CONSUMED_EMPTY"
SKIPPED_BUSY = "SKIPPED_BUSY"
SKIPPED_CAPACITY = "SKIPPED_CAPACITY"
BLOCKED_RELEASE_DRIFT = "BLOCKED_RELEASE_DRIFT"
FAILED_RETRYABLE = "FAILED_RETRYABLE"
FAILED_TERMINAL = "FAILED_TERMINAL"

TERMINAL_DISPOSITIONS = frozenset(
    {
        CONSUMED_OK,
        CONSUMED_EMPTY,
        SKIPPED_BUSY,
        SKIPPED_CAPACITY,
        BLOCKED_RELEASE_DRIFT,
        FAILED_RETRYABLE,
        FAILED_TERMINAL,
    }
)

RELEASE_CURRENT = "CURRENT"
RELEASE_DRIFT = "RELEASE_DRIFT"
RELEASE_UNVERIFIED = "UNVERIFIED"

RELEASE_COMPATIBILITY_STATUSES = frozenset(
    {RELEASE_CURRENT, RELEASE_DRIFT, RELEASE_UNVERIFIED}
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def disposition_path(state_root: Path | str, job: str) -> Path:
    return Path(state_root) / f"{job}.disposition.env"


def consumption_summary_path(data_root: Path | str, job: str) -> Path:
    return Path(data_root) / ".learning_consumption" / f"{job}.json"


def classify_release_compatibility(
    worker_sha: str | None, expected_sha: str | None
) -> str:
    """Exact-SHA equality with production last-good / export expected SHA."""
    worker = (worker_sha or "").strip().lower()
    expected = (expected_sha or "").strip().lower()
    if not _is_full_sha(worker) or not _is_full_sha(expected):
        return RELEASE_UNVERIFIED
    if worker == expected:
        return RELEASE_CURRENT
    return RELEASE_DRIFT


def _is_full_sha(value: str) -> bool:
    return len(value) == 40 and all(ch in "0123456789abcdef" for ch in value)


def write_disposition_env(
    state_root: Path | str,
    *,
    job: str,
    disposition: str,
    release_compatibility_status: str = RELEASE_UNVERIFIED,
    worker_sha: str = "",
    expected_sha: str = "",
    exit_code: int | str | None = None,
    detail: str = "",
    recorded_at_utc: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Path:
    """Write a durable KEY=VALUE disposition marker for diagnose/sync."""
    if disposition not in TERMINAL_DISPOSITIONS:
        raise ValueError(f"unsupported disposition: {disposition}")
    if release_compatibility_status not in RELEASE_COMPATIBILITY_STATUSES:
        raise ValueError(
            f"unsupported release_compatibility_status: {release_compatibility_status}"
        )

    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True)
    path = disposition_path(root, job)
    tmp = path.with_suffix(path.suffix + ".tmp")
    recorded = recorded_at_utc or utc_now_iso()
    lines = [
        f"job={job}",
        f"disposition={disposition}",
        f"recorded_at_utc={recorded}",
        f"release_compatibility_status={release_compatibility_status}",
        f"worker_sha={worker_sha}",
        f"expected_sha={expected_sha}",
        f"exit_code={'' if exit_code is None else exit_code}",
        f"detail={_sanitize_env_value(detail)}",
        "measurement_only=true",
        "trade_authority_changed=false",
        "policy_change_authorized=false",
    ]
    if extra:
        for key, value in extra.items():
            safe_key = str(key).strip().replace("=", "_").replace(" ", "_")
            if not safe_key:
                continue
            lines.append(f"{safe_key}={_sanitize_env_value(value)}")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_disposition_env(state_root: Path | str, job: str) -> dict[str, str]:
    path = disposition_path(state_root, job)
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        result[key] = value
    return result


def write_consumption_summary(
    data_root: Path | str,
    *,
    job: str,
    disposition: str,
    payload: Mapping[str, Any],
) -> Path:
    """Write JSON consumption summary under the learning data plane."""
    if disposition not in TERMINAL_DISPOSITIONS:
        raise ValueError(f"unsupported disposition: {disposition}")
    root = Path(data_root) / ".learning_consumption"
    root.mkdir(parents=True, exist_ok=True)
    path = consumption_summary_path(data_root, job)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = {
        "job": job,
        "disposition": disposition,
        "recorded_at_utc": utc_now_iso(),
        "measurement_only": True,
        "trade_authority_changed": False,
        "policy_change_authorized": False,
        **dict(payload),
    }
    tmp.write_text(
        json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def read_consumption_summary(
    data_root: Path | str, job: str
) -> dict[str, Any] | None:
    path = consumption_summary_path(data_root, job)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def disposition_for_exit_code(exit_code: int, *, empty: bool = False) -> str:
    if exit_code == 0:
        return CONSUMED_EMPTY if empty else CONSUMED_OK
    if exit_code in (124, 137):
        return FAILED_RETRYABLE
    return FAILED_TERMINAL


def _sanitize_env_value(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("\r", " ").strip()
