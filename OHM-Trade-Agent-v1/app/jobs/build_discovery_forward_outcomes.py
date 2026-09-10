"""Mature Discovery V2-01 forward outcomes on the learning worker.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Reads persisted Broad Search screening rows and full-market observations,
writes joinable outcome/attribution records, and never touches ranking,
alerts, or trading authority. Failures are bounded and must not abort the
broader opportunity-intelligence cycle.

Incomplete labels stay retry-eligible. Later revisions are append-only.
Consumers resolve the latest revision per observation_id.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.opip.discovery.constants import DISCOVERY_BOUNDED_MAX_ROWS
from app.opip.discovery.maturation import mature_discovery_outcomes_bounded


DEFAULT_DATA_ROOT = Path("/app/data")
DEFAULT_SCREENING = Path("/app/data/opip/qualification/screening_evaluations.jsonl")
DEFAULT_OBSERVATIONS = Path("/app/data/full_market_observations.jsonl")
BOUNDED_MAX_ROWS = DISCOVERY_BOUNDED_MAX_ROWS

_LEDGER_FAIL_CLOSED = (
    "DISCOVERY_SCREENING_LEDGER_TRUNCATED",
    "DISCOVERY_SCREENING_LEDGER_DIVERGED",
    "DISCOVERY_SCREENING_CHECKPOINT_SHORT_READ",
)


def build_discovery_outcomes_bounded(
    *,
    screening_path: Path = DEFAULT_SCREENING,
    observation_path: Path = DEFAULT_OBSERVATIONS,
    output_dir: Path | None = None,
    max_rows: int = BOUNDED_MAX_ROWS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Label a bounded due queue. Ledger truncation fails closed, not silent."""
    root = output_dir or Path("/app/data/opip/discovery")
    try:
        return mature_discovery_outcomes_bounded(
            screening_path=screening_path,
            observation_path=observation_path,
            output_dir=root,
            max_rows=max_rows,
            now=now,
        )
    except RuntimeError as exc:
        message = str(exc)
        if any(token in message for token in _LEDGER_FAIL_CLOSED):
            return {
                "measurement_only": True,
                "trade_authority_changed": False,
                "evaluated": 0,
                "written_outcomes": 0,
                "error": message,
            }
        raise
