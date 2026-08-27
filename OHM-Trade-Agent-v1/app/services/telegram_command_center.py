from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import re
import time
from typing import Any

from app.core.config import get_settings
from app.exchanges.kraken import KrakenClient
from app.exchanges.kraken_identity import canonicalize_asset, canonicalize_pair
from app.exchanges.kraken_private import KrakenPrivateAPIError, KrakenPrivateClient
from app.services.active_trade_registry import get_active_trades
from app.services.asset_display_identity import display_asset_text, display_market_label
from app.services.kraken_reconciliation import _order_matches_intent
from app.services.order_intent_registry import get_live_order_intents
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_market_insights import (
    MarketInsight,
    MarketInsightUnavailable,
    MarketResolutionError,
    analyze_market,
    format_market_insight,
    resolve_market,
)
from app.services.telegram_notifier import (
    edit_telegram_message,
    send_telegram_message,
    send_telegram_message_with_id,
)


WATCH_FILE = Path("/app/data/telegram_market_watches.json")
WATCH_LOCK = WATCH_FILE.parent / ".telegram_market_watches.lock"
MAX_WATCHES = 8
WATCH_REFRESH_SECONDS = 300
WATCH_PRICE_CHANGE_PCT = 2.0
MAX_WATCH_REFRESH_PER_PASS = 2
MAX_COMMAND_LENGTH = 96
MAX_ORDERS_DISPLAY = 10
MAX_POSITIONS_DISPLAY = 10
FIAT_ASSETS = {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"}


_HELP = (
    "🤖 OHM TELEGRAM COMMANDS\n\n"
    "/COIN — quick scan of any Kraken-supported USD/USDT spot coin\n"
    "/scan COIN — deterministic Kraken/OHM market read\n"
    "/why COIN — deeper scan details\n"
    "/watch COIN — create/update a monitored Telegram card\n"
    "/unwatch COIN — stop monitoring\n"
    "/watchlist — show monitored coins\n"
    "/orders — review current Kraken open orders\n"
    "/positions — review Kraken spot balances/margin positions\n"
    "/market — BTC-led market regime read\n"
    "/help — show commands\n\n"
    "Examples: /CAP, /BTC, /ETH, /scan VVV\n\n"
    "Read-only/advisory: Telegram commands never place, cancel, modify, or confirm Kraken orders."
)


class TelegramCommandError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if parsed >= 0 else default


def _safe_message_id(value: Any) -> int:
    parsed = _safe_nonnegative_int(value)
    return parsed if parsed > 0 else 0


def _fmt_price(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if value >= 0.01:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.10f}".rstrip("0").rstrip(".")


def parse_command(text: str | None) -> tuple[str, tuple[str, ...]] | None:
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return None
    if len(raw) > MAX_COMMAND_LENGTH:
        raise TelegramCommandError("Command is too long")
    parts = raw.split()
    if not parts:
        return None
    command_token = parts[0][1:]
    command = command_token.split("@", 1)[0].strip().casefold()
    if not command or not re.fullmatch(r"[a-z0-9_]+", command):
        raise TelegramCommandError("Invalid command")
    return command, tuple(parts[1:])


def _require_symbol(args: tuple[str, ...], command: str) -> str:
    if len(args) != 1:
        raise TelegramCommandError(f"Usage: /{command} COIN")
    return args[0]


def _canonical_watch_symbol(value: str) -> str:
    """Normalize a watch key locally so /unwatch works during Kraken outages."""
    cleaned = str(value or "").strip().upper().lstrip("$#")
    plain = re.fullmatch(r"[A-Z0-9]{1,24}", cleaned)
    quoted = re.fullmatch(r"[A-Z0-9]{1,18}[/_.-](?:USD|USDT)", cleaned)
    if not cleaned or (plain is None and quoted is None):
        raise TelegramCommandError("Invalid coin symbol")
    # A separator is an explicit pair request, so strip its quote locally.
    # A plain symbol is treated as the base asset exactly as typed. This avoids
    # corrupting legitimate asset tickers that themselves end in USD/USDT.
    if quoted is not None:
        pair = canonicalize_pair(cleaned)
        for quote in ("USDT", "USD"):
            if pair.endswith(quote) and len(pair) > len(quote):
                pair = pair[: -len(quote)]
                break
        symbol = canonicalize_asset(pair)
    else:
        symbol = canonicalize_asset(cleaned)
    if not symbol or not re.fullmatch(r"[A-Z0-9]{1,20}", symbol):
        raise TelegramCommandError("Invalid coin symbol")
    return symbol


def _load_watches(path: Path = WATCH_FILE) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    watches = payload.get("watches", payload)
    if not isinstance(watches, dict):
        raise RegistryIOError("Telegram watch registry has invalid structure")
    normalized: dict[str, dict[str, Any]] = {}
    for symbol, row in watches.items():
        if not isinstance(symbol, str) or not isinstance(row, dict):
            raise RegistryIOError("Telegram watch registry contains an invalid row")
        normalized[symbol.upper()] = dict(row)
    return normalized


def _save_watches(watches: dict[str, dict[str, Any]], path: Path = WATCH_FILE) -> None:
    save_json_atomic(path, {"version": 1, "watches": watches})


def get_watches(path: Path = WATCH_FILE) -> dict[str, dict[str, Any]]:
    lock = path.parent / f".{path.name}.lock" if path != WATCH_FILE else WATCH_LOCK
    with registry_lock(lock):
        return _load_watches(path)


def _put_watch(symbol: str, row: dict[str, Any], path: Path = WATCH_FILE) -> None:
    lock = path.parent / f".{path.name}.lock" if path != WATCH_FILE else WATCH_LOCK
    with registry_lock(lock):
        watches = _load_watches(path)
        watches[symbol.upper()] = dict(row)
        _save_watches(watches, path)


def _delete_watch(symbol: str, path: Path = WATCH_FILE) -> bool:
    lock = path.parent / f".{path.name}.lock" if path != WATCH_FILE else WATCH_LOCK
    with registry_lock(lock):
        watches = _load_watches(path)
        removed = watches.pop(symbol.upper(), None) is not None
        if removed:
            _save_watches(watches, path)
        return removed


def _watch_row(insight: MarketInsight, message_id: int, *, failures: int = 0) -> dict[str, Any]:
    return {
        "symbol": insight.symbol,
        "pair": insight.pair,
        "message_id": int(message_id),
        "state": insight.state,
        "action": insight.action,
        "risk": insight.risk,
        "tradeability": insight.tradeability,
        # last_price is intentionally the last successfully DELIVERED price.
        # Non-material observations do not move this baseline, otherwise a
        # gradual move could evade the cumulative WATCH_PRICE_CHANGE_PCT gate.
        "last_price": float(insight.current_price),
        "last_checked_at": _now_iso(),
        "consecutive_failures": int(failures),
    }


def _touch_watch_row(row: dict[str, Any], *, failures: int = 0) -> dict[str, Any]:
    updated = dict(row)
    updated["last_checked_at"] = _now_iso()
    updated["consecutive_failures"] = int(max(0, failures))
    return updated


def _is_material_watch_change(row: dict[str, Any], insight: MarketInsight) -> bool:
    if _safe_message_id(row.get("message_id")) <= 0:
        return True
    previous_signature = (
        str(row.get("state") or ""),
        str(row.get("action") or ""),
        str(row.get("risk") or ""),
        str(row.get("tradeability") or ""),
    )
    if previous_signature != insight.material_signature():
        return True
    previous_price = _finite(row.get("last_price"))
    if previous_price is None or previous_price <= 0:
        return True
    change = abs(insight.current_price / previous_price - 1.0) * 100.0
    return change >= WATCH_PRICE_CHANGE_PCT


def _format_watchlist(watches: dict[str, dict[str, Any]]) -> str:
    if not watches:
        return "👁 OHM WATCHLIST\nNo coins are being watched. Use /watch COIN."
    lines = [f"👁 OHM WATCHLIST — {len(watches)}/{MAX_WATCHES}"]
    for symbol, row in sorted(watches.items()):
        lines.append(
            f"{symbol}: {row.get('state', 'UNKNOWN')} | {row.get('action', 'UNKNOWN')} | {_fmt_price(_finite(row.get('last_price')))}"
        )
    return "\n".join(lines)[:4000]


def _distance_to_order(*, side: str, current: float | None, limit_price: float | None) -> float | None:
    if current is None or limit_price is None or current <= 0 or limit_price <= 0:
        return None
    if side.upper() == "SELL":
        return (limit_price / current - 1.0) * 100.0
    if side.upper() == "BUY":
        return (current / limit_price - 1.0) * 100.0
    return None


def _order_age_days(row: dict[str, Any]) -> float | None:
    opened = _finite(row.get("opentm"))
    if opened is None or opened <= 0:
        return None
    return max(0.0, (time.time() - opened) / 86400.0)


def _order_label(*, distance: float | None, age_days: float | None) -> str:
    if distance is not None and distance <= 0:
        return "AT/THROUGH LIMIT"
    if age_days is not None and age_days >= 30 and distance is not None and distance >= 100:
        return "STALE — EXIT REVIEW"
    if distance is not None and distance <= 3:
        return "NEAR LIMIT"
    if distance is not None and distance <= 20:
        return "WATCH"
    if age_days is not None and age_days >= 30:
        return "STALE"
    return "OPEN"


def format_orders_report(
    open_orders: dict[str, dict[str, Any]],
    intents: list[Any],
    client: KrakenClient,
) -> str:
    if not open_orders:
        return "📌 OHM ORDERS\nNo open Kraken orders."
    lines = [f"📌 OHM ORDERS — {len(open_orders)} open"]
    for index, (order_id, row) in enumerate(open_orders.items()):
        if index >= MAX_ORDERS_DISPLAY:
            lines.append(f"… {len(open_orders) - MAX_ORDERS_DISPLAY} more not shown")
            break
        descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
        pair = str(descr.get("pair") or row.get("pair") or "UNKNOWN").upper()
        side = str(descr.get("type") or row.get("type") or "UNKNOWN").upper()
        order_type = str(descr.get("ordertype") or row.get("ordertype") or "UNKNOWN").upper()
        limit_price = _finite(descr.get("price") or row.get("price"))
        current: float | None = None
        if pair and pair != "UNKNOWN":
            try:
                current = _finite(client.get_ticker(pair).get("last"))
            except Exception:
                current = None
        distance = _distance_to_order(side=side, current=current, limit_price=limit_price)
        age_days = _order_age_days(row)
        managed = any(_order_matches_intent(row, intent) for intent in intents)
        label = _order_label(distance=distance, age_days=age_days)
        distance_text = "N/A" if distance is None else f"{distance:+.1f}%"
        age_text = "N/A" if age_days is None else f"{age_days:.0f}d"
        source = "OHM-MATCHED" if managed else "EXTERNAL"
        lines.extend(
            [
                "",
                f"{pair} — {side} {order_type}",
                f"Limit: {_fmt_price(limit_price)} | Current: {_fmt_price(current)} | Gap: {distance_text}",
                f"Age: {age_text} | Status: {label} | Source: {source}",
                f"Ref: {str(order_id)[-8:]}",
            ]
        )
    lines.append("\nRead-only review. OHM did not change any Kraken order.")
    return "\n".join(lines)[:4000]


def _private_client() -> KrakenPrivateClient:
    client = KrakenPrivateClient()
    if not client.enabled:
        raise TelegramCommandError("Kraken private read-only credentials are not configured")
    client.assert_read_only()
    return client


def orders_report() -> str:
    try:
        private = _private_client()
        open_orders = private.get_open_orders()
        intents = get_live_order_intents()
        return format_orders_report(open_orders, intents, KrakenClient(timeout_seconds=5.0))
    except KrakenPrivateAPIError:
        return "⚠️ OHM ORDERS — Kraken private account data is temporarily unavailable. No order was changed."
    except RegistryIOError:
        return "⚠️ OHM ORDERS — local lifecycle state is unavailable, so order attribution is unsafe. No order was changed."


def _aggregate_balances(raw: dict[str, float]) -> tuple[dict[str, float], bool]:
    balances: dict[str, float] = {}
    invalid = False
    for raw_asset, raw_amount in raw.items():
        amount = _finite(raw_amount)
        if amount is None or amount < 0:
            invalid = True
            continue
        asset = canonicalize_asset(str(raw_asset))
        if not asset:
            continue
        balances[asset] = balances.get(asset, 0.0) + amount
    return balances, invalid


def positions_report() -> str:
    try:
        private = _private_client()
        balances, invalid = _aggregate_balances(private.get_balance())
        margin_positions = private.get_open_positions()
    except KrakenPrivateAPIError:
        return "⚠️ OHM POSITIONS — Kraken private account data is temporarily unavailable."

    public = KrakenClient(timeout_seconds=5.0)
    try:
        pair_directory = public.get_asset_pairs()
    except Exception:
        pair_directory = {}

    class _DirectoryClient:
        def get_asset_pairs(self):
            return pair_directory

    resolver = _DirectoryClient()
    rows: list[tuple[float, str, float, str]] = []
    unpriced: list[tuple[str, float]] = []
    for asset, quantity in balances.items():
        if quantity <= 0 or asset in FIAT_ASSETS or asset == "USDT":
            continue
        try:
            identity = resolve_market(asset, resolver)  # type: ignore[arg-type]
            price = _finite(public.get_ticker(identity.altname).get("last"))
        except Exception:
            price = None
        if price is None or price <= 0:
            unpriced.append((asset, quantity))
            continue
        rows.append((quantity * price, asset, quantity, identity.display_pair))

    rows.sort(reverse=True)
    lines = ["💼 OHM POSITIONS — KRAKEN READ-ONLY"]
    if not rows and not unpriced and not margin_positions:
        lines.append("No non-cash spot balances or margin positions found.")
    for value, asset, quantity, pair in rows[:MAX_POSITIONS_DISPLAY]:
        lines.append(f"{asset}: {quantity:.8g} | ~${value:,.2f} | {pair}")
    remaining = max(0, len(rows) - MAX_POSITIONS_DISPLAY)
    if remaining:
        lines.append(f"… {remaining} more valued spot balances not shown")
    for asset, quantity in unpriced[:3]:
        lines.append(f"{asset}: {quantity:.8g} | value unavailable")
    if margin_positions:
        lines.append(f"Margin positions: {len(margin_positions)} (use Kraken/OHM active monitoring for lifecycle detail)")
    else:
        lines.append("Margin positions: 0")
    active = [trade for trade in get_active_trades() if getattr(trade, "status", "") == "active"]
    lines.append(f"OHM active lifecycles: {len(active)}")
    if invalid:
        lines.append("⚠️ One or more malformed/non-finite balances were excluded from this display.")
    lines.append("Spot balances may include units reserved by open sell orders.")
    return "\n".join(lines)[:4000]


def market_report() -> str:
    try:
        insight = analyze_market("BTC")
    except (MarketResolutionError, MarketInsightUnavailable) as exc:
        return f"⚠️ OHM MARKET — BTC regime data unavailable: {str(exc)[:160]}"
    if insight.momentum_24h_pct <= -3.0 or insight.momentum_1h_pct <= -2.0 or insight.state == "REVERSAL_RISK":
        regime = "RISK_OFF / DEFENSIVE"
    elif insight.momentum_24h_pct >= 3.0 and insight.momentum_1h_pct > -0.5 and insight.trend == "BULLISH":
        regime = "RISK_ON"
    else:
        regime = "MIXED / TRANSITION"
    return (
        f"🌐 OHM MARKET — BTC-LED READ\n"
        f"Regime: {regime}\n"
        f"BTC: {_fmt_price(insight.current_price)}\n"
        f"State: {insight.state}\n"
        f"Momentum: 1h {insight.momentum_1h_pct:+.2f}% | 6h {insight.momentum_6h_pct:+.2f}% | 24h {insight.momentum_24h_pct:+.2f}%\n"
        f"Volume: {insight.relative_volume:.2f}x | Flow: {insight.flow_bias}\n"
        f"Risk: {insight.risk}\n"
        f"Action: {insight.action}\n\n"
        "This is a BTC-led regime read, not full-market breadth."
    )


def _send(settings: Any, text: str) -> bool:
    return send_telegram_message(settings.telegram_bot_token, settings.telegram_chat_id, text[:4000])


def _handle_watch(settings: Any, symbol_query: str) -> None:
    try:
        insight = analyze_market(symbol_query)
    except (MarketResolutionError, MarketInsightUnavailable) as exc:
        _send(settings, f"⚠️ OHM WATCH — {str(exc)[:220]}")
        return
    try:
        watches = get_watches()
    except RegistryIOError:
        _send(settings, "⚠️ OHM WATCH — watch registry is unavailable; no watch was added.")
        return
    existing = watches.get(insight.symbol)
    if existing is None and len(watches) >= MAX_WATCHES:
        _send(settings, f"⚠️ OHM WATCH — limit reached ({MAX_WATCHES}). Use /unwatch COIN first.")
        return

    text = format_market_insight(insight, watch=True)
    message_id: int | None = None
    if existing is not None:
        old_id = _safe_message_id(existing.get("message_id"))
        if old_id > 0 and edit_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            old_id,
            text,
        ):
            message_id = old_id
    if message_id is None:
        message_id = send_telegram_message_with_id(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            text,
        )
    if message_id is None:
        return
    try:
        _put_watch(insight.symbol, _watch_row(insight, message_id))
    except (RegistryIOError, OSError, TimeoutError):
        _send(settings, "⚠️ OHM WATCH — card sent, but watch persistence failed; automatic refresh is not active.")
        return
    if existing is not None:
        _send(settings, f"👁 {insight.symbol} watch refreshed. The existing watch card will update on material changes.")


def _handle_unwatch(settings: Any, symbol_query: str) -> None:
    try:
        symbol = _canonical_watch_symbol(symbol_query)
        removed = _delete_watch(symbol)
    except TelegramCommandError as exc:
        _send(settings, f"⚠️ OHM UNWATCH — {exc}")
        return
    except (RegistryIOError, OSError, TimeoutError):
        _send(settings, "⚠️ OHM UNWATCH — watch registry is unavailable; no change was made.")
        return
    _send(settings, f"👁 {symbol} removed from watchlist." if removed else f"👁 {symbol} was not on the watchlist.")


def process_command_message(update: dict[str, Any], settings: Any | None = None) -> bool:
    """Process one already-authorized Telegram message update.

    Returns True only when the update contained an OHM command. Command paths
    are deliberately read-only with respect to Kraken and OHM trade authority.
    """
    settings = settings or get_settings()
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict):
        return False
    try:
        parsed = parse_command(message.get("text"))
    except TelegramCommandError as exc:
        _send(settings, f"⚠️ {exc}")
        return True
    if parsed is None:
        return False
    command, args = parsed

    try:
        if command in {"start", "help"}:
            _send(settings, _HELP)
        elif command == "scan":
            symbol = _require_symbol(args, "scan")
            try:
                _send(settings, format_market_insight(analyze_market(symbol)))
            except (MarketResolutionError, MarketInsightUnavailable) as exc:
                _send(settings, f"⚠️ OHM SCAN — {str(exc)[:220]}")
        elif command == "why":
            symbol = _require_symbol(args, "why")
            try:
                _send(settings, format_market_insight(analyze_market(symbol), verbose=True))
            except (MarketResolutionError, MarketInsightUnavailable) as exc:
                _send(settings, f"⚠️ OHM WHY — {str(exc)[:220]}")
        elif command == "watch":
            _handle_watch(settings, _require_symbol(args, "watch"))
        elif command == "unwatch":
            _handle_unwatch(settings, _require_symbol(args, "unwatch"))
        elif command == "watchlist":
            if args:
                raise TelegramCommandError("Usage: /watchlist")
            try:
                _send(settings, _format_watchlist(get_watches()))
            except RegistryIOError:
                _send(settings, "⚠️ OHM WATCHLIST — watch registry is unavailable.")
        elif command == "orders":
            if args:
                raise TelegramCommandError("Usage: /orders")
            _send(settings, orders_report())
        elif command == "positions":
            if args:
                raise TelegramCommandError("Usage: /positions")
            _send(settings, positions_report())
        elif command == "market":
            if args:
                raise TelegramCommandError("Usage: /market")
            _send(settings, market_report())
        else:
            _send(settings, f"Unknown command /{command}. Use /help.")
    except TelegramCommandError as exc:
        _send(settings, f"⚠️ {exc}")
    except Exception as exc:
        _send(settings, f"⚠️ OHM command failed safely ({type(exc).__name__}). No Kraken order was changed.")
    return True


def refresh_market_watches(settings: Any | None = None, *, force: bool = False) -> int:
    """Refresh persistent watch cards and edit only on material delivered changes."""
    settings = settings or get_settings()
    try:
        watches = get_watches()
    except RegistryIOError:
        _send(
            settings,
            "⚠️ OHM WATCH DEGRADED — watch registry is unavailable or corrupt. Existing Telegram cards may be stale.",
        )
        return 0
    changed = 0
    processed = 0
    now = datetime.now(timezone.utc)
    for symbol, row in sorted(watches.items()):
        if not force:
            raw_checked = str(row.get("last_checked_at") or "")
            try:
                checked = datetime.fromisoformat(raw_checked.replace("Z", "+00:00"))
            except ValueError:
                checked = None
            if checked is not None and checked.tzinfo is not None:
                if (now - checked).total_seconds() < WATCH_REFRESH_SECONDS:
                    continue
            if processed >= MAX_WATCH_REFRESH_PER_PASS:
                break
        processed += 1
        try:
            insight = analyze_market(symbol)
        except Exception:
            failures = _safe_nonnegative_int(row.get("consecutive_failures")) + 1
            try:
                _put_watch(symbol, _touch_watch_row(row, failures=failures))
            except Exception:
                pass
            if failures == 3:
                _send(settings, f"⚠️ OHM WATCH DEGRADED — {symbol}: three consecutive market-data refresh failures. Existing card retained.")
            continue

        material = _is_material_watch_change(row, insight)
        if not material:
            # Preserve the last successfully delivered price/signature so the
            # 2% threshold remains cumulative from what the user actually saw.
            try:
                _put_watch(symbol, _touch_watch_row(row, failures=0))
            except Exception:
                pass
            continue

        message_id = _safe_message_id(row.get("message_id"))
        delivered = False
        if message_id > 0:
            delivered = edit_telegram_message(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                message_id,
                format_market_insight(insight, watch=True),
            )
        if not delivered:
            replacement = send_telegram_message_with_id(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                format_market_insight(insight, watch=True),
            )
            if replacement is not None:
                message_id = replacement
                delivered = True

        if delivered and message_id > 0:
            changed += 1
            try:
                _put_watch(symbol, _watch_row(insight, message_id))
            except Exception:
                pass
        else:
            # Telegram did not receive the new state. Never advance the stored
            # delivered baseline; keep it eligible for retry on the next pass.
            try:
                _put_watch(symbol, _touch_watch_row(row, failures=0))
            except Exception:
                pass
    return changed
