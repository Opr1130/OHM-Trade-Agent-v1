from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from app.exchanges.kraken import KrakenClient
from app.opip.early.flags import early_selector_promoted, early_validation_parity_enabled
from app.opip.early.shadow_observer import (
    candidate_features_from_movers,
    load_observation_history,
    observe_scan_shadow,
    persist_shadow_rows,
)
from app.opip.early.cohort_selector import select_early_candidates
from app.opip.early.stage0_evidence import (
    SELECTOR_PRODUCTION_COARSE,
    SELECTOR_PROMOTED_COHORT,
    STAGE0_EVIDENCE_SCHEMA_VERSION,
    ScoreComponents,
    Stage0DecisionFeatures,
    build_advanced_metadata,
    build_below_threshold_metadata,
    build_coarse_status_metadata,
    build_rank_limit_metadata,
    build_selector_comparison_metadata,
    rank_contexts_for_ranked,
    rank_contexts_for_truncation,
)
from app.opip.early.taxonomy import (
    EvidenceGrade,
    MarketPhase,
    compare_extension_definitions,
    resolve_market_phase,
    resolve_operator_disposition,
)
from app.opip.early.validation_parity import evaluate_early_watch_validations
from app.scanner.market_scanner import analyze_symbol
from app.scanner.models import MarketSnapshot
from app.scanner.universe import (
    TICKER_BATCH_SIZE,
    UniverseAsset,
    _is_excluded_market,
    _market_symbols,
)
from app.services.asset_display_identity import VERIFIED_MAPPING_STATUSES
from app.services.notification_policy import record_emitted, should_emit
from app.services.telegram_delivery import (
    record_telegram_not_eligible,
    record_telegram_suppression,
    send_tracked_telegram,
)

VERSION = "movement-discovery-v2.1"
WATCH = "WATCH"
READY = "READY"
ENTRY_BREAKOUT = "BREAKOUT_ENTRY_POSSIBLE"
ENTRY_PULLBACK = "WAIT_FOR_PULLBACK"
ENTRY_WATCH = "WATCH_ONLY"
ENTRY_TOO_EXTENDED = "TOO_EXTENDED"
ENTRY_HIGH_RISK = "HIGH_RISK_WATCH_ONLY"
MOMENTUM_ACCELERATING = "ACCELERATING"
MOMENTUM_STEADY = "STEADY"
MOMENTUM_DECELERATING = "DECELERATING"
DEFAULT_DEEP_CANDIDATES = 40
MIN_COARSE_LIFT_FROM_24H_LOW_PCT = 2.0
MAX_COARSE_DISTANCE_FROM_24H_HIGH_PCT = 6.0
MIN_DISCOVERY_NOTIONAL_USD = 2_500.0
# Named for Phase 0A evidence only. Same value the deep evaluator has always
# used; naming it lets a BELOW_THRESHOLD row record the threshold it missed.
MIN_DEEP_DISCOVERY_SCORE = 45
DEFAULT_LEARNING_PATH = "/app/data/movement_discovery_v2_1.jsonl"


logger = logging.getLogger(__name__)
ScreeningCallback = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class FlowEvidence:
    available: bool = False
    bias: str = "NEUTRAL"
    strength: int = 0
    source: str = "NONE"
    freshness_seconds: float | None = None


@dataclass(frozen=True)
class WhaleEvidence:
    available: bool = False
    bias: str = "NEUTRAL"
    strength: int = 0
    source: str = "NONE"
    freshness_seconds: float | None = None
    exchange_inflow_usd: float | None = None
    exchange_outflow_usd: float | None = None
    large_transfer_count: int | None = None


@dataclass(frozen=True)
class SocialEvidence:
    available: bool = False
    bias: str = "NEUTRAL"
    strength: int = 0
    source: str = "NONE"
    freshness_seconds: float | None = None
    mention_velocity_ratio: float | None = None
    unique_author_velocity_ratio: float | None = None


class FlowEvidenceProvider(Protocol):
    def get_flow_evidence(self, *, base_asset: str, symbol: str) -> FlowEvidence: ...


class WhaleEvidenceProvider(Protocol):
    def get_whale_evidence(self, *, base_asset: str, symbol: str) -> WhaleEvidence: ...


class SocialEvidenceProvider(Protocol):
    def get_social_evidence(self, *, base_asset: str, symbol: str) -> SocialEvidence: ...


@dataclass(frozen=True)
class CoarseMover:
    base_asset: str
    primary_pair: str
    kraken_public_symbol: str
    last_price: float
    volume_24h: float
    notional_24h_usd_approx: float
    high_24h: float
    low_24h: float
    lift_from_24h_low_pct: float
    distance_from_24h_high_pct: float
    coarse_score: float
    universe_count: int = 0
    # Issue #223: the coarse ticker already carries a live quote. Retaining it
    # is what lets Early Watch run the spread and bad-print validations that
    # previously no-opped because no universe context reached analyze_symbol.
    ticker_bid: float = 0.0
    ticker_ask: float = 0.0
    secondary_pair: str | None = None


@dataclass(frozen=True)
class EarlyMoverSignal:
    version: str
    symbol: str
    base_asset: str
    stage: str
    direction: str
    discovery_score: int
    score_is_probability: bool
    continuation_confidence: int
    continuation_confidence_is_probability: bool
    entry_quality: int
    entry_recommendation: str
    momentum_state: str
    momentum_1h_pct: float
    momentum_6h_pct: float
    momentum_24h_pct: float
    relative_volume: float
    distance_to_24h_high_pct: float
    liquidity_24h_usd_approx: float
    extended_move: bool
    flow_confirmation: str
    whale_confirmation: str
    social_confirmation: str
    alert_eligible: bool
    actionable: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    reference_price: float = 0.0
    detection_timeframe: str = "1H"
    # Issue #223 taxonomy. Additive with defaults so every existing caller and
    # fixture keeps working. ``stage`` above is retained only as the alert
    # governor transition token; these three are the operator-facing facts.
    market_phase: str = MarketPhase.DORMANT.value
    evidence_grade: str = EvidenceGrade.OBSERVED.value
    operator_disposition: str = "NO_ACTION"
    # Unclamped additive total behind the bounded ``continuation_confidence``.
    # ``evaluate_early_mover`` clamps to 100, so a strong candidate saturates
    # and loses cross-sectional information; this preserves it without
    # changing the bounded value any existing consumer reads.
    continuation_score_raw: float = 0.0
    discovery_score_raw: float = 0.0
    qualification_blocking_failures: tuple[str, ...] = ()
    actionability_reasons: tuple[str, ...] = ()
    # Decision-time volatility percentiles, retained so the timeframe shadow
    # experiment compares real values rather than manufacturing an inversion
    # from None defaults.
    bollinger_bandwidth_percentile: float | None = None
    atr_percentile: float | None = None

    @property
    def fingerprint(self) -> str:
        return ":".join((self.stage, str(self.continuation_confidence // 5 * 5),
                         str(self.entry_quality // 5 * 5), self.entry_recommendation,
                         self.momentum_state))

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fingerprint"] = self.fingerprint
        return payload


@dataclass(frozen=True)
class DeepEvaluationRejection:
    """Deep analysis ran but the discovery score did not advance.

    Distinct from ``None`` (malformed inputs): this carries the achieved score
    and component breakdown so a ``BELOW_THRESHOLD`` screening row can
    reconstruct the margin without changing the promotion decision.
    """

    achieved_score: float
    required_score: float
    discovery_score_raw: float
    components: Mapping[str, float]
    blocking_reason: str = "deep_discovery_score_below_minimum"
    failed_predicates: tuple[str, ...] = ("deep_discovery_score_below_minimum",)

    @property
    def score_margin(self) -> float:
        return float(self.achieved_score) - float(self.required_score)

    def as_score_components(self) -> ScoreComponents:
        return ScoreComponents(
            achieved_score=float(self.achieved_score),
            required_score=float(self.required_score),
            components=dict(self.components),
            failed_predicates=self.failed_predicates,
            blocking_reason=self.blocking_reason,
        )


def _pct(current: float, reference: float) -> float:
    return 0.0 if reference <= 0 else (current / reference - 1.0) * 100.0


def _optional_price(value: Any) -> float:
    """Parse an optional quote field, returning 0.0 when it is unusable."""
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed > 0 else 0.0


def _ticker_for_market(tickers: dict[str, dict[str, float]], pair_id: str, altname: str, display_pair: str):
    return tickers.get(pair_id) or tickers.get(altname) or tickers.get(display_pair)


def _emit_screening_fail_soft(
    callback: ScreeningCallback | None,
    payload: Mapping[str, Any],
    *,
    scan_id: str | None,
) -> None:
    if callback is None:
        return
    raw = str(payload.get("raw_identifier") or "UNKNOWN")
    try:
        callback(dict(payload))
    except Exception as exc:
        logger.warning(
            "O'Pip screening callback failed open scanner_type=EARLY_WATCH "
            "scan_id=%s raw=%s operation=record_screening_evaluation error=%s",
            scan_id or "UNKNOWN",
            raw,
            type(exc).__name__,
        )


def discover_coarse_movers(
    client: KrakenClient | None = None,
    *,
    max_candidates: int = DEFAULT_DEEP_CANDIDATES,
    min_notional_usd: float = MIN_DISCOVERY_NOTIONAL_USD,
    on_evaluated: ScreeningCallback | None = None,
    scan_id: str | None = None,
    decision_at: datetime | None = None,
    on_ranked: Callable[[list[CoarseMover]], None] | None = None,
    emit_rank_limit: bool = True,
) -> list[CoarseMover]:
    """Cheap, permissive full-universe discovery. Never authorizes a trade."""
    client = client or KrakenClient()
    decision_at = decision_at or datetime.now(timezone.utc)
    pair_details = client.get_asset_pairs()
    universe_count = len(pair_details)
    markets: list[tuple[str, str, str, str, str]] = []
    for pair_id, details in pair_details.items():
        raw_identifier = str(details.get("altname") or pair_id)
        if _is_excluded_market(details):
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": raw_identifier,
                    "outcome": "EXCLUDED_MARKET",
                    "reason": "market excluded by the production universe policy",
                    "metadata": build_coarse_status_metadata(universe_count=universe_count),
                },
                scan_id=scan_id,
            )
            continue
        symbols = _market_symbols(details)
        if symbols is None:
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": raw_identifier,
                    "outcome": "DATA_UNAVAILABLE",
                    "reason": "market symbols could not be resolved",
                    "metadata": build_coarse_status_metadata(universe_count=universe_count),
                },
                scan_id=scan_id,
            )
            continue
        base, quote = symbols
        altname = str(details.get("altname", pair_id)).upper()
        markets.append((pair_id, altname, f"{base}{quote}", base, quote))

    tickers: dict[str, dict[str, float]] = {}
    pair_ids = sorted({item[0] for item in markets})
    for start in range(0, len(pair_ids), TICKER_BATCH_SIZE):
        try:
            tickers.update(client.get_tickers(pair_ids[start:start + TICKER_BATCH_SIZE]))
        except Exception:
            continue

    by_asset: dict[str, tuple[str, str, str, str, str, dict[str, float]]] = {}
    secondary_by_asset: dict[str, str] = {}
    for pair_id, altname, display_pair, base, quote in markets:
        ticker = _ticker_for_market(tickers, pair_id, altname, display_pair)
        if ticker is None:
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": altname or pair_id,
                    "outcome": "DATA_UNAVAILABLE",
                    "reason": "ticker unavailable",
                    "metadata": build_coarse_status_metadata(universe_count=universe_count),
                },
                scan_id=scan_id,
            )
            continue
        current = by_asset.get(base)
        if current is None or (current[4] != "USD" and quote == "USD"):
            if current is not None:
                if current[4] == "USDT":
                    secondary_by_asset[base] = current[2]
                _emit_screening_fail_soft(
                    on_evaluated,
                    {
                        "raw_identifier": current[1] or current[0],
                        "outcome": "EXCLUDED_MARKET",
                        "reason": "non-primary quote market for canonical asset",
                        "metadata": build_coarse_status_metadata(universe_count=universe_count),
                    },
                    scan_id=scan_id,
                )
            by_asset[base] = (pair_id, altname, display_pair, base, quote, ticker)
        else:
            if quote == "USDT":
                secondary_by_asset[base] = display_pair
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": altname or pair_id,
                    "outcome": "EXCLUDED_MARKET",
                    "reason": "non-primary quote market for canonical asset",
                    "metadata": build_coarse_status_metadata(universe_count=universe_count),
                },
                scan_id=scan_id,
            )

    movers: list[CoarseMover] = []
    for pair_id, altname, display_pair, base, quote, ticker in by_asset.values():
        raw_identifier = altname or pair_id
        try:
            last = float(ticker.get("last") or 0.0)
            high = float(ticker.get("high_24h") or 0.0)
            low = float(ticker.get("low_24h") or 0.0)
            volume = float(ticker.get("volume_24h") or 0.0)
            # Optional quote. Absent bid/ask stays 0.0 and is reported as
            # UNAVAILABLE downstream rather than as a zero-width spread.
            bid = _optional_price(ticker.get("bid"))
            ask = _optional_price(ticker.get("ask"))
        except (TypeError, ValueError):
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": raw_identifier,
                    "outcome": "DATA_UNAVAILABLE",
                    "reason": "ticker contained a non-numeric field",
                    "metadata": build_coarse_status_metadata(universe_count=universe_count),
                },
                scan_id=scan_id,
            )
            continue
        if not all(math.isfinite(value) for value in (last, high, low, volume)):
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": raw_identifier,
                    "outcome": "DATA_UNAVAILABLE",
                    "reason": "ticker contained a non-finite field",
                    "metadata": build_coarse_status_metadata(universe_count=universe_count),
                },
                scan_id=scan_id,
            )
            continue
        if min(last, high, low) <= 0 or high < low or volume <= 0:
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": raw_identifier,
                    "outcome": "DATA_UNAVAILABLE",
                    "reason": "ticker failed structural validity checks",
                    "metadata": build_coarse_status_metadata(universe_count=universe_count),
                },
                scan_id=scan_id,
            )
            continue
        notional = last * volume
        lift = _pct(last, low)
        distance = max(0.0, (high - last) / last * 100.0)
        if not all(math.isfinite(value) for value in (notional, lift, distance)):
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": raw_identifier,
                    "outcome": "DATA_UNAVAILABLE",
                    "reason": "derived coarse measurements were non-finite",
                    "metadata": build_coarse_status_metadata(universe_count=universe_count),
                },
                scan_id=scan_id,
            )
            continue
        failed_predicates = []
        if notional < min_notional_usd:
            failed_predicates.append("minimum_notional_usd")
        if lift < MIN_COARSE_LIFT_FROM_24H_LOW_PCT:
            failed_predicates.append("minimum_lift_from_24h_low_pct")
        if distance > MAX_COARSE_DISTANCE_FROM_24H_HIGH_PCT:
            failed_predicates.append("maximum_distance_from_24h_high_pct")
        coarse_features = Stage0DecisionFeatures(
            lift_from_24h_low_pct=lift,
            distance_from_24h_high_pct=distance,
            notional_usd=notional,
            last_price=last,
            volume_24h=volume,
            decision_at=decision_at,
        )
        if failed_predicates:
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": raw_identifier,
                    "outcome": "BELOW_COARSE_THRESHOLD",
                    "reason": "one or more coarse predicates failed",
                    "metadata": build_below_threshold_metadata(
                        universe_count=universe_count,
                        features=coarse_features,
                        score=ScoreComponents(
                            failed_predicates=tuple(failed_predicates),
                            blocking_reason=failed_predicates[0],
                        ),
                        base_metadata={
                            "universe_count": universe_count,
                            "failed_predicates": failed_predicates,
                            "notional_usd": notional,
                            "minimum_notional_usd": min_notional_usd,
                            "lift_from_24h_low_pct": lift,
                            "minimum_lift_from_24h_low_pct": MIN_COARSE_LIFT_FROM_24H_LOW_PCT,
                            "distance_from_24h_high_pct": distance,
                            "maximum_distance_from_24h_high_pct": MAX_COARSE_DISTANCE_FROM_24H_HIGH_PCT,
                        },
                    ),
                },
                scan_id=scan_id,
            )
            continue
        liquidity_component = max(0.0, min(15.0, math.log10(max(notional, 1.0)) * 2.0))
        coarse_score = lift * 4.0 + max(0.0, 6.0 - distance) * 2.0 + liquidity_component
        if not math.isfinite(coarse_score):
            continue
        movers.append(CoarseMover(base, display_pair, f"{base}/{quote}", last, volume, notional,
                                  high, low, lift, distance, round(coarse_score, 4), universe_count,
                                  ticker_bid=bid, ticker_ask=ask,
                                  secondary_pair=secondary_by_asset.get(base)))

    movers.sort(key=lambda item: (-item.coarse_score, -item.lift_from_24h_low_pct,
                                  -item.notional_24h_usd_approx, item.base_asset))
    selected = movers[:max_candidates]
    # Phase 0A: persist rank, cutoff and margin for every truncated candidate.
    # Selection itself is untouched; this is the evidence that was missing when
    # the RAY retrospective tried to explain why a candidate never advanced.
    rank_contexts = rank_contexts_for_truncation(
        ranked_scores=[(item.primary_pair, item.coarse_score) for item in movers],
        selected_count=len(selected),
        universe_count=universe_count,
    )
    for mover in movers[max_candidates:]:
        if not emit_rank_limit:
            continue
        rank_context = rank_contexts.get(mover.primary_pair)
        _emit_screening_fail_soft(
            on_evaluated,
            {
                "raw_identifier": mover.primary_pair,
                "outcome": "COARSE_RANK_LIMIT",
                "long_score": mover.coarse_score,
                "reason": "coarse candidate fell outside the deep-analysis cap",
                "metadata": (
                    build_rank_limit_metadata(
                        rank_context=rank_context,
                        features=Stage0DecisionFeatures(
                            lift_from_24h_low_pct=mover.lift_from_24h_low_pct,
                            distance_from_24h_high_pct=mover.distance_from_24h_high_pct,
                            notional_usd=mover.notional_24h_usd_approx,
                            last_price=mover.last_price,
                            volume_24h=mover.volume_24h,
                            decision_at=decision_at,
                        ),
                        base_metadata={"universe_count": universe_count},
                    )
                    if rank_context is not None
                    else {"universe_count": universe_count}
                ),
            },
            scan_id=scan_id,
        )
    if on_ranked is not None:
        # The rank-limited tail is where an igniting asset was previously lost,
        # so the shadow selector must see it. Observation only.
        on_ranked(list(movers))
    return selected


def _momentum_state(one_hour: float, six_hour: float, day: float) -> str:
    if one_hour >= 1.0 and six_hour >= 2.0 and day >= 3.0:
        return MOMENTUM_ACCELERATING
    if (day >= 8.0 and one_hour <= 0.25) or (six_hour >= 4.0 and one_hour < 0.50):
        return MOMENTUM_DECELERATING
    return MOMENTUM_STEADY


def _optional_adjustment(flow: FlowEvidence | None, whale: WhaleEvidence | None, social: SocialEvidence | None):
    adjustment = 0
    reasons: list[str] = []
    warnings: list[str] = []
    statuses = []
    for label, evidence, positive_cap, negative_cap in (
        ("trade-flow", flow, 10, 12), ("whale", whale, 8, 10), ("social", social, 6, 6)
    ):
        status = "UNAVAILABLE"
        if evidence is not None and evidence.available:
            status = evidence.bias
            strength = max(0, int(getattr(evidence, "strength", 0) or 0))
            if evidence.bias == "BULLISH":
                adjustment += min(positive_cap, strength)
                reasons.append(f"{label} evidence is supportive ({strength}/10)")
            elif evidence.bias == "BEARISH":
                adjustment -= min(negative_cap, strength)
                warnings.append(f"{label} evidence is adverse ({strength}/10)")
        statuses.append(status)
    return adjustment, reasons, warnings, statuses


def evaluate_early_mover(snapshot: MarketSnapshot, coarse: CoarseMover, *, flow_evidence: FlowEvidence | None = None,
                         whale_evidence: WhaleEvidence | None = None, social_evidence: SocialEvidence | None = None,
                         prior_observation_count: int | None = None,
                         persistence_scans: int | None = None,
                         native_flow_available: bool | None = None,
                         cross_venue_available: bool | None = None,
                         depth_slippage_available: bool | None = None,
                         symbol_identity_resolved: bool | None = None,
                         duplicate_state_detected: bool | None = None,
                         validation_parity_enabled: bool | None = None,
                         ) -> EarlyMoverSignal | DeepEvaluationRejection | None:
    """Score discovery separately from continuation and entry quality.

    Returns ``DeepEvaluationRejection`` (not ``None``) when the deep score is
    computed but below the advance threshold, so Stage-0 can persist the
    achieved score and margin. ``None`` remains reserved for malformed inputs.
    """
    try:
        one_hour = float(snapshot.confirmed_price_change_1h_pct)
        six_hour = float(snapshot.momentum_6h_pct)
        day = float(snapshot.momentum_24h_pct)
        volume = float(snapshot.movement_volume_ratio or snapshot.volume_ratio or 0.0)
        near_high = float(snapshot.distance_to_24h_high_pct)
        liquidity = float(coarse.notional_24h_usd_approx)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (one_hour, six_hour, day, volume, near_high, liquidity)):
        return None
    if volume < 0 or near_high < 0 or liquidity < 0:
        return None

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []
    components: dict[str, float] = {}

    if one_hour >= 2.0:
        score += 30; reasons.append(f"1h momentum is accelerating at {one_hour:+.2f}%"); components["momentum_1h"] = 30.0
    elif one_hour >= 0.75:
        score += 20; reasons.append(f"1h momentum is positive at {one_hour:+.2f}%"); components["momentum_1h"] = 20.0
    if six_hour >= 4.0:
        score += 25; reasons.append(f"6h momentum is strong at {six_hour:+.2f}%"); components["momentum_6h"] = 25.0
    elif six_hour >= 2.0:
        score += 18; reasons.append(f"6h momentum is building at {six_hour:+.2f}%"); components["momentum_6h"] = 18.0
    if day >= 8.0:
        score += 20; reasons.append(f"24h momentum is strong at {day:+.2f}%"); components["momentum_24h"] = 20.0
    elif day >= 4.0:
        score += 14; reasons.append(f"24h momentum is positive at {day:+.2f}%"); components["momentum_24h"] = 14.0
    if volume >= 2.5:
        score += 20; reasons.append(f"relative volume expanded to {volume:.2f}x"); components["relative_volume"] = 20.0
    elif volume >= 1.5:
        score += 14; reasons.append(f"relative volume is elevated at {volume:.2f}x"); components["relative_volume"] = 14.0
    if near_high <= 2.0:
        score += 10; reasons.append(f"price is within {near_high:.2f}% of its 24h high"); components["near_high"] = 10.0
    if snapshot.trend == "bullish":
        score += 8; reasons.append("EMA structure is bullish"); components["trend_bullish"] = 8.0
    # Retain the unclamped additive total before bounding. The bounded value
    # keeps its exact historical meaning; the raw total restores the
    # cross-sectional information that saturation at 100 destroys.
    discovery_score_raw = float(score)
    score = min(100, score)
    if score < MIN_DEEP_DISCOVERY_SCORE:
        return DeepEvaluationRejection(
            achieved_score=float(score),
            required_score=float(MIN_DEEP_DISCOVERY_SCORE),
            discovery_score_raw=discovery_score_raw,
            components=components,
        )

    state = _momentum_state(one_hour, six_hour, day)
    continuation = score
    if volume < 0.50:
        continuation -= 18; warnings.append(f"relative volume is weak at {volume:.2f}x")
    elif volume < 1.00:
        continuation -= 8; warnings.append(f"relative volume is below baseline at {volume:.2f}x")
    if liquidity < 50_000:
        continuation -= 20; warnings.append("low approximate 24h liquidity; execution risk may be extreme")
    elif liquidity < 250_000:
        continuation -= 8; warnings.append("limited approximate 24h liquidity; execution quality requires validation")
    if state == MOMENTUM_ACCELERATING:
        continuation += 8; reasons.append("multi-horizon momentum is accelerating")
    elif state == MOMENTUM_DECELERATING:
        continuation -= 15; warnings.append("momentum is decelerating across the latest horizon")

    extended = day >= 15.0 or one_hour >= 6.0
    if extended:
        continuation -= 8; warnings.append("move is already extended; discovery alert is not permission to chase")

    adjustment, opt_reasons, opt_warnings, statuses = _optional_adjustment(flow_evidence, whale_evidence, social_evidence)
    # ``continuation`` accumulated deltas from ``score``, so the delta sum is
    # ``continuation + adjustment - score``. Applying it to the unclamped
    # discovery total yields the raw continuation without restating each rule.
    continuation_unbounded = continuation + adjustment
    continuation_score_raw = discovery_score_raw + float(continuation_unbounded - score)
    continuation = max(0, min(100, continuation_unbounded))
    reasons.extend(opt_reasons); warnings.extend(opt_warnings)

    entry_quality = 50
    entry_quality += 15 if state == MOMENTUM_ACCELERATING else (-20 if state == MOMENTUM_DECELERATING else 0)
    entry_quality += 10 if volume >= 1.5 else (-15 if volume < 0.5 else (-8 if volume < 1.0 else 0))
    entry_quality += 8 if near_high <= 1.0 else (-8 if near_high > 4.0 else 0)
    entry_quality += 8 if snapshot.trend == "bullish" else 0
    entry_quality -= 30 if extended else 0
    entry_quality -= 25 if liquidity < 50_000 else (8 if liquidity < 250_000 else 0)
    entry_quality = max(0, min(100, entry_quality))

    if liquidity < 50_000:
        recommendation = ENTRY_HIGH_RISK
    elif extended and entry_quality < 55:
        recommendation = ENTRY_TOO_EXTENDED
    elif extended:
        recommendation = ENTRY_PULLBACK
    elif state == MOMENTUM_ACCELERATING and entry_quality >= 65:
        recommendation = ENTRY_BREAKOUT
    elif entry_quality >= 50:
        recommendation = ENTRY_PULLBACK
    else:
        recommendation = ENTRY_WATCH

    stage = READY if (continuation >= 65 and liquidity >= 50_000 and volume >= 0.50) else WATCH
    legacy_alert_eligible = (stage == READY and entry_quality >= 55 and recommendation not in {ENTRY_TOO_EXTENDED, ENTRY_HIGH_RISK})

    # Issue #223: resolve the three orthogonal operator concepts. Market phase
    # is dominated by canonical extension, so an already-extended asset can no
    # longer be presented as an early discovery no matter how high its score.
    phase = resolve_market_phase(
        momentum_1h_pct=one_hour,
        momentum_6h_pct=six_hour,
        momentum_24h_pct=day,
        distance_to_24h_high_pct=near_high,
        relative_volume=volume,
        bandwidth_percentile=getattr(snapshot, "bollinger_bandwidth_percentile", None),
        atr_percentile=getattr(snapshot, "atr_percentile", None),
    )
    flow_for_validation = flow_evidence
    native_available = (
        native_flow_available
        if native_flow_available is not None
        else (flow_for_validation.available if flow_for_validation is not None else _native_flow_available_from_snapshot(snapshot))
    )
    native_bias = None
    if flow_for_validation is not None and flow_for_validation.available:
        native_bias = flow_for_validation.bias
    elif getattr(snapshot, "native_flow_evidence", None) is not None:
        native_bias = getattr(snapshot.native_flow_evidence, "bias", None)
        native_available = bool(getattr(snapshot.native_flow_evidence, "available", native_available))
    validation = evaluate_early_watch_validations(
        market_data_validation=getattr(snapshot, "market_data_validation", None),
        symbol_identity_resolved=_resolve_symbol_identity(
            coarse, snapshot, explicit=symbol_identity_resolved
        ),
        completed_candle_count=getattr(
            getattr(snapshot, "market_data_validation", None), "candle_count", None
        ),
        liquidity_24h_usd=liquidity,
        ticker_last=getattr(
            getattr(snapshot, "market_data_validation", None), "ticker_last", None
        ),
        latest_ohlc_close=getattr(
            getattr(snapshot, "market_data_validation", None), "latest_ohlc_close", None
        ),
        ticker_vs_ohlc_difference_pct=getattr(
            getattr(snapshot, "market_data_validation", None),
            "ticker_vs_ohlc_difference_pct",
            None,
        ),
        ticker_bid=coarse.ticker_bid or None,
        ticker_ask=coarse.ticker_ask or None,
        spread_pct=_spread_pct_from_snapshot(snapshot, coarse),
        finite_features=True,
        duplicate_state_detected=duplicate_state_detected,
        persistence_scans=persistence_scans,
        prior_observation_count=prior_observation_count,
        native_flow_available=native_available,
        native_flow_bias=native_bias,
        cross_market_status=getattr(snapshot, "cross_pair_confirmation_status", None),
        cross_venue_status=_cross_venue_status_from_snapshot(snapshot),
        cross_venue_available=(
            cross_venue_available
            if cross_venue_available is not None
            else _independent_venue_present(snapshot)
        ),
        execution_validation=getattr(snapshot, "execution_validation", None),
        depth_slippage_available=(
            depth_slippage_available
            if depth_slippage_available is not None
            else _depth_slippage_available_from_snapshot(snapshot)
        ),
        relative_strength_percentile=getattr(snapshot, "relative_strength_percentile", None),
        volatility_regime=getattr(snapshot, "atr_percentile", None),
        social_available=social_evidence.available if social_evidence is not None else None,
        whale_available=whale_evidence.available if whale_evidence is not None else None,
        extension_blocked=extended,
        entry_geometry_available=True,
        entry_geometry_acceptable=recommendation
        not in {ENTRY_TOO_EXTENDED, ENTRY_HIGH_RISK, ENTRY_WATCH},
    )
    disposition = resolve_operator_disposition(
        phase=phase,
        grade=validation.evidence_grade,
        entry_recommendation=recommendation,
        actionability_blocked=validation.actionability_blocked,
    )

    # Fail-closed qualification. While the flag is dark, alert eligibility is
    # byte-identical to the historical rule; when it is enabled, promotion
    # additionally requires every mandatory validation to pass, which can only
    # remove candidates and never add them.
    parity = (
        early_validation_parity_enabled()
        if validation_parity_enabled is None
        else bool(validation_parity_enabled)
    )
    alert_eligible = legacy_alert_eligible and (
        not parity or validation.evidence_grade is EvidenceGrade.QUALIFIED
    )

    bandwidth = getattr(snapshot, "bollinger_bandwidth_percentile", None)
    atr = getattr(snapshot, "atr_percentile", None)
    try:
        bandwidth_value = float(bandwidth) if bandwidth is not None else None
    except (TypeError, ValueError):
        bandwidth_value = None
    try:
        atr_value = float(atr) if atr is not None else None
    except (TypeError, ValueError):
        atr_value = None

    return EarlyMoverSignal(VERSION, snapshot.symbol, coarse.base_asset, stage, "LONG", score, False,
                            continuation, False, entry_quality, recommendation, state, round(one_hour, 4),
                            round(six_hour, 4), round(day, 4), round(volume, 4), round(near_high, 4),
                            round(liquidity, 2), extended, statuses[0], statuses[1],
                            statuses[2], alert_eligible, False, tuple(reasons), tuple(warnings),
                            reference_price=round(float(snapshot.last_price), 8),
                            detection_timeframe=str(snapshot.movement_timeframe or "1H"),
                            market_phase=phase.value,
                            evidence_grade=validation.evidence_grade.value,
                            operator_disposition=disposition.value,
                            continuation_score_raw=round(continuation_score_raw, 4),
                            discovery_score_raw=round(discovery_score_raw, 4),
                            qualification_blocking_failures=validation.blocking_failures,
                            actionability_reasons=validation.actionability_reasons,
                            bollinger_bandwidth_percentile=bandwidth_value,
                            atr_percentile=atr_value)


#: CoinGecko price/freshness statuses that may accompany a verified mapping.
_IDENTITY_ACCEPTED_STATUSES = frozenset(
    {
        "CONFIRMED",
        "WARN",
        "RESOLVED",
        "PASS",
        "MATCH",
        "OK",
    }
)
#: Explicit producer rejections. Identity is False, not unknown.
_IDENTITY_REJECTED_STATUSES = frozenset(
    {
        "AMBIGUOUS",
        "FAIL",
        "REJECT",
        "UNRESOLVED",
        "MATERIAL_DIVERGENCE",
    }
)
_IDENTITY_REJECTED_MAPPINGS = frozenset(
    {
        "AMBIGUOUS",
        "FAIL",
        "REJECT",
        "UNRESOLVED",
    }
)


def _reference_has_identity_fields(reference: Any) -> bool:
    return bool(
        str(getattr(reference, "coingecko_id", "") or "").strip()
        or str(getattr(reference, "coingecko_name", "") or "").strip()
    )


def _resolve_symbol_identity(
    coarse: CoarseMover,
    snapshot: MarketSnapshot,
    *,
    explicit: bool | None,
) -> bool | None:
    """Canonical identity for qualification — never ``bool(base_asset)``.

    A non-empty base-asset string is necessary but not sufficient. Prefer an
    explicit caller resolution, then the snapshot's independent market
    reference, then a strict primary-pair / base-asset consistency check.

    CoinGecko ``status`` is a price/freshness verdict. Identity is
    ``mapping_status`` plus CoinGecko identity fields. ``CONFIRMED`` with an
    ambiguous or missing mapping stays fail-closed.
    """
    if explicit is not None:
        return bool(explicit)
    reference = getattr(snapshot, "independent_market_reference", None)
    if reference is not None:
        status = str(getattr(reference, "status", "") or "").upper()
        mapping = str(getattr(reference, "mapping_status", "") or "").upper()
        if mapping in _IDENTITY_REJECTED_MAPPINGS or status in _IDENTITY_REJECTED_STATUSES:
            return False
        if (
            mapping in VERIFIED_MAPPING_STATUSES
            and status in _IDENTITY_ACCEPTED_STATUSES
            and _reference_has_identity_fields(reference)
        ):
            return True
        return None
    base = str(coarse.base_asset or "").strip().upper()
    pair = str(coarse.primary_pair or getattr(snapshot, "symbol", "") or "").strip().upper()
    if not base or not pair:
        return None
    if not pair.startswith(base):
        return False
    return True


def _spread_pct_from_snapshot(snapshot: MarketSnapshot, coarse: CoarseMover) -> float | None:
    execution = getattr(snapshot, "execution_validation", None)
    spread = getattr(execution, "spread_pct", None) if execution is not None else None
    if spread is not None:
        try:
            parsed = float(spread)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and math.isfinite(parsed):
            return parsed
    bid = coarse.ticker_bid
    ask = coarse.ticker_ask
    if bid > 0 and ask > 0 and ask > bid:
        mid = (bid + ask) / 2.0
        return (ask - bid) / mid * 100.0 if mid > 0 else None
    return None


def _native_flow_available_from_snapshot(snapshot: MarketSnapshot) -> bool | None:
    evidence = getattr(snapshot, "native_flow_evidence", None)
    if evidence is not None:
        return bool(getattr(evidence, "available", True))
    metrics = getattr(snapshot, "native_flow_metrics", None)
    if metrics is None:
        return None
    return bool(getattr(metrics, "available", True))


def _independent_venue_present(snapshot: MarketSnapshot) -> bool | None:
    reference = getattr(snapshot, "independent_market_reference", None)
    if reference is None:
        return None
    return True


def _cross_venue_status_from_snapshot(snapshot: MarketSnapshot) -> str | None:
    reference = getattr(snapshot, "independent_market_reference", None)
    if reference is None:
        return None
    return getattr(reference, "status", None)


def _cross_venue_available_from_snapshot(snapshot: MarketSnapshot) -> bool | None:
    """Independent venue only. Same-venue USD/USDT is CROSS_MARKET, not this."""
    return _independent_venue_present(snapshot)


def _depth_slippage_available_from_snapshot(snapshot: MarketSnapshot) -> bool | None:
    execution = getattr(snapshot, "execution_validation", None)
    if execution is None:
        return None
    status = str(getattr(execution, "status", "") or "").upper()
    if status in {"UNAVAILABLE"}:
        return False
    return True


def universe_context_for(mover: CoarseMover) -> UniverseAsset:
    """Build the ticker/liquidity context Early Watch never used to pass.

    ``analyze_symbol`` only runs the mandatory ticker-vs-OHLC bad-print check
    when a ``universe_asset`` supplies ``primary_ticker_last``. Bid/ask are
    passed through verbatim: a genuinely absent quote stays ``0.0`` and is
    reported as UNAVAILABLE rather than being backfilled into a zero spread.
    """
    quote = str(mover.kraken_public_symbol).rsplit("/", 1)[-1].upper() or "USD"
    usd_notional = mover.notional_24h_usd_approx if quote == "USD" else 0.0
    usdt_notional = mover.notional_24h_usd_approx if quote != "USD" else 0.0
    secondary = getattr(mover, "secondary_pair", None)
    return UniverseAsset(
        base_asset=mover.base_asset,
        primary_pair=mover.primary_pair,
        secondary_pair=secondary,
        usd_pair=mover.primary_pair if quote == "USD" else None,
        usdt_pair=secondary if quote == "USD" else mover.primary_pair,
        primary_quote_currency=quote,
        usd_24h_notional_usd=usd_notional,
        usdt_24h_notional_usd_equivalent=usdt_notional,
        combined_24h_notional_usd=mover.notional_24h_usd_approx,
        primary_kraken_symbol=mover.kraken_public_symbol,
        primary_ticker_last=mover.last_price,
        primary_ticker_bid=mover.ticker_bid,
        primary_ticker_ask=mover.ticker_ask,
    )


def apply_promoted_selector(
    ranked: Sequence[CoarseMover],
    *,
    max_candidates: int,
    history: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[CoarseMover]:
    """Re-select from the full coarse-passing universe with reserved cohorts.

    Production Stage-0 still runs the legacy ranker first so every truncated
    candidate retains reconstructable evidence. When the promotion flag is on,
    this function reallocates the *same* total budget across reserved cohorts
    so already-extended movers cannot monopolise every slot. It never widens
    ``max_candidates``.
    """
    budget = max(0, int(max_candidates))
    if not ranked or budget == 0:
        return []
    features = candidate_features_from_movers(ranked, history=history)
    selection = select_early_candidates(
        features,
        total_candidates=budget,
        universe_count=ranked[0].universe_count if ranked else len(ranked),
    )
    by_pair = {item.primary_pair: item for item in ranked}
    by_base = {item.base_asset.upper(): item for item in ranked}
    selected: list[CoarseMover] = []
    claimed: set[str] = set()
    for item in selection.selected:
        mover = by_pair.get(item.identifier) or by_base.get(item.base_asset.upper())
        if mover is None:
            continue
        key = mover.base_asset.upper()
        if key in claimed:
            continue
        claimed.add(key)
        selected.append(mover)
        if len(selected) >= budget:
            break
    return selected


def scan_early_movers(
    client: KrakenClient | None = None,
    *,
    max_candidates: int = DEFAULT_DEEP_CANDIDATES,
    on_coarse_evaluated: ScreeningCallback | None = None,
    on_evaluated: ScreeningCallback | None = None,
    scan_id: str | None = None,
    decision_at: datetime | None = None,
    prior_observation_counts: Mapping[str, int] | None = None,
    persistence_scans: Mapping[str, int] | None = None,
    validation_parity_enabled: bool | None = None,
    selector_promoted: bool | None = None,
    observation_history: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
):
    decision_at = decision_at or datetime.now(timezone.utc)
    parity = (
        early_validation_parity_enabled()
        if validation_parity_enabled is None
        else bool(validation_parity_enabled)
    )
    promoted = (
        early_selector_promoted()
        if selector_promoted is None
        else bool(selector_promoted)
    )
    priors = dict(prior_observation_counts or {})
    persistence = dict(persistence_scans or {})
    ranked: list[CoarseMover] = []
    coarse = discover_coarse_movers(
        client,
        max_candidates=max_candidates,
        on_evaluated=on_coarse_evaluated,
        scan_id=scan_id,
        decision_at=decision_at,
        on_ranked=ranked.extend,
        emit_rank_limit=not promoted,
    )
    # Authoritative reserved-cohort selection. Dark by default: when the flag
    # is false the legacy top-N list is byte-identical. evaluate_promotion is
    # never consulted here; a human still has to flip the flag.
    legacy_selected_ids = {item.primary_pair for item in coarse}
    selector_meta_by_pair: dict[str, dict[str, Any]] = {}
    if promoted and ranked:
        history = (
            observation_history
            if observation_history is not None
            else load_observation_history()
        )
        coarse = apply_promoted_selector(
            ranked,
            max_candidates=max_candidates,
            history=history,
        )
        promoted_ids = {item.primary_pair for item in coarse}
        legacy_top = ranked[:max_candidates]
        legacy_selected_ids = {item.primary_pair for item in legacy_top}
        rank_contexts = rank_contexts_for_ranked(
            ranked_scores=[(item.primary_pair, item.coarse_score) for item in ranked],
            selected_count=len(legacy_top),
            universe_count=ranked[0].universe_count if ranked else 0,
        )
        for mover in ranked:
            comparison = build_selector_comparison_metadata(
                legacy_selected=mover.primary_pair in legacy_selected_ids,
                promoted_selected=mover.primary_pair in promoted_ids,
                legacy_rank=(
                    rank_contexts[mover.primary_pair].as_dict()
                    if mover.primary_pair in rank_contexts
                    else None
                ),
                authoritative_selector=SELECTOR_PROMOTED_COHORT,
            )
            selector_meta_by_pair[mover.primary_pair] = comparison
            if mover.primary_pair in promoted_ids:
                continue
            features = Stage0DecisionFeatures(
                lift_from_24h_low_pct=mover.lift_from_24h_low_pct,
                distance_from_24h_high_pct=mover.distance_from_24h_high_pct,
                notional_usd=mover.notional_24h_usd_approx,
                last_price=mover.last_price,
                volume_24h=mover.volume_24h,
                decision_at=decision_at,
            )
            rank_context = rank_contexts.get(mover.primary_pair)
            if rank_context is not None:
                metadata = build_rank_limit_metadata(
                    rank_context=rank_context,
                    features=features,
                    base_metadata={"universe_count": mover.universe_count},
                    selector=SELECTOR_PROMOTED_COHORT,
                )
            else:
                metadata = {
                    "stage0_evidence_schema_version": STAGE0_EVIDENCE_SCHEMA_VERSION,
                    "universe_count": mover.universe_count,
                    "selector": SELECTOR_PROMOTED_COHORT,
                    "measurement_only": True,
                    "trade_authority_changed": False,
                    "production_selection_changed": True,
                    "decision_features": features.as_dict(),
                }
            metadata.update(comparison)
            _emit_screening_fail_soft(
                on_coarse_evaluated,
                {
                    "raw_identifier": mover.primary_pair,
                    "outcome": "COARSE_RANK_LIMIT",
                    "long_score": mover.coarse_score,
                    "reason": "authoritative reserved-cohort selector did not admit this candidate",
                    "metadata": metadata,
                },
                scan_id=scan_id,
            )
    signals: list[EarlyMoverSignal] = []
    # A base asset quoted in both USD and USDT yields two coarse movers, so
    # duplicate candidate state is structurally possible. Detecting it here is
    # what lets the qualification check assert consistency instead of assuming
    # it; the second sighting is still evaluated and recorded, just not
    # promotable.
    seen_assets: set[str] = set()
    for mover in coarse:
        duplicate_state = mover.base_asset.upper() in seen_assets
        seen_assets.add(mover.base_asset.upper())
        # Validation parity: supply the real ticker context so the mandatory
        # bad-print and staleness checks stop silently no-opping. Dark by
        # default, so the historical call signature is preserved until an
        # operator opts in.
        status, snapshot, _ = (
            analyze_symbol(mover.primary_pair, universe_context_for(mover))
            if parity
            else analyze_symbol(mover.primary_pair)
        )
        if parity and snapshot is not None:
            _enrich_bounded_candidate_evidence(snapshot, mover, client=client)
        features = Stage0DecisionFeatures(
            lift_from_24h_low_pct=mover.lift_from_24h_low_pct,
            distance_from_24h_high_pct=mover.distance_from_24h_high_pct,
            notional_usd=mover.notional_24h_usd_approx,
            last_price=mover.last_price,
            volume_24h=mover.volume_24h,
            relative_volume=(
                getattr(snapshot, "movement_volume_ratio", None) if snapshot is not None else None
            ),
            bandwidth_percentile=(
                getattr(snapshot, "bollinger_bandwidth_percentile", None)
                if snapshot is not None
                else None
            ),
            atr_percentile=(
                getattr(snapshot, "atr_percentile", None) if snapshot is not None else None
            ),
            prior_observation_count=priors.get(mover.base_asset.upper()),
            decision_at=decision_at,
        )
        selector_extra = selector_meta_by_pair.get(mover.primary_pair, {})
        deep_selector = SELECTOR_PROMOTED_COHORT if promoted else SELECTOR_PRODUCTION_COARSE
        def _meta_base() -> dict[str, Any]:
            payload = {"universe_count": mover.universe_count}
            payload.update(selector_extra)
            return payload
        if status != "ok" or snapshot is None:
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": mover.primary_pair,
                    "outcome": "DATA_UNAVAILABLE",
                    "reason": f"deep analysis unavailable: {status}",
                    "metadata": build_below_threshold_metadata(
                        universe_count=mover.universe_count,
                        features=features,
                        score=ScoreComponents(blocking_reason=f"deep_analysis_{status}"),
                        base_metadata=_meta_base(),
                        selector=deep_selector,
                    ),
                },
                scan_id=scan_id,
            )
            continue
        result = evaluate_early_mover(
            snapshot,
            mover,
            flow_evidence=getattr(snapshot, "native_flow_evidence", None),
            prior_observation_count=priors.get(mover.base_asset.upper()),
            persistence_scans=persistence.get(mover.base_asset.upper()),
            duplicate_state_detected=duplicate_state,
            validation_parity_enabled=parity,
        )
        if isinstance(result, DeepEvaluationRejection):
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": mover.primary_pair,
                    "outcome": "BELOW_THRESHOLD",
                    "long_score": result.achieved_score,
                    "reason": "deep early-mover score did not advance",
                    "metadata": build_below_threshold_metadata(
                        universe_count=mover.universe_count,
                        features=features,
                        score=result.as_score_components(),
                        base_metadata=_meta_base(),
                        selector=deep_selector,
                    ),
                },
                scan_id=scan_id,
            )
            continue
        if result is None:
            _emit_screening_fail_soft(
                on_evaluated,
                {
                    "raw_identifier": mover.primary_pair,
                    "outcome": "BELOW_THRESHOLD",
                    "reason": "deep early-mover inputs were malformed",
                    "metadata": build_below_threshold_metadata(
                        universe_count=mover.universe_count,
                        features=features,
                        score=ScoreComponents(
                            required_score=MIN_DEEP_DISCOVERY_SCORE,
                            blocking_reason="deep_discovery_inputs_malformed",
                        ),
                        base_metadata=_meta_base(),
                        selector=deep_selector,
                    ),
                },
                scan_id=scan_id,
            )
            continue
        signal = result
        signals.append(signal)
        _emit_screening_fail_soft(
            on_evaluated,
            {
                "raw_identifier": mover.primary_pair,
                "outcome": "ADVANCED",
                "long_score": signal.discovery_score,
                "advanced_direction": "LONG",
                "reason": "deep early-mover score advanced",
                "metadata": build_advanced_metadata(
                    universe_count=mover.universe_count,
                    features=features,
                    market_phase=signal.market_phase,
                    evidence_grade=signal.evidence_grade,
                    operator_disposition=signal.operator_disposition,
                    score=ScoreComponents(
                        achieved_score=signal.discovery_score,
                        required_score=MIN_DEEP_DISCOVERY_SCORE,
                        components={
                            "discovery_score_raw": signal.discovery_score_raw,
                            "continuation_confidence": float(signal.continuation_confidence),
                            "continuation_score_raw": signal.continuation_score_raw,
                            "entry_quality": float(signal.entry_quality),
                        },
                    ),
                    validation_results={
                        "evidence_grade": signal.evidence_grade,
                        "blocking_failures": list(signal.qualification_blocking_failures),
                        "actionability_reasons": list(signal.actionability_reasons),
                        "validation_parity_enabled": parity,
                    },
                    extension_comparison=compare_extension_definitions(
                        momentum_1h_pct=signal.momentum_1h_pct,
                        momentum_6h_pct=signal.momentum_6h_pct,
                        momentum_24h_pct=signal.momentum_24h_pct,
                        distance_to_24h_high_pct=signal.distance_to_24h_high_pct,
                    ),
                    base_metadata=_meta_base(),
                    selector=deep_selector,
                ),
            },
            scan_id=scan_id,
        )
    signals.sort(key=lambda item: (-item.continuation_confidence, -item.entry_quality, -item.discovery_score, item.symbol))
    # Shadow observation runs after every production decision is final, so it
    # cannot influence one. Every phase inside is independently flagged dark.
    try:
        persist_shadow_rows(
            observe_scan_shadow(
                all_movers=ranked or coarse,
                production_selection=coarse,
                signals=signals,
                universe_count=coarse[0].universe_count if coarse else 0,
                scan_id=scan_id,
                decision_at=decision_at,
                history=observation_history,
            )
        )
    except Exception as exc:
        logger.warning(
            "O'Pip early scan shadow failed open error=%s",
            type(exc).__name__,
        )
    return coarse, signals


def _enrich_bounded_candidate_evidence(
    snapshot: MarketSnapshot,
    mover: CoarseMover,
    *,
    client: KrakenClient | None,
) -> None:
    """Attach execution, native-flow and cross-market evidence for one deep candidate.

    Full-universe observation stays cheap. Expensive PreTrade/PostTrade and
    secondary-pair OHLC run only on the already-bounded deep set, fail soft,
    and never invent a supportive pass.
    """
    symbol = mover.kraken_public_symbol or mover.primary_pair
    book = None
    trades = None
    try:
        active = client or KrakenClient()
    except Exception:
        active = None
    if getattr(snapshot, "execution_validation", None) is None:
        try:
            from app.scanner.execution_validation import evaluate_execution, unavailable_execution
        except Exception:
            evaluate_execution = None
            unavailable_execution = None
        if evaluate_execution is not None and active is not None:
            try:
                book = active.get_pre_trade(symbol)
                try:
                    trades = active.get_post_trade(symbol, count=50)
                except Exception:
                    trades = None
                snapshot.execution_validation = evaluate_execution(
                    book=book,
                    validation_notional_usd=max(100.0, min(2_500.0, mover.notional_24h_usd_approx * 0.001)),
                    ticker_last=mover.last_price,
                    quote_to_usd_rate=1.0,
                    trades=trades,
                )
            except Exception as exc:
                if unavailable_execution is not None:
                    snapshot.execution_validation = unavailable_execution(
                        f"PreTrade unavailable: {type(exc).__name__}"
                    )
    if getattr(snapshot, "native_flow_evidence", None) is None and active is not None:
        try:
            from app.services.native_flow_evidence import evaluate_native_flow_shadow

            flow = evaluate_native_flow_shadow(
                symbol, client=active, book=book, trades=trades
            )
            snapshot.native_flow_evidence = flow.evidence
        except Exception:
            snapshot.native_flow_evidence = FlowEvidence(available=False, bias="NEUTRAL")
    secondary = getattr(mover, "secondary_pair", None) or getattr(snapshot, "secondary_pair", None)
    try:
        from app.scanner.cross_pair_confirmation import evaluate_cross_pair_confirmation

        secondary_snapshot = None
        if secondary:
            snapshot.secondary_pair = secondary
            try:
                status, secondary_snapshot, _ = analyze_symbol(secondary)
                if status != "ok":
                    secondary_snapshot = None
            except Exception:
                secondary_snapshot = None
        result = evaluate_cross_pair_confirmation(snapshot, secondary_snapshot)
        snapshot.cross_pair_confirmation_status = result.status
        snapshot.cross_pair_strengths = result.strengths
        snapshot.cross_pair_warnings = result.warnings
    except Exception:
        if not getattr(snapshot, "cross_pair_confirmation_status", None):
            snapshot.cross_pair_confirmation_status = "UNAVAILABLE"


def append_detection_snapshots(signals: list[EarlyMoverSignal], *, path: str = DEFAULT_LEARNING_PATH) -> int:
    """Capture immutable detection-time evidence; never backfill future outcomes here."""
    if not signals:
        return 0
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for signal in signals:
            payload = signal.as_dict()
            payload["record_type"] = "DETECTION"
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return len(signals)


def format_early_mover_message(signal: EarlyMoverSignal) -> str:
    from app.opip.early.operator_semantics import format_operator_watch_message

    return format_operator_watch_message(signal, style="telegram")


def send_early_mover_update(signal: EarlyMoverSignal, *, bot_token: str, chat_id: str, cooldown_seconds: int = 1800) -> bool:
    identity = f"EARLY_MOVER:{signal.symbol}"
    if not signal.alert_eligible:
        record_telegram_not_eligible(
            identity=identity,
            alert_family="EARLY_MOVER",
            event_type=signal.stage,
            fingerprint=signal.fingerprint,
            reason="ALERT_NOT_ELIGIBLE",
            symbol=signal.symbol,
        )
        return False
    if not should_emit(identity=identity, event_type=signal.stage,
                       fingerprint=signal.fingerprint, cooldown_seconds=cooldown_seconds):
        record_telegram_suppression(
            identity=identity,
            alert_family="EARLY_MOVER",
            event_type=signal.stage,
            fingerprint=signal.fingerprint,
            reason="NOTIFICATION_POLICY",
            symbol=signal.symbol,
        )
        return False
    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=format_early_mover_message(signal),
        identity=identity,
        alert_family="EARLY_MOVER",
        event_type=signal.stage,
        fingerprint=signal.fingerprint,
        symbol=signal.symbol,
    )
    if delivery.delivered:
        record_emitted(identity=identity, event_type=signal.stage, fingerprint=signal.fingerprint)
    return delivery.delivered
