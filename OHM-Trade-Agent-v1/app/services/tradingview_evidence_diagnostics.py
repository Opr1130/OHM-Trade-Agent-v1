from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.services.registry_io import load_json, registry_lock
from app.services.tradingview_inbox import INBOX_FILE, LOCK_FILE, resolve_kraken_pair


def _now() -> datetime:
    return datetime.now(timezone.utc)


def analyze_tradingview_registry(
    registry: dict[str, Any],
    *,
    now: datetime,
    freshness_seconds: int,
) -> dict[str, Any]:
    processed_qualified = []
    recent = []
    stale = []
    pairs: set[str] = set()
    directions: dict[str, set[str]] = {}
    for row in (registry.get("events") or {}).values():
        if row.get("status") != "PROCESSED" or row.get("final_disposition") != "QUALIFIED_FOR_NATIVE_CYCLE":
            continue
        processed_qualified.append(row)
        payload = row.get("payload") or {}
        try:
            pair = resolve_kraken_pair(str(payload.get("symbol") or ""))
            direction = str(payload.get("direction") or "").upper()
            received = datetime.fromisoformat(str(row.get("received_at") or "").replace("Z", "+00:00"))
            age = (now - received).total_seconds()
        except (TypeError, ValueError):
            continue
        pairs.add(pair)
        directions.setdefault(pair, set()).add(direction)
        item = {"pair": pair, "direction": direction, "age_seconds": round(age, 1)}
        if 0 <= age <= freshness_seconds:
            recent.append(item)
        else:
            stale.append(item)
    recent.sort(key=lambda item: item["age_seconds"])
    stale.sort(key=lambda item: item["age_seconds"])
    return {
        "processed_qualified_total": len(processed_qualified),
        "recent_qualified": len(recent),
        "stale_or_future_qualified": len(stale),
        "qualified_pairs": sorted(pairs),
        "qualified_directions": {pair: sorted(values) for pair, values in sorted(directions.items())},
        "recent_evidence": recent,
        "freshness_seconds": freshness_seconds,
    }


def load_tradingview_evidence_diagnostics(now: datetime | None = None) -> dict[str, Any]:
    settings = get_settings()
    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or {"events": {}, "metrics": {}}
    result = analyze_tradingview_registry(
        registry,
        now=now or _now(),
        freshness_seconds=int(settings.tradingview_dedup_window_seconds),
    )
    metrics = registry.get("metrics") or {}
    result.update(
        {
            "accepted_webhooks": int(metrics.get("accepted_webhooks", 0)),
            "queue_depth": sum(
                row.get("status") in {"QUEUED", "RETRY"}
                for row in (registry.get("events") or {}).values()
            ),
            "bridge_enabled": bool(settings.tradingview_v2_enabled),
        }
    )
    if not settings.tradingview_v2_enabled:
        reason = "BRIDGE_DISABLED"
    elif result["processed_qualified_total"] == 0:
        reason = "NO_QUALIFIED_TRADINGVIEW_EVENTS"
    elif result["recent_qualified"] == 0:
        reason = "NO_RECENT_QUALIFIED_TRADINGVIEW_EVENTS"
    else:
        reason = "RECENT_EVIDENCE_AVAILABLE"
    result["coverage_status"] = reason
    return result
