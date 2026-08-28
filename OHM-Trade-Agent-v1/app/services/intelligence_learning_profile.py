from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.intelligence_journey import EVENT_FILE
from app.services.registry_io import save_json_atomic


PROFILE_FILE = Path("/app/data/intelligence_learning/profile.json")
MAX_EVENTS = 10000


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def build_intelligence_learning_profile(
    *,
    event_file: Path = EVENT_FILE,
    persist: bool = True,
    profile_file: Path = PROFILE_FILE,
) -> dict[str, Any]:
    events = _read_events(event_file)
    journeys: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "early": [],
            "signals": [],
            "outcomes": [],
        }
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
    signal_journeys = [value for value in journeys.values() if value["signals"]]
    paper_journeys = [value for value in journeys.values() if value["outcomes"]]
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
        if row.get("signal_id")
        and bool((row.get("payload") or {}).get("paper_requested"))
    }
    paper_outcome_signal_ids = {
        str(row.get("signal_id"))
        for value in journeys.values()
        for row in value["outcomes"]
        if row.get("signal_id")
    }
    converted_signal_ids = paper_requested_signal_ids & paper_outcome_signal_ids
    early_to_signal = [
        value for value in journeys.values()
        if value["early"] and value["signals"]
    ]

    paper_outcomes: list[dict[str, Any]] = []
    early_paper: list[dict[str, Any]] = []
    delivered_early_paper: list[dict[str, Any]] = []
    nondelivered_early_paper: list[dict[str, Any]] = []
    direct_paper: list[dict[str, Any]] = []
    latencies: list[float] = []
    buckets: dict[str, dict[str, Any]] = {}

    for value in journeys.values():
        if value["early"] and value["signals"]:
            early_times = [
                parsed
                for parsed in (_parse(row.get("observed_at")) for row in value["early"])
                if parsed is not None
            ]
            signal_times = [
                parsed
                for parsed in (_parse(row.get("observed_at")) for row in value["signals"])
                if parsed is not None
            ]
            first_early = min(early_times) if early_times else None
            first_signal = min(signal_times) if signal_times else None
            if first_early and first_signal and first_signal >= first_early:
                latencies.append((first_signal - first_early).total_seconds() / 60.0)

        for outcome in value["outcomes"]:
            payload = outcome.get("payload") or {}
            try:
                pnl = float(payload.get("net_pnl"))
            except (TypeError, ValueError):
                continue
            paper_outcomes.append(payload)
            if value["early"]:
                early_paper.append(payload)
                if any(bool(row.get("delivered")) for row in value["early"]):
                    delivered_early_paper.append(payload)
                else:
                    nondelivered_early_paper.append(payload)
            else:
                direct_paper.append(payload)

            early_payload = (
                (value["early"][-1].get("payload") or {})
                if value["early"]
                else {}
            )
            stage = str(early_payload.get("stage") or "NO_EARLY_WATCH")
            pattern = str(early_payload.get("pattern") or "UNCLASSIFIED")
            key = f"{stage}|{pattern}"
            bucket = buckets.setdefault(
                key,
                {
                    "count": 0,
                    "wins": 0,
                    "returns": [],
                    "pnl_by_currency": {},
                },
            )
            bucket["count"] = int(bucket["count"]) + 1
            if pnl > 0:
                bucket["wins"] = int(bucket["wins"]) + 1
            try:
                bucket["returns"].append(float(payload.get("close_profit_ratio")))
            except (TypeError, ValueError):
                pass
            currency = str(payload.get("pnl_currency") or "USD").upper()
            by_currency = bucket["pnl_by_currency"]
            by_currency.setdefault(currency, []).append(pnl)

    def paper_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pnl: list[float] = []
        pnl_by_currency: dict[str, list[float]] = defaultdict(list)
        returns: list[float] = []
        for row in rows:
            try:
                value = float(row.get("net_pnl"))
            except (TypeError, ValueError):
                continue
            currency = str(row.get("pnl_currency") or "USD").upper()
            pnl.append(value)
            pnl_by_currency[currency].append(value)
            try:
                returns.append(float(row.get("close_profit_ratio")))
            except (TypeError, ValueError):
                pass
        wins = sum(value > 0 for value in pnl)
        losses = sum(value < 0 for value in pnl)
        by_currency = {
            currency: {
                "count": len(values),
                "net_pnl": round(sum(values), 8),
                "avg_net_pnl": round(mean(values), 8),
            }
            for currency, values in sorted(pnl_by_currency.items())
        }
        # Absolute USD and USDT P/L are intentionally not combined into one
        # exact money figure. Cross-quote comparison uses return ratios.
        single_currency_net = (
            round(sum(pnl), 8)
            if len(by_currency) <= 1 and pnl
            else None
        )
        return {
            "count": len(pnl),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": _pct(wins, len(pnl)),
            "net_pnl": single_currency_net,
            "net_pnl_by_currency": by_currency,
            "avg_net_pnl": round(mean(pnl), 8) if len(by_currency) <= 1 and pnl else None,
            "avg_return_pct": round(mean(returns) * 100.0, 6) if returns else None,
        }

    bucket_rows: dict[str, Any] = {}
    for key, bucket in buckets.items():
        count = int(bucket["count"])
        wins = int(bucket["wins"])
        returns = list(bucket["returns"])
        pnl_by_currency = {
            currency: {
                "count": len(values),
                "net_pnl": round(sum(values), 8),
                "avg_net_pnl": round(mean(values), 8),
            }
            for currency, values in sorted(
                (bucket["pnl_by_currency"] or {}).items()
            )
        }
        bucket_rows[key] = {
            "count": count,
            "wins": wins,
            "win_rate_pct": _pct(wins, count),
            "avg_return_pct": (
                round(mean(returns) * 100.0, 6)
                if returns
                else None
            ),
            "net_pnl_by_currency": pnl_by_currency,
        }

    profile = {
        "schema_version": 1,
        "population": "FREQTRADE_DRY_RUN_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events_considered": len(events),
        "journeys": len(journeys),
        "early_watch_journeys": len(early_journeys),
        "qualified_signal_journeys": len(signal_journeys),
        "qualified_signals": len(qualified_signal_ids),
        "paper_requested_signals": len(paper_requested_signal_ids),
        "paper_outcome_journeys": len(paper_journeys),
        "paper_outcome_signals": len(paper_outcome_signal_ids),
        "early_watch_to_signal_conversion_pct": _pct(
            len(early_to_signal),
            len(early_journeys),
        ),
        "signal_to_paper_outcome_conversion_pct": _pct(
            len(converted_signal_ids),
            len(paper_requested_signal_ids),
        ),
        "early_watch_to_signal_latency_minutes": {
            "samples": len(latencies),
            "average": round(mean(latencies), 2) if latencies else None,
            "minimum": round(min(latencies), 2) if latencies else None,
            "maximum": round(max(latencies), 2) if latencies else None,
        },
        "paper_performance": paper_stats(paper_outcomes),
        "paper_performance_with_early_watch": paper_stats(early_paper),
        "paper_performance_after_delivered_early_alert": paper_stats(delivered_early_paper),
        "paper_performance_after_nondelivered_early_detection": paper_stats(nondelivered_early_paper),
        "paper_performance_without_early_watch": paper_stats(direct_paper),
        "early_stage_pattern_performance": bucket_rows,
        "learning_only": True,
        "production_decision_changed": False,
    }
    if persist:
        profile_file.parent.mkdir(parents=True, exist_ok=True)
        save_json_atomic(profile_file, profile)
    return profile
