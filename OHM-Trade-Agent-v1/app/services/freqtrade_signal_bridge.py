from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any

from app.exchanges.kraken_identity import canonicalize_asset
from app.services.registry_io import load_json, registry_lock, save_json_atomic


BRIDGE_DIR = Path("/app/data/freqtrade_bridge")
SIGNALS_FILE = BRIDGE_DIR / "signals.json"
PAIRLIST_USD_FILE = BRIDGE_DIR / "pairlist_usd.json"
PAIRLIST_USDT_FILE = BRIDGE_DIR / "pairlist_usdt.json"
# Backward-compatible alias for tests/importers that assume the primary USD path.
PAIRLIST_FILE = PAIRLIST_USD_FILE
SIGNAL_RETENTION = timedelta(days=7)


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Freqtrade bridge timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_freqtrade_pair(base_asset: str, quote_asset: str = "USD") -> str:
    base = canonicalize_asset(str(base_asset or "").strip().upper())
    quote = canonicalize_asset(str(quote_asset or "USD").strip().upper())
    if not base or quote not in {"USD", "USDT"}:
        raise ValueError("Freqtrade paper bridge supports USD/USDT spot pairs only")
    return f"{base}/{quote}"


def build_signal_id(*, episode_id: str, pair: str, decision_at: datetime) -> str:
    raw = f"{episode_id}|{pair}|{_utc(decision_at).isoformat()}|LONG"
    return "OHM:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def ensure_bridge_files(
    *,
    signals_file: Path = SIGNALS_FILE,
    pairlist_usd_file: Path = PAIRLIST_USD_FILE,
    pairlist_usdt_file: Path = PAIRLIST_USDT_FILE,
) -> None:
    signals_file.parent.mkdir(parents=True, exist_ok=True)
    if not signals_file.exists():
        save_json_atomic(signals_file, {"schema_version": 1, "signals": []})
    if not pairlist_usd_file.exists():
        save_json_atomic(
            pairlist_usd_file,
            {"pairs": ["BTC/USD"], "refresh_period": 10, "stake_currency": "USD"},
        )
    if not pairlist_usdt_file.exists():
        save_json_atomic(
            pairlist_usdt_file,
            {"pairs": ["BTC/USDT"], "refresh_period": 10, "stake_currency": "USDT"},
        )


def publish_qualified_long(
    *,
    episode_id: str,
    cohort_id: str,
    journey_id: str,
    ohm_symbol: str,
    base_asset: str,
    quote_asset: str,
    decision_at: datetime,
    valid_now: bool,
    entry_style: str,
    entry_low: float,
    entry_high: float,
    chase_limit: float,
    stop_price: float,
    target_1: float,
    target_2: float,
    stake_amount: float,
    max_hold_hours: int,
    pending_ttl_hours: int,
    confidence: int,
    profit_rank: int | None,
    profit_rank_score: float | None,
    early_watch_context: dict[str, Any] | None = None,
    signals_file: Path = SIGNALS_FILE,
    pairlist_file: Path | None = None,
) -> dict[str, Any]:
    decision = _utc(decision_at)
    quote = canonicalize_asset(str(quote_asset or "USD").strip().upper())
    pair = to_freqtrade_pair(base_asset, quote)
    signal_id = build_signal_id(
        episode_id=episode_id,
        pair=pair,
        decision_at=decision,
    )
    ttl_hours = max_hold_hours if valid_now else pending_ttl_hours
    expires = decision + timedelta(hours=max(1, int(ttl_hours)))
    signal = {
        "schema_version": 1,
        "signal_id": signal_id,
        "episode_id": str(episode_id),
        "cohort_id": str(cohort_id),
        "journey_id": str(journey_id),
        "pair": pair,
        "ohm_symbol": str(ohm_symbol).upper(),
        "base_asset": canonicalize_asset(str(base_asset).upper()),
        "quote_asset": quote,
        "direction": "LONG",
        "decision_at": decision.isoformat(),
        "expires_at": expires.isoformat(),
        "valid_now": bool(valid_now),
        "entry_style": str(entry_style),
        "entry_low": float(entry_low),
        "entry_high": float(entry_high),
        "entry_price": float(entry_high if valid_now else entry_low),
        "chase_limit": float(chase_limit),
        "stop_price": float(stop_price),
        "target_1": float(target_1),
        "target_2": float(target_2),
        "stake_amount": float(stake_amount),
        "max_hold_hours": int(max_hold_hours),
        "pending_ttl_hours": int(pending_ttl_hours),
        "confidence": int(confidence),
        "profit_rank": profit_rank,
        "profit_rank_score": profit_rank_score,
        "early_watch_context": dict(early_watch_context or {}),
        "population": "FREQTRADE_DRY_RUN_V1",
        "exchange_write_authority": False,
    }

    signals_file.parent.mkdir(parents=True, exist_ok=True)
    lock = signals_file.parent / f".{signals_file.name}.lock"
    with registry_lock(lock):
        payload = load_json(signals_file)
        rows = payload.get("signals") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            rows = []
        cutoff = decision - SIGNAL_RETENTION
        retained: list[dict[str, Any]] = []
        replaced = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_time = _parse(row.get("decision_at"))
            if row_time is not None and row_time < cutoff:
                continue
            if row.get("signal_id") == signal_id:
                retained.append(signal)
                replaced = True
            else:
                retained.append(row)
        if not replaced:
            retained.append(signal)
        save_json_atomic(
            signals_file,
            {
                "schema_version": 1,
                "updated_at": decision.isoformat(),
                "signals": retained[-500:],
            },
        )

    target_pairlist = pairlist_file or (
        PAIRLIST_USDT_FILE if quote == "USDT" else PAIRLIST_USD_FILE
    )
    pair_lock = target_pairlist.parent / f".{target_pairlist.name}.lock"
    with registry_lock(pair_lock):
        pairs = {pair}
        payload = load_json(signals_file)
        suffix = f"/{quote}"
        for row in payload.get("signals", []):
            if not isinstance(row, dict):
                continue
            expiry = _parse(row.get("expires_at"))
            if expiry is not None and expiry >= decision:
                value = str(row.get("pair") or "")
                if value.endswith(suffix):
                    pairs.add(value)
        save_json_atomic(
            target_pairlist,
            {
                "pairs": sorted(pairs),
                "refresh_period": 10,
                "updated_at": decision.isoformat(),
                "stake_currency": quote,
            },
        )
    return signal
