"""O'Pip early-detection and high-confidence qualification (Issue #223).

This package separates three orthogonal concepts that the legacy Early Watch
path conflated into a single ``stage`` string:

``MarketPhase``
    what the market is doing.
``EvidenceGrade``
    what O'Pip has actually validated.
``OperatorDisposition``
    what the operator should do now.

Everything here is measurement-first. No module in this package places an
order, mutates a risk gate, changes position sizing, or grants any execution
authority. Modules that could change production selection are gated by
explicitly dark feature flags in :mod:`app.opip.early.flags`.
"""

from app.opip.early.taxonomy import (
    EXTENSION_POLICY_VERSION,
    TAXONOMY_VERSION,
    EvidenceGrade,
    MarketPhase,
    OperatorDisposition,
    ValidationClass,
    ValidationResult,
    coerce_evidence_grade,
    coerce_market_phase,
    coerce_operator_disposition,
)

__all__ = [
    "EXTENSION_POLICY_VERSION",
    "TAXONOMY_VERSION",
    "EvidenceGrade",
    "MarketPhase",
    "OperatorDisposition",
    "ValidationClass",
    "ValidationResult",
    "coerce_evidence_grade",
    "coerce_market_phase",
    "coerce_operator_disposition",
]
