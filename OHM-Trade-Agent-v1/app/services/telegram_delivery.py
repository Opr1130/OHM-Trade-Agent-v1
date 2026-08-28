from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from app.services.asset_display_identity import resolve_asset_identity
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_notifier import edit_telegram_message, send_telegram_message_with_id


EVENT_FILE = Path("/app/data/telegram_delivery_events.jsonl")
STATE_FILE = Path("/app/data/telegram_delivery_state.json")
LOCK_FILE = STATE_FILE.parent / ".telegram_delivery.lock"
MAX_SUMMARY_EVENTS = 20_000


@dataclass(frozen=True)
class TelegramDeliveryResult:
    status: str
    delivered: bool
    message_id: int | None
    alert_id: str
    attempt: int
    retry_count: int
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "delivered": self.delivered,
            "message_id": self.message_id,
            "alert_id": self.alert_id,
            "attempt": self.attempt,
            "retry_count": self.retry_count,
            "latency_ms": self.latency_ms,
        }


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Telegram delivery timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _alert_id(*, identity: str, alert_family: str, event_type: str, fingerprint: str) -> str:
    raw = "|".join(
        (
            str(identity or "").strip().upper(),
            str(alert_family or "").strip().upper(),
            str(event_type or "").strip().upper(),
            str(fingerprint or "").strip(),
        )
    )
    return "TGALERT:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _safe_message_id(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _append_event_locked(row: dict[str, Any], *, event_file: Path) -> None:
    event_file.parent.mkdir(parents=True, exist_ok=True)
    with event_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str) + "\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass


def _record(
    *,
    identity: str,
    alert_family: str,
    event_type: str,
    fingerprint: str,
    status: str,
    symbol: str | None,
    pair: str | None,
    message_id: int | None,
    journey_id: str | None,
    trade_id: str | None,
    suppression_reason: str | None,
    failure_reason: str | None,
    generated_at: datetime | None,
    attempted_at: datetime | None,
    latency_ms: int,
    state_file: Path,
    event_file: Path,
) -> TelegramDeliveryResult:
    generated = _utc(generated_at)
    attempted = _utc(attempted_at)
    normalized_identity = str(identity or "").strip()
    family = str(alert_family or "UNKNOWN").strip().upper()
    event = str(event_type or "UNKNOWN").strip().upper()
    fingerprint = str(fingerprint or "").strip()
    alert_id = _alert_id(
        identity=normalized_identity,
        alert_family=family,
        event_type=event,
        fingerprint=fingerprint,
    )
    normalized_status = str(status or "UNKNOWN").strip().upper()
    delivered = normalized_status in {"DELIVERED", "EDITED", "TRANSITION_PUSHED"}
    is_attempt = normalized_status not in {"SUPPRESSED", "NOT_ELIGIBLE"}

    identity_info = resolve_asset_identity(symbol=symbol, pair=pair or symbol)
    market_pair = identity_info.pair or str(pair or symbol or "").upper()
    asset_symbol = identity_info.base_asset or str(symbol or "").upper()

    attempt = 0
    retry_count = 0
    try:
        lock_file = state_file.parent / f".{state_file.name}.delivery.lock"
        with registry_lock(lock_file):
            state = load_json(state_file)
            alerts = state.get("alerts")
            identities = state.get("identities")
            if not isinstance(alerts, dict):
                alerts = {}
            if not isinstance(identities, dict):
                identities = {}
            previous = alerts.get(alert_id)
            if not isinstance(previous, dict):
                previous = {}
            previous_attempts = int(previous.get("attempts") or 0)
            attempt = previous_attempts + 1 if is_attempt else previous_attempts
            retry_count = max(0, attempt - 1) if is_attempt else max(0, previous_attempts - 1)

            row = {
                "schema_version": 1,
                "record_type": "TELEGRAM_DELIVERY_EVENT",
                "alert_id": alert_id,
                "identity": normalized_identity,
                "alert_family": family,
                "event_type": event,
                "fingerprint": fingerprint,
                "symbol": asset_symbol or None,
                "pair": market_pair or None,
                "display_label": identity_info.label,
                "journey_id": str(journey_id or "") or None,
                "trade_id": str(trade_id or "") or None,
                "generated_at": generated.isoformat(),
                "attempted_at": attempted.isoformat(),
                "status": normalized_status,
                "delivered": delivered,
                "message_id": _safe_message_id(message_id),
                "attempt": attempt,
                "retry_count": retry_count,
                "latency_ms": max(0, int(latency_ms)),
                "suppression_reason": str(suppression_reason or "") or None,
                "failure_reason": str(failure_reason or "") or None,
            }
            _append_event_locked(row, event_file=event_file)

            updated = dict(previous)
            updated.update(
                {
                    "identity": normalized_identity,
                    "alert_family": family,
                    "event_type": event,
                    "fingerprint": fingerprint,
                    "attempts": attempt,
                    "retry_count": retry_count,
                    "last_status": normalized_status,
                    "updated_at": attempted.isoformat(),
                }
            )
            if delivered and _safe_message_id(message_id) is not None:
                updated["message_id"] = int(message_id)
                updated["last_delivered_at"] = attempted.isoformat()
                updated["consecutive_failures"] = 0
                identities[normalized_identity] = {
                    "alert_id": alert_id,
                    "message_id": int(message_id),
                    "fingerprint": fingerprint,
                    "event_type": event,
                    "updated_at": attempted.isoformat(),
                }
            elif is_attempt and not delivered:
                updated["consecutive_failures"] = int(previous.get("consecutive_failures") or 0) + 1
                updated["last_failure_reason"] = str(failure_reason or normalized_status)
            alerts[alert_id] = updated
            state["schema_version"] = 1
            state["alerts"] = alerts
            state["identities"] = identities
            save_json_atomic(state_file, state)
    except (OSError, TimeoutError, RegistryIOError, ValueError, TypeError):
        # Delivery telemetry is observational. It must never turn a successful
        # Telegram send into a failed trading/monitoring path.
        pass

    return TelegramDeliveryResult(
        status=normalized_status,
        delivered=delivered,
        message_id=_safe_message_id(message_id),
        alert_id=alert_id,
        attempt=attempt,
        retry_count=retry_count,
        latency_ms=max(0, int(latency_ms)),
    )


def send_tracked_telegram(
    *,
    bot_token: str,
    chat_id: str,
    message: str,
    identity: str,
    alert_family: str,
    event_type: str,
    fingerprint: str,
    symbol: str | None = None,
    pair: str | None = None,
    journey_id: str | None = None,
    trade_id: str | None = None,
    generated_at: datetime | None = None,
    success_status: str = "DELIVERED",
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> TelegramDeliveryResult:
    generated = _utc(generated_at)
    attempted = _utc()
    started = time.monotonic()
    message_id: int | None = None
    failure_reason: str | None = None
    try:
        message_id = send_telegram_message_with_id(bot_token, chat_id, message)
    except Exception as exc:
        failure_reason = type(exc).__name__
    latency_ms = round((time.monotonic() - started) * 1000)
    status = success_status if message_id is not None else "SEND_FAILED"
    if message_id is None and failure_reason is None:
        failure_reason = "TELEGRAM_NOT_ACCEPTED"
    return _record(
        identity=identity,
        alert_family=alert_family,
        event_type=event_type,
        fingerprint=fingerprint,
        status=status,
        symbol=symbol,
        pair=pair,
        message_id=message_id,
        journey_id=journey_id,
        trade_id=trade_id,
        suppression_reason=None,
        failure_reason=failure_reason,
        generated_at=generated,
        attempted_at=attempted,
        latency_ms=latency_ms,
        state_file=state_file,
        event_file=event_file,
    )


def edit_tracked_telegram(
    *,
    bot_token: str,
    chat_id: str,
    message_id: int,
    message: str,
    identity: str,
    alert_family: str,
    event_type: str,
    fingerprint: str,
    symbol: str | None = None,
    pair: str | None = None,
    journey_id: str | None = None,
    trade_id: str | None = None,
    generated_at: datetime | None = None,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> TelegramDeliveryResult:
    generated = _utc(generated_at)
    attempted = _utc()
    started = time.monotonic()
    delivered = False
    failure_reason: str | None = None
    try:
        delivered = bool(edit_telegram_message(bot_token, chat_id, int(message_id), message))
    except Exception as exc:
        failure_reason = type(exc).__name__
    latency_ms = round((time.monotonic() - started) * 1000)
    if not delivered and failure_reason is None:
        failure_reason = "TELEGRAM_EDIT_NOT_ACCEPTED"
    return _record(
        identity=identity,
        alert_family=alert_family,
        event_type=event_type,
        fingerprint=fingerprint,
        status="EDITED" if delivered else "EDIT_FAILED",
        symbol=symbol,
        pair=pair,
        message_id=message_id if delivered else None,
        journey_id=journey_id,
        trade_id=trade_id,
        suppression_reason=None,
        failure_reason=failure_reason,
        generated_at=generated,
        attempted_at=attempted,
        latency_ms=latency_ms,
        state_file=state_file,
        event_file=event_file,
    )


def record_telegram_suppression(
    *,
    identity: str,
    alert_family: str,
    event_type: str,
    fingerprint: str,
    reason: str,
    symbol: str | None = None,
    pair: str | None = None,
    journey_id: str | None = None,
    trade_id: str | None = None,
    generated_at: datetime | None = None,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> TelegramDeliveryResult:
    now = _utc()
    return _record(
        identity=identity,
        alert_family=alert_family,
        event_type=event_type,
        fingerprint=fingerprint,
        status="SUPPRESSED",
        symbol=symbol,
        pair=pair,
        message_id=None,
        journey_id=journey_id,
        trade_id=trade_id,
        suppression_reason=reason,
        failure_reason=None,
        generated_at=generated_at or now,
        attempted_at=now,
        latency_ms=0,
        state_file=state_file,
        event_file=event_file,
    )



def record_telegram_not_eligible(
    *,
    identity: str,
    alert_family: str,
    event_type: str,
    fingerprint: str,
    reason: str,
    symbol: str | None = None,
    pair: str | None = None,
    journey_id: str | None = None,
    trade_id: str | None = None,
    generated_at: datetime | None = None,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> TelegramDeliveryResult:
    now = _utc()
    return _record(
        identity=identity,
        alert_family=alert_family,
        event_type=event_type,
        fingerprint=fingerprint,
        status="NOT_ELIGIBLE",
        symbol=symbol,
        pair=pair,
        message_id=None,
        journey_id=journey_id,
        trade_id=trade_id,
        suppression_reason=reason,
        failure_reason=None,
        generated_at=generated_at or now,
        attempted_at=now,
        latency_ms=0,
        state_file=state_file,
        event_file=event_file,
    )



def link_delivery_to_journey(
    *,
    identity: str,
    alert_family: str,
    event_type: str,
    fingerprint: str,
    journey_id: str,
    signal_id: str | None = None,
    trade_id: str | None = None,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> bool:
    """Attach post-alert intelligence lineage without rewriting delivery history."""
    alert_id = _alert_id(
        identity=identity,
        alert_family=alert_family,
        event_type=event_type,
        fingerprint=fingerprint,
    )
    now = _utc()
    lock_file = state_file.parent / f".{state_file.name}.delivery.lock"
    try:
        with registry_lock(lock_file):
            state = load_json(state_file)
            alerts = state.get("alerts")
            if not isinstance(alerts, dict):
                alerts = {}
            row = alerts.get(alert_id)
            if not isinstance(row, dict):
                row = {
                    "identity": identity,
                    "alert_family": str(alert_family).upper(),
                    "event_type": str(event_type).upper(),
                    "fingerprint": fingerprint,
                }
            row["journey_id"] = str(journey_id)
            if signal_id:
                row["signal_id"] = str(signal_id)
            if trade_id:
                row["trade_id"] = str(trade_id)
            row["lineage_updated_at"] = now.isoformat()
            alerts[alert_id] = row
            state["alerts"] = alerts
            save_json_atomic(state_file, state)
            _append_event_locked(
                {
                    "schema_version": 1,
                    "record_type": "TELEGRAM_DELIVERY_LINEAGE",
                    "alert_id": alert_id,
                    "identity": identity,
                    "alert_family": str(alert_family).upper(),
                    "event_type": str(event_type).upper(),
                    "fingerprint": fingerprint,
                    "journey_id": str(journey_id),
                    "signal_id": str(signal_id or "") or None,
                    "trade_id": str(trade_id or "") or None,
                    "linked_at": now.isoformat(),
                },
                event_file=event_file,
            )
        return True
    except (OSError, TimeoutError, RegistryIOError, TypeError, ValueError):
        return False


def canonical_message_id(identity: str, *, state_file: Path = STATE_FILE) -> int | None:
    try:
        lock_file = state_file.parent / f".{state_file.name}.delivery.lock"
        with registry_lock(lock_file):
            state = load_json(state_file)
        identities = state.get("identities") or {}
        row = identities.get(str(identity or "").strip()) or {}
        return _safe_message_id(row.get("message_id"))
    except (OSError, TimeoutError, RegistryIOError, TypeError):
        return None


def read_delivery_events(*, path: Path = EVENT_FILE, limit: int = MAX_SUMMARY_EVENTS) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, int(limit)))
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return list(rows)


def build_delivery_summary(*, scope: str = "all", path: Path = EVENT_FILE) -> dict[str, Any]:
    if scope not in {"all", "today"}:
        raise ValueError("scope must be all or today")
    rows = read_delivery_events(path=path)
    if scope == "today":
        today = datetime.now(timezone.utc).date()
        rows = [
            row
            for row in rows
            if str(row.get("attempted_at") or "")[:10] == today.isoformat()
        ]
    lineage_links = sum(1 for row in rows if row.get("record_type") == "TELEGRAM_DELIVERY_LINEAGE")
    delivery_rows = [row for row in rows if row.get("record_type") == "TELEGRAM_DELIVERY_EVENT"]
    statuses: dict[str, int] = {}
    families: dict[str, int] = {}
    delivered = 0
    failed = 0
    suppressed = 0
    latencies: list[int] = []
    for row in delivery_rows:
        status = str(row.get("status") or "UNKNOWN").upper()
        family = str(row.get("alert_family") or "UNKNOWN").upper()
        statuses[status] = statuses.get(status, 0) + 1
        families[family] = families.get(family, 0) + 1
        if bool(row.get("delivered")):
            delivered += 1
            value = row.get("latency_ms")
            if isinstance(value, int) and value >= 0:
                latencies.append(value)
        elif status == "SUPPRESSED":
            suppressed += 1
        elif status.endswith("FAILED"):
            failed += 1
    return {
        "events": len(delivery_rows),
        "lineage_links": lineage_links,
        "delivered": delivered,
        "failed": failed,
        "suppressed": suppressed,
        "delivery_success_pct": (
            round(delivered / (delivered + failed) * 100.0, 2)
            if delivered + failed > 0
            else None
        ),
        "avg_delivery_latency_ms": (
            round(sum(latencies) / len(latencies), 2) if latencies else None
        ),
        "by_status": dict(sorted(statuses.items())),
        "by_family": dict(sorted(families.items())),
        "recent": list(reversed(delivery_rows[-40:])),
    }
