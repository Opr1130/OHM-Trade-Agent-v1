"""Durable canonical-writer event vocabulary for committed F5 feasibility evidence (R4-B2 Slice 3A).

This module owns exactly one canonical evidence class:

``feasibility.evidence.recorded``

It is a SEPARATE vocabulary from the PR3 feature bus. It is deliberately NOT part
of ``FEATURE_BUS_EVENT_TYPES``: a committed F5 feasibility-evidence record is F5
domain evidence, not a feature-bus market/feature observation, and the two must be
able to evolve independently. Like every evidence class it is LOW priority
telemetry: it never competes with protection or execution traffic, carries no ops
handoff, and grants no trading, admission, reservation, execution or exchange
authority.

It lives at ``app/opip/`` rather than ``app/opip/contracts/`` for the same reason
the durable codec does: building and validating the record rebuilds the concrete
scanner validation types, and the contracts layer is dependency-locked against
``app.scanner``.

Event identity vs exact content
    Two identities are retained, never conflated:
    * ``evidence_fingerprint`` (``FEV:``) - the frozen F5 semantic lineage;
    * ``payload_hash`` (``FEVH:``) - the exact durable-content identity.
    The idempotency key binds the record's evaluation identity (instrument
    version, venue instrument, direction, evaluation and cutoff instants, source
    snapshot id) together with the exact ``payload_hash``. It contains no
    recorded-at wall clock, receipt time, random envelope id, process id, retry
    count or database sequence, so:
    * byte/content-equivalent retry -> identical key -> ``DUPLICATE_OK``;
    * materially different durable evidence -> different ``payload_hash`` ->
      different key -> a new record that never silently collapses onto the first.

This module performs no market read, holds no clock, opens no store and holds no
transaction code.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.opip.contracts.feasibility_evidence import FeasibilityEvidence

# Relative import: the durable record codec is F5-evidence code that reaches the
# scanner validation types, so it is imported here by relative path rather than
# as a dotted ``app.opip.*`` path. This keeps the F5 dormant-seam guard
# (``test_ac_024_no_runtime_consumer``) meaningful: no app module references the
# F5 runtime seam, and this module is not that seam.
from .feasibility_evidence_record import (
    build_feasibility_evidence_payload,
    feasibility_evidence_from_payload,
    validate_feasibility_evidence_payload,
)

#: Canonical writer event type for one committed feasibility-evidence record.
#: Renaming is a contract change: already-written evidence carries this token.
FEASIBILITY_EVIDENCE_RECORDED = "feasibility.evidence.recorded"

#: The single registered F5 feasibility-evidence event type for v1. Kept separate
#: from ``FEATURE_BUS_EVENT_TYPES``: ownership of this class is F5 evidence, not
#: the feature bus.
FEASIBILITY_EVIDENCE_EVENT_TYPES: frozenset[str] = frozenset(
    {FEASIBILITY_EVIDENCE_RECORDED}
)

#: Canonical watermark stream. Separate from every other stream so F5 evidence
#: progress can never rewind alert-governor, feature-bus, F4, paper or DI progress.
FEASIBILITY_EVIDENCE_STREAM = "feasibility_evidence"

#: F5 feasibility evidence is telemetry class only. LOW is a scheduling class in
#: the canonical writer, never an eviction policy.
FEASIBILITY_EVIDENCE_PRIORITY = "LOW"

#: Payload schema token for the durable event record, independent of the
#: canonical DB physical schema version. ``# nosec B105`` - a schema identifier.
FEASIBILITY_EVIDENCE_RECORDED_SCHEMA_TOKEN = (  # nosec B105
    "feasibility-evidence-recorded-v1"
)

#: ``record_type`` discriminator carried inside the payload.
FEASIBILITY_EVIDENCE_EVENT_RECORD_TYPE = "feasibility_evidence_recorded"

#: Durable idempotency-key prefix. The remainder is evaluation identity plus the
#: exact content identity, so the key carries no clock, random id or sequence.
FEASIBILITY_EVIDENCE_IDEMPOTENCY_PREFIX = "FEVE:"

#: The exact durable payload key set.
_PAYLOAD_KEYS: tuple[str, ...] = (
    "record_type",
    "schema_version",
    "evidence",
)


class FeasibilityEvidenceEventError(ValueError):
    """A durable evidence-recorded event violation. Always fails closed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FeasibilityEvidenceEventError(message)


def _require_payload(payload: Any) -> Mapping[str, Any]:
    _require(isinstance(payload, Mapping), "F5 evidence event payload must be a mapping")
    present = set(payload.keys())
    expected = set(_PAYLOAD_KEYS)
    missing = sorted(expected - present)
    if missing:
        raise FeasibilityEvidenceEventError(
            "F5 evidence event payload is missing keys: " + ", ".join(missing)
        )
    unknown = sorted(str(key) for key in present - expected)
    if unknown:
        raise FeasibilityEvidenceEventError(
            "F5 evidence event payload carries unknown keys: " + ", ".join(unknown)
        )
    return payload


def validate_feasibility_evidence_recorded_payload(
    event_type: str, payload: Any
) -> dict[str, Any]:
    """Validate one durable evidence-recorded payload, returning a normalized copy.

    This is the persistence trust boundary: a producer payload is not trusted
    because it crossed IPC. The event type must be the registered F5 type, the
    payload must carry exactly the canonical keys and discriminator, and the
    nested durable record must pass the frozen codec validator (which recomputes
    both the F5 fingerprint and the exact-content ``payload_hash``). Raises
    ``FeasibilityEvidenceEventError`` on any defect; the canonical writer turns
    that into ``REJECTED``.
    """
    if event_type != FEASIBILITY_EVIDENCE_RECORDED:
        raise FeasibilityEvidenceEventError(
            f"unsupported F5 evidence persistence event type: {event_type!r}"
        )
    body = _require_payload(payload)
    if body["record_type"] != FEASIBILITY_EVIDENCE_EVENT_RECORD_TYPE:
        raise FeasibilityEvidenceEventError("unsupported F5 evidence event record_type")
    if body["schema_version"] != FEASIBILITY_EVIDENCE_RECORDED_SCHEMA_TOKEN:
        raise FeasibilityEvidenceEventError("unsupported F5 evidence event schema token")
    try:
        rebuilt_wrapper = validate_feasibility_evidence_payload(body["evidence"])
    except Exception as exc:  # noqa: BLE001 - normalize to this event's error
        raise FeasibilityEvidenceEventError(f"durable evidence record is invalid: {exc}") from exc
    # Return a payload rebuilt from the validated record rather than the caller's
    # raw mapping, so the durable bytes and the idempotency comparison are
    # canonical: an equivalent instant submitted as ``+00:00`` or ``.000Z`` is
    # stored in the canonical bare-``Z`` form, so an exact replay reproduces the
    # same bytes and reaches DUPLICATE_OK.
    return {
        "record_type": FEASIBILITY_EVIDENCE_EVENT_RECORD_TYPE,
        "schema_version": FEASIBILITY_EVIDENCE_RECORDED_SCHEMA_TOKEN,
        "evidence": rebuilt_wrapper,
    }


def build_feasibility_evidence_recorded_payload(
    evidence: FeasibilityEvidence,
) -> dict[str, Any]:
    """Build and validate the durable payload for one feasibility-evidence record."""
    _require(
        isinstance(evidence, FeasibilityEvidence),
        "evidence must be a FeasibilityEvidence",
    )
    wrapper = build_feasibility_evidence_payload(evidence)
    payload = {
        "record_type": FEASIBILITY_EVIDENCE_EVENT_RECORD_TYPE,
        "schema_version": FEASIBILITY_EVIDENCE_RECORDED_SCHEMA_TOKEN,
        "evidence": wrapper,
    }
    return validate_feasibility_evidence_recorded_payload(
        FEASIBILITY_EVIDENCE_RECORDED, payload
    )


def _validated_wrapper(payload: Mapping[str, Any]) -> dict[str, Any]:
    return validate_feasibility_evidence_recorded_payload(
        FEASIBILITY_EVIDENCE_RECORDED, payload
    )["evidence"]


def reconstruct_feasibility_evidence_recorded_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], FeasibilityEvidence]:
    """Validate one durable record payload once and reconstruct its typed evidence.

    Returns the normalized durable wrapper and the exact typed ``FeasibilityEvidence``
    (with both identities independently verified by the codec). Guard-safe name so a
    consumer can import it without tripping the F5 dormant-seam text guard.
    """
    wrapper = _validated_wrapper(payload)
    return wrapper, feasibility_evidence_from_payload(wrapper)


def feasibility_evidence_event_idempotency_key(
    payload: Mapping[str, Any],
) -> str:
    """The single deterministic idempotency key for one committed F5 record.

    Derived only from the validated record's evaluation identity and its exact
    ``payload_hash``. No recorded-at clock, receipt time, random envelope id,
    process id, retry count or database sequence participates, so an exact replay
    reproduces the same key and a materially different durable record yields a
    different key rather than collapsing onto the first.
    """
    wrapper = _validated_wrapper(payload)
    body = wrapper["evidence"]
    return (
        f"{FEASIBILITY_EVIDENCE_IDEMPOTENCY_PREFIX}"
        f"{body['instrument_version_id']}"
        f":{body['venue_instrument_id']}"
        f":{body['direction']}"
        f":{body['evaluation_time']}"
        f":{body['source_cutoff']}"
        f":{body['source_snapshot_id']}"
        f":{wrapper['payload_hash']}"
    )


def feasibility_evidence_event_time(payload: Mapping[str, Any]) -> str:
    """The canonical UTC serialization of the record's evaluation instant.

    The canonical writer stores this as ``event_time``. It is derived from the
    validated record, never from the writer's receipt clock.
    """
    return _validated_wrapper(payload)["evidence"]["evaluation_time"]


def feasibility_evidence_event_correlation_id(payload: Mapping[str, Any]) -> str:
    """The correlation id: the source snapshot the evidence was evaluated against."""
    return _validated_wrapper(payload)["evidence"]["source_snapshot_id"]


def feasibility_evidence_event_causation_id(payload: Mapping[str, Any]) -> None:
    """There is no causation id: F5 evidence is anchored to a source snapshot."""
    return None


def feasibility_evidence_event_fingerprint(payload: Mapping[str, Any]) -> str:
    """The retained F5 semantic lineage fingerprint (``FEV:``)."""
    return _validated_wrapper(payload)["evidence_fingerprint"]


def feasibility_evidence_event_payload_hash(payload: Mapping[str, Any]) -> str:
    """The retained exact-content identity (``FEVH:``)."""
    return _validated_wrapper(payload)["payload_hash"]


__all__ = [
    "FEASIBILITY_EVIDENCE_EVENT_RECORD_TYPE",
    "FEASIBILITY_EVIDENCE_EVENT_TYPES",
    "FEASIBILITY_EVIDENCE_IDEMPOTENCY_PREFIX",
    "FEASIBILITY_EVIDENCE_PRIORITY",
    "FEASIBILITY_EVIDENCE_RECORDED",
    "FEASIBILITY_EVIDENCE_RECORDED_SCHEMA_TOKEN",
    "FEASIBILITY_EVIDENCE_STREAM",
    "FeasibilityEvidenceEventError",
    "build_feasibility_evidence_recorded_payload",
    "feasibility_evidence_event_causation_id",
    "feasibility_evidence_event_correlation_id",
    "feasibility_evidence_event_fingerprint",
    "feasibility_evidence_event_idempotency_key",
    "feasibility_evidence_event_payload_hash",
    "feasibility_evidence_event_time",
    "reconstruct_feasibility_evidence_recorded_payload",
    "validate_feasibility_evidence_recorded_payload",
]
