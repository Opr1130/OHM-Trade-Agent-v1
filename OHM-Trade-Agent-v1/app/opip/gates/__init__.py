"""Shared, pure, versioned gate interface (V6-D02).

Isolated by design: nothing in the live scanner imports this package. See
``docs/v6/V6_D02_GATE_INTERFACE.md``.
"""

from app.opip.gates.interface import (
    GATE_INTERFACE_VERSION,
    GateInput,
    GatePolicy,
    GateResult,
    ReasonCode,
    evaluate_gate,
)

__all__ = [
    "GATE_INTERFACE_VERSION",
    "GateInput",
    "GatePolicy",
    "GateResult",
    "ReasonCode",
    "evaluate_gate",
]
