"""B/C-3 Paper v2 immutable protection-plan builder.

Translates a qualified opportunity's already-decided exit geometry into the frozen
B/C-2 protection plan. This is translation, not strategy: no trailing stops, no
dynamic optimisation, no AI adjustment, no learning, and no new behaviour beyond
what the qualified opportunity plus bounded paper-only policy already express.

Policy versus opportunity data
------------------------------

The stop and target *prices* come from the qualified opportunity and are never
duplicated in configuration. Only the two genuine policy assumptions are
configured: the first target's share of the position and the maximum hold. A
staged plan is expressed by giving the first target that share and the residual
target the remainder, which sums to exactly the whole position.

Identity is deterministic and immutable: the plan id is derived from the trade and
plan sequence with the repository's existing ``stable_hash`` convention, so
re-running the producer for the same trade yields the same plan identity rather
than a second plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_PROTECTION_PLAN_RECORDED,
    validate_paper_evidence_payload,
)
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc

PLAN_ID_PREFIX = "PPLAN"
FIRST_TARGET_ID = "TP1"
RESIDUAL_TARGET_ID = "TP2"


@dataclass(frozen=True)
class ProtectionPlanSource:
    """Qualified exit geometry for one Paper v2 trade.

    ``target_prices`` is the opportunity's own ordered target list (ascending), so
    the builder never invents a level the qualification did not produce.
    """

    paper_trade_id: str
    plan_seq: int
    stop_price: float
    target_prices: tuple[float, ...]
    plan_time: datetime


def build_protection_plan_id(
    *,
    paper_trade_id: str,
    plan_seq: int,
) -> str:
    """Deterministic, immutable plan identity for a trade and plan sequence."""
    return stable_hash(
        PLAN_ID_PREFIX,
        {
            "paper_trade_id": str(paper_trade_id),
            "plan_seq": int(plan_seq),
        },
    )


def _require_positive(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite positive number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        raise ValueError(f"{field_name} must be a finite positive number")
    return number


def build_protection_plan_payload(
    source: ProtectionPlanSource,
    *,
    tp1_fraction: float,
    max_hold_seconds: int,
) -> dict:
    """Build the canonical B/C-2 protection plan payload.

    Long-only semantics are enforced here: the stop must sit below the position and
    the targets must ascend above it, matching the qualified geometry. The payload
    is validated through the frozen contract, so a plan the canonical writer would
    reject can never be produced.
    """
    if not isinstance(source, ProtectionPlanSource):
        raise ValueError("source must be a ProtectionPlanSource")

    paper_trade_id = str(source.paper_trade_id or "")
    if not paper_trade_id or paper_trade_id != paper_trade_id.strip():
        raise ValueError("paper_trade_id must be a non-empty canonical string")
    if type(source.plan_seq) is not int or source.plan_seq < 0:
        raise ValueError("plan_seq must be a non-negative integer")

    stop_price = _require_positive(source.stop_price, field_name="stop_price")
    prices = tuple(
        _require_positive(price, field_name="target price")
        for price in source.target_prices
    )
    if len(prices) < 2:
        raise ValueError(
            "a staged protection plan requires at least two qualified targets"
        )
    if list(prices) != sorted(prices):
        raise ValueError("target prices must ascend")
    if len(set(prices)) != len(prices):
        raise ValueError("target prices must be distinct")
    # Long-only geometry: protection sits below the position and targets above it.
    if stop_price >= prices[0]:
        raise ValueError("stop_price must sit below the first target")

    first_fraction = float(tp1_fraction)
    if not 0.0 < first_fraction < 1.0:
        raise ValueError("tp1_fraction must be within (0, 1)")
    residual_fraction = 1.0 - first_fraction
    if not 0.0 < residual_fraction <= 1.0:
        raise ValueError("residual target fraction must be within (0, 1]")

    hold = int(max_hold_seconds)
    if hold < 1:
        raise ValueError("max_hold_seconds must be positive")

    plan_time = require_utc(source.plan_time, field_name="plan_time")

    # Two staged targets: the first takes its configured share, the residual
    # target takes the remainder so the plan never allocates more than the
    # position and never silently leaves part of it unprotected.
    targets = [
        {
            "target_id": FIRST_TARGET_ID,
            "price": prices[0],
            "fraction": first_fraction,
        },
        {
            "target_id": RESIDUAL_TARGET_ID,
            "price": prices[-1],
            "fraction": residual_fraction,
        },
    ]

    payload = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_plan_id": build_protection_plan_id(
            paper_trade_id=paper_trade_id, plan_seq=source.plan_seq
        ),
        "paper_trade_id": paper_trade_id,
        "plan_seq": source.plan_seq,
        "stop_price": stop_price,
        "targets": targets,
        "max_hold_seconds": hold,
        "plan_time": {
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": iso_z(plan_time, field_name="plan_time"),
        },
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }
    # The frozen contract is the authority on plan shape.
    return validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


def protection_plan_idempotency_key(payload: dict) -> str:
    """The canonical idempotency key for a protection plan payload."""
    from app.opip.contracts.paper_execution_events import (
        paper_evidence_idempotency_key,
    )

    return paper_evidence_idempotency_key(PAPER_PROTECTION_PLAN_RECORDED, payload)


__all__ = [
    "FIRST_TARGET_ID",
    "PLAN_ID_PREFIX",
    "RESIDUAL_TARGET_ID",
    "ProtectionPlanSource",
    "build_protection_plan_id",
    "build_protection_plan_payload",
    "protection_plan_idempotency_key",
]
