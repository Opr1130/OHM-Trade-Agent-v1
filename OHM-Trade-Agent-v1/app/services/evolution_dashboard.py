from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from statistics import mean
from typing import Any

from app.services.freqtrade_result_ingest import DB_FILES, freqtrade_dry_run_status
from app.services.intelligence_journey import EVENT_FILE
from app.services.operations_analytics import build_operations_summary
from app.services.p1_shadow_outbox import (
    DEFAULT_DEAD_LETTER_FILE,
    outbox_health,
)
from app.services.paper_trade_control import get_paper_trade_control
from app.services.tradingview_evidence_diagnostics import (
    load_tradingview_evidence_diagnostics,
)


EVOLUTION_SCHEMA_VERSION = 1
MEASUREMENT_BASELINE_VERSION = "OHM_EVOLUTION_BASELINE_2026_08_28"
SUPPORTED_SCOPES = {"today", "7d", "30d", "all"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cutoff(scope: str, now: datetime) -> datetime | None:
    if scope == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if scope == "7d":
        return now - timedelta(days=7)
    if scope == "30d":
        return now - timedelta(days=30)
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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
    return rows


def _filter_events(
    events: list[dict[str, Any]],
    *,
    cutoff: datetime | None,
) -> list[dict[str, Any]]:
    if cutoff is None:
        return events
    filtered: list[dict[str, Any]] = []
    for row in events:
        at = _parse_time(row.get("observed_at"))
        if at is not None and at >= cutoff:
            filtered.append(row)
    return filtered


def _scoped_journey_history(
    all_events: list[dict[str, Any]],
    *,
    cutoff: datetime | None,
) -> list[dict[str, Any]]:
    """Select journeys active in the window while retaining full linked history."""
    if cutoff is None:
        return list(all_events)
    selected_journeys = {
        str(row.get("journey_id"))
        for row in all_events
        if row.get("journey_id")
        and (at := _parse_time(row.get("observed_at"))) is not None
        and at >= cutoff
    }
    return [
        row
        for row in all_events
        if str(row.get("journey_id") or "") in selected_journeys
    ]


def _pct(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator) * 100.0, 2)


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _event_type(row: dict[str, Any]) -> str:
    return str(row.get("event_type") or "").upper()


def _journeys(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        journey_id = str(row.get("journey_id") or "")
        if journey_id:
            grouped[journey_id].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: str(row.get("observed_at") or ""))
    return grouped


def _funnel(events: list[dict[str, Any]]) -> dict[str, Any]:
    journeys = _journeys(events)
    early = {
        jid
        for jid, rows in journeys.items()
        if any(
            _event_type(row) in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}
            for row in rows
        )
    }
    early_delivered = {
        jid
        for jid, rows in journeys.items()
        if jid in early
        and any(
            _event_type(row) in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}
            and bool(row.get("delivered"))
            for row in rows
        )
    }
    qualified = {
        jid
        for jid, rows in journeys.items()
        if jid in early
        and any(_event_type(row) == "QUALIFIED_SIGNAL" for row in rows)
    }
    requested = {
        jid
        for jid, rows in journeys.items()
        if jid in early
        and any(
            _event_type(row) == "QUALIFIED_SIGNAL"
            and bool((row.get("payload") or {}).get("paper_requested"))
            for row in rows
        )
    }
    admitted = {
        jid
        for jid, rows in journeys.items()
        if jid in early
        and any(
            _event_type(row) == "PAPER_ADMISSION"
            and bool(row.get("admitted"))
            for row in rows
        )
    }
    rejected = {
        jid
        for jid, rows in journeys.items()
        if jid in early
        and any(
            _event_type(row) == "PAPER_ADMISSION"
            and not bool(row.get("admitted"))
            for row in rows
        )
    }
    closed = {
        jid
        for jid, rows in journeys.items()
        if jid in early
        and any(_event_type(row) == "PAPER_OUTCOME" for row in rows)
    }
    profitable = {
        jid
        for jid, rows in journeys.items()
        if jid in early
        and any(
            _event_type(row) == "PAPER_OUTCOME"
            and (_safe_float((row.get("payload") or {}).get("net_pnl")) or 0.0) > 0
            for row in rows
        )
    }
    direct_qualified = {
        jid
        for jid, rows in journeys.items()
        if jid not in early
        and any(_event_type(row) == "QUALIFIED_SIGNAL" for row in rows)
    }
    qualified_signal_ids = {
        str(row.get("signal_id"))
        for rows in journeys.values()
        for row in rows
        if _event_type(row) == "QUALIFIED_SIGNAL" and row.get("signal_id")
    }

    stage_values = [
        ("Early detected", len(early)),
        ("Qualified", len(qualified)),
        ("Paper requested", len(requested)),
        ("Admitted", len(admitted)),
        ("Closed", len(closed)),
        ("Profitable", len(profitable)),
    ]
    stages: list[dict[str, Any]] = []
    prior: int | None = None
    for name, count in stage_values:
        stages.append(
            {
                "stage": name,
                "count": count,
                "conversion_from_prior_pct": (
                    _pct(count, prior)
                    if prior is not None
                    else None
                ),
            }
        )
        prior = count

    return {
        "stages": stages,
        "early_detected": len(early),
        "early_delivered": len(early_delivered),
        "early_delivered_pct": _pct(len(early_delivered), len(early)),
        "qualified_journeys_from_early": len(qualified),
        "qualified_signals": len(qualified_signal_ids),
        "direct_qualified_journeys": len(direct_qualified),
        "paper_requested": len(requested),
        "paper_admitted": len(admitted),
        "paper_rejected": len(rejected),
        "paper_closed": len(closed),
        "paper_profitable": len(profitable),
        "early_to_signal_pct": _pct(len(qualified), len(early)),
        "requested_to_closed_pct": _pct(len(closed), len(requested)),
        "closed_win_rate_pct": _pct(len(profitable), len(closed)),
        "scope_semantics": (
            "Journeys with activity in the selected window are selected, then "
            "their complete linked lifecycle is retained. Sequential funnel "
            "stages use the Early Watch cohort; delivered-alert coverage is "
            "reported separately because delivery is not required for qualification."
        ),
    }

def _paper_outcomes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if _event_type(event) != "PAPER_OUTCOME":
            continue
        payload = event.get("payload") or {}
        net = _safe_float(payload.get("net_pnl"))
        ratio = _safe_float(payload.get("close_profit_ratio"))
        if net is None:
            continue
        rows.append(
            {
                "observed_at": event.get("observed_at"),
                "journey_id": event.get("journey_id"),
                "signal_id": event.get("signal_id"),
                "symbol": event.get("symbol"),
                "pair": payload.get("pair"),
                "pnl_currency": str(payload.get("pnl_currency") or "USD").upper(),
                "net_pnl": net,
                "return_pct": round(ratio * 100.0, 6) if ratio is not None else None,
                "exit_reason": str(payload.get("exit_reason") or "UNKNOWN"),
                "open_rate": _safe_float(payload.get("open_rate")),
                "open_rate_requested": _safe_float(payload.get("open_rate_requested")),
                "close_rate": _safe_float(payload.get("close_rate")),
                "stake_amount": _safe_float(payload.get("stake_amount")),
                "open_date": payload.get("open_date"),
                "close_date": payload.get("close_date"),
            }
        )
    rows.sort(key=lambda row: str(row.get("observed_at") or ""))
    return rows


def _equity_curve(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    running: dict[str, float] = defaultdict(float)
    result: list[dict[str, Any]] = []
    for row in outcomes:
        currency = row["pnl_currency"]
        running[currency] += float(row["net_pnl"])
        result.append(
            {
                "at": row["observed_at"],
                "label": _short_date(row["observed_at"]),
                "currency": currency,
                "cumulative_pnl": round(running[currency], 8),
            }
        )
    return result


def _short_date(value: Any) -> str:
    at = _parse_time(value)
    if at is None:
        return "—"
    return at.strftime("%b %d")


def _daily_trend(
    events: list[dict[str, Any]],
    *,
    context_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    context = context_events if context_events is not None else events
    context_journeys = _journeys(context)
    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "early_journeys": set(),
            "qualified_signals": set(),
            "paper_outcomes": [],
            "early_outcomes": [],
            "wins": 0,
        }
    )
    for row in events:
        at = _parse_time(row.get("observed_at"))
        if at is None:
            continue
        key = at.date().isoformat()
        kind = _event_type(row)
        if kind in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}:
            if row.get("journey_id"):
                daily[key]["early_journeys"].add(str(row["journey_id"]))
        elif kind == "QUALIFIED_SIGNAL" and row.get("signal_id"):
            daily[key]["qualified_signals"].add(str(row["signal_id"]))
        elif kind == "PAPER_OUTCOME":
            payload = row.get("payload") or {}
            net = _safe_float(payload.get("net_pnl"))
            ratio = _safe_float(payload.get("close_profit_ratio"))
            if net is None:
                continue
            daily[key]["paper_outcomes"].append(ratio)
            if net > 0:
                daily[key]["wins"] += 1
            journey_id = str(row.get("journey_id") or "")
            history = context_journeys.get(journey_id, [])
            if any(
                _event_type(item) in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}
                and (
                    (_parse_time(item.get("observed_at")) or at) <= at
                )
                for item in history
            ):
                daily[key]["early_outcomes"].append(net)

    result: list[dict[str, Any]] = []
    for key in sorted(daily):
        row = daily[key]
        outcome_count = len(row["paper_outcomes"])
        wins = int(row["wins"])
        early_values = row["early_outcomes"]
        valid_returns = [
            value * 100.0
            for value in row["paper_outcomes"]
            if value is not None
        ]
        result.append(
            {
                "date": key,
                "label": datetime.fromisoformat(key).strftime("%b %d"),
                "early_watch_count": len(row["early_journeys"]),
                "qualified_signal_count": len(row["qualified_signals"]),
                "paper_outcome_count": outcome_count,
                "paper_win_rate_pct": _pct(wins, outcome_count),
                "early_watch_win_rate_pct": _pct(
                    sum(value > 0 for value in early_values),
                    len(early_values),
                ),
                "avg_return_pct": (
                    round(mean(valid_returns), 6)
                    if valid_returns
                    else None
                ),
            }
        )
    return result

def _failure_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in events:
        at = _parse_time(row.get("observed_at"))
        if at is None:
            continue
        kind = _event_type(row)
        if kind == "PAPER_ADMISSION" and not bool(row.get("admitted")):
            reason = str(row.get("reason") or "UNKNOWN_ADMISSION_REJECTION").upper()
            failures.append(
                {
                    "at": at,
                    "family": f"ADMISSION · {reason}",
                    "source": "PAPER_ADMISSION",
                    "signal_id": row.get("signal_id"),
                }
            )
        elif kind == "PAPER_OUTCOME":
            payload = row.get("payload") or {}
            net = _safe_float(payload.get("net_pnl"))
            if net is not None and net <= 0:
                reason = str(payload.get("exit_reason") or "LOSS_UNKNOWN").upper()
                failures.append(
                    {
                        "at": at,
                        "family": f"TRADE · {reason}",
                        "source": "PAPER_OUTCOME",
                        "signal_id": row.get("signal_id"),
                    }
                )
    failures.sort(key=lambda row: row["at"])
    return failures


def _failure_summary(
    scoped_events: list[dict[str, Any]],
    all_events: list[dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    scoped = _failure_events(scoped_events)
    all_failures = _failure_events(all_events)

    seen: set[str] = set()
    repeated = 0
    scoped_start = min(
        (row["at"] for row in scoped),
        default=None,
    )
    if scoped_start is not None:
        seen.update(
            row["family"]
            for row in all_failures
            if row["at"] < scoped_start
        )
    for row in scoped:
        family = row["family"]
        if family in seen:
            repeated += 1
        seen.add(family)

    recent_cutoff = now - timedelta(days=7)
    prior_cutoff = now - timedelta(days=14)
    recent = Counter(
        row["family"] for row in all_failures if row["at"] >= recent_cutoff
    )
    prior = Counter(
        row["family"]
        for row in all_failures
        if prior_cutoff <= row["at"] < recent_cutoff
    )
    families = sorted(set(recent) | set(prior))
    rows: list[dict[str, Any]] = []
    for family in families:
        r = recent.get(family, 0)
        p = prior.get(family, 0)
        if p == 0 and r > 0:
            status = "NEW"
        elif p > 0 and r == 0:
            status = "ERADICATED"
        elif r <= p * 0.6:
            status = "IMPROVING"
        elif r > p * 1.2:
            status = "REGRESSED"
        else:
            status = "RECURRING"
        rows.append(
            {
                "family": family,
                "recent_7d": r,
                "prior_7d": p,
                "change": r - p,
                "status": status,
            }
        )
    rows.sort(
        key=lambda row: (
            {"REGRESSED": 0, "NEW": 1, "RECURRING": 2, "IMPROVING": 3, "ERADICATED": 4}.get(
                row["status"], 9
            ),
            -row["recent_7d"],
        )
    )
    return {
        "events": len(scoped),
        "families": len({row["family"] for row in scoped}),
        "repeated_failure_rate_pct": _pct(repeated, len(scoped)),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "families_detail": rows[:12],
        "methodology": (
            "Failure families use explicit paper-admission rejection reasons and "
            "non-profitable Freqtrade exit reasons. Status compares the most recent "
            "7 days with the preceding 7 days; it is recurrence evidence, not causal attribution."
        ),
    }


def _pattern_performance(
    events: list[dict[str, Any]],
    *,
    context_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    context = context_events if context_events is not None else events
    context_journeys = _journeys(context)
    buckets: dict[str, list[float]] = defaultdict(list)

    for outcome in events:
        if _event_type(outcome) != "PAPER_OUTCOME":
            continue
        ratio = _safe_float((outcome.get("payload") or {}).get("close_profit_ratio"))
        if ratio is None:
            continue
        outcome_at = _parse_time(outcome.get("observed_at"))
        journey_id = str(outcome.get("journey_id") or "")
        history = context_journeys.get(journey_id, [])
        early = [
            row
            for row in history
            if _event_type(row) in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}
            and (
                outcome_at is None
                or (_parse_time(row.get("observed_at")) or outcome_at) <= outcome_at
            )
        ]
        pattern = "NO EARLY WATCH"
        stage = "—"
        if early:
            payload = early[-1].get("payload") or {}
            pattern = str(payload.get("pattern") or "UNCLASSIFIED")
            stage = str(payload.get("stage") or "UNKNOWN")
        buckets[f"{stage} · {pattern}"].append(ratio * 100.0)

    result: list[dict[str, Any]] = []
    for name, values in buckets.items():
        result.append(
            {
                "segment": name,
                "samples": len(values),
                "win_rate_pct": _pct(sum(value > 0 for value in values), len(values)),
                "avg_return_pct": round(mean(values), 6) if values else None,
            }
        )
    result.sort(key=lambda row: (-row["samples"], -(row["avg_return_pct"] or -9999)))
    return result[:10]

def _recent_journeys(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for jid, rows in _journeys(events).items():
        early = [
            row for row in rows
            if _event_type(row) in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}
        ]
        signals = [row for row in rows if _event_type(row) == "QUALIFIED_SIGNAL"]
        admissions = [row for row in rows if _event_type(row) == "PAPER_ADMISSION"]
        outcomes = [row for row in rows if _event_type(row) == "PAPER_OUTCOME"]
        latest = rows[-1]
        early_payload = (early[-1].get("payload") or {}) if early else {}
        outcome_payload = (outcomes[-1].get("payload") or {}) if outcomes else {}
        admission = admissions[-1] if admissions else {}
        status = (
            "CLOSED"
            if outcomes
            else "ADMITTED"
            if admission and admission.get("admitted")
            else "REJECTED"
            if admission
            else "QUALIFIED"
            if signals
            else "EARLY"
        )
        result.append(
            {
                "journey_id": jid,
                "symbol": latest.get("symbol"),
                "started_at": rows[0].get("observed_at"),
                "last_seen_at": latest.get("observed_at"),
                "status": status,
                "early_stage": early_payload.get("stage"),
                "pattern": early_payload.get("pattern"),
                "early_delivered": any(bool(row.get("delivered")) for row in early),
                "signal_id": signals[-1].get("signal_id") if signals else None,
                "admission_reason": admission.get("reason") if admission else None,
                "net_pnl": _safe_float(outcome_payload.get("net_pnl")),
                "pnl_currency": outcome_payload.get("pnl_currency"),
                "return_pct": (
                    round(float(outcome_payload["close_profit_ratio"]) * 100.0, 6)
                    if _safe_float(outcome_payload.get("close_profit_ratio")) is not None
                    else None
                ),
                "exit_reason": outcome_payload.get("exit_reason"),
            }
        )
    result.sort(key=lambda row: str(row.get("last_seen_at") or ""), reverse=True)
    return result[:30]


def _live_candidates(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    lookback_hours: int = 6,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(hours=max(1, lookback_hours))
    latest_by_journey: dict[str, dict[str, Any]] = {}
    for row in events:
        if _event_type(row) not in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}:
            continue
        at = _parse_time(row.get("observed_at"))
        journey_id = str(row.get("journey_id") or "")
        if at is None or at < cutoff or not journey_id:
            continue
        existing = latest_by_journey.get(journey_id)
        existing_at = _parse_time(existing.get("observed_at")) if existing else None
        if existing is None or existing_at is None or at >= existing_at:
            latest_by_journey[journey_id] = row

    rows: list[dict[str, Any]] = []
    for journey_id, row in latest_by_journey.items():
        payload = row.get("payload") or {}
        at = _parse_time(row.get("observed_at"))
        rows.append(
            {
                "journey_id": journey_id,
                "symbol": row.get("symbol"),
                "observed_at": row.get("observed_at"),
                "age_minutes": (
                    round((now - at).total_seconds() / 60.0, 1)
                    if at is not None
                    else None
                ),
                "event_type": _event_type(row),
                "stage": payload.get("stage"),
                "pattern": payload.get("pattern"),
                "opportunity_score": _safe_float(payload.get("opportunity_score")),
                "explosion_potential_score": _safe_float(
                    payload.get("explosion_potential_score")
                ),
                "tradeability_score": _safe_float(payload.get("tradeability_score")),
                "relative_strength_score": _safe_float(
                    payload.get("relative_strength_score")
                ),
                "persistence_score": _safe_float(payload.get("persistence_score")),
                "exhaustion_penalty": _safe_float(payload.get("exhaustion_penalty")),
                "delivery_action": row.get("delivery_action"),
                "delivered": bool(row.get("delivered")),
            }
        )
    rows.sort(
        key=lambda row: (
            -(row["opportunity_score"] if row["opportunity_score"] is not None else -1),
            row["age_minutes"] if row["age_minutes"] is not None else 10**9,
        )
    )
    return rows[:30]


def _paper_trade_rows(
    *,
    cutoff: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    opened: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    for db in DB_FILES:
        if not db.exists():
            continue
        currency = "USDT" if "usdt" in db.name.casefold() else "USD"
        try:
            connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "trades" not in tables:
                continue
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(trades)").fetchall()
            }
            open_rows = connection.execute(
                "SELECT * FROM trades "
                "WHERE enter_tag LIKE 'OHM:%' AND is_open = 1 "
                "ORDER BY id DESC"
            ).fetchall()
            closed_rows = connection.execute(
                "SELECT * FROM trades "
                "WHERE enter_tag LIKE 'OHM:%' AND is_open = 0 "
                "ORDER BY id DESC LIMIT 200"
            ).fetchall()
            for row in [*open_rows, *closed_rows]:
                get = lambda key, default=None: row[key] if key in columns else default
                payload = {
                    "trade_id": get("id"),
                    "pair": get("pair"),
                    "signal_id": get("enter_tag"),
                    "currency": currency,
                    "is_open": bool(get("is_open")),
                    "open_date": get("open_date"),
                    "close_date": get("close_date"),
                    "open_rate": _safe_float(get("open_rate")),
                    "requested_entry": _safe_float(get("open_rate_requested")),
                    "close_rate": _safe_float(get("close_rate")),
                    "stake_amount": _safe_float(get("stake_amount")),
                    "net_pnl": _safe_float(get("close_profit_abs")),
                    "return_pct": (
                        round(float(get("close_profit")) * 100.0, 6)
                        if _safe_float(get("close_profit")) is not None
                        else None
                    ),
                    "exit_reason": get("exit_reason"),
                }
                if payload["is_open"]:
                    opened.append(payload)
                else:
                    closed_at = _parse_time(payload.get("close_date"))
                    if cutoff is None or (
                        closed_at is not None and closed_at >= cutoff
                    ):
                        closed.append(payload)
        except sqlite3.Error:
            continue
        finally:
            connection.close()
    opened.sort(key=lambda row: str(row.get("open_date") or ""), reverse=True)
    closed.sort(key=lambda row: str(row.get("close_date") or ""), reverse=True)
    return {"open": opened, "closed": closed[:50]}


def _dead_letter_count() -> int:
    return len(_read_jsonl(DEFAULT_DEAD_LETTER_FILE))


def _source_health(
    *,
    operations: dict[str, Any],
    paper: dict[str, Any],
    tv: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    last_scan = _parse_time((operations.get("market") or {}).get("last_scan_utc"))
    event_times = [
        parsed
        for parsed in (_parse_time(row.get("observed_at")) for row in events)
        if parsed is not None
    ]
    last_event = max(event_times) if event_times else None
    now = _now()
    scan_age = (now - last_scan).total_seconds() if last_scan else None
    event_age = (now - last_event).total_seconds() if last_event else None
    tv_status = str(tv.get("coverage_status") or "UNKNOWN").upper()
    operations_status = str(
        operations.get("_analytics_status") or "OK"
    ).upper()
    return [
        {
            "source": "Kraken scan",
            "status": "OK" if scan_age is not None and scan_age <= 1800 else "STALE",
            "age_seconds": round(scan_age, 1) if scan_age is not None else None,
            "detail": "Primary market universe and price/liquidity scan.",
        },
        {
            "source": "TradingView v2",
            "status": "OK" if tv_status in {"OK", "HEALTHY", "ADEQUATE"} else tv_status,
            "age_seconds": None,
            "detail": f"Coverage {tv_status}; queue {tv.get('queue_depth', 0)}.",
        },
        {
            "source": "Chief AI",
            "status": (
                "OK"
                if operations_status == "OK"
                else operations_status
            ),
            "age_seconds": None,
            "detail": (
                f"{(operations.get('ai') or {}).get('chief_calls', 0)} calls "
                "in current UTC day."
                if operations_status == "OK"
                else "Operations analytics source unavailable; Chief AI health "
                "cannot be inferred from a zero call count."
            ),
        },
        {
            "source": "O’Pip journeys",
            "status": "OK" if event_age is not None and event_age <= 3600 else "STALE",
            "age_seconds": round(event_age, 1) if event_age is not None else None,
            "detail": f"{len(events)} intelligence journey events retained.",
        },
        {
            "source": "Freqtrade paper",
            "status": str(paper.get("status") or "NOT_READY"),
            "age_seconds": None,
            "detail": "Authoritative dry-run execution layer; USD and USDT workers.",
        },
        {
            "source": "News / catalysts",
            "status": "BASELINE_BUILDING",
            "age_seconds": None,
            "detail": "Source-level freshness instrumentation is not yet persisted for dashboard trending.",
        },
    ]


def _version_attribution(events: list[dict[str, Any]]) -> dict[str, Any]:
    tagged = [
        row
        for row in events
        if isinstance(row.get("measurement_versions"), dict)
    ]
    latest_versions: dict[str, Any] = {}
    if tagged:
        latest = max(
            tagged,
            key=lambda row: str(row.get("observed_at") or ""),
        )
        latest_versions = dict(latest.get("measurement_versions") or {})
    return {
        "current": latest_versions or {
            "intelligence_stack": MEASUREMENT_BASELINE_VERSION,
        },
        "tagged_events": len(tagged),
        "unversioned_events": max(0, len(events) - len(tagged)),
        "coverage_pct": _pct(len(tagged), len(events)),
        "status": "MEASURED" if tagged else "BASELINE_BUILDING",
    }


def _evolution_scorecard(
    all_events: list[dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    context_journeys = _journeys(all_events)
    outcomes_by_signal = {
        str(row.get("signal_id")): row
        for row in all_events
        if _event_type(row) == "PAPER_OUTCOME" and row.get("signal_id")
    }

    def window(start: datetime, end: datetime) -> dict[str, Any]:
        outcomes = [
            row
            for row in all_events
            if _event_type(row) == "PAPER_OUTCOME"
            and (at := _parse_time(row.get("observed_at"))) is not None
            and start <= at < end
        ]
        failures = [
            row
            for row in _failure_events(all_events)
            if start <= row["at"] < end
        ]
        early_outcomes: list[float] = []
        paper_returns: list[float] = []
        paper_wins = 0
        for outcome in outcomes:
            payload = outcome.get("payload") or {}
            net = _safe_float(payload.get("net_pnl"))
            ratio = _safe_float(payload.get("close_profit_ratio"))
            if net is None:
                continue
            if net > 0:
                paper_wins += 1
            if ratio is not None:
                paper_returns.append(ratio * 100.0)
            journey_id = str(outcome.get("journey_id") or "")
            history = context_journeys.get(journey_id, [])
            outcome_at = _parse_time(outcome.get("observed_at"))
            if any(
                _event_type(row) in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}
                and (
                    outcome_at is None
                    or (_parse_time(row.get("observed_at")) or outcome_at) <= outcome_at
                )
                for row in history
            ):
                early_outcomes.append(net)

        maturity_cutoff = now - timedelta(hours=48)
        mature_requested: set[str] = set()
        for row in all_events:
            if _event_type(row) != "QUALIFIED_SIGNAL" or not row.get("signal_id"):
                continue
            at = _parse_time(row.get("observed_at"))
            if (
                at is not None
                and start <= at < end
                and at <= maturity_cutoff
                and bool((row.get("payload") or {}).get("paper_requested"))
            ):
                mature_requested.add(str(row["signal_id"]))
        mature_closed = mature_requested & set(outcomes_by_signal)

        tagged_outcomes = sum(
            isinstance(row.get("measurement_versions"), dict)
            for row in outcomes
        )
        mature_signal_rows = [
            row
            for row in all_events
            if _event_type(row) == "QUALIFIED_SIGNAL"
            and str(row.get("signal_id") or "") in mature_requested
        ]
        tagged_mature_signals = sum(
            isinstance(row.get("measurement_versions"), dict)
            for row in mature_signal_rows
        )
        version_evidence_total = len(outcomes) + len(mature_signal_rows)
        version_evidence_tagged = tagged_outcomes + tagged_mature_signals
        version_coverage_pct = _pct(
            version_evidence_tagged,
            version_evidence_total,
        )

        return {
            "early_precision_pct": _pct(
                sum(value > 0 for value in early_outcomes),
                len(early_outcomes),
            ),
            "signal_to_outcome_pct": _pct(
                len(mature_closed),
                len(mature_requested),
            ),
            "paper_win_rate_pct": _pct(paper_wins, len(outcomes)),
            "avg_return_pct": (
                round(mean(paper_returns), 6)
                if paper_returns
                else None
            ),
            "failure_events": len(failures),
            "samples": len(outcomes),
            "mature_signal_samples": len(mature_requested),
            "version_coverage_pct": version_coverage_pct,
        }

    recent = window(now - timedelta(days=7), now)
    prior = window(now - timedelta(days=14), now - timedelta(days=7))
    version_ready = (
        recent["version_coverage_pct"] == 100.0
        and prior["version_coverage_pct"] == 100.0
    )
    sample_ready = (
        recent["samples"] >= 5
        and prior["samples"] >= 5
    )
    comparison_ready = sample_ready and version_ready

    metrics = []
    specs = [
        ("Early-watch / closed win rate", "early_precision_pct", "higher"),
        ("Mature signal → closed conversion", "signal_to_outcome_pct", "higher"),
        ("Paper win rate", "paper_win_rate_pct", "higher"),
        ("Average paper return", "avg_return_pct", "higher"),
        ("Failure events", "failure_events", "lower"),
    ]
    for label, key, better in specs:
        r = recent.get(key)
        p = prior.get(key)
        delta = None
        if r is not None and p is not None:
            delta = round(float(r) - float(p), 4)
        metrics.append(
            {
                "metric": label,
                "recent": r,
                "prior": p,
                "delta": delta,
                "direction": better,
                "status": (
                    "BASELINE_BUILDING"
                    if not comparison_ready
                    else "MEASURED"
                ),
            }
        )
    return {
        "baseline_version": MEASUREMENT_BASELINE_VERSION,
        "recent_window": "Last 7 days",
        "prior_window": "Previous 7 days",
        "recent_samples": recent["samples"],
        "prior_samples": prior["samples"],
        "recent_mature_signal_samples": recent["mature_signal_samples"],
        "prior_mature_signal_samples": prior["mature_signal_samples"],
        "recent_version_coverage_pct": recent["version_coverage_pct"],
        "prior_version_coverage_pct": prior["version_coverage_pct"],
        "metrics": metrics,
        "attribution_status": (
            "BASELINE_BUILDING"
            if not comparison_ready
            else "MEASURED"
        ),
        "version_attribution": _version_attribution(all_events),
    }

def build_evolution_dashboard(scope: str = "30d") -> dict[str, Any]:
    scope = str(scope or "30d").lower()
    if scope not in SUPPORTED_SCOPES:
        raise ValueError("scope must be today, 7d, 30d, or all")

    now = _now()
    all_events = _read_jsonl(EVENT_FILE)
    cutoff = _cutoff(scope, now)
    scoped_events = _filter_events(all_events, cutoff=cutoff)
    scoped_journey_history = _scoped_journey_history(
        all_events,
        cutoff=cutoff,
    )
    try:
        operations = build_operations_summary("today")
        operations["_analytics_status"] = "OK"
    except Exception as exc:
        operations = {
            "_analytics_status": "UNAVAILABLE",
            "_analytics_error": type(exc).__name__,
            "market": {},
            "ai": {},
            "current": {},
        }
    try:
        paper = freqtrade_dry_run_status()
    except Exception as exc:
        paper = {
            "status": "UNAVAILABLE",
            "open_trades": 0,
            "closed_trades": 0,
            "realized_pnl_by_currency": {},
            "active_stake_by_currency": {},
            "workers": {},
            "reason": f"{type(exc).__name__}",
        }
    try:
        paper_control = get_paper_trade_control()
    except Exception:
        class _Control:
            enabled = False
            status = "UNAVAILABLE"
            updated_at = None
            updated_by = "UNKNOWN"
        paper_control = _Control()
    try:
        tv = load_tradingview_evidence_diagnostics()
    except Exception as exc:
        tv = {
            "coverage_status": "UNAVAILABLE",
            "queue_depth": 0,
            "reason": f"{type(exc).__name__}",
        }
    outcomes = _paper_outcomes(scoped_events)
    failure = _failure_summary(scoped_events, all_events, now=now)
    evidence = outbox_health()
    funnel = _funnel(scoped_journey_history)

    return {
        "schema_version": EVOLUTION_SCHEMA_VERSION,
        "generated_at_utc": now.isoformat(),
        "scope": scope,
        "read_only": True,
        "trade_authority_changed": False,
        "executive": {
            "paper_mode": "ON" if paper_control.enabled else "OFF",
            "paper_status": paper.get("status"),
            "open_paper_trades": paper.get("open_trades", 0),
            "closed_paper_trades": paper.get("closed_trades", 0),
            "realized_pnl_by_currency": paper.get("realized_pnl_by_currency") or {},
            "active_stake_by_currency": paper.get("active_stake_by_currency") or {},
            "early_watch_to_signal_pct": funnel["early_to_signal_pct"],
            "paper_win_rate_pct": _pct(
                sum(row["net_pnl"] > 0 for row in outcomes),
                len(outcomes),
            ),
            "avg_return_pct": (
                round(
                    mean(row["return_pct"] for row in outcomes if row["return_pct"] is not None),
                    6,
                )
                if any(row["return_pct"] is not None for row in outcomes)
                else None
            ),
            "repeated_failure_rate_pct": failure["repeated_failure_rate_pct"],
            "learning_health": (
                "OK"
                if evidence.get("status") == "OK"
                and _dead_letter_count() == 0
                else "ATTENTION"
            ),
        },
        "evolution": _evolution_scorecard(all_events, now=now),
        "intelligence_monitor": {
            "sources": _source_health(
                operations=operations,
                paper=paper,
                tv=tv,
                events=all_events,
            ),
            "market": operations.get("market") or {},
            "ai": operations.get("ai") or {},
            "tradingview": tv,
            "live_candidates": _live_candidates(
                all_events,
                now=now,
            ),
        },
        "funnel": funnel,
        "trend": _daily_trend(
            scoped_events,
            context_events=all_events,
        ),
        "paper": {
            "status": paper,
            "control": {
                "enabled": paper_control.enabled,
                "status": paper_control.status,
                "updated_at": paper_control.updated_at,
                "updated_by": paper_control.updated_by,
            },
            "equity_curve": _equity_curve(outcomes),
            "trades": _paper_trade_rows(cutoff=_cutoff(scope, now)),
        },
        "signal_intelligence": {
            "pattern_performance": _pattern_performance(
                scoped_events,
                context_events=all_events,
            ),
        },
        "failures": failure,
        "journeys": _recent_journeys(scoped_journey_history),
        "learning_health": {
            "journey_events": len(scoped_events),
            "all_journey_events": len(all_events),
            "outbox": evidence,
            "dead_letter_count": _dead_letter_count(),
            "freqtrade_dedup": "SQLITE_PRIMARY_KEY",
            "freqtrade_outcome_rows": len(outcomes),
            "measurement_baseline": MEASUREMENT_BASELINE_VERSION,
            "version_attribution": _version_attribution(all_events),
            "notes": [
                "Historical strategy-version tags were not persisted before this dashboard baseline.",
                "Failure recurrence uses explicit rejection/exit codes and does not claim causal attribution.",
                "News/catalyst source freshness needs additional persisted source telemetry.",
            ],
        },
    }
