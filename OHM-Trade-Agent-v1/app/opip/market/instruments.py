"""Instrument version identity for the feature bus (PR3 slice B).

Venue reference data changes over time: decimals, minimum order size, listing
class. A feature computed under old reference data does not mean the same thing
as one computed under new reference data, so a material change mints a new
``InstrumentVersion`` instead of mutating identity in place.

Eligibility constants are reused from ``app.scanner.universe`` rather than
restated, so the feature bus cannot silently drift from the production universe
definition of a tradeable USD/USDT market.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping

from app.opip.contracts.identity import InstrumentVersion
from app.opip.identity.contract import (
    IDENTITY_REFERENCE_DATA_VERSION,
    IdentityProvenance,
    IdentityResolutionStatus,
    InstrumentClass,
)
from app.scanner.universe import (
    BASE_ALIASES,
    ELIGIBLE_QUOTES,
    EXCLUDED_BASE_ASSETS,
    EXCLUDED_SUFFIXES,
)

KRAKEN_VENUE = "kraken"


@dataclass(frozen=True)
class VenueInstrumentDescriptor:
    """Venue reference data for one instrument, already extracted and typed.

    Unknown metadata stays ``None``. Nothing here is defaulted from a guess.
    """

    venue: str
    base_asset: str
    quote_currency: str
    venue_instrument_id: str
    instrument_class: InstrumentClass = InstrumentClass.SPOT
    resolution_status: IdentityResolutionStatus = IdentityResolutionStatus.EXACT
    provenance: IdentityProvenance = IdentityProvenance.KRAKEN_METADATA
    price_decimals: int | None = None
    tick_size: float | None = None
    min_order_size: float | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)

    @property
    def instrument_key(self) -> str:
        return ":".join(
            (
                str(self.venue).strip().lower(),
                str(self.base_asset).strip().upper(),
                str(self.quote_currency).strip().upper(),
            )
        )


def _normalize_base(base: str) -> str:
    upper = str(base or "").strip().upper()
    return BASE_ALIASES.get(upper, upper)


def _pair_symbols(details: Mapping[str, Any]) -> tuple[str, str] | None:
    """Prefer the venue's own ``wsname``; fall back to altname suffix parsing."""
    wsname = details.get("wsname")
    if isinstance(wsname, str) and "/" in wsname:
        base, quote = wsname.upper().split("/", maxsplit=1)
        return _normalize_base(base), quote
    altname = details.get("altname")
    if not isinstance(altname, str):
        return None
    upper = altname.upper()
    for quote in ("USDT", "USD"):
        if upper.endswith(quote) and len(upper) > len(quote):
            return _normalize_base(upper[: -len(quote)]), quote
    return None


def is_eligible_pair(details: Mapping[str, Any]) -> bool:
    """Same eligibility rule as the production universe: spot USD/USDT only."""
    symbols = _pair_symbols(details)
    altname = details.get("altname")
    if symbols is None or not isinstance(altname, str):
        return False
    base_asset, quote_currency = symbols
    if quote_currency not in ELIGIBLE_QUOTES or base_asset in EXCLUDED_BASE_ASSETS:
        return False
    upper_altname = altname.upper()
    return not any(
        upper_altname.endswith(f"{suffix}{quote_currency}")
        or upper_altname.endswith(suffix)
        for suffix in EXCLUDED_SUFFIXES
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def kraken_descriptor(
    pair_id: str, details: Mapping[str, Any]
) -> VenueInstrumentDescriptor | None:
    """Extract a descriptor from one ``AssetPairs`` entry. Pure, no network."""
    symbols = _pair_symbols(details)
    if symbols is None:
        return None
    base_asset, quote_currency = symbols
    price_decimals = _optional_int(details.get("pair_decimals"))
    tick_size = 10.0 ** (-price_decimals) if price_decimals is not None else None
    altname = str(details.get("altname") or pair_id).upper()
    attributes = {"pair_id": str(pair_id), "altname": altname}
    status = details.get("status")
    if isinstance(status, str) and status.strip():
        attributes["status"] = status.strip()
    return VenueInstrumentDescriptor(
        venue=KRAKEN_VENUE,
        base_asset=base_asset,
        quote_currency=quote_currency,
        venue_instrument_id=altname,
        price_decimals=price_decimals,
        tick_size=tick_size,
        min_order_size=_optional_float(details.get("ordermin")),
        attributes=attributes,
    )


def kraken_descriptors(
    pair_details: Mapping[str, Mapping[str, Any]],
    *,
    eligible_only: bool = True,
) -> list[VenueInstrumentDescriptor]:
    """Deterministically ordered descriptors for a Kraken AssetPairs payload."""
    descriptors: list[VenueInstrumentDescriptor] = []
    for pair_id in sorted(pair_details):
        details = pair_details[pair_id]
        if not isinstance(details, Mapping):
            continue
        if eligible_only and not is_eligible_pair(details):
            continue
        descriptor = kraken_descriptor(pair_id, details)
        if descriptor is not None:
            descriptors.append(descriptor)
    return descriptors


class InstrumentVersionRegistry:
    """Mints instrument versions and holds the current version per instrument.

    Deterministic: the same descriptor sequence always yields the same version
    numbers. Re-observing unchanged reference data is a no-op rather than a new
    version, so version numbers stay meaningful.
    """

    def __init__(
        self,
        *,
        reference_data_version: str = IDENTITY_REFERENCE_DATA_VERSION,
        known: Mapping[str, InstrumentVersion] | None = None,
    ) -> None:
        self._reference_data_version = str(reference_data_version)
        self._current: dict[str, InstrumentVersion] = dict(known or {})

    @property
    def reference_data_version(self) -> str:
        return self._reference_data_version

    def current(self, instrument_key: str) -> InstrumentVersion | None:
        return self._current.get(instrument_key)

    def snapshot(self) -> dict[str, InstrumentVersion]:
        return dict(self._current)

    def observe(
        self,
        descriptor: VenueInstrumentDescriptor,
        *,
        observed_at_utc: datetime,
    ) -> InstrumentVersion:
        candidate = InstrumentVersion(
            venue=descriptor.venue,
            base_asset=descriptor.base_asset,
            quote_currency=descriptor.quote_currency,
            venue_instrument_id=descriptor.venue_instrument_id,
            version=1,
            reference_data_version=self._reference_data_version,
            observed_at_utc=observed_at_utc,
            instrument_class=descriptor.instrument_class,
            resolution_status=descriptor.resolution_status,
            provenance=descriptor.provenance,
            price_decimals=descriptor.price_decimals,
            tick_size=descriptor.tick_size,
            min_order_size=descriptor.min_order_size,
            attributes=descriptor.attributes,
        )
        existing = self._current.get(candidate.instrument_key)
        if existing is None:
            self._current[candidate.instrument_key] = candidate
            return candidate
        if existing.reference_fingerprint() == candidate.reference_fingerprint():
            return existing
        superseding = replace(candidate, version=existing.version + 1)
        self._current[superseding.instrument_key] = superseding
        return superseding

    def observe_all(
        self,
        descriptors: list[VenueInstrumentDescriptor],
        *,
        observed_at_utc: datetime,
    ) -> list[InstrumentVersion]:
        return [
            self.observe(descriptor, observed_at_utc=observed_at_utc)
            for descriptor in descriptors
        ]


__all__ = [
    "KRAKEN_VENUE",
    "InstrumentVersionRegistry",
    "VenueInstrumentDescriptor",
    "is_eligible_pair",
    "kraken_descriptor",
    "kraken_descriptors",
]
