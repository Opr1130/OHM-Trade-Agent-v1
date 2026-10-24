"""Read-only native O'Pip intelligence for a Tradevisor observation.

Tradevisor supplies a *claimed* BUY/SELL event. This module attaches the native
evidence O'Pip already owns so an operator can see independent corroboration,
contradiction and market-quality concerns beside that claim.

Rules honoured here
-------------------
* Nothing in this module decides, sizes, or authorizes a trade.
* No indicator, ranking, breadth or risk formula is re-implemented. Every field
  is either delegated to its existing owner (``market_scanner.analyze_symbol``,
  ``market_regime.evaluate_market_regime``, ``chase_risk.assess_chase_risk``,
  ``execution_validation``) or explicitly declared unavailable.
* An unavailable component is never reported as corroboration: it carries
  ``status = UNAVAILABLE`` and a machine-readable reason.
* A component whose input contract does not fit a single-symbol evaluation is
  declared incompatible rather than approximated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from app.scanner.market_regime import evaluate_market_regime
from app.scanner.models import MarketSnapshot
from app.services.chase_risk import ChaseRiskInput, assess_chase_risk

CONTEXT_SCHEMA_VERSION = "tradevisor-native-context-v1"
DISCOVERY_SCHEMA_VERSION = "tradevisor-discovery-coverage-v1"

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"

BTC_SOURCE = "app.scanner.market_scanner.analyze_symbol"
BREADTH_SOURCE = "app.scanner.market_regime.evaluate_market_regime"
CHASE_SOURCE = "app.services.chase_risk.assess_chase_risk"
RANKING_SOURCE = "app.scanner.intelligence_ranking"
SIGNAL_QUALITY_SOURCE = "app.services.signal_scoring"
LIQUIDITY_SOURCE = "app.scanner.execution_validation.evaluate_execution"


def _iso(now: datetime | None) -> str | None:
    if now is None:
        return None
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("native-context timestamps must be timezone-aware")
    return now.astimezone(timezone.utc).isoformat()


def _available(
    source: str,
    value: Any,
    *,
    now: datetime | None,
    market: str | None = None,
    freshness_seconds: float | None = None,
    limitations: Iterable[str] = (),
) -> dict:
    return {
        "source": source,
        "status": AVAILABLE,
        "value": value,
        "observed_at": _iso(now),
        "freshness_seconds": freshness_seconds,
        "market": market,
        "reason": None,
        "limitations": list(limitations),
    }


def _unavailable(
    source: str,
    reason: str,
    *,
    market: str | None = None,
    limitations: Iterable[str] = (),
) -> dict:
    return {
        "source": source,
        "status": UNAVAILABLE,
        "value": None,
        "observed_at": None,
        "freshness_seconds": None,
        "market": market,
        "reason": reason,
        "limitations": list(limitations),
    }


def _resolve_snapshot(result: Any) -> tuple[MarketSnapshot | None, str | None]:
    """Accept a snapshot or the scanner's ``(status, snapshot, reason)`` tuple.

    Anything else is treated as unavailable rather than trusted: a provider that
    returns an unexpected object must not become market evidence.
    """
    if isinstance(result, tuple) and len(result) == 3:
        status, snapshot, reason = result
        if status != "ok" or snapshot is None:
            return None, str(reason or status)
        return snapshot, None
    if isinstance(result, MarketSnapshot):
        return result, None
    if result is None:
        return None, "NO_SNAPSHOT"
    return None, f"UNSUPPORTED_PROVIDER_RESULT:{type(result).__name__}"


def btc_regime_context(
    *,
    provider: Callable[[str], Any] | None,
    market: str,
    now: datetime | None,
) -> dict:
    """BTC context from the native scanner (the same owner as any other symbol).

    This is BTC's own completed-1H EMA stack, not a new broad-market formula.
    Universe breadth is a separate field and stays unavailable when its sample
    is too small.
    """
    if not market:
        return _unavailable(BTC_SOURCE, "DISABLED_BY_CONFIGURATION")
    if provider is None:
        return _unavailable(BTC_SOURCE, "NO_PROVIDER", market=market)
    try:
        snapshot, reason = _resolve_snapshot(provider(market))
    except Exception as exc:  # noqa: BLE001 - context must never break evaluation
        return _unavailable(BTC_SOURCE, f"{type(exc).__name__}: {exc}", market=market)
    if snapshot is None:
        return _unavailable(BTC_SOURCE, reason or "NO_SNAPSHOT", market=market)
    trend = str(getattr(snapshot, "trend", "") or "").upper()
    if not trend:
        return _unavailable(BTC_SOURCE, "BTC_TREND_UNAVAILABLE", market=market)
    return _available(
        BTC_SOURCE,
        trend,
        now=now,
        market=market,
        limitations=("BTC_1H_EMA_STACK_ONLY", "NOT_BROAD_MARKET_BREADTH"),
    )


def market_breadth_context(
    snapshots: Iterable[MarketSnapshot] | None,
    *,
    now: datetime | None,
) -> dict:
    """Delegate breadth to the native regime owner.

    ``evaluate_market_regime`` itself refuses fewer than 30 validated
    snapshots, so a single-symbol evaluation is reported as unavailable by the
    owner rather than approximated here.
    """
    if snapshots is None:
        return _unavailable(
            BREADTH_SOURCE,
            "REQUIRES_MIN_30_VALIDATED_SNAPSHOTS: no native universe was supplied",
        )
    sample = list(snapshots)
    try:
        context = evaluate_market_regime(sample)
    except Exception as exc:  # noqa: BLE001 - context must never break evaluation
        return _unavailable(BREADTH_SOURCE, f"{type(exc).__name__}: {exc}")
    if getattr(context, "status", UNAVAILABLE) != AVAILABLE:
        return _unavailable(
            BREADTH_SOURCE,
            "REQUIRES_MIN_30_VALIDATED_SNAPSHOTS: owner returned "
            f"{getattr(context, 'regime', UNAVAILABLE)} at sample_size={len(sample)}",
        )
    return _available(
        BREADTH_SOURCE,
        {
            "regime": getattr(context, "regime", None),
            "breadth_score": getattr(context, "breadth_score", None),
            "sample_size": getattr(context, "sample_size", len(sample)),
        },
        now=now,
    )


def chase_risk_context(snapshot: MarketSnapshot, *, now: datetime | None) -> dict:
    """Late-entry/chase risk from the native owner, with its inputs disclosed."""
    try:
        price = float(
            getattr(snapshot, "ticker_last", 0.0)
            or getattr(snapshot, "last_price", 0.0)
            or 0.0
        )
    except (TypeError, ValueError):
        return _unavailable(CHASE_SOURCE, "PRICE_NOT_NUMERIC")
    if price <= 0:
        return _unavailable(CHASE_SOURCE, "NO_EXECUTABLE_PRICE")

    recent_high = getattr(snapshot, "recent_24h_high", None)
    try:
        recent_high_value = float(recent_high) if recent_high else None
    except (TypeError, ValueError):
        recent_high_value = None

    try:
        assessment = assess_chase_risk(
            ChaseRiskInput(current_price=price, recent_high=recent_high_value)
        )
    except Exception as exc:  # noqa: BLE001 - context must never break evaluation
        return _unavailable(CHASE_SOURCE, f"{type(exc).__name__}: {exc}")

    # Disclose exactly which inputs the native scorer did not receive, so a LOW
    # band is never mistaken for a complete assessment.
    unsupplied = (
        "breakout_level",
        "lift_from_24h_low_pct",
        "move_completed_fraction_pct",
        "persistence_scans",
        "exhaustion_penalty",
        "retest_state",
    )
    return _available(
        CHASE_SOURCE,
        {
            "band": assessment.band,
            "score": assessment.score,
            "late_entry": assessment.late_entry,
            "retest_available": assessment.retest_available,
            "distance_from_recent_high_pct": assessment.distance_from_recent_high_pct,
            "reasons": list(assessment.reasons),
            "advisory_only": bool(assessment.advisory_only),
        },
        now=now,
        limitations=[f"INPUT_NOT_SUPPLIED:{name}" for name in unsupplied],
    )


def signal_quality_context() -> dict:
    """Declared incompatible with a single-symbol evaluation."""
    return _unavailable(
        SIGNAL_QUALITY_SOURCE,
        "REQUIRES_SCAN_SYMBOL_FEATURES: stage/exhaustion scoring needs the "
        "native scan's SymbolFeatures, which one snapshot cannot supply",
    )


def opportunity_ranking_context() -> dict:
    """Declared incompatible with a single-symbol evaluation."""
    return _unavailable(
        RANKING_SOURCE,
        "REQUIRES_UNIVERSE_CANDIDATE_SET: ranking orders a scan candidate set, "
        "not one externally claimed observation",
    )


def liquidity_context(execution: Any, *, now: datetime | None) -> dict:
    """Executable spread/depth already measured by the native owner."""
    if execution is None:
        return _unavailable(LIQUIDITY_SOURCE, "EXECUTION_EVIDENCE_UNAVAILABLE")
    return _available(
        LIQUIDITY_SOURCE,
        {
            "status": getattr(execution, "status", None),
            "book_coverage_status": getattr(execution, "book_coverage_status", None),
            "spread_bps": getattr(execution, "spread_bps", None),
            "round_trip_market_drag_pct": getattr(
                execution, "estimated_visible_round_trip_market_drag_pct", None
            ),
            "warnings": list(getattr(execution, "warnings", []) or []),
        },
        now=now,
        freshness_seconds=getattr(execution, "latest_trade_age_seconds", None),
    )


def build_native_context(
    *,
    snapshot: MarketSnapshot,
    execution: Any,
    now: datetime,
    btc_provider: Callable[[str], Any] | None = None,
    btc_market: str = "XBTUSD",
    native_snapshots: Iterable[MarketSnapshot] | None = None,
) -> dict:
    """Assemble the native context block attached to one observation."""
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "generated_at": _iso(now),
        "authority": "READ_ONLY_CONTEXT",
        "btc_regime": btc_regime_context(
            provider=btc_provider, market=btc_market, now=now
        ),
        "market_breadth": market_breadth_context(
            [snapshot] if native_snapshots is None else native_snapshots, now=now
        ),
        "chase_risk": chase_risk_context(snapshot, now=now),
        "signal_quality": signal_quality_context(),
        "opportunity_ranking": opportunity_ranking_context(),
        "liquidity": liquidity_context(execution, now=now),
    }


def observed_markets_from_records(records: Iterable[object] | None) -> set[str]:
    """Markets that actually produced a durable Tradevisor observation.

    Reads the rows the inbox already holds; it never creates, infers or replays a
    signal, and malformed rows are ignored rather than guessed at.
    """
    markets: set[str] = set()
    for row in records or ():
        if not isinstance(row, dict):
            continue
        signal = row.get("signal")
        if not isinstance(signal, dict):
            continue
        market = str(signal.get("market") or "").strip().upper()
        if market:
            markets.add(market)
    return markets


def discovery_coverage(
    *,
    native_markets: Iterable[str] | None,
    config: Any,
    observed_markets: Iterable[str] | None = None,
) -> dict:
    """Separate the four coin populations without inventing anything.

    ``native_markets`` is the eligible market list produced by the native scan
    universe (``app.scanner.universe``); it is passed in rather than fetched, so
    this diagnostic performs no network call and cannot become a second scanner.
    ``observed_markets`` comes from durable inbox rows.

    The four populations are kept distinct on purpose:

    1. discovered by O'Pip and configured for an external TradingView alert;
    2. configured for an external alert but absent from the native universe;
    3. markets that actually produced an observation;
    4. native markets with **no** TradingView coverage at all.
    """
    configured: dict[str, tuple[str, str]] = {}
    for (exchange, symbol), (_instrument, market) in getattr(
        config, "instruments", {}
    ).items():
        configured[str(market).strip().upper()] = (exchange, symbol)

    native = (
        None
        if native_markets is None
        else {str(item).strip().upper() for item in native_markets if str(item).strip()}
    )
    observed = {
        str(item).strip().upper() for item in (observed_markets or ()) if str(item).strip()
    }

    universe = set(configured) | observed | (native or set())
    rows: list[dict] = []
    for market in sorted(universe):
        in_native = None if native is None else market in native
        rows.append(
            {
                "market": market,
                "native_discovered": in_native,
                "tradingview_configured": market in configured,
                "tradevisor_observed": market in observed,
                "tradingview_coverage_gap": (
                    None if in_native is None else (in_native and market not in configured)
                ),
                "configured_source": configured.get(market, (None, None))[0],
                "configured_symbol": configured.get(market, (None, None))[1],
            }
        )

    counts: dict[str, int | None] = {
        "native_discovered": None if native is None else len(native),
        "tradingview_configured": len(configured),
        "tradevisor_observed": len(observed),
        "configured_not_native": (
            None if native is None else sum(1 for m in configured if m not in native)
        ),
        "native_without_tradingview_coverage": (
            None if native is None else sum(1 for m in native if m not in configured)
        ),
    }
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "authority": "READ_ONLY_DIAGNOSTIC",
        "native_universe": "UNAVAILABLE" if native is None else "AVAILABLE",
        "native_universe_size": None if native is None else len(native),
        "counts": counts,
        "rows": rows,
        "ranking": {
            "status": UNAVAILABLE,
            "source": RANKING_SOURCE,
            "reason": (
                "REQUIRES_SCAN_CANDIDATE_SET: ranking orders the native scan's "
                "candidate set; reusing it here would let an external observation "
                "compete for native admission, which is an owner architecture "
                "decision rather than an integration detail"
            ),
        },
    }


def instrument_coverage(
    config: Any,
    *,
    known_markets: Iterable[str] | None = None,
) -> dict:
    """Report which configured Tradevisor symbols have a known Kraken market.

    No exchange client is created here: this path has no public
    venue-catalogue call, so without a supplied catalogue every row is
    ``UNVERIFIED_NO_VENUE_CATALOGUE`` rather than an assumed success. A typo in
    the operator mapping therefore stays visible instead of silently becoming a
    permanent ``MARKET_DATA_UNAVAILABLE`` on every alert.
    """
    catalogue = (
        None
        if known_markets is None
        else {str(item).strip().upper() for item in known_markets if str(item).strip()}
    )
    rows: list[dict] = []
    for (exchange, symbol), (instrument, market) in sorted(
        getattr(config, "instruments", {}).items()
    ):
        if catalogue is None:
            status = "UNVERIFIED_NO_VENUE_CATALOGUE"
            reason = "no Kraken venue catalogue supplied; support is not assumed"
        elif market.upper() in catalogue:
            status, reason = "SUPPORTED", None
        else:
            status = "UNSUPPORTED"
            reason = "market is absent from the supplied Kraken venue catalogue"
        rows.append(
            {
                "exchange": exchange,
                "source_symbol": symbol,
                "instrument": instrument,
                "market": market,
                "status": status,
                "reason": reason,
            }
        )

    statuses = {row["status"] for row in rows}
    if not rows:
        overall = "EMPTY"
    elif catalogue is None:
        overall = "UNVERIFIED"
    elif statuses == {"SUPPORTED"}:
        overall = "VERIFIED"
    elif "SUPPORTED" in statuses:
        overall = "PARTIAL"
    else:
        overall = "UNSUPPORTED"
    return {
        "schema_version": "tradevisor-instrument-coverage-v1",
        "status": overall,
        "catalogue_supplied": catalogue is not None,
        "instruments": rows,
    }
