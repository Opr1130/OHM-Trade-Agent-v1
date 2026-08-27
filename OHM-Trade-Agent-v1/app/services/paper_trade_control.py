from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.registry_io import (
    RegistryIOError,
    load_json,
    registry_lock,
    save_json_atomic,
)


CONTROL_FILE = Path("/app/data/paper_trading/control.json")
LOCK_FILE = CONTROL_FILE.parent / ".paper_control.lock"


@dataclass(frozen=True)
class PaperTradeControl:
    enabled: bool
    updated_at: str | None
    updated_by: str
    status: str = "OK"

    def as_dict(self) -> dict:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_file(path: Path) -> Path:
    return path.parent / f".{path.name}.lock" if path != CONTROL_FILE else LOCK_FILE


def get_paper_trade_control(path: Path = CONTROL_FILE) -> PaperTradeControl:
    """Read operator state; missing/unreadable state never creates exposure."""
    if not path.exists():
        return PaperTradeControl(
            enabled=False,
            updated_at=None,
            updated_by="SYSTEM_DEFAULT",
            status="DEFAULT_OFF",
        )
    try:
        with registry_lock(_lock_file(path)):
            payload = load_json(path)
    except (OSError, TimeoutError, RegistryIOError):
        return PaperTradeControl(
            enabled=False,
            updated_at=None,
            updated_by="SYSTEM_FAIL_SAFE",
            status="UNAVAILABLE",
        )

    return PaperTradeControl(
        enabled=bool(payload.get("enabled", False)),
        updated_at=(
            str(payload.get("updated_at"))
            if payload.get("updated_at")
            else None
        ),
        updated_by=str(payload.get("updated_by") or "UNKNOWN"),
        status="OK",
    )


def paper_trade_enabled(path: Path = CONTROL_FILE) -> bool:
    return get_paper_trade_control(path).enabled


def set_paper_trade_enabled(
    enabled: bool,
    *,
    updated_by: str = "CLI_OPERATOR",
    now: datetime | None = None,
    path: Path = CONTROL_FILE,
) -> PaperTradeControl:
    """Persist one explicit on/off operator decision atomically."""
    timestamp = now or _now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("paper control timestamp must be timezone-aware")
    timestamp = timestamp.astimezone(timezone.utc)
    payload = {
        "schema_version": 1,
        "enabled": bool(enabled),
        "updated_at": timestamp.isoformat(),
        "updated_by": str(updated_by or "CLI_OPERATOR"),
        "paper_only": True,
        "kraken_execution_authority": False,
    }
    with registry_lock(_lock_file(path)):
        save_json_atomic(path, payload)
    try:
        from app.services.freqtrade_signal_bridge import mirror_control
        mirror_control(
            enabled=bool(enabled),
            updated_at=timestamp,
            updated_by=payload["updated_by"],
        )
    except Exception:
        # Paper-control durability is authoritative. Bridge mirroring is
        # fail-soft and can never alter production trading authority.
        pass
    return PaperTradeControl(
        enabled=bool(enabled),
        updated_at=timestamp.isoformat(),
        updated_by=payload["updated_by"],
        status="OK",
    )
