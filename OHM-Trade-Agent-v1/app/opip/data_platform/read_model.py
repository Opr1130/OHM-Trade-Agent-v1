from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect
from app.opip.data_platform.streams import STREAM_SPECS


logger = logging.getLogger(__name__)


def _stream_health_is_stale(
    health: list[dict[str, Any]],
    *,
    maximum_lag_seconds: int = 1800,
) -> bool:
    required_streams = {item.name for item in STREAM_SPECS if item.required}
    required_health = [
        item for item in health if str(item["stream_name"]) in required_streams
    ]
    present_required = {str(item["stream_name"]) for item in required_health}
    maximum_lag = max(
        (item["lag_seconds"] for item in required_health),
        default=0,
    )
    return (
        bool(required_streams - present_required)
        or maximum_lag > maximum_lag_seconds
        or any(
            item["last_reconciliation_status"] not in {None, "CLEAN"}
            or item["unresolved_dead_letters"] > 0
            for item in required_health
        )
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
    }


def read_historical_snapshot(
    config: DataPlatformConfig | None = None,
) -> dict[str, Any]:
    """Read only small analytics views, failing soft to local dashboard data."""
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
                        "SELECT to_regclass('learning.opportunity_accountability_daily_mv')"
                    )
                    accountability_available = cursor.fetchone()[0] is not None
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
                               last_reconciliation_status, last_reconciled_at
                        FROM ops.platform_health_v ORDER BY stream_name
                        """
                    )
                    health = [
                        {
                            "stream_name": row[0],
                            "source_file": row[1],
                            "byte_offset": int(row[2]),
                            "rows_ingested": int(row[3]),
                            "source_size": int(row[4]),
                            "updated_at": _iso(row[5]),
                            "lag_seconds": int(row[6]),
                            "unresolved_dead_letters": int(row[7]),
                            "last_reconciliation_status": row[8],
                            "last_reconciled_at": _iso(row[9]),
                        }
                        for row in cursor.fetchall()
                    ]
        stale = _stream_health_is_stale(health)
        return {
            "enabled": True,
            "available": True,
            "status": "STALE" if stale else "OK",
            "error_type": None,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "stale": stale,
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
