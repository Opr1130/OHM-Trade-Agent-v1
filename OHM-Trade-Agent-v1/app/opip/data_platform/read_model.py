from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect


logger = logging.getLogger(__name__)


def _stream_health_is_stale(
    health: list[dict[str, Any]],
    *,
    maximum_lag_seconds: int = 1800,
) -> bool:
    """Return whether required canonical stream health is stale or unavailable."""
    required = [item for item in health if item.get("required")]
    if any("freshness_status" in item for item in health):
        if not required:
            return True
        return any(str(item.get("freshness_status")) != "LIVE" for item in required)
    present = {str(item["stream_name"]) for item in health}
    maximum_lag = max(
        (
            item["lag_seconds"]
            for item in health
            if item.get("lag_seconds") is not None
        ),
        default=0,
    )
    return (
        maximum_lag > maximum_lag_seconds
        or any(
            item["last_reconciliation_status"] not in {None, "CLEAN"}
            or item["unresolved_dead_letters"] > 0
            for item in health
        )
        or not present
    )


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    return str(value) if value is not None else None


def _empty(status: str, *, error_type: str | None = None) -> dict[str, Any]:
    return {
        "enabled": status != "DISABLED",
        "available": False,
        "status": status,
        "error_type": error_type,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "stale": True,
        "intelligence_daily": [],
        "attrition_daily": [],
        "rejection_mix_daily": [],
        "opportunity_accountability_daily": [],
        "opportunity_accountability_all_time": {},
        "stream_health": [],
        "freshness": {
            "status": "UNAVAILABLE",
            "ready": False,
            "reason": "DISABLED" if status == "DISABLED" else "READ_UNAVAILABLE",
            "problems": [],
        },
    }


def read_historical_snapshot(
    config: DataPlatformConfig | None = None,
) -> dict[str, Any]:
    """Read small analytics views, failing soft to local dashboard data."""
    settings = config or DataPlatformConfig.from_env()
    dsn = settings.dashboard_dsn()
    if not dsn:
        return _empty("DISABLED")
    try:
        with connect(
            dsn,
            connect_timeout_seconds=settings.connect_timeout_seconds,
            application_name="opip-dashboard-readonly",
        ) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{settings.statement_timeout_ms}ms",),
                    )
                    cursor.execute(
                        """
                        SELECT day, events, early_watch_journeys,
                               qualified_signals, paper_outcomes
                        FROM signal.intelligence_daily_mv
                        ORDER BY day DESC LIMIT 60
                        """
                    )
                    intelligence = [
                        {
                            "date": (_iso(row[0]) or "")[:10],
                            "events": int(row[1]),
                            "early_watch_journeys": int(row[2]),
                            "qualified_signals": int(row[3]),
                            "paper_outcomes": int(row[4]),
                        }
                        for row in cursor.fetchall()
                    ]
                    cursor.execute(
                        """
                        SELECT day, scanner_type, outcome, reason_code,
                               evaluations, instruments
                        FROM market.attrition_daily_mv
                        ORDER BY day DESC, scanner_type, outcome LIMIT 1000
                        """
                    )
                    attrition = [
                        {
                            "date": (_iso(row[0]) or "")[:10],
                            "scanner_type": row[1],
                            "outcome": row[2],
                            "reason_code": row[3],
                            "evaluations": int(row[4]),
                            "instruments": int(row[5]),
                        }
                        for row in cursor.fetchall()
                    ]
                    cursor.execute(
                        """
                        SELECT day, gate_name, reason_code, transitions
                        FROM lifecycle.rejection_mix_daily_mv
                        ORDER BY day DESC, transitions DESC LIMIT 1000
                        """
                    )
                    rejection_mix = [
                        {
                            "date": (_iso(row[0]) or "")[:10],
                            "gate_name": row[1],
                            "reason_code": row[2],
                            "transitions": int(row[3]),
                        }
                        for row in cursor.fetchall()
                    ]
                    cursor.execute(
                        """
                        SELECT coalesce(
                            (
                                SELECT relispopulated
                                FROM pg_class
                                WHERE oid = to_regclass(
                                    'learning.opportunity_accountability_daily_mv'
                                )
                            ),
                            false
                        )
                        """
                    )
                    accountability_available = bool(cursor.fetchone()[0])
                    accountability = []
                    accountability_all_time = {}
                    if accountability_available:
                        cursor.execute(
                            """
                            SELECT day, directional_evaluations,
                                   completed_forward_outcomes,
                                   market_winner_candidates, captured_winners,
                                   executable_false_negatives,
                                   threshold_70_79_miss_candidates,
                                   ranking_or_cap_miss_candidates,
                                   operational_executable_misses,
                                   estimated_missed_move_pct_sum,
                                   decision_latency_samples,
                                   mean_decision_latency_ms
                            FROM learning.opportunity_accountability_daily_mv
                            ORDER BY day DESC LIMIT 60
                            """
                        )
                        accountability = [
                            {
                                "date": (_iso(row[0]) or "")[:10],
                                "directional_evaluations": int(row[1]),
                                "completed_forward_outcomes": int(row[2]),
                                "market_winner_candidates": int(row[3]),
                                "captured_winners": int(row[4]),
                                "executable_false_negatives": int(row[5]),
                                "threshold_70_79_miss_candidates": int(row[6]),
                                "ranking_or_cap_miss_candidates": int(row[7]),
                                "operational_executable_misses": int(row[8]),
                                "estimated_missed_move_pct_sum": (
                                    float(row[9]) if row[9] is not None else 0.0
                                ),
                                "decision_latency_samples": int(row[10]),
                                "mean_decision_latency_ms": (
                                    float(row[11]) if row[11] is not None else None
                                ),
                            }
                            for row in cursor.fetchall()
                        ]
                        cursor.execute(
                            """
                            SELECT
                                coalesce(sum(directional_evaluations), 0),
                                coalesce(sum(completed_forward_outcomes), 0),
                                coalesce(sum(market_winner_candidates), 0),
                                coalesce(sum(captured_winners), 0),
                                coalesce(sum(executable_false_negatives), 0),
                                coalesce(sum(threshold_70_79_miss_candidates), 0),
                                coalesce(sum(ranking_or_cap_miss_candidates), 0),
                                coalesce(sum(operational_executable_misses), 0),
                                coalesce(sum(estimated_missed_move_pct_sum), 0),
                                coalesce(sum(decision_latency_samples), 0),
                                CASE
                                    WHEN coalesce(sum(decision_latency_samples), 0) > 0
                                    THEN sum(
                                        coalesce(mean_decision_latency_ms, 0)
                                        * decision_latency_samples
                                    ) / sum(decision_latency_samples)
                                    ELSE NULL
                                END
                            FROM learning.opportunity_accountability_daily_mv
                            """
                        )
                        row = cursor.fetchone()
                        accountability_all_time = {
                            "directional_evaluations": int(row[0]),
                            "completed_forward_outcomes": int(row[1]),
                            "market_winner_candidates": int(row[2]),
                            "captured_winners": int(row[3]),
                            "executable_false_negatives": int(row[4]),
                            "threshold_70_79_miss_candidates": int(row[5]),
                            "ranking_or_cap_miss_candidates": int(row[6]),
                            "operational_executable_misses": int(row[7]),
                            "estimated_missed_move_pct_sum": float(row[8]),
                            "decision_latency_samples": int(row[9]),
                            "mean_decision_latency_ms": (
                                float(row[10]) if row[10] is not None else None
                            ),
                        }
                    cursor.execute(
                        """
                        SELECT stream_name, source_file, byte_offset,
                               rows_ingested, source_size, updated_at,
                               lag_seconds, unresolved_dead_letters,
                               last_reconciliation_status, last_reconciled_at,
                               freshness_status, freshness_reason, required
                        FROM ops.platform_health_v ORDER BY stream_name
                        """
                    )
                    health = [
                        {
                            "stream_name": row[0],
                            "source_file": row[1],
                            "byte_offset": int(row[2]) if row[2] is not None else 0,
                            "rows_ingested": int(row[3]) if row[3] is not None else 0,
                            "source_size": int(row[4]) if row[4] is not None else 0,
                            "updated_at": _iso(row[5]),
                            "lag_seconds": int(row[6]) if row[6] is not None else None,
                            "unresolved_dead_letters": int(row[7]),
                            "last_reconciliation_status": row[8],
                            "last_reconciled_at": _iso(row[9]),
                            "freshness_status": row[10],
                            "freshness_reason": row[11],
                            "required": bool(row[12]),
                        }
                        for row in cursor.fetchall()
                    ]
                    cursor.execute(
                        """
                        SELECT status, reason
                        FROM ops.dashboard_freshness_v
                        WHERE stream_name = '__maintenance__'
                        """
                    )
                    maintenance_row = cursor.fetchone()

        canonical_problems = [
            {"stream": row["stream_name"], "reason": row["freshness_reason"]}
            for row in health
            if row["freshness_reason"]
        ]
        if maintenance_row is not None and maintenance_row[1]:
            canonical_problems.append(
                {"stream": "__maintenance__", "reason": maintenance_row[1]}
            )

        blocking_health = [
            row
            for row in health
            if row["required"] or row["freshness_reason"] == "UNKNOWN_STREAM_POLICY"
        ]

        if not health or maintenance_row is None:
            canonical_status = "UNAVAILABLE"
        elif any(
            row["freshness_status"] == "UNAVAILABLE" for row in blocking_health
        ) or maintenance_row[0] == "UNAVAILABLE":
            canonical_status = "UNAVAILABLE"
        elif any(
            row["freshness_status"] == "STALE" for row in blocking_health
        ) or maintenance_row[0] == "STALE":
            canonical_status = "STALE"
        elif any(
            row["freshness_status"] == "DEGRADED" for row in blocking_health
        ) or maintenance_row[0] == "DEGRADED":
            canonical_status = "DEGRADED"
        else:
            canonical_status = "LIVE"

        component_status = {
            row["stream_name"]: row["freshness_status"] for row in health
        }
        if maintenance_row is not None:
            component_status["__maintenance__"] = maintenance_row[0]

        if canonical_status == "LIVE":
            canonical_reason = "OK"
        else:
            canonical_reason = next(
                (
                    problem["reason"]
                    for problem in canonical_problems
                    if component_status.get(problem["stream"]) == canonical_status
                ),
                canonical_problems[0]["reason"]
                if canonical_problems
                else "READ_UNAVAILABLE",
            )

        stale = canonical_status != "LIVE"
        return {
            "enabled": True,
            "available": True,
            "status": "OK" if not stale else "STALE",
            "error_type": None,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "stale": stale,
            "freshness": {
                "status": canonical_status,
                "ready": canonical_status == "LIVE",
                "reason": canonical_reason,
                "problems": canonical_problems,
            },
            "intelligence_daily": list(reversed(intelligence)),
            "attrition_daily": attrition,
            "rejection_mix_daily": rejection_mix,
            "opportunity_accountability_daily": list(reversed(accountability)),
            "opportunity_accountability_all_time": accountability_all_time,
            "stream_health": health,
        }
    except Exception as exc:
        logger.warning(
            "O'Pip historical PostgreSQL read failed soft: %s", type(exc).__name__
        )
        return _empty("UNAVAILABLE", error_type=type(exc).__name__)
