from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.core.config import get_settings
from app.exchanges.kraken import KrakenClient
from app.models.signal import TradingViewSignalV2
from app.services.operator_control import get_operator_decision
from app.services.registry_io import load_json, registry_lock, save_json_atomic


logger = logging.getLogger(__name__)

INBOX_FILE = Path("/app/data/tradingview_v2_inbox.json")
LOCK_FILE = INBOX_FILE.parent / ".tradingview_v2_inbox.lock"

# Terminal dispositions are eligible for pruning once older than the
# configured retention window; PROCESSING is deliberately excluded so a crash
# mid-cycle is reclaimed rather than silently aged out.
TERMINAL = {"PROCESSED", "REJECTED", "DUPLICATE", "POISONED"}
ACTIVE = {"QUEUED", "RETRY"}

# TradingView cannot forge a shorter bar interval than the script emits, but
# it *can* resubmit an old, previously-accepted payload with only
# bar_closed_at bumped to dodge the exact-match dedup hash. Refusing a second
# distinct signal_id for the same pair/direction inside less than one 15m bar
# (minus tolerance) closes that gap without needing payload signing.
MIN_BAR_SPACING_SECONDS = 12 * 60

# Kraken's own base-asset aliasing (see app/scanner/universe.py:BASE_ALIASES).
# Kept in sync deliberately rather than imported, since universe.py is scanner
# infrastructure and this module must not create a dependency cycle risk with
# it; the alias set changes rarely and is covered by a regression test.
_BASE_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


class TradingViewPolicyError(ValueError):
    """A terminal payload-policy rejection safe to return to an authenticated sender."""


class TradingViewInboxFullError(RuntimeError):
    """The bounded durable inbox has no capacity for another event."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def resolve_kraken_pair(symbol: str) -> str:
    """Resolve a TradingView symbol (e.g. "KRAKEN:BTCUSD") to OHM's own
    primary_pair format (e.g. "BTCUSD") — the exact string used elsewhere in
    this codebase both as MarketSnapshot.primary_pair/.symbol and as the
    `pair` argument passed straight to KrakenClient. Matching that format
    exactly (rather than inventing a separate Kraken-altname mapping) is what
    lets merge_native_candidate_evidence/tradingview_only_candidates actually
    find native candidates instead of silently matching nothing.
    """
    normalized = symbol.upper().replace("KRAKEN:", "").replace("-", "").replace("/", "")
    quote = "USDT" if normalized.endswith("USDT") else "USD" if normalized.endswith("USD") else ""
    if not quote:
        raise TradingViewPolicyError("unknown or ambiguous TradingView symbol")
    base = normalized[: -len(quote)]
    if not base:
        raise TradingViewPolicyError("unknown or ambiguous TradingView symbol")
    base = _BASE_ALIASES.get(base, base)
    return f"{base}{quote}"


def validate_event_policy(signal: TradingViewSignalV2, now: datetime | None = None) -> None:
    settings = get_settings()
    now = now or _now()
    if signal.schema_version != settings.tradingview_expected_schema_version:
        raise TradingViewPolicyError("unsupported schema_version")
    if signal.script_version not in _csv(settings.tradingview_allowed_script_versions):
        raise TradingViewPolicyError("unsupported script_version")
    if signal.exchange != "KRAKEN" or not signal.symbol.upper().startswith("KRAKEN:"):
        raise TradingViewPolicyError("TradingView signal must originate from a Kraken chart")
    resolve_kraken_pair(signal.symbol)
    age = (now - signal.bar_closed_at).total_seconds()
    if age > settings.tradingview_max_event_age_seconds:
        raise TradingViewPolicyError("stale TradingView event")
    if age < -settings.tradingview_future_tolerance_seconds:
        raise TradingViewPolicyError("excessively future-dated TradingView event")


def deduplication_key(signal: TradingViewSignalV2) -> str:
    raw = "|".join((signal.signal_id, resolve_kraken_pair(signal.symbol), signal.direction, signal.bar_closed_at.isoformat()))
    return hashlib.sha256(raw.encode()).hexdigest()


def _empty_registry() -> dict:
    return {"events": {}, "metrics": {}, "last_bar_by_key": {}}


def _prune_terminal_events(registry: dict, now: datetime, retention_seconds: int) -> int:
    events = registry.get("events", {})
    cutoff = now.timestamp() - retention_seconds
    stale_keys = []
    for key, row in events.items():
        if row.get("status") not in TERMINAL:
            continue
        stamp = row.get("processed_at") or row.get("received_at")
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if parsed.timestamp() < cutoff:
            stale_keys.append(key)
    for key in stale_keys:
        del events[key]
    # Replay-spacing state is bounded by the same retention policy. A key with
    # no recent bar has no security value but would otherwise accumulate for
    # every pair/direction ever observed.
    last_bar_by_key = registry.setdefault("last_bar_by_key", {})
    for cadence_key, stamp in list(last_bar_by_key.items()):
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            del last_bar_by_key[cadence_key]
            continue
        if parsed.timestamp() < cutoff:
            del last_bar_by_key[cadence_key]
    if stale_keys:
        metrics = registry.setdefault("metrics", {})
        metrics["pruned_events"] = int(metrics.get("pruned_events", 0)) + len(stale_keys)
    return len(stale_keys)


def _reclaim_stuck_processing(registry: dict, now: datetime, timeout_seconds: int) -> int:
    events = registry.get("events", {})
    reclaimed = 0
    for row in events.values():
        if row.get("status") != "PROCESSING":
            continue
        started = row.get("processing_started_at")
        try:
            parsed = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            # No usable timestamp for a PROCESSING row is itself a crash
            # signature (the writer died before recording one) — reclaim it.
            row["status"] = "RETRY"
            row["failure_reason"] = "reclaimed: missing processing_started_at"
            reclaimed += 1
            continue
        if (now - parsed).total_seconds() > timeout_seconds:
            row["status"] = "RETRY"
            row["failure_reason"] = "reclaimed after processing timeout (likely crash mid-cycle)"
            reclaimed += 1
    if reclaimed:
        metrics = registry.setdefault("metrics", {})
        metrics["reclaimed_stuck_processing"] = int(metrics.get("reclaimed_stuck_processing", 0)) + reclaimed
    return reclaimed


def record_rejection(kind: str) -> None:
    settings = get_settings()
    now = _now()
    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        _prune_terminal_events(registry, now, settings.tradingview_retention_seconds)
        metrics = registry.setdefault("metrics", {})
        key = f"{kind}_rejections"
        metrics[key] = int(metrics.get(key, 0)) + 1
        save_json_atomic(INBOX_FILE, registry)


def enqueue(signal: TradingViewSignalV2, received_at: datetime | None = None) -> tuple[dict, bool]:
    validate_event_policy(signal, received_at)
    settings = get_settings()
    now = received_at or _now()
    pair = resolve_kraken_pair(signal.symbol)
    key = deduplication_key(signal)
    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        _prune_terminal_events(registry, now, settings.tradingview_retention_seconds)
        events = registry.setdefault("events", {})
        metrics = registry.setdefault("metrics", {})
        last_bar_by_key = registry.setdefault("last_bar_by_key", {})

        if key in events:
            metrics["replayed_events"] = int(metrics.get("replayed_events", 0)) + 1
            save_json_atomic(INBOX_FILE, registry)
            return events[key], False

        if len(events) >= settings.tradingview_max_inbox_events:
            metrics["capacity_rejections"] = int(metrics.get("capacity_rejections", 0)) + 1
            save_json_atomic(INBOX_FILE, registry)
            raise TradingViewInboxFullError("TradingView inbox is at capacity")

        cadence_key = f"{pair}:{signal.direction}"
        last_bar_iso = last_bar_by_key.get(cadence_key)
        if last_bar_iso:
            try:
                last_bar = datetime.fromisoformat(last_bar_iso)
                gap = abs((signal.bar_closed_at - last_bar).total_seconds())
                if gap < MIN_BAR_SPACING_SECONDS:
                    metrics["replay_spacing_rejections"] = int(metrics.get("replay_spacing_rejections", 0)) + 1
                    save_json_atomic(INBOX_FILE, registry)
                    raise TradingViewPolicyError(
                        "signal for this pair/direction resubmitted faster than one bar interval"
                    )
            except ValueError:
                raise
            except Exception:  # noqa: BLE001 - malformed stored timestamp must never block ingestion
                pass

        row = {
            "received_at": now.isoformat(), "payload": signal.model_dump(mode="json"),
            "schema_version": signal.schema_version, "script_version": signal.script_version,
            "deduplication_key": key, "status": "QUEUED", "attempts": 0,
            "final_disposition": None, "failure_reason": None,
            "related_decision_id": None, "related_trade_id": None,
        }
        events[key] = row
        last_bar_by_key[cadence_key] = signal.bar_closed_at.isoformat()
        metrics["accepted_webhooks"] = int(metrics.get("accepted_webhooks", 0)) + 1
        metrics["last_accepted_at"] = now.isoformat()
        save_json_atomic(INBOX_FILE, registry)
        return row, True


def inbox_status() -> dict:
    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
    events = registry.get("events", {})
    metrics = dict(registry.get("metrics", {}))
    for key in (
        "accepted_webhooks", "authentication_rejections", "schema_rejections",
        "stale_rejections", "future_rejections", "version_rejections",
        "replayed_events", "replay_spacing_rejections", "price_divergence_rejections",
        "native_tradingview_duplicates", "qualified_candidates",
        "chief_calls_caused_by_tradingview", "telegram_alerts",
        "later_verified_fills", "pruned_events", "poisoned_events",
        "reclaimed_stuck_processing",
        "capacity_rejections",
    ):
        metrics.setdefault(key, 0)
    metrics["queue_depth"] = sum(row.get("status") in ACTIVE for row in events.values())
    metrics["stuck_processing"] = sum(row.get("status") == "PROCESSING" for row in events.values())
    metrics["successful_processing"] = sum(row.get("status") == "PROCESSED" for row in events.values())
    metrics["retry_count"] = sum(max(0, int(row.get("attempts", 0)) - 1) for row in events.values())
    metrics["failure_count"] = sum(row.get("status") == "RETRY" for row in events.values())
    metrics["total_events_tracked"] = len(events)
    metrics["schema_versions"] = sorted({str(row.get("schema_version")) for row in events.values()})
    metrics["script_versions"] = sorted({str(row.get("script_version")) for row in events.values()})
    return metrics


def _qualified_rows(registry: dict) -> list[dict]:
    return [
        row for row in registry.get("events", {}).values()
        if row.get("status") == "PROCESSED" and row.get("final_disposition") == "QUALIFIED_FOR_NATIVE_CYCLE"
    ]


def _evidence_from_row(row: dict, classification: str) -> dict:
    data = row["payload"]
    return {
        "source_classification": classification,
        "signal_id": data["signal_id"], "script_version": data["script_version"],
        "setup_1h": data["setup_1h"], "entry_trigger_15m": data["entry_trigger_15m"],
        "regime_4h": data["regime_4h"], "timeframe_agreement": "4H_1H_15M",
        "technical_score": data["technical_score"],
    }


def merge_native_candidate_evidence(candidates: list, now: datetime | None = None) -> int:
    """Attach recent qualified evidence to native snapshots without duplicating
    alerts. Augmentation only — this never changes a native candidate's
    direction, gates, or whether it was selected; it only tags it for context
    the Chief AI review and later attribution can see.
    """
    settings, now = get_settings(), now or _now()
    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        rows = _qualified_rows(registry)
    attached = 0
    for candidate in candidates:
        pair = str(getattr(candidate, "primary_pair", None) or getattr(candidate, "symbol", "")).upper()
        direction = str(getattr(candidate, "trade_direction", "LONG")).upper()
        matches = []
        for row in rows:
            data = row.get("payload") or {}
            try:
                same_pair = resolve_kraken_pair(str(data.get("symbol", ""))) == pair
                age = (now - datetime.fromisoformat(str(row["received_at"]).replace("Z", "+00:00"))).total_seconds()
            except (ValueError, KeyError):
                continue
            if same_pair and str(data.get("direction", "")).upper() == direction and 0 <= age <= settings.tradingview_dedup_window_seconds:
                matches.append(row)
        if matches:
            newest = max(matches, key=lambda row: row["received_at"])
            candidate._tradingview_evidence = _evidence_from_row(newest, "TRADINGVIEW_CONFIRMED_NATIVE")
            attached += 1
    if attached:
        with registry_lock(LOCK_FILE):
            registry = load_json(INBOX_FILE) or _empty_registry()
            metrics = registry.setdefault("metrics", {})
            metrics["native_tradingview_duplicates"] = int(metrics.get("native_tradingview_duplicates", 0)) + attached
            save_json_atomic(INBOX_FILE, registry)
    return attached


def tradingview_only_candidates(
    snapshots: list,
    native_candidates: list,
    now: datetime | None = None,
) -> list:
    """Return no promoted candidates.

    A prior approximation compared a TradingView-matched snapshot only with
    the weakest native score. That could bypass the native selector's
    one-candidate-per-asset rule, contradictory-direction prevention,
    per-direction caps, and overall ranking. Because these snapshots were
    already evaluated by the native selector in this same cycle, a rejected
    snapshot cannot be promoted safely without making TradingView an
    authority. Qualified TradingView rows therefore attach only to candidates
    the native selector already admitted (merge_native_candidate_evidence).
    The arguments remain for backwards compatibility with extensions/tests.
    """
    del snapshots, native_candidates, now
    return []


def process_queued_events(
    *, client: KrakenClient | None = None,
    candidate_handler: Callable[[dict, dict], dict] | None = None,
    now: datetime | None = None, limit: int | None = None,
) -> dict:
    """Revalidate queued evidence and hand qualified candidates to existing
    lifecycle adapters. The default handler deliberately just records a
    qualified candidate for the native scanner/Chief cycle; it never creates
    an order, fill, trade, or paid AI call itself.
    """
    settings = get_settings()
    client = client or KrakenClient(timeout_seconds=settings.tradingview_kraken_timeout_seconds)
    now = now or _now()
    configured_limit = settings.tradingview_processing_batch_limit
    limit = configured_limit if limit is None else min(configured_limit, max(0, limit))
    summary = {"checked": 0, "processed": 0, "rejected": 0, "retried": 0, "qualified": 0, "poisoned": 0}
    if not settings.tradingview_v2_enabled:
        return summary

    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        _prune_terminal_events(registry, now, settings.tradingview_retention_seconds)
        summary["reclaimed"] = _reclaim_stuck_processing(registry, now, settings.tradingview_processing_timeout_seconds)
        save_json_atomic(INBOX_FILE, registry)
        keys = [key for key, row in registry.get("events", {}).items() if row.get("status") in ACTIVE][:limit]

    for key in keys:
        summary["checked"] += 1
        with registry_lock(LOCK_FILE):
            registry = load_json(INBOX_FILE) or _empty_registry()
            row = registry["events"].get(key)
            if row is None:
                continue
            attempts = int(row.get("attempts", 0)) + 1
            if attempts > settings.tradingview_max_attempts:
                row["status"] = "POISONED"
                row["final_disposition"] = "MAX_ATTEMPTS_EXCEEDED"
                row["failure_reason"] = f"exceeded {settings.tradingview_max_attempts} processing attempts"
                metrics = registry.setdefault("metrics", {})
                metrics["poisoned_events"] = int(metrics.get("poisoned_events", 0)) + 1
                save_json_atomic(INBOX_FILE, registry)
                summary["poisoned"] += 1
                continue
            row["attempts"] = attempts
            row["status"] = "PROCESSING"
            row["processing_started_at"] = now.isoformat()
            save_json_atomic(INBOX_FILE, registry)
        try:
            signal = TradingViewSignalV2.model_validate(row["payload"])
            validate_event_policy(signal, now)
            pair = resolve_kraken_pair(signal.symbol)
            ticker = client.get_ticker(pair)
            kraken_price = float(ticker["last"])
            divergence = abs(signal.tradingview_close - kraken_price) / kraken_price * 100
            if divergence > settings.tradingview_max_price_divergence_pct:
                disposition, reason = "PRICE_DIVERGENCE_REJECT", f"TradingView/Kraken divergence {divergence:.4f}%"
                summary["rejected"] += 1
            else:
                operator = get_operator_decision(now)
                if operator.effective_mode != "SEARCH" or not operator.search_allowed:
                    disposition, reason = "OPERATOR_GATE_REJECT", operator.reason
                    summary["rejected"] += 1
                else:
                    evidence = {"source": "TRADINGVIEW", "kraken_pair": pair, "kraken_price": kraken_price,
                                "price_divergence_pct": round(divergence, 6), "payload": row["payload"]}
                    result = candidate_handler(row["payload"], evidence) if candidate_handler else {"decision_id": None, "disposition": "QUALIFIED_FOR_NATIVE_CYCLE"}
                    disposition, reason = str(result.get("disposition", "QUALIFIED_FOR_NATIVE_CYCLE")), None
                    row["related_decision_id"] = result.get("decision_id")
                    summary["qualified"] += 1
            with registry_lock(LOCK_FILE):
                registry = load_json(INBOX_FILE) or _empty_registry()
                stored = registry["events"].get(key)
                if stored is None:
                    continue
                stored.update(row)
                stored["status"] = "PROCESSED"
                stored["final_disposition"] = disposition
                stored["failure_reason"] = reason
                stored["processed_at"] = now.isoformat()
                metrics = registry.setdefault("metrics", {})
                metrics["last_processed_at"] = now.isoformat()
                if disposition == "PRICE_DIVERGENCE_REJECT":
                    metrics["price_divergence_rejections"] = int(metrics.get("price_divergence_rejections", 0)) + 1
                if disposition in {"QUALIFIED_FOR_NATIVE_CYCLE", "QUALIFIED"}:
                    metrics["qualified_candidates"] = int(metrics.get("qualified_candidates", 0)) + 1
                save_json_atomic(INBOX_FILE, registry)
            summary["processed"] += 1
        except TradingViewPolicyError as exc:
            with registry_lock(LOCK_FILE):
                registry = load_json(INBOX_FILE) or _empty_registry()
                stored = registry["events"].get(key)
                if stored is not None:
                    stored["status"] = "REJECTED"
                    stored["final_disposition"] = "VALIDATION_REJECT"
                    stored["failure_reason"] = str(exc)
                    save_json_atomic(INBOX_FILE, registry)
            summary["rejected"] += 1
        except Exception as exc:  # noqa: BLE001 - a single bad event must never abort the cycle
            with registry_lock(LOCK_FILE):
                registry = load_json(INBOX_FILE) or _empty_registry()
                stored = registry["events"].get(key)
                if stored is not None:
                    stored["status"] = "RETRY"
                    # Only the exception type is persisted, not the full message:
                    # a generic except here can catch library exceptions whose
                    # text embeds request/response detail that should not be
                    # written into an audit-retained file.
                    stored["failure_reason"] = f"{type(exc).__name__} during processing (see application logs)"
                    save_json_atomic(INBOX_FILE, registry)
            logger.exception("TradingView event %s failed processing", key)
            summary["retried"] += 1
    return summary
