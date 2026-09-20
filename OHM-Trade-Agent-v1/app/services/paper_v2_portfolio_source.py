"""Mode-aware portfolio source for the scan action gate.

The action gate's existing portfolio-risk rules (duplicate symbol, position count,
same-direction concentration, gross exposure, capital allocation) are unchanged.
What changes is *where the existing positions come from*.

Under the legacy authority those positions live in the legacy active-trade
registry. Under a granted Paper-v2 authority they live in committed canonical
evidence, because a Paper-v2 fill never touches the legacy registry - and a
canonical fill that the next scan cannot see would let portfolio protection be
bypassed exactly when Paper v2 is the authority.

Count-once rule
---------------

A trade contributes to portfolio risk exactly once, from the stage it has actually
reached:

* a reservation with no fill is **not** exposure - admission reservation accounting
  already constrains new admissions, so counting it here would double-charge it;
* a fill with remaining quantity is exposure, sized by its committed cost basis;
* a zero-fill or fully closed trade contributes nothing.

This function therefore projects only *filled, still-open* canonical exposure. It
never mutates anything and never widens the risk rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "CanonicalPortfolioPosition",
    "canonical_portfolio_positions",
    "projected_positions",
]


@dataclass(frozen=True)
class CanonicalPortfolioPosition:
    """One canonical Paper-v2 exposure, shaped for the existing portfolio rules.

    Attribute names match what the existing portfolio-risk engine already reads
    from a legacy active trade, so no risk rule is reimplemented or reinterpreted.
    """

    symbol: str
    status: str
    direction: str
    capital: float
    margin_leverage: float
    paper_trade_id: str


def canonical_portfolio_positions(client: Any) -> list[CanonicalPortfolioPosition]:
    """Read canonical active exposure as portfolio-risk positions.

    Fails closed: a projection that cannot be read or is reported unhealthy raises
    rather than returning an empty list, because an empty list reads as "the
    portfolio is empty" and would authorize entries against unknown exposure. The
    caller decides how to handle that; it must not silently continue.
    """
    projection = client.get_paper_v2_active_exposures()
    status = str(getattr(projection, "status", "") or "")
    if status != "OK":
        raise RuntimeError(
            "canonical Paper-v2 exposure is unavailable: "
            f"{getattr(projection, 'error_code', None) or status or 'UNKNOWN'}"
        )
    positions: list[CanonicalPortfolioPosition] = []
    for exposure in projection.exposures:
        symbol = str(exposure.symbol or "")
        if not symbol:
            # A position whose production symbol cannot be proven must not be
            # silently dropped from portfolio risk: that would understate exposure.
            raise RuntimeError(
                "canonical Paper-v2 exposure has no provable decision symbol"
            )
        positions.append(
            CanonicalPortfolioPosition(
                symbol=symbol,
                status="active",
                direction=str(exposure.direction or "LONG"),
                capital=float(exposure.remaining_notional_basis or 0.0),
                # Long spot is unlevered by construction in this engine.
                margin_leverage=1.0,
                paper_trade_id=str(exposure.paper_trade_id),
            )
        )
    return positions


def projected_positions(authority: Any, *, client: Any) -> list[Any]:
    """The existing-position list the action gate should evaluate against.

    LEGACY keeps the legacy registry exactly as before. A granted Paper-v2 authority
    uses canonical evidence instead, so canonical fills constrain the next scan. A
    non-granted Paper-v2 request (DRAINING/UNAVAILABLE) authorizes no new entry
    anyway, so it is evaluated against canonical exposure purely so telemetry is
    truthful - it cannot produce a new entry either way.
    """
    from app.services.active_trade_registry import get_active_trades

    granted = str(getattr(authority, "granted", "") or "")
    if granted == "LEGACY":
        return list(get_active_trades())
    return canonical_portfolio_positions(client)
