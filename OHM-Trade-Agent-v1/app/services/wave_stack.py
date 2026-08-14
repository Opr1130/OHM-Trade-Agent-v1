from __future__ import annotations

from dataclasses import dataclass

from app.services.autonomy_readiness import AutonomyReadiness
from app.services.learning_governance import LearningPromotionDecision
from app.services.production_scaling import ProductionStageDecision


@dataclass(frozen=True)
class WaveStackStatus:
    learning: LearningPromotionDecision
    operations: AutonomyReadiness
    production: ProductionStageDecision

    @property
    def safe_for_requested_stage_advance(self) -> bool:
        return (
            self.learning.eligible_for_review
            and self.operations.unattended_allowed
            and self.production.advance_allowed
        )
