"""Versioned strategy policy proposal, never an execution permission source.

The application retains existing V1 production policy until a separate reviewed
cutover wires an explicit, readiness-verified selection of the V2 evaluator.
A UI must not treat this file as an authorization to trade.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

DEFAULT_POLICY_PATH = Path(__file__).with_name("strategy_policy.json")
EXPECTED_ROOT = {
    "schema_version", "profile_id", "scoring",
    "advisory_economics", "ai_advisory", "protection",
}


@dataclass(frozen=True)
class StrategyPolicy:
    profile_id: str
    digest: str
    scoring: dict
    advisory_economics: dict
    ai_advisory: dict


def load_strategy_policy(path: Path | None = None) -> StrategyPolicy:
    source = (path or DEFAULT_POLICY_PATH).read_bytes()
    doc = json.loads(source)
    if not isinstance(doc, dict) or set(doc) != EXPECTED_ROOT or doc["schema_version"] != 1:
        raise ValueError("INVALID_STRATEGY_POLICY_SCHEMA")
    if not isinstance(doc["profile_id"], str) or not 1 <= len(doc["profile_id"]) <= 100:
        raise ValueError("INVALID_STRATEGY_POLICY_ID")
    s, e, a, p = (doc[key] for key in ("scoring", "advisory_economics", "ai_advisory", "protection"))
    if not all(isinstance(item, dict) for item in (s, e, a, p)):
        raise ValueError("INVALID_STRATEGY_POLICY_SECTION")
    if set(s) != {"production", "target_production", "shadow", "v1_threshold", "v2_threshold", "max_candidates", "max_per_direction"}:
        raise ValueError("INVALID_SCORING_POLICY")
    if (s["production"], s["target_production"], s["shadow"]) != ("v1", "v2", "v1"):
        raise ValueError("PRODUCTION_CUTOVER_NOT_AUTHORIZED")
    for key in ("v1_threshold", "v2_threshold", "max_candidates", "max_per_direction"):
        if type(s[key]) is not int or not 0 < s[key] <= 100:
            raise ValueError("INVALID_SCORING_BOUND")
    if s["max_candidates"] != 8 or s["max_per_direction"] != 5 or s["v1_threshold"] != 80:
        raise ValueError("PRODUCTION_CAP_DRIFT")
    if set(e) != {"min_net_profit_usd", "min_target_2_move_pct", "min_reward_to_risk", "estimated_round_trip_cost_pct"}:
        raise ValueError("INVALID_ECONOMIC_POLICY")
    for key, value in e.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("INVALID_ECONOMIC_BOUND")
    if set(a) != {"potential_move_scenarios", "mode"} or type(a["potential_move_scenarios"]) is not bool or a["mode"] != "advisory_only":
        raise ValueError("AI_ADVISORY_AUTHORITY_DRIFT")
    if set(p) != {"allow_funded_orders", "allow_auto_trading_activation"} or p != {
        "allow_funded_orders": False, "allow_auto_trading_activation": False
    }:
        raise ValueError("TRADING_PERMISSION_CONFIG_FORBIDDEN")
    return StrategyPolicy(
        profile_id=doc["profile_id"],
        digest=hashlib.sha256(source).hexdigest(),
        scoring=dict(s),
        advisory_economics=dict(e),
        ai_advisory=dict(a),
    )
