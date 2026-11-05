"""Bounded, read-only read-through to canonical evidence.

The derived SQLite projection is the preferred read path, but it is populated by a
maintenance worker that is not scheduled yet. Without a fallback the console shows
nothing at all even though the evidence exists on disk, which is indistinguishable
from "no signals" to an operator.

This module reads the *same registered sources* the ingestion worker reads, with
bounded tail reads, and labels every payload:

* ``source = "CANONICAL_DIRECT"`` - read straight from canonical evidence;
* ``verification = "UNPROJECTED"`` - not reconciled through the projection.

It never writes, never fabricates, and never claims an unprojected read is
verified. An unprojected read is evidence; it is not a reconciled KPI.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.opip.analytics import queries, sources
from app.opip.storage.bounded_jsonl import read_tail_lines

SOURCE_DIRECT = "CANONICAL_DIRECT"
VERIFICATION_UNPROJECTED = "UNPROJECTED"

DEFAULT_V2_THRESHOLD = 75.0

#: Bounds. A projection read costs a query; a direct read costs bytes, so every
#: direct reader is line- and file-bounded and reports truncation.
MAX_COMPARISON_ROWS = 24
MAX_ATTEMPT_EVENTS = 400
MAX_SNAPSHOT_ATTEMPTS = 3
MAX_SNAPSHOT_ROWS = 400
REASON_NO_AI_SOURCE = "NO_AI_INFERENCE_SOURCE"


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _text(value: Any, limit: int = 200) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _tail_records(path: Path, max_lines: int) -> list[tuple[dict[str, Any], str]]:
    """Return ``(record, sha256)`` for the trailing rows of a JSONL file."""
    try:
        lines = read_tail_lines(path, max_lines)
    except OSError:
        return []
    out: list[tuple[dict[str, Any], str]] = []
    for raw in lines:
        try:
            item = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(item, dict):
            out.append((item, hashlib.sha256(raw).hexdigest()))
    return out


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def provenance() -> dict[str, Any]:
    return {
        "source": SOURCE_DIRECT,
        "verification": VERIFICATION_UNPROJECTED,
        "detail": (
            "read directly from canonical evidence; not yet reconciled through the "
            "analytical projection"
        ),
    }


def comparisons(settings: Any, *, limit: int = 12) -> dict[str, Any]:
    """Paired V1/V2 comparison rows, tail-read from the canonical ledger."""
    path = sources.comparison_ledger_path(settings)
    limit = max(1, min(int(limit), MAX_COMPARISON_ROWS))
    records = _tail_records(path, limit)
    rows: list[dict[str, Any]] = []
    for record, sha in reversed(records):
        rows.append(
            {
                "comparison_key": f"{_text(record.get('scan_id')) or 'unknown'}:{sha[:16]}",
                "scan_id": _text(record.get("scan_id")),
                "model_authority": _text(record.get("model_authority"), 64),
                "v1_threshold": _num(record.get("v1_threshold")),
                "v2_threshold": _num(record.get("v2_threshold")),
                "scanned": _num(record.get("scanned")),
                "v2_ready": _num(record.get("v2_ready")),
                "v1_top": record.get("v1_top") if isinstance(record.get("v1_top"), list) else None,
                "v2_top": record.get("v2_top") if isinstance(record.get("v2_top"), list) else None,
                "v1_only": record.get("v1_only") if isinstance(record.get("v1_only"), list) else None,
                "v2_only": record.get("v2_only") if isinstance(record.get("v2_only"), list) else None,
                "outcome_status": _text(record.get("outcome_status"), 32) or "UNRECORDED",
                "observed_at_utc": _iso(record.get("observed_at_utc")),
                "record_sha256": sha,
                "path": str(path),
            }
        )
    return {
        "status": queries.STATUS_MEASURED if rows else queries.STATUS_NO_EVIDENCE,
        "reason_code": None if rows else "NO_COMPARISON_EVIDENCE",
        "rows": rows,
        "path": str(path),
        **provenance(),
    }


def attempt_events(settings: Any, *, max_lines: int = MAX_ATTEMPT_EVENTS) -> dict[str, Any]:
    """Attempt lifecycle events, plus the latest state per attempt."""
    path = sources.scan_state_dir(settings) / "scan_attempts.jsonl"
    records = _tail_records(path, max_lines)
    events: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    for record, sha in records:
        attempt_id = _text(record.get("attempt_id"), 64)
        if not attempt_id:
            continue
        event = {
            "attempt_id": attempt_id,
            "event_type": _text(record.get("event"), 32),
            "state": _text(record.get("state"), 32),
            "outcome": _text(record.get("outcome"), 32),
            "reason_code": _text(record.get("reason_code"), 96),
            "event_seq": _num(record.get("seq")),
            "observed_at_utc": _iso(record.get("at") or record.get("created_at")),
            "record_sha256": sha,
        }
        events.append(event)
        latest[attempt_id] = event
    return {
        "status": queries.STATUS_MEASURED if events else queries.STATUS_NO_EVIDENCE,
        "reason_code": None if events else "NO_SCAN_ATTEMPT_EVIDENCE",
        "attempts": len(latest),
        "events_read": len(events),
        "truncated": len(records) >= max_lines,
        "latest": latest,
        "path": str(path),
        **provenance(),
    }


def snapshot_batches(
    settings: Any, *, max_attempts: int = MAX_SNAPSHOT_ATTEMPTS
) -> list[dict[str, Any]]:
    """Newest acquisition batches, each bounded to a tail read."""
    descriptors = sources.discover_batch_sources(settings, limit=max_attempts)
    batches: list[dict[str, Any]] = []
    for descriptor in descriptors:
        if descriptor.record_kind != sources.KIND_SCAN_SNAPSHOT:
            continue
        manifest = _read_json(descriptor.manifest_path) if descriptor.manifest_path else None
        manifest = manifest or {}
        attempt_id = _text(manifest.get("attempt_id"), 64) or descriptor.attempt_id
        observed = _iso(manifest.get("started_at")) or _mtime_iso(descriptor.path)
        worker = None
        if descriptor.manifest_path is not None:
            worker = _read_json(descriptor.manifest_path.with_name("worker_status.json"))
        rows = _tail_records(descriptor.path, MAX_SNAPSHOT_ROWS)
        batches.append(
            {
                "attempt_id": attempt_id,
                "directory": descriptor.attempt_id,
                "source_id": descriptor.source_id,
                "path": str(descriptor.path),
                "manifest_present": bool(manifest),
                "manifest_sha256": ((manifest.get("snapshots") or {}) or {}).get("sha256"),
                "observed_at_utc": observed,
                "rows_read": len(rows),
                "truncated": len(rows) >= MAX_SNAPSHOT_ROWS,
                "worker_status": worker,
                "records": rows,
            }
        )
    return batches


def _signal_row(
    record: Mapping[str, Any],
    *,
    attempt_id: str,
    observed_at: str | None,
    source_id: str,
    record_sha: str,
) -> dict[str, Any] | None:
    symbol = _text(record.get("symbol"))
    if not symbol:
        return None
    row: dict[str, Any] = {
        "snapshot_key": f"{attempt_id}:{symbol}",
        "attempt_id": attempt_id,
        "symbol": symbol,
        "underlying_asset": _text(record.get("underlying_asset")),
        "trade_direction": _text(record.get("trade_direction")),
        "timeframe": _text(record.get("movement_timeframe"), 32),
        "technical_score": _num(record.get("technical_score")),
        "technical_score_version": _text(record.get("technical_score_version"), 32),
        "directional_long_score": _num(record.get("directional_long_score")),
        "directional_short_score": _num(record.get("directional_short_score")),
        "v2_long_score": _num(record.get("v2_long_score")),
        "v2_short_score": _num(record.get("v2_short_score")),
        "v2_feature_status": _text(record.get("v2_feature_status"), 96) or "NOT_COMPUTED",
        "v2_profile_version": _text(record.get("v2_profile_version"), 64),
        "v2_profile_digest": _text(record.get("v2_profile_digest"), 64),
        "outcome_maturity": "NOT_MATURED",
        "rejection_reason": None,
        "observed_at_utc": observed_at,
        "source_id": source_id,
        # A direct read has no committed offset in the projection; saying so is
        # better than inventing one.
        "byte_offset": None,
        "line_number": None,
        "record_sha256": record_sha,
        "is_verified": 0,
        "components": {
            direction: record.get(field)
            for direction, field in (
                ("LONG", "v2_long_components"),
                ("SHORT", "v2_short_components"),
            )
            if isinstance(record.get(field), dict)
        },
    }
    return row



def thresholds(settings: Any) -> tuple[float, float]:
    """V1/V2 classification thresholds from the newest recorded comparison."""
    rows = comparisons(settings, limit=1)["rows"]
    if rows:
        v1 = rows[0].get("v1_threshold")
        v2 = rows[0].get("v2_threshold")
        return (
            float(v1) if isinstance(v1, (int, float)) else queries.V1_THRESHOLD,
            float(v2) if isinstance(v2, (int, float)) else DEFAULT_V2_THRESHOLD,
        )
    return queries.V1_THRESHOLD, DEFAULT_V2_THRESHOLD


def signals(settings: Any, *, limit: int = 50) -> dict[str, Any]:
    """Per-symbol rows from the newest acquisition batches, classified in place."""
    v1_threshold, v2_threshold = thresholds(settings)
    batches = snapshot_batches(settings)
    limit = max(1, min(int(limit), 200))
    rows: list[dict[str, Any]] = []
    truncated = False
    files: list[str] = []
    for batch in batches:
        files.append(batch["path"])
        if batch["truncated"]:
            truncated = True
        for record, sha in reversed(batch["records"]):
            row = _signal_row(
                record,
                attempt_id=batch["attempt_id"],
                observed_at=batch["observed_at_utc"],
                source_id=batch["source_id"],
                record_sha=sha,
            )
            if row is None:
                continue
            row.update(queries.classify_signal(row, v1_threshold, v2_threshold))
            rows.append(row)
    rows.sort(key=lambda item: (str(item.get("observed_at_utc") or ""), item["symbol"]), reverse=True)
    page = rows[:limit]
    return {
        "status": queries.STATUS_MEASURED if page else queries.STATUS_NO_EVIDENCE,
        "reason_code": None if page else "NO_SCANNER_SNAPSHOT_EVIDENCE",
        "rows": page,
        "page_size": limit,
        "next_cursor": None,
        "v1_threshold": v1_threshold,
        "v2_threshold": v2_threshold,
        "attempts_read": len(batches),
        "rows_available": len(rows),
        "truncated": truncated,
        "files": files,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **provenance(),
    }


def v2_status_counts(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, int] = {}
    for row in rows:
        status = str(row.get("v2_feature_status") or "NOT_COMPUTED")
        grouped[status] = grouped.get(status, 0) + 1

    def bucket(prefix: str) -> int:
        return sum(total for key, total in grouped.items() if key.upper().startswith(prefix))

    return {
        "status": queries.STATUS_MEASURED if grouped else queries.STATUS_NO_EVIDENCE,
        "exact": grouped,
        "READY": grouped.get("READY", 0),
        "INSUFFICIENT_FEATURES": bucket("INSUFFICIENT"),
        "ERROR": bucket("ERROR"),
        "NOT_COMPUTED": grouped.get("NOT_COMPUTED", 0),
        "total": sum(grouped.values()),
    }


def signal_summary(rows: list[Mapping[str, Any]], *, threshold: float) -> dict[str, Any]:
    """Counts over the rows a direct read actually produced."""
    if not rows:
        return {
            "status": queries.STATUS_NO_EVIDENCE,
            "reason_code": "NO_SNAPSHOT_EVIDENCE",
            "symbols_scanned": 0,
            "v2_threshold": threshold,
            "qualified": None,
            "rejected": None,
            "qualification_source": queries.STATUS_UNAVAILABLE,
            "qualification_reason": "NO_QUALIFICATION_EVENT_SOURCE",
        }
    directionals = 0
    ready = 0
    insufficient = 0
    errors = 0
    not_computed = 0
    for row in rows:
        status = str(row.get("v2_feature_status") or "NOT_COMPUTED").upper()
        if status == "READY":
            ready += 1
        elif status.startswith("INSUFFICIENT"):
            insufficient += 1
        elif status.startswith("ERROR"):
            errors += 1
        elif status == "NOT_COMPUTED":
            not_computed += 1
        best = row.get("v2_best_score")
        if isinstance(best, (int, float)) and best >= threshold:
            directionals += 1
    return {
        "status": queries.STATUS_MEASURED,
        "symbols_scanned": len({str(row.get("symbol")) for row in rows}),
        "snapshots": len(rows),
        "attempts_in_window": len({str(row.get("attempt_id")) for row in rows}),
        "v2_ready": ready,
        "v2_directional": directionals,
        "insufficient_features": insufficient,
        "v2_errors": errors,
        "v2_not_computed": not_computed,
        "v2_threshold": threshold,
        "last_observed_at_utc": max(
            (str(row.get("observed_at_utc") or "") for row in rows), default=None
        )
        or None,
        "last_ingested_at_utc": None,
        "qualified": None,
        "rejected": None,
        "qualification_source": queries.STATUS_UNAVAILABLE,
        "qualification_reason": "NO_QUALIFICATION_EVENT_SOURCE",
    }



def funnel(settings: Any, *, limit: int = 5) -> dict[str, Any]:
    """Attempt-by-attempt funnel assembled from canonical files."""
    events = attempt_events(settings)
    batches = {batch["attempt_id"]: batch for batch in snapshot_batches(settings)}
    latest = events.get("latest") or {}
    attempts: list[dict[str, Any]] = []
    for attempt_id, event in list(latest.items())[-max(1, int(limit)):]:
        batch = batches.get(attempt_id) or {}
        rows = [
            row
            for row in (
                _signal_row(
                    record,
                    attempt_id=attempt_id,
                    observed_at=batch.get("observed_at_utc"),
                    source_id=batch.get("source_id") or "direct",
                    record_sha=sha,
                )
                for record, sha in (batch.get("records") or [])
            )
            if row is not None
        ]
        worker = batch.get("worker_status")
        attempts.append(
            {
                "attempt_id": attempt_id,
                "state": event.get("state"),
                "acquisition_outcome": event.get("outcome"),
                "reason_code": event.get("reason_code"),
                "last_event_at_utc": event.get("observed_at_utc"),
                "events": 1,
                "snapshots": len(rows),
                "snapshot_first_at_utc": batch.get("observed_at_utc"),
                "snapshot_last_at_utc": batch.get("observed_at_utc"),
                "v2_status": v2_status_counts(rows),
                "worker_status": worker,
                "worker_status_at_utc": _iso((worker or {}).get("finished_at")) if worker else None,
                "promoted": None,
                "promotion_reason": "NO_PROMOTION_EVENT_SOURCE",
                "qualified": None,
                "qualification_reason": "NO_QUALIFICATION_EVENT_SOURCE",
                "ai_review": None,
                "ai_reason": REASON_NO_AI_SOURCE,
                "outcome_maturity": "NOT_MATURED",
            }
        )
    attempts.sort(key=lambda item: str(item.get("last_event_at_utc") or ""), reverse=True)
    return {
        "status": queries.STATUS_MEASURED if attempts else queries.STATUS_NO_EVIDENCE,
        "reason_code": None if attempts else events.get("reason_code"),
        "attempts_recorded": events.get("attempts") or 0,
        "attempts": attempts,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **provenance(),
    }


def data_coverage(settings: Any) -> dict[str, Any]:
    """Which canonical sources exist right now, and how fresh they are."""
    entries: list[dict[str, Any]] = []
    for descriptor in sources.all_sources(settings):
        path = descriptor.path
        exists = path.is_file()
        size = None
        if exists:
            try:
                size = path.stat().st_size
            except OSError:
                size = None
        entries.append(
            {
                "source_id": descriptor.source_id,
                "record_kind": descriptor.record_kind,
                "path": str(path),
                "exists": exists,
                "bytes": size,
                "last_modified_utc": _mtime_iso(path) if exists else None,
                "projected": False,
                "status": "PRESENT" if exists else "ABSENT",
            }
        )
    present = sum(1 for item in entries if item["exists"])
    return {
        "status": queries.STATUS_MEASURED if entries else queries.STATUS_NO_EVIDENCE,
        "sources": entries,
        "sources_registered": len(entries),
        "sources_present": present,
        "sources_absent": len(entries) - present,
        "retention": None,
        "retention_preview": None,
        "retention_runs": [],
        "quarantine_backlog": 0,
        "aging": {},
        "schema_version": None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **provenance(),
    }


def ai(_settings: Any) -> dict[str, Any]:
    """No AI inference source is registered, so there is nothing to read."""
    return {
        "status": queries.STATUS_NO_EVIDENCE,
        "reason_code": REASON_NO_AI_SOURCE,
        "summary": {
            "status": queries.STATUS_NO_EVIDENCE,
            "reason_code": REASON_NO_AI_SOURCE,
            "inferences": 0,
            "matured_outcomes": 0,
            "win_rate": None,
            "false_signal_rate": None,
            "missed_opportunity_rate": None,
            "latency_ms_p50": None,
            "metrics_reason": REASON_NO_AI_SOURCE,
        },
        "page": {"status": queries.STATUS_NO_EVIDENCE, "rows": []},
        **provenance(),
    }



def forensics(settings: Any, *, attempt_id: str, symbol: str) -> dict[str, Any]:
    """Provenance for one row, read straight from its acquisition batch."""
    wanted = str(attempt_id)
    v1_threshold, v2_threshold = thresholds(settings)
    for batch in snapshot_batches(settings):
        if wanted not in {str(batch["attempt_id"]), str(batch["directory"])}:
            continue
        for record, sha in batch["records"]:
            row = _signal_row(
                record,
                attempt_id=batch["attempt_id"],
                observed_at=batch["observed_at_utc"],
                source_id=batch["source_id"],
                record_sha=sha,
            )
            if row is None or row["symbol"] != str(symbol):
                continue
            row.update(queries.classify_signal(row, v1_threshold, v2_threshold))
            components = [
                {"direction": direction, "component": name, "points": value}
                for direction, values in (row.pop("components") or {}).items()
                for name, value in sorted(values.items())
            ]
            return {
                "status": queries.STATUS_MEASURED,
                "signal": row,
                "components": components,
                "attempt_events": [],
                "worker_status": batch.get("worker_status"),
                "worker_status_provenance": {
                    "source_id": batch["source_id"],
                    "manifest_sha256": batch.get("manifest_sha256"),
                    "observed_at_utc": batch.get("observed_at_utc"),
                },
                "comparison": None,
                "ai_reasoning": None,
                "ai_reason": REASON_NO_AI_SOURCE,
                "missing_fields": ["byte_offset", "line_number", "rejection_reason"],
                "path": batch["path"],
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                **provenance(),
            }
    return {
        "status": queries.STATUS_NO_EVIDENCE,
        "reason_code": "SNAPSHOT_NOT_FOUND",
        "attempt_id": attempt_id,
        "symbol": symbol,
        **provenance(),
    }




def health_block(settings: Any, *, window_days: int = 0, limit: int = 200) -> dict[str, Any]:
    """The projection-shaped summary, assembled from canonical reads.

    Used only when the analytical projection is absent or empty. The block says so
    through ``projection`` and ``verification`` so a direct read can never be
    mistaken for a reconciled KPI.
    """
    signals_payload = signals(settings, limit=limit)
    rows = signals_payload["rows"]
    threshold = signals_payload["v2_threshold"]
    comparison = comparisons(settings, limit=1)
    return {
        "status": queries.STATUS_MEASURED,
        "kpi": signal_summary(rows, threshold=threshold),
        "reconciliation": {
            "state": "UNVERIFIED",
            "reason_code": "PROJECTION_NOT_POPULATED",
            "observed_at_utc": None,
            "sources_checked": 0,
            "sources_drifted": 0,
            "sources_unavailable": 0,
            "drift": [],
            "detail": "no projection exists to reconcile; reads are direct",
        },
        "comparison": comparison["rows"][0] if comparison["rows"] else None,
        "v2_status": v2_status_counts(rows),
        "retention": None,
        "ai": ai(settings)["summary"],
        "schema_version": None,
        "db_bytes": None,
        "wal_bytes": None,
        "window_days": window_days or None,
        "projection": {"status": "UNAVAILABLE", "reason_code": "ANALYTICS_DB_UNAVAILABLE"},
        **provenance(),
    }
