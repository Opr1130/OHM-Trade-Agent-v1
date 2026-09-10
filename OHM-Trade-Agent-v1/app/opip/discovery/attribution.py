"""Exclusive Stage-0 discovery attribution.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Each observation receives exactly one category. Rank-cap and threshold
rejection cannot overlap. A later risk/execution rejection is not a
discovery miss: an admitted instrument stays ADMITTED.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Mapping

from app.opip.discovery.constants import (
    ATTRIBUTION_ADMITTED,
    ATTRIBUTION_BELOW_THRESHOLD,
    ATTRIBUTION_DATA_UNAVAILABLE,
    ATTRIBUTION_EXCLUDED_MARKET,
    ATTRIBUTION_NOT_OBSERVED,
    ATTRIBUTION_RANKED_OUTSIDE_BUDGET,
    DISCOVERY_ATTRIBUTION_SCHEMA_VERSION,
    DISCOVERY_ATTRIBUTION_TAXONOMY_VERSION,
    PENDING_FINALIZATION,
    SCREENING_TO_ATTRIBUTION,
    STAGE0_ATTRIBUTION_CATEGORIES,
)


_OBSERVED_RESULTS = {
    ATTRIBUTION_ADMITTED,
    ATTRIBUTION_BELOW_THRESHOLD,
    ATTRIBUTION_DATA_UNAVAILABLE,
    ATTRIBUTION_EXCLUDED_MARKET,
    ATTRIBUTION_RANKED_OUTSIDE_BUDGET,
}


def attribute_stage0_observation(
    screening_row: Mapping[str, Any] | None,
    *,
    downstream_rejection: str | None = None,
) -> str:
    """Return the exclusive Stage-0 attribution for one instrument.

    ``downstream_rejection`` is accepted so callers can prove it is ignored.
    """
    del downstream_rejection  # never a discovery miss
    if screening_row is None:
        return ATTRIBUTION_NOT_OBSERVED
    metadata = screening_row.get("metadata")
    outcome = str(screening_row.get("outcome") or "").strip()
    recorded = ""
    if isinstance(metadata, Mapping):
        recorded = str(metadata.get("production_admission_result") or "").strip()
        if str(metadata.get("finalization_status") or "") == PENDING_FINALIZATION:
            return PENDING_FINALIZATION
        if recorded == PENDING_FINALIZATION:
            return PENDING_FINALIZATION
    if outcome == PENDING_FINALIZATION:
        return PENDING_FINALIZATION
    if recorded in _OBSERVED_RESULTS:
        return recorded
    mapped = SCREENING_TO_ATTRIBUTION.get(outcome)
    if mapped:
        return mapped
    return ATTRIBUTION_DATA_UNAVAILABLE


def _optional_text(row: Mapping[str, Any] | None, key: str) -> str | None:
    if row is None:
        return None
    value = row.get(key)
    return str(value) if value is not None else None


def attribution_record_id(
    *,
    observation_id: str | None,
    scan_id: str | None,
    venue_instrument_id: str | None,
    category: str,
) -> str:
    """Stable idempotency key for one terminal Stage-0 attribution."""
    identity = str(observation_id or "").strip()
    if not identity:
        identity = "|".join(
            (
                str(scan_id or "").strip(),
                str(venue_instrument_id or "").strip(),
            )
        )
    raw = "|".join(
        (
            DISCOVERY_ATTRIBUTION_TAXONOMY_VERSION,
            identity,
            str(category or "").strip(),
        )
    )
    return "DATTR:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def attribution_record(
    screening_row: Mapping[str, Any] | None,
    *,
    observation_id: str | None = None,
    scan_id: str | None = None,
    venue_instrument_id: str | None = None,
    downstream_rejection: str | None = None,
    labeled_at: datetime | None = None,
) -> dict[str, Any]:
    category = attribute_stage0_observation(
        screening_row,
        downstream_rejection=downstream_rejection,
    )
    metadata = (
        screening_row.get("metadata")
        if isinstance(screening_row, Mapping)
        and isinstance(screening_row.get("metadata"), Mapping)
        else {}
    )
    terminal = category in STAGE0_ATTRIBUTION_CATEGORIES
    resolved_observation_id = observation_id or (
        metadata.get("observation_id") if isinstance(metadata, Mapping) else None
    )
    resolved_scan_id = scan_id or _optional_text(screening_row, "scan_id")
    resolved_venue_id = venue_instrument_id or _optional_text(
        screening_row, "venue_instrument_id"
    )
    record_id = attribution_record_id(
        observation_id=(
            str(resolved_observation_id)
            if resolved_observation_id is not None
            else None
        ),
        scan_id=resolved_scan_id,
        venue_instrument_id=resolved_venue_id,
        category=category,
    )
    return {
        "schema_version": DISCOVERY_ATTRIBUTION_SCHEMA_VERSION,
        "taxonomy_version": DISCOVERY_ATTRIBUTION_TAXONOMY_VERSION,
        "measurement_only": True,
        "trade_authority_changed": False,
        "attribution_record_id": record_id,
        "observation_id": resolved_observation_id,
        "scan_id": resolved_scan_id,
        "venue_instrument_id": resolved_venue_id,
        "stage0_attribution": category,
        "exclusive": terminal,
        "canonical_terminal": terminal,
        "downstream_rejection_ignored": downstream_rejection,
        "labeled_at": (labeled_at or datetime.now(timezone.utc)).isoformat(),
        "valid_categories": list(STAGE0_ATTRIBUTION_CATEGORIES),
    }


def attributions_are_exclusive(categories: list[str]) -> bool:
    return len(categories) == 1 and categories[0] in STAGE0_ATTRIBUTION_CATEGORIES
