from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
from typing import Any

from app.exchanges.kraken_identity import canonicalize_pair
from app.services.registry_io import load_json, registry_lock, save_json_atomic


QUEUE_FILE = Path("/app/data/opportunity_monitor_queue.json")


@dataclass(frozen=True)
class CandidateObservation:
    symbol: str
    direction: str
    source: str
    observed_at: datetime
    price: float | None = None
    relative_strength_percentile: float | None = None
    volume_acceleration_score: float | None = None
    liquidity_usd: float | None = None
    priority_score: float = 0.0

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.direction.upper() not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")


@dataclass(frozen=True)
class QueueSummary:
    active: int
    expired: int
    evicted: int


def _lock_file() -> Path:
    return QUEUE_FILE.parent / f".{QUEUE_FILE.name}.lock"


def _max_candidates() -> int:
    try:
        return max(25, min(1000, int(os.getenv("OPIP_MONITOR_QUEUE_MAX", "200"))))
    except ValueError:
        return 200


def _ttl_seconds() -> int:
    try:
        return max(
            300,
            min(24 * 3600, int(os.getenv("OPIP_MONITOR_QUEUE_TTL_SECONDS", "21600"))),
        )
    except ValueError:
        return 21600


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _identity(symbol: str, direction: str) -> str:
    pair = canonicalize_pair(symbol) or str(symbol or "").upper()
    return f"{pair}:{direction.upper()}"


def _history_append(history: list[dict[str, Any]], *, at: str, value: float | None) -> list[dict[str, Any]]:
    if value is None:
        return history[-11:]
    if history and history[-1].get("at") == at and history[-1].get("value") == value:
        return history[-12:]
    history.append({"at": at, "value": value})
    return history[-12:]


def upsert_candidate(observation: CandidateObservation) -> QueueSummary:
    now = _utc(observation.observed_at)
    key = _identity(observation.symbol, observation.direction)
    ttl = _ttl_seconds()
    expired_count = 0
    evicted_count = 0

    with registry_lock(_lock_file()):
        rows = load_json(QUEUE_FILE)

        # Expire before insertion so stale candidates cannot occupy capacity.
        for existing_key, row in list(rows.items()):
            expires = _parse((row or {}).get("expires_at")) if isinstance(row, dict) else None
            if expires is None or expires <= now:
                rows.pop(existing_key, None)
                expired_count += 1

        previous = rows.get(key)
        row = dict(previous) if isinstance(previous, dict) else {}
        observed_iso = now.isoformat()
        row["schema_version"] = 1
        row["candidate_id"] = key
        row["symbol"] = canonicalize_pair(observation.symbol) or observation.symbol.upper()
        row["direction"] = observation.direction.upper()
        row.setdefault("first_seen_at", observed_iso)
        row["last_seen_at"] = observed_iso
        row["expires_at"] = (now + timedelta(seconds=ttl)).isoformat()

        source = str(observation.source)
        sources = {
            str(item)
            for item in (row.get("sources") or [])
            if str(item).strip()
        }
        sources.add(source)
        row["sources"] = sorted(sources)

        price = _finite_optional(observation.price)
        rs = _finite_optional(observation.relative_strength_percentile)
        volume = _finite_optional(observation.volume_acceleration_score)
        liquidity = _finite_optional(observation.liquidity_usd)
        priority = _finite_optional(observation.priority_score) or 0.0

        if price is not None:
            row["latest_price"] = price
        if rs is not None:
            row["relative_strength_percentile"] = rs
        if volume is not None:
            row["volume_acceleration_score"] = volume
        if liquidity is not None:
            row["liquidity_usd"] = liquidity

        source_priorities = {
            str(name): float(value)
            for name, value in (row.get("source_priority_scores") or {}).items()
            if _finite_optional(value) is not None
        }
        source_priorities[source] = max(0.0, min(100.0, priority))
        row["source_priority_scores"] = source_priorities
        row["priority_score"] = max(source_priorities.values(), default=0.0)

        rs_history = list(row.get("relative_strength_history") or [])
        volume_history = list(row.get("volume_acceleration_history") or [])
        row["relative_strength_history"] = _history_append(
            rs_history,
            at=observed_iso,
            value=rs,
        )
        row["volume_acceleration_history"] = _history_append(
            volume_history,
            at=observed_iso,
            value=volume,
        )
        if len(row["relative_strength_history"]) >= 2:
            first = row["relative_strength_history"][0]["value"]
            last = row["relative_strength_history"][-1]["value"]
            row["relative_strength_velocity"] = round(float(last) - float(first), 4)
        else:
            row["relative_strength_velocity"] = 0.0

        rows[key] = row

        maximum = _max_candidates()
        if len(rows) > maximum:
            ranked = sorted(
                rows.items(),
                key=lambda item: (
                    float((item[1] or {}).get("priority_score") or 0.0),
                    str((item[1] or {}).get("last_seen_at") or ""),
                    item[0],
                ),
            )
            for evict_key, _ in ranked[: len(rows) - maximum]:
                rows.pop(evict_key, None)
                evicted_count += 1

        save_json_atomic(QUEUE_FILE, rows)
        active = len(rows)

    return QueueSummary(active=active, expired=expired_count, evicted=evicted_count)


def read_candidates(*, now: datetime | None = None) -> list[dict[str, Any]]:
    current = _utc(now or datetime.now(timezone.utc))
    with registry_lock(_lock_file()):
        rows = load_json(QUEUE_FILE)
        changed = False
        for key, row in list(rows.items()):
            expires = _parse((row or {}).get("expires_at")) if isinstance(row, dict) else None
            if expires is None or expires <= current:
                rows.pop(key, None)
                changed = True
        if changed:
            save_json_atomic(QUEUE_FILE, rows)

    values = [dict(row) for row in rows.values() if isinstance(row, dict)]
    values.sort(
        key=lambda row: (
            -float(row.get("priority_score") or 0.0),
            -float(row.get("relative_strength_velocity") or 0.0),
            str(row.get("candidate_id") or ""),
        )
    )
    return values