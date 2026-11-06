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
from app.services.scan_manifest import (
    COVERAGE_SCHEMA_VERSION,
    coverage_is_complete,
    sha256_path,
    write_manifest,
)
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

V2_STATUS_NOT_COMPUTED = "NOT_COMPUTED"

#: Bounded exclusion evidence. The whole document is projected into analytics, so
#: it must not grow with the candidate pool: the true total is always reported and
#: truncation is explicit, never silent.
EXCLUSIONS_RECORDED_LIMIT = 25
EXCLUSION_REASON_MAX_CHARS = 200
_EXCLUSION_FIELDS = ("pair", "base_asset", "liquidity_rank", "status", "reason", "stage")



def _v2_evidence(settings: Any, snapshots: list[Any]) -> dict[str, Any]:
    """Record the scoring provenance this worker actually produced.

    "The image predates the V2 evidence fields" and "the image has them but the
    attempt ran without the V2 scoring authority" have different remedies, and
    both used to look identical downstream: N snapshots, no V2 status. The
    answer is recorded here, beside the acquisition evidence, instead of being
    inferred later from the absence of a field.
    """
    from dataclasses import fields as dataclass_fields

    from app.scanner.models import MarketSnapshot

    declared = {field.name for field in dataclass_fields(MarketSnapshot)}
    counts: dict[str, int] = {}
    for snapshot in snapshots:
        status = str(getattr(snapshot, "v2_feature_status", "") or "")
        key = status.split(":", 1)[0].strip() or V2_STATUS_NOT_COMPUTED
        counts[key] = counts.get(key, 0) + 1
    required = {"v2_long_score", "v2_short_score", "v2_feature_status", "v2_profile_digest"}
    return {
        "production_scoring_version": str(
            getattr(settings, "opip_production_scoring_version", "") or ""
        ),
        "v2_evaluation_enabled": bool(
            getattr(settings, "opip_score_v2_evaluation_enabled", False)
        ),
        # False means this IMAGE cannot emit V2 evidence at all, whatever the
        # configuration says; True with all NOT_COMPUTED means the attempt ran
        # without the scoring authority. Neither may stay silent.
        "v2_fields_in_snapshot_schema": required.issubset(declared),
        "v2_status_counts": dict(sorted(counts.items())),
    }


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


def _coverage(result: Any, requested: int) -> dict[str, Any]:
    """Return the coverage block that proves (or denies) the scan target.

    ``requested`` stays the target and ``analyzed`` stays the number of valid
    snapshots. ``skipped``/``failed``/``data_quality_rejected`` keep their
    established meaning (every observation in this scan), so a recovered scan
    reports its exclusions truthfully; completeness is then decided by comparing
    the achieved target with the attempt accounting, not by hiding the exclusion.
    """
    snapshots = list(getattr(result, "snapshots", []) or [])
    analyzed = int(getattr(result, "analyzed", len(snapshots)) or 0)
    skipped = int(getattr(result, "skipped", 0) or 0)
    failed = int(getattr(result, "failed", 0) or 0)
    data_rejected = int(getattr(result, "data_quality_rejected", 0) or 0)
    attempted = int(getattr(result, "attempted", 0) or 0)
    if attempted <= 0:
        # A producer that predates the counter still classifies every candidate
        # into exactly one bucket, so their sum is the truthful attempt count.
        attempted = analyzed + skipped + failed + data_rejected
    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "requested": int(getattr(result, "requested", requested) or 0),
        "analyzed": analyzed,
        "failed": failed,
        "skipped": skipped,
        "data_quality_rejected": data_rejected,
        "attempted": attempted,
        "unique_assets": len({_asset_key(item) for item in snapshots}),
        "replacement_attempts": int(getattr(result, "replacement_attempts", 0) or 0),
        "remaining_candidate_capacity": int(
            getattr(result, "remaining_candidate_capacity", 0) or 0
        ),
    }


def _asset_key(snapshot: Any) -> str:
    for attribute in ("underlying_asset", "primary_pair", "symbol"):
        value = str(getattr(snapshot, attribute, "") or "").strip()
        if value:
            return value
    return ""


def _exclusions(result: Any) -> dict[str, Any]:
    """Return bounded, explicit exclusion evidence for this attempt."""
    raw = list(getattr(result, "exclusions", []) or [])
    records: list[dict[str, Any]] = []
    for item in raw[:EXCLUSIONS_RECORDED_LIMIT]:
        if not isinstance(item, dict):
            continue
        record: dict[str, Any] = {}
        for key in _EXCLUSION_FIELDS:
            value = item.get(key)
            if isinstance(value, str):
                record[key] = value[:EXCLUSION_REASON_MAX_CHARS]
            elif isinstance(value, int) and not isinstance(value, bool):
                record[key] = value
        if record.get("pair"):
            records.append(record)
    return {
        "recorded": len(records),
        "total": len(raw),
        "truncated": len(raw) > len(records),
        "records": records,
    }


def _universe_evidence(result: Any) -> dict[str, Any]:
    """Record how large the ranked pool was, so replacement capacity is auditable."""
    universe = getattr(result, "universe", None)
    if universe is None:
        return {"status": "UNAVAILABLE"}
    return {
        "status": "RECORDED",
        "unique_underlying_assets": int(
            getattr(universe, "unique_underlying_assets", 0) or 0
        ),
        "selected_liquid_assets": int(
            getattr(universe, "selected_liquid_assets", 0) or 0
        ),
        "eligible_usd_markets": int(getattr(universe, "eligible_usd_markets", 0) or 0),
        "eligible_usdt_markets": int(getattr(universe, "eligible_usdt_markets", 0) or 0),
        "ticker_batch_requests": int(getattr(universe, "ticker_batch_requests", 0) or 0),
        "warnings": [str(item)[:EXCLUSION_REASON_MAX_CHARS] for item in list(getattr(universe, "warnings", []) or [])[:5]],
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
                "scoring": _v2_evidence(settings, []),
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
    scoring = _v2_evidence(settings, snapshots)
    exclusions = _exclusions(result)
    # Completeness is earned from valid data: the target must be met by distinct
    # underlying assets whose attempts all reconcile. A scan that replaced a
    # legitimately ineligible candidate is complete *and* still reports the
    # exclusion, so nothing is hidden to manufacture a pass.
    complete = coverage_is_complete(coverage)
    manifest = write_manifest(
        staging,
        {
            "attempt_id": attempt_id,
            "fence": attempt.fence,
            "started_at": started.isoformat(),
            "coverage": coverage,
            "exclusions": exclusions,
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
            "exclusions": exclusions,
            "universe": _universe_evidence(result),
            "scoring": scoring,
            "manifest_sha256": (manifest.get("snapshots") or {}).get("sha256"),
            "finished_at": _now().isoformat(),
        },
    )
    print(
        f"scan_worker: attempt={attempt_id} complete={complete} "
        f"analyzed={coverage['analyzed']}/{coverage['requested']} "
        f"failed={coverage['failed']} skipped={coverage['skipped']}"
    )
    if exclusions["total"] or not complete:
        # An attempt that lost a candidate must be diagnosable from its own log,
        # not only from a persisted document nobody may be able to read.
        print(
            f"scan_worker: attempted={coverage['attempted']} "
            f"replaced={coverage['replacement_attempts']} "
            f"excluded={exclusions['total']} recorded={exclusions['recorded']} "
            f"truncated={exclusions['truncated']} "
            f"remaining_candidates={coverage['remaining_candidate_capacity']}"
        )
        for record in exclusions["records"][:5]:
            print(
                f"scan_worker: excluded pair={record.get('pair')} "
                f"rank={record.get('liquidity_rank')} status={record.get('status')} "
                f"stage={record.get('stage')} reason={record.get('reason')}"
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
