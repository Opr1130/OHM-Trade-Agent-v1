from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.exchanges.kraken import KrakenClient
from app.exchanges.kraken_private import KrakenPrivateAPIError, KrakenPrivateClient
from app.services.kraken_reconciliation import _order_matches_intent
from app.services.order_intent_registry import get_live_order_intents
from app.services.registry_io import load_json, registry_lock, save_json_atomic
from app.services.asset_display_identity import display_market_label
from app.services.telegram_delivery import (
    record_telegram_not_eligible,
    send_tracked_telegram,
)


REVIEW_FILE = Path("/app/data/external_order_reviews.json")
LOCK_FILE = REVIEW_FILE.parent / ".external_order_reviews.lock"


@dataclass(frozen=True)
class ExternalOrderReviewSummary:
    status: str
    open_orders_seen: int = 0
    unmatched_orders_seen: int = 0
    new_reviews: int = 0
    notifications_sent: int = 0
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_reviewed() -> dict[str, dict[str, Any]]:
    return load_json(REVIEW_FILE)


def _save_reviewed(data: dict[str, dict[str, Any]]) -> None:
    save_json_atomic(REVIEW_FILE, data)


def _pair_from_order(row: dict[str, Any]) -> str:
    descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
    return str(descr.get("pair") or row.get("pair") or "").upper()


def _side_from_order(row: dict[str, Any]) -> str:
    descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
    return str(descr.get("type") or row.get("type") or "").upper()


def _price_from_order(row: dict[str, Any]) -> float | None:
    descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
    raw = descr.get("price") or row.get("price")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _volume_from_order(row: dict[str, Any]) -> float | None:
    raw = row.get("vol") or row.get("volume")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _order_opened_at(row: dict[str, Any]) -> str | None:
    try:
        ts = float(row.get("opentm") or 0)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _distance_to_limit_pct(*, side: str, current: float, limit_price: float) -> float:
    if current <= 0 or limit_price <= 0:
        return 0.0
    if side == "SELL":
        return (limit_price / current - 1.0) * 100.0
    return (current / limit_price - 1.0) * 100.0


def _format_review(
    *,
    order_id: str,
    pair: str,
    side: str,
    limit_price: float | None,
    current_price: float | None,
    volume: float | None,
    opened_at: str | None,
) -> str:
    limit_text = f"{limit_price:.10g}" if limit_price is not None else "N/A"
    current_text = f"{current_price:.10g}" if current_price is not None else "N/A"
    volume_text = f"{volume:.10g}" if volume is not None else "N/A"
    distance_text = "N/A"
    if limit_price is not None and current_price is not None:
        distance_text = f"{_distance_to_limit_pct(side=side, current=current_price, limit_price=limit_price):+.2f}%"

    return (
        "📌 OHM AI — EXTERNAL KRAKEN ORDER REVIEW\n\n"
        f"Market: {display_market_label(pair or 'UNKNOWN')}\n"
        f"Side: {side or 'UNKNOWN'}\n"
        f"Limit: {limit_text}\n"
        f"Current: {current_text}\n"
        f"Distance to limit: {distance_text}\n"
        f"Quantity: {volume_text}\n"
        f"Opened: {opened_at or 'N/A'}\n\n"
        "Status: EXTERNAL / UNMANAGED\n\n"
        "This is a one-time review only. OHM will leave the order untouched and will not attribute its prior gain/loss to the OHM strategy. "
        "If you later adopt this position into LEGACY_MANAGED mode, OHM can monitor recovery and provide separate HOLD / REDUCE / EXIT guidance without contaminating normal OHM trade-learning results.\n\n"
        f"Reference: {order_id[-8:]}"
    )


def review_external_open_orders(
    private_client: KrakenPrivateClient | None = None,
    public_client: KrakenClient | None = None,
) -> ExternalOrderReviewSummary:
    private_client = private_client or KrakenPrivateClient()
    if not private_client.enabled:
        return ExternalOrderReviewSummary(status="UNAVAILABLE", reason="Kraken private credentials are not configured")

    try:
        private_client.assert_read_only()
        open_orders = private_client.get_open_orders()
    except KrakenPrivateAPIError as exc:
        return ExternalOrderReviewSummary(status="ERROR", reason=str(exc))

    intents = get_live_order_intents()
    unmatched: list[tuple[str, dict[str, Any]]] = []
    for order_id, row in open_orders.items():
        if any(_order_matches_intent(row, intent) for intent in intents):
            continue
        unmatched.append((order_id, row))

    settings = get_settings()
    telegram_ready = bool(settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_chat_id)
    public_client = public_client or KrakenClient()

    with registry_lock(LOCK_FILE):
        reviewed = _load_reviewed()
        new_reviews = 0
        sent = 0
        for order_id, row in unmatched:
            if order_id in reviewed:
                continue
            new_reviews += 1
            pair = _pair_from_order(row)
            side = _side_from_order(row)
            limit_price = _price_from_order(row)
            volume = _volume_from_order(row)
            current_price: float | None = None
            if pair:
                try:
                    current_price = float(public_client.get_ticker(pair)["last"])
                except Exception:
                    current_price = None

            message = _format_review(
                order_id=order_id,
                pair=pair,
                side=side,
                limit_price=limit_price,
                current_price=current_price,
                volume=volume,
                opened_at=_order_opened_at(row),
            )
            delivered = False
            identity = f"EXTERNAL_ORDER:{order_id}"
            fingerprint = f"{pair}:{side}:{limit_price}:{volume}"
            if telegram_ready:
                delivery = send_tracked_telegram(
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                    message=message,
                    identity=identity,
                    alert_family="EXTERNAL_ORDER_REVIEW",
                    event_type="UNMANAGED_ORDER",
                    fingerprint=fingerprint,
                    symbol=pair,
                )
                delivered = delivery.delivered
            else:
                record_telegram_not_eligible(
                    identity=identity,
                    alert_family="EXTERNAL_ORDER_REVIEW",
                    event_type="UNMANAGED_ORDER",
                    fingerprint=fingerprint,
                    reason="TELEGRAM_NOT_CONFIGURED",
                    symbol=pair,
                )
            if delivered:
                sent += 1
                reviewed[order_id] = {
                    "reviewed_at": _now(),
                    "pair": pair,
                    "side": side,
                    "limit_price": limit_price,
                    "current_price": current_price,
                    "status": "EXTERNAL_UNMANAGED",
                }
        if sent:
            _save_reviewed(reviewed)

    reason = "one-time unmanaged-order review complete"
    if unmatched and not telegram_ready:
        reason = "unmanaged orders found but Telegram is not configured/enabled"
    return ExternalOrderReviewSummary(
        status="OK",
        open_orders_seen=len(open_orders),
        unmatched_orders_seen=len(unmatched),
        new_reviews=new_reviews,
        notifications_sent=sent,
        reason=reason,
    )
