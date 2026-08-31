from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.exchanges.kraken import KrakenClient
from app.scanner.universe import TICKER_BATCH_SIZE, _is_excluded_market, _market_symbols
from app.services.registry_io import load_json, registry_lock, save_json_atomic
from app.services.signal_features import (
    FeatureDerivationConfig,
    ObservationSnapshot,
    QualifyingConditions,
    derive_features_for_universe,
    derive_universe_percentiles,
    snapshot_from_mapping,
)
from app.services.signal_scoring import (
    SignalQualityCandidate,
    SignalQualityConfig,
    evaluate_universe,
)


VERSION = "full-market-observation-v1"
OBSERVATION_FILE = Path("/app/data/full_market_observations.jsonl")
STATE_FILE = Path("/app/data/full_market_observation_state.json")
ALERT_GOVERNOR_STATE_FILE = Path("/app/data/full_market_observation_alert_governor_state.json")
HEARTBEAT_SECONDS = 60 * 60

# Schema 1 kept one row per symbol (latest_by_symbol) and updated it only when
# the observation was worth persisting to JSONL. Schema 2 adds a bounded
# per-symbol runtime scan history that advances on *every* scan, because
# learning persistence and feature-state persistence are different problems:
# the JSONL stream is deliberately event-sampled, and a quiet scan that is not
# worth an event still belongs in a temporal feature series.
#
# The migration is non-destructive in both directions. latest_by_symbol keeps
# its exact schema-1 semantics so the existing transition detector and its
# consumers are unaffected; an existing state file is seeded into history
# rather than discarded; and schema 2 is only ever written while
# signal_quality_v1_enabled is set, so a disabled deployment stays byte-shaped
# like schema 1. Disabling after an enabled run stops updating the history but
# never deletes it.
HISTORY_SCHEMA_VERSION = 2
DEFAULT_HISTORY_SCANS = 8
# One hour, i.e. six scans at the default cadence. Retention bounds how long
# unobserved evidence is kept; it is deliberately longer than the continuity
# window, which bounds how long a persistence chain survives a gap.
DEFAULT_STALE_HISTORY_RETENTION_SECONDS = 3600.0
MIN_PERSIST_PRICE_CHANGE_PCT = 1.0
MIN_PERSIST_LIFT_CHANGE_PCT = 0.75
MIN_PERSIST_HIGH_DISTANCE_CHANGE_PCT = 0.75
MIN_NOTIONAL_RATIO_CHANGE = 1.50


@dataclass(frozen=True)
class MarketObservation:
    version: str
    base_asset: str
    symbol: str
    kraken_public_symbol: str
    last_price: float
    volume_24h: float
    notional_24h_usd_approx: float
    high_24h: float
    low_24h: float
    lift_from_24h_low_pct: float
    distance_from_24h_high_pct: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketTransition:
    version: str
    symbol: str
    pattern: str
    score: int
    price_change_since_prior_pct: float
    lift_change_since_prior_pct: float
    lift_from_24h_low_pct: float
    distance_from_24h_high_pct: float
    liquidity_24h_usd_approx: float
    alert_tier: str
    reference_price: float = 0.0
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False

    @property
    def transition_key(self) -> str:
        score_bucket = self.score // 10 * 10
        return f"{self.pattern}:{self.alert_tier}:{score_bucket}"


@dataclass(frozen=True)
class FullMarketResult:
    observed_markets: int
    persisted_events: int
    transition_alerts: tuple[MarketTransition, ...]
    # Build 2 canonical capture source: the exact eligible Kraken spot cohort
    # already fetched by this scan. Exposing this immutable tuple prevents a
    # second scanner/network fetch and lets post-alert learning record every
    # pair considered, including markets that never became ranked candidates.
    market_observations: tuple[MarketObservation, ...] = ()
    # Signal Quality v1 leaderboard, including suppressed rows with their
    # reasons. Empty unless signal_quality_v1_enabled is set; Phase 1 ships
    # dark and the legacy Broad Watch path is untouched while it is off.
    signal_quality_candidates: tuple[SignalQualityCandidate, ...] = ()
    signal_quality_enabled: bool = False
    # Phase 3A: the exact same-scan observation price each candidate in
    # signal_quality_candidates was derived from, keyed by symbol. Read
    # straight out of the in-memory history this scan already built for
    # evaluate_signal_quality() - not a second lookup, not a later print, and
    # not a new market-data fetch. Empty whenever signal_quality_enabled is
    # False. Exists so telemetry (Phase 3A) can record a real decision price
    # without SignalQualityCandidate itself needing one.
    signal_quality_reference_prices: Mapping[str, float] = field(default_factory=dict)


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def _pct(current: float, reference: float) -> float:
    if not _finite(current, reference) or reference <= 0:
        return 0.0
    result = (current / reference - 1.0) * 100.0
    return result if math.isfinite(result) else 0.0


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def collect_full_market_observations(client: KrakenClient | None = None) -> list[MarketObservation]:
    """Observe every eligible Kraken spot base asset without tradeability filtering."""
    client = client or KrakenClient()
    pair_details = client.get_asset_pairs()
    markets: list[tuple[str, str, str, str, str]] = []
    for pair_id, details in pair_details.items():
        if _is_excluded_market(details):
            continue
        symbols = _market_symbols(details)
        if symbols is None:
            continue
        base, quote = symbols
        altname = str(details.get("altname", pair_id)).upper()
        markets.append((pair_id, altname, f"{base}{quote}", base, quote))

    tickers: dict[str, dict[str, float]] = {}
    pair_ids = sorted({row[0] for row in markets})
    for start in range(0, len(pair_ids), TICKER_BATCH_SIZE):
        try:
            tickers.update(client.get_tickers(pair_ids[start:start + TICKER_BATCH_SIZE]))
        except Exception:
            continue

    by_asset: dict[str, tuple[str, str, str, str, str, dict[str, float]]] = {}
    for pair_id, altname, display_pair, base, quote in markets:
        ticker = tickers.get(pair_id) or tickers.get(altname) or tickers.get(display_pair)
        if ticker is None:
            continue
        current = by_asset.get(base)
        if current is None or (current[4] != "USD" and quote == "USD"):
            by_asset[base] = (pair_id, altname, display_pair, base, quote, ticker)

    rows: list[MarketObservation] = []
    for _, _, display_pair, base, quote, ticker in by_asset.values():
        try:
            last = float(ticker.get("last") or 0.0)
            high = float(ticker.get("high_24h") or 0.0)
            low = float(ticker.get("low_24h") or 0.0)
            volume = float(ticker.get("volume_24h") or 0.0)
        except (TypeError, ValueError):
            continue
        if not _finite(last, high, low, volume):
            continue
        if min(last, high, low) <= 0 or high < low or volume <= 0:
            continue
        notional = last * volume
        lift = _pct(last, low)
        distance = max(0.0, (high - last) / last * 100.0)
        if not _finite(notional, lift, distance):
            continue
        rows.append(MarketObservation(
            version=VERSION,
            base_asset=base,
            symbol=display_pair,
            kraken_public_symbol=f"{base}/{quote}",
            last_price=round(last, 12),
            volume_24h=round(volume, 8),
            notional_24h_usd_approx=round(notional, 2),
            high_24h=round(high, 12),
            low_24h=round(low, 12),
            lift_from_24h_low_pct=round(lift, 6),
            distance_from_24h_high_pct=round(distance, 6),
        ))
    rows.sort(key=lambda row: row.base_asset)
    return rows


def _notional_ratio(current: float, previous: float) -> float:
    if not _finite(current, previous) or current <= 0 or previous <= 0:
        return 1.0
    ratio = max(current / previous, previous / current)
    return ratio if math.isfinite(ratio) else 1.0


def _previous_finite(previous: dict[str, Any], *keys: str) -> tuple[float, ...] | None:
    values: list[float] = []
    try:
        for key in keys:
            value = float(previous.get(key) or 0.0)
            if not math.isfinite(value):
                return None
            values.append(value)
    except (TypeError, ValueError):
        return None
    return tuple(values)


def _should_persist(current: MarketObservation, previous: dict[str, Any] | None, now: datetime) -> tuple[bool, str]:
    if previous is None:
        return True, "FIRST_OBSERVATION"
    previous_at = _parse_time(previous.get("recorded_at"))
    if previous_at is None or (now - previous_at).total_seconds() >= HEARTBEAT_SECONDS:
        return True, "HEARTBEAT"
    parsed = _previous_finite(
        previous,
        "last_price",
        "lift_from_24h_low_pct",
        "distance_from_24h_high_pct",
        "notional_24h_usd_approx",
    )
    if parsed is None:
        return True, "INVALID_PRIOR_STATE"
    prior_price, prior_lift, prior_distance, prior_notional = parsed
    if abs(_pct(current.last_price, prior_price)) >= MIN_PERSIST_PRICE_CHANGE_PCT:
        return True, "PRICE_CHANGE"
    if abs(current.lift_from_24h_low_pct - prior_lift) >= MIN_PERSIST_LIFT_CHANGE_PCT:
        return True, "LIFT_CHANGE"
    if abs(current.distance_from_24h_high_pct - prior_distance) >= MIN_PERSIST_HIGH_DISTANCE_CHANGE_PCT:
        return True, "HIGH_DISTANCE_CHANGE"
    if _notional_ratio(current.notional_24h_usd_approx, prior_notional) >= MIN_NOTIONAL_RATIO_CHANGE:
        return True, "LIQUIDITY_CHANGE"
    return False, "NO_MEANINGFUL_CHANGE"


def load_history_state(
    state: dict[str, Any],
    *,
    history_scans: int = DEFAULT_HISTORY_SCANS,
) -> dict[str, list[ObservationSnapshot]]:
    """Read schema-2 history, migrating schema-1 state in place if needed.

    Migration is additive and lossless in the direction that matters: an
    existing ``latest_by_symbol`` row seeds a one-element history so a
    long-running deployment starts from real evidence instead of throwing away
    what it already observed. Unparseable rows are skipped rather than
    poisoning the series.
    """
    retain = max(2, int(history_scans))
    raw_history = state.get("history_by_symbol")
    history: dict[str, list[ObservationSnapshot]] = {}

    if isinstance(raw_history, dict):
        for key, rows in raw_history.items():
            if not isinstance(rows, list):
                continue
            snapshots = [
                snapshot
                for snapshot in (snapshot_from_mapping(row) for row in rows if isinstance(row, dict))
                if snapshot is not None
            ]
            snapshots.sort(key=lambda row: row.observed_at)
            if snapshots:
                history[str(key).upper()] = snapshots[-retain:]
        return history

    # Schema 1 (or an absent history block): seed from the persisted latest row.
    latest = state.get("latest_by_symbol") or {}
    if isinstance(latest, dict):
        for key, row in latest.items():
            if not isinstance(row, dict):
                continue
            snapshot = snapshot_from_mapping(row, observed_at=row.get("recorded_at"))
            if snapshot is not None:
                history[str(key).upper()] = [snapshot]
    return history


def _serialise_history(
    history: dict[str, list[ObservationSnapshot]],
    *,
    history_scans: int,
) -> dict[str, list[dict[str, Any]]]:
    retain = max(2, int(history_scans))
    return {
        key: [snapshot.as_dict() for snapshot in rows[-retain:]]
        for key, rows in history.items()
        if rows
    }


def _append_history(
    history: dict[str, list[ObservationSnapshot]],
    key: str,
    snapshot: ObservationSnapshot,
    *,
    history_scans: int,
) -> None:
    """Append one runtime scan to a symbol's bounded ring buffer.

    Called for every observation on every scan, independently of
    ``_should_persist``. That independence is the point: persistence credit
    must come from consecutive runtime scans, not from consecutive JSONL rows.
    """
    retain = max(2, int(history_scans))
    rows = history.setdefault(key, [])
    if rows and snapshot.observed_at <= rows[-1].observed_at:
        # A replayed or out-of-order scan must not create a fake interval.
        rows[-1] = snapshot
    else:
        rows.append(snapshot)
    if len(rows) > retain:
        del rows[: len(rows) - retain]


def _snapshot(observation: MarketObservation, now: datetime) -> ObservationSnapshot:
    return ObservationSnapshot(
        observed_at=now,
        last_price=observation.last_price,
        volume_24h=observation.volume_24h,
        notional_24h_usd_approx=observation.notional_24h_usd_approx,
        high_24h=observation.high_24h,
        low_24h=observation.low_24h,
        lift_from_24h_low_pct=observation.lift_from_24h_low_pct,
        distance_from_24h_high_pct=observation.distance_from_24h_high_pct,
    )


def prune_stale_history(
    history: dict[str, list[ObservationSnapshot]],
    *,
    now: datetime,
    retention_seconds: float,
) -> dict[str, list[ObservationSnapshot]]:
    """Drop only symbols unobserved for longer than the retention window.

    Absence from a single scan is not evidence of delisting.
    ``collect_full_market_observations`` fail-softs on a ticker batch error, so
    a transient Kraken fault, a malformed response or a network gap all look
    identical to a symbol disappearing. Pruning on presence would let any of
    them silently erase a symbol's feature history, reset its persistence and
    make its return look like a first observation.

    Retention is a bound on evidence, not on credit: a symbol that returns
    after a dropout keeps its snapshots, but the continuity check in
    ``derive_symbol_features`` still severs its persistence chain because the
    elapsed gap exceeds the continuity window. Genuinely delisted markets age
    out once the window passes, so state stays bounded.
    """
    window = max(0.0, float(retention_seconds))
    retained: dict[str, list[ObservationSnapshot]] = {}
    for key, rows in history.items():
        if not rows:
            continue
        if (now - rows[-1].observed_at).total_seconds() <= window:
            retained[key] = rows
    return retained


def evaluate_signal_quality(
    history: dict[str, list[ObservationSnapshot]],
    *,
    settings: Any = None,
    observed_symbols: set[str] | None = None,
) -> tuple[SignalQualityCandidate, ...]:
    """Run the Phase 1 two-pass pipeline over the current scan history.

    Pass one derives per-symbol temporal features; pass two ranks them against
    the whole observed universe. Relative strength is computed across every
    market with derivable features - not only transition candidates - because
    ranking inside an already-filtered set makes the percentile
    selection-biased by construction.

    ``observed_symbols`` restricts scoring to markets seen in the current scan.
    Retained-but-absent symbols keep their history for when they return, but
    must not be scored or ranked from stale snapshots as though they were
    current observations.
    """
    if settings is None:
        try:
            from app.core.config import get_settings

            settings = get_settings()
        except Exception:
            # Without a readable configuration the feature stays dark, which is
            # the Phase 1 default anyway.
            return ()

    config = SignalQualityConfig.from_settings(settings)
    if not config.enabled:
        return ()

    feature_config = FeatureDerivationConfig(
        nominal_interval_seconds=float(getattr(settings, "signal_quality_scan_interval_seconds", 600)),
        continuity_multiplier=float(getattr(settings, "signal_quality_continuity_multiplier", 2.5)),
        qualifying=QualifyingConditions(),
        run_up_window_scans=int(getattr(settings, "signal_quality_history_scans", DEFAULT_HISTORY_SCANS)),
    )
    scored = (
        history
        if observed_symbols is None
        else {key: rows for key, rows in history.items() if key in observed_symbols}
    )
    features = derive_features_for_universe(scored, config=feature_config)
    percentiles = derive_universe_percentiles(features)
    return evaluate_universe(features, percentiles, config=config)


def _transition(current: MarketObservation, previous: dict[str, Any] | None) -> MarketTransition | None:
    if previous is None:
        return None
    current_values = (
        current.last_price,
        current.notional_24h_usd_approx,
        current.lift_from_24h_low_pct,
        current.distance_from_24h_high_pct,
    )
    if not _finite(*current_values):
        return None
    parsed = _previous_finite(previous, "last_price", "lift_from_24h_low_pct")
    if parsed is None:
        return None
    prior_price, prior_lift = parsed
    if prior_price <= 0:
        return None
    price_change = _pct(current.last_price, prior_price)
    lift_change = current.lift_from_24h_low_pct - prior_lift
    near_high = current.distance_from_24h_high_pct
    if not _finite(price_change, lift_change, near_high):
        return None

    pattern: str | None = None
    if prior_lift <= 3.0 and current.lift_from_24h_low_pct >= 4.0 and lift_change >= 3.0 and near_high <= 4.0:
        pattern = "COMPRESSION_RELEASE"
    elif prior_lift >= 5.0 and lift_change >= 2.0 and price_change >= 1.5 and near_high <= 4.0:
        pattern = "REACCELERATION"
    elif current.lift_from_24h_low_pct >= 5.0 and lift_change >= 1.5 and price_change >= 1.25 and near_high <= 5.0:
        pattern = "PROGRESSIVE_EXPANSION"
    if pattern is None:
        return None

    score = 0.0
    score += min(35.0, max(0.0, lift_change) * 8.0)
    score += min(25.0, max(0.0, price_change) * 7.0)
    score += max(0.0, 20.0 - near_high * 4.0)
    if current.notional_24h_usd_approx >= 1_000_000:
        score += 15.0
    elif current.notional_24h_usd_approx >= 250_000:
        score += 12.0
    elif current.notional_24h_usd_approx >= 50_000:
        score += 7.0
    else:
        score += max(0.0, min(4.0, math.log10(max(current.notional_24h_usd_approx, 1.0))))
    if not math.isfinite(score):
        return None
    bounded = int(round(max(0.0, min(100.0, score))))
    if bounded < 60:
        return None

    alert_tier = "DEEP_REVIEW" if current.notional_24h_usd_approx >= 250_000 and bounded >= 75 else "WATCH_ONLY"
    return MarketTransition(
        version=VERSION,
        symbol=current.symbol,
        pattern=pattern,
        score=bounded,
        price_change_since_prior_pct=round(price_change, 4),
        lift_change_since_prior_pct=round(lift_change, 4),
        lift_from_24h_low_pct=current.lift_from_24h_low_pct,
        distance_from_24h_high_pct=current.distance_from_24h_high_pct,
        liquidity_24h_usd_approx=current.notional_24h_usd_approx,
        alert_tier=alert_tier,
        reference_price=current.last_price,
    )


def process_full_market_observations(
    *,
    client: KrakenClient | None = None,
    now: datetime | None = None,
    observation_file: Path | None = None,
    state_file: Path | None = None,
    settings: Any = None,
) -> FullMarketResult:
    """Capture broad market evidence and derive high-signal transition watches."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = observation_file or OBSERVATION_FILE
    state_target = state_file or STATE_FILE
    client = client or KrakenClient()
    observations = collect_full_market_observations(client)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"

    if settings is None:
        try:
            from app.core.config import get_settings

            settings = get_settings()
        except Exception:
            settings = None
    # Dark mode is a hard branch, not a filter at the end. While the flag is
    # off, Signal Quality v1 performs no state migration, writes no new state
    # keys, derives no features and scores nothing - the scan is operationally
    # identical to pre-Phase-1 behaviour.
    signal_quality_enabled = bool(getattr(settings, "signal_quality_v1_enabled", False))
    history_scans = int(getattr(settings, "signal_quality_history_scans", DEFAULT_HISTORY_SCANS) or DEFAULT_HISTORY_SCANS)
    stale_history_retention_seconds = float(
        getattr(settings, "signal_quality_stale_history_retention_seconds", None)
        or DEFAULT_STALE_HISTORY_RETENTION_SECONDS
    )

    persisted = 0
    transitions: list[MarketTransition] = []
    candidates: tuple[SignalQualityCandidate, ...] = ()
    history: dict[str, list[ObservationSnapshot]] = {}
    with registry_lock(lock):
        state = load_json(state_target)
        latest = state.get("latest_by_symbol") or {}
        if not isinstance(latest, dict):
            raise ValueError("full-market latest_by_symbol state must be an object")
        if signal_quality_enabled:
            # First enabled scan seeds from latest_by_symbol; later scans read
            # back the schema-2 block. Both paths are idempotent.
            history = load_history_state(state, history_scans=history_scans)
        observed_keys: set[str] = set()
        with target.open("a", encoding="utf-8") as handle:
            for observation in observations:
                key = observation.symbol.upper()
                previous = latest.get(key)
                transition = _transition(observation, previous if isinstance(previous, dict) else None)
                if transition is not None:
                    transitions.append(transition)

                if signal_quality_enabled:
                    # Feature state advances on every runtime scan, before and
                    # independently of the JSONL persistence decision below.
                    observed_keys.add(key)
                    _append_history(history, key, _snapshot(observation, now), history_scans=history_scans)

                should_persist, reason = _should_persist(observation, previous if isinstance(previous, dict) else None, now)
                if not should_persist:
                    continue
                payload = {
                    "record_type": "FULL_MARKET_OBSERVATION",
                    "observed_at": now.isoformat(),
                    "capture_reason": reason,
                    **observation.as_dict(),
                    "learning_only": True,
                    "trade_authority_changed": False,
                    "production_execution_gate_changed": False,
                }
                handle.write(json.dumps(payload, sort_keys=True, default=str, allow_nan=False) + "\n")
                handle.flush()
                latest[key] = {**observation.as_dict(), "recorded_at": now.isoformat()}
                persisted += 1
        state["latest_by_symbol"] = latest
        if signal_quality_enabled:
            # Age-based, not presence-based: a symbol missing from this scan
            # because of a fail-soft ticker error keeps its history, while a
            # genuinely delisted market ages out and state stays bounded.
            history = prune_stale_history(
                history, now=now, retention_seconds=stale_history_retention_seconds
            )
            state["history_by_symbol"] = _serialise_history(history, history_scans=history_scans)
            state["schema_version"] = HISTORY_SCHEMA_VERSION
        # When disabled, any history written by an earlier enabled run is left
        # exactly as it is. Dark mode means no new Signal Quality mutation, not
        # a destructive rollback of state the operator already accumulated.
        state["last_scan_at"] = now.isoformat()
        state["observed_markets_last_scan"] = len(observations)
        save_json_atomic(state_target, state)

    reference_prices: dict[str, float] = {}
    if signal_quality_enabled:
        # Scoring is pure CPU over an in-memory copy, so it runs outside the
        # registry lock rather than holding it across the whole universe.
        try:
            candidates = evaluate_signal_quality(
                history, settings=settings, observed_symbols=observed_keys
            )
        except Exception as exc:
            # Advisory scoring must never break market observation or the
            # learning stream it feeds.
            candidates = ()
            print("Signal Quality v1: fail-soft", type(exc).__name__)

        try:
            # Same-scan price each candidate was actually derived from - the
            # last entry history[symbol] already holds after this scan's
            # _append_history call, not a second lookup or a later print.
            # Phase 3A telemetry (app/services/decision_telemetry.py) is the
            # only consumer; nothing here changes scoring or stage output.
            reference_prices = {
                candidate.symbol: history[candidate.symbol][-1].last_price
                for candidate in candidates
                if history.get(candidate.symbol)
            }
        except Exception as exc:
            reference_prices = {}
            print("Signal Quality v1 reference prices: fail-soft", type(exc).__name__)

    transitions.sort(key=lambda row: (-row.score, -row.liquidity_24h_usd_approx, row.symbol))

    # Production/default storage closes the previously missing Full-Market
    # outcome loop. Custom paths are used heavily by unit tests; do not write to
    # unrelated default registries when a caller explicitly isolates storage.
    if observation_file is None and state_file is None:
        try:
            from app.services.full_market_transition_learning import (
                capture_full_market_transitions,
                observe_due_full_market_transition_outcomes,
            )

            capture_full_market_transitions(transitions, now=now)
            observe_due_full_market_transition_outcomes(now=now, client=client)
        except Exception as exc:
            # Learning is never allowed to break market observation or change
            # trading authority. The calling job already reports scan health.
            print("Full-market transition learning: fail-soft", type(exc).__name__)

    return FullMarketResult(
        observed_markets=len(observations),
        persisted_events=persisted,
        transition_alerts=tuple(transitions),
        market_observations=tuple(observations),
        signal_quality_candidates=candidates,
        signal_quality_enabled=signal_quality_enabled,
        signal_quality_reference_prices=reference_prices,
    )
