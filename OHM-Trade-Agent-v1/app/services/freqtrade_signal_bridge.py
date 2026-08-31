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
FREQTRADE_WORKER_STARTING_BALANCE = 5_000.0


class PaperAdmissionRejected(RuntimeError):
    def __init__(self, reason: str):
        self.reason = str(reason)
        super().__init__(self.reason)


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


def build_signal_id(
    *,
    episode_id: str,
    pair: str,
    decision_at: datetime,
    direction: str = "LONG",
) -> str:
    """Return the direction-scoped signal identity for one episode and pair.

    Direction used to be hard-coded, which meant ``BTC/USD LONG`` and
    ``BTC/USD SHORT`` in the same episode produced the same signal id and
    silently collided in every downstream join.

    ``direction`` defaults to ``LONG`` and is interpolated where the literal
    used to be, so every id previously issued for a LONG signal is reproduced
    byte-for-byte and existing historical records stay resolvable.
    """
    normalized = str(direction or "LONG").strip().upper() or "LONG"
    raw = f"{episode_id}|{pair}|{_utc(decision_at).isoformat()}|{normalized}"
    return "OHM:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def ensure_bridge_files(
    *,
    signals_file: Path = SIGNALS_FILE,
    pairlist_usd_file: Path = PAIRLIST_USD_FILE,
    pairlist_usdt_file: Path = PAIRLIST_USDT_FILE,
) -> None:
    signals_file.parent.mkdir(parents=True, exist_ok=True)
    if not signals_file.exists():
        save_json_atomic(signals_file, {"schema_version": 1, "signals": []}, mode=0o644)
    if not pairlist_usd_file.exists():
        save_json_atomic(
            pairlist_usd_file,
            {"pairs": ["BTC/USD"], "refresh_period": 10, "stake_currency": "USD"},
            mode=0o644,
        )
    if not pairlist_usdt_file.exists():
        save_json_atomic(
            pairlist_usdt_file,
            {"pairs": ["BTC/USDT"], "refresh_period": 10, "stake_currency": "USDT"},
            mode=0o644,
        )
    for shared_file in (signals_file, pairlist_usd_file, pairlist_usdt_file):
        shared_file.chmod(0o644)


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
    starting_equity: float,
    max_positions: int,
    early_watch_context: dict[str, Any] | None = None,
    signals_file: Path = SIGNALS_FILE,
    pairlist_file: Path | None = None,
    authoritative_status: dict[str, Any] | None = None,
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
    if authoritative_status is None:
        try:
            from app.services.freqtrade_result_ingest import freqtrade_dry_run_status

            authoritative_status = freqtrade_dry_run_status()
        except Exception as exc:
            raise PaperAdmissionRejected(
                f"AUTHORITATIVE_CAPACITY_UNAVAILABLE:{type(exc).__name__}"
            ) from exc
    if authoritative_status.get("status") != "OK":
        raise PaperAdmissionRejected("AUTHORITATIVE_CAPACITY_UNAVAILABLE")

    active_signal_ids = {
        str(value)
        for value in (authoritative_status.get("active_signal_ids") or [])
    }
    actual_open_positions = int(authoritative_status.get("open_trades") or 0)
    workers = authoritative_status.get("workers") or {}
    worker = workers.get(quote) or {}
    try:
        actual_worker_stake = max(0.0, float(worker.get("active_stake") or 0.0))
        realized_worker_pnl = float(worker.get("realized_net_pnl") or 0.0)
    except (TypeError, ValueError):
        raise PaperAdmissionRejected("AUTHORITATIVE_CAPITAL_STATE_INVALID")

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
        "admission_status": "ADMITTED",
        "admitted_at": decision.isoformat(),
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
        existing_signal: dict[str, Any] | None = None
        active_admissions: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_time = _parse(row.get("decision_at"))
            if row_time is not None and row_time < cutoff:
                continue
            retained.append(row)
            if row.get("signal_id") == signal_id:
                existing_signal = row
                continue
            expiry = _parse(row.get("expires_at"))
            if (
                str(row.get("admission_status") or "").upper() == "ADMITTED"
                and str(row.get("signal_id") or "") not in active_signal_ids
                and expiry is not None
                and expiry >= decision
            ):
                active_admissions.append(row)

        if existing_signal is not None:
            return existing_signal

        if int(max_positions) < 1:
            raise PaperAdmissionRejected("GLOBAL_POSITION_CAPACITY_INVALID")
        if actual_open_positions + len(active_admissions) >= int(max_positions):
            raise PaperAdmissionRejected("GLOBAL_POSITION_CAPACITY")

        try:
            pending_worker_stake = sum(
                max(0.0, float(row.get("stake_amount") or 0.0))
                for row in active_admissions
                if str(row.get("quote_asset") or "").upper() == quote
            )
        except (TypeError, ValueError):
            raise PaperAdmissionRejected("GLOBAL_CAPITAL_STATE_INVALID")
        requested_stake = float(stake_amount)
        equity = float(starting_equity)
        if requested_stake <= 0 or equity <= 0:
            raise PaperAdmissionRejected("GLOBAL_CAPITAL_POLICY_INVALID")

        # The authoritative sidecar has two independent 5,000-unit dry-run
        # wallets. Never let a configurable OHM paper-equity value claim more
        # capital than the actual worker owns.
        worker_budget = min(
            max(0.0, equity / 2.0),
            FREQTRADE_WORKER_STARTING_BALANCE,
        ) + realized_worker_pnl
        reserved_worker = actual_worker_stake + pending_worker_stake
        if reserved_worker + requested_stake > worker_budget + 1e-9:
            raise PaperAdmissionRejected("GLOBAL_CAPITAL_CAPACITY")

        retained.append(signal)
        save_json_atomic(
            signals_file,
            {
                "schema_version": 1,
                "updated_at": decision.isoformat(),
                "signals": retained[-500:],
            },
            mode=0o644,
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
            mode=0o644,
        )
    return signal



def mark_signal_terminal(
    signal_id: str,
    *,
    terminal_at: datetime,
    outcome: str,
    signals_file: Path = SIGNALS_FILE,
) -> bool:
    signal_key = str(signal_id or "").strip()
    if not signal_key or not signals_file.exists():
        return False
    timestamp = _utc(terminal_at)
    lock = signals_file.parent / f".{signals_file.name}.lock"
    with registry_lock(lock):
        payload = load_json(signals_file)
        rows = payload.get("signals") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return False
        changed = False
        for row in rows:
            if not isinstance(row, dict) or row.get("signal_id") != signal_key:
                continue
            row["admission_status"] = "TERMINAL"
            row["terminal_at"] = timestamp.isoformat()
            row["terminal_outcome"] = str(outcome or "UNKNOWN")
            changed = True
            break
        if changed:
            payload["updated_at"] = timestamp.isoformat()
            payload["signals"] = rows
            save_json_atomic(signals_file, payload, mode=0o644)
        return changed


def cancel_admitted_signals(
    *,
    cancelled_at: datetime,
    reason: str = "OPERATOR_OFF",
    signals_file: Path = SIGNALS_FILE,
) -> int:
    if not signals_file.exists():
        return 0
    timestamp = _utc(cancelled_at)
    lock = signals_file.parent / f".{signals_file.name}.lock"
    with registry_lock(lock):
        payload = load_json(signals_file)
        rows = payload.get("signals") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return 0
        changed = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("admission_status") or "").upper() == "ADMITTED":
                row["admission_status"] = "CANCELLED"
                row["terminal_at"] = timestamp.isoformat()
                row["terminal_outcome"] = str(reason)
                changed += 1
        if changed:
            payload["updated_at"] = timestamp.isoformat()
            payload["signals"] = rows
            save_json_atomic(signals_file, payload, mode=0o644)
        return changed
