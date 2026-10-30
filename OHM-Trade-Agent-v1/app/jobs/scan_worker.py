"""Acquisition-only isolated scan worker (R01/R06).

This module acquires market data and writes an integrity-checked manifest. It
deliberately imports no notification, AI-review, paper-admission or canonical
writer module, so the isolated cgroup cannot acquire new authority by accident.

The worker never writes the attempt ledger: the core process owns that. It
emits ``worker_status.json`` beside the manifest and lets the core commit the
terminal outcome, which keeps exactly one writer per durable artefact.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

from app.core.config import get_settings
from app.services.scan_attempt import ScanAttemptStore
from app.services.scan_manifest import sha256_path, write_manifest
from app.services.scan_supervisor import (
    MODE_ISOLATED,
    attempt_staging_dir,
    execution_mode,
    state_dir,
)


EXIT_OK = 0
EXIT_TRANSIENT = 75
EXIT_PRECONDITION = 76
EXIT_REJECTED = 78

STATUS_NAME = "worker_status.json"
SNAPSHOTS_NAME = "snapshots.jsonl"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _coverage(result: Any, requested: int) -> dict[str, int]:
    return {
        "requested": int(getattr(result, "requested", requested) or 0),
        "analyzed": int(getattr(result, "analyzed", 0) or 0),
        "failed": int(getattr(result, "failed", 0) or 0),
        "skipped": int(getattr(result, "skipped", 0) or 0),
    }


def run(*, attempt_id: str, limit: int | None = None) -> int:
    settings = get_settings()
    root = state_dir(settings)
    # Read-only: the worker validates its own attempt but must never mutate the
    # ledger, the admission token or any operational registry.
    store = ScanAttemptStore(root, read_only=True)
    attempt = store.get(attempt_id)
    if attempt is None:
        print(f"scan_worker: unknown attempt {attempt_id}", file=sys.stderr)
        return EXIT_REJECTED
    if not attempt.active:
        print(f"scan_worker: attempt {attempt_id} is not active", file=sys.stderr)
        return EXIT_REJECTED
    if execution_mode(settings) != MODE_ISOLATED:
        print("scan_worker: isolated mode is not selected", file=sys.stderr)
        return EXIT_PRECONDITION

    staging = attempt_staging_dir(settings, attempt_id)
    staging.mkdir(parents=True, exist_ok=True)

    from app.scanner.market_scanner import DEFAULT_UNIQUE_ASSET_LIMIT, scan_market

    requested_target = int(limit or DEFAULT_UNIQUE_ASSET_LIMIT)
    started = _now()
    try:
        result = scan_market(limit=requested_target)
    except Exception as exc:  # noqa: BLE001 - classified by the core, not hidden
        _write_json(
            staging / STATUS_NAME,
            {
                "schema_version": 1,
                "attempt_id": attempt_id,
                "state": "FAILED",
                "reason_code": f"ACQUISITION_EXCEPTION_{type(exc).__name__.upper()}",
                "finished_at": _now().isoformat(),
            },
        )
        print(
            f"scan_worker: acquisition failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_TRANSIENT

    snapshots = list(getattr(result, "snapshots", []) or [])
    snapshots_path = staging / SNAPSHOTS_NAME
    with snapshots_path.open("w", encoding="utf-8") as handle:
        for snapshot in snapshots:
            handle.write(json.dumps(_jsonable(snapshot), sort_keys=True, allow_nan=False))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    coverage = _coverage(result, requested_target)
    complete = (
        coverage["requested"] > 0
        and coverage["analyzed"] == coverage["requested"]
        and coverage["failed"] == 0
        and coverage["skipped"] == 0
    )
    manifest = write_manifest(
        staging,
        {
            "attempt_id": attempt_id,
            "fence": attempt.fence,
            "started_at": started.isoformat(),
            "coverage": coverage,
            "snapshots": {
                "file": SNAPSHOTS_NAME,
                "rows": len(snapshots),
                "sha256": sha256_path(snapshots_path),
            },
        },
        complete=complete,
        row_count=len(snapshots),
    )
    _write_json(
        staging / STATUS_NAME,
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "state": "COMPLETED" if complete else "PARTIAL",
            "reason_code": None if complete else "COVERAGE_INCOMPLETE",
            "coverage": coverage,
            "manifest_sha256": (manifest.get("snapshots") or {}).get("sha256"),
            "finished_at": _now().isoformat(),
        },
    )
    print(
        f"scan_worker: attempt={attempt_id} complete={complete} "
        f"analyzed={coverage['analyzed']}/{coverage['requested']} "
        f"failed={coverage['failed']} skipped={coverage['skipped']}"
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated acquisition-only scan worker")
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parsed = parser.parse_args(argv)
    return run(attempt_id=parsed.attempt_id, limit=parsed.limit)


if __name__ == "__main__":
    raise SystemExit(main())
