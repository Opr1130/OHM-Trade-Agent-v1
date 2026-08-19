from __future__ import annotations

from typing import Any

from app.services.shadow_learning import record_shadow_candidate
from app.services.target_attainability_v2_shadow import evaluate_target_v2_shadow


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
        explicit_intelligence = market_intelligence is not None
        intelligence = market_intelligence
        if intelligence is None:
            intelligence = getattr(snapshot, "_wave8_market_intelligence", None)
        intelligence_payload = dict(intelligence) if isinstance(intelligence, dict) else {}

        # Target v2 is evidence-only. Attach its proposal to the existing shadow
        # record for normal production target captures so the 5m->24h observer
        # validates it without a parallel data path. Explicit intelligence is a
        # caller-owned override and must pass through unchanged.
        if (
            not explicit_intelligence
            and source in {"target_quality_gate", "qualified_profit_rank"}
        ):
            try:
                intelligence_payload["target_v2_shadow"] = evaluate_target_v2_shadow(snapshot).as_dict()
            except Exception:
                pass

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
            market_intelligence=intelligence_payload or None,
            price_movement=getattr(snapshot, "price_movement_signal", None),
        )
        return True
    except Exception:
        return False
