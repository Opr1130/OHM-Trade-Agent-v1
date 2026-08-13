from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.active_trade_registry import get_active_trades
from app.services.decision_learning import classify_shadow_decision
from app.services.openai_usage_telemetry import USAGE_FILE
from app.services.order_intent_registry import list_order_intents
from app.services.pending_setup_registry import get_pending_setups
from app.services.registry_io import registry_lock
from app.services.shadow_learning import get_shadow_records
from app.services.trade_outcome_registry import get_outcomes


SCAN_ACTIVITY_FILE = Path("/app/data/scan_activity.jsonl")
SCAN_ACTIVITY_LOCK = SCAN_ACTIVITY_FILE.parent / ".scan_activity.lock"


class _Tee(io.TextIOBase):
    def __init__(self, *targets: io.TextIOBase):
        self.targets = targets

    def write(self, value: str) -> int:
        for target in self.targets:
            target.write(value)
        return len(value)

    def flush(self) -> None:
        for target in self.targets:
            target.flush()


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _today_utc(value: Any) -> bool:
    dt = _parse_time(value)
    return dt is not None and dt.date() == _now().date()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows


def parse_scan_output(text: str) -> dict[str, Any]:
    """Convert the existing human-readable scanner output into durable metrics.

    This keeps the dashboard observational: scanner behavior is unchanged and
    telemetry failure can never alter a trade decision.
    """
    result: dict[str, Any] = {
        "requested": 0,
        "analyzed": 0,
        "skipped": 0,
        "failed": 0,
        "technical_shortlist": 0,
        "long_shortlist": 0,
        "short_shortlist": 0,
        "market_regime": "UNKNOWN",
        "ai_top_candidates": 0,
        "qualified_alerts": 0,
        "qualified_survivors": 0,
        "margin_rejects": 0,
        "execution_rejects": 0,
        "target_rejects": 0,
        "economic_rejects": 0,
        "pending_saved": 0,
        "notifications_sent": 0,
    }
    patterns = {
        "requested": r"^Requested:\s*(\d+)",
        "analyzed": r"^Analyzed:\s*(\d+)",
        "skipped": r"^Skipped:\s*(\d+)",
        "failed": r"^Failed:\s*(\d+)",
        "technical_shortlist": r"^Technical shortlist:\s*(\d+)",
        "ai_top_candidates": r"^AI top candidates:\s*(\d+)",
        "qualified_alerts": r"^Qualified alerts before deterministic quality gates:\s*(\d+)",
        "qualified_survivors": r"^Qualified survivors:\s*(\d+)",
        "execution_rejects": r"^Execution structural/short-quality rejects:\s*(\d+)",
        "target_rejects": r"^Target quality rejects:\s*(\d+)",
        "economic_rejects": r"^Economic rejects:\s*(\d+)",
        "pending_saved": r"^Pending setups saved:\s*(\d+)",
        "notifications_sent": r"^Telegram notifications sent:\s*(\d+)",
    }
    for line in text.splitlines():
        line = line.strip()
        for key, pattern in patterns.items():
            match = re.match(pattern, line)
            if match:
                result[key] = int(match.group(1))
        match = re.match(r"^Directional shortlist:\s*LONG=(\d+)\s+SHORT=(\d+)", line)
        if match:
            result["long_shortlist"] = int(match.group(1))
            result["short_shortlist"] = int(match.group(2))
        match = re.match(r"^Regime:\s*(\S+)", line)
        if match:
            result["market_regime"] = match.group(1)
        if line.startswith("MARGIN ") and "Status=INELIGIBLE" in line:
            result["margin_rejects"] += 1
    return result


def record_scan_output(text: str, *, started_at: datetime | None = None) -> dict[str, Any]:
    row = parse_scan_output(text)
    row["timestamp_utc"] = (started_at or _now()).isoformat()
    row["completed_at_utc"] = _now().isoformat()
    try:
        SCAN_ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with registry_lock(SCAN_ACTIVITY_LOCK):
            with SCAN_ACTIVITY_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                handle.flush()
    except Exception:
        # Analytics must never block or fail the production scan path.
        pass
    return row


def run_scan_with_telemetry(scan_callable: Callable[[], Any]) -> Any:
    started = _now()
    buffer = io.StringIO()
    previous_stdout = sys.stdout
    sys.stdout = _Tee(previous_stdout, buffer)
    try:
        return scan_callable()
    finally:
        sys.stdout = previous_stdout
        record_scan_output(buffer.getvalue(), started_at=started)


def _sum(rows: list[dict[str, Any]], field: str) -> int:
    total = 0
    for row in rows:
        value = row.get(field)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def _decision_counts(shadows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("decision") or "UNKNOWN").upper() for row in shadows)
    rejected = sum(
        count
        for decision, count in counts.items()
        if decision.endswith("_REJECT") or decision in {"REJECT", "AI_NOT_SELECTED", "NO_TRADE"}
    )
    accepted = sum(counts[name] for name in ("ENTER", "ENTER_NOW", "ALERT", "QUALIFIED"))
    waits = counts["WAIT"] + counts["AI_WATCH"]
    return {
        "accepted": accepted,
        "rejected": rejected,
        "wait": waits,
        "total": sum(counts.values()),
        "by_type": dict(sorted(counts.items())),
    }


def _outcome_metrics(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    financial = [row for row in outcomes if isinstance(row.get("net_pnl"), (int, float))]
    profits = [row for row in financial if float(row["net_pnl"]) > 0]
    losses = [row for row in financial if float(row["net_pnl"]) < 0]
    breakeven = [row for row in financial if float(row["net_pnl"]) == 0]
    net = sum(float(row["net_pnl"]) for row in financial)
    gross = sum(float(row.get("gross_pnl") or 0.0) for row in financial)
    costs = sum(float(row.get("total_costs") or 0.0) for row in financial)
    return {
        "closed_financial_trades": len(financial),
        "profit_count": len(profits),
        "loss_count": len(losses),
        "breakeven_count": len(breakeven),
        "net_pnl": round(net, 8),
        "gross_pnl": round(gross, 8),
        "total_costs": round(costs, 8),
    }


def build_operations_summary(scope: str = "today") -> dict[str, Any]:
    if scope not in {"today", "all"}:
        raise ValueError("scope must be today or all")

    scans = _read_jsonl(SCAN_ACTIVITY_FILE)
    usage = _read_jsonl(USAGE_FILE)
    shadows = get_shadow_records()
    outcomes = get_outcomes()

    if scope == "today":
        scans = [row for row in scans if _today_utc(row.get("timestamp_utc"))]
        usage = [row for row in usage if _today_utc(row.get("timestamp_utc"))]
        shadows = [row for row in shadows if _today_utc(row.get("observed_at"))]
        outcomes = [row for row in outcomes if _today_utc(row.get("terminal_timestamp"))]

    observations = 0
    for row in shadows:
        observations += len(row.get("observations") or {})

    quality = [classify_shadow_decision(row) for row in shadows]
    evaluated = [item for item in quality if item.get("status") == "EVALUATED"]
    missed = sum(bool(item.get("missed_profitable_opportunity")) for item in evaluated)
    correct_avoid = sum(bool(item.get("correct_wait_or_reject")) for item in evaluated)

    latest_scan = scans[-1] if scans else {}
    decisions = _decision_counts(shadows)
    trading = _outcome_metrics(outcomes)

    return {
        "generated_at_utc": _now().isoformat(),
        "scope": scope,
        "market": {
            "scans": len(scans),
            "pairs_requested": _sum(scans, "requested"),
            "pairs_analyzed": _sum(scans, "analyzed"),
            "pairs_skipped": _sum(scans, "skipped"),
            "pairs_failed": _sum(scans, "failed"),
            "technical_shortlist": _sum(scans, "technical_shortlist"),
            "long_shortlist": _sum(scans, "long_shortlist"),
            "short_shortlist": _sum(scans, "short_shortlist"),
            "latest_regime": latest_scan.get("market_regime", "UNKNOWN"),
            "last_scan_utc": latest_scan.get("completed_at_utc"),
        },
        "ai": {
            "chief_calls": len(usage),
            "candidates_reviewed": _sum(usage, "candidate_count"),
            "input_tokens": _sum(usage, "input_tokens"),
            "output_tokens": _sum(usage, "output_tokens"),
            "reasoning_tokens": _sum(usage, "reasoning_tokens"),
            "total_tokens": _sum(usage, "total_tokens"),
            "learning_paid_ai_calls": 0,
        },
        "funnel": {
            "analyzed": _sum(scans, "analyzed"),
            "shortlisted": _sum(scans, "technical_shortlist"),
            "ai_reviewed": _sum(usage, "candidate_count"),
            "ai_qualified": _sum(scans, "qualified_alerts"),
            "final_qualified": _sum(scans, "qualified_survivors"),
            "margin_rejects": _sum(scans, "margin_rejects"),
            "execution_rejects": _sum(scans, "execution_rejects"),
            "target_rejects": _sum(scans, "target_rejects"),
            "economic_rejects": _sum(scans, "economic_rejects"),
        },
        "decisions": decisions,
        "trading": trading,
        "learning": {
            "decisions_tracked": len(shadows),
            "observations": observations,
            "evaluated_decisions": len(evaluated),
            "correct_avoids": correct_avoid,
            "missed_profitable_opportunities": missed,
        },
        "current": {
            "active_trades": len(get_active_trades()),
            "pending_setups": len(get_pending_setups()),
            "live_order_intents": sum(
                1 for item in list_order_intents() if str(getattr(item, "status", "")).lower() not in {"filled", "cancelled", "closed"}
            ),
        },
    }
