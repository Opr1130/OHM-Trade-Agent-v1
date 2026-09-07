"""Canonical early-detection taxonomy (Issue #223, root causes F/G/H).

The legacy Early Watch path derived one ``stage`` string (``READY``/``WATCH``)
independently from an ``entry_recommendation`` string. That made
``READY`` + ``WAIT_FOR_PULLBACK`` structurally possible and let an
already-extended asset render as an early discovery.

Three orthogonal vocabularies replace that single axis:

* :class:`MarketPhase` — what the market is doing.
* :class:`EvidenceGrade` — what O'Pip validated.
* :class:`OperatorDisposition` — what to do now.

``READY`` is retained only as an alert-governor transition token
(``app.services.alert_governor.PRIORITY_TRANSITION_STAGES``). It is never an
operator-facing label. See :func:`governor_stage_token`.

Extension unification: ``movement_discovery_v2``, ``explosion_state`` and
Signal Quality each define "extended" differently. :func:`extension_state`
resolves one canonical answer as the *union* of the existing rules, which is
never looser than any single legacy rule. Production gating is unchanged;
:func:`compare_extension_definitions` exists so the difference can be observed
in shadow before anything is promoted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any

TAXONOMY_VERSION = "opip-early-taxonomy-v1"
EXTENSION_POLICY_VERSION = "opip-extension-policy-v1"


class MarketPhase(str, Enum):
    """What the market is doing. Ordered dormant -> exhausted."""

    DORMANT = "DORMANT"
    COILED = "COILED"
    IGNITION = "IGNITION"
    EARLY_EXPANSION = "EARLY_EXPANSION"
    CONFIRMED_EXPANSION = "CONFIRMED_EXPANSION"
    LATE_EXTENSION = "LATE_EXTENSION"
    EXHAUSTION_RISK = "EXHAUSTION_RISK"


class EvidenceGrade(str, Enum):
    """What O'Pip has actually validated about a candidate."""

    OBSERVED = "OBSERVED"
    CORROBORATED = "CORROBORATED"
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"


class OperatorDisposition(str, Enum):
    """What the operator should do now. Never an entry authorization."""

    NO_ACTION = "NO_ACTION"
    MONITOR = "MONITOR"
    DEEP_REVIEW = "DEEP_REVIEW"
    DO_NOT_CHASE = "DO_NOT_CHASE"


class ValidationResult(str, Enum):
    """Explicit validation semantics. ``UNAVAILABLE`` is never ``PASS``."""

    PASS = "PASS"  # nosec B105 - validation verdict, not a credential
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_EVALUATED = "NOT_EVALUATED"


class ValidationClass(str, Enum):
    """How a validation result participates in promotion.

    ``MANDATORY``
        must be ``PASS`` for :attr:`EvidenceGrade.QUALIFIED`. Anything else,
        including ``UNAVAILABLE`` and ``NOT_EVALUATED``, fails closed.
    ``SOFT``
        may be ``UNAVAILABLE`` without blocking promotion, but never counts as
        corroboration while it is missing.
    ``CONFIDENCE``
        shifts the continuation score only.
    ``ADVISORY``
        cannot promote a candidate by itself and cannot block promotion.
    ``ACTIONABILITY``
        does not affect :attr:`EvidenceGrade.QUALIFIED`; it constrains
        :class:`OperatorDisposition` only.
    """

    MANDATORY = "MANDATORY"
    SOFT = "SOFT"
    CONFIDENCE = "CONFIDENCE"
    ADVISORY = "ADVISORY"
    ACTIONABILITY = "ACTIONABILITY"


#: Phases in which a discovery is genuinely early.
EARLY_PHASES = frozenset({MarketPhase.IGNITION, MarketPhase.EARLY_EXPANSION})

#: Phases in which the move has substantially happened already.
EXTENDED_PHASES = frozenset({MarketPhase.LATE_EXTENSION, MarketPhase.EXHAUSTION_RISK})

#: Legacy entry recommendations that deny actionability.
NON_ACTIONABLE_ENTRY_RECOMMENDATIONS = frozenset(
    {"WAIT_FOR_PULLBACK", "WATCH_ONLY", "TOO_EXTENDED", "HIGH_RISK_WATCH_ONLY"}
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def coerce_market_phase(value: Any, default: MarketPhase = MarketPhase.DORMANT) -> MarketPhase:
    """Accept a member or its value without going through ``str()``.

    ``str(MarketPhase.IGNITION)`` is ``'MarketPhase.IGNITION'`` for a
    ``(str, Enum)`` class, not ``'IGNITION'``. Round-tripping through
    ``str()`` therefore fails silently, so every coercion goes through here.
    """
    try:
        return MarketPhase(value)
    except (ValueError, TypeError):
        return default


def coerce_evidence_grade(
    value: Any, default: EvidenceGrade = EvidenceGrade.OBSERVED
) -> EvidenceGrade:
    try:
        return EvidenceGrade(value)
    except (ValueError, TypeError):
        return default


def coerce_operator_disposition(
    value: Any, default: OperatorDisposition = OperatorDisposition.NO_ACTION
) -> OperatorDisposition:
    try:
        return OperatorDisposition(value)
    except (ValueError, TypeError):
        return default


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


# ---------------------------------------------------------------------------
# Extension unification (root cause H)
# ---------------------------------------------------------------------------

#: ``movement_discovery_v2.evaluate_early_mover`` rule, verbatim.
LEGACY_MOVEMENT_DISCOVERY = "movement_discovery_v2"
#: ``explosion_state._phase_from_features`` rules, verbatim.
LEGACY_EXPLOSION_STATE = "explosion_state"


def legacy_movement_discovery_extended(*, momentum_1h_pct: Any, momentum_24h_pct: Any) -> bool:
    """Reproduce ``extended = day >= 15.0 or one_hour >= 6.0`` exactly."""
    return _finite(momentum_24h_pct) >= 15.0 or _finite(momentum_1h_pct) >= 6.0


def legacy_explosion_late_extension(
    *,
    momentum_1h_pct: Any,
    momentum_24h_pct: Any,
    distance_to_24h_high_pct: Any,
) -> bool:
    """Reproduce the ``explosion_state`` late-extension rule exactly."""
    day = _finite(momentum_24h_pct)
    one_hour = _finite(momentum_1h_pct)
    near_high = max(0.0, _finite(distance_to_24h_high_pct))
    return day >= 18.0 or (day >= 12.0 and near_high <= 1.0 and one_hour >= 2.5)


def legacy_explosion_exhaustion(
    *,
    momentum_1h_pct: Any,
    momentum_6h_pct: Any,
    momentum_24h_pct: Any,
    momentum_acceleration: Any = None,
) -> bool:
    """Reproduce the ``explosion_state`` exhaustion rule exactly."""
    day = _finite(momentum_24h_pct)
    one_hour = _finite(momentum_1h_pct)
    six_hour = _finite(momentum_6h_pct)
    acceleration = _finite_optional(momentum_acceleration)
    decelerating_large_move = day >= 12.0 and one_hour <= 0.25 and six_hour >= 4.0
    reversing_large_move = day >= 18.0 and acceleration is not None and acceleration < -0.25
    return decelerating_large_move or reversing_large_move


@dataclass(frozen=True)
class ExtensionState:
    """One canonical answer about how far a move has already travelled.

    ``policy_version`` makes the answer reproducible after a threshold change.
    ``agreeing_definitions`` / ``disagreeing_definitions`` let shadow
    comparison quantify the migration cost before any production gate moves.
    """

    policy_version: str
    extended: bool
    late_extension: bool
    exhaustion_risk: bool
    agreeing_definitions: tuple[str, ...]
    disagreeing_definitions: tuple[str, ...]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "extended": self.extended,
            "late_extension": self.late_extension,
            "exhaustion_risk": self.exhaustion_risk,
            "agreeing_definitions": list(self.agreeing_definitions),
            "disagreeing_definitions": list(self.disagreeing_definitions),
            "reasons": list(self.reasons),
        }


def extension_state(
    *,
    momentum_1h_pct: Any,
    momentum_6h_pct: Any,
    momentum_24h_pct: Any,
    distance_to_24h_high_pct: Any,
    momentum_acceleration: Any = None,
) -> ExtensionState:
    """Resolve the canonical extension state as the union of legacy rules.

    The union can only be more conservative than any single legacy rule, so
    adopting it for *labelling* cannot make an extended asset look early.
    It is deliberately not wired into any production admission threshold.
    """
    movement_extended = legacy_movement_discovery_extended(
        momentum_1h_pct=momentum_1h_pct,
        momentum_24h_pct=momentum_24h_pct,
    )
    explosion_late = legacy_explosion_late_extension(
        momentum_1h_pct=momentum_1h_pct,
        momentum_24h_pct=momentum_24h_pct,
        distance_to_24h_high_pct=distance_to_24h_high_pct,
    )
    exhaustion = legacy_explosion_exhaustion(
        momentum_1h_pct=momentum_1h_pct,
        momentum_6h_pct=momentum_6h_pct,
        momentum_24h_pct=momentum_24h_pct,
        momentum_acceleration=momentum_acceleration,
    )

    agreeing: list[str] = []
    disagreeing: list[str] = []
    reasons: list[str] = []
    if movement_extended:
        agreeing.append(LEGACY_MOVEMENT_DISCOVERY)
        reasons.append("movement-discovery extension thresholds are met")
    else:
        disagreeing.append(LEGACY_MOVEMENT_DISCOVERY)
    if explosion_late or exhaustion:
        agreeing.append(LEGACY_EXPLOSION_STATE)
        reasons.append("explosion-state late-extension or exhaustion thresholds are met")
    else:
        disagreeing.append(LEGACY_EXPLOSION_STATE)

    late_extension = movement_extended or explosion_late or exhaustion
    return ExtensionState(
        policy_version=EXTENSION_POLICY_VERSION,
        extended=late_extension,
        late_extension=late_extension and not exhaustion,
        exhaustion_risk=exhaustion,
        agreeing_definitions=tuple(agreeing),
        disagreeing_definitions=tuple(disagreeing),
        reasons=tuple(reasons),
    )


def compare_extension_definitions(
    *,
    momentum_1h_pct: Any,
    momentum_6h_pct: Any,
    momentum_24h_pct: Any,
    distance_to_24h_high_pct: Any,
    momentum_acceleration: Any = None,
) -> dict[str, Any]:
    """Shadow-compare each legacy extension rule against the canonical union."""
    canonical = extension_state(
        momentum_1h_pct=momentum_1h_pct,
        momentum_6h_pct=momentum_6h_pct,
        momentum_24h_pct=momentum_24h_pct,
        distance_to_24h_high_pct=distance_to_24h_high_pct,
        momentum_acceleration=momentum_acceleration,
    )
    movement_extended = legacy_movement_discovery_extended(
        momentum_1h_pct=momentum_1h_pct,
        momentum_24h_pct=momentum_24h_pct,
    )
    return {
        "policy_version": canonical.policy_version,
        "canonical_extended": canonical.extended,
        "legacy_movement_discovery_extended": movement_extended,
        "legacy_explosion_late_extension": legacy_explosion_late_extension(
            momentum_1h_pct=momentum_1h_pct,
            momentum_24h_pct=momentum_24h_pct,
            distance_to_24h_high_pct=distance_to_24h_high_pct,
        ),
        "legacy_explosion_exhaustion": legacy_explosion_exhaustion(
            momentum_1h_pct=momentum_1h_pct,
            momentum_6h_pct=momentum_6h_pct,
            momentum_24h_pct=momentum_24h_pct,
            momentum_acceleration=momentum_acceleration,
        ),
        # True whenever adopting the canonical union would relabel this row.
        "definitions_diverge": bool(canonical.disagreeing_definitions)
        and bool(canonical.agreeing_definitions),
        "production_gate_changed": False,
        "shadow_only": True,
    }


# ---------------------------------------------------------------------------
# MarketPhase resolution
# ---------------------------------------------------------------------------

#: Bandwidth/ATR percentile at or below which volatility counts as compressed.
#: Deliberately stricter than the scanner's 40th-percentile fine-timeframe
#: prefilter: ``COILED`` is an operator-facing claim about a coiled market,
#: not merely a reason to fetch finer candles.
COILED_PERCENTILE_CEILING = 25.0
#: Directional movement above which a market is no longer merely coiled.
COILED_MAX_ABS_MOMENTUM_1H_PCT = 0.5


def resolve_market_phase(
    *,
    momentum_1h_pct: Any,
    momentum_6h_pct: Any,
    momentum_24h_pct: Any,
    distance_to_24h_high_pct: Any,
    relative_volume: Any = None,
    momentum_acceleration: Any = None,
    bandwidth_percentile: Any = None,
    atr_percentile: Any = None,
) -> MarketPhase:
    """Resolve one :class:`MarketPhase` from decision-time features only.

    Extension dominates: a substantially completed move can never resolve to
    an early phase, which is the defect Issue #223 reported. ``COILED`` is
    admitted only when the existing compression evidence justifies it.
    """
    state = extension_state(
        momentum_1h_pct=momentum_1h_pct,
        momentum_6h_pct=momentum_6h_pct,
        momentum_24h_pct=momentum_24h_pct,
        distance_to_24h_high_pct=distance_to_24h_high_pct,
        momentum_acceleration=momentum_acceleration,
    )
    if state.exhaustion_risk:
        return MarketPhase.EXHAUSTION_RISK
    if state.extended:
        return MarketPhase.LATE_EXTENSION

    one_hour = _finite(momentum_1h_pct)
    six_hour = _finite(momentum_6h_pct)
    volume = max(0.0, _finite(relative_volume))
    acceleration = _finite_optional(momentum_acceleration)

    if one_hour >= 1.0 and six_hour >= 2.0 and volume >= 1.25:
        return MarketPhase.CONFIRMED_EXPANSION
    if one_hour >= 0.75 and six_hour >= 1.5:
        return MarketPhase.EARLY_EXPANSION
    if one_hour > 0.25 or (acceleration is not None and acceleration > 0.25):
        return MarketPhase.IGNITION

    bandwidth = _finite_optional(bandwidth_percentile)
    atr = _finite_optional(atr_percentile)
    compressed = [value for value in (bandwidth, atr) if value is not None]
    if (
        compressed
        and min(compressed) <= COILED_PERCENTILE_CEILING
        and abs(one_hour) <= COILED_MAX_ABS_MOMENTUM_1H_PCT
    ):
        return MarketPhase.COILED
    return MarketPhase.DORMANT


#: ``explosion_state`` phase strings that map 1:1 onto :class:`MarketPhase`.
_EXPLOSION_PHASE_ALIASES = {
    "DORMANT": MarketPhase.DORMANT,
    "IGNITION": MarketPhase.IGNITION,
    "EARLY_EXPANSION": MarketPhase.EARLY_EXPANSION,
    "CONFIRMED_EXPANSION": MarketPhase.CONFIRMED_EXPANSION,
    "LATE_EXTENSION": MarketPhase.LATE_EXTENSION,
    "EXHAUSTION_RISK": MarketPhase.EXHAUSTION_RISK,
}


def market_phase_from_explosion_phase(
    phase: Any,
    *,
    bandwidth_percentile: Any = None,
    atr_percentile: Any = None,
    momentum_1h_pct: Any = None,
) -> MarketPhase:
    """Adopt an existing ``ExplosionStateVector.phase`` without re-deriving it.

    ``DORMANT`` is refined to ``COILED`` when the same observation already
    shows volatility compression, which is the only new phase this taxonomy
    adds relative to ``explosion_state``.
    """
    token = phase.value if isinstance(phase, MarketPhase) else str(phase or "")
    resolved = _EXPLOSION_PHASE_ALIASES.get(token.strip().upper())
    if resolved is None:
        return MarketPhase.DORMANT
    if resolved is not MarketPhase.DORMANT:
        return resolved
    bandwidth = _finite_optional(bandwidth_percentile)
    atr = _finite_optional(atr_percentile)
    compressed = [value for value in (bandwidth, atr) if value is not None]
    if (
        compressed
        and min(compressed) <= COILED_PERCENTILE_CEILING
        and abs(_finite(momentum_1h_pct)) <= COILED_MAX_ABS_MOMENTUM_1H_PCT
    ):
        return MarketPhase.COILED
    return MarketPhase.DORMANT


def is_early_phase(phase: MarketPhase | str) -> bool:
    resolved = coerce_market_phase(phase, default=None)  # type: ignore[arg-type]
    return resolved in EARLY_PHASES


def is_extended_phase(phase: MarketPhase | str) -> bool:
    resolved = coerce_market_phase(phase, default=None)  # type: ignore[arg-type]
    return resolved in EXTENDED_PHASES


# ---------------------------------------------------------------------------
# OperatorDisposition resolution
# ---------------------------------------------------------------------------

def resolve_operator_disposition(
    *,
    phase: MarketPhase | str,
    grade: EvidenceGrade | str,
    entry_recommendation: Any = None,
    actionability_blocked: bool = False,
) -> OperatorDisposition:
    """Resolve what the operator should do now.

    Deliberately conservative: nothing here ever returns an entry
    authorization, and an extended move can only ever return
    :attr:`OperatorDisposition.DO_NOT_CHASE` no matter how strong the score is.
    """
    resolved_grade = coerce_evidence_grade(grade)
    if resolved_grade is EvidenceGrade.REJECTED:
        return OperatorDisposition.NO_ACTION

    if is_extended_phase(phase):
        return OperatorDisposition.DO_NOT_CHASE

    recommendation = str(entry_recommendation or "").strip().upper()
    if recommendation in NON_ACTIONABLE_ENTRY_RECOMMENDATIONS or actionability_blocked:
        # Qualified evidence without actionable geometry is still only a watch.
        return OperatorDisposition.MONITOR
    if resolved_grade is EvidenceGrade.QUALIFIED:
        return OperatorDisposition.DEEP_REVIEW
    if resolved_grade is EvidenceGrade.CORROBORATED:
        return OperatorDisposition.MONITOR
    return OperatorDisposition.NO_ACTION


def governor_stage_token(*, alert_eligible: bool) -> str:
    """Return the alert-governor stage token for a transition key.

    ``app.services.alert_governor.PRIORITY_TRANSITION_STAGES`` keys off the
    first ``:``-separated token. Issue #223 removes ``READY`` from operator
    text but must not silently change governor priority behaviour, so the
    token itself is preserved exactly.
    """
    return "READY" if alert_eligible else "WATCH"
