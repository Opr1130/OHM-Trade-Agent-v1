from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import KrakenClient, KrakenAPIError
from app.services.registry_io import load_json, registry_lock, save_json_atomic


SHADOW_FILE = Path("/app/data/shadow_learning.json")
LOCK_FILE = SHADOW_FILE.parent / ".shadow_learning.lock"
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
) -> dict[str, Any]:
    if reference_price <= 0:
        raise ValueError("reference_price must be positive")
    ts = observed_at or _iso()
    key = _key(symbol, direction, ts, decision)
    with registry_lock(LOCK_FILE):
        data = _load()
        if key in data:
            return dict(data[key])
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

        # Kraken may return canonical pair names different from requested names.
        # If batch-key matching is ambiguous, fall back to one ticker call for that symbol.
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
