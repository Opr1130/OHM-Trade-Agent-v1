"""Explicit relevance model for the O'Pip Event Risk Shield.

Asset relevance is never guessed from raw ticker text. Only canonical
Sequence 2 identity with MappingStatus.UNIQUE may attach an event to a
specific exposure. AMBIGUOUS and UNKNOWN evidence is retained for audit and
reported as UNRELATED for protection purposes.

Market-wide and macro scope is honoured only when an adapter has explicitly
marked it in sanitized source metadata. An event with no resolvable identity
is not silently promoted to "affects everything".
"""

from __future__ import annotations

from app.opip.events.contract import MappingStatus, OPipEvent
from app.opip.events.identity import normalize_identity_text, normalize_symbol
from app.opip.risk.contract import ExposureView, Relevance


# Adapters may set this sanitized key when a provider genuinely publishes a
# market-wide or macro item. Absence means "no declared scope", never
# "applies to every exposure".
MARKET_SCOPE_KEY = "opip_market_scope"
MARKET_WIDE_SCOPE = "MARKET_WIDE"
MACRO_SCOPE = "MACRO"
ECOSYSTEM_SCOPE_KEY = "opip_ecosystem"


def _declared_scope(event: OPipEvent) -> str:
    metadata = event.source_metadata if isinstance(event.source_metadata, dict) else {}
    return str(metadata.get(MARKET_SCOPE_KEY) or "").strip().upper()


def _declared_ecosystem(event: OPipEvent) -> str:
    metadata = event.source_metadata if isinstance(event.source_metadata, dict) else {}
    return normalize_identity_text(str(metadata.get(ECOSYSTEM_SCOPE_KEY) or ""))


def has_unique_identity(event: OPipEvent) -> bool:
    return event.identity.mapping_status == MappingStatus.UNIQUE


def matches_exposure_asset(event: OPipEvent, exposure: ExposureView) -> bool:
    """Canonical-identity match only.

    A symbol string is accepted only as corroboration of an already-UNIQUE
    canonical mapping, never as the mapping itself.
    """
    if not has_unique_identity(event):
        return False

    exposure_canonical = normalize_identity_text(exposure.canonical_asset_id or "")
    event_canonical = normalize_identity_text(event.identity.canonical_asset_id or "")
    if exposure_canonical and event_canonical:
        return exposure_canonical == event_canonical

    # The exposure could not be resolved to a canonical asset at decision
    # time. Refuse to attach rather than falling back to ticker text.
    return False


def _venue_matches(event: OPipEvent, exposure: ExposureView) -> bool:
    event_venue = normalize_identity_text(event.identity.venue or "")
    exposure_venue = normalize_identity_text(exposure.venue or "")
    return bool(event_venue) and event_venue == exposure_venue


def _ecosystem_matches(event: OPipEvent, exposure: ExposureView) -> bool:
    declared = _declared_ecosystem(event)
    if not declared:
        return False
    return declared in {
        normalize_identity_text(exposure.base_asset),
        normalize_identity_text(exposure.canonical_asset_id or ""),
    }


def classify(event: OPipEvent, exposure: ExposureView) -> Relevance:
    """Classify one event against one exposure.

    Ordering is deliberate: a direct canonical asset match outranks a declared
    market-wide scope, so a token-specific critical event is never diluted
    into generic market context.
    """
    if matches_exposure_asset(event, exposure):
        return Relevance.DIRECT_ASSET

    scope = _declared_scope(event)
    if scope == MARKET_WIDE_SCOPE:
        return Relevance.MARKET_WIDE
    if scope == MACRO_SCOPE:
        return Relevance.MACRO

    if _ecosystem_matches(event, exposure):
        return Relevance.ECOSYSTEM

    if _venue_matches(event, exposure):
        return Relevance.VENUE

    return Relevance.UNRELATED


def identity_warning(event: OPipEvent) -> str | None:
    """Audit warning when asset-specific evidence could not be attached."""
    status = event.identity.mapping_status
    if status == MappingStatus.AMBIGUOUS:
        return "IDENTITY_AMBIGUOUS_NOT_ATTACHED"
    if status == MappingStatus.UNKNOWN:
        return "IDENTITY_UNKNOWN_NOT_ATTACHED"
    return None


def symbol_hint(event: OPipEvent) -> str:
    """Non-authoritative display hint for audit records only."""
    return normalize_symbol(event.identity.source_symbol)
