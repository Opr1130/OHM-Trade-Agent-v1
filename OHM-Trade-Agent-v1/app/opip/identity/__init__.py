"""Evidence-only instrument identity for O'Pip.

This package deliberately has no exchange-client imports and no trading
authority.  Callers inject the repository's existing canonicalizers so the
captured record describes what was known at observation time without creating
another execution identity subsystem.
"""

from app.opip.identity.contract import (
    IDENTITY_REFERENCE_DATA_VERSION,
    IdentityProvenance,
    IdentityResolutionStatus,
    InstrumentClass,
)
from app.opip.identity.instrument_identity import (
    ResolvedInstrumentIdentity,
    resolve_asset_identity,
    resolve_venue_instrument_identity,
)

__all__ = [
    "IDENTITY_REFERENCE_DATA_VERSION",
    "IdentityProvenance",
    "IdentityResolutionStatus",
    "InstrumentClass",
    "ResolvedInstrumentIdentity",
    "resolve_asset_identity",
    "resolve_venue_instrument_identity",
]
