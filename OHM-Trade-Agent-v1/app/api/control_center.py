"""Authenticated, bounded read API for the O'Pip Control Center V2.

Every endpoint here is a **read** except two explicit maintenance POSTs and the
configuration staging POST, which require the same dual credential the existing
strategy-administration endpoints already require.

Design rules enforced in this module:

* the reader connection is opened ``mode=ro`` with ``query_only`` set, so a GET
  cannot mutate the projection even if a query is wrong;
* a missing projection is reported as ``UNAVAILABLE`` with a reason, never as an
  empty dataset and never as a 500;
* every response names its own status and the source timestamps it used;
* no response contains a secret, a credential or a raw provider payload.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import sqlite3
import time
from typing import Any, Iterator

from fastapi import APIRouter, Body, Header, HTTPException, Query

from app.core.config import get_settings
from app.opip.analytics import queries, reconcile, retention, runtime
from app.opip.analytics import direct
from app.opip.analytics import sources as source_registry
from app.opip.analytics.ingest import ingest_all
from app.opip.analytics.store import (
    AnalyticsStoreError,
    connect_reader,
    connect_writer,
    database_size_bytes,
)
from app.opip.config import staging as config_staging
from app.opip.config.validation import validate_document

router = APIRouter()

REASON_DB_UNAVAILABLE = "ANALYTICS_DB_UNAVAILABLE"
REASON_QUERY_TIMEOUT = "ANALYTICS_QUERY_TIMEOUT"


def _require_secret(value: str | None) -> None:
    """Delegate to the single operator-credential authority.

    The import is deferred so this module stays importable on a workstation
    without POSIX-only dependencies; the rule itself is never re-implemented
    here, because a second copy of an authentication rule can drift.
    """
    from app.api.dashboard import _require_secret as implementation

    implementation(value)


def _require_strategy_admin(
    operator_secret: str | None, admin_secret: str | None
) -> None:
    """Delegate to the single dual-credential strategy-admin authority."""
    from app.api.dashboard import _require_strategy_admin as implementation

    implementation(operator_secret, admin_secret)


def _db_path():
    return source_registry.analytics_db_path(get_settings())


@contextmanager
def reader(timeout_seconds: float | None = None) -> Iterator[sqlite3.Connection | None]:
    """Yield a read-only connection, or ``None`` when the projection is absent."""
    settings = get_settings()
    limit = float(
        timeout_seconds
        if timeout_seconds is not None
        else getattr(settings, "opip_analytics_query_timeout_seconds", 5.0)
    )
    try:
        conn = connect_reader(
            _db_path(),
            busy_timeout_ms=int(getattr(settings, "opip_analytics_busy_timeout_ms", 5000)),
        )
    except AnalyticsStoreError:
        yield None
        return
    deadline = time.monotonic() + max(0.1, limit)
    try:
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 5_000
        )
        yield conn
    finally:
        conn.set_progress_handler(None, 0)
        conn.close()


def _unavailable(reason: str = REASON_DB_UNAVAILABLE) -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE",
        "reason_code": reason,
        "db_path": str(_db_path()),
        "generated_at_utc": runtime._utc().isoformat(),
    }


def _note_fallback(
    payload: dict[str, Any], projection_status: str, reason_code: str | None
) -> dict[str, Any]:
    """Stamp a read-through payload with the projection state that caused it."""
    payload["projection"] = {"status": projection_status, "reason_code": reason_code}
    payload["projection_fallback"] = True
    return payload


def _guarded(
    payload_builder,
    *,
    fallback=None,
    reason: str = REASON_DB_UNAVAILABLE,
) -> dict[str, Any]:
    """Run a projection read, converting absence or a timeout into a reason.

    When ``fallback`` is supplied and the projection cannot answer (absent, timed
    out, or holding no rows for this surface), the read-through to canonical
    evidence answers instead - labelled ``CANONICAL_DIRECT`` / ``UNPROJECTED`` so it
    is never presented as a reconciled projection value.
    """
    with reader() as conn:
        if conn is None:
            if fallback is None:
                return _unavailable(reason)
            return _note_fallback(fallback(), "UNAVAILABLE", reason)
        try:
            payload = payload_builder(conn)
        except sqlite3.OperationalError as exc:
            if fallback is not None:
                return _note_fallback(fallback(), "UNAVAILABLE", REASON_QUERY_TIMEOUT)
            if "interrupted" in str(exc).lower():
                return _unavailable(REASON_QUERY_TIMEOUT)
            return _unavailable(f"ANALYTICS_QUERY_FAILED_{type(exc).__name__.upper()}")
        except AnalyticsStoreError:
            if fallback is not None:
                return _note_fallback(fallback(), "UNAVAILABLE", reason)
            return _unavailable()
    if fallback is not None and payload.get("status") in {
        "UNAVAILABLE",
        "NO_EVIDENCE",
    }:
        direct_payload = fallback()
        if direct_payload.get("status") == "MEASURED":
            return _note_fallback(
                direct_payload, str(payload.get("status")), payload.get("reason_code")
            )
    return payload


@router.get("/api/opip/control-center/health")
def control_center_health(
    window_days: int = Query(default=0, ge=0, le=90),
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Global header plus the KPI ribbon, from runtime evidence and the projection."""
    _require_secret(x_webhook_secret)
    settings = get_settings()
    evidence = runtime.runtime_evidence(settings)
    observed_from = None
    if window_days:
        observed_from = (runtime._utc() - timedelta(days=int(window_days))).isoformat()
    with reader() as conn:
        if conn is None:
            projection: dict[str, Any] = direct.health_block(
                settings, window_days=window_days
            )
            projection["projection_fallback"] = True
        else:
            try:
                projection = {
                    "status": "MEASURED",
                    "kpi": queries.signal_summary(conn, observed_from=observed_from),
                    "reconciliation": reconcile.latest_status(conn),
                    "comparison": queries.latest_comparison(conn),
                    "v2_status": queries.v2_status_counts(conn),
                    "retention": retention.current_policy(conn),
                    "ai": queries.ai_summary(conn),
                    "schema_version": queries.current_version(conn),
                }
                main_bytes, wal_bytes = database_size_bytes(_db_path())
                projection["db_bytes"] = main_bytes
                projection["wal_bytes"] = wal_bytes
                if projection["kpi"].get("status") != "MEASURED":
                    # The projection exists but holds no rows for the window: read
                    # through to canonical evidence instead of showing nothing.
                    fallback = direct.health_block(settings, window_days=window_days)
                    if fallback["kpi"].get("status") == "MEASURED":
                        fallback["projection"] = {
                            "status": projection["kpi"].get("status"),
                            "reason_code": projection["kpi"].get("reason_code"),
                        }
                        fallback["projection_fallback"] = True
                        fallback["db_bytes"] = main_bytes
                        fallback["wal_bytes"] = wal_bytes
                        projection = fallback
            except sqlite3.OperationalError as exc:
                projection = {
                    "status": "UNAVAILABLE",
                    "reason_code": (
                        REASON_QUERY_TIMEOUT
                        if "interrupted" in str(exc).lower()
                        else f"ANALYTICS_QUERY_FAILED_{type(exc).__name__.upper()}"
                    ),
                }
    return {
        "status": "MEASURED",
        "runtime": evidence,
        "projection": projection,
        "window_days": window_days or None,
        "generated_at_utc": runtime._utc().isoformat(),
    }


def _direct_signals(settings, *, symbols, direction, v2_status, divergence,
                    min_score, max_score, limit) -> dict[str, Any]:
    """Apply the same filters to a direct read as the projection query would."""
    payload = direct.signals(settings, limit=200)
    rows = payload.get("rows") or []
    if symbols:
        wanted = {str(item).upper() for item in symbols}
        rows = [row for row in rows if str(row.get("symbol", "")).upper() in wanted]
    if direction in {"LONG", "SHORT"}:
        rows = [row for row in rows if str(row.get("trade_direction", "")).upper() == direction]
    if v2_status:
        rows = [row for row in rows if str(row.get("v2_feature_status")) == v2_status]
    if divergence:
        rows = [row for row in rows if str(row.get("divergence")) == divergence]
    if min_score is not None:
        rows = [
            row
            for row in rows
            if isinstance(row.get("technical_score"), (int, float))
            and row["technical_score"] >= float(min_score)
        ]
    if max_score is not None:
        rows = [
            row
            for row in rows
            if isinstance(row.get("technical_score"), (int, float))
            and row["technical_score"] <= float(max_score)
        ]
    page = rows[: max(1, min(int(limit), 200))]
    payload["rows"] = page
    payload["rows_available"] = len(rows)
    if not page:
        payload["status"] = "NO_EVIDENCE"
        payload["reason_code"] = "NO_ROWS_MATCH_FILTERS"
    return payload


@router.get("/api/opip/control-center/signals")
def control_center_signals(
    symbols: str | None = Query(default=None, max_length=400),
    direction: str | None = Query(default=None, max_length=8),
    v2_status: str | None = Query(default=None, max_length=96),
    divergence: str | None = Query(default=None, max_length=32),
    min_score: float | None = Query(default=None, ge=0, le=100),
    max_score: float | None = Query(default=None, ge=0, le=100),
    observed_from: str | None = Query(default=None, max_length=40),
    observed_to: str | None = Query(default=None, max_length=40),
    attempt_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=200),
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """One keyset page of live signal evidence with V1/V2 classification."""
    _require_secret(x_webhook_secret)
    symbol_list = [part.strip() for part in (symbols or "").split(",") if part.strip()]

    def build(conn: sqlite3.Connection) -> dict[str, Any]:
        try:
            return queries.signals_page(
                conn,
                symbols=symbol_list,
                direction=direction,
                v2_status=v2_status,
                divergence=divergence,
                min_score=min_score,
                max_score=max_score,
                observed_from=observed_from,
                observed_to=observed_to,
                attempt_id=attempt_id,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def fallback() -> dict[str, Any]:
        return _direct_signals(
            get_settings(),
            symbols=symbol_list,
            direction=direction,
            v2_status=v2_status,
            divergence=divergence,
            min_score=min_score,
            max_score=max_score,
            limit=limit,
        )

    return _guarded(build, fallback=fallback)


@router.get("/api/opip/control-center/forensics")
def control_center_forensics(
    attempt_id: str = Query(min_length=1, max_length=64),
    symbol: str = Query(min_length=1, max_length=32),
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Full provenance for one row, naming every field it could not prove."""
    _require_secret(x_webhook_secret)
    return _guarded(
        lambda conn: queries.forensics(conn, attempt_id=attempt_id, symbol=symbol),
        fallback=lambda: direct.forensics(
            get_settings(), attempt_id=attempt_id, symbol=symbol
        ),
    )


@router.get("/api/opip/control-center/scanner")
def control_center_scanner(
    limit: int = Query(default=10, ge=1, le=25),
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Attempt-by-attempt operational funnel."""
    _require_secret(x_webhook_secret)
    return _guarded(
        lambda conn: queries.scanner_funnel(conn, limit=limit),
        fallback=lambda: direct.funnel(get_settings(), limit=limit),
    )


@router.get("/api/opip/control-center/ai")
def control_center_ai(
    limit: int = Query(default=50, ge=1, le=200),
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Recorded AI inference evidence, or an explicit empty state."""
    _require_secret(x_webhook_secret)

    def build(conn: sqlite3.Connection) -> dict[str, Any]:
        return {
            "status": "MEASURED",
            "summary": queries.ai_summary(conn),
            "page": queries.ai_page(conn, limit=limit),
            "generated_at_utc": runtime._utc().isoformat(),
        }

    return _guarded(build, fallback=lambda: direct.ai(get_settings()))



@router.get("/api/opip/control-center/data")
def control_center_data(x_webhook_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Source coverage, ingestion lag, quarantine, aging and retention state."""
    _require_secret(x_webhook_secret)
    path = _db_path()
    return _guarded(
        lambda conn: queries.data_health(conn, db_path=path),
        fallback=lambda: direct.data_coverage(get_settings()),
    )


@router.get("/api/opip/control-center/config")
def control_center_config(
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """The schema-driven registry, staged versions and live effective values."""
    _require_secret(x_webhook_secret)
    settings = get_settings()

    def build(conn: sqlite3.Connection) -> dict[str, Any]:
        return config_staging.overview(conn, settings)

    # Configuration is readable without a projection: the registry and the live
    # readback do not depend on analytics being present.
    with reader() as conn:
        try:
            return build(conn) if conn is not None else config_staging.overview(None, settings)
        except sqlite3.OperationalError:
            return config_staging.overview(None, settings)


@router.post("/api/opip/control-center/config/preview")
def control_center_config_preview(
    domain: str = Body(..., embed=True),
    values: dict = Body(..., embed=True),
    window_days: int | None = Body(default=None, embed=True),
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Tier 3 historical impact preview. Read-only and explicitly hypothetical."""
    _require_strategy_admin(x_webhook_secret, x_strategy_admin_secret)
    result = validate_document(domain, values)
    with reader() as conn:
        preview = config_staging.preview(
            conn, domain=domain, values=result.normalized or values, window_days=window_days
        )
    return {
        "status": "MEASURED",
        "domain": domain,
        "validation": result.as_dict(),
        "preview": preview,
        "generated_at_utc": runtime._utc().isoformat(),
    }


@router.post("/api/opip/control-center/config/stage")
def control_center_config_stage(
    domain: str = Body(..., embed=True),
    values: dict = Body(..., embed=True),
    actor: str = Body(..., embed=True),
    audit_note: str = Body(..., embed=True),
    expected_checksum: str = Body(default="", embed=True),
    window_days: int | None = Body(default=None, embed=True),
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Stage a validated configuration version. This changes no runtime value."""
    _require_strategy_admin(x_webhook_secret, x_strategy_admin_secret)
    settings = get_settings()
    conn = connect_writer(
        _db_path(),
        busy_timeout_ms=int(getattr(settings, "opip_analytics_busy_timeout_ms", 5000)),
    )
    try:
        from app.opip.analytics import schema as analytics_schema

        analytics_schema.ensure_schema(conn)
        config_staging.sync_registry(conn)
        result = validate_document(domain, values)
        if not result.ok:
            raise HTTPException(
                status_code=422,
                detail={"reason": "VALIDATION_FAILED", "validation": result.as_dict()},
            )
        preview_payload = config_staging.preview(
            conn, domain=domain, values=result.normalized, window_days=window_days
        )
        try:
            return config_staging.stage(
                conn,
                domain=domain,
                values=values,
                actor=actor,
                note=audit_note,
                expected_checksum=expected_checksum,
                preview_result=preview_payload,
            )
        except config_staging.ConfigStageConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except config_staging.ConfigStageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()



def _writer_credentials_ok(
    operator_secret: str | None, admin_secret: str | None
) -> None:
    _require_strategy_admin(operator_secret, admin_secret)


@router.post("/api/opip/control-center/maintenance/ingest")
def control_center_ingest(
    max_runs_per_source: int = Body(default=3, embed=True),
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Run one bounded, idempotent ingestion pass over registered sources."""
    _writer_credentials_ok(x_webhook_secret, x_strategy_admin_secret)
    settings = get_settings()
    conn = connect_writer(
        _db_path(),
        busy_timeout_ms=int(getattr(settings, "opip_analytics_busy_timeout_ms", 5000)),
    )
    try:
        from app.opip.analytics import schema as analytics_schema

        analytics_schema.ensure_schema(conn)
        descriptors = source_registry.all_sources(settings)
        results = ingest_all(
            conn,
            descriptors,
            batch_records=int(getattr(settings, "opip_analytics_ingest_batch_records", 500)),
            max_bytes=int(getattr(settings, "opip_analytics_ingest_max_bytes", 2_097_152)),
            max_runs_per_source=max(1, min(int(max_runs_per_source), 10)),
        )
        return {
            "status": "MEASURED",
            "sources": results,
            "sources_considered": len(descriptors),
            "inserted": sum(item.get("records_inserted") or 0 for item in results),
            "quarantined": sum(item.get("records_quarantined") or 0 for item in results),
            "mutates_trading_state": False,
            "generated_at_utc": runtime._utc().isoformat(),
        }
    finally:
        conn.close()


@router.post("/api/opip/control-center/maintenance/reconcile")
def control_center_reconcile(
    sample_records: int = Body(default=5, embed=True),
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Reconcile the projection against its canonical sources."""
    _writer_credentials_ok(x_webhook_secret, x_strategy_admin_secret)
    settings = get_settings()
    conn = connect_writer(
        _db_path(),
        busy_timeout_ms=int(getattr(settings, "opip_analytics_busy_timeout_ms", 5000)),
    )
    try:
        from app.opip.analytics import schema as analytics_schema

        analytics_schema.ensure_schema(conn)
        result = reconcile.reconcile_all(
            conn,
            source_registry.all_sources(settings),
            sample_records=max(1, min(int(sample_records), 50)),
        )
        result["mutates_trading_state"] = False
        return result
    finally:
        conn.close()


@router.post("/api/opip/control-center/maintenance/retention")
def control_center_retention(
    retention_days: int | None = Body(default=None, embed=True),
    dry_run: bool = Body(default=True, embed=True),
    record_policy: bool = Body(default=False, embed=True),
    audit_note: str = Body(default="", embed=True),
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Run retention housekeeping. Deletion requires a separate environment gate."""
    _writer_credentials_ok(x_webhook_secret, x_strategy_admin_secret)
    settings = get_settings()
    conn = connect_writer(
        _db_path(),
        busy_timeout_ms=int(getattr(settings, "opip_analytics_busy_timeout_ms", 5000)),
    )
    try:
        from app.opip.analytics import schema as analytics_schema

        analytics_schema.ensure_schema(conn)
        days = retention.validate_retention_days(
            retention_days
            if retention_days is not None
            else getattr(settings, "opip_analytics_retention_days", 30)
        )
        if record_policy:
            retention.record_policy(
                conn,
                retention_days=days,
                actor="control-center",
                note=audit_note or "policy recorded from the Data & Retention workspace",
                dry_run=dry_run,
            )
        result = retention.housekeeping(
            conn,
            retention_days=days,
            dry_run=dry_run,
            allow_delete=bool(getattr(settings, "opip_enable_retention_delete", False)),
            batch_size=int(getattr(settings, "opip_analytics_ingest_batch_records", 500)),
            actor="control-center",
        )
        result["mutates_canonical_evidence"] = False
        return result
    finally:
        conn.close()

