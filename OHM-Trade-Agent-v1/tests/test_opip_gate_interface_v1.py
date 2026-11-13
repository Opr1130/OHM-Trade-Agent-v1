"""V6-D02: shared gate interface - contract, fail-closed and equivalence tests."""

import ast
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

EXEC = GatePolicy(
    "P-EXEC",
    min_net_profit=75.0,
    min_target_2_move_pct=4.0,
    min_reward_to_risk=2.5,
    estimated_round_trip_cost_pct=0.60,
    max_capital_fraction=0.20,
    max_account_risk_at_stop_pct=5.0,
)
ADV = GatePolicy("P-ADV")


def good(**kw):
    base = dict(
        direction="LONG", entry_low=99.0, entry_high=101.0, stop_price=95.0,
        target_1=106.0, target_2=112.0, reward_to_risk_2=2.4,
        available_capital=10000.0, leverage=1.0, estimated_margin_cost_pct=0.0,
    )
    base.update(kw)
    return GateInput(**base)


def test_pass_carries_version_digest_and_metrics():
    r = evaluate_gate(good(), ADV)
    assert r.qualified and r.reasons == () and r.primary_reason is None
    assert r.interface_version == GATE_INTERFACE_VERSION
    assert r.policy_digest == ADV.digest() and len(r.policy_digest) == 64


def test_digest_changes_with_any_policy_field():
    assert ADV.digest() != dataclasses.replace(ADV, min_net_profit=1.0).digest()
    assert ADV.digest() == GatePolicy("P-ADV").digest()


@pytest.mark.parametrize("field", [
    "direction", "entry_low", "entry_high", "stop_price", "target_1",
    "target_2", "reward_to_risk_2", "available_capital", "leverage",
    "estimated_margin_cost_pct",
])
def test_missing_is_never_zero(field):
    r = evaluate_gate(good(**{field: None}), ADV)
    assert not r.qualified
    assert r.primary_reason in {ReasonCode.INPUT_MISSING, ReasonCode.INPUT_INVALID}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True, "5", [1]])
def test_non_numeric_non_finite_and_bool_fail_closed(bad):
    for f in ("stop_price", "available_capital", "reward_to_risk_2"):
        r = evaluate_gate(good(**{f: bad}), ADV)
        assert not r.qualified and r.primary_reason == ReasonCode.INPUT_INVALID


def test_invalid_policy_fails_closed_never_raises():
    for p in (
        dataclasses.replace(ADV, min_net_profit=-1),
        dataclasses.replace(ADV, max_capital_fraction=0),
        dataclasses.replace(ADV, max_capital_fraction=1.5),
        dataclasses.replace(ADV, min_reward_to_risk=float("nan")),
        dataclasses.replace(ADV, policy_id=" "),
        dataclasses.replace(ADV, max_account_risk_at_stop_pct=float("inf")),
    ):
        r = evaluate_gate(good(), p)
        assert not r.qualified and r.primary_reason == ReasonCode.POLICY_INVALID
    assert not evaluate_gate(good(), None).qualified
    assert not evaluate_gate(None, ADV).qualified


def test_bad_geometry_and_direction():
    assert evaluate_gate(good(stop_price=102.0), ADV).primary_reason == ReasonCode.GEOMETRY_INVALID
    assert evaluate_gate(good(target_2=105.0), ADV).primary_reason == ReasonCode.GEOMETRY_INVALID
    assert evaluate_gate(good(direction="SIDEWAYS"), ADV).primary_reason == ReasonCode.INPUT_INVALID


def test_zero_net_profit_is_rejected_even_with_filters_off():
    # costs consume the whole target-2 gross profit -> exactly $0.00 net
    c = good(entry_low=100, entry_high=100, stop_price=90, target_1=101, target_2=102)
    pol = dataclasses.replace(ADV, estimated_round_trip_cost_pct=2.0)
    r = evaluate_gate(c, pol)
    assert not r.qualified and ReasonCode.NET_PROFIT_BELOW_MIN in r.reasons


def test_reasons_are_ordered_and_complete():
    c = good(reward_to_risk_2=1.0, target_2=100.5 + 0.0001, target_1=100.4)
    r = evaluate_gate(c, EXEC)
    assert r.reasons[0] == ReasonCode.REWARD_TO_RISK_BELOW_MIN
    assert ReasonCode.TARGET_MOVE_BELOW_MIN in r.reasons
    assert list(r.reasons) == sorted(r.reasons, key=list(ReasonCode).index)


def test_result_is_immutable_and_deterministic():
    r1, r2 = evaluate_gate(good(), EXEC), evaluate_gate(good(), EXEC)
    assert r1 == r2
    with pytest.raises(dataclasses.FrozenInstanceError):
        r1.qualified = False  # type: ignore[misc]


def test_module_is_pure_no_io_clock_env_or_service_imports():
    tree = ast.parse(pathlib.Path(gate_module.__file__).read_text())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0])
    assert imported <= {"__future__", "hashlib", "json", "math", "dataclasses", "enum", "typing"}


def test_not_wired_into_live_scanner():
    root = pathlib.Path(gate_module.__file__).parents[2]
    offenders = [
        str(p) for p in root.rglob("*.py")
        if "app/opip/gates" not in str(p).replace("\\", "/")
        and "opip.gates" in p.read_text(errors="ignore")
    ]
    assert offenders == []


# --- equivalence with the production gate (evidence for later integration) ---

def _prod(c: GateInput, p: GatePolicy):
    plan = EntryExitPlan(
        symbol="X", valid_now=True, entry_style="t", entry_low=c.entry_low,
        entry_high=c.entry_high, chase_limit=c.entry_high, stop_price=c.stop_price,
        target_1=c.target_1, target_2=c.target_2, reward_to_risk_1=1.0,
        reward_to_risk_2=c.reward_to_risk_2, risk_level="m", reason="", direction=c.direction,
    )
    return evaluate_economic_quality(
        plan, c.available_capital,
        min_target_2_move_pct=p.min_target_2_move_pct, min_net_profit=p.min_net_profit,
        min_reward_to_risk=p.min_reward_to_risk,
        estimated_round_trip_cost_pct=p.estimated_round_trip_cost_pct,
        max_capital_fraction=p.max_capital_fraction, direction=c.direction,
        leverage=c.leverage, estimated_margin_cost_pct=c.estimated_margin_cost_pct,
        max_account_risk_at_stop_pct=p.max_account_risk_at_stop_pct, policy_id=p.policy_id,
    )


def _cases():
    rng = random.Random(1130)
    for _ in range(3000):
        d = rng.choice(["LONG", "SHORT"])
        e = rng.uniform(1, 500)
        s = rng.uniform(0.5, 12) / 100
        m1, m2 = rng.uniform(0.2, 8) / 100, rng.uniform(0.5, 20) / 100
        sign = 1 if d == "LONG" else -1
        c = GateInput(
            d, e * 0.999, e * 1.001, e * (1 - sign * s), e * (1 + sign * m1),
            e * (1 + sign * (m1 + m2)), rng.uniform(0.5, 6),
            rng.choice([500.0, 1000.0, 5000.0, 25000.0]), rng.choice([1.0, 2.0, 3.0, 5.0]),
            rng.choice([0.0, 0.05, 0.3]),
        )
        yield c, rng.choice([EXEC, ADV, dataclasses.replace(EXEC, max_account_risk_at_stop_pct=None)])


def test_equivalent_to_production_gate_on_randomized_corpus():
    n = 0
    for c, p in _cases():
        prod, new = _prod(c, p), evaluate_gate(c, p)
        assert new.qualified == prod.qualified, (c, p)
        if prod.qualified:
            assert new.metrics["target_2_net_profit"] == prod.target_2_net_profit
            assert math.isclose(new.metrics["account_risk_at_stop_pct"], prod.account_risk_at_stop_pct, abs_tol=0.005)
        n += 1
    assert n == 3000


def test_equivalent_on_boundary_grid():
    # values straddling each floor exactly
    for rr, move, cap in itertools.product((2.49, 2.5, 2.51), (3.99, 4.0, 4.01), (1000.0, 1875.0, 1876.0)):
        e = 100.0
        c = GateInput("LONG", e, e, 98.0, e * 1.02, e * (1 + move / 100), rr, cap, 1.0, 0.0)
        p = dataclasses.replace(EXEC, max_account_risk_at_stop_pct=None)
        assert evaluate_gate(c, p).qualified == _prod(c, p).qualified, (rr, move, cap)
