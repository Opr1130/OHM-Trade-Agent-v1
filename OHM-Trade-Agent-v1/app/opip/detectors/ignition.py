"""The pure v1 IGNITION detector runtime (R3 F3).

``evaluate(snapshot, prior_state, evaluation_time)`` is the frozen three-argument
interface. It is pure: it reads no clock, environment, filesystem, database or
network, and it holds no global mutable state. Its entire input is the sealed
``FeatureSnapshot``, the supplied prior ``DetectorState`` and the explicit
``evaluation_time``; its applied policy is a versioned code artifact of this
module.

SHADOW / NON-AUTHORITATIVE. This policy is a research policy. It grants no
trading, admission, allocation, risk, paper or funded authority, it is not
wired into ``run_cycle``, and it does not activate the Feature Bus. The
parameters below are the OWNER-ratified provisional shadow policy
``ignition-shadow-policy-v1``. They are NOT v1.4.3 architecture invariants and
are not permanent: changing any of them requires a new versioned policy and
fresh OWNER approval.

``DEBOUNCE_INTERVALS`` is deliberately zero. The two-interval entry/release
persistence and the asymmetric entry/hold thresholds *are* the v1 debounce
mechanism. There is no additional cooldown, and adding one would be a policy
change.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping

from app.opip.contracts.detector import (
    IGNITION_DETECTOR_VERSION,
    IGNITION_FAMILY,
    IGNITION_POLICY_VERSION,
    PERSISTENCE_INTERVAL_SECONDS,
    DetectorClaim,
    DetectorContractError,
    DetectorFamily,
    DetectorPhase,
    DetectorResetReason,
    DetectorState,
    DetectorTransition,
    require_enum,
    require_seconds,
)
from app.opip.contracts.enums import (
    CoverageState,
    Missingness,
    RestartState,
    TrendState,
)
from app.opip.contracts.features import (
    DEFAULT_EVALUATION_GRID_SECONDS,
    FeatureSnapshot,
)
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc
from app.opip.features.replay import detector_input_fingerprint

#: Applied policy version. OWNER-ratified provisional shadow policy.
POLICY_VERSION = IGNITION_POLICY_VERSION

#: Applied detector implementation version.
DETECTOR_VERSION = IGNITION_DETECTOR_VERSION

#: The declared evaluation grid. Longer-cadence inputs (for example a 15-minute
#: range) are feature values; they never redefine detector cadence.
EVALUATION_GRID_SECONDS = DEFAULT_EVALUATION_GRID_SECONDS

#: Two consecutive 60-second evaluations. Entry and release share the same
#: persistence duration; the asymmetry is in the predicates, not the duration.
ENTRY_PERSISTENCE_SECONDS = 2 * PERSISTENCE_INTERVAL_SECONDS
RELEASE_PERSISTENCE_SECONDS = 2 * PERSISTENCE_INTERVAL_SECONDS

#: Confirmations required in addition to the directional core.
ENTRY_MIN_CONFIRMATIONS = 2
HOLD_MIN_CONFIRMATIONS = 1

#: No extra cooldown. See the module docstring.
DEBOUNCE_INTERVALS = 0

#: Entry thresholds (stricter) and hold thresholds (looser). The asymmetry is
#: the hysteresis mechanism.
ENTRY_ACCELERATION_MIN = 0.0  # strictly greater than
HOLD_ACCELERATION_MIN = 0.0  # strictly greater than
ENTRY_VOLUME_EXPANSION_MIN = 1.25
HOLD_VOLUME_EXPANSION_MIN = 1.00
ENTRY_COMPRESSION_RELEASE_MIN = 0.35
HOLD_COMPRESSION_RELEASE_MIN = 0.15

#: The required PRESENT features. A snapshot that cannot supply all of them is
#: epistemically insufficient, never favourable.
REQUIRED_FEATURES: tuple[str, ...] = (
    "trend_state",
    "return_5m",
    "acceleration_5m_vs_15m",
    "volume_expansion_5m_vs_20m",
    "compression_release_score",
)

#: Deterministic claim reason tokens: which additional confirmations were
#: satisfied when the DORMANT -> IGNITION transition completed. Sorted before
#: emission so identical evidence yields an identical reason tuple.
REASON_ENTRY_ACCELERATION = "entry-confirmation:acceleration_5m_vs_15m>0"
REASON_ENTRY_VOLUME_EXPANSION = "entry-confirmation:volume_expansion_5m_vs_20m>=1.25"
REASON_ENTRY_COMPRESSION_RELEASE = (
    "entry-confirmation:compression_release_score>=0.35"
)

#: The directional core token carried on the claim for tamper-evident reading.
REASON_DIRECTIONAL_CORE = "directional-core:trend_state=UP&return_5m>0"

_UNUSABLE_TREND_TOKENS: frozenset[TrendState] = frozenset({TrendState.UNKNOWN})


def _fail(message: str) -> DetectorContractError:
    return DetectorContractError(message)


def _require_utc_instant(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise _fail(f"{field_name} must be an explicit datetime")
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise _fail(str(exc)) from exc


def _require_number(value: Any, *, field_name: str) -> float:
    """Validate one required numeric feature value.

    A bool is not an int, a string is never coerced, and a non-finite value is
    refused. All three are structural violations, not epistemic shortfalls.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{field_name} must be a numeric feature value")
    number = float(value)
    if not math.isfinite(number):
        raise _fail(f"{field_name} must be finite")
    return number


def _present_feature_value(
    values: Mapping[str, Any],
    missingness: Mapping[str, Any],
    name: str,
) -> Any:
    """Return the value of a required PRESENT feature, or ``None`` if unusable.

    Absence is epistemic: a missing key, a non-``PRESENT`` missingness stamp or
    a ``None`` value yields ``None`` (the caller maps that to
    ``INSUFFICIENT_EVIDENCE``). A *present* value that is not representable as
    the declared type is a structural violation and raises instead.
    """
    if name not in values:
        return None
    stamp = missingness.get(name)
    if stamp is None:
        return None
    state = require_enum(Missingness, stamp, field_name=f"missingness[{name}]")
    if state is not Missingness.PRESENT:
        return None
    value = values.get(name)
    if value is None:
        return None
    return value


class _SnapshotProjection:
    """The strictly validated view of one sealed snapshot for this detector.

    Construction either returns a usable projection or raises a structural
    contract violation. Epistemic shortfalls are reported as ``reset_reason``
    rather than raised, so the caller can return a safe DORMANT state.
    """

    __slots__ = ("reset_reason", "_numbers", "_trend")

    def __init__(
        self,
        *,
        reset_reason: DetectorResetReason,
        numbers: dict[str, float] | None = None,
        trend: TrendState | None = None,
    ) -> None:
        self.reset_reason = reset_reason
        self._numbers = numbers or {}
        self._trend = trend

    @property
    def usable(self) -> bool:
        return self.reset_reason is DetectorResetReason.NONE

    def number(self, name: str) -> float:
        return self._numbers[name]

    def directional_core(self) -> bool:
        """``trend_state == "UP"`` AND ``return_5m > 0.0``."""
        return self._trend is TrendState.UP and self.number("return_5m") > 0.0

    def entry_confirmations(self) -> tuple[str, ...]:
        """The satisfied entry confirmations, deterministically ordered."""
        satisfied: list[str] = []
        if self.number("acceleration_5m_vs_15m") > ENTRY_ACCELERATION_MIN:
            satisfied.append(REASON_ENTRY_ACCELERATION)
        if self.number("volume_expansion_5m_vs_20m") >= ENTRY_VOLUME_EXPANSION_MIN:
            satisfied.append(REASON_ENTRY_VOLUME_EXPANSION)
        if self.number("compression_release_score") >= ENTRY_COMPRESSION_RELEASE_MIN:
            satisfied.append(REASON_ENTRY_COMPRESSION_RELEASE)
        return tuple(sorted(satisfied))

    def entry_qualifies(self) -> bool:
        """Directional core plus at least TWO OF THREE confirmations."""
        return (
            self.directional_core()
            and len(self.entry_confirmations()) >= ENTRY_MIN_CONFIRMATIONS
        )

    def hold_satisfied(self) -> bool:
        """Directional core plus at least ONE OF THREE confirmations."""
        confirmations = 0
        if self.number("acceleration_5m_vs_15m") > HOLD_ACCELERATION_MIN:
            confirmations += 1
        if self.number("volume_expansion_5m_vs_20m") >= HOLD_VOLUME_EXPANSION_MIN:
            confirmations += 1
        if self.number("compression_release_score") >= HOLD_COMPRESSION_RELEASE_MIN:
            confirmations += 1
        return self.directional_core() and confirmations >= HOLD_MIN_CONFIRMATIONS


def _validate_structure(
    snapshot: Any,
    prior_state: Any,
    evaluation_time: Any,
) -> datetime:
    """Fail closed on every structural contract violation. Returns UTC instant."""
    if not isinstance(snapshot, FeatureSnapshot):
        raise _fail("snapshot must be a FeatureSnapshot")
    if not isinstance(prior_state, DetectorState):
        raise _fail("prior_state must be a DetectorState")

    instant = _require_utc_instant(evaluation_time, field_name="evaluation_time")
    if instant.microsecond:
        raise _fail("evaluation_time must sit on the declared 60-second grid")
    if int(instant.timestamp()) % EVALUATION_GRID_SECONDS != 0:
        raise _fail("evaluation_time must sit on the declared 60-second grid")

    grid = int(snapshot.evaluation_grid_seconds)
    if grid != EVALUATION_GRID_SECONDS:
        raise _fail(
            "snapshot evaluation_grid_seconds must be "
            f"{EVALUATION_GRID_SECONDS}, got {grid}"
        )

    cutoff = _require_utc_instant(
        snapshot.evaluation_cutoff, field_name="snapshot.evaluation_cutoff"
    )
    if instant != cutoff:
        raise _fail(
            "evaluation_time must equal snapshot.evaluation_cutoff; a stale "
            "snapshot cannot be reused as a later detector evaluation"
        )

    # Instrument identity: one instrument's phase or persistence evidence must
    # never be applied to another.
    if prior_state.instrument_version_id != snapshot.instrument_version_id:
        raise _fail("prior_state instrument_version_id does not match the snapshot")
    if prior_state.venue_instrument_id != snapshot.venue_instrument_id:
        raise _fail("prior_state venue_instrument_id does not match the snapshot")

    # Detector/policy binding: no silent substitution of an applied policy.
    if prior_state.detector_family is not DetectorFamily.IGNITION:
        raise _fail("prior_state detector_family is not IGNITION")
    if prior_state.detector_version != DETECTOR_VERSION:
        raise _fail(
            "prior_state detector_version does not match the applied detector version"
        )
    if prior_state.policy_version != POLICY_VERSION:
        raise _fail(
            "prior_state policy_version does not match the applied policy version"
        )

    # Typed persistence, re-validated at the evaluation boundary so a state
    # that bypassed construction cannot slip a fractional value through.
    require_seconds(prior_state.persistence_seconds, field_name="persistence_seconds")
    require_enum(DetectorPhase, prior_state.phase, field_name="phase")
    require_enum(
        DetectorResetReason, prior_state.reset_reason, field_name="reset_reason"
    )
    require_enum(
        DetectorTransition, prior_state.transition, field_name="transition"
    )

    if not isinstance(prior_state.last_evaluation_cutoff, (datetime, type(None))):
        raise _fail("prior_state last_evaluation_cutoff must be a datetime or None")

    require_enum(CoverageState, snapshot.coverage, field_name="coverage")
    require_enum(RestartState, snapshot.restart_state, field_name="restart_state")

    return instant


def _project(
    snapshot: FeatureSnapshot,
    evaluation_time: datetime,
) -> _SnapshotProjection:
    """Resolve epistemic sufficiency, then strictly validate present values.

    Precedence, when more than one condition holds: a material gap wins over an
    epistemic shortfall. Both produce no claim and a safe DORMANT state, so the
    precedence only selects which deterministic reason token is recorded.
    """
    coverage = require_enum(CoverageState, snapshot.coverage, field_name="coverage")
    restart_state = require_enum(
        RestartState, snapshot.restart_state, field_name="restart_state"
    )
    values = snapshot.values
    missingness = snapshot.missingness

    if coverage is not CoverageState.COMPLETE:
        return _SnapshotProjection(
            reset_reason=DetectorResetReason.MATERIAL_GAP
        )

    if restart_state is not RestartState.WARM:
        return _SnapshotProjection(
            reset_reason=DetectorResetReason.INSUFFICIENT_EVIDENCE
        )

    raw_trend = _present_feature_value(values, missingness, "trend_state")
    trend = (
        None
        if raw_trend is None
        else require_enum(TrendState, raw_trend, field_name="values[trend_state]")
    )

    numbers: dict[str, float] = {}
    for name in (
        "return_5m",
        "acceleration_5m_vs_15m",
        "volume_expansion_5m_vs_20m",
        "compression_release_score",
    ):
        value = _present_feature_value(values, missingness, name)
        if value is None:
            return _SnapshotProjection(
                reset_reason=DetectorResetReason.INSUFFICIENT_EVIDENCE
            )
        numbers[name] = _require_number(value, field_name=f"values[{name}]")

    if trend is None or trend in _UNUSABLE_TREND_TOKENS:
        return _SnapshotProjection(
            reset_reason=DetectorResetReason.INSUFFICIENT_EVIDENCE
        )

    return _SnapshotProjection(
        reset_reason=DetectorResetReason.NONE, numbers=numbers, trend=trend
    )


def _continued_state(
    prior_state: DetectorState,
    *,
    phase: DetectorPhase,
    persistence_seconds: int,
    reset_reason: DetectorResetReason,
    transition: DetectorTransition,
    snapshot: FeatureSnapshot,
    evaluation_time: datetime,
) -> DetectorState:
    return replace(
        prior_state,
        phase=phase,
        persistence_seconds=persistence_seconds,
        reset_reason=reset_reason,
        transition=transition,
        last_evaluation_cutoff=evaluation_time,
        last_snapshot_id=snapshot.snapshot_id,
        last_detector_input_fingerprint=detector_input_fingerprint(snapshot),
    )


def _phase_change(
    prior_phase: DetectorPhase, next_phase: DetectorPhase
) -> DetectorTransition:
    if prior_phase is next_phase:
        return DetectorTransition.NONE
    if next_phase is DetectorPhase.IGNITION:
        return DetectorTransition.DORMANT_TO_IGNITION
    return DetectorTransition.IGNITION_TO_DORMANT


def evaluate(
    snapshot: FeatureSnapshot,
    prior_state: DetectorState,
    evaluation_time: datetime,
) -> tuple[list[DetectorClaim], DetectorState]:
    """Evaluate one sealed snapshot and return ``(claims, next_state)``.

    A claim is emitted only when the entry persistence completes and the phase
    becomes ``IGNITION``. A release, a non-qualifying evaluation and every
    epistemic reset return an empty claim list.
    """
    instant = _validate_structure(snapshot, prior_state, evaluation_time)
    projection = _project(snapshot, instant)

    if not projection.usable:
        # Fail closed: no claim, safe DORMANT state, persistence reset to 0, and
        # no favourable evidence preserved.
        next_state = _continued_state(
            prior_state,
            phase=DetectorPhase.DORMANT,
            persistence_seconds=0,
            reset_reason=projection.reset_reason,
            transition=_phase_change(prior_state.phase, DetectorPhase.DORMANT),
            snapshot=snapshot,
            evaluation_time=instant,
        )
        return [], next_state

    if prior_state.phase is DetectorPhase.DORMANT:
        if not projection.entry_qualifies():
            return [], _continued_state(
                prior_state,
                phase=DetectorPhase.DORMANT,
                persistence_seconds=0,
                reset_reason=DetectorResetReason.NONE,
                transition=DetectorTransition.NONE,
                snapshot=snapshot,
                evaluation_time=instant,
            )
        persistence = prior_state.persistence_seconds + PERSISTENCE_INTERVAL_SECONDS
        if persistence < ENTRY_PERSISTENCE_SECONDS:
            return [], _continued_state(
                prior_state,
                phase=DetectorPhase.DORMANT,
                persistence_seconds=persistence,
                reset_reason=DetectorResetReason.NONE,
                transition=DetectorTransition.NONE,
                snapshot=snapshot,
                evaluation_time=instant,
            )
        # Persistence completed: DORMANT -> IGNITION, exactly one claim.
        claim = DetectorClaim.create(
            detector_family=IGNITION_FAMILY,
            detector_version=DETECTOR_VERSION,
            policy_version=POLICY_VERSION,
            instrument_version_id=snapshot.instrument_version_id,
            venue_instrument_id=snapshot.venue_instrument_id,
            snapshot_id=snapshot.snapshot_id,
            detector_input_fingerprint=detector_input_fingerprint(snapshot),
            evaluation_cutoff=instant,
            reasons=(
                REASON_DIRECTIONAL_CORE,
                *projection.entry_confirmations(),
            ),
        )
        next_state = _continued_state(
            prior_state,
            phase=DetectorPhase.IGNITION,
            persistence_seconds=0,
            reset_reason=DetectorResetReason.NONE,
            transition=DetectorTransition.DORMANT_TO_IGNITION,
            snapshot=snapshot,
            evaluation_time=instant,
        )
        return [claim], next_state

    # Already in IGNITION: hold or release.
    if projection.hold_satisfied():
        return [], _continued_state(
            prior_state,
            phase=DetectorPhase.IGNITION,
            persistence_seconds=0,
            reset_reason=DetectorResetReason.NONE,
            transition=DetectorTransition.NONE,
            snapshot=snapshot,
            evaluation_time=instant,
        )

    failed_hold = prior_state.persistence_seconds + PERSISTENCE_INTERVAL_SECONDS
    if failed_hold < RELEASE_PERSISTENCE_SECONDS:
        return [], _continued_state(
            prior_state,
            phase=DetectorPhase.IGNITION,
            persistence_seconds=failed_hold,
            reset_reason=DetectorResetReason.NONE,
            transition=DetectorTransition.NONE,
            snapshot=snapshot,
            evaluation_time=instant,
        )
    # Two consecutive failed holds: IGNITION -> DORMANT. No positive claim.
    return [], _continued_state(
        prior_state,
        phase=DetectorPhase.DORMANT,
        persistence_seconds=0,
        reset_reason=DetectorResetReason.NONE,
        transition=DetectorTransition.IGNITION_TO_DORMANT,
        snapshot=snapshot,
        evaluation_time=instant,
    )


__all__ = [
    "DEBOUNCE_INTERVALS",
    "DETECTOR_VERSION",
    "ENTRY_MIN_CONFIRMATIONS",
    "ENTRY_PERSISTENCE_SECONDS",
    "EVALUATION_GRID_SECONDS",
    "HOLD_MIN_CONFIRMATIONS",
    "POLICY_VERSION",
    "REASON_DIRECTIONAL_CORE",
    "REASON_ENTRY_ACCELERATION",
    "REASON_ENTRY_COMPRESSION_RELEASE",
    "REASON_ENTRY_VOLUME_EXPANSION",
    "RELEASE_PERSISTENCE_SECONDS",
    "REQUIRED_FEATURES",
    "evaluate",
]
