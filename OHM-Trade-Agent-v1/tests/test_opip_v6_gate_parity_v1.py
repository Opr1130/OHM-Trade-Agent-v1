"""Simulator-to-production gate parity: drift is visible, production is untouched."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.opip.decision.thresholds import gate_policy_constants
from app.opip.simulator import parity

PARITY_FILE = Path(parity.__file__)


@pytest.mark.acceptance
def test_production_baseline_equals_live_constants():
    """ATDD-OPIP-V6-GATE-PARITY-v1/AC-001: the production baseline is derived from the live gate constants."""
    c = gate_policy_constants()
    b = parity.production_baseline()
    assert b.score_threshold == c["min_technical_score"]
    assert b.min_confidence == pytest.approx(c["ai_min_confidence"] / 100.0)
    assert b.max_spread_pct == pytest.approx(c["max_long_spread_bps"] / 100.0)
    assert b.min_net_profit_usd == c["advisory_economic_min_net_profit"]
    assert b.min_target_2_move_pct == c["advisory_economic_min_target_2_move_pct"]
    assert b.min_reward_to_risk == c["advisory_economic_min_reward_to_risk"]


@pytest.mark.acceptance
def test_a_changed_production_constant_changes_the_baseline_and_digest():
    """ATDD-OPIP-V6-GATE-PARITY-v1/AC-002: threshold drift in production is detected by the baseline digest."""
    c = dict(gate_policy_constants())
    before = parity.production_baseline(c)
    c["min_technical_score"] = c["min_technical_score"] + 5
    after = parity.production_baseline(c)
    assert after.score_threshold == before.score_threshold + 5
    assert after.digest() != before.digest()


@pytest.mark.acceptance
def test_report_lists_unmodelled_gates_and_marks_approximations():
    """ATDD-OPIP-V6-GATE-PARITY-v1/AC-003: the report states what is not reproduced instead of implying full parity."""
    rep = parity.parity_report()
    assert "target_attainability" in rep["not_modelled_gates"]
    statuses = {f["sim_field"]: f["status"] for f in rep["fields"]}
    assert statuses["max_spread_pct"] == "LONG_ONLY_APPROX"
    assert statuses["round_trip_cost_pct"] == "NOT_BOUND_SIM_ASSUMPTION"
    assert "not a replay" in rep["notice"]


@pytest.mark.acceptance
def test_parity_module_is_read_only_and_does_not_import_execution_code():
    """ATDD-OPIP-V6-GATE-PARITY-v1/AC-004: parity reads thresholds only; no order, network, notification or scanner imports."""
    tree = ast.parse(PARITY_FILE.read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    banned = ("kraken", "requests", "httpx", "telegram", "scan_opportunities", "socket", "urllib")
    assert not [m for m in mods if any(b in m for b in banned)]
    assert gate_policy_constants() == gate_policy_constants()


@pytest.mark.acceptance
def test_unmapped_gate_change_moves_the_full_fingerprint_but_not_the_simulator_digest():
    """ATDD-OPIP-V6-GATE-PARITY-v1/AC-002: drift in a gate the simulator does not model is still detected, and provenance is not called live."""
    c = dict(gate_policy_constants())
    base_fp, base_sim = parity.full_policy_fingerprint(c), parity.production_baseline(c).digest()
    c["max_short_spread_bps"] = c["max_short_spread_bps"] + 1
    assert parity.full_policy_fingerprint(c) != base_fp
    assert parity.production_baseline(c).digest() == base_sim
    rep = parity.parity_report()
    assert rep["provenance"] == "CODE_CONSTANTS_V1_REFERENCE"
    assert "baseline_digest" not in rep and rep["simulator_digest"] and rep["full_policy_fingerprint"].startswith("PGC:")
