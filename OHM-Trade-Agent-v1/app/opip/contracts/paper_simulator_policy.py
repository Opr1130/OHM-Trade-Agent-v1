"""Paper v2 simulator-policy contract for PR-B/C-0.

This freezes what the simulator is allowed to claim before runtime activation.
It intentionally does not invent calibration values that the repository has not
yet justified with retained market evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.opip.contracts.paper_execution import (
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
)


PAPER_SIMULATOR_POLICY_SCHEMA_VERSION = 1
PAPER_SIMULATOR_POLICY_VERSION = "opip-paper-sim-policy-v1"


class SimulationFidelity(str, Enum):
    LEVEL_0_OHLC_BOUNDED = "LEVEL_0_OHLC_BOUNDED"
    LEVEL_1_QUOTE_BACKED = "LEVEL_1_QUOTE_BACKED"


class ModelingDisposition(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL_WHEN_EVIDENCE_EXISTS = "OPTIONAL_WHEN_EVIDENCE_EXISTS"
    NOT_MODELED = "NOT_MODELED"


class LatencyModel(str, Enum):
    NOT_MODELED = "NOT_MODELED"
    FIXED_DETERMINISTIC = "FIXED_DETERMINISTIC"


class PassiveLimitFillModel(str, Enum):
    OHLC_TOUCH_BOUNDED = "OHLC_TOUCH_BOUNDED"
    QUOTE_CROSS_CONFIRMATION = "QUOTE_CROSS_CONFIRMATION"


@dataclass(frozen=True)
class PaperSimulatorPolicy:
    policy_version: str
    fidelity: SimulationFidelity
    execution_model_version: str
    economic_model_version: str
    protection_model_version: str

    quote_freshness: ModelingDisposition
    bid_ask_spread: ModelingDisposition
    fees: ModelingDisposition
    slippage: ModelingDisposition
    partial_fills: ModelingDisposition
    market_impact: ModelingDisposition
    queue_position: ModelingDisposition

    latency_model: LatencyModel
    fixed_latency_ms: int | None
    passive_limit_fill_model: PassiveLimitFillModel

    require_quote_currency_identity: bool = True
    allow_cross_currency_equity_aggregation: bool = False
    trigger_is_exit: bool = False
    schema_version: int = PAPER_SIMULATOR_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.policy_version != PAPER_SIMULATOR_POLICY_VERSION:
            raise ValueError("unsupported paper simulator policy version")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported paper simulator policy schema version")

        for field_name, enum_type in (
            ("fidelity", SimulationFidelity),
            ("quote_freshness", ModelingDisposition),
            ("bid_ask_spread", ModelingDisposition),
            ("fees", ModelingDisposition),
            ("slippage", ModelingDisposition),
            ("partial_fills", ModelingDisposition),
            ("market_impact", ModelingDisposition),
            ("queue_position", ModelingDisposition),
            ("latency_model", LatencyModel),
            ("passive_limit_fill_model", PassiveLimitFillModel),
        ):
            value = getattr(self, field_name)
            if not isinstance(value, enum_type):
                try:
                    object.__setattr__(self, field_name, enum_type(str(value)))
                except ValueError as exc:
                    raise ValueError(f"unsupported {field_name}") from exc

        if self.execution_model_version != PAPER_EXECUTION_MODEL_VERSION:
            raise ValueError("execution model version must match Paper v2 contract")
        if self.economic_model_version != PAPER_ECONOMIC_MODEL_VERSION:
            raise ValueError("economic model version must match Paper v2 contract")
        if self.protection_model_version != PAPER_PROTECTION_MODEL_VERSION:
            raise ValueError("protection model version must match Paper v2 contract")

        if self.latency_model is LatencyModel.FIXED_DETERMINISTIC:
            if type(self.fixed_latency_ms) is not int or self.fixed_latency_ms < 0:
                raise ValueError("FIXED_DETERMINISTIC latency requires non-negative fixed_latency_ms")
        elif self.fixed_latency_ms is not None:
            raise ValueError("fixed_latency_ms is legal only for FIXED_DETERMINISTIC latency")

        if self.trigger_is_exit:
            raise ValueError("a protection trigger cannot be treated as an exit")
        if not self.require_quote_currency_identity:
            raise ValueError("Paper v2 requires exact quote-currency identity")
        if self.allow_cross_currency_equity_aggregation:
            raise ValueError("cross-currency equity aggregation is not authorized")

        if self.market_impact is not ModelingDisposition.NOT_MODELED:
            raise ValueError("market impact is not supported by retained evidence")
        if self.queue_position is not ModelingDisposition.NOT_MODELED:
            raise ValueError("queue position is not supported by retained evidence")

        if self.fidelity is SimulationFidelity.LEVEL_1_QUOTE_BACKED:
            for field_name in ("quote_freshness", "bid_ask_spread", "fees", "slippage"):
                if getattr(self, field_name) is ModelingDisposition.NOT_MODELED:
                    raise ValueError(f"Level-1 quote-backed simulation must model {field_name}")


#: Target policy for new prospective Paper v2 trades.
#:
#: Latency stays NOT_MODELED in B/C-0 because no defensible calibration value is
#: yet frozen. B/C-1 may introduce FIXED_DETERMINISTIC only together with a
#: versioned numeric assumption and tests. Partial fills are conditional on
#: reliable quote/depth evidence; market impact and queue position remain out.
OPIP_PAPER_V2_TARGET_POLICY = PaperSimulatorPolicy(
    policy_version=PAPER_SIMULATOR_POLICY_VERSION,
    fidelity=SimulationFidelity.LEVEL_1_QUOTE_BACKED,
    execution_model_version=PAPER_EXECUTION_MODEL_VERSION,
    economic_model_version=PAPER_ECONOMIC_MODEL_VERSION,
    protection_model_version=PAPER_PROTECTION_MODEL_VERSION,
    quote_freshness=ModelingDisposition.REQUIRED,
    bid_ask_spread=ModelingDisposition.REQUIRED,
    fees=ModelingDisposition.REQUIRED,
    slippage=ModelingDisposition.REQUIRED,
    partial_fills=ModelingDisposition.OPTIONAL_WHEN_EVIDENCE_EXISTS,
    market_impact=ModelingDisposition.NOT_MODELED,
    queue_position=ModelingDisposition.NOT_MODELED,
    latency_model=LatencyModel.NOT_MODELED,
    fixed_latency_ms=None,
    passive_limit_fill_model=PassiveLimitFillModel.QUOTE_CROSS_CONFIRMATION,
)


#: Compatibility diagnostic policy for old OHLC-derived evidence. It cannot
#: claim exact intrabar occurrence times or become headline Paper v2 economics.
LEGACY_OHLC_DIAGNOSTIC_POLICY = PaperSimulatorPolicy(
    policy_version=PAPER_SIMULATOR_POLICY_VERSION,
    fidelity=SimulationFidelity.LEVEL_0_OHLC_BOUNDED,
    execution_model_version=PAPER_EXECUTION_MODEL_VERSION,
    economic_model_version=PAPER_ECONOMIC_MODEL_VERSION,
    protection_model_version=PAPER_PROTECTION_MODEL_VERSION,
    quote_freshness=ModelingDisposition.OPTIONAL_WHEN_EVIDENCE_EXISTS,
    bid_ask_spread=ModelingDisposition.OPTIONAL_WHEN_EVIDENCE_EXISTS,
    fees=ModelingDisposition.REQUIRED,
    slippage=ModelingDisposition.REQUIRED,
    partial_fills=ModelingDisposition.NOT_MODELED,
    market_impact=ModelingDisposition.NOT_MODELED,
    queue_position=ModelingDisposition.NOT_MODELED,
    latency_model=LatencyModel.NOT_MODELED,
    fixed_latency_ms=None,
    passive_limit_fill_model=PassiveLimitFillModel.OHLC_TOUCH_BOUNDED,
)


__all__ = [
    "LEGACY_OHLC_DIAGNOSTIC_POLICY",
    "LatencyModel",
    "ModelingDisposition",
    "OPIP_PAPER_V2_TARGET_POLICY",
    "PAPER_SIMULATOR_POLICY_SCHEMA_VERSION",
    "PAPER_SIMULATOR_POLICY_VERSION",
    "PaperSimulatorPolicy",
    "PassiveLimitFillModel",
    "SimulationFidelity",
]
