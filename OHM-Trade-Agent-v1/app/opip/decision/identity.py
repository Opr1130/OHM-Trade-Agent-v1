"""Direction-safe identity for O'Pip qualification evidence.

``BTCUSD LONG`` and ``BTCUSD SHORT`` are different opportunities with
different economics and different execution venues. Any identity that omits
direction silently merges them, which corrupts every downstream join
(episode -> candidate -> decision -> paper position -> outcome).

Identity here is built from stable attributes only: the canonical episode, the
pair, the market type, and the direction. It deliberately does not depend on a
microsecond timestamp - the episode already encodes the decision boundary, so
recomputing the same candidate's id later in the same scan yields the same
value.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone


#: Spot is the only venue O'Pip goes long on; shorts are Kraken US retail
#: margin pairs, validated upstream by the margin eligibility gate.
SPOT = "SPOT"
MARGIN = "MARGIN"


def normalize_direction(value: object) -> str:
    """Return an upper-case direction, defaulting to LONG like the scanner."""
    direction = str(value or "LONG").strip().upper()
    return direction or "LONG"


def market_type_for(direction: object) -> str:
    """Return the venue class implied by a direction.

    O'Pip v1 has exactly one short venue (Kraken US retail margin) and one long
    venue (spot), so the mapping is total.
    """
    return MARGIN if normalize_direction(direction) == "SHORT" else SPOT


def decision_boundary(decision_at: datetime) -> str:
    """Return the whole-second UTC boundary that identifies a decision.

    Truncating to the second keeps identity reproducible across recomputation
    inside one scan without making it depend on arbitrary microseconds.
    """
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    return (
        decision_at.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _digest(prefix: str, basis: str, *, length: int) -> str:
    return f"{prefix}:{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:length]}"


def opip_candidate_id(
    *,
    episode_id: str,
    pair: str,
    direction: object,
    market_type: str | None = None,
) -> str:
    """Return the direction-scoped candidate identity for one episode.

    Deterministic and collision-safe across directions: the same episode and
    pair with a different direction produces a different id.
    """
    normalized_direction = normalize_direction(direction)
    venue = str(market_type or market_type_for(normalized_direction)).upper()
    basis = "|".join(
        (
            str(episode_id or ""),
            str(pair or "").upper(),
            venue,
            normalized_direction,
        )
    )
    return _digest("OPIPC", basis, length=20)


def opip_scan_id(*, cohort_id: str, decision_at: datetime) -> str:
    """Return the identity of one instrumented scan.

    Derived from the canonical cohort so an O'Pip funnel row joins directly to
    the canonical episode evidence written by the same scan.
    """
    basis = f"{cohort_id}|{decision_boundary(decision_at)}"
    return _digest("OPIPS", basis, length=20)


def candidate_key(symbol: object, direction: object) -> tuple[str, str]:
    """Return the in-scan lookup key for a candidate.

    Mirrors the production path, which already keys snapshots by
    ``(symbol, trade_direction)``.
    """
    return (str(symbol or "").upper(), normalize_direction(direction))
