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
    early_to_signal = [
        value for value in journeys.values()
        if value["early"] and value["signals"]
    ]

    paper_outcomes: list[dict[str, Any]] = []
    early_paper: list[dict[str, Any]] = []
    direct_paper: list[dict[str, Any]] = []
    latencies: list[float] = []
    buckets: dict[str, dict[str, list[float] | int]] = {}

    for value in journeys.values():
        if value["early"] and value["signals"]:
            first_early = min(
                (_parse(row.get("observed_at")) for row in value["early"]),
                default=None,
            )
            first_signal = min(
                (_parse(row.get("observed_at")) for row in value["signals"]),
                default=None,
            )
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
                {"count": 0, "wins": 0, "pnl": []},
            )
            bucket["count"] = int(bucket["count"]) + 1
            if pnl > 0:
                bucket["wins"] = int(bucket["wins"]) + 1
            bucket["pnl"].append(pnl)

    def paper_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pnl: list[float] = []
        for row in rows:
            try:
                pnl.append(float(row.get("net_pnl")))
            except (TypeError, ValueError):
                continue
        wins = sum(value > 0 for value in pnl)
        losses = sum(value < 0 for value in pnl)
        return {
            "count": len(pnl),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": _pct(wins, len(pnl)),
            "net_pnl": round(sum(pnl), 8) if pnl else 0.0,
            "avg_net_pnl": round(mean(pnl), 8) if pnl else None,
        }

    bucket_rows: dict[str, Any] = {}
    for key, bucket in buckets.items():
        pnl = list(bucket["pnl"])
        count = int(bucket["count"])
        wins = int(bucket["wins"])
        bucket_rows[key] = {
            "count": count,
            "wins": wins,
            "win_rate_pct": _pct(wins, count),
            "net_pnl": round(sum(pnl), 8) if pnl else 0.0,
            "avg_net_pnl": round(mean(pnl), 8) if pnl else None,
        }

    profile = {
        "schema_version": 1,
        "population": "FREQTRADE_DRY_RUN_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events_considered": len(events),
        "journeys": len(journeys),
        "early_watch_journeys": len(early_journeys),
        "qualified_signal_journeys": len(signal_journeys),
        "paper_outcome_journeys": len(paper_journeys),
        "early_watch_to_signal_conversion_pct": _pct(
            len(early_to_signal),
            len(early_journeys),
        ),
        "signal_to_paper_outcome_conversion_pct": _pct(
            len(paper_journeys),
            len(signal_journeys),
        ),
        "early_watch_to_signal_latency_minutes": {
            "samples": len(latencies),
            "average": round(mean(latencies), 2) if latencies else None,
            "minimum": round(min(latencies), 2) if latencies else None,
            "maximum": round(max(latencies), 2) if latencies else None,
        },
        "paper_performance": paper_stats(paper_outcomes),
        "paper_performance_with_early_watch": paper_stats(early_paper),
        "paper_performance_without_early_watch": paper_stats(direct_paper),
        "early_stage_pattern_performance": bucket_rows,
        "learning_only": True,
        "production_decision_changed": False,
    }
    if persist:
        profile_file.parent.mkdir(parents=True, exist_ok=True)
        save_json_atomic(profile_file, profile)
    return profile
