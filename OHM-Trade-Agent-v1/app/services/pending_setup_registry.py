from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

from app.services.candidate_trace import trace_candidate_event
from app.services.registry_io import load_json, registry_lock, save_json_atomic


PENDING_FILE = Path("/app/data/pending_setups.json")


@dataclass
class PendingSetup:
    symbol: str
    entry_low: float
    entry_high: float
    chase_limit: float
    stop_price: float
    target_1: float
    target_2: float
    risk_level: str
    confidence: int
    status: str = "waiting"
    created_at: str = ""
    trade_id: str = ""
    confirmation_price: float | None = None
    direction: str = "LONG"
    margin_leverage: float = 1.0
    expires_at: str = ""


def _load_raw() -> dict:
    return load_json(PENDING_FILE)


def _save_raw(data: dict) -> None:
    save_json_atomic(PENDING_FILE, data)


def registry_lock_file() -> Path:
    return PENDING_FILE.parent / ".trade_registry.lock"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ttl_hours() -> int:
    try:
        return max(1, min(168, int(os.getenv("PENDING_SETUP_TTL_HOURS", "24"))))
    except ValueError:
        return 24


def add_pending_setup(setup: PendingSetup) -> PendingSetup:
    setup.direction = (setup.direction or "LONG").upper()
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        if not setup.created_at:
            setup.created_at = datetime.now(timezone.utc).isoformat()
        if not setup.trade_id:
            setup.trade_id = f"OHM-{setup.symbol.upper()}-{uuid4().hex[:12]}"
        if not setup.expires_at:
            created = _parse(setup.created_at) or datetime.now(timezone.utc)
            setup.expires_at = (created + timedelta(hours=_ttl_hours())).isoformat()
        # Preserve historical symbol-keyed storage for compatibility. trade_id
        # is the canonical lifecycle identity used by terminal transitions.
        data[setup.symbol] = asdict(setup)
        _save_raw(data)
    return setup


def _from_item(item: dict) -> PendingSetup:
    normalized = dict(item)
    normalized.setdefault("direction", "LONG")
    normalized.setdefault("margin_leverage", 1.0)
    normalized.setdefault("expires_at", "")
    return PendingSetup(**normalized)


def get_pending_setups() -> list[PendingSetup]:
    with registry_lock(registry_lock_file()):
        data = _load_raw()
    return [_from_item(item) for item in data.values() if item.get("status") == "waiting"]


def get_pending_setup_by_trade_id(trade_id: str) -> PendingSetup | None:
    return next((setup for setup in get_pending_setups() if setup.trade_id == trade_id), None)


def terminalize_pending_setup(trade_id: str, status: str) -> bool:
    if status not in {
        "skipped", "invalidated", "too_extended", "send_failed",
        "portfolio_risk_rejected", "tracking_failed", "expired",
    }:
        raise ValueError(f"Unsupported terminal status: {status}")
    try:
        from app.services.order_intent_registry import cancel_order_intent, get_order_intent

        intent = get_order_intent(trade_id)
        if intent is not None and intent.status == "FILLED":
            mark_pending_setup_entered(trade_id)
            return False
        if intent is not None and intent.status == "LIMIT_PLACED":
            cancel_order_intent(trade_id)
    except Exception:
        return False

    terminalized = False
    traced_symbol = ""
    traced_direction = "LONG"
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        for item in data.values():
            if item.get("trade_id") == trade_id and item.get("status") == "waiting":
                item["status"] = status
                item["terminal_at"] = datetime.now(timezone.utc).isoformat()
                traced_symbol = str(item.get("symbol") or "")
                traced_direction = str(item.get("direction") or "LONG")
                _save_raw(data)
                terminalized = True
                break
    if terminalized:
        from app.services.trade_outcome_registry import terminalize_setup_outcome

        terminalize_setup_outcome(trade_id, status)
        trace_candidate_event(
            symbol=traced_symbol or "UNKNOWN",
            direction=traced_direction,
            stage="SETUP_LIFECYCLE",
            reason_code=status.upper(),
            details={"trade_id": trade_id},
        )
        if status in {"tracking_failed", "send_failed", "portfolio_risk_rejected"}:
            print(
                f"ALERT SUPPRESSED Symbol={traced_symbol or 'UNKNOWN'} "
                f"Reason={status.upper()} TradeId={trade_id}"
            )
    return terminalized


def expire_due_pending_setups(*, now: datetime | None = None) -> int:
    """Expire stale waiting setups by trade_id; exchange truth still wins."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    expired = 0
    for setup in get_pending_setups():
        expiry = _parse(setup.expires_at)
        if expiry is None:
            created = _parse(setup.created_at)
            if created is None:
                continue
            expiry = created + timedelta(hours=_ttl_hours())
        if expiry <= now.astimezone(timezone.utc):
            if terminalize_pending_setup(setup.trade_id, "expired"):
                expired += 1
    return expired


def mark_pending_setup_entered(trade_id: str) -> bool:
    """Retire a pending setup after Kraken proves that its order filled."""
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        for item in data.values():
            if item.get("trade_id") == trade_id and item.get("status") == "waiting":
                item["status"] = "entered"
                item["terminal_at"] = datetime.now(timezone.utc).isoformat()
                _save_raw(data)
                return True
    return False


def remove_pending_setup(symbol: str) -> bool:
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        if symbol not in data:
            return False
        del data[symbol]
        _save_raw(data)
        return True
