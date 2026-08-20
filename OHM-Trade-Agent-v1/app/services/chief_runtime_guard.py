from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services.registry_io import load_json, registry_lock, save_json_atomic


CACHE_FILE = Path("/app/data/chief_review_cache.json")
CACHE_LOCK = CACHE_FILE.parent / ".chief_review_cache.lock"
USAGE_FILE = Path("/app/data/openai_usage.jsonl")


def _bucket(value: Any, step: float) -> Any:
    if not isinstance(value, (int, float)):
        return value
    if step <= 0:
        return value
    return round(float(value) / step) * step


def _candidate_fingerprint_view(candidate: dict[str, Any]) -> dict[str, Any]:
    structure = candidate.get("market_structure") or {}
    quality = candidate.get("deterministic_quality_by_risk_level") or {}
    movement = candidate.get("price_movement_intelligence") or {}
    execution = candidate.get("execution_evidence") or {}
    return {
        "symbol": candidate.get("symbol"),
        "direction": candidate.get("direction"),
        "technical_score_bucket": _bucket(candidate.get("technical_score"), 5),
        "trend": candidate.get("trend"),
        "rsi_bucket": _bucket(candidate.get("rsi"), 2),
        "volume_bucket": _bucket(candidate.get("volume_ratio"), 0.25),
        "momentum_6h_bucket": _bucket(structure.get("momentum_6h_pct"), 0.5),
        "momentum_24h_bucket": _bucket(structure.get("momentum_24h_pct"), 1.0),
        "distance_24h_high_bucket": _bucket(structure.get("distance_to_24h_high_pct"), 0.5),
        "low_quality": {
            "target": (quality.get("low") or {}).get("target_quality_qualified"),
            "economic": (quality.get("low") or {}).get("economic_qualified"),
        },
        "medium_quality": {
            "target": (quality.get("medium") or {}).get("target_quality_qualified"),
            "economic": (quality.get("medium") or {}).get("economic_qualified"),
        },
        "movement_stage": movement.get("stage") if isinstance(movement, dict) else None,
        "execution_status": execution.get("status") if isinstance(execution, dict) else None,
    }


def build_chief_fingerprint(request_payload: dict[str, Any]) -> str:
    compact = {
        "candidate_count": request_payload.get("candidate_count"),
        "market_regime": request_payload.get("market_regime_context"),
        "candidates": [
            _candidate_fingerprint_view(item)
            for item in (request_payload.get("candidates") or [])
            if isinstance(item, dict)
        ],
    }
    raw = json.dumps(compact, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("CHIEF_REVIEW_CACHE_TTL_SECONDS", "0")))
    except ValueError:
        return 0


def get_cached_review(fingerprint: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    ttl = cache_ttl_seconds()
    if ttl <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        with registry_lock(CACHE_LOCK):
            state = load_json(CACHE_FILE)
            row = state.get(fingerprint)
            if not isinstance(row, dict):
                return None
            created_raw = row.get("created_at")
            created = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if now - created.astimezone(timezone.utc) > timedelta(seconds=ttl):
                return None
            review = row.get("review")
            return dict(review) if isinstance(review, dict) else None
    except (OSError, TimeoutError, ValueError):
        return None


def store_cached_review(
    fingerprint: str,
    review: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    if cache_ttl_seconds() <= 0:
        return
    now = now or datetime.now(timezone.utc)
    try:
        with registry_lock(CACHE_LOCK):
            state = load_json(CACHE_FILE)
            state[fingerprint] = {
                "created_at": now.isoformat(),
                "review": review,
            }
            if len(state) > 128:
                rows = sorted(
                    state.items(),
                    key=lambda item: str((item[1] or {}).get("created_at") or ""),
                    reverse=True,
                )
                state = dict(rows[:128])
            save_json_atomic(CACHE_FILE, state)
    except (OSError, TimeoutError):
        return


def _today_usage(*, now: datetime | None = None) -> tuple[int, int]:
    now = now or datetime.now(timezone.utc)
    if not USAGE_FILE.exists():
        return 0, 0
    calls = 0
    tokens = 0
    try:
        with USAGE_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    stamp = datetime.fromisoformat(
                        str(row.get("timestamp_utc") or "").replace("Z", "+00:00")
                    )
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp.astimezone(timezone.utc).date() != now.date():
                    continue
                calls += 1
                tokens += int(row.get("total_tokens") or 0)
    except OSError:
        return 0, 0
    return calls, tokens


def budget_block_reason(*, now: datetime | None = None) -> str | None:
    try:
        call_budget = max(0, int(os.getenv("OPENAI_DAILY_CALL_BUDGET", "0")))
    except ValueError:
        call_budget = 0
    try:
        token_budget = max(0, int(os.getenv("OPENAI_DAILY_TOKEN_BUDGET", "0")))
    except ValueError:
        token_budget = 0
    if call_budget <= 0 and token_budget <= 0:
        return None
    calls, tokens = _today_usage(now=now)
    if call_budget > 0 and calls >= call_budget:
        return f"daily OpenAI call budget reached ({calls}/{call_budget})"
    if token_budget > 0 and tokens >= token_budget:
        return f"daily OpenAI token budget reached ({tokens}/{token_budget})"
    return None
