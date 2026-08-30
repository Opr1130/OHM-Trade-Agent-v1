"""Paper-learning direction readiness for O'Pip Sequence 5 Wave A3.

This module reports capability; it never enables paper or funded execution.
Current production Paper Trade v1 is spot-LONG only. SHORT/extended_short learning
therefore remains explicitly NOT_READY until direction-safe lifecycle and cost
accounting are implemented and reviewed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class PaperReadinessState(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class PaperDirectionReadiness:
    direction: str
    state: PaperReadinessState
    reasons: tuple[str, ...]
    paper_only: bool = True
    funded_execution_allowed: bool = False
    leverage_authority: bool = False
    trade_authority_changed: bool = False

    def __post_init__(self) -> None:
        """Validate one immutable paper-readiness assessment."""
        direction = str(self.direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("paper readiness direction must be LONG or SHORT")
        object.__setattr__(self, "direction", direction)
        object.__setattr__(
            self,
            "reasons",
            tuple(sorted({str(item) for item in self.reasons if str(item)})),
        )
        if self.funded_execution_allowed or self.leverage_authority:
            raise ValueError("paper readiness cannot authorize funded/leverage execution")
        if self.trade_authority_changed:
            raise ValueError("paper readiness cannot change trade authority")
        if self.state is PaperReadinessState.NOT_READY and not self.reasons:
            raise ValueError("NOT_READY requires explicit reasons")

    def as_dict(self) -> dict[str, Any]:
        """Return dashboard-ready paper readiness evidence."""
        row = asdict(self)
        row["state"] = self.state.value
        row["reasons"] = list(self.reasons)
        return row


@dataclass(frozen=True)
class PaperLearningReadinessReport:
    long: PaperDirectionReadiness
    short: PaperDirectionReadiness
    extended_short_learning_ready: bool
    measurement_only: bool = True
    funded_execution_allowed: bool = False
    trade_authority_changed: bool = False

    def __post_init__(self) -> None:
        """Validate the report-level paper authority boundary."""
        if not self.measurement_only:
            raise ValueError("paper readiness report must remain measurement-only")
        if self.funded_execution_allowed or self.trade_authority_changed:
            raise ValueError("paper readiness report cannot change funded trade authority")

    def as_dict(self) -> dict[str, Any]:
        """Return the combined LONG/SHORT readiness report."""
        return {
            "long": self.long.as_dict(),
            "short": self.short.as_dict(),
            "extended_short_learning_ready": self.extended_short_learning_ready,
            "measurement_only": self.measurement_only,
            "funded_execution_allowed": self.funded_execution_allowed,
            "trade_authority_changed": self.trade_authority_changed,
        }


def assess_paper_learning_readiness(
    *,
    long_production_verified: bool = False,
) -> PaperLearningReadinessReport:
    """Report current paper capability, failing closed on unverified production health."""
    long = PaperDirectionReadiness(
        direction="LONG",
        state=(
            PaperReadinessState.READY
            if long_production_verified
            else PaperReadinessState.NOT_READY
        ),
        reasons=(
            ()
            if long_production_verified
            else ("LONG_PAPER_PRODUCTION_HEALTH_NOT_VERIFIED",)
        ),
    )
    short = PaperDirectionReadiness(
        direction="SHORT",
        state=PaperReadinessState.NOT_READY,
        reasons=(
            "PAPER_ENGINE_V1_LONG_ONLY",
            "SHORT_ENTRY_STOP_TARGET_GEOMETRY_NOT_IMPLEMENTED",
            "SHORT_LIFECYCLE_ACCOUNTING_UNVERIFIED",
            "FUNDING_MARGIN_LIQUIDATION_ACCOUNTING_UNVERIFIED",
        ),
    )
    return PaperLearningReadinessReport(
        long=long,
        short=short,
        extended_short_learning_ready=False,
    )
