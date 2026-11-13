"""Private offline strategy simulator: deterministic, explicit reasons, isolated."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from app.core import strategy_policy
from app.opip.simulator import cli, engine
from app.opip.simulator.engine import SimPolicy

SIM_DIR = Path(engine.__file__).parent


def cand(**kw):
    base = {"symbol": "AAAUSD", "direction": "LONG", "entry": 100.0, "stop": 98.0, "target_2": 106.0,
            "v1_score": 90, "v2_score": 90.0, "confidence": 0.95, "liquidity_usd": 900_000,
            "spread_pct": 0.05, "est_slippage_pct": 0.05, "btc_regime": "UP"}
    base.update(kw)
    return base


@pytest.mark.acceptance
@pytest.mark.parametrize("policy,candidate,reason", [
    (SimPolicy(score_threshold=95), cand(), engine.SCORE_BELOW_THRESHOLD),
    (SimPolicy(min_confidence=0.99), cand(), engine.CONFIDENCE_BELOW_MIN),
    (SimPolicy(min_liquidity_usd=1_000_000), cand(), engine.LIQUIDITY_BELOW_MIN),
    (SimPolicy(max_spread_pct=0.01), cand(), engine.SPREAD_ABOVE_MAX),
    (SimPolicy(max_slippage_pct=0.01), cand(), engine.SLIPPAGE_ABOVE_MAX),
    (SimPolicy(min_target_2_move_pct=10), cand(), engine.TARGET_MOVE_BELOW_MIN),
    (SimPolicy(min_reward_to_risk=5), cand(), engine.REWARD_TO_RISK_BELOW_MIN),
    (SimPolicy(min_net_profit_usd=1000), cand(), engine.NET_PROFIT_BELOW_MIN),
    (SimPolicy(blocked_btc_regimes=("UP",)), cand(), engine.BACKDROP_BLOCKED),
])
def test_each_threshold_rejects_with_its_own_reason_code(policy, candidate, reason):
    """ATDD-OPIP-V6-SIMULATOR-v1/AC-001: every threshold is independently testable and yields an explicit reason code."""
    decision = engine.evaluate_candidate(candidate, policy)
    assert decision["status"] == engine.REJECTED
    assert decision["reasons"] == [reason]
    assert engine.evaluate_candidate(candidate, SimPolicy())["status"] == engine.QUALIFIED


@pytest.mark.acceptance
def test_early_watch_band_and_net_economics_are_explicit():
    """ATDD-OPIP-V6-SIMULATOR-v1/AC-001: the Early Watch liquidity band is configurable and costs are subtracted from the move."""
    policy = SimPolicy(min_liquidity_usd=600_000, early_watch_liquidity_usd=500_000)
    assert engine.evaluate_candidate(cand(liquidity_usd=550_000), policy)["status"] == engine.EARLY_WATCH
    assert engine.evaluate_candidate(cand(liquidity_usd=400_000), policy)["reasons"] == [engine.LIQUIDITY_BELOW_MIN]
    m = engine.evaluate_candidate(cand(), SimPolicy(round_trip_cost_pct=0.6))["metrics"]
    assert m["gross_move_pct"] == 6.0 and m["net_move_pct"] == pytest.approx(6.0 - 0.6 - 0.1)
    short = engine.evaluate_candidate(cand(direction="SHORT", entry=100.0, stop=102.0, target_2=94.0), SimPolicy())
    assert short["status"] == engine.QUALIFIED and short["metrics"]["reward_to_risk"] == 3.0


@pytest.mark.acceptance
@pytest.mark.parametrize("bad", [
    cand(entry=None), cand(direction="SIDEWAYS"), cand(stop=float("nan")), cand(target_2="x"),
    cand(entry=100.0, stop=101.0), cand(target_2=99.0), cand(entry=0.0),
])
def test_missing_or_invalid_data_fails_closed(bad):
    """ATDD-OPIP-V6-SIMULATOR-v1/AC-002: missing or invalid data and bad geometry are rejected, never qualified."""
    decision = engine.evaluate_candidate(bad, SimPolicy())
    assert decision["status"] == engine.REJECTED
    assert decision["reasons"][0].split(":")[0] in {engine.DATA_MISSING, engine.GEOMETRY_INVALID}


@pytest.mark.acceptance
def test_missing_score_for_the_selected_scorer_is_data_missing():
    """ATDD-OPIP-V6-SIMULATOR-v1/AC-002: a candidate without the selected scorer's score is rejected, not defaulted."""
    c = cand()
    del c["v2_score"]
    assert engine.evaluate_candidate(c, SimPolicy(scoring_version="v2"))["reasons"] == [f"{engine.DATA_MISSING}:v2_score"]
    assert engine.evaluate_candidate(c, SimPolicy(scoring_version="v1"))["status"] == engine.QUALIFIED


@pytest.mark.acceptance
def test_comparison_reports_funnel_changes_labels_and_digests():
    """ATDD-OPIP-V6-SIMULATOR-v1/AC-003: baseline and variant funnels, changed candidates, evidence label and policy digests are exported."""
    rows = cli.synthetic_candidates(200, seed=3)
    base = engine.baseline_from_strategy_policy(strategy_policy.load_strategy_policy())
    var = engine.with_overrides(base, {"min_confidence": 0.9, "min_liquidity_usd": 600_000})
    report = engine.compare_policies(rows, base, var, evidence=engine.EVIDENCE_SYNTHETIC)
    assert report["evidence_class"] == "SYNTHETIC" and "not a historical backtest" in report["notice"].lower()
    assert report["authority"] == {"orders": False, "production_settings_changed": False}
    assert report["baseline"]["policy_digest"] != report["variant"]["policy_digest"]
    assert sum(report["baseline"]["funnel"].values()) == 200 == sum(report["variant"]["funnel"].values())
    assert report["variant"]["funnel"]["QUALIFIED"] < report["baseline"]["funnel"]["QUALIFIED"]
    assert report["changed_candidates"] and set(report["changed_candidates"][0]) >= {"baseline", "variant", "variant_reasons"}
    assert report == engine.compare_policies(rows, base, var, evidence=engine.EVIDENCE_SYNTHETIC)
    with pytest.raises(ValueError):
        engine.compare_policies(rows, base, var, evidence="REAL_VERIFIED")


@pytest.mark.acceptance
def test_synthetic_data_is_deterministic_and_the_baseline_matches_the_reference_policy():
    """ATDD-OPIP-V6-SIMULATOR-v1/AC-003: the same seed gives the same data and the baseline derives from the validated reference policy."""
    assert cli.synthetic_candidates(50, 9) == cli.synthetic_candidates(50, 9) != cli.synthetic_candidates(50, 10)
    pol = strategy_policy.load_strategy_policy()
    base = engine.baseline_from_strategy_policy(pol)
    assert base.scoring_version == "v2" and base.score_threshold == pol.scoring["v2_threshold"]
    assert base.round_trip_cost_pct == pol.advisory_economics["estimated_round_trip_cost_pct"]


@pytest.mark.acceptance
def test_simulator_has_no_order_network_or_notification_dependencies():
    """ATDD-OPIP-V6-SIMULATOR-v1/AC-004: the simulator package imports no exchange, order, notification or network module."""
    forbidden = ("kraken", "order", "telegram", "requests", "httpx", "urllib", "socket", "openai",
                 "subprocess", "app.exchanges", "app.services", "app.jobs")
    for path in SIM_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name == f or name.startswith(f + ".") or f in name.split(".")[-1] for f in forbidden), (path.name, name)


@pytest.mark.acceptance
def test_cli_writes_only_the_report_and_never_changes_production_policy(tmp_path):
    """ATDD-OPIP-V6-SIMULATOR-v1/AC-004: the CLI writes only the requested report, rejects unknown parameters and leaves the policy file untouched."""
    policy_file = strategy_policy.DEFAULT_POLICY_PATH
    before = hashlib.sha256(policy_file.read_bytes()).hexdigest()
    var = tmp_path / "v.json"
    var.write_text(json.dumps({"min_confidence": 0.9}))
    out = tmp_path / "r.json"
    assert cli.main(["--synthetic", "40", "--seed", "2", "--variant", str(var), "--out", str(out)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["r.json", "v.json"]
    assert json.loads(out.read_text())["evidence_class"] == "SYNTHETIC"
    assert hashlib.sha256(policy_file.read_bytes()).hexdigest() == before
    var.write_text(json.dumps({"allow_funded_orders": True}))
    with pytest.raises(ValueError):
        cli.main(["--synthetic", "5", "--variant", str(var), "--out", str(tmp_path / "x.json")])
    data = tmp_path / "c.jsonl"
    data.write_text(json.dumps(cand()) + "\n")
    assert cli.main(["--candidates", str(data), "--out", str(tmp_path / "s.json")]) == 0
    assert json.loads((tmp_path / "s.json").read_text())["evidence_class"] == "SUPPLIED_UNVERIFIED"
