"""Bounded, read-only queries over the analytical projection.

Every function here returns a payload that carries its own status: a value is
either measured, explicitly ``NO_EVIDENCE``, or explicitly ``UNAVAILABLE`` with
a reason. Nothing is coerced to zero and nothing is inferred to look healthy.

Pagination is keyset (row-value comparison), never ``OFFSET``: a growing
evidence store must not make page 20 progressively more expensive.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any

from app.opip.analytics import retention
from app.opip.analytics.reconcile import latest_status as reconciliation_status
from app.opip.analytics.schema import SCHEMA_VERSION, current_version
from app.opip.analytics.store import database_size_bytes

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

#: V1 production selection threshold as recorded by the comparison contract.
V1_THRESHOLD = 80.0

STATUS_MEASURED = "MEASURED"
STATUS_NO_EVIDENCE = "NO_EVIDENCE"
STATUS_UNAVAILABLE = "UNAVAILABLE"

DIVERGENCE_BOTH = "BOTH"
DIVERGENCE_V1_ONLY = "V1_ONLY"
DIVERGENCE_V2_ONLY = "V2_ONLY"
DIVERGENCE_NEITHER = "NEITHER"
DIVERGENCE_INSUFFICIENT = "INSUFFICIENT_FEATURES"
DIVERGENCE_NOT_COMPUTED = "V2_NOT_COMPUTED"
DIVERGENCE_UNMATURED = "OUTCOME_NOT_MATURED"

_V2_MAX = "MAX(COALESCE(v2_long_score, -1.0), COALESCE(v2_short_score, -1.0))"


def _utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _rows(cursor: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor]


def _clamp_page(limit: Any, default: int = DEFAULT_PAGE_SIZE) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_PAGE_SIZE))


def divergence_clause(kind: str, v1: float, v2: float) -> tuple[str, tuple[Any, ...]]:
    """Map a divergence filter onto a parameterized SQL predicate."""
    normalised = str(kind or "").strip().upper()
    if normalised == DIVERGENCE_INSUFFICIENT:
        return "v2_feature_status LIKE 'INSUFFICIENT%'", ()
    if normalised == DIVERGENCE_NOT_COMPUTED:
        return "v2_feature_status = 'NOT_COMPUTED'", ()
    if normalised == DIVERGENCE_BOTH:
        return (
            f"COALESCE(technical_score, 0) >= ? AND {_V2_MAX} >= ?",
            (v1, v2),
        )
    if normalised == DIVERGENCE_V1_ONLY:
        return (
            f"COALESCE(technical_score, 0) >= ? AND {_V2_MAX} < ?",
            (v1, v2),
        )
    if normalised == DIVERGENCE_V2_ONLY:
        return (
            f"COALESCE(technical_score, 0) < ? AND {_V2_MAX} >= ?",
            (v1, v2),
        )
    if normalised == DIVERGENCE_NEITHER:
        return (
            f"COALESCE(technical_score, 0) < ? AND {_V2_MAX} < ?",
            (v1, v2),
        )
    raise ValueError("UNKNOWN_DIVERGENCE_FILTER")


def encode_cursor(row: dict[str, Any]) -> str:
    """Opaque, URL-safe keyset cursor.

    The key contains an ISO timestamp with ``+00:00``; embedded raw in a query
    string the ``+`` decodes to a space and the comparison silently matches
    nothing. Base64url keeps the cursor safe for any client and any proxy.
    """
    raw = "|".join(
        [
            str(row.get("observed_at_utc") or ""),
            str(row.get("attempt_id") or ""),
            str(row.get("symbol") or ""),
        ]
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[str, str, str] | None:
    if not cursor:
        return None
    text = str(cursor).strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        decoded = None
    if decoded is not None:
        parts = decoded.split("|")
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
    # Legacy plain cursors remain readable so an open console is not broken.
    parts = text.split("|")
    if len(parts) != 3:
        raise ValueError("INVALID_CURSOR")
    return parts[0], parts[1], parts[2]


def latest_comparison(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT comparison_key, scan_id, model_authority, v1_threshold, v2_threshold, "
        "scanned, v2_ready, v1_top_json, v2_top_json, v1_only_json, v2_only_json, "
        "outcome_status, observed_at_utc, source_id, byte_offset, line_number, "
        "record_sha256 FROM scoring_comparisons "
        "ORDER BY COALESCE(observed_at_utc, ingested_at_utc) DESC, comparison_key "
        "LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None


def thresholds(conn: Any) -> tuple[float, float]:
    latest = latest_comparison(conn)
    if latest is None:
        return V1_THRESHOLD, 75.0
    v1 = latest.get("v1_threshold")
    v2 = latest.get("v2_threshold")
    return (
        float(v1) if isinstance(v1, (int, float)) else V1_THRESHOLD,
        float(v2) if isinstance(v2, (int, float)) else 75.0,
    )



DIVERGENCE_ERROR = "V2_EVALUATION_ERROR"

_SIGNAL_COLUMNS = (
    "snapshot_key, attempt_id, symbol, underlying_asset, trade_direction, timeframe, "
    "technical_score, technical_score_version, directional_long_score, "
    "directional_short_score, v2_long_score, v2_short_score, v2_feature_status, "
    "v2_profile_version, v2_profile_digest, outcome_maturity, rejection_reason, "
    "observed_at_utc, source_id, byte_offset, line_number, record_sha256, is_verified"
)


def classify_signal(
    row: dict[str, Any], v1_threshold: float, v2_threshold: float
) -> dict[str, Any]:
    """Classify one snapshot into exactly one divergence state.

    A score that was never computed stays ``None`` - never ``0`` - so a delta is
    only produced when both scores legitimately exist and are comparable.
    """
    v2_scores = [
        value
        for value in (row.get("v2_long_score"), row.get("v2_short_score"))
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    v2_best = max(v2_scores) if v2_scores else None
    v1_score = row.get("technical_score")
    v1_value = (
        float(v1_score)
        if isinstance(v1_score, (int, float)) and not isinstance(v1_score, bool)
        else None
    )
    status = str(row.get("v2_feature_status") or "NOT_COMPUTED").upper()
    if v2_best is None:
        if status.startswith("ERROR"):
            divergence = DIVERGENCE_ERROR
        elif status.startswith("INSUFFICIENT"):
            divergence = DIVERGENCE_INSUFFICIENT
        else:
            divergence = DIVERGENCE_NOT_COMPUTED
    else:
        v1_pass = v1_value is not None and v1_value >= v1_threshold
        v2_pass = v2_best >= v2_threshold
        if v1_pass and v2_pass:
            divergence = DIVERGENCE_BOTH
        elif v1_pass:
            divergence = DIVERGENCE_V1_ONLY
        elif v2_pass:
            divergence = DIVERGENCE_V2_ONLY
        else:
            divergence = DIVERGENCE_NEITHER
    delta = (
        round(v2_best - v1_value, 2)
        if v2_best is not None and v1_value is not None
        else None
    )
    return {
        "divergence": divergence,
        "v2_best_score": v2_best,
        "v2_score_delta": delta,
        "outcome_maturity": row.get("outcome_maturity") or DIVERGENCE_UNMATURED,
        "verified": bool(row.get("is_verified")),
    }


def signals_page(
    conn: Any,
    *,
    symbols: list[str] | None = None,
    direction: str | None = None,
    v2_status: str | None = None,
    divergence: str | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    observed_from: str | None = None,
    observed_to: str | None = None,
    attempt_id: str | None = None,
    limit: Any = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, Any]:
    """One keyset page of signal rows with explicit divergence classification."""
    v1_threshold, v2_threshold = thresholds(conn)
    clauses: list[str] = []
    params: list[Any] = []
    if symbols:
        cleaned = [str(item)[:32] for item in symbols if str(item).strip()][:25]
        if cleaned:
            clauses.append("symbol IN (" + ",".join("?" for _ in cleaned) + ")")
            params.extend(cleaned)
    if direction in {"LONG", "SHORT"}:
        clauses.append("UPPER(COALESCE(trade_direction, '')) = ?")
        params.append(direction)
    if v2_status:
        clauses.append("v2_feature_status = ?")
        params.append(str(v2_status)[:96])
    if attempt_id:
        clauses.append("attempt_id = ?")
        params.append(str(attempt_id)[:64])
    if divergence:
        clause, extra = divergence_clause(divergence, v1_threshold, v2_threshold)
        clauses.append(clause)
        params.extend(extra)
    if min_score is not None:
        clauses.append("COALESCE(technical_score, -1.0) >= ?")
        params.append(float(min_score))
    if max_score is not None:
        clauses.append("COALESCE(technical_score, -1.0) <= ?")
        params.append(float(max_score))
    if observed_from:
        clauses.append("COALESCE(observed_at_utc, '') >= ?")
        params.append(str(observed_from))
    if observed_to:
        clauses.append("COALESCE(observed_at_utc, '') <= ?")
        params.append(str(observed_to))
    decoded = decode_cursor(cursor)
    if decoded is not None:
        # ORDER BY is (observed_at DESC, attempt_id DESC, symbol ASC), so the
        # keyset predicate must mirror each direction. A single same-direction
        # tuple comparison would repeat or skip rows whose sort keys tie.
        clauses.append(
            "(COALESCE(observed_at_utc, '') < ?"
            " OR (COALESCE(observed_at_utc, '') = ? AND attempt_id < ?)"
            " OR (COALESCE(observed_at_utc, '') = ? AND attempt_id = ? AND symbol > ?))"
        )
        params.extend(
            [
                decoded[0],
                decoded[0],
                decoded[1],
                decoded[0],
                decoded[1],
                decoded[2],
            ]
        )
    where = " AND ".join(clauses) if clauses else "1 = 1"
    page_size = _clamp_page(limit)
    rows = _rows(
        conn.execute(
            f"SELECT {_SIGNAL_COLUMNS} FROM scanner_snapshots WHERE {where} "  # nosec B608 - identifiers are module constants, values bound
            "ORDER BY COALESCE(observed_at_utc, '') DESC, attempt_id DESC, symbol ASC "
            "LIMIT ?",
            (*params, page_size + 1),
        )
    )
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    for row in rows:
        row.update(classify_signal(row, v1_threshold, v2_threshold))
    return {
        "status": STATUS_MEASURED if rows else STATUS_NO_EVIDENCE,
        "rows": rows,
        "page_size": page_size,
        "next_cursor": encode_cursor(rows[-1]) if has_more and rows else None,
        "v1_threshold": v1_threshold,
        "v2_threshold": v2_threshold,
        "generated_at_utc": _utc().isoformat(),
    }


def v2_status_counts(conn: Any, *, attempt_id: str | None = None) -> dict[str, Any]:
    """Preserve the recorded status verbatim, including ``ERROR:ExceptionType``."""
    clause = "WHERE attempt_id = ?" if attempt_id else ""
    params: tuple[Any, ...] = (attempt_id,) if attempt_id else ()
    rows = _rows(
        conn.execute(
            "SELECT COALESCE(v2_feature_status, 'NOT_COMPUTED') AS status, "  # nosec B608 - clause is a constant, values are bound
            f"COUNT(*) AS total FROM scanner_snapshots {clause} "
            "GROUP BY COALESCE(v2_feature_status, 'NOT_COMPUTED') ORDER BY status",
            params,
        )
    )
    grouped = {str(row["status"]): int(row["total"]) for row in rows}

    def bucket(prefix: str) -> int:
        return sum(
            total
            for status, total in grouped.items()
            if status.upper().startswith(prefix)
        )

    return {
        "status": STATUS_MEASURED if grouped else STATUS_NO_EVIDENCE,
        "exact": grouped,
        "READY": grouped.get("READY", 0),
        "INSUFFICIENT_FEATURES": bucket("INSUFFICIENT"),
        "ERROR": bucket("ERROR"),
        "NOT_COMPUTED": grouped.get("NOT_COMPUTED", 0),
        "total": sum(grouped.values()),
    }




_V2_STATUS = "UPPER(COALESCE(v2_feature_status, ''))"
_V2_MAX_SQL = _V2_MAX


def signal_summary(conn: Any, *, observed_from: str | None = None) -> dict[str, Any]:
    """KPI counts for the latest observation window.

    ``qualified`` and ``rejected`` are intentionally not derived from a score
    threshold: qualification is a downstream pipeline decision with no ingestion
    source in this increment, so they are reported unavailable instead of being
    approximated by scoring.
    """
    _v1, v2_threshold = thresholds(conn)
    where = "WHERE COALESCE(observed_at_utc, '') >= ?" if observed_from else ""
    params: tuple[Any, ...] = (observed_from,) if observed_from else ()
    sql = ("SELECT COUNT(*) AS snapshots, COUNT(DISTINCT symbol) AS symbols, "  # nosec B608 - fragments are module constants
           "COUNT(DISTINCT attempt_id) AS attempts, "
           f"SUM(CASE WHEN {_V2_STATUS} = 'READY' THEN 1 ELSE 0 END) AS v2_ready, "
           f"SUM(CASE WHEN {_V2_MAX_SQL} >= ? THEN 1 ELSE 0 END) AS v2_directional, "
           f"SUM(CASE WHEN {_V2_STATUS} LIKE 'INSUFFICIENT%' THEN 1 ELSE 0 END) AS insufficient, "
           f"SUM(CASE WHEN {_V2_STATUS} LIKE 'ERROR%' THEN 1 ELSE 0 END) AS errors, "
           f"SUM(CASE WHEN {_V2_STATUS} = 'NOT_COMPUTED' THEN 1 ELSE 0 END) AS not_computed, "
           "MAX(observed_at_utc) AS last_observed_at_utc, "
           "MAX(ingested_at_utc) AS last_ingested_at_utc "
           f"FROM scanner_snapshots {where}")
    row = conn.execute(sql, (v2_threshold, *params)).fetchone()
    attempts_row = conn.execute(
        "SELECT COUNT(*) AS total FROM ("
        "SELECT attempt_id FROM scanner_attempt_events GROUP BY attempt_id)"
    ).fetchone()
    if row is None or int(row["snapshots"] or 0) == 0:
        return {
            "status": STATUS_NO_EVIDENCE,
            "reason_code": "NO_SNAPSHOT_EVIDENCE",
            "symbols_scanned": 0,
            "v2_threshold": v2_threshold,
            "attempts_recorded": int(attempts_row["total"] or 0) if attempts_row else 0,
            "qualified": None,
            "rejected": None,
            "qualification_source": STATUS_UNAVAILABLE,
            "qualification_reason": "NO_QUALIFICATION_EVENT_SOURCE",
        }
    return {
        "status": STATUS_MEASURED,
        "symbols_scanned": int(row["symbols"] or 0),
        "snapshots": int(row["snapshots"] or 0),
        "attempts_in_window": int(row["attempts"] or 0),
        "attempts_recorded": int(attempts_row["total"] or 0) if attempts_row else 0,
        "v2_ready": int(row["v2_ready"] or 0),
        "v2_directional": int(row["v2_directional"] or 0),
        "insufficient_features": int(row["insufficient"] or 0),
        "v2_errors": int(row["errors"] or 0),
        "v2_not_computed": int(row["not_computed"] or 0),
        "v2_threshold": v2_threshold,
        "last_observed_at_utc": row["last_observed_at_utc"],
        "last_ingested_at_utc": row["last_ingested_at_utc"],
        "qualified": None,
        "rejected": None,
        "qualification_source": STATUS_UNAVAILABLE,
        "qualification_reason": "NO_QUALIFICATION_EVENT_SOURCE",
    }


def _loads(raw: Any) -> Any:
    import json

    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def comparison_rows(conn: Any, *, limit: int = 12) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    rows = _rows(
        conn.execute(
            "SELECT comparison_key, scan_id, model_authority, v1_threshold, "
            "v2_threshold, scanned, v2_ready, v1_top_json, v2_top_json, "
            "v1_only_json, v2_only_json, outcome_status, observed_at_utc, "
            "source_id, byte_offset, line_number, record_sha256 "
            "FROM scoring_comparisons "
            "ORDER BY COALESCE(observed_at_utc, ingested_at_utc) DESC LIMIT ?",
            (limit,),
        )
    )
    for row in rows:
        for field in ("v1_top", "v2_top", "v1_only", "v2_only"):
            raw = row.pop(f"{field}_json", None)
            row[field] = _loads(raw)
    return {
        "status": STATUS_MEASURED if rows else STATUS_NO_EVIDENCE,
        "rows": rows,
        "limit": limit,
        "generated_at_utc": _utc().isoformat(),
    }


def scanner_funnel(conn: Any, *, limit: int = 10) -> dict[str, Any]:
    """The operational funnel, attempt by attempt, from recorded evidence only.

    A stage with no evidence source is reported ``null`` with a reason rather
    than being filled in from the stage before it. "A scan ran" is not evidence
    that admission, promotion or qualification happened.
    """
    limit = max(1, min(int(limit), 25))
    attempts = _rows(
        conn.execute(
            "SELECT attempt_id, MAX(COALESCE(observed_at_utc, '')) AS last_event_at "
            "FROM scanner_attempt_events GROUP BY attempt_id "
            "ORDER BY last_event_at DESC LIMIT ?",
            (limit,),
        )
    )
    rendered: list[dict[str, Any]] = []
    for attempt in attempts:
        attempt_id = str(attempt["attempt_id"])
        events = _rows(
            conn.execute(
                "SELECT event_type, state, outcome, reason_code, event_seq, "
                "observed_at_utc, container_id FROM scanner_attempt_events "
                "WHERE attempt_id = ? "
                "ORDER BY COALESCE(observed_at_utc, ''), event_seq",
                (attempt_id,),
            )
        )
        latest = events[-1] if events else {}
        snapshot_row = conn.execute(
            "SELECT COUNT(*) AS total, MIN(observed_at_utc) AS first_at, "
            "MAX(observed_at_utc) AS last_at FROM scanner_snapshots "
            "WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        telemetry = conn.execute(
            "SELECT payload_json, observed_at_utc FROM operational_telemetry "
            "WHERE section = 'worker_status' AND attempt_id = ? "
            "ORDER BY COALESCE(observed_at_utc, '') DESC LIMIT 1",
            (attempt_id,),
        ).fetchone()
        rendered.append(
            {
                "attempt_id": attempt_id,
                "state": latest.get("state"),
                "acquisition_outcome": latest.get("outcome"),
                "reason_code": latest.get("reason_code"),
                "last_event_at_utc": attempt["last_event_at"] or None,
                "events": len(events),
                "snapshots": int(snapshot_row["total"] or 0) if snapshot_row else 0,
                "snapshot_first_at_utc": snapshot_row["first_at"] if snapshot_row else None,
                "snapshot_last_at_utc": snapshot_row["last_at"] if snapshot_row else None,
                "v2_status": v2_status_counts(conn, attempt_id=attempt_id),
                "worker_status": _loads(telemetry["payload_json"]) if telemetry else None,
                "worker_status_at_utc": telemetry["observed_at_utc"] if telemetry else None,
                "promoted": None,
                "promotion_reason": "NO_PROMOTION_EVENT_SOURCE",
                "qualified": None,
                "qualification_reason": "NO_QUALIFICATION_EVENT_SOURCE",
                "ai_review": None,
                "ai_reason": "NO_AI_INFERENCE_SOURCE",
                "outcome_maturity": DIVERGENCE_UNMATURED,
            }
        )
    totals = conn.execute(
        "SELECT COUNT(DISTINCT attempt_id) AS attempts FROM scanner_attempt_events"
    ).fetchone()
    return {
        "status": STATUS_MEASURED if rendered else STATUS_NO_EVIDENCE,
        "attempts_recorded": int(totals["attempts"] or 0) if totals else 0,
        "attempts": rendered,
        "generated_at_utc": _utc().isoformat(),
    }



def ai_summary(conn: Any) -> dict[str, Any]:
    """AI inference evidence, or an explicit statement that there is none.

    No inference is ever synthesised. An empty inference table is reported as
    ``NO_EVIDENCE`` with its reason, so every performance figure is unavailable
    instead of being zero.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN outcome_maturity = 'MATURED' THEN 1 ELSE 0 END) AS matured, "
        "SUM(CASE WHEN confidence IS NOT NULL THEN 1 ELSE 0 END) AS with_confidence, "
        "SUM(CASE WHEN duration_ms IS NOT NULL THEN 1 ELSE 0 END) AS with_latency, "
        "MIN(observed_at_utc) AS first_at, MAX(observed_at_utc) AS last_at "
        "FROM ai_inferences"
    ).fetchone()
    models = conn.execute(
        "SELECT model, model_version, COUNT(*) AS total FROM ai_inferences "
        "WHERE model IS NOT NULL GROUP BY model, model_version "
        "ORDER BY total DESC LIMIT 5"
    ).fetchall()
    total = int(row["total"] or 0) if row else 0
    return {
        "status": STATUS_MEASURED if total else STATUS_NO_EVIDENCE,
        "reason_code": None if total else "NO_AI_INFERENCE_SOURCE",
        "inferences": total,
        "matured_outcomes": int(row["matured"] or 0) if row else 0,
        "with_confidence": int(row["with_confidence"] or 0) if row else 0,
        "with_latency": int(row["with_latency"] or 0) if row else 0,
        "first_observed_at_utc": row["first_at"] if row else None,
        "last_observed_at_utc": row["last_at"] if row else None,
        "models": [dict(item) for item in models],
        "win_rate": None,
        "false_signal_rate": None,
        "missed_opportunity_rate": None,
        "latency_ms_p50": None,
        "metrics_reason": "OUTCOMES_NOT_MATURED" if total else "NO_AI_INFERENCE_SOURCE",
    }


def ai_page(conn: Any, *, limit: Any = DEFAULT_PAGE_SIZE) -> dict[str, Any]:
    page = _clamp_page(limit)
    rows = _rows(
        conn.execute(
            "SELECT inference_key, symbol, direction, provider, model, model_version, "
            "verdict, confidence, agreement_state, duration_ms, outcome_maturity, "
            "verified_result, request_ref, observed_at_utc, source_id, byte_offset, "
            "line_number, record_sha256 FROM ai_inferences "
            "ORDER BY COALESCE(observed_at_utc, ingested_at_utc) DESC LIMIT ?",
            (page,),
        )
    )
    return {
        "status": STATUS_MEASURED if rows else STATUS_NO_EVIDENCE,
        "reason_code": None if rows else "NO_AI_INFERENCE_SOURCE",
        "rows": rows,
        "page_size": page,
        "generated_at_utc": _utc().isoformat(),
    }




def data_health(
    conn: Any, *, db_path: Any = None, now: datetime | None = None
) -> dict[str, Any]:
    """Source coverage, ingestion lag, quarantine, aging and retention state."""
    moment = _utc(now)
    main_bytes, wal_bytes = (
        database_size_bytes(db_path) if db_path is not None else (None, None)
    )
    sources = _rows(
        conn.execute(
            """
            SELECT s.source_id, s.record_kind, s.display_name, s.path,
                   c.generation, c.file_size, c.last_offset, c.records_committed,
                   c.last_verified_at_utc, c.updated_at_utc,
                   (SELECT COUNT(*) FROM quarantined_records q
                     WHERE q.source_id = s.source_id) AS quarantined,
                   (SELECT COUNT(*) FROM ingested_records r
                     WHERE r.source_id = s.source_id) AS projected_records
            FROM ingestion_sources s
            LEFT JOIN ingestion_checkpoints c ON c.source_id = s.source_id
            ORDER BY s.source_id
            """
        )
    )
    last_runs = {
        str(row["source_id"]): row
        for row in _rows(
            conn.execute(
                "SELECT source_id, MAX(started_at_utc) AS last_run_at_utc, "
                "SUM(records_inserted) AS inserted FROM ingestion_events "
                "GROUP BY source_id"
            )
        )
    }
    for source in sources:
        last = last_runs.get(str(source["source_id"]), {})
        source["last_ingest_at_utc"] = last.get("last_run_at_utc")
        source["records_inserted_total"] = int(last.get("inserted") or 0)
        lag = None
        if last.get("last_run_at_utc"):
            try:
                parsed = datetime.fromisoformat(
                    str(last["last_run_at_utc"]).replace("Z", "+00:00")
                )
                lag = max(0.0, (moment - parsed).total_seconds())
            except ValueError:
                lag = None
        source["ingestion_lag_seconds"] = lag
        source["status"] = (
            "NEVER_INGESTED" if source["last_ingest_at_utc"] is None else "INGESTED"
        )
    quarantine = conn.execute(
        "SELECT COUNT(*) AS total, MAX(ingested_at_utc) AS last_at "
        "FROM quarantined_records"
    ).fetchone()
    day = (moment - timedelta(days=1)).isoformat()
    week = (moment - timedelta(days=7)).isoformat()
    month = (moment - timedelta(days=30)).isoformat()
    aging_rows = _rows(
        conn.execute(
            """
            SELECT CASE
                     WHEN observed_at_utc IS NULL THEN 'UNKNOWN'
                     WHEN observed_at_utc >= ? THEN 'LAST_24H'
                     WHEN observed_at_utc >= ? THEN 'LAST_7D'
                     WHEN observed_at_utc >= ? THEN 'LAST_30D'
                     ELSE 'OLDER_THAN_30D' END AS bucket,
                   COUNT(*) AS total
            FROM scanner_snapshots GROUP BY bucket
            """,
            (day, week, month),
        )
    )
    return {
        "status": STATUS_MEASURED if sources else STATUS_NO_EVIDENCE,
        "schema_version": current_version(conn),
        "expected_schema_version": SCHEMA_VERSION,
        "db_bytes": main_bytes,
        "wal_bytes": wal_bytes,
        "sources": sources,
        "quarantine_backlog": int(quarantine["total"] or 0) if quarantine else 0,
        "quarantine_last_at_utc": quarantine["last_at"] if quarantine else None,
        "aging": {str(row["bucket"]): int(row["total"]) for row in aging_rows},
        "retention": retention.current_policy(conn),
        "retention_preview": retention.preview(conn, now=moment).as_dict(),
        "retention_runs": retention.recent_runs(conn, limit=5),
        "reconciliation": reconciliation_status(conn),
        "generated_at_utc": moment.isoformat(),
    }


def forensics(conn: Any, *, attempt_id: str, symbol: str) -> dict[str, Any]:
    """Full available provenance for one analytical row, with gaps named."""
    row = conn.execute(
        f"SELECT {_SIGNAL_COLUMNS} FROM scanner_snapshots "  # nosec B608 - identifiers are module constants, values bound
        "WHERE attempt_id = ? AND symbol = ? LIMIT 1",
        (str(attempt_id)[:64], str(symbol)[:32]),
    ).fetchone()
    if row is None:
        return {
            "status": STATUS_NO_EVIDENCE,
            "reason_code": "SNAPSHOT_NOT_FOUND",
            "attempt_id": attempt_id,
            "symbol": symbol,
        }
    signal = dict(row)
    signal.update(classify_signal(signal, *thresholds(conn)))
    components = _rows(
        conn.execute(
            "SELECT direction, component, points FROM scoring_components "
            "WHERE snapshot_key = ? ORDER BY direction, component",
            (signal["snapshot_key"],),
        )
    )
    events = _rows(
        conn.execute(
            "SELECT event_type, state, outcome, reason_code, event_seq, "
            "observed_at_utc FROM scanner_attempt_events WHERE attempt_id = ? "
            "ORDER BY COALESCE(observed_at_utc, ''), event_seq",
            (signal["attempt_id"],),
        )
    )
    telemetry = conn.execute(
        "SELECT payload_json, observed_at_utc, source_id, record_sha256 "
        "FROM operational_telemetry WHERE section = 'worker_status' "
        "AND attempt_id = ? ORDER BY COALESCE(observed_at_utc, '') DESC LIMIT 1",
        (signal["attempt_id"],),
    ).fetchone()
    comparison = conn.execute(
        "SELECT comparison_key, scan_id, model_authority, v1_threshold, "
        "v2_threshold, scanned, v2_ready, outcome_status, observed_at_utc, "
        "source_id, byte_offset, line_number, record_sha256 "
        "FROM scoring_comparisons WHERE scan_id = ? "
        "ORDER BY COALESCE(observed_at_utc, ingested_at_utc) DESC LIMIT 1",
        (signal["attempt_id"],),
    ).fetchone()
    optional_fields = {
        "underlying_asset": signal.get("underlying_asset"),
        "timeframe": signal.get("timeframe"),
        "v2_profile_version": signal.get("v2_profile_version"),
        "v2_profile_digest": signal.get("v2_profile_digest"),
        "rejection_reason": signal.get("rejection_reason"),
        "byte_offset": signal.get("byte_offset"),
        "line_number": signal.get("line_number"),
    }
    return {
        "status": STATUS_MEASURED,
        "signal": signal,
        "components": components,
        "attempt_events": events,
        "worker_status": _loads(telemetry["payload_json"]) if telemetry else None,
        "worker_status_provenance": (
            {
                "source_id": telemetry["source_id"],
                "record_sha256": telemetry["record_sha256"],
                "observed_at_utc": telemetry["observed_at_utc"],
            }
            if telemetry
            else None
        ),
        "comparison": dict(comparison) if comparison is not None else None,
        "ai_reasoning": None,
        "ai_reason": "NO_AI_INFERENCE_SOURCE",
        "missing_fields": [
            name for name, value in optional_fields.items() if value is None
        ],
        "generated_at_utc": _utc().isoformat(),
    }

