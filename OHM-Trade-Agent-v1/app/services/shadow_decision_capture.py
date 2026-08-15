from __future__ import annotations

from typing import Any

from app.services.shadow_learning import record_shadow_candidate


def capture_snapshot_decision(
    snapshot: Any,
    *,
    decision: str,
    market_regime: str | None,
    reason: str,
    source: str,
    profit_rank_score: float | None = None,
    market_intelligence: dict[str, Any] | None = None,
) -> bool:
    """Persist a future-outcome shadow record without affecting trading flow.

    Learning must always be fail-open: malformed optional evidence or a storage
    problem is never allowed to block a scan, risk gate, alert, or monitor.
    """
    try:
        execution = getattr(snapshot, "execution_validation", None)
        spread_bps = getattr(execution, "spread_bps", None) if execution is not None else None
        record_shadow_candidate(
            symbol=str(snapshot.symbol),
            direction=str(getattr(snapshot, "trade_direction", "LONG") or "LONG"),
            decision=decision,
            reference_price=float(snapshot.last_price),
            market_regime=market_regime,
            technical_score=getattr(snapshot, "technical_score", None),
            profit_rank_score=profit_rank_score,
            volume_ratio=getattr(snapshot, "volume_ratio", None),
            spread_bps=spread_bps,
            reason=reason,
            source=source,
            market_intelligence=market_intelligence,
        )
        return True
    except Exception:
        return False
