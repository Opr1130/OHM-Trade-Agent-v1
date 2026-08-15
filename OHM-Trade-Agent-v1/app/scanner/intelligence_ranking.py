from __future__ import annotations

from dataclasses import dataclass

from app.scanner.intelligence_context import build_intelligence_context
from app.scanner.market_regime import MarketRegimeContext
from app.scanner.models import MarketSnapshot
from app.services.market_intelligence import MarketIntelligenceAssessment


@dataclass(frozen=True)
class RankedCandidate:
    candidate: MarketSnapshot
    intelligence_score: int
    setup_type: str
    regime_alignment: str
    external_market_score: int | None = None


def _external_adjustment(assessment: MarketIntelligenceAssessment | None) -> int:
    """Return a deliberately bounded external-evidence adjustment.

    Wave 8 providers are context, not a new authority. Missing evidence changes
    nothing; available evidence can move ordering by at most ten points.
    """
    if assessment is None or assessment.status != "AVAILABLE":
        return 0
    return max(-10, min(10, round((assessment.context_score - 50) / 2)))


def rank_candidates_with_context(
    candidates: list[MarketSnapshot],
    market_regime: MarketRegimeContext | None = None,
    external_market_intelligence: dict[str, MarketIntelligenceAssessment] | None = None,
) -> list[RankedCandidate]:
    """Rank admitted candidates using bounded deterministic context.

    This helper only orders candidates that were already admitted by upstream
    screening. It never creates a candidate, changes direction, or bypasses
    target, economic, execution, portfolio, drawdown, or human-approval gates.
    """
    ranked = []
    external_market_intelligence = external_market_intelligence or {}
    for candidate in candidates:
        context = build_intelligence_context(candidate, market_regime)
        external = external_market_intelligence.get(candidate.symbol)
        score = max(0, min(100, context.context_score + _external_adjustment(external)))
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                intelligence_score=score,
                setup_type=context.setup_type,
                regime_alignment=context.regime_alignment,
                external_market_score=(external.context_score if external else None),
            )
        )
    return sorted(
        ranked,
        key=lambda item: (item.intelligence_score, item.candidate.technical_score),
        reverse=True,
    )
