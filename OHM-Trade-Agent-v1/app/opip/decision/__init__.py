"""O'Pip Decision Engine (shadow / read-only comparison mode).

Build 1 scope: the engine reproduces the existing production qualification
logic, centralises the decision representation, exposes gate-level reasoning,
and records a complete qualification funnel. It is deliberately NOT
authoritative: production admissions continue to be produced by the legacy
path in ``app.jobs.scan_opportunities``.

Safety: no module in this package imports an exchange client, and no object
defined here exposes an order-placement method. See
``tests/test_opip_decision_engine_safety_v1.py``.
"""

from app.opip.decision.models import (
    AdmissionDecision,
    DecisionOutcome,
    GateName,
    GateResult,
    GateStatus,
    ReasonClass,
    ReasonCode,
)
from app.opip.decision.versioning import (
    GATE_POLICY_VERSION,
    INTELLIGENCE_VERSION,
    STRATEGY_VERSION,
    version_stamp,
)

__all__ = [
    "AdmissionDecision",
    "DecisionOutcome",
    "GateName",
    "GateResult",
    "GateStatus",
    "ReasonClass",
    "ReasonCode",
    "GATE_POLICY_VERSION",
    "INTELLIGENCE_VERSION",
    "STRATEGY_VERSION",
    "version_stamp",
]
