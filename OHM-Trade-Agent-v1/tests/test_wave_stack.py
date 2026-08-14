from app.services.autonomy_readiness import AutonomyReadiness
from app.services.learning_governance import LearningPromotionDecision
from app.services.production_scaling import ProductionStageDecision
from app.services.wave_stack import WaveStackStatus


def test_wave_stack_requires_learning_operations_and_scaling_gate():
    status = WaveStackStatus(
        learning=LearningPromotionDecision("READY_FOR_HUMAN_REVIEW", True, False, "ok"),
        operations=AutonomyReadiness("READY_FOR_SUPERVISED_AUTOMATION", True, ()),
        production=ProductionStageDecision("PAPER", "TINY_LIVE", True, 0.05, "ok"),
    )
    assert status.safe_for_requested_stage_advance is True


def test_wave_stack_blocks_when_any_gate_fails():
    status = WaveStackStatus(
        learning=LearningPromotionDecision("REJECT", False, False, "bad edge"),
        operations=AutonomyReadiness("READY_FOR_SUPERVISED_AUTOMATION", True, ()),
        production=ProductionStageDecision("PAPER", "TINY_LIVE", True, 0.05, "ok"),
    )
    assert status.safe_for_requested_stage_advance is False
