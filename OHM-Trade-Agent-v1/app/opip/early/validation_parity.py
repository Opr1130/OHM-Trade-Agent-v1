"""Phase 0B Early Watch validation parity (Issue #223, root cause C).

The Early Watch path called ``analyze_symbol(pair)`` without a
``universe_asset``. That single omission silently disabled validation O'Pip
already owns:

* ``ticker_last`` was ``None``, so ``validate_market_data`` skipped the
  mandatory ticker-vs-OHLC bad-print comparison entirely;
* ``ticker_bid`` / ``ticker_ask`` stayed ``0.0``, so no spread could be
  checked;
* no depth/slippage, cross-venue or native-flow evidence was ever attached.

This module makes each of those an explicit, named result rather than an
absence. The governing rule is that ``UNAVAILABLE`` and ``NOT_EVALUATED`` are
never treated as ``PASS``: for a mandatory check they fail closed, so a
candidate whose bad-print check could not run cannot reach ``QUALIFIED``.

Cheap observation still runs over the full universe. Everything here operates
on evidence already gathered for a bounded candidate set; it adds no
full-universe API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from app.opip.early.taxonomy import (
    EvidenceGrade,
    ValidationClass,
    ValidationResult,
)

VALIDATION_PARITY_VERSION = "opip-early-validation-parity-v1"

#: Minimum approximate 24h notional for a qualified candidate. Mirrors the
#: existing ``evaluate_early_mover`` "execution risk may be extreme" floor so
#: this module is never looser than current production behaviour.
MIN_QUALIFIED_LIQUIDITY_USD = 50_000.0
#: Spread ceiling applied only when a real bid/ask is available. Conservative
#: and explicitly not yet calibrated; widening it requires evidence.
MAX_QUALIFIED_SPREAD_PCT = 1.5
#: Consecutive qualifying observations required before promotion, once a prior
#: observation exists at all. First sightings are soft, not failures.
MIN_QUALIFIED_PERSISTENCE_SCANS = 2
#: Minimum completed hourly candles for the qualification feature set.
MIN_QUALIFIED_CANDLES = 200

# Mandatory (fail-closed) check names.
CHECK_MARKET_DATA = "market_data_integrity"
CHECK_FINITE_FEATURES = "finite_decision_features"
CHECK_SYMBOL_IDENTITY = "canonical_symbol_identity"
CHECK_HISTORY_SUFFICIENCY = "history_sufficiency"
CHECK_MIN_LIQUIDITY = "minimum_liquidity"
CHECK_SPREAD = "spread_sanity"
CHECK_BAD_PRINT = "ticker_vs_ohlc_bad_print"
CHECK_PERSISTENCE = "observation_persistence"
CHECK_STATE_CONSISTENCY = "duplicate_state_consistency"

# Soft / retry check names.
CHECK_NATIVE_FLOW = "native_flow_evidence"
CHECK_CROSS_VENUE = "cross_venue_reference"
CHECK_DEPTH_SLIPPAGE = "depth_and_slippage_estimate"
CHECK_PRIOR_OBSERVATION = "prior_observation_available"
CHECK_SIGNAL_QUALITY_HISTORY = "signal_quality_history_continuity"

# Confidence-affecting check names.
CHECK_RELATIVE_STRENGTH = "relative_strength_percentile"
CHECK_RANK_VELOCITY = "rank_velocity"
CHECK_VOLATILITY_REGIME = "volatility_regime"

# Advisory-only check names.
CHECK_SOCIAL = "social_evidence"
CHECK_WHALE = "whale_transfer_evidence"
CHECK_NEWS = "news_evidence"

# Actionability gate names.
CHECK_EXTENSION_RISK = "extension_chase_risk"
CHECK_ENTRY_GEOMETRY = "entry_geometry"

MANDATORY_CHECKS = (
    CHECK_MARKET_DATA,
    CHECK_FINITE_FEATURES,
    CHECK_SYMBOL_IDENTITY,
    CHECK_HISTORY_SUFFICIENCY,
    CHECK_MIN_LIQUIDITY,
    CHECK_SPREAD,
    CHECK_BAD_PRINT,
    CHECK_PERSISTENCE,
    CHECK_STATE_CONSISTENCY,
)


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class ValidationCheck:
    """One named validation with an explicit four-state result."""

    name: str
    result: ValidationResult
    classification: ValidationClass
    detail: str = ""
    observed_value: float | None = None
    threshold: float | None = None

    @property
    def blocks_qualification(self) -> bool:
        """Whether this check denies :attr:`EvidenceGrade.QUALIFIED`.

        Mandatory checks fail closed on anything other than ``PASS``. That
        deliberately includes ``UNAVAILABLE`` and ``NOT_EVALUATED`` so a
        silently skipped check can never be mistaken for a satisfied one.
        """
        if self.classification is not ValidationClass.MANDATORY:
            return False
        return self.result is not ValidationResult.PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "result": self.result.value,
            "classification": self.classification.value,
            "detail": self.detail,
            "observed_value": _finite_optional(self.observed_value),
            "threshold": _finite_optional(self.threshold),
            "blocks_qualification": self.blocks_qualification,
        }


@dataclass(frozen=True)
class EarlyWatchValidationReport:
    """Aggregate validation outcome for one Early Watch candidate."""

    version: str
    checks: tuple[ValidationCheck, ...]
    evidence_grade: EvidenceGrade
    actionability_blocked: bool
    actionability_reasons: tuple[str, ...]

    @property
    def qualified(self) -> bool:
        return self.evidence_grade is EvidenceGrade.QUALIFIED

    @property
    def blocking_failures(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if check.blocks_qualification)

    @property
    def soft_unavailable(self) -> tuple[str, ...]:
        return tuple(
            check.name
            for check in self.checks
            if check.classification is ValidationClass.SOFT
            and check.result in {ValidationResult.UNAVAILABLE, ValidationResult.NOT_EVALUATED}
        )

    def check(self, name: str) -> ValidationCheck | None:
        return next((item for item in self.checks if item.name == name), None)

    def result_for(self, name: str) -> ValidationResult:
        found = self.check(name)
        return found.result if found is not None else ValidationResult.NOT_EVALUATED

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "evidence_grade": self.evidence_grade.value,
            "qualified": self.qualified,
            "blocking_failures": list(self.blocking_failures),
            "soft_unavailable": list(self.soft_unavailable),
            "actionability_blocked": self.actionability_blocked,
            "actionability_reasons": list(self.actionability_reasons),
            "checks": [check.as_dict() for check in self.checks],
            "trade_authority_changed": False,
        }


def _market_data_check(validation: Any) -> ValidationCheck:
    if validation is None:
        return ValidationCheck(
            CHECK_MARKET_DATA,
            ValidationResult.NOT_EVALUATED,
            ValidationClass.MANDATORY,
            "market-data validation was never run for this candidate",
        )
    qualified = bool(getattr(validation, "qualified", False))
    status = str(getattr(validation, "status", "UNKNOWN"))
    if not qualified:
        reasons = list(getattr(validation, "rejection_reasons", ()) or ())
        return ValidationCheck(
            CHECK_MARKET_DATA,
            ValidationResult.FAIL,
            ValidationClass.MANDATORY,
            "; ".join(reasons) or f"market-data validation status {status}",
        )
    return ValidationCheck(
        CHECK_MARKET_DATA,
        ValidationResult.PASS,
        ValidationClass.MANDATORY,
        f"market-data validation status {status}",
    )


def _bad_print_check(
    *,
    ticker_last: Any,
    latest_ohlc_close: Any,
    reported_difference_pct: Any,
    reject_threshold_pct: float,
) -> ValidationCheck:
    """Ticker-vs-OHLC consistency, which must never silently no-op.

    ``ticker_last is None`` is the exact production defect: the underlying
    validator skips the comparison and reports success. Here that becomes
    ``NOT_EVALUATED`` on a mandatory check, so it fails closed instead.
    """
    last = _finite_optional(ticker_last)
    close = _finite_optional(latest_ohlc_close)
    if last is None or last <= 0:
        return ValidationCheck(
            CHECK_BAD_PRINT,
            ValidationResult.NOT_EVALUATED,
            ValidationClass.MANDATORY,
            "ticker last price was not supplied, so the bad-print check could not run",
            threshold=reject_threshold_pct,
        )
    if close is None or close <= 0:
        return ValidationCheck(
            CHECK_BAD_PRINT,
            ValidationResult.UNAVAILABLE,
            ValidationClass.MANDATORY,
            "no valid latest OHLC close to compare the ticker against",
            observed_value=last,
            threshold=reject_threshold_pct,
        )
    difference = _finite_optional(reported_difference_pct)
    if difference is None:
        difference = abs(last - close) / close * 100.0
    if difference > reject_threshold_pct:
        return ValidationCheck(
            CHECK_BAD_PRINT,
            ValidationResult.FAIL,
            ValidationClass.MANDATORY,
            "ticker and latest OHLC close are inconsistent beyond the bad-print ceiling",
            observed_value=difference,
            threshold=reject_threshold_pct,
        )
    return ValidationCheck(
        CHECK_BAD_PRINT,
        ValidationResult.PASS,
        ValidationClass.MANDATORY,
        "ticker agrees with the latest OHLC close",
        observed_value=difference,
        threshold=reject_threshold_pct,
    )


def _spread_check(*, bid: Any, ask: Any, spread_pct: Any) -> ValidationCheck:
    """Spread sanity, evaluated only when a real quote is available.

    Bid/ask of ``0.0`` is the production default when no universe context was
    passed. That is genuinely unavailable rather than a wide spread, so it is
    reported as ``UNAVAILABLE``; because the check is mandatory, promotion
    still fails closed until the quote is wired in.
    """
    reported = _finite_optional(spread_pct)
    if reported is None:
        best_bid = _finite_optional(bid)
        best_ask = _finite_optional(ask)
        if not best_bid or not best_ask or best_bid <= 0 or best_ask <= 0:
            return ValidationCheck(
                CHECK_SPREAD,
                ValidationResult.UNAVAILABLE,
                ValidationClass.MANDATORY,
                "no bid/ask quote was available for this candidate",
                threshold=MAX_QUALIFIED_SPREAD_PCT,
            )
        if best_bid >= best_ask:
            return ValidationCheck(
                CHECK_SPREAD,
                ValidationResult.FAIL,
                ValidationClass.MANDATORY,
                "quote is crossed or locked",
                threshold=MAX_QUALIFIED_SPREAD_PCT,
            )
        mid = (best_bid + best_ask) / 2.0
        reported = (best_ask - best_bid) / mid * 100.0
    if reported < 0:
        return ValidationCheck(
            CHECK_SPREAD,
            ValidationResult.FAIL,
            ValidationClass.MANDATORY,
            "computed spread was negative",
            observed_value=reported,
            threshold=MAX_QUALIFIED_SPREAD_PCT,
        )
    if reported > MAX_QUALIFIED_SPREAD_PCT:
        return ValidationCheck(
            CHECK_SPREAD,
            ValidationResult.FAIL,
            ValidationClass.MANDATORY,
            "spread is abnormally wide for a qualified candidate",
            observed_value=reported,
            threshold=MAX_QUALIFIED_SPREAD_PCT,
        )
    return ValidationCheck(
        CHECK_SPREAD,
        ValidationResult.PASS,
        ValidationClass.MANDATORY,
        "spread is within the qualification ceiling",
        observed_value=reported,
        threshold=MAX_QUALIFIED_SPREAD_PCT,
    )


def _persistence_check(
    *,
    persistence_scans: Any,
    prior_observation_count: Any,
) -> ValidationCheck:
    """Persistence after the required observation window.

    A genuinely first sighting cannot have persisted yet, so it is
    ``UNAVAILABLE`` — still fail-closed for promotion, but recorded as a
    retryable state rather than adverse evidence.
    """
    prior = _finite_optional(prior_observation_count)
    scans = _finite_optional(persistence_scans)
    if prior is None or prior <= 0:
        return ValidationCheck(
            CHECK_PERSISTENCE,
            ValidationResult.UNAVAILABLE,
            ValidationClass.MANDATORY,
            "first sighting; persistence cannot be evaluated yet",
            observed_value=scans,
            threshold=float(MIN_QUALIFIED_PERSISTENCE_SCANS),
        )
    if scans is None:
        return ValidationCheck(
            CHECK_PERSISTENCE,
            ValidationResult.NOT_EVALUATED,
            ValidationClass.MANDATORY,
            "persistence was not measured despite an available observation history",
            threshold=float(MIN_QUALIFIED_PERSISTENCE_SCANS),
        )
    if scans < MIN_QUALIFIED_PERSISTENCE_SCANS:
        return ValidationCheck(
            CHECK_PERSISTENCE,
            ValidationResult.FAIL,
            ValidationClass.MANDATORY,
            "evidence did not persist across the required observation window",
            observed_value=scans,
            threshold=float(MIN_QUALIFIED_PERSISTENCE_SCANS),
        )
    return ValidationCheck(
        CHECK_PERSISTENCE,
        ValidationResult.PASS,
        ValidationClass.MANDATORY,
        "evidence persisted across consecutive observations",
        observed_value=scans,
        threshold=float(MIN_QUALIFIED_PERSISTENCE_SCANS),
    )


def _optional_evidence_check(
    name: str,
    classification: ValidationClass,
    *,
    available: Any,
    supportive: Any = None,
    detail_available: str = "",
    detail_missing: str = "",
) -> ValidationCheck:
    if available is None:
        return ValidationCheck(
            name,
            ValidationResult.NOT_EVALUATED,
            classification,
            detail_missing or f"{name} was not evaluated",
        )
    if not bool(available):
        return ValidationCheck(
            name,
            ValidationResult.UNAVAILABLE,
            classification,
            detail_missing or f"{name} is unavailable for this market",
        )
    if supportive is None:
        return ValidationCheck(
            name,
            ValidationResult.PASS,
            classification,
            detail_available or f"{name} is available",
        )
    return ValidationCheck(
        name,
        ValidationResult.PASS if bool(supportive) else ValidationResult.FAIL,
        classification,
        detail_available or f"{name} evaluated",
    )


def evaluate_early_watch_validations(
    *,
    market_data_validation: Any = None,
    symbol_identity_resolved: Any = None,
    completed_candle_count: Any = None,
    liquidity_24h_usd: Any = None,
    ticker_last: Any = None,
    latest_ohlc_close: Any = None,
    ticker_vs_ohlc_difference_pct: Any = None,
    bad_print_reject_threshold_pct: float = 10.0,
    ticker_bid: Any = None,
    ticker_ask: Any = None,
    spread_pct: Any = None,
    finite_features: Any = None,
    persistence_scans: Any = None,
    prior_observation_count: Any = None,
    duplicate_state_detected: Any = None,
    native_flow_available: Any = None,
    cross_venue_available: Any = None,
    depth_slippage_available: Any = None,
    signal_quality_history_continuous: Any = None,
    relative_strength_percentile: Any = None,
    rank_velocity: Any = None,
    volatility_regime: Any = None,
    social_available: Any = None,
    whale_available: Any = None,
    news_available: Any = None,
    extension_blocked: Any = False,
    entry_geometry_available: Any = None,
    entry_geometry_acceptable: Any = None,
) -> EarlyWatchValidationReport:
    """Evaluate every Early Watch validation with explicit four-state results.

    No argument is required. Anything not supplied is reported as
    ``NOT_EVALUATED`` or ``UNAVAILABLE`` for its own class, which for a
    mandatory check means promotion fails closed.
    """
    checks: list[ValidationCheck] = [_market_data_check(market_data_validation)]

    # Not supplied means the caller never asserted finiteness. That is
    # NOT_EVALUATED, not a pass: a defaulted-true mandatory check would be
    # exactly the silent no-op this module exists to remove.
    if finite_features is None:
        checks.append(
            ValidationCheck(
                CHECK_FINITE_FEATURES,
                ValidationResult.NOT_EVALUATED,
                ValidationClass.MANDATORY,
                "no caller asserted that the decision features are finite",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                CHECK_FINITE_FEATURES,
                ValidationResult.PASS if bool(finite_features) else ValidationResult.FAIL,
                ValidationClass.MANDATORY,
                "decision features are finite"
                if bool(finite_features)
                else "decision features contained malformed or non-finite values",
            )
        )

    if symbol_identity_resolved is None:
        checks.append(
            ValidationCheck(
                CHECK_SYMBOL_IDENTITY,
                ValidationResult.NOT_EVALUATED,
                ValidationClass.MANDATORY,
                "canonical symbol identity was not resolved for this candidate",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                CHECK_SYMBOL_IDENTITY,
                ValidationResult.PASS
                if bool(symbol_identity_resolved)
                else ValidationResult.FAIL,
                ValidationClass.MANDATORY,
                "canonical symbol identity resolved"
                if bool(symbol_identity_resolved)
                else "canonical symbol identity could not be resolved",
            )
        )

    candles = _finite_optional(completed_candle_count)
    if candles is None:
        checks.append(
            ValidationCheck(
                CHECK_HISTORY_SUFFICIENCY,
                ValidationResult.NOT_EVALUATED,
                ValidationClass.MANDATORY,
                "completed candle count was not supplied",
                threshold=float(MIN_QUALIFIED_CANDLES),
            )
        )
    else:
        sufficient = candles >= MIN_QUALIFIED_CANDLES
        checks.append(
            ValidationCheck(
                CHECK_HISTORY_SUFFICIENCY,
                ValidationResult.PASS if sufficient else ValidationResult.FAIL,
                ValidationClass.MANDATORY,
                "sufficient completed history"
                if sufficient
                else "insufficient completed history for the qualification feature set",
                observed_value=candles,
                threshold=float(MIN_QUALIFIED_CANDLES),
            )
        )

    liquidity = _finite_optional(liquidity_24h_usd)
    if liquidity is None:
        checks.append(
            ValidationCheck(
                CHECK_MIN_LIQUIDITY,
                ValidationResult.UNAVAILABLE,
                ValidationClass.MANDATORY,
                "approximate 24h liquidity was unavailable",
                threshold=MIN_QUALIFIED_LIQUIDITY_USD,
            )
        )
    else:
        liquid = liquidity >= MIN_QUALIFIED_LIQUIDITY_USD
        checks.append(
            ValidationCheck(
                CHECK_MIN_LIQUIDITY,
                ValidationResult.PASS if liquid else ValidationResult.FAIL,
                ValidationClass.MANDATORY,
                "approximate 24h liquidity clears the qualification floor"
                if liquid
                else "approximate 24h liquidity is below the qualification floor",
                observed_value=liquidity,
                threshold=MIN_QUALIFIED_LIQUIDITY_USD,
            )
        )

    checks.append(_spread_check(bid=ticker_bid, ask=ticker_ask, spread_pct=spread_pct))
    checks.append(
        _bad_print_check(
            ticker_last=ticker_last,
            latest_ohlc_close=latest_ohlc_close,
            reported_difference_pct=ticker_vs_ohlc_difference_pct,
            reject_threshold_pct=float(bad_print_reject_threshold_pct),
        )
    )
    checks.append(
        _persistence_check(
            persistence_scans=persistence_scans,
            prior_observation_count=prior_observation_count,
        )
    )
    if duplicate_state_detected is None:
        checks.append(
            ValidationCheck(
                CHECK_STATE_CONSISTENCY,
                ValidationResult.NOT_EVALUATED,
                ValidationClass.MANDATORY,
                "candidate state consistency was not checked",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                CHECK_STATE_CONSISTENCY,
                ValidationResult.FAIL
                if bool(duplicate_state_detected)
                else ValidationResult.PASS,
                ValidationClass.MANDATORY,
                "duplicate or inconsistent candidate state detected"
                if bool(duplicate_state_detected)
                else "candidate state is internally consistent",
            )
        )

    # Soft / retryable evidence. Missing values never block promotion, but they
    # never count as corroboration either.
    checks.append(
        _optional_evidence_check(
            CHECK_NATIVE_FLOW,
            ValidationClass.SOFT,
            available=native_flow_available,
            detail_missing="Kraken-native flow evidence is unavailable for this observation",
        )
    )
    checks.append(
        _optional_evidence_check(
            CHECK_CROSS_VENUE,
            ValidationClass.SOFT,
            available=cross_venue_available,
            detail_missing="no supported cross-venue reference feed for this asset",
        )
    )
    checks.append(
        _optional_evidence_check(
            CHECK_DEPTH_SLIPPAGE,
            ValidationClass.SOFT,
            available=depth_slippage_available,
            detail_missing="depth and slippage estimate unavailable",
        )
    )
    prior = _finite_optional(prior_observation_count)
    checks.append(
        ValidationCheck(
            CHECK_PRIOR_OBSERVATION,
            ValidationResult.PASS if prior is not None and prior > 0 else ValidationResult.UNAVAILABLE,
            ValidationClass.SOFT,
            "a prior observation exists for delta features"
            if prior is not None and prior > 0
            else "no prior observation yet; delta features are unavailable on first sighting",
            observed_value=prior,
        )
    )
    checks.append(
        _optional_evidence_check(
            CHECK_SIGNAL_QUALITY_HISTORY,
            ValidationClass.SOFT,
            available=signal_quality_history_continuous,
            detail_missing="Signal Quality history is not yet continuous for this asset",
        )
    )

    # Confidence-affecting evidence.
    for name, value in (
        (CHECK_RELATIVE_STRENGTH, relative_strength_percentile),
        (CHECK_RANK_VELOCITY, rank_velocity),
        (CHECK_VOLATILITY_REGIME, volatility_regime),
    ):
        parsed = _finite_optional(value)
        checks.append(
            ValidationCheck(
                name,
                ValidationResult.PASS if parsed is not None else ValidationResult.UNAVAILABLE,
                ValidationClass.CONFIDENCE,
                f"{name} observed" if parsed is not None else f"{name} unavailable",
                observed_value=parsed,
            )
        )

    # Advisory-only evidence. Cannot promote and cannot block.
    for name, available in (
        (CHECK_SOCIAL, social_available),
        (CHECK_WHALE, whale_available),
        (CHECK_NEWS, news_available),
    ):
        checks.append(
            _optional_evidence_check(
                name,
                ValidationClass.ADVISORY,
                available=available,
                detail_missing=f"{name} is unavailable and is advisory only",
                detail_available=f"{name} is available and is advisory only",
            )
        )

    # Actionability gates. These never affect QUALIFIED.
    actionability_reasons: list[str] = []
    checks.append(
        ValidationCheck(
            CHECK_EXTENSION_RISK,
            ValidationResult.FAIL if bool(extension_blocked) else ValidationResult.PASS,
            ValidationClass.ACTIONABILITY,
            "move is already extended; chasing is not appropriate"
            if bool(extension_blocked)
            else "extension risk is acceptable",
        )
    )
    if bool(extension_blocked):
        actionability_reasons.append("move is already extended")

    if entry_geometry_available is None:
        checks.append(
            ValidationCheck(
                CHECK_ENTRY_GEOMETRY,
                ValidationResult.NOT_EVALUATED,
                ValidationClass.ACTIONABILITY,
                "entry geometry was not evaluated",
            )
        )
        actionability_reasons.append("entry geometry was not evaluated")
    elif not bool(entry_geometry_available):
        checks.append(
            ValidationCheck(
                CHECK_ENTRY_GEOMETRY,
                ValidationResult.UNAVAILABLE,
                ValidationClass.ACTIONABILITY,
                "entry geometry evidence is unavailable",
            )
        )
        actionability_reasons.append("entry geometry evidence is unavailable")
    else:
        acceptable = bool(entry_geometry_acceptable)
        checks.append(
            ValidationCheck(
                CHECK_ENTRY_GEOMETRY,
                ValidationResult.PASS if acceptable else ValidationResult.FAIL,
                ValidationClass.ACTIONABILITY,
                "entry geometry is acceptable" if acceptable else "entry geometry is unfavourable",
            )
        )
        if not acceptable:
            actionability_reasons.append("entry geometry is unfavourable")

    grade = resolve_evidence_grade(checks)
    return EarlyWatchValidationReport(
        version=VALIDATION_PARITY_VERSION,
        checks=tuple(checks),
        evidence_grade=grade,
        actionability_blocked=bool(actionability_reasons),
        actionability_reasons=tuple(actionability_reasons),
    )


#: Independent soft/confidence evidence families required, on top of every
#: mandatory check passing, before a candidate may reach QUALIFIED. Mirrors
#: the ExplosionPrecursor N-of-M corroboration pattern (evidence >= 3): a
#: perfect mandatory pass with every optional confirmation missing is not
#: high-confidence qualification.
MIN_CORROBORATING_FAMILIES_FOR_QUALIFIED = 2

#: Soft and confidence checks that count as independent evidence families.
#: Advisory evidence never participates.
CORROBORATING_FAMILY_CHECKS = frozenset(
    {
        CHECK_NATIVE_FLOW,
        CHECK_CROSS_VENUE,
        CHECK_DEPTH_SLIPPAGE,
        CHECK_PRIOR_OBSERVATION,
        CHECK_SIGNAL_QUALITY_HISTORY,
        CHECK_RELATIVE_STRENGTH,
        CHECK_RANK_VELOCITY,
        CHECK_VOLATILITY_REGIME,
    }
)


def corroborating_family_count(checks: Sequence[ValidationCheck]) -> int:
    """Count independent soft/confidence families that actually passed."""
    return sum(
        1
        for check in checks
        if check.name in CORROBORATING_FAMILY_CHECKS
        and check.result is ValidationResult.PASS
        and check.classification in {ValidationClass.SOFT, ValidationClass.CONFIDENCE}
    )


def resolve_evidence_grade(checks: Sequence[ValidationCheck]) -> EvidenceGrade:
    """Map validation results onto an :class:`EvidenceGrade`.

    ``REJECTED`` requires an actual ``FAIL`` on a mandatory check: an absent
    measurement is not adverse evidence, it is merely insufficient. Anything
    unresolved on a mandatory check therefore stops at ``OBSERVED`` or
    ``CORROBORATED`` and can never reach ``QUALIFIED``.

    ``QUALIFIED`` additionally requires
    :data:`MIN_CORROBORATING_FAMILIES_FOR_QUALIFIED` independent soft or
    confidence families to pass. Mandatory integrity alone is not
    high-confidence qualification.
    """
    mandatory = [
        check for check in checks if check.classification is ValidationClass.MANDATORY
    ]
    if any(check.result is ValidationResult.FAIL for check in mandatory):
        return EvidenceGrade.REJECTED

    corroborating = corroborating_family_count(checks)
    if (
        mandatory
        and all(check.result is ValidationResult.PASS for check in mandatory)
        and corroborating >= MIN_CORROBORATING_FAMILIES_FOR_QUALIFIED
    ):
        return EvidenceGrade.QUALIFIED

    if corroborating > 0:
        return EvidenceGrade.CORROBORATED
    return EvidenceGrade.OBSERVED


def advisory_only_promotion_attempt(checks: Sequence[ValidationCheck]) -> bool:
    """Whether the only passing evidence is advisory.

    Advisory evidence alone must never promote a candidate. This helper makes
    that assertion directly testable.
    """
    passing = [check for check in checks if check.result is ValidationResult.PASS]
    if not passing:
        return False
    return all(check.classification is ValidationClass.ADVISORY for check in passing)


def report_from_snapshot(
    snapshot: Any,
    *,
    liquidity_24h_usd: Any = None,
    ticker_bid: Any = None,
    ticker_ask: Any = None,
    persistence_scans: Any = None,
    prior_observation_count: Any = None,
    native_flow_available: Any = None,
    extension_blocked: Any = False,
    entry_geometry_available: Any = None,
    entry_geometry_acceptable: Any = None,
    symbol_identity_resolved: Any = None,
    overrides: Mapping[str, Any] | None = None,
) -> EarlyWatchValidationReport:
    """Build a report from a ``MarketSnapshot`` plus bounded extra evidence.

    The snapshot's own ``market_data_validation`` is the source of truth for
    integrity and bad-print evidence. When the Early Watch path ran without
    ticker context, ``ticker_last`` there is ``None`` and the bad-print check
    correctly reports ``NOT_EVALUATED`` instead of an unearned pass.
    """
    validation = getattr(snapshot, "market_data_validation", None)
    execution = getattr(snapshot, "execution_validation", None)
    reference = getattr(snapshot, "independent_market_reference", None)

    resolved_bid = ticker_bid if ticker_bid is not None else getattr(snapshot, "ticker_bid", None)
    resolved_ask = ticker_ask if ticker_ask is not None else getattr(snapshot, "ticker_ask", None)
    liquidity = (
        liquidity_24h_usd
        if liquidity_24h_usd is not None
        else getattr(snapshot, "combined_24h_liquidity_usd", None)
    )

    kwargs: dict[str, Any] = {
        "market_data_validation": validation,
        "symbol_identity_resolved": symbol_identity_resolved,
        "completed_candle_count": (
            getattr(validation, "candle_count", None) if validation is not None else None
        ),
        "liquidity_24h_usd": liquidity,
        "ticker_last": getattr(validation, "ticker_last", None) if validation is not None else None,
        "latest_ohlc_close": (
            getattr(validation, "latest_ohlc_close", None) if validation is not None else None
        ),
        "ticker_vs_ohlc_difference_pct": (
            getattr(validation, "ticker_vs_ohlc_difference_pct", None)
            if validation is not None
            else None
        ),
        "ticker_bid": resolved_bid,
        "ticker_ask": resolved_ask,
        "spread_pct": getattr(execution, "spread_pct", None) if execution is not None else None,
        "finite_features": True,
        "persistence_scans": persistence_scans,
        "prior_observation_count": prior_observation_count,
        "native_flow_available": native_flow_available,
        "cross_venue_available": reference is not None,
        "depth_slippage_available": execution is not None,
        "relative_strength_percentile": None,
        "volatility_regime": getattr(snapshot, "atr_percentile", None),
        "extension_blocked": extension_blocked,
        "entry_geometry_available": entry_geometry_available,
        "entry_geometry_acceptable": entry_geometry_acceptable,
    }
    if overrides:
        kwargs.update(dict(overrides))
    return evaluate_early_watch_validations(**kwargs)
