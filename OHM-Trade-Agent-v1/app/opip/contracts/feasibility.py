"""Feasibility & Safety vocabulary and typed decision contracts (R3 F5).

This is the shared, pure vocabulary for the third R3 slice: the F5 Feasibility &
Safety seam that sits in front of the (not yet built) forecast. It owns the
overall disposition tokens, the component status tokens, the required check
names, the versioned policy, the immutable per-component check, the deterministic
``FEAS:`` decision identity and the immutable ``FeasibilityDecision`` record.

Boundaries that are deliberate and enforced here:

* The overall disposition is exactly ``FEASIBLE`` / ``VETO`` /
  ``INSUFFICIENT_EVIDENCE``. There is no fourth token, and PASS/FAIL/ERROR/WATCH/
  REJECT/NO_TRADE are never overall dispositions.
* A component is exactly ``PASS`` / ``VETO`` / ``INSUFFICIENT_EVIDENCE`` /
  ``NOT_APPLICABLE``.
* The three required checks are exactly ``MARKET_DATA``,
  ``MARGIN_ELIGIBILITY`` and ``EXECUTION_LIQUIDITY``.
* The decision identity is a deterministic function of the decision schema
  version, the episode id, the source evidence fingerprint, the explicit
  evaluation time, the F5 version and the F5 policy version. No UUID, receipt
  timestamp, retry, pid or database sequence takes part, and a forged decision
  identity fails closed.
* Missing evidence is never favorable evidence: absent or unavailable required
  evidence is ``INSUFFICIENT_EVIDENCE``, never ``FEASIBLE``. A present but
  malformed structure raises :class:`FeasibilityContractError` and is never
  coerced into zero, default or pass.

Nothing here reads a clock, the environment, the filesystem or the network, and
nothing here grants trading, admission, paper, risk or funded authority.

SHADOW / NON-AUTHORITATIVE. This vocabulary is a research artifact. It is not
wired into ``run_cycle`` or ``scan_opportunities``, it activates no Feature Bus,
and it writes no canonical evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc

#: Schema version of the durable ``FeasibilityDecision`` record.
FEASIBILITY_DECISION_SCHEMA_VERSION = "feasibility-decision-v1"

#: Applied feasibility-seam implementation version. A deterministic code artifact.
FEASIBILITY_VERSION = "feasibility-seam-v1"

#: Applied feasibility policy version. A deterministic, replayable code artifact
#: tagged by this token; it is not a caller-selected input.
FEASIBILITY_POLICY_VERSION = "feasibility-shadow-policy-v1"

#: Semantic prefix of the deterministic ``FEAS:`` decision identity.
FEASIBILITY_DECISION_ID_PREFIX = "FEAS"

#: Semantic prefix of the deterministic ``FEASEV:`` evidence fingerprint.
FEASIBILITY_EVIDENCE_FINGERPRINT_PREFIX = "FEASEV"


class FeasibilityContractError(ValueError):
    """A structural contract violation. Always fails closed."""


class FeasibilityDisposition(str, Enum):
    """The F5 overall disposition. There is no fourth token."""

    FEASIBLE = "FEASIBLE"
    VETO = "VETO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class FeasibilityCheckName(str, Enum):
    """The exactly three required pre-forecast feasibility checks."""

    MARKET_DATA = "MARKET_DATA"
    MARGIN_ELIGIBILITY = "MARGIN_ELIGIBILITY"
    EXECUTION_LIQUIDITY = "EXECUTION_LIQUIDITY"


class FeasibilityCheckStatus(str, Enum):
    """One component's result. ``NOT_APPLICABLE`` never blocks."""

    PASS = "PASS"
    VETO = "VETO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


#: The deterministic evaluation / aggregation order. It matches the live scan's
#: hard-filter order (market data, then margin, then execution quality).
FEASIBILITY_CHECK_ORDER: tuple[FeasibilityCheckName, ...] = (
    FeasibilityCheckName.MARKET_DATA,
    FeasibilityCheckName.MARGIN_ELIGIBILITY,
    FeasibilityCheckName.EXECUTION_LIQUIDITY,
)


def require_feasibility_enum(
    enum_type: type[Enum], value: Any, *, field_name: str
) -> Any:
    """Coerce one enum token strictly; reject malformed or unsupported tokens.

    Accepts an existing member or its exact string token. Rejects bools,
    numbers and unknown strings, so a mistyped durable token is refused rather
    than silently substituted.
    """
    if isinstance(value, enum_type):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise FeasibilityContractError(
            f"{field_name} must be a {enum_type.__name__} token"
        )
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise FeasibilityContractError(
                f"{field_name} has an unsupported token: {value!r}"
            ) from exc
    raise FeasibilityContractError(f"{field_name} must be a {enum_type.__name__} token")


def require_feasibility_text(value: Any, *, field_name: str) -> str:
    """Validate one required canonical text token.

    A number or bool is never coerced into text, and a token with surrounding
    whitespace is refused so it cannot silently diverge from its digest.
    """
    if not isinstance(value, str):
        raise FeasibilityContractError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise FeasibilityContractError(
            f"{field_name} must be a non-empty, whitespace-free token"
        )
    return value


def require_feasibility_utc(value: Any, *, field_name: str) -> datetime:
    """Validate one required explicit UTC instant (naive/malformed fails closed)."""
    if not isinstance(value, datetime):
        raise FeasibilityContractError(f"{field_name} must be an explicit datetime")
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise FeasibilityContractError(str(exc)) from exc


def _parse_persisted_utc(value: Any, *, field_name: str) -> datetime:
    """Strictly parse one durable UTC instant at a durability trust boundary."""
    if not isinstance(value, str):
        raise FeasibilityContractError(f"{field_name} must be an ISO-8601 UTC string")
    if value == "" or value != value.strip():
        raise FeasibilityContractError(
            f"{field_name} must be a non-empty, whitespace-free ISO-8601 UTC string"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeasibilityContractError(
            f"{field_name} must be an ISO-8601 UTC instant"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FeasibilityContractError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise FeasibilityContractError(f"{field_name} must be a UTC instant")
    return parsed.astimezone(timezone.utc)


def _require_durable_mapping(
    value: Any, *, field_name: str, expected_keys: tuple[str, ...]
) -> Mapping[str, Any]:
    """Refuse a non-mapping, or one whose key set is not exactly ``expected_keys``."""
    if not isinstance(value, Mapping):
        raise FeasibilityContractError(f"{field_name} must be a mapping")
    present = set(value.keys())
    expected = set(expected_keys)
    missing = sorted(expected - present)
    if missing:
        raise FeasibilityContractError(
            f"{field_name} is missing mandatory keys: " + ", ".join(missing)
        )
    unknown = sorted(str(key) for key in present - expected)
    if unknown:
        raise FeasibilityContractError(
            f"{field_name} carries unknown keys: " + ", ".join(unknown)
        )
    return value


def _freeze_fingerprint_value(value: Any, *, field_name: str) -> Any:
    """Return a canonical, JSON-serializable primitive for a fingerprint.

    Only ``None``, ``str``, finite ``int``/``float``, ``bool``, and sequences of
    such primitives are accepted. Any other object is refused rather than
    serialized through ``repr``, so an evidence fingerprint can never silently
    depend on object identity or memory layout.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise FeasibilityContractError(f"{field_name} must be finite")
        return value
    if isinstance(value, (list, tuple)):
        return [
            _freeze_fingerprint_value(item, field_name=field_name) for item in value
        ]
    if isinstance(value, Mapping):
        return {
            str(key): _freeze_fingerprint_value(
                item, field_name=f"{field_name}[{key}]"
            )
            for key, item in value.items()
        }
    raise FeasibilityContractError(
        f"{field_name} must be a canonical primitive, not {type(value).__name__}"
    )


def feasibility_evidence_fingerprint(payload: Mapping[str, Any]) -> str:
    """The deterministic ``FEASEV:<digest>`` fingerprint of normalized evidence.

    The caller passes only the normalized F5-required inputs (primitives and
    primitive sequences). The fingerprint is a pure function of those inputs; it
    is not derived from an object repr, and it changes if any required input
    changes.
    """
    if not isinstance(payload, Mapping):
        raise FeasibilityContractError("evidence fingerprint payload must be a mapping")
    normalized = {
        require_feasibility_text(str(key), field_name="fingerprint key"):
        _freeze_fingerprint_value(value, field_name=f"fingerprint[{key}]")
        for key, value in payload.items()
    }
    return stable_hash(FEASIBILITY_EVIDENCE_FINGERPRINT_PREFIX, normalized)


def canonical_check_sequence(checks: Any) -> tuple[tuple[str, str], ...]:
    """Return the canonical ``(name, status)`` sequence for an identity payload.

    A non-sequence, a non-``FeasibilityCheck`` member, an unknown token or a
    non-unique name is refused rather than coerced. The canonical sequence is
    bound into the decision identity so a tampered disposition, a dropped veto or
    a reordered/replaced check list changes the identity and fails closed.
    """
    if isinstance(checks, (str, bytes)) or not isinstance(checks, (list, tuple)):
        raise FeasibilityContractError("checks must be a list or tuple of checks")
    canonical: list[tuple[str, str]] = []
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, FeasibilityCheck):
            raise FeasibilityContractError("checks must be FeasibilityCheck values")
        name = check.name.value
        if name in seen:
            raise FeasibilityContractError("checks must not repeat a component")
        seen.add(name)
        canonical.append((name, check.status.value))
    return tuple(canonical)


def feasibility_decision_identity(
    *,
    decision_schema_version: str = FEASIBILITY_DECISION_SCHEMA_VERSION,
    episode_id: str,
    source_claim_id: str,
    instrument_version_id: str,
    venue_instrument_id: str,
    detector_snapshot_id: str,
    evidence_fingerprint: str,
    evaluation_time: datetime,
    disposition: FeasibilityDisposition | str,
    checks: Any,
    feasibility_version: str = FEASIBILITY_VERSION,
    policy_version: str = FEASIBILITY_POLICY_VERSION,
) -> str:
    """The deterministic ``FEAS:<digest>`` identity of one feasibility decision.

    Identity binds the decision schema version, the preserved F4 lineage
    (episode id, source claim id, instrument version id, venue instrument id,
    detector snapshot id), the source evidence fingerprint, the explicit
    evaluation time, the F5 version, the F5 policy version, the overall
    disposition and the canonical ordered ``(name, status)`` check sequence.

    Binding the lineage and the disposition/checks makes a durable record
    tamper-evident: changing any copied lineage field, the disposition, or the
    recorded checks changes the identity, so a forged decision fails closed. The
    identity contains no UUID, receipt timestamp, retry count, process identity
    or database sequence, so re-deriving the same decision yields the same id.
    """
    instant = require_feasibility_utc(evaluation_time, field_name="evaluation_time")
    disposition_token = require_feasibility_enum(
        FeasibilityDisposition, disposition, field_name="disposition"
    )
    payload = {
        "decision_schema_version": require_feasibility_text(
            decision_schema_version, field_name="decision_schema_version"
        ),
        "episode_id": require_feasibility_text(episode_id, field_name="episode_id"),
        "source_claim_id": require_feasibility_text(
            source_claim_id, field_name="source_claim_id"
        ),
        "instrument_version_id": require_feasibility_text(
            instrument_version_id, field_name="instrument_version_id"
        ),
        "venue_instrument_id": require_feasibility_text(
            venue_instrument_id, field_name="venue_instrument_id"
        ),
        "detector_snapshot_id": require_feasibility_text(
            detector_snapshot_id, field_name="detector_snapshot_id"
        ),
        "evidence_fingerprint": require_feasibility_text(
            evidence_fingerprint, field_name="evidence_fingerprint"
        ),
        "evaluation_time": iso_z(instant, field_name="evaluation_time"),
        "feasibility_version": require_feasibility_text(
            feasibility_version, field_name="feasibility_version"
        ),
        "policy_version": require_feasibility_text(
            policy_version, field_name="policy_version"
        ),
        "disposition": disposition_token.value,
        "checks": [list(item) for item in canonical_check_sequence(checks)],
    }
    return stable_hash(FEASIBILITY_DECISION_ID_PREFIX, payload)


@dataclass(frozen=True)
class FeasibilityPolicy:
    """The applied, versioned feasibility policy.

    The policy is a deterministic code artifact. A policy that does not carry
    the ratified version tokens is refused rather than silently substituted, so
    a caller cannot select an unratified policy.
    """

    decision_schema_version: str = FEASIBILITY_DECISION_SCHEMA_VERSION
    feasibility_version: str = FEASIBILITY_VERSION
    policy_version: str = FEASIBILITY_POLICY_VERSION

    def __post_init__(self) -> None:
        for name, expected in (
            ("decision_schema_version", FEASIBILITY_DECISION_SCHEMA_VERSION),
            ("feasibility_version", FEASIBILITY_VERSION),
            ("policy_version", FEASIBILITY_POLICY_VERSION),
        ):
            value = require_feasibility_text(getattr(self, name), field_name=name)
            if value != expected:
                raise FeasibilityContractError(f"{name} is not the ratified {expected}")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_schema_version": self.decision_schema_version,
            "feasibility_version": self.feasibility_version,
            "policy_version": self.policy_version,
        }


#: Exact durable key set of ``FeasibilityCheck.to_dict()``.
_CHECK_DURABLE_KEYS: tuple[str, ...] = (
    "name",
    "status",
    "reason",
)


@dataclass(frozen=True)
class FeasibilityCheck:
    """One immutable required-check result."""

    name: FeasibilityCheckName
    status: FeasibilityCheckStatus
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            require_feasibility_enum(
                FeasibilityCheckName, self.name, field_name="check.name"
            ),
        )
        object.__setattr__(
            self,
            "status",
            require_feasibility_enum(
                FeasibilityCheckStatus, self.status, field_name="check.status"
            ),
        )
        if not isinstance(self.reason, str):
            raise FeasibilityContractError("check.reason must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "FeasibilityCheck":
        body = _require_durable_mapping(
            raw, field_name="check", expected_keys=_CHECK_DURABLE_KEYS
        )
        return cls(
            name=body["name"],
            status=body["status"],
            reason=body["reason"],
        )


#: Exact durable key set of ``FeasibilityDecision.to_dict()``. ``from_dict``
#: refuses a mapping that omits a mandatory key or carries an unknown one, so a
#: drifted or tampered durable record cannot be silently reconstituted.
_DECISION_DURABLE_KEYS: tuple[str, ...] = (
    "decision_id",
    "decision_schema_version",
    "feasibility_version",
    "policy_version",
    "episode_id",
    "source_claim_id",
    "instrument_version_id",
    "venue_instrument_id",
    "detector_snapshot_id",
    "evaluation_time",
    "evidence_fingerprint",
    "disposition",
    "checks",
)


@dataclass(frozen=True)
class FeasibilityDecision:
    """One immutable feasibility decision and its preserved F4 lineage.

    Lineage reuses the identities already carried by the frozen F4 episode; no
    episode is minted, no lifecycle state is mutated, and no detector transition
    is reinterpreted. The decision is immutable and its ``FEAS:`` identity is
    re-derived on construction, so a forged identity fails closed.
    """

    decision_id: str
    decision_schema_version: str
    feasibility_version: str
    policy_version: str
    episode_id: str
    source_claim_id: str
    instrument_version_id: str
    venue_instrument_id: str
    detector_snapshot_id: str
    evaluation_time: datetime
    evidence_fingerprint: str
    disposition: FeasibilityDisposition
    checks: tuple[FeasibilityCheck, ...]

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "episode_id",
            "source_claim_id",
            "instrument_version_id",
            "venue_instrument_id",
            "detector_snapshot_id",
            "evidence_fingerprint",
        ):
            object.__setattr__(
                self, name, require_feasibility_text(getattr(self, name), field_name=name)
            )

        # The decision's version fields are bound to the ratified code artifacts,
        # so a reconstituted decision carrying an unratified schema/feasibility/
        # policy tag is refused rather than accepted.
        for name, expected in (
            ("decision_schema_version", FEASIBILITY_DECISION_SCHEMA_VERSION),
            ("feasibility_version", FEASIBILITY_VERSION),
            ("policy_version", FEASIBILITY_POLICY_VERSION),
        ):
            if getattr(self, name) != expected:
                raise FeasibilityContractError(f"{name} is not the ratified {expected}")

        object.__setattr__(
            self,
            "evaluation_time",
            require_feasibility_utc(self.evaluation_time, field_name="evaluation_time"),
        )
        object.__setattr__(
            self,
            "disposition",
            require_feasibility_enum(
                FeasibilityDisposition, self.disposition, field_name="disposition"
            ),
        )

        checks = tuple(self.checks)
        for check in checks:
            if not isinstance(check, FeasibilityCheck):
                raise FeasibilityContractError(
                    "checks must be FeasibilityCheck values"
                )
        object.__setattr__(self, "checks", checks)
        self._validate_check_sequence_and_aggregation()

        expected = feasibility_decision_identity(
            decision_schema_version=self.decision_schema_version,
            episode_id=self.episode_id,
            source_claim_id=self.source_claim_id,
            instrument_version_id=self.instrument_version_id,
            venue_instrument_id=self.venue_instrument_id,
            detector_snapshot_id=self.detector_snapshot_id,
            evidence_fingerprint=self.evidence_fingerprint,
            evaluation_time=self.evaluation_time,
            disposition=self.disposition,
            checks=self.checks,
            feasibility_version=self.feasibility_version,
            policy_version=self.policy_version,
        )
        if self.decision_id != expected:
            raise FeasibilityContractError(
                "decision_id does not match its binding; build decisions with the "
                "feasibility seam"
            )

    def _validate_check_sequence_and_aggregation(self) -> None:
        """Enforce the canonical ordered check sequence, then the aggregation rule.

        A decision must record a non-empty ordered *prefix* of the required checks
        with no duplicate component. A ``VETO`` short-circuits, so it may only be
        the last recorded check; a decision with no ``VETO`` must record every
        required check. Combined with the identity binding, this refuses a
        tampered durable record - an emptied, dropped, reordered or replaced check
        list - instead of letting it claim ``FEASIBLE``.
        """
        checks = self.checks
        names = [check.name for check in checks]
        if not names or names != list(FEASIBILITY_CHECK_ORDER[: len(names)]):
            raise FeasibilityContractError(
                "checks must be a non-empty ordered prefix of the required checks"
            )
        # MARKET_DATA and EXECUTION_LIQUIDITY are always required; only the
        # SHORT-only margin applicability can be NOT_APPLICABLE. A tampered
        # record cannot mark an always-required check inapplicable to reach
        # FEASIBLE without proving it.
        for check in checks:
            if (
                check.status is FeasibilityCheckStatus.NOT_APPLICABLE
                and check.name is not FeasibilityCheckName.MARGIN_ELIGIBILITY
            ):
                raise FeasibilityContractError(
                    "NOT_APPLICABLE is only valid for MARGIN_ELIGIBILITY"
                )
        veto_positions = [
            index
            for index, check in enumerate(checks)
            if check.status is FeasibilityCheckStatus.VETO
        ]
        if veto_positions and veto_positions != [len(checks) - 1]:
            raise FeasibilityContractError(
                "a VETO short-circuits, so it must be the last recorded check"
            )
        if not veto_positions and len(checks) != len(FEASIBILITY_CHECK_ORDER):
            raise FeasibilityContractError(
                "a non-veto decision must carry every required check"
            )
        if veto_positions:
            expected = FeasibilityDisposition.VETO
        elif any(
            check.status is FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE
            for check in checks
        ):
            expected = FeasibilityDisposition.INSUFFICIENT_EVIDENCE
        else:
            expected = FeasibilityDisposition.FEASIBLE
        if self.disposition is not expected:
            raise FeasibilityContractError(
                "disposition does not follow the aggregation rule for its checks"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "decision_schema_version": self.decision_schema_version,
            "feasibility_version": self.feasibility_version,
            "policy_version": self.policy_version,
            "episode_id": self.episode_id,
            "source_claim_id": self.source_claim_id,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "detector_snapshot_id": self.detector_snapshot_id,
            "evaluation_time": iso_z(
                self.evaluation_time, field_name="evaluation_time"
            ),
            "evidence_fingerprint": self.evidence_fingerprint,
            "disposition": self.disposition.value,
            "checks": [check.to_dict() for check in self.checks],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "FeasibilityDecision":
        """Reconstitute one durable decision, re-running every constructor invariant.

        This is a durability trust boundary: the mapping must carry exactly the
        canonical key set, tokens are never coerced, the evaluation instant must
        be an explicit UTC ISO-8601 string, and the reconstructed decision is put
        back through ``__post_init__`` so its identity and aggregation are
        re-verified. A malformed, drifted or forged durable record is refused.
        """
        body = _require_durable_mapping(
            raw, field_name="decision", expected_keys=_DECISION_DURABLE_KEYS
        )
        raw_checks = body["checks"]
        if not isinstance(raw_checks, (list, tuple)):
            raise FeasibilityContractError("decision.checks must be a list")
        return cls(
            decision_id=body["decision_id"],
            decision_schema_version=body["decision_schema_version"],
            feasibility_version=body["feasibility_version"],
            policy_version=body["policy_version"],
            episode_id=body["episode_id"],
            source_claim_id=body["source_claim_id"],
            instrument_version_id=body["instrument_version_id"],
            venue_instrument_id=body["venue_instrument_id"],
            detector_snapshot_id=body["detector_snapshot_id"],
            evaluation_time=_parse_persisted_utc(
                body["evaluation_time"], field_name="evaluation_time"
            ),
            evidence_fingerprint=body["evidence_fingerprint"],
            disposition=body["disposition"],
            checks=tuple(FeasibilityCheck.from_dict(item) for item in raw_checks),
        )


__all__ = [
    "FEASIBILITY_CHECK_ORDER",
    "FEASIBILITY_DECISION_ID_PREFIX",
    "FEASIBILITY_DECISION_SCHEMA_VERSION",
    "FEASIBILITY_EVIDENCE_FINGERPRINT_PREFIX",
    "FEASIBILITY_POLICY_VERSION",
    "FEASIBILITY_VERSION",
    "FeasibilityCheck",
    "FeasibilityCheckName",
    "FeasibilityCheckStatus",
    "FeasibilityContractError",
    "FeasibilityDecision",
    "FeasibilityDisposition",
    "FeasibilityPolicy",
    "canonical_check_sequence",
    "feasibility_decision_identity",
    "feasibility_evidence_fingerprint",
    "require_feasibility_enum",
    "require_feasibility_text",
    "require_feasibility_utc",
]
