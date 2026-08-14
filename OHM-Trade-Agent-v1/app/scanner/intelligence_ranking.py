from __future__ import annotations

from dataclasses import dataclass

from app.scanner.intelligence_context import build_intelligence_context
from app.scanner.market_regime import MarketRegimeContext
from app.scanner.models import MarketSnapshot


@dataclass(frozen=True)
class RankedCandidate:
    candidate: MarketSnapshot
    intelligence_score: int
    setup_type: str
    regime_alignment: str


def rank_candidates_with_context(
    candidates: list[MarketSnapshot],
    market_regime: MarketRegimeContext | None = None,
) -> list[RankedCandidate]:
    """Rank review candidates without bypassing any existing eligibility gate.

    This helper only orders candidates that were already admitted by upstream
    deterministic screening. It never creates a candidate or changes direction.
    """
    ranked = []
    for candidate in candidates:
        context = build_intelligence_context(candidate, market_regime)
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                intelligence_score=context.context_score,
                setup_type=context.setup_type,
                regime_alignment=context.regime_alignment,
            )
        )
    return sorted(
        ranked,
        key=lambda item: (item.intelligence_score, item.candidate.technical_score),
        reverse=True,
    )
