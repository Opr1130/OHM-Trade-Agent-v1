"""Immutable three-level identity records for captured evidence.

Raw venue identifiers are retained verbatim.  Ambiguous Kraken instrument
classes fail closed and never become ordinary spot assets merely because a
suffix can be stripped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable, Mapping

from app.opip.identity.contract import (
    IDENTITY_REFERENCE_DATA_VERSION,
    IdentityProvenance,
    IdentityResolutionStatus,
    InstrumentClass,
)


AssetCanonicalizer = Callable[[str | None], str]
PairSplitter = Callable[[str | None], tuple[str, str] | None]

_KNOWN_QUOTE_CURRENCIES: tuple[str, ...] = (
    "USDT",
    "USDC",
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "JPY",
    "CHF",
    "BTC",
    "ETH",
)

_SPECIAL_SUFFIXES: tuple[tuple[str, InstrumentClass], ...] = (
    (".B", InstrumentClass.BONDING),
    (".S", InstrumentClass.STAKED),
    (".D", InstrumentClass.DERIVATIVE),
    ("2L", InstrumentClass.LEVERAGED_TOKEN),
    ("2S", InstrumentClass.LEVERAGED_TOKEN),
    ("3L", InstrumentClass.LEVERAGED_TOKEN),
    ("3S", InstrumentClass.LEVERAGED_TOKEN),
    ("5L", InstrumentClass.LEVERAGED_TOKEN),
    ("5S", InstrumentClass.LEVERAGED_TOKEN),
)


def _normalized(value: object) -> str:
    return re.sub(r"[\s/\-_]", "", str(value or "").upper())


def _utc(value: datetime | None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise ValueError("resolved_at_utc must be timezone-aware")
    return resolved.astimezone(timezone.utc)


def _instrument_class(base: str) -> InstrumentClass | None:
    value = str(base or "").strip().upper()
    if value == "ETH2":
        return InstrumentClass.STAKED
    for suffix, classification in _SPECIAL_SUFFIXES:
        if value.endswith(suffix):
            return classification
    return None


def _local_pair_parts(value: str) -> tuple[str, str] | None:
    for quote in _KNOWN_QUOTE_CURRENCIES:
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)], quote
    return None


@dataclass(frozen=True)
class ResolvedInstrumentIdentity:
    """What O'Pip knew about one instrument when evidence was captured."""

    canonical_asset_id: str | None
    venue_instrument_symbol: str | None
    quote_currency: str | None
    raw_identifier: str
    instrument_class: InstrumentClass
    resolution_status: IdentityResolutionStatus
    resolution_provenance: IdentityProvenance
    reference_data_version: str
    resolved_at_utc: datetime

    def __post_init__(self) -> None:
        if self.resolved_at_utc.tzinfo is None or self.resolved_at_utc.utcoffset() is None:
            raise ValueError("resolved_at_utc must be timezone-aware")
        unresolved = self.resolution_status in {
            IdentityResolutionStatus.CLASSIFIED_UNRESOLVED,
            IdentityResolutionStatus.UNKNOWN,
        }
        if unresolved and self.canonical_asset_id is not None:
            raise ValueError("unresolved evidence cannot contain a canonical asset")
        if not unresolved and not str(self.canonical_asset_id or "").strip():
            raise ValueError("resolved evidence requires a canonical asset")

    @property
    def venue_instrument_id(self) -> str:
        return str(self.venue_instrument_symbol or _normalized(self.raw_identifier))

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_asset_id": self.canonical_asset_id,
            "venue_instrument_symbol": self.venue_instrument_symbol,
            "quote_currency": self.quote_currency,
            "raw_identifier": self.raw_identifier,
            "instrument_class": self.instrument_class.value,
            "resolution_status": self.resolution_status.value,
            "resolution_provenance": self.resolution_provenance.value,
            "reference_data_version": self.reference_data_version,
            "resolved_at_utc": self.resolved_at_utc.astimezone(timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResolvedInstrumentIdentity":
        parsed_at = datetime.fromisoformat(str(payload["resolved_at_utc"]))
        return cls(
            canonical_asset_id=(
                str(payload["canonical_asset_id"])
                if payload.get("canonical_asset_id") is not None
                else None
            ),
            venue_instrument_symbol=(
                str(payload["venue_instrument_symbol"])
                if payload.get("venue_instrument_symbol") is not None
                else None
            ),
            quote_currency=(
                str(payload["quote_currency"])
                if payload.get("quote_currency") is not None
                else None
            ),
            raw_identifier=str(payload.get("raw_identifier") or ""),
            instrument_class=InstrumentClass(payload["instrument_class"]),
            resolution_status=IdentityResolutionStatus(payload["resolution_status"]),
            resolution_provenance=IdentityProvenance(payload["resolution_provenance"]),
            reference_data_version=str(payload["reference_data_version"]),
            resolved_at_utc=_utc(parsed_at),
        )


def _unresolved(
    *,
    raw_identifier: str,
    venue_instrument_symbol: str | None,
    quote_currency: str | None,
    instrument_class: InstrumentClass,
    resolved_at_utc: datetime | None,
) -> ResolvedInstrumentIdentity:
    return ResolvedInstrumentIdentity(
        canonical_asset_id=None,
        venue_instrument_symbol=venue_instrument_symbol,
        quote_currency=quote_currency,
        raw_identifier=raw_identifier,
        instrument_class=instrument_class,
        resolution_status=IdentityResolutionStatus.CLASSIFIED_UNRESOLVED,
        resolution_provenance=IdentityProvenance.STATIC_REFERENCE,
        reference_data_version=IDENTITY_REFERENCE_DATA_VERSION,
        resolved_at_utc=_utc(resolved_at_utc),
    )


def resolve_asset_identity(
    raw_identifier: str,
    *,
    canonicalize_asset: AssetCanonicalizer,
    resolved_at_utc: datetime | None = None,
) -> ResolvedInstrumentIdentity:
    raw = str(raw_identifier)
    value = _normalized(raw)
    classification = _instrument_class(value)
    if classification is not None:
        return _unresolved(
            raw_identifier=raw,
            venue_instrument_symbol=value or None,
            quote_currency=None,
            instrument_class=classification,
            resolved_at_utc=resolved_at_utc,
        )
    canonical = str(canonicalize_asset(value) or "").strip().upper()
    if not value or not canonical:
        return ResolvedInstrumentIdentity(
            canonical_asset_id=None,
            venue_instrument_symbol=value or None,
            quote_currency=None,
            raw_identifier=raw,
            instrument_class=InstrumentClass.UNKNOWN,
            resolution_status=IdentityResolutionStatus.UNKNOWN,
            resolution_provenance=IdentityProvenance.RAW_ONLY,
            reference_data_version=IDENTITY_REFERENCE_DATA_VERSION,
            resolved_at_utc=_utc(resolved_at_utc),
        )
    status = (
        IdentityResolutionStatus.EXACT
        if canonical == value
        else IdentityResolutionStatus.REFERENCE_ALIAS
    )
    return ResolvedInstrumentIdentity(
        canonical_asset_id=canonical,
        venue_instrument_symbol=canonical,
        quote_currency=None,
        raw_identifier=raw,
        instrument_class=InstrumentClass.SPOT,
        resolution_status=status,
        resolution_provenance=IdentityProvenance.STATIC_REFERENCE,
        reference_data_version=IDENTITY_REFERENCE_DATA_VERSION,
        resolved_at_utc=_utc(resolved_at_utc),
    )


def resolve_venue_instrument_identity(
    raw_identifier: str,
    *,
    canonicalize_asset: AssetCanonicalizer,
    split_canonical_pair: PairSplitter,
    resolved_at_utc: datetime | None = None,
) -> ResolvedInstrumentIdentity:
    raw = str(raw_identifier)
    value = _normalized(raw)

    local_parts = _local_pair_parts(value)
    if local_parts is not None:
        local_base, local_quote = local_parts
        classification = _instrument_class(local_base)
        if classification is not None:
            return _unresolved(
                raw_identifier=raw,
                venue_instrument_symbol=value,
                quote_currency=local_quote,
                instrument_class=classification,
                resolved_at_utc=resolved_at_utc,
            )

    split = split_canonical_pair(raw)
    if split is None:
        return ResolvedInstrumentIdentity(
            canonical_asset_id=None,
            venue_instrument_symbol=value or None,
            quote_currency=(local_parts[1] if local_parts else None),
            raw_identifier=raw,
            instrument_class=InstrumentClass.UNKNOWN,
            resolution_status=IdentityResolutionStatus.UNKNOWN,
            resolution_provenance=IdentityProvenance.RAW_ONLY,
            reference_data_version=IDENTITY_REFERENCE_DATA_VERSION,
            resolved_at_utc=_utc(resolved_at_utc),
        )

    canonical_base = str(split[0] or "").strip().upper()
    canonical_quote = str(split[1] or "").strip().upper()
    classification = _instrument_class(canonical_base)
    if classification is not None:
        return _unresolved(
            raw_identifier=raw,
            venue_instrument_symbol=value or None,
            quote_currency=canonical_quote or None,
            instrument_class=classification,
            resolved_at_utc=resolved_at_utc,
        )
    canonical_base = str(canonicalize_asset(canonical_base) or "").strip().upper()
    if not canonical_base or not canonical_quote:
        return ResolvedInstrumentIdentity(
            canonical_asset_id=None,
            venue_instrument_symbol=value or None,
            quote_currency=canonical_quote or None,
            raw_identifier=raw,
            instrument_class=InstrumentClass.UNKNOWN,
            resolution_status=IdentityResolutionStatus.UNKNOWN,
            resolution_provenance=IdentityProvenance.RAW_ONLY,
            reference_data_version=IDENTITY_REFERENCE_DATA_VERSION,
            resolved_at_utc=_utc(resolved_at_utc),
        )

    canonical_symbol = f"{canonical_base}{canonical_quote}"
    status = (
        IdentityResolutionStatus.EXACT
        if value == canonical_symbol
        else IdentityResolutionStatus.REFERENCE_ALIAS
    )
    return ResolvedInstrumentIdentity(
        canonical_asset_id=canonical_base,
        venue_instrument_symbol=canonical_symbol,
        quote_currency=canonical_quote,
        raw_identifier=raw,
        instrument_class=InstrumentClass.SPOT,
        resolution_status=status,
        resolution_provenance=IdentityProvenance.STATIC_REFERENCE,
        reference_data_version=IDENTITY_REFERENCE_DATA_VERSION,
        resolved_at_utc=_utc(resolved_at_utc),
    )
