from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import KrakenClient, KrakenAPIError
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
    """Backfill new non-authoritative evidence onto a deduplicated shadow row.

    Deduplication must preserve the original decision timestamp, reference price,
    and observations. It may add newly introduced learning-only metadata so a
    software rollout does not create a multi-hour measurement blind spot.
    """
    record_key = str(existing.get("record_key") or "")
    stored = data.get(record_key)
    enriched_fields: list[str] = []
    if stored is not None and target_v2_shadow is not None and stored.get("target_v2_shadow") is None:
        stored["target_v2_shadow"] = target_v2_shadow
        stored["updated_at"] = updated_at
        enriched_fields.append("target_v2_shadow")
        _save(data)
        existing = dict(stored)
    existing["deduplicated"] = True
    existing["dedup_cooldown_seconds"] = SHADOW_DEDUP_SECONDS
    if enriched_fields:
        existing["dedup_enriched_fields"] = enriched_fields
    return existing


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
    if reference_price <= 0:
        raise ValueError("reference_price must be positive")
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
    if direction.upper() == "SHORT":
        return (start / end - 1.0) * 100.0
    return (end / start - 1.0) * 100.0


def observe_due_shadows(
    *,
    client: KrakenClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    client = client or KrakenClient()
    with registry_lock(LOCK_FILE):
        data = _load()
        due_by_symbol: dict[str, list[tuple[str, str]]] = {}
        for key, row in data.items():
            if row.get("complete"):
                continue
            try:
                age = (now - _parse(str(row["observed_at"]))).total_seconds()
            except (KeyError, TypeError, ValueError):
                continue
            observations = row.get("observations") or {}
            for horizon, seconds in HORIZONS_SECONDS.items():
                if horizon not in observations and age >= seconds:
                    due_by_symbol.setdefault(str(row["symbol"]), []).append((key, horizon))

        if not due_by_symbol:
            return {"status": "NOT_DUE", "records_checked": len(data), "prices_requested": 0, "observations_added": 0}

        try:
            tickers = client.get_tickers(sorted(due_by_symbol))
        except KrakenAPIError as exc:
            return {"status": "ERROR", "reason": str(exc), "records_checked": len(data), "prices_requested": len(due_by_symbol), "observations_added": 0}

        observations_added = 0
        for symbol, due in due_by_symbol.items():
            ticker = tickers.get(symbol)
            if ticker is None:
                try:
                    ticker = client.get_ticker(symbol)
                except KrakenAPIError:
                    continue
            price = float(ticker["last"])
            for key, horizon in due:
                row = data[key]
                start = float(row["reference_price"])
                move = _directional_move_pct(str(row["direction"]), start, price)
                row.setdefault("observations", {})[horizon] = {
                    "observed_at": _iso(now),
                    "price": price,
                    "directional_move_pct": round(move, 6),
                }
                row["updated_at"] = _iso(now)
                observations_added += 1
                if all(h in row["observations"] for h in HORIZONS_SECONDS):
                    row["complete"] = True
        _save(data)
        return {
            "status": "OK",
            "records_checked": len(data),
            "prices_requested": len(due_by_symbol),
            "observations_added": observations_added,
        }


def get_shadow_records() -> list[dict[str, Any]]:
    with registry_lock(LOCK_FILE):
        return [dict(row) for row in _load().values()]
