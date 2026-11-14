"""Parity between the simulator and the live production gate constants.

The production thresholds are owned by app.opip.decision.thresholds (which
imports them from the modules that own them). This module only READS them. It
builds a simulator baseline from the live constants and reports, field by
field, what the simulator models, models only approximately, or does not model,
so a divergence is visible instead of silent. It changes no production code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.opip.decision.thresholds import gate_policy_constants
from app.opip.simulator.engine import SimPolicy

PARITY_SCHEMA_VERSION = 2
# Provenance: these are module-level code constants (v1 reference). They are NOT
# proof of the deployed scorer or the effective runtime policy.
PROVENANCE = "CODE_CONSTANTS_V1_REFERENCE"


def full_policy_fingerprint(constants: dict[str, Any] | None = None) -> str:
    """Digest of EVERY production gate constant, including unmapped ones."""
    c = constants if constants is not None else gate_policy_constants()
    blob = json.dumps(c, sort_keys=True, separators=(",", ":"), default=str)
    return "PGC:" + hashlib.sha256(blob.encode()).hexdigest()[:16]

# Production gates that need a full scan snapshot and are NOT reproduced by the
# parametric simulator. Real-snapshot parity is the decision engine's job.
NOT_MODELLED_GATES = (
    "margin_tradeable",
    "execution_quality (book coverage, round-trip drag)",
    "target_attainability",
    "cross_market",
    "reference",
    "market_intelligence",
    "ai_invocation_and_result",
    "recommendation_gate (risk level, direction allow-list)",
)


def production_baseline(constants: dict[str, Any] | None = None) -> SimPolicy:
    """Code-constants v1 reference baseline (NOT proof of live behaviour, NOT the v2 target).

    Confidence is NOT an admission rule in production and is left disabled.
    Spread bps convert to percent.
    """
    c = constants if constants is not None else gate_policy_constants()
    return SimPolicy(
        scoring_version="v1",
        score_threshold=float(c["min_technical_score"]),
        # Confidence is measurement evidence in production (valid 0-100 required,
        # no admission floor: recommendation_gate.candidate_alert_authorized), so
        # the simulator filter stays disabled here.
        min_confidence=0.0,
        max_spread_pct=float(c["max_long_spread_bps"]) / 100.0,
        min_net_profit_usd=float(c["advisory_economic_min_net_profit"]),
        min_target_2_move_pct=float(c["advisory_economic_min_target_2_move_pct"]),
        min_reward_to_risk=float(c["advisory_economic_min_reward_to_risk"]),
    )


def parity_report(constants: dict[str, Any] | None = None) -> dict[str, Any]:
    c = constants if constants is not None else gate_policy_constants()
    base = production_baseline(c)
    rows = [
        ("score_threshold", "min_technical_score", base.score_threshold, "EXACT"),
        ("min_confidence", "ai_min_confidence", base.min_confidence, "MEASUREMENT_ONLY_NOT_ADMISSION"),
        ("max_spread_pct", "max_long_spread_bps", base.max_spread_pct, "LONG_ONLY_APPROX"),
        ("min_net_profit_usd", "advisory_economic_min_net_profit", base.min_net_profit_usd, "EXACT"),
        ("min_target_2_move_pct", "advisory_economic_min_target_2_move_pct", base.min_target_2_move_pct, "EXACT"),
        ("min_reward_to_risk", "advisory_economic_min_reward_to_risk", base.min_reward_to_risk, "EXACT"),
        ("round_trip_cost_pct", "max_long_round_trip_drag_pct", base.round_trip_cost_pct, "NOT_BOUND_SIM_ASSUMPTION"),
    ]
    return {
        "schema_version": PARITY_SCHEMA_VERSION,
        "policy_fingerprint_inputs": {k: c[k] for k in sorted(c)},
        "fields": [
            {"sim_field": s, "production_key": p, "sim_value": v, "status": st}
            for s, p, v, st in rows
        ],
        "not_modelled_gates": list(NOT_MODELLED_GATES),
        "provenance": PROVENANCE,
        "full_policy_fingerprint": full_policy_fingerprint(c),
        "simulator_digest": base.digest(),
        "notice": (
            "Parametric approximation of production gate CONSTANTS (v1 reference); "
            "not a replay of production decisions and not proof of the deployed "
            "scorer or effective runtime policy. A change to an unmapped gate "
            "changes full_policy_fingerprint but not simulator_digest."
        ),
    }
