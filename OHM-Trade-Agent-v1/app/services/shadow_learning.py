from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any

from app.exchanges.kraken import KrakenClient
from app.services.registry_io import load_json, registry_lock, save_json_atomic


SHADOW_FILE = Path("/app/data/shadow_learning.json")
LOCK_FILE = SHADOW_FILE.parent / ".shadow_learning.lock"
SHADOW_DEDUP_SECONDS = 4 * 60 * 60
HORIZONS_SECONDS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "24h": 24 * 60 * 60,
}
OUTCOME_INTERVAL_MINUTES = 5
MAX_OUTCOME_LAG_SECONDS = OUTCOME_INTERVAL_MINUTES * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load() -> dict[str, dict[str, Any]]:
    return load_json(SHADOW_FILE)


def _save(data: dict[str, dict[str, Any]]) -> None:
    save_json_atomic(SHADOW_FILE, data)


def _key(symbol: str, direction: str, observed_at: str, decision: str) -> str:
    return f"{symbol.upper()}:{direction.upper()}:{decision.upper()}:{observed_at}"


def _dedup_identity(
    *,
    symbol: str,
    direction: str,
    decision: str,
    source: str,
    market_regime: str | None,
) -> tuple[str, str, str, str, str]:
    return (
        symbol.upper(),
        direction.upper(),
        decision.upper(),
        source,
        (market_regime or "UNKNOWN").upper(),
    )


def _existing_within_cooldown(
    data: dict[str, dict[str, Any]],
    *,
    identity: tuple[str, str, str, str, str],
    observed_at: str,
) -> dict[str, Any] | None:
    target_time = _parse(observed_at)
    newest: tuple[datetime, dict[str, Any]] | None = None
    for row in data.values():
        row_identity = _dedup_identity(
            symbol=str(row.get("symbol") or ""),
            direction=str(row.get("direction") or ""),
            decision=str(row.get("decision") or ""),
            source=str(row.get("source") or ""),
            market_regime=str(row.get("market_regime") or "UNKNOWN"),
        )
        if row_identity != identity:
            continue
        try:
            row_time = _parse(str(row["observed_at"]))
        except (KeyError, TypeError, ValueError):
            continue
        age = (target_time - row_time).total_seconds()
        if age < 0 or age >= SHADOW_DEDUP_SECONDS:
            continue
        if newest is None or row_time > newest[0]:
            newest = (row_time, row)
    return dict(newest[1]) if newest is not None else None


def _enrich_deduplicated_shadow(
    data: dict[str, dict[str, Any]],
    existing: dict[str, Any],
    *,
    target_v2_shadow: dict[str, Any] | None,
    updated_at: str,
) -> dict[str, Any]:
    """Return the original immutable decision row without hindsight backfill.

    Earlier code attached a Target-v2 feature vector computed at T+delta to a
    record whose reference price/outcomes started at T. That contaminated the
    future Target-v2 hit-rate summary. Dedup now preserves the old row exactly;
    a later scan must wait for a new cooldown bucket to create a new observation.
    """
    result = dict(existing)
    result["deduplicated"] = True
    result["dedup_cooldown_seconds"] = SHADOW_DEDUP_SECONDS
    if target_v2_shadow is not None and existing.get("target_v2_shadow") is None:
        result["dedup_enrichment_skipped"] = True
        result["dedup_enrichment_reason"] = "preserve decision-time feature/reference alignment"
    return result


def record_shadow_candidate(
    *,
    symbol: str,
    direction: str,
    decision: str,
    reference_price: float,
    market_regime: str | None = None,
    technical_score: float | None = None,
    profit_rank_score: float | None = None,
    volume_ratio: float | None = None,
    spread_bps: float | None = None,
    reason: str | None = None,
    source: str = "opportunity_scan",
    observed_at: str | None = None,
    market_intelligence: dict[str, Any] | None = None,
    price_movement: dict[str, Any] | None = None,
    target_v2_shadow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not math.isfinite(float(reference_price)) or reference_price <= 0:
        raise ValueError("reference_price must be finite and positive")
    ts = observed_at or _iso()
    key = _key(symbol, direction, ts, decision)
    identity = _dedup_identity(
        symbol=symbol,
        direction=direction,
        decision=decision,
        source=source,
        market_regime=market_regime,
    )
    with registry_lock(LOCK_FILE):
        data = _load()
        if key in data:
            return dict(data[key])
        existing = _existing_within_cooldown(data, identity=identity, observed_at=ts)
        if existing is not None:
            return _enrich_deduplicated_shadow(
                data,
                existing,
                target_v2_shadow=target_v2_shadow,
                updated_at=ts,
            )
        row = {
            "record_key": key,
            "symbol": symbol.upper(),
            "direction": direction.upper(),
            "decision": decision.upper(),
            "source": source,
            "observed_at": ts,
            "reference_price": float(reference_price),
            "market_regime": (market_regime or "UNKNOWN").upper(),
            "technical_score": technical_score,
            "profit_rank_score": profit_rank_score,
            "volume_ratio": volume_ratio,
            "spread_bps": spread_bps,
            "reason": reason or "",
            "market_intelligence": market_intelligence,
            "price_movement": price_movement,
            "target_v2_shadow": target_v2_shadow,
            "observations": {},
            "complete": False,
            "updated_at": ts,
        }
        data[key] = row
        _save(data)
        return dict(row)


def _directional_move_pct(direction: str, start: float, end: float) -> float:
    if not all(math.isfinite(value) and value > 0 for value in (start, end)):
        raise ValueError("shadow outcome prices must be finite and positive")
    if direction.upper() == "SHORT":
        return (start - end) / start * 100.0
    return (end - start) / start * 100.0


def _horizon_price(candles: list[Any], due_at: datetime) -> float | None:
    due_ts = due_at.timestamp()
    eligible = [item for item in candles if float(item.timestamp) <= due_ts]
    if not eligible:
        return None
    candle = max(eligible, key=lambda item: float(item.timestamp))
    if due_ts - float(candle.timestamp) > MAX_OUTCOME_LAG_SECONDS:
        return None
    try:
        price = float(candle.close)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def observe_due_shadows(
    *,
    client: KrakenClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Label due shadows from historical 5m OHLC, never delayed live tickers."""
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    client = client or KrakenClient()
    with registry_lock(LOCK_FILE):
        data = _load()
        due_by_symbol: dict[str, list[tuple[str, str, datetime, datetime]]] = {}
        for key, row in data.items():
            if row.get("complete"):
                continue
            try:
                observed_at = _parse(str(row["observed_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            observations = row.get("observations") or {}
            for horizon, seconds in HORIZONS_SECONDS.items():
                due_at = observed_at + timedelta(seconds=seconds)
                if horizon not in observations and now >= due_at:
                    due_by_symbol.setdefault(str(row["symbol"]), []).append(
                        (key, horizon, observed_at, due_at)
                    )

        if not due_by_symbol:
            return {"status": "NOT_DUE", "records_checked": len(data), "prices_requested": 0, "observations_added": 0}

        observations_added = 0
        unavailable = 0
        for symbol, due in due_by_symbol.items():
            earliest = min(item[2] for item in due) - timedelta(minutes=OUTCOME_INTERVAL_MINUTES)
            try:
                candles = client.get_ohlc(
                    symbol,
                    interval=OUTCOME_INTERVAL_MINUTES,
                    since=int(earliest.timestamp()),
                )
            except Exception:
                unavailable += len(due)
                continue
            for key, horizon, _observed_at, due_at in due:
                price = _horizon_price(candles, due_at)
                if price is None:
                    unavailable += 1
                    continue
                row = data[key]
                try:
                    start = float(row["reference_price"])
                    move = _directional_move_pct(str(row["direction"]), start, price)
                except (KeyError, TypeError, ValueError):
                    unavailable += 1
                    continue
                row.setdefault("observations", {})[horizon] = {
                    "observed_at": due_at.isoformat(),
                    "labelled_at": now.isoformat(),
                    "price": price,
                    "directional_move_pct": round(move, 6),
                }
                row["updated_at"] = now.isoformat()
                observations_added += 1
                if all(h in row["observations"] for h in HORIZONS_SECONDS):
                    row["complete"] = True
        _save(data)
        return {
            "status": "OK",
            "records_checked": len(data),
            "prices_requested": len(due_by_symbol),
            "observations_added": observations_added,
            "unavailable_horizons": unavailable,
        }
