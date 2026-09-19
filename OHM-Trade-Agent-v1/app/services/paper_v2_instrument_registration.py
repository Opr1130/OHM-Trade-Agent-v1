"""B/C-3 narrow canonical instrument-version registration for Paper v2.

Paper v2 execution evidence is bound to a canonical execution instrument
identity: B/C-1 quote ancestry resolves the instrument version the decision
context names, and fails closed when it is not registered. This module is the
single, narrow way the Paper v2 activation path ensures that record exists.

Scope discipline - what this deliberately does NOT do:

* It does **not** go through ``FeatureBusPublisher``. That publisher is gated by
  the Feature Bus capture switch, and Feature Bus capture is an independent
  optional plane that must not become an operational dependency of Paper v2
  execution. This module reuses only ``instrument_version_intent`` - a pure
  intent builder with no mode check - and submits it directly to the canonical
  writer.
* It does **not** emit market observations, feature snapshots, checkpoints,
  coverage gaps or restarts, and it never changes a Feature Bus mode.
* It does **not** create a second instrument-version store, registry or
  persistence format. The canonical writer remains the only durable authority,
  and the payload/identity are exactly what Feature Bus registration produces.

It fails closed: registration is only accepted when a canonical ACK proves the
record is durably committed and supplies its canonical position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.features.publisher import instrument_version_intent

#: Canonical ACK statuses that prove a record is durably committed. Anything
#: else - REJECTED, RETRYABLE, SPOOLED, DISABLED - is not proof.
COMMITTED_ACK_STATUSES = frozenset({"OK", "DUPLICATE_OK"})


class InstrumentRegistrationError(RuntimeError):
    """The canonical instrument version could not be proven, so Paper v2 must not continue."""


class WriterSubmitClient(Protocol):
    """The only writer capability this module needs."""

    def submit(self, intent: Any) -> Any: ...


@dataclass(frozen=True)
class RegisteredInstrument:
    """Proof that one canonical instrument version is durably committed."""

    instrument_version_id: str
    idempotency_key: str
    event_id: str
    history_epoch: int
    local_sequence: int
    status: str

    @property
    def watermark(self) -> ConsumedInputWatermark:
        """Canonical position of the registration, usable as consumed-input evidence."""
        return ConsumedInputWatermark(
            history_epoch=self.history_epoch,
            local_sequence=self.local_sequence,
        )


def ensure_instrument_version_registered(
    version: InstrumentVersion,
    *,
    client: WriterSubmitClient,
) -> RegisteredInstrument:
    """Ensure the canonical instrument version exists, and prove it.

    Reuses the existing canonical instrument-version intent builder, so the
    payload, identity and reference fingerprint match exactly what Feature Bus
    registration produces - there is no second instrument-version format or
    store. Submitting an already-registered version resolves through the writer's
    idempotency path and returns ``DUPLICATE_OK``, so repeated activation cycles
    are safe without any dedupe state of our own.

    No Feature Bus mode is consulted or changed, and no other Feature Bus event
    type is emitted.
    """
    if not isinstance(version, InstrumentVersion):
        raise ValueError("version must be a canonical InstrumentVersion")

    intent = instrument_version_intent(version)
    ack = client.submit(intent)
    return _require_committed_registration(
        ack,
        instrument_version_id=version.instrument_version_id,
        idempotency_key=intent.idempotency_key,
    )


def _require_committed_registration(
    ack: Any,
    *,
    instrument_version_id: str,
    idempotency_key: str,
) -> RegisteredInstrument:
    """Convert a writer ACK into proof, or fail closed.

    A missing ACK, a non-committed status, a missing event id or a missing
    canonical sequence means persistence is unproven, so the Paper v2 path must
    not continue on the assumption that the instrument exists.
    """
    what = f"instrument version {instrument_version_id}"
    if ack is None:
        raise InstrumentRegistrationError(
            f"{what} was not acknowledged by the canonical writer"
        )
    status = str(getattr(ack, "status", "") or "")
    if status not in COMMITTED_ACK_STATUSES:
        error_code = getattr(ack, "error_code", None)
        raise InstrumentRegistrationError(
            f"{what} was not proven committed (status={status or 'UNKNOWN'}"
            f"{', error=' + str(error_code) if error_code else ''})"
        )
    event_id = getattr(ack, "event_id", None)
    history_epoch = getattr(ack, "history_epoch", None)
    local_sequence = getattr(ack, "local_sequence", None)
    if not event_id or history_epoch is None or local_sequence is None:
        raise InstrumentRegistrationError(
            f"{what} acknowledgement is missing canonical identity or sequence"
        )
    return RegisteredInstrument(
        instrument_version_id=instrument_version_id,
        idempotency_key=idempotency_key,
        event_id=str(event_id),
        history_epoch=int(history_epoch),
        local_sequence=int(local_sequence),
        status=status,
    )


__all__ = [
    "COMMITTED_ACK_STATUSES",
    "InstrumentRegistrationError",
    "RegisteredInstrument",
    "WriterSubmitClient",
    "ensure_instrument_version_registered",
]
