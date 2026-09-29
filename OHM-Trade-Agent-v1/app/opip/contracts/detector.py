"""Detector vocabulary and typed state/claim contracts (R3 F3).

This is the shared, pure vocabulary for the first stateful detector runtime.
It owns the detector lifecycle phase, the transition, the reset reason, the
versioned detector/policy tokens and the immutable ``DetectorState`` /
``DetectorClaim`` types.

Three boundaries are deliberate:

* The opportunity lifecycle is **not** here. ``episode_id`` exists only so the
  frozen contract's declared form is represented; F3 never mints one, and a
  claim that carries an episode identity is rejected. F4 owns deadlines and
  expiry.
* The identity of a claim is a pure function of the detector family/version,
  the applied policy version, the instrument, the transition and the sealed
  snapshot evidence identity. No wall clock and no process identity take part.
* Persistence is a typed second count on a one-minute evaluation grid. A bool
  is not an int and a fractional value is never truncated into an apparently
  valid state.

Nothing here reads a clock, the environment, the filesystem or the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.opip.contracts.features import DEFAULT_EVALUATION_GRID_SECONDS
from app.opip.contracts.serialization import stable_hash
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc

#: The sole active detector family for v1. The value is a durable vocabulary
#: token and may not be renamed without a contract change.
IGNITION_FAMILY = "IGNITION"

#: Applied detector implementation version. It is a deterministic code artifact,
#: not a caller argument and not a hidden input.
IGNITION_DETECTOR_VERSION = "ignition-detector-v1"

#: Applied detector policy version. The policy parameters (hysteresis,
#: asymmetric thresholds and the two-interval persistence rule) are a
#: deterministic, replayable code artifact tagged by this token.
IGNITION_POLICY_VERSION = "ignition-shadow-policy-v1"

#: The persistence counter accrues one evaluation interval at a time. It is the
#: detector-side mirror of the frozen one-minute evaluation grid.
PERSISTENCE_INTERVAL_SECONDS = DEFAULT_EVALUATION_GRID_SECONDS


class DetectorContractError(ValueError):
    """A structural contract violation. Always fails closed."""


class DetectorFamily(str, Enum):
    """Which detector family produced a state or claim."""

    IGNITION = IGNITION_FAMILY


class DetectorPhase(str, Enum):
    """The v1 detector lifecycle phase. There is no seventh phase."""

    DORMANT = "DORMANT"
    IGNITION = "IGNITION"


class DetectorTransition(str, Enum):
    """A phase change owned by the detector. ``NONE`` means no phase change."""

    NONE = "NONE"
    DORMANT_TO_IGNITION = "DORMANT_TO_IGNITION"
    IGNITION_TO_DORMANT = "IGNITION_TO_DORMANT"


class DetectorResetReason(str, Enum):
    """Why a reset-to-DORMANT evaluation returned no claim.

    ``NONE`` means the evaluation was not an epistemic reset. The two reasons
    are the OWNER-ratified ``ignition-shadow-policy-v1`` mapping:

    * ``MATERIAL_GAP`` — the sealed snapshot's covered window is not complete.
    * ``INSUFFICIENT_EVIDENCE`` — the warm-up state is not ``WARM`` or a
      required feature is absent, unsupported, ``NOT_RETAINED`` or otherwise
      unusable. Any other epistemic insufficiency that is not a structural
      contract violation also lands here.
    """

    NONE = "NONE"
    MATERIAL_GAP = "MATERIAL_GAP"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def require_enum(enum_type: type[Enum], value: Any, *, field_name: str) -> Any:
    """Coerce one enum token strictly; reject malformed or unsupported tokens.

    Accepts an existing member or its exact string token. Rejects bools,
    numbers and unknown strings, so a mistyped durable token is refused rather
    than silently substituted.
    """
    if isinstance(value, enum_type):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise DetectorContractError(f"{field_name} must be a {enum_type.__name__} token")
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise DetectorContractError(
                f"{field_name} has an unsupported token: {value!r}"
            ) from exc
    raise DetectorContractError(f"{field_name} must be a {enum_type.__name__} token")


def require_seconds(value: Any, *, field_name: str) -> int:
    """Validate one persistence second count.

    A bool is not an int, a float is never truncated, and the value must sit on
    the declared one-minute persistence grid and be non-negative.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise DetectorContractError(f"{field_name} must be an integer second count")
    if value < 0:
        raise DetectorContractError(f"{field_name} must be non-negative")
    if value % PERSISTENCE_INTERVAL_SECONDS != 0:
        raise DetectorContractError(
            f"{field_name} must be a multiple of the "
            f"{PERSISTENCE_INTERVAL_SECONDS}-second persistence grid"
        )
    return int(value)


def _require_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DetectorContractError(f"{field_name} is required")
    return text


def _require_optional_utc(value: Any, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise DetectorContractError(f"{field_name} must be a datetime or None")
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise DetectorContractError(str(exc)) from exc


@dataclass(frozen=True)
class DetectorState:
    """Deterministic detector state for one instrument.

    The state is the entire input the next evaluation needs beyond the sealed
    snapshot and the explicit evaluation instant. ``persistence_seconds`` is a
    single transition-evidence counter whose meaning is scoped by ``phase``:
    consecutive qualifying evaluations while ``DORMANT``, consecutive failed
    hold evaluations while ``IGNITION``.
    """

    instrument_version_id: str
    venue_instrument_id: str
    detector_family: DetectorFamily = DetectorFamily.IGNITION
    detector_version: str = IGNITION_DETECTOR_VERSION
    policy_version: str = IGNITION_POLICY_VERSION
    phase: DetectorPhase = DetectorPhase.DORMANT
    persistence_seconds: int = 0
    reset_reason: DetectorResetReason = DetectorResetReason.NONE
    transition: DetectorTransition = DetectorTransition.NONE
    last_evaluation_cutoff: datetime | None = None
    last_snapshot_id: str | None = None
    last_detector_input_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "instrument_version_id",
            _require_text(self.instrument_version_id, field_name="instrument_version_id"),
        )
        object.__setattr__(
            self,
            "venue_instrument_id",
            _require_text(self.venue_instrument_id, field_name="venue_instrument_id"),
        )
        object.__setattr__(
            self,
            "detector_family",
            require_enum(DetectorFamily, self.detector_family, field_name="detector_family"),
        )
        object.__setattr__(
            self,
            "detector_version",
            _require_text(self.detector_version, field_name="detector_version"),
        )
        object.__setattr__(
            self,
            "policy_version",
            _require_text(self.policy_version, field_name="policy_version"),
        )
        object.__setattr__(
            self, "phase", require_enum(DetectorPhase, self.phase, field_name="phase")
        )
        object.__setattr__(
            self,
            "persistence_seconds",
            require_seconds(self.persistence_seconds, field_name="persistence_seconds"),
        )
        object.__setattr__(
            self,
            "reset_reason",
            require_enum(
                DetectorResetReason, self.reset_reason, field_name="reset_reason"
            ),
        )
        object.__setattr__(
            self,
            "transition",
            require_enum(
                DetectorTransition, self.transition, field_name="transition"
            ),
        )
        object.__setattr__(
            self,
            "last_evaluation_cutoff",
            _require_optional_utc(
                self.last_evaluation_cutoff, field_name="last_evaluation_cutoff"
            ),
        )
        for name in ("last_snapshot_id", "last_detector_input_fingerprint"):
            value = getattr(self, name)
            if value is None:
                continue
            object.__setattr__(self, name, _require_text(value, field_name=name))

    def to_dict(self) -> dict[str, Any]:
        """Canonical serialization. Deterministic field order, no clock."""
        return {
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "detector_family": self.detector_family.value,
            "detector_version": self.detector_version,
            "policy_version": self.policy_version,
            "phase": self.phase.value,
            "persistence_seconds": self.persistence_seconds,
            "reset_reason": self.reset_reason.value,
            "transition": self.transition.value,
            "last_evaluation_cutoff": (
                None
                if self.last_evaluation_cutoff is None
                else self.last_evaluation_cutoff.isoformat()
            ),
            "last_snapshot_id": self.last_snapshot_id,
            "last_detector_input_fingerprint": self.last_detector_input_fingerprint,
        }


def detector_claim_identity(
    *,
    detector_family: DetectorFamily | str,
    detector_version: str,
    policy_version: str,
    instrument_version_id: str,
    venue_instrument_id: str,
    transition: DetectorTransition | str,
    snapshot_id: str,
    detector_input_fingerprint: str,
) -> tuple[str, str]:
    """Deterministic ``(claim_id, idempotency_key)`` for one transition.

    Bound to the detector family/version, the applied policy version, the
    instrument, the transition and the sealed snapshot evidence identity
    (``snapshot_id`` plus the ``DETIN`` detector-input fingerprint). No wall
    clock and no process identity take part, so the same transition repeated at
    two different wall-clock instants yields the same identity.
    """
    family = require_enum(DetectorFamily, detector_family, field_name="detector_family")
    move = require_enum(DetectorTransition, transition, field_name="transition")
    payload = {
        "detector_family": family.value,
        "detector_version": _require_text(detector_version, field_name="detector_version"),
        "policy_version": _require_text(policy_version, field_name="policy_version"),
        "instrument_version_id": _require_text(
            instrument_version_id, field_name="instrument_version_id"
        ),
        "venue_instrument_id": _require_text(
            venue_instrument_id, field_name="venue_instrument_id"
        ),
        "transition": move.value,
        "snapshot_id": _require_text(snapshot_id, field_name="snapshot_id"),
        "detector_input_fingerprint": _require_text(
            detector_input_fingerprint, field_name="detector_input_fingerprint"
        ),
    }
    return stable_hash("DCLM", payload), stable_hash("DCLMKEY", payload)


@dataclass(frozen=True)
class DetectorClaim:
    """One deterministic IGNITION claim for a ``DORMANT -> IGNITION`` transition.

    F3 emits a claim only for that transition. A claim carries no episode,
    deferral, deadline or terminal lifecycle reason, because the opportunity
    lifecycle is F4's and a claim that tries to mint an episode is rejected.
    """

    claim_id: str
    idempotency_key: str
    detector_family: DetectorFamily
    detector_version: str
    policy_version: str
    instrument_version_id: str
    venue_instrument_id: str
    phase: DetectorPhase
    transition: DetectorTransition
    snapshot_id: str
    detector_input_fingerprint: str
    evaluation_cutoff: datetime
    reasons: tuple[str, ...] = ()
    episode_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "claim_id", _require_text(self.claim_id, field_name="claim_id")
        )
        object.__setattr__(
            self,
            "idempotency_key",
            _require_text(self.idempotency_key, field_name="idempotency_key"),
        )
        object.__setattr__(
            self,
            "detector_family",
            require_enum(DetectorFamily, self.detector_family, field_name="detector_family"),
        )
        object.__setattr__(
            self,
            "detector_version",
            _require_text(self.detector_version, field_name="detector_version"),
        )
        object.__setattr__(
            self,
            "policy_version",
            _require_text(self.policy_version, field_name="policy_version"),
        )
        object.__setattr__(
            self,
            "instrument_version_id",
            _require_text(self.instrument_version_id, field_name="instrument_version_id"),
        )
        object.__setattr__(
            self,
            "venue_instrument_id",
            _require_text(self.venue_instrument_id, field_name="venue_instrument_id"),
        )
        phase = require_enum(DetectorPhase, self.phase, field_name="phase")
        transition = require_enum(
            DetectorTransition, self.transition, field_name="transition"
        )
        if (
            phase is not DetectorPhase.IGNITION
            or transition is not DetectorTransition.DORMANT_TO_IGNITION
        ):
            raise DetectorContractError(
                "F3 emits a claim only for the DORMANT -> IGNITION transition"
            )
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "transition", transition)
        object.__setattr__(
            self, "snapshot_id", _require_text(self.snapshot_id, field_name="snapshot_id")
        )
        object.__setattr__(
            self,
            "detector_input_fingerprint",
            _require_text(
                self.detector_input_fingerprint,
                field_name="detector_input_fingerprint",
            ),
        )
        cutoff = _require_optional_utc(
            self.evaluation_cutoff, field_name="evaluation_cutoff"
        )
        if cutoff is None:
            raise DetectorContractError("evaluation_cutoff is required")
        object.__setattr__(self, "evaluation_cutoff", cutoff)
        reasons = tuple(str(item) for item in self.reasons)
        if any(not item.strip() for item in reasons):
            raise DetectorContractError("claim reasons must be non-empty tokens")
        object.__setattr__(self, "reasons", reasons)
        if self.episode_id is not None:
            raise DetectorContractError(
                "F3 never creates an opportunity episode; episode_id must be None"
            )

        # Enforce the identity invariant the contract requires: a claim's
        # identity is a pure function of its evidence, so a directly
        # constructed claim whose identifiers do not match its own evidence is
        # refused rather than accepted as a valid-looking claim.
        expected_id, expected_key = detector_claim_identity(
            detector_family=self.detector_family,
            detector_version=self.detector_version,
            policy_version=self.policy_version,
            instrument_version_id=self.instrument_version_id,
            venue_instrument_id=self.venue_instrument_id,
            transition=self.transition,
            snapshot_id=self.snapshot_id,
            detector_input_fingerprint=self.detector_input_fingerprint,
        )
        if self.claim_id != expected_id or self.idempotency_key != expected_key:
            raise DetectorContractError(
                "claim identity does not match its evidence; build the claim with "
                "DetectorClaim.create()"
            )

    @classmethod
    def create(
        cls,
        *,
        detector_version: str,
        policy_version: str,
        instrument_version_id: str,
        venue_instrument_id: str,
        snapshot_id: str,
        detector_input_fingerprint: str,
        evaluation_cutoff: datetime,
        reasons: tuple[str, ...] = (),
        detector_family: DetectorFamily | str = DetectorFamily.IGNITION,
        phase: DetectorPhase | str = DetectorPhase.IGNITION,
        transition: DetectorTransition | str = DetectorTransition.DORMANT_TO_IGNITION,
    ) -> "DetectorClaim":
        """Build a claim whose identity is a pure function of its evidence."""
        claim_id, idempotency_key = detector_claim_identity(
            detector_family=detector_family,
            detector_version=detector_version,
            policy_version=policy_version,
            instrument_version_id=instrument_version_id,
            venue_instrument_id=venue_instrument_id,
            transition=transition,
            snapshot_id=snapshot_id,
            detector_input_fingerprint=detector_input_fingerprint,
        )
        return cls(
            claim_id=claim_id,
            idempotency_key=idempotency_key,
            detector_family=require_enum(
                DetectorFamily, detector_family, field_name="detector_family"
            ),
            detector_version=detector_version,
            policy_version=policy_version,
            instrument_version_id=instrument_version_id,
            venue_instrument_id=venue_instrument_id,
            phase=phase,
            transition=transition,
            snapshot_id=snapshot_id,
            detector_input_fingerprint=detector_input_fingerprint,
            evaluation_cutoff=evaluation_cutoff,
            reasons=reasons,
            episode_id=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "idempotency_key": self.idempotency_key,
            "detector_family": self.detector_family.value,
            "detector_version": self.detector_version,
            "policy_version": self.policy_version,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "phase": self.phase.value,
            "transition": self.transition.value,
            "snapshot_id": self.snapshot_id,
            "detector_input_fingerprint": self.detector_input_fingerprint,
            "evaluation_cutoff": self.evaluation_cutoff.isoformat(),
            "reasons": list(self.reasons),
            "episode_id": self.episode_id,
        }


__all__ = [
    "DetectorClaim",
    "DetectorContractError",
    "DetectorFamily",
    "DetectorPhase",
    "DetectorResetReason",
    "DetectorState",
    "DetectorTransition",
    "IGNITION_DETECTOR_VERSION",
    "IGNITION_FAMILY",
    "IGNITION_POLICY_VERSION",
    "PERSISTENCE_INTERVAL_SECONDS",
    "detector_claim_identity",
    "require_enum",
    "require_seconds",
]
