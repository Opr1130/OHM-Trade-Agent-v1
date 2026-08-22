from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import KrakenAPIError, KrakenClient
from app.services.price_movement_radar import EXPIRED
from app.services.registry_io import load_json, registry_lock, save_json_atomic


MOVEMENT_FILE = Path("/app/data/price_movement_learning.json")
LOCK_FILE = MOVEMENT_FILE.parent / ".price_movement_learning.lock"
DEDUP_SECONDS = 4 * 60 * 60
HORIZONS_SECONDS = {"1h": 60 * 60, "4h": 4 * 60 * 60, "12h": 12 * 60 * 60}
OBSERVATION_INTERVAL_MINUTES = 15
MAX_HORIZON_PRICE_LAG_SECONDS = OBSERVATION_INTERVAL_MINUTES * 60
NEAR_DUE_TICKER_FALLBACK_SECONDS = 5 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load() -> dict[str, Any]:
    state = load_json(MOVEMENT_FILE)
    if not isinstance(state.get("records"), dict):
        state["records"] = {}
    if not isinstance(state.get("latest"), dict):
        state["latest"] = {}
    return state


def _record_key(signal: dict[str, Any]) -> str:
    return ":".join((str(signal.get("symbol") or "UNKNOWN").upper(), str(signal.get("stage") or "UNKNOWN").upper(), str(signal.get("direction") or "UNCONFIRMED").upper(), str(signal.get("observed_at") or _now().isoformat())))


def _deduplicated_record(records: dict[str, dict[str, Any]], signal: dict[str, Any]) -> dict[str, Any] | None:
    try:
        observed_at = _parse(signal["observed_at"])
    except (KeyError, TypeError, ValueError):
        return None
    identity = (str(signal.get("symbol") or "").upper(), str(signal.get("stage") or "").upper(), str(signal.get("direction") or "").upper(), str(signal.get("source") or ""))
    newest: tuple[datetime, dict[str, Any]] | None = None
    for row in records.values():
        row_identity = (str(row.get("symbol") or "").upper(), str(row.get("stage") or "").upper(), str(row.get("direction") or "").upper(), str(row.get("source") or ""))
        if row_identity != identity:
            continue
        try:
            row_time = _parse(row.get("observed_at"))
        except (TypeError, ValueError):
            continue
        age = (observed_at - row_time).total_seconds()
        if 0 <= age < DEDUP_SECONDS and (newest is None or row_time > newest[0]):
            newest = (row_time, row)
    return dict(newest[1]) if newest else None


def record_price_movement(signal: dict[str, Any]) -> dict[str, Any]:
    try:
        reference_price = float(signal.get("reference_price") or 0.0)
        reference_atr = float(signal.get("reference_atr") or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("movement reference values must be numeric") from exc
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError("movement reference_price must be finite and positive")
    if not math.isfinite(reference_atr) or reference_atr <= 0:
        raise ValueError("movement reference_atr must be finite and positive")

    with registry_lock(LOCK_FILE):
        state = _load()
        records: dict[str, dict[str, Any]] = state["records"]
        duplicate = _deduplicated_record(records, signal)
        if duplicate is not None:
            duplicate["deduplicated"] = True
            return duplicate
        key = _record_key(signal)
        row = dict(signal)
        row["record_key"] = key
        row["observations"] = {}
        row["complete"] = str(signal.get("stage") or "").upper() == EXPIRED
        row["updated_at"] = str(signal.get("observed_at") or _now().isoformat())
        records[key] = row
        state["latest"][str(signal.get("symbol") or "").upper()] = key
        save_json_atomic(MOVEMENT_FILE, state)
        return dict(row)


def get_latest_price_movement(symbol: str) -> dict[str, Any] | None:
    try:
        with registry_lock(LOCK_FILE):
            state = _load()
            key = state["latest"].get(symbol.upper())
            row = state["records"].get(key) if key else None
            return dict(row) if isinstance(row, dict) else None
    except OSError:
        return None


def get_price_movement_records() -> list[dict[str, Any]]:
    try:
        with registry_lock(LOCK_FILE):
            return [dict(row) for row in _load()["records"].values()]
    except OSError:
        return []


def _ticker_price(client: KrakenClient, symbol: str) -> float | None:
    try:
        ticker = client.get_ticker(symbol)
    except Exception:
        try:
            ticker = client.get_tickers([symbol]).get(symbol)
        except Exception:
            return None
    if not isinstance(ticker, dict):
        return None
    try:
        price = float(ticker.get("last") or 0.0)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _price_at_horizon(
    client: KrakenClient,
    symbol: str,
    observed_at: datetime,
    due_at: datetime,
    *,
    labelled_at: datetime,
) -> float | None:
    """Return a price representative of the declared horizon without smearing.

    Historical 15m OHLC is authoritative. A current ticker is used only when
    the observer is running within five minutes of the due timestamp and the
    client cannot provide historical OHLC; this preserves legacy/lightweight
    clients without allowing a delayed run to relabel a later price as 1h/4h.
    """
    try:
        candles = client.get_ohlc(
            symbol,
            interval=OBSERVATION_INTERVAL_MINUTES,
            since=int((observed_at - timedelta(minutes=OBSERVATION_INTERVAL_MINUTES)).timestamp()),
        )
    except Exception:
        lag = (labelled_at - due_at).total_seconds()
        if 0 <= lag <= NEAR_DUE_TICKER_FALLBACK_SECONDS:
            return _ticker_price(client, symbol)
        return None

    due_ts = due_at.timestamp()
    # Kraken timestamps candles by bar open. Only candles whose open is at or
    # before the horizon are candidates for the point-in-time close; the lag
    # guard below prevents a stale candle from masquerading as the horizon.
    eligible = [candle for candle in candles if float(candle.timestamp) <= due_ts]
    if not eligible:
        return None
    candle = max(eligible, key=lambda item: float(item.timestamp))
    if due_ts - float(candle.timestamp) > MAX_HORIZON_PRICE_LAG_SECONDS:
        return None
    try:
        price = float(candle.close)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def observe_due_price_movements(*, client: KrakenClient | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Measure magnitude accuracy at exact 1h/4h/12h horizons using Kraken OHLC."""
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    client = client or KrakenClient()
    with registry_lock(LOCK_FILE):
        state = _load()
        records: dict[str, dict[str, Any]] = state["records"]
        due_by_symbol: dict[str, list[tuple[str, str, datetime, datetime]]] = {}
        for key, row in records.items():
            if row.get("complete"):
                continue
            try:
                observed_at = _parse(row["observed_at"])
            except (KeyError, TypeError, ValueError):
                continue
            observations = row.get("observations") or {}
            for horizon, seconds in HORIZONS_SECONDS.items():
                due_at = observed_at + timedelta(seconds=seconds)
                if horizon not in observations and now >= due_at:
                    due_by_symbol.setdefault(str(row.get("symbol") or ""), []).append((key, horizon, observed_at, due_at))

        if not due_by_symbol:
            return {"status": "NOT_DUE", "records_checked": len(records), "prices_requested": 0, "observations_added": 0}

        added = 0
        for symbol, due in due_by_symbol.items():
            for key, horizon, observed_at, due_at in due:
                price = _price_at_horizon(client, symbol, observed_at, due_at, labelled_at=now)
                if price is None:
                    continue
                row = records[key]
                try:
                    reference = float(row["reference_price"])
                    reference_atr = float(row["reference_atr"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not all(math.isfinite(value) and value > 0 for value in (reference, reference_atr)):
                    continue
                raw_move_pct = (price - reference) / reference * 100.0
                absolute_move_pct = abs(raw_move_pct)
                move_atr = abs(price - reference) / reference_atr
                direction = str(row.get("direction") or "UNCONFIRMED").upper()
                directional_move_pct: float | None = None
                if direction == "LONG":
                    directional_move_pct = raw_move_pct
                elif direction == "SHORT":
                    directional_move_pct = (reference - price) / reference * 100.0
                row.setdefault("observations", {})[horizon] = {
                    "observed_at": due_at.isoformat(),
                    "labelled_at": now.isoformat(),
                    "price": price,
                    "absolute_move_pct": round(absolute_move_pct, 6),
                    "move_atr": round(move_atr, 6),
                    "directional_move_pct": round(directional_move_pct, 6) if directional_move_pct is not None else None,
                    "met_expected_low_atr": move_atr >= float(row.get("expected_move_low_atr") or 0.0),
                }
                row["updated_at"] = now.isoformat()
                added += 1
                if all(item in row["observations"] for item in HORIZONS_SECONDS):
                    row["complete"] = True
        save_json_atomic(MOVEMENT_FILE, state)
        return {"status": "OK", "records_checked": len(records), "prices_requested": len(due_by_symbol), "observations_added": added}
