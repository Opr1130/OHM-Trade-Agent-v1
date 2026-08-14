from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.exchanges.kraken_private import KrakenPrivateAPIError, KrakenPrivateClient
from app.services.active_trade_registry import ActiveTrade, close_trade, get_active_trades
from app.services.execution_learning_registry import record_execution_event
from app.services.order_intent_registry import (
    bind_exchange_order_txid,
    get_live_order_intents,
    mark_order_filled,
    recover_orphaned_filled_intents,
)


@dataclass(frozen=True)
class ReconciliationSummary:
    status: str
    mode: str
    key_name: str = ""
    active_checked: int = 0
    order_intents_checked: int = 0
    fills_seen: int = 0
    open_orders_seen: int = 0
    positions_seen: int = 0
    would_close: tuple[str, ...] = ()
    closed: tuple[str, ...] = ()
    would_fill: tuple[str, ...] = ()
    filled: tuple[str, ...] = ()
    unmatched_open_orders: int = 0
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def reconciliation_enabled() -> bool:
    return os.getenv("KRAKEN_RECONCILIATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def reconciliation_mode() -> str:
    value = os.getenv("KRAKEN_RECONCILIATION_MODE", "observe").strip().lower()
    return value if value in {"observe", "apply"} else "observe"


def _iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _epoch_to_iso(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _canonical_pair(pair: str) -> str:
    value = str(pair or "").upper().replace("/", "").replace("-", "").replace("_", "")
    aliases = {
        "XXBTZUSD": "BTCUSD",
        "XBTUSD": "BTCUSD",
        "XXBTZUSDT": "BTCUSDT",
        "XBTUSDT": "BTCUSDT",
        "XETHZUSD": "ETHUSD",
        "ETHUSD": "ETHUSD",
    }
    return aliases.get(value, value)


def _base_asset(symbol: str) -> str | None:
    value = _canonical_pair(symbol)
    for quote in ("USDT", "USD", "EUR", "GBP", "CAD", "AUD", "BTC", "ETH"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)]
    return None


def _matching_fills(
    trades: dict[str, dict[str, Any]],
    *,
    symbol: str,
    side: str,
    after_epoch: int | None,
    order_txid: str | None = None,
) -> list[dict[str, Any]]:
    wanted_pair = _canonical_pair(symbol)
    result: list[dict[str, Any]] = []
    for txid, row in trades.items():
        if order_txid is not None and str(row.get("ordertxid") or "") != order_txid:
            continue
        if _canonical_pair(str(row.get("pair") or "")) != wanted_pair:
            continue
        if str(row.get("type") or "").lower() != side.lower():
            continue
        try:
            timestamp = float(row.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if after_epoch is not None and timestamp < after_epoch:
            continue
        normalized = dict(row)
        normalized["txid"] = txid
        result.append(normalized)
    result.sort(key=lambda item: float(item.get("time") or 0))
    return result


def _fill_stats(rows: list[dict[str, Any]]) -> tuple[float, float | None, float, str | None, str | None]:
    quantity = 0.0
    notional = 0.0
    fees = 0.0
    times: list[float] = []
    for row in rows:
        try:
            vol = float(row.get("vol") or 0)
            price = float(row.get("price") or 0)
            fee = float(row.get("fee") or 0)
            timestamp = float(row.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0 or price <= 0:
            continue
        quantity += vol
        notional += vol * price
        fees += max(0.0, fee)
        if timestamp > 0:
            times.append(timestamp)
    vwap = notional / quantity if quantity > 0 else None
    first_fill = _epoch_to_iso(min(times)) if times else None
    last_fill = _epoch_to_iso(max(times)) if times else None
    return quantity, vwap, fees, first_fill, last_fill


def _expected_quantity(trade: ActiveTrade) -> float | None:
    if trade.capital is None or trade.capital <= 0 or trade.entry_price <= 0:
        return None
    return trade.capital * max(1.0, float(trade.margin_leverage or 1.0)) / trade.entry_price


def _balance_for_asset(balances: dict[str, float], asset: str | None) -> float | None:
    if not asset:
        return None
    aliases = {asset, f"X{asset}", f"Z{asset}"}
    if asset == "BTC":
        aliases.update({"XBT", "XXBT"})
    values = [float(amount) for key, amount in balances.items() if str(key).upper() in aliases]
    return sum(values) if values else 0.0


def _order_matches_intent(row: dict[str, Any], intent: Any) -> bool:
    descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
    pair = descr.get("pair") or row.get("pair") or ""
    side = descr.get("type") or row.get("type") or ""
    price = descr.get("price") or row.get("price") or 0
    wanted_side = "buy" if intent.direction.upper() == "LONG" else "sell"
    if _canonical_pair(str(pair)) != _canonical_pair(intent.symbol) or str(side).lower() != wanted_side:
        return False
    try:
        order_price = float(price)
    except (TypeError, ValueError):
        return False
    if order_price <= 0 or intent.limit_price <= 0:
        return False
    return abs(order_price / intent.limit_price - 1.0) <= 0.01


def reconcile_kraken_account(client: KrakenPrivateClient | None = None) -> ReconciliationSummary:
    mode = reconciliation_mode()
    if not reconciliation_enabled():
        return ReconciliationSummary(status="DISABLED", mode=mode, reason="KRAKEN_RECONCILIATION_ENABLED is false")

    recovery_note = ""
    if mode == "apply":
        try:
            recovered, recovery_failures = recover_orphaned_filled_intents()
            if recovered or recovery_failures:
                recovery_note = (
                    f"; orphan recovery recovered={len(recovered)} "
                    f"failed={len(recovery_failures)}"
                )
        except Exception as exc:
            recovery_note = f"; orphan recovery unavailable: {exc}"

    client = client or KrakenPrivateClient()
    if not client.enabled:
        return ReconciliationSummary(
            status="UNAVAILABLE",
            mode=mode,
            reason="Kraken private credentials are not configured" + recovery_note,
        )

    try:
        key_info = client.assert_read_only()
        active = get_active_trades()
        intents = get_live_order_intents()
        starts = [_iso_to_epoch(t.opened_at) for t in active] + [_iso_to_epoch(i.created_at) for i in intents]
        starts = [value for value in starts if value is not None]
        history_start = max(0, min(starts) - 300) if starts else None
        balances = client.get_balance()
        open_orders = client.get_open_orders()
        trades = client.get_trades_history(start=history_start)
        positions = client.get_open_positions()
    except KrakenPrivateAPIError as exc:
        return ReconciliationSummary(status="ERROR", mode=mode, reason=str(exc) + recovery_note)

    would_close: list[str] = []
    closed: list[str] = []
    would_fill: list[str] = []
    filled: list[str] = []
    matched_open_order_ids: set[str] = set()

    for trade in active:
        opened_epoch = _iso_to_epoch(trade.opened_at)
        close_side = "sell" if trade.direction.upper() == "LONG" else "buy"
        close_fills = _matching_fills(trades, symbol=trade.symbol, side=close_side, after_epoch=opened_epoch)
        qty, vwap, fee, first_at, last_at = _fill_stats(close_fills)
        expected = _expected_quantity(trade)
        should_close = False

        # Spot LONG reconciliation is intentionally conservative: an observed
        # sell after OHM's entry must cover nearly all expected quantity and
        # Kraken must no longer show a meaningful base-asset balance.
        if trade.direction.upper() == "LONG" and expected and expected > 0 and qty >= expected * 0.90:
            remaining = _balance_for_asset(balances, _base_asset(trade.symbol))
            should_close = remaining is not None and remaining <= expected * 0.10

        # Margin positions are not auto-terminalized until OHM can bind the
        # Kraken position txid directly to the trade. Avoid guessing.
        if should_close and vwap is not None:
            would_close.append(trade.symbol)
            record_execution_event(
                trade.trade_id or f"LEGACY-{trade.symbol}",
                symbol=trade.symbol,
                direction=trade.direction,
                signal_at=trade.opened_at,
                first_fill_at=trade.opened_at,
                full_fill_at=trade.opened_at,
                closed_at=last_at,
                close_price=vwap,
                quantity=qty,
                exit_fee=fee,
            )
            if mode == "apply":
                close_trade(
                    trade.symbol,
                    close_price=vwap,
                    actual_exit_fee=fee,
                    reason="kraken_reconciled_close",
                )
                closed.append(trade.symbol)

    for intent in intents:
        matching_open_ids = [
            order_id
            for order_id, row in open_orders.items()
            if _order_matches_intent(row, intent)
        ]
        matched_open_order_ids.update(matching_open_ids)

        order_txid = intent.exchange_order_txid
        if order_txid:
            # A persisted exact binding is authoritative. Do not let a similar
            # manual order on the same pair/side/time contaminate attribution.
            matching_open_ids = [order_id for order_id in matching_open_ids if order_id == order_txid]
        elif len(matching_open_ids) == 1:
            # A unique open-order match can be used exactly for this cycle. In
            # apply mode persist it so later closed-order fills stay exact.
            order_txid = matching_open_ids[0]
            if mode == "apply":
                bind_exchange_order_txid(intent.trade_id, order_txid)

        for order_id in matching_open_ids:
            row = open_orders[order_id]
            record_execution_event(
                intent.trade_id,
                symbol=intent.symbol,
                direction=intent.direction,
                signal_at=intent.created_at,
                order_placed_at=_epoch_to_iso(row.get("opentm")) or intent.created_at,
                limit_price=intent.limit_price,
            )

        entry_side = "buy" if intent.direction.upper() == "LONG" else "sell"
        entry_fills = _matching_fills(
            trades,
            symbol=intent.symbol,
            side=entry_side,
            after_epoch=_iso_to_epoch(intent.created_at),
            order_txid=order_txid,
        )
        qty, vwap, fee, first_at, last_at = _fill_stats(entry_fills)
        intended_notional = intent.capital * max(1.0, float(intent.margin_leverage or 1.0))
        filled_notional = qty * vwap if vwap is not None else 0.0
        complete = vwap is not None and intended_notional > 0 and filled_notional >= intended_notional * 0.90
        if complete:
            would_fill.append(intent.trade_id)
            record_execution_event(
                intent.trade_id,
                symbol=intent.symbol,
                direction=intent.direction,
                signal_at=intent.created_at,
                order_placed_at=intent.created_at,
                first_fill_at=first_at,
                full_fill_at=last_at,
                limit_price=intent.limit_price,
                fill_price=vwap,
                quantity=qty,
                entry_fee=fee,
            )
            if mode == "apply":
                mark_order_filled(intent.trade_id, fill_price=vwap, actual_entry_fee=fee)
                filled.append(intent.trade_id)

    return ReconciliationSummary(
        status="OK",
        mode=mode,
        key_name=key_info.name,
        active_checked=len(active),
        order_intents_checked=len(intents),
        fills_seen=len(trades),
        open_orders_seen=len(open_orders),
        positions_seen=len(positions),
        would_close=tuple(would_close),
        closed=tuple(closed),
        would_fill=tuple(would_fill),
        filled=tuple(filled),
        unmatched_open_orders=max(0, len(open_orders) - len(matched_open_order_ids)),
        reason="read-only account reconciliation complete" + recovery_note,
    )
