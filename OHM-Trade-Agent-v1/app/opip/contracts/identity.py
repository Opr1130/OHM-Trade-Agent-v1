"""Instrument version identity and canonical commit-order watermarks.

One fact, one authoritative identity. An ``InstrumentVersion`` is what O'Pip
believed about a tradeable instrument at the time evidence was captured; when
the venue's reference data materially changes, a new version is minted rather
than mutating history in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.identity.contract import (
    IdentityProvenance,
    IdentityResolutionStatus,
    InstrumentClass,
)
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc


INSTRUMENT_VERSION_PREFIX = "INSTR"


@dataclass(frozen=True, order=True)
class ConsumedInputWatermark:
    """Canonical commit order: ``(history_epoch, local_sequence)``.

    Ordering is lexicographic on the pair, so a history epoch bump always
    dominates. This is commit order, never source event order.
    """

    history_epoch: int
    local_sequence: int

    def __post_init__(self) -> None:
        if int(self.history_epoch) < 0:
            raise ValueError("history_epoch must be non-negative")
        if int(self.local_sequence) < 0:
            raise ValueError("local_sequence must be non-negative")
        object.__setattr__(self, "history_epoch", int(self.history_epoch))
        object.__setattr__(self, "local_sequence", int(self.local_sequence))

    @classmethod
    def zero(cls) -> "ConsumedInputWatermark":
        return cls(history_epoch=0, local_sequence=0)

    def advanced_to(
        self, *, history_epoch: int, local_sequence: int
    ) -> "ConsumedInputWatermark":
        """Return the later of self and the supplied position.

        Never regresses. A stale ack cannot rewind a consumed watermark.
        """
        candidate = ConsumedInputWatermark(
            history_epoch=history_epoch, local_sequence=local_sequence
        )
        return candidate if candidate > self else self

    def to_dict(self) -> dict[str, int]:
        return {
            "history_epoch": self.history_epoch,
            "local_sequence": self.local_sequence,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ConsumedInputWatermark":
        return cls(
            history_epoch=int(payload["history_epoch"]),
            local_sequence=int(payload["local_sequence"]),
        )


@dataclass(frozen=True)
class InstrumentVersion:
    """Reference-data identity for one venue instrument at one point in time.

    Unknown venue metadata stays ``None``. It is never back-filled from a
    default, because a fabricated tick size silently changes what a feature
    means.
    """

    venue: str
    base_asset: str
    quote_currency: str
    venue_instrument_id: str
    version: int
    reference_data_version: str
    observed_at_utc: datetime
    instrument_class: InstrumentClass = InstrumentClass.SPOT
    resolution_status: IdentityResolutionStatus = IdentityResolutionStatus.EXACT
    provenance: IdentityProvenance = IdentityProvenance.KRAKEN_METADATA
    price_decimals: int | None = None
    tick_size: float | None = None
    min_order_size: float | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("venue", "base_asset", "quote_currency", "venue_instrument_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if int(self.version) < 1:
            raise ValueError("version must be >= 1")
        if not str(self.reference_data_version or "").strip():
            raise ValueError("reference_data_version is required")
        attributes = {
            str(key): str(value) for key, value in dict(self.attributes).items()
        }
        object.__setattr__(self, "venue", str(self.venue).strip().lower())
        object.__setattr__(self, "base_asset", str(self.base_asset).strip().upper())
        object.__setattr__(
            self, "quote_currency", str(self.quote_currency).strip().upper()
        )
        object.__setattr__(
            self, "venue_instrument_id", str(self.venue_instrument_id).strip()
        )
        object.__setattr__(self, "version", int(self.version))
        object.__setattr__(
            self,
            "observed_at_utc",
            require_utc(self.observed_at_utc, field_name="observed_at_utc"),
        )
        object.__setattr__(self, "attributes", MappingProxyType(attributes))

    @property
    def instrument_version_id(self) -> str:
        return ":".join(
            (
                INSTRUMENT_VERSION_PREFIX,
                self.venue,
                self.base_asset,
                self.quote_currency,
                str(self.version),
            )
        )

    @property
    def instrument_key(self) -> str:
        """Version-independent key for the same underlying venue instrument."""
        return ":".join((self.venue, self.base_asset, self.quote_currency))

    def reference_fingerprint(self) -> str:
        """Hash of the reference data that defines this version.

        ``observed_at_utc`` and ``version`` are excluded so that re-observing
        unchanged metadata does not mint a new version.
        """
        return stable_hash(
            "INSTRFP",
            {
                "venue": self.venue,
                "base_asset": self.base_asset,
                "quote_currency": self.quote_currency,
                "venue_instrument_id": self.venue_instrument_id,
                "instrument_class": self.instrument_class.value,
                "resolution_status": self.resolution_status.value,
                "reference_data_version": self.reference_data_version,
                "price_decimals": self.price_decimals,
                "tick_size": self.tick_size,
                "min_order_size": self.min_order_size,
                "attributes": dict(sorted(self.attributes.items())),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_version_id": self.instrument_version_id,
            "instrument_key": self.instrument_key,
            "venue": self.venue,
            "base_asset": self.base_asset,
            "quote_currency": self.quote_currency,
            "venue_instrument_id": self.venue_instrument_id,
            "version": self.version,
            "instrument_class": self.instrument_class.value,
            "resolution_status": self.resolution_status.value,
            "provenance": self.provenance.value,
            "reference_data_version": self.reference_data_version,
            "observed_at_utc": iso_z(
                self.observed_at_utc, field_name="observed_at_utc"
            ),
            "price_decimals": self.price_decimals,
            "tick_size": self.tick_size,
            "min_order_size": self.min_order_size,
            "reference_fingerprint": self.reference_fingerprint(),
            "attributes": dict(sorted(self.attributes.items())),
        }


__all__ = [
    "ConsumedInputWatermark",
    "INSTRUMENT_VERSION_PREFIX",
    "InstrumentVersion",
]
