"""Acceptance checks for the O'Pip v6 shared gate interface (V6-D02).

The interface is the isolated, pure, versioned half of V6-D02: it mirrors the
production after-cost economic gate's numerics without being wired into it.
These tests pin the increment's two declared criteria: bad data fails closed with
an ordered reason code and never raises, and the numeric outcome agrees with the
production gate while the new package stays unimported outside itself.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
import pathlib
import random

import pytest

from app.opip.gates import (
    GATE_INTERFACE_VERSION,
    GateInput,
    GatePolicy,
    ReasonCode,
    evaluate_gate,
)
from app.opip.gates import interface as gate_module
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.entry_exit_advisor import EntryExitPlan

POLICY = GatePolicy(
    "P-V6-D02-ACCEPTANCE",
    min_net_profit=75.0,
    min_target_2_move_pct=4.0,
    min_reward_to_risk=2.5,
    estimated_round_trip_cost_pct=0.60,
    max_capital_fraction=0.20,
    max_account_risk_at_stop_pct=5.0,
)
ADVISORY_POLICY = GatePolicy("P-V6-D02-ADVISORY")


def _candidate(**overrides):
    base = dict(
        direction="LONG",
        entry_low=99.0,
        entry_high=101.0,
        stop_price=95.0,
        target_1=106.0,
        target_2=112.0,
        reward_to_risk_2=2.4,
        available_capital=10000.0,
        leverage=1.0,
        estimated_margin_cost_pct=0.0,
    )
    base.update(overrides)
    return GateInput(**base)


@pytest.mark.acceptance
def test_bad_input_fails_closed_with_a_reason_code_and_never_raises() -> None:
    """ATDD-OPIP-V6-GATE-INTERFACE-v1/AC-001"""
    bad_values = (None, float("nan"), float("inf"), -float("inf"), True, "5", [1])
    for field_name in ("stop_price", "available_capital", "reward_to_risk_2"):
        for bad in bad_values:
            result = evaluate_gate(_candidate(**{field_name: bad}), POLICY)
            assert result.qualified is False, (field_name, bad)
            assert result.interface_version == GATE_INTERFACE_VERSION
            assert result.reasons, (field_name, bad)
            assert result.primary_reason in {
                ReasonCode.INPUT_MISSING,
                ReasonCode.INPUT_INVALID,
            }, (field_name, bad)

    # Malformed geometry, direction and policy reject instead of raising.
    assert (
        evaluate_gate(_candidate(stop_price=102.0), POLICY).primary_reason
        == ReasonCode.GEOMETRY_INVALID
    )
    assert (
        evaluate_gate(_candidate(direction="SIDEWAYS"), POLICY).primary_reason
        == ReasonCode.INPUT_INVALID
    )
    for broken_policy in (
        None,
        dataclasses.replace(POLICY, min_net_profit=-1.0),
        dataclasses.replace(POLICY, max_capital_fraction=0.0),
        dataclasses.replace(POLICY, min_reward_to_risk=float("nan")),
    ):
        assert (
            evaluate_gate(_candidate(), broken_policy).primary_reason
            == ReasonCode.POLICY_INVALID
        )
    assert evaluate_gate(None, POLICY).primary_reason == ReasonCode.INPUT_MISSING

    # A rejection is still immutable, versioned and attributable.
    rejected = evaluate_gate(_candidate(available_capital=None), POLICY)
    assert rejected.qualified is False
    assert rejected.policy_id == POLICY.policy_id
    assert len(rejected.policy_digest) == 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        rejected.qualified = True  # type: ignore[misc]


def _production_result(candidate: GateInput, policy: GatePolicy):
    plan = EntryExitPlan(
        symbol="X",
        valid_now=True,
        entry_style="t",
        entry_low=candidate.entry_low,
        entry_high=candidate.entry_high,
        chase_limit=candidate.entry_high,
        stop_price=candidate.stop_price,
        target_1=candidate.target_1,
        target_2=candidate.target_2,
        reward_to_risk_1=1.0,
        reward_to_risk_2=candidate.reward_to_risk_2,
        risk_level="m",
        reason="",
        direction=candidate.direction,
    )
    return evaluate_economic_quality(
        plan,
        candidate.available_capital,
        min_target_2_move_pct=policy.min_target_2_move_pct,
        min_net_profit=policy.min_net_profit,
        min_reward_to_risk=policy.min_reward_to_risk,
        estimated_round_trip_cost_pct=policy.estimated_round_trip_cost_pct,
        max_capital_fraction=policy.max_capital_fraction,
        direction=candidate.direction,
        leverage=candidate.leverage,
        estimated_margin_cost_pct=candidate.estimated_margin_cost_pct,
        max_account_risk_at_stop_pct=policy.max_account_risk_at_stop_pct,
        policy_id=policy.policy_id,
    )


@pytest.mark.acceptance
def test_gate_agrees_with_production_and_stays_inside_its_package() -> None:
    """ATDD-OPIP-V6-GATE-INTERFACE-v1/AC-002"""
    rng = random.Random(1130)
    checked = 0
    for _ in range(300):
        direction = rng.choice(["LONG", "SHORT"])
        entry = rng.uniform(1, 500)
        stop_pct = rng.uniform(0.5, 12) / 100
        first_leg = rng.uniform(0.2, 8) / 100
        second_leg = rng.uniform(0.5, 20) / 100
        sign = 1 if direction == "LONG" else -1
        candidate = GateInput(
            direction,
            entry * 0.999,
            entry * 1.001,
            entry * (1 - sign * stop_pct),
            entry * (1 + sign * first_leg),
            entry * (1 + sign * (first_leg + second_leg)),
            rng.uniform(0.5, 6),
            rng.choice([500.0, 1000.0, 5000.0, 25000.0]),
            rng.choice([1.0, 2.0, 3.0, 5.0]),
            rng.choice([0.0, 0.05, 0.3]),
        )
        policy = rng.choice(
            [
                POLICY,
                ADVISORY_POLICY,
                dataclasses.replace(POLICY, max_account_risk_at_stop_pct=None),
            ]
        )
        production = _production_result(candidate, policy)
        shared = evaluate_gate(candidate, policy)
        assert shared.qualified == production.qualified, candidate
        if production.qualified:
            assert (
                shared.metrics["target_2_net_profit"]
                == production.target_2_net_profit
            )
            assert math.isclose(
                shared.metrics["account_risk_at_stop_pct"],
                production.account_risk_at_stop_pct,
                abs_tol=0.005,
            )
        checked += 1
    assert checked == 300

    # Values straddling each floor agree on qualification exactly.
    for reward_to_risk, move, capital in itertools.product(
        (2.49, 2.5, 2.51), (3.99, 4.0, 4.01), (1000.0, 1875.0, 1876.0)
    ):
        entry = 100.0
        candidate = GateInput(
            "LONG",
            entry,
            entry,
            98.0,
            entry * 1.02,
            entry * (1 + move / 100),
            reward_to_risk,
            capital,
            1.0,
            0.0,
        )
        boundary_policy = dataclasses.replace(
            POLICY, max_account_risk_at_stop_pct=None
        )
        assert (
            evaluate_gate(candidate, boundary_policy).qualified
            == _production_result(candidate, boundary_policy).qualified
        ), (reward_to_risk, move, capital)

    # Isolation: nothing outside the new package imports it.
    app_root = pathlib.Path(gate_module.__file__).parents[2]
    offenders = [
        str(path)
        for path in app_root.rglob("*.py")
        if "app/opip/gates" not in str(path).replace("\\", "/")
        and "opip.gates" in path.read_text(errors="ignore")
    ]
    assert offenders == []
