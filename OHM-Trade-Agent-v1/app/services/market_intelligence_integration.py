from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.scanner.intelligence_ranking import RankedCandidate, rank_candidates_with_context
from app.scanner.market_regime import MarketRegimeContext
from app.scanner.models import MarketSnapshot
from app.services.market_intelligence import MarketIntelligenceAssessment
from app.services.market_intelligence_runtime import (
    DEFAULT_EXTERNAL_ENRICHMENT_CANDIDATES,
    MarketIntelligenceEvidence,
    build_market_intelligence_evidence,
    serialize_market_intelligence_evidence,
)


@dataclass(frozen=True)
class ChiefMarketRegimeContext:
    """Chief payload shape that preserves breadth fields and adds Wave 8 context."""

    status: str
    sample_size: int
    regime: str
    breadth_score: float | None
    pct_above_ema20: float | None
    pct_above_ema50: float | None
    pct_above_ema200: float | None
    pct_positive_momentum_24h: float | None
    pct_positive_momentum_72h: float | None
    pct_bullish_trend: float | None
    median_momentum_24h_pct: float | None
    median_momentum_72h_pct: float | None
    warnings: tuple[str, ...]
    external_market_intelligence: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class FinalistMarketIntelligence:
    candidates: tuple[MarketSnapshot, ...]
    assessments: dict[str, MarketIntelligenceAssessment]
    evidence: dict[str, dict[str, Any]]
    chief_market_regime_context: Any
    ranked_finalists: tuple[RankedCandidate, ...]


def _chief_context(
    market_regime: MarketRegimeContext,
    evidence: dict[str, dict[str, Any]],
) -> Any:
    # No external evidence must be an exact no-op for every existing Chief
    # caller and extension seam. In particular, older tests and integrations may
    # provide a lightweight namespace instead of the full MarketRegimeContext.
    if not evidence:
        return market_regime

    return ChiefMarketRegimeContext(
        status=str(getattr(market_regime, "status", "AVAILABLE")),
        sample_size=int(getattr(market_regime, "sample_size", 0) or 0),
        regime=str(getattr(market_regime, "regime", "UNKNOWN")),
        breadth_score=getattr(market_regime, "breadth_score", None),
        pct_above_ema20=getattr(market_regime, "pct_above_ema20", None),
        pct_above_ema50=getattr(market_regime, "pct_above_ema50", None),
        pct_above_ema200=getattr(market_regime, "pct_above_ema200", None),
        pct_positive_momentum_24h=getattr(
            market_regime,
            "pct_positive_momentum_24h",
            None,
        ),
        pct_positive_momentum_72h=getattr(
            market_regime,
            "pct_positive_momentum_72h",
            None,
        ),
        pct_bullish_trend=getattr(market_regime, "pct_bullish_trend", None),
        median_momentum_24h_pct=getattr(
            market_regime,
            "median_momentum_24h_pct",
            None,
        ),
        median_momentum_72h_pct=getattr(
            market_regime,
            "median_momentum_72h_pct",
            None,
        ),
        warnings=tuple(getattr(market_regime, "warnings", ()) or ()),
        external_market_intelligence=evidence,
    )


def enrich_finalist_market_intelligence(
    candidates: Sequence[MarketSnapshot],
    market_regime: MarketRegimeContext,
    *,
    max_candidates: int = DEFAULT_EXTERNAL_ENRICHMENT_CANDIDATES,
) -> FinalistMarketIntelligence:
    """Enrich only the already-admitted finalist prefix and optionally reorder it.

    External evidence never creates a candidate, changes its direction, or
    bypasses execution, target, economic, portfolio, drawdown, or human gates.
    If no provider is configured or no evidence is available, upstream ordering
    is preserved exactly.
    """
    if max_candidates < 0:
        raise ValueError("max_candidates cannot be negative")

    finalist_count = min(len(candidates), max_candidates)
    finalists = list(candidates[:finalist_count])
    tail = list(candidates[finalist_count:])

    try:
        raw_evidence: dict[str, MarketIntelligenceEvidence] = (
            build_market_intelligence_evidence(
                [(candidate.symbol, candidate.trade_direction) for candidate in finalists],
                max_candidates=max_candidates,
            )
            if finalists and max_candidates > 0
            else {}
        )
        assessments = {
            symbol: item.assessment
            for symbol, item in raw_evidence.items()
        }
        serialized = {
            symbol: serialize_market_intelligence_evidence(item)
            for symbol, item in raw_evidence.items()
        }
    except Exception:
        # External intelligence is advisory only. A provider/orchestration defect
        # must degrade to the original finalist order rather than stop a scan.
        assessments = {}
        serialized = {}

    for candidate in finalists:
        # In-memory, sanitized evidence lets every downstream shadow decision --
        # including Chief AI rejects captured in another module -- retain the
        # same Wave 8 context without changing the MarketSnapshot schema.
        setattr(
            candidate,
            "_wave8_market_intelligence",
            serialized.get(candidate.symbol),
        )

    any_available = any(
        assessment.status == "AVAILABLE"
        for assessment in assessments.values()
    )
    ranked_finalists: tuple[RankedCandidate, ...] = ()
    ordered_finalists = finalists
    if any_available:
        try:
            ranked_finalists = tuple(
                rank_candidates_with_context(
                    finalists,
                    market_regime=market_regime,
                    external_market_intelligence=assessments,
                )
            )
            ordered_finalists = [item.candidate for item in ranked_finalists]
        except Exception:
            # Ranking context is optional and cannot remove or block finalists.
            ranked_finalists = ()
            ordered_finalists = finalists

    return FinalistMarketIntelligence(
        candidates=tuple(ordered_finalists + tail),
        assessments=assessments,
        evidence=serialized,
        chief_market_regime_context=_chief_context(market_regime, serialized),
        ranked_finalists=ranked_finalists,
    )
