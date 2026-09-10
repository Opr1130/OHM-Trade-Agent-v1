"""Replay helpers for Discovery V2-01 forensic parity.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.opip.discovery.attribution import attribute_stage0_observation
from app.opip.early.replay import forensic_rows, replay_forensic
from app.scanner.directional_candidates import select_directional_candidates
from app.scanner.models import MarketSnapshot


def production_shortlist_fingerprint(snapshots: Sequence[MarketSnapshot]) -> tuple[tuple[str, str, int], ...]:
    """Stable identity of the production shortlist for a snapshot set."""
    selected = select_directional_candidates(list(snapshots))
    return tuple(
        (
            str(item.underlying_asset or item.symbol),
            str(item.trade_direction),
            int(item.technical_score),
        )
        for item in selected
    )


def forensic_admission_report(
    screening_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Explain Stage-0 admissions from persisted rows without changing them."""
    forensic = replay_forensic(screening_rows)
    rows = forensic_rows(screening_rows)
    attributions = {}
    for row in rows:
        if not row.venue_instrument_id:
            continue
        metadata = dict(row.metadata) if isinstance(row.metadata, Mapping) else {}
        observation_id = str(metadata.get("observation_id") or "").strip()
        key = observation_id or (row.scan_id, row.venue_instrument_id)
        attributions[key] = attribute_stage0_observation(
            {
                "outcome": row.outcome,
                "metadata": metadata,
                "scan_id": row.scan_id,
                "venue_instrument_id": row.venue_instrument_id,
            }
        )
    forensic["stage0_attributions"] = attributions
    forensic["measurement_only"] = True
    forensic["trade_authority_changed"] = False
    return forensic
