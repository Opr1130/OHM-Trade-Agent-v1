from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.execution_learning_registry import get_execution_records
from app.services.freqtrade_result_ingest import freqtrade_dry_run_status
from app.services.intelligence_journey import EVENT_FILE
from app.services.intelligence_learning_profile import build_intelligence_learning_profile
from app.services.operations_analytics import build_operations_summary
from app.services.paper_trade_control import paper_trade_enabled
from app.services.profitability_learning import build_profitability_profile
from app.services.shadow_learning import get_shadow_records
from app.services.trade_outcome_registry import get_outcomes


MAX_EVENTS = 20000
MAX_RECENT_EVENTS = 80


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _read_events(path: Path = EVENT_FILE, limit: int = MAX_EVENTS) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows[-max(1, int(limit)):]


def _scope_rows(rows: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    if scope == "all":
        return rows
    today = datetime.now(timezone.utc).date()
    return [
        row for row in rows
        if (parsed := _parse_time(row.get("observed_at"))) is not None
        and parsed.date() == today
    ]


def _paper_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl: list[float] = []
    returns: list[float] = []
    for row in rows:
        payload = row.get("payload") or {}
        value = _number(payload.get("net_pnl"))
        if value is None:
            continue
        pnl.append(value)
        ratio = _number(payload.get("close_profit_ratio"))
        if ratio is not None:
            returns.append(ratio)
    wins = sum(value > 0 for value in pnl)
    losses = sum(value < 0 for value in pnl)
    return {
        "count": len(pnl),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": _pct(wins, len(pnl)),
        "avg_return_pct": round(mean(returns) * 100.0, 6) if returns else None,
    }


def _scoped_intelligence_profile(events: list[dict[str, Any]]) -> dict[str, Any]:
    journeys: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"early": [], "signals": [], "outcomes": []}
    )
    for row in events:
        journey_id = str(row.get("journey_id") or "")
        if not journey_id:
            continue
        event_type = str(row.get("event_type") or "").upper()
        if event_type in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}:
            journeys[journey_id]["early"].append(row)
        elif event_type == "QUALIFIED_SIGNAL":
            journeys[journey_id]["signals"].append(row)
        elif event_type == "PAPER_OUTCOME":
            journeys[journey_id]["outcomes"].append(row)

    early_journeys = [value for value in journeys.values() if value["early"]]
    qualified_signal_ids = {
        str(row.get("signal_id"))
        for value in journeys.values()
        for row in value["signals"]
        if row.get("signal_id")
    }
    paper_requested_signal_ids = {
        str(row.get("signal_id"))
        for value in journeys.values()
        for row in value["signals"]
        if row.get("signal_id") and bool((row.get("payload") or {}).get("paper_requested"))
    }
    paper_outcome_signal_ids = {
        str(row.get("signal_id"))
        for value in journeys.values()
        for row in value["outcomes"]
        if row.get("signal_id")
    }
    early_to_signal = sum(bool(value["signals"]) for value in early_journeys)
    latencies: list[float] = []
    paper_rows: list[dict[str, Any]] = []
    early_paper_rows: list[dict[str, Any]] = []
    stage_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for value in journeys.values():
        if value["early"] and value["signals"]:
            early_times = [t for t in (_parse_time(r.get("observed_at")) for r in value["early"]) if t]
            signal_times = [t for t in (_parse_time(r.get("observed_at")) for r in value["signals"]) if t]
            if early_times and signal_times:
                first_early, first_signal = min(early_times), min(signal_times)
                if first_signal >= first_early:
                    latencies.append((first_signal - first_early).total_seconds() / 60.0)
        for outcome in value["outcomes"]:
            paper_rows.append(outcome)
            if value["early"]:
                early_paper_rows.append(outcome)
            early_payload = (value["early"][-1].get("payload") or {}) if value["early"] else {}
            key = f"{early_payload.get('stage') or 'NO_EARLY_WATCH'}|{early_payload.get('pattern') or 'UNCLASSIFIED'}"
            stage_buckets[key].append(outcome)

    stage_pattern: dict[str, Any] = {}
    for key, rows in stage_buckets.items():
        stats = _paper_stats(rows)
        stage_pattern[key] = {
            "count": stats["count"],
            "wins": stats["wins"],
            "win_rate_pct": stats["win_rate_pct"],
            "avg_return_pct": stats["avg_return_pct"],
        }

    return {
        "events_considered": len(events),
        "journeys": len(journeys),
        "early_watch_journeys": len(early_journeys),
        "qualified_signals": len(qualified_signal_ids),
        "paper_requested_signals": len(paper_requested_signal_ids),
        "paper_outcome_signals": len(paper_outcome_signal_ids),
        "early_watch_to_signal_conversion_pct": _pct(early_to_signal, len(early_journeys)),
        "signal_to_paper_outcome_conversion_pct": _pct(
            len(paper_requested_signal_ids & paper_outcome_signal_ids),
            len(paper_requested_signal_ids),
        ),
        "early_watch_to_signal_latency_minutes": {
            "samples": len(latencies),
            "average": round(mean(latencies), 2) if latencies else None,
            "minimum": round(min(latencies), 2) if latencies else None,
            "maximum": round(max(latencies), 2) if latencies else None,
        },
        "paper_performance": _paper_stats(paper_rows),
        "paper_performance_with_early_watch": _paper_stats(early_paper_rows),
        "early_stage_pattern_performance": stage_pattern,
    }


def _daily_intelligence_series(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    journeys: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"first_early": None, "signals": set(), "outcomes": []}
    )
    outcomes_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    signals_by_day: dict[str, set[str]] = defaultdict(set)
    early_by_day: dict[str, set[str]] = defaultdict(set)

    for row in events:
        observed = _parse_time(row.get("observed_at"))
        journey_id = str(row.get("journey_id") or "")
        if observed is None or not journey_id:
            continue
        day = observed.date().isoformat()
        event_type = str(row.get("event_type") or "").upper()
        journey = journeys[journey_id]
        if event_type in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}:
            early_by_day[day].add(journey_id)
            current = journey["first_early"]
            if current is None or observed < current:
                journey["first_early"] = observed
        elif event_type == "QUALIFIED_SIGNAL":
            signal_id = str(row.get("signal_id") or "")
            if signal_id:
                journey["signals"].add(signal_id)
                signals_by_day[day].add(signal_id)
        elif event_type == "PAPER_OUTCOME":
            journey["outcomes"].append(row)
            outcomes_by_day[day].append(row)

    cohort_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for journey in journeys.values():
        first_early = journey["first_early"]
        if first_early is not None:
            cohort_by_day[first_early.date().isoformat()].append(journey)

    days = sorted(set(early_by_day) | set(signals_by_day) | set(outcomes_by_day) | set(cohort_by_day))
    rows: list[dict[str, Any]] = []
    for day in days[-60:]:
        cohort = cohort_by_day.get(day, [])
        converted = sum(bool(item["signals"]) for item in cohort)
        early_outcome_rows = [
            outcome
            for item in cohort
            for outcome in item["outcomes"]
            if _number((outcome.get("payload") or {}).get("net_pnl")) is not None
        ]
        paper_rows = [
            row for row in outcomes_by_day.get(day, [])
            if _number((row.get("payload") or {}).get("net_pnl")) is not None
        ]
        paper_pnl = [float((row.get("payload") or {})["net_pnl"]) for row in paper_rows]
        paper_returns = [
            value for value in (
                _number((row.get("payload") or {}).get("close_profit_ratio"))
                for row in paper_rows
            )
            if value is not None
        ]
        early_pnl = [float((row.get("payload") or {})["net_pnl"]) for row in early_outcome_rows]
        rows.append(
            {
                "date": day,
                "early_watch_journeys": len(early_by_day.get(day, set())),
                "qualified_signals": len(signals_by_day.get(day, set())),
                "paper_outcomes": len(paper_pnl),
                "early_to_signal_conversion_pct": _pct(converted, len(cohort)),
                "paper_win_rate_pct": _pct(sum(value > 0 for value in paper_pnl), len(paper_pnl)),
                "paper_avg_return_pct": round(mean(paper_returns) * 100.0, 4) if paper_returns else None,
                "early_watch_paper_win_rate_pct": _pct(
                    sum(value > 0 for value in early_pnl),
                    len(early_pnl),
                ),
            }
        )
    return rows


def _failure_reason(row: dict[str, Any]) -> str | None:
    net = _number(row.get("net_pnl"))
    if net is None or net >= 0:
        return None
    mae = _number(row.get("mae_pct"))
    mfe = _number(row.get("mfe_pct"))
    if row.get("stop_observed") and mfe is not None and mfe <= 0.25:
        return "NO_FAVORABLE_EXCURSION"
    if mae is not None and mfe is not None and mae > max(2.0, mfe * 2.0):
        return "ADVERSE_EXCURSION_DOMINATED"
    return "OTHER_LOSS"


def _failure_snapshot(outcomes: list[dict[str, Any]], profitability: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in outcomes:
        reason = _failure_reason(row)
        if not reason:
            continue
        counts[reason] += 1
        terminal = _parse_time(row.get("terminal_timestamp"))
        if terminal is not None:
            daily[terminal.date().isoformat()][reason] += 1

    classified = sum(counts.values())
    repeated = sum(max(0, count - 1) for count in counts.values())
    loss_learning = profitability.get("loss_learning") or {}
    shadow = profitability.get("shadow_learning") or {}
    return {
        "classified_losing_trades": classified,
        "by_reason": dict(sorted(counts.items())),
        "heuristic_recurrence_pct": _pct(repeated, classified),
        "recurrence_definition": "classified loss instances after the first occurrence of the same heuristic reason",
        "potentially_avoidable_losses": int(loss_learning.get("potentially_avoidable_losses") or 0),
        "potentially_avoidable_loss_value": loss_learning.get("potentially_avoidable_loss_dollars"),
        "missed_profitable_opportunities": int(shadow.get("missed_profitable") or 0),
        "coverage": "HEURISTIC_PARTIAL",
        "daily": [
            {"date": day, "by_reason": dict(sorted(reasons.items())), "total": sum(reasons.values())}
            for day, reasons in sorted(daily.items())[-60:]
        ],
    }


def _recent_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in events[-MAX_RECENT_EVENTS:]:
        payload = row.get("payload") or {}
        compact.append(
            {
                "observed_at": row.get("observed_at"),
                "event_type": row.get("event_type"),
                "symbol": row.get("symbol"),
                "journey_id": row.get("journey_id"),
                "signal_id": row.get("signal_id"),
                "stage": payload.get("stage"),
                "pattern": payload.get("pattern"),
                "delivered": row.get("delivered"),
                "admitted": row.get("admitted"),
                "reason": row.get("reason"),
                "net_pnl": payload.get("net_pnl"),
                "return_pct": (
                    round(float(payload["close_profit_ratio"]) * 100.0, 4)
                    if _number(payload.get("close_profit_ratio")) is not None
                    else None
                ),
            }
        )
    compact.reverse()
    return compact


def _paper_status() -> dict[str, Any]:
    try:
        status = freqtrade_dry_run_status()
    except Exception as exc:
        status = {"status": "UNAVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}
    try:
        enabled = bool(paper_trade_enabled())
    except Exception:
        enabled = False
    return {
        "enabled": enabled,
        "engine": "FREQTRADE_DRY_RUN",
        "kraken_execution_authority": False,
        "status": status,
    }


def build_dashboard_read_model(scope: str = "all") -> dict[str, Any]:
    if scope not in {"today", "all"}:
        raise ValueError("scope must be today or all")

    operations = build_operations_summary(scope=scope)
    events_all = _read_events()
    events = _scope_rows(events_all, scope)
    intelligence = (
        build_intelligence_learning_profile(persist=False)
        if scope == "all"
        else _scoped_intelligence_profile(events)
    )
    outcomes = get_outcomes()
    shadows = get_shadow_records()
    executions = get_execution_records()
    if scope == "today":
        today = datetime.now(timezone.utc).date()
        outcomes = [
            row for row in outcomes
            if (terminal := _parse_time(row.get("terminal_timestamp"))) is not None
            and terminal.date() == today
        ]
        shadows = [
            row for row in shadows
            if (observed := _parse_time(row.get("observed_at"))) is not None
            and observed.date() == today
        ]
        profitability = build_profitability_profile(
            outcomes=outcomes,
            executions=executions,
            shadows=shadows,
            persist=False,
        )
    else:
        profitability = build_profitability_profile(
            outcomes=outcomes,
            executions=executions,
            shadows=shadows,
            persist=False,
        )

    paper = intelligence.get("paper_performance") or {}
    early_paper = intelligence.get("paper_performance_with_early_watch") or {}
    shadow = profitability.get("shadow_learning") or {}
    calibration = profitability.get("trade_calibration") or {}
    sample_count = int(paper.get("count") or 0)
    evidence_state = "INSUFFICIENT_DATA" if sample_count < 30 else "MEASURED"

    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "read_only": True,
        "operations": operations,
        "intelligence": {
            "evidence_state": evidence_state,
            "events_considered": intelligence.get("events_considered", 0),
            "journeys": intelligence.get("journeys", 0),
            "early_watch_journeys": intelligence.get("early_watch_journeys", 0),
            "qualified_signals": intelligence.get("qualified_signals", 0),
            "paper_requested_signals": intelligence.get("paper_requested_signals", 0),
            "paper_outcome_signals": intelligence.get("paper_outcome_signals", 0),
            "early_watch_to_signal_conversion_pct": intelligence.get("early_watch_to_signal_conversion_pct"),
            "signal_to_paper_outcome_conversion_pct": intelligence.get("signal_to_paper_outcome_conversion_pct"),
            "early_watch_to_signal_latency_minutes": intelligence.get("early_watch_to_signal_latency_minutes") or {},
            "paper_performance": paper,
            "early_watch_paper_performance": early_paper,
            "decision_accuracy_pct": shadow.get("decision_accuracy_pct"),
            "decision_accuracy_samples": shadow.get("samples", 0),
            "calibration_status": calibration.get("status", "UNKNOWN"),
            "calibration_samples": calibration.get("samples", 0),
            "calibration_objective": calibration.get("objective"),
            "change_impact": {
                "status": "AWAITING_VERSIONED_CHANGE_COHORTS",
                "message": "Strategy-change before/after attribution is not credited until versioned change cohorts exist.",
            },
            "trend": _daily_intelligence_series(events),
            "stage_pattern_performance": intelligence.get("early_stage_pattern_performance") or {},
        },
        "failure_eradication": _failure_snapshot(outcomes, profitability),
        "paper_engine": _paper_status(),
        "recent_events": _recent_events(events),
        "guardrails": {
            "dashboard_can_change_rankings": False,
            "dashboard_can_change_alerts": False,
            "dashboard_can_admit_paper_trades": False,
            "dashboard_can_write_exchange_orders": False,
        },
    }
