"""Deterministic policy evaluation for offline what-if comparison.

Pure functions only: no network, no exchange client, no order or notification
code, no writes. Every decision carries explicit reason codes. Missing data
fails closed. Results are SIMULATION evidence, never a profitability claim.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping

from app.core.strategy_policy import StrategyPolicy

SIM_SCHEMA_VERSION = 1
QUALIFIED, EARLY_WATCH, REJECTED = "QUALIFIED", "EARLY_WATCH", "REJECTED"
EVIDENCE_SYNTHETIC = "SYNTHETIC"
EVIDENCE_SUPPLIED = "SUPPLIED_UNVERIFIED"

# Rejection reason codes (stable identifiers).
SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"
CONFIDENCE_BELOW_MIN = "CONFIDENCE_BELOW_MIN"
LIQUIDITY_BELOW_MIN = "LIQUIDITY_BELOW_MIN"
SPREAD_ABOVE_MAX = "SPREAD_ABOVE_MAX"
SLIPPAGE_ABOVE_MAX = "SLIPPAGE_ABOVE_MAX"
GEOMETRY_INVALID = "GEOMETRY_INVALID"
TARGET_MOVE_BELOW_MIN = "TARGET_MOVE_BELOW_MIN"
REWARD_TO_RISK_BELOW_MIN = "REWARD_TO_RISK_BELOW_MIN"
NET_PROFIT_BELOW_MIN = "NET_PROFIT_BELOW_MIN"
BACKDROP_BLOCKED = "BACKDROP_BLOCKED"
DATA_MISSING = "DATA_MISSING"

_REQUIRED = ("symbol", "direction", "entry", "stop", "target_2")


@dataclass(frozen=True)
class SimPolicy:
    """Tunable simulator parameters. NOT a production policy and never applied to one."""
    scoring_version: str = "v2"
    score_threshold: float = 75.0
    min_confidence: float = 0.0
    min_liquidity_usd: float = 0.0
    early_watch_liquidity_usd: float | None = None
    max_spread_pct: float = 100.0
    max_slippage_pct: float = 100.0
    round_trip_cost_pct: float = 0.6
    min_net_profit_usd: float = 0.0
    min_target_2_move_pct: float = 0.0
    min_reward_to_risk: float = 0.0
    notional_usd: float = 1000.0
    blocked_btc_regimes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_policy(self)

    def digest(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _validate_policy(p: "SimPolicy") -> None:
    """Reject nonphysical or guard-disabling parameters with a stable reason."""
    def bad(name: str) -> None:
        raise ValueError(f"INVALID_SIM_PARAMETER:{name}")

    if p.scoring_version not in ("v1", "v2"):
        bad("scoring_version")
    ranges = {
        "score_threshold": (0.0, 100.0), "min_confidence": (0.0, 1.0),
        "min_liquidity_usd": (0.0, 1e12), "max_spread_pct": (0.0, 100.0),
        "max_slippage_pct": (0.0, 100.0), "round_trip_cost_pct": (0.0, 100.0),
        "min_net_profit_usd": (0.0, 1e12), "min_target_2_move_pct": (0.0, 1e4),
        "min_reward_to_risk": (0.0, 1e4),
    }
    for name, (lo, hi) in ranges.items():
        value = getattr(p, name)
        if not _finite(value) or not lo <= value <= hi:
            bad(name)
    if not _finite(p.notional_usd) or not 0.0 < p.notional_usd <= 1e12:
        bad("notional_usd")
    ew = p.early_watch_liquidity_usd
    if ew is not None and (not _finite(ew) or not 0.0 <= ew <= p.min_liquidity_usd):
        bad("early_watch_liquidity_usd")
    regimes = p.blocked_btc_regimes
    if not isinstance(regimes, tuple) or not all(isinstance(r, str) and r.strip() for r in regimes):
        bad("blocked_btc_regimes")


def baseline_from_strategy_policy(policy: StrategyPolicy) -> SimPolicy:
    """Start from the validated reference policy so the baseline matches production intent."""
    s, e = policy.scoring, policy.advisory_economics
    return SimPolicy(
        scoring_version=str(s["target_production"]),
        score_threshold=float(s["v2_threshold"] if s["target_production"] == "v2" else s["v1_threshold"]),
        round_trip_cost_pct=float(e["estimated_round_trip_cost_pct"]),
        min_net_profit_usd=float(e["min_net_profit_usd"]),
        min_target_2_move_pct=float(e["min_target_2_move_pct"]),
        min_reward_to_risk=float(e["min_reward_to_risk"]),
    )


def with_overrides(base: SimPolicy, overrides: Mapping[str, Any]) -> SimPolicy:
    allowed = set(asdict(base))
    unknown = set(overrides) - allowed
    if unknown:
        raise ValueError(f"UNKNOWN_SIM_PARAMETER:{sorted(unknown)}")
    values = dict(overrides)
    if "blocked_btc_regimes" in values:
        regimes = values["blocked_btc_regimes"]
        if not isinstance(regimes, (list, tuple)):
            raise ValueError("INVALID_SIM_PARAMETER:blocked_btc_regimes")
        values["blocked_btc_regimes"] = tuple(regimes)
    return replace(base, **values)  # __post_init__ validates every field


def _num(candidate: Mapping[str, Any], key: str) -> float | None:
    value = candidate.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def evaluate_candidate(candidate: Mapping[str, Any], policy: SimPolicy) -> dict[str, Any]:
    """Return a decision: status, ordered reason codes and derived economics."""
    reasons: list[str] = []
    entry, stop, t2 = _num(candidate, "entry"), _num(candidate, "stop"), _num(candidate, "target_2")
    direction = str(candidate.get("direction") or "").upper()
    missing = [k for k in _REQUIRED if candidate.get(k) in (None, "")]
    if missing or entry is None or stop is None or t2 is None or direction not in ("LONG", "SHORT"):
        return _decision(candidate, REJECTED, [f"{DATA_MISSING}:{','.join(missing) or 'invalid_numeric_or_direction'}"], {})

    sign = 1.0 if direction == "LONG" else -1.0
    reward = sign * (t2 - entry)
    risk = sign * (entry - stop)
    if entry <= 0 or risk <= 0 or reward <= 0:
        return _decision(candidate, REJECTED, [GEOMETRY_INVALID], {})

    raw_slip = candidate.get("est_slippage_pct")
    slippage_missing = raw_slip is None
    if not slippage_missing and (not _finite(raw_slip) or raw_slip < 0):
        return _decision(candidate, REJECTED, [f"{DATA_MISSING}:est_slippage_pct_invalid"], {})
    slippage = 0.0 if slippage_missing else float(raw_slip)
    gross_pct = reward / entry * 100.0
    net_pct = gross_pct - policy.round_trip_cost_pct - 2.0 * slippage
    metrics = {
        "gross_move_pct": round(gross_pct, 6),
        "net_move_pct": round(net_pct, 6),
        "net_profit_usd": round(policy.notional_usd * net_pct / 100.0, 6),
        "reward_to_risk": round(reward / risk, 6),
        # Costs: round_trip_cost_pct covers fees and spread for the full round
        # trip; slippage is per side (entry and exit), hence 2x.
        "slippage_assumed_zero": slippage_missing,
    }

    score = _num(candidate, "v2_score" if policy.scoring_version == "v2" else "v1_score")
    if score is None:
        reasons.append(f"{DATA_MISSING}:{policy.scoring_version}_score")
    elif score < policy.score_threshold:
        reasons.append(SCORE_BELOW_THRESHOLD)
    confidence = _num(candidate, "confidence")
    if policy.min_confidence > 0 and (confidence is None or confidence < policy.min_confidence):
        reasons.append(CONFIDENCE_BELOW_MIN)
    if policy.max_spread_pct < 100 and (_num(candidate, "spread_pct") is None or _num(candidate, "spread_pct") > policy.max_spread_pct):
        reasons.append(SPREAD_ABOVE_MAX)
    if policy.max_slippage_pct < 100 and (slippage_missing or slippage > policy.max_slippage_pct):
        reasons.append(SLIPPAGE_ABOVE_MAX)
    if gross_pct < policy.min_target_2_move_pct:
        reasons.append(TARGET_MOVE_BELOW_MIN)
    if metrics["reward_to_risk"] < policy.min_reward_to_risk:
        reasons.append(REWARD_TO_RISK_BELOW_MIN)
    if metrics["net_profit_usd"] < policy.min_net_profit_usd:
        reasons.append(NET_PROFIT_BELOW_MIN)
    if str(candidate.get("btc_regime") or "") in policy.blocked_btc_regimes:
        reasons.append(BACKDROP_BLOCKED)

    liquidity = _num(candidate, "liquidity_usd")
    liquidity_low = policy.min_liquidity_usd > 0 and (liquidity is None or liquidity < policy.min_liquidity_usd)
    status = QUALIFIED
    if liquidity_low:
        ew = policy.early_watch_liquidity_usd
        if ew is not None and liquidity is not None and liquidity >= ew and not reasons:
            status = EARLY_WATCH
        else:
            reasons.append(LIQUIDITY_BELOW_MIN)
    if reasons:
        status = REJECTED
    return _decision(candidate, status, reasons, metrics)


def _decision(candidate, status, reasons, metrics) -> dict[str, Any]:
    return {
        "symbol": candidate.get("symbol"),
        "direction": str(candidate.get("direction") or "").upper() or None,
        "status": status,
        "reasons": list(reasons),
        "metrics": metrics,
    }


def run_policy(candidates: Iterable[Mapping[str, Any]], policy: SimPolicy) -> dict[str, Any]:
    decisions = [evaluate_candidate(c, policy) for c in candidates]
    funnel = {QUALIFIED: 0, EARLY_WATCH: 0, REJECTED: 0}
    reason_counts: dict[str, int] = {}
    for d in decisions:
        funnel[d["status"]] += 1
        for r in d["reasons"]:
            key = r.split(":")[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1
    return {"policy_digest": policy.digest(), "policy": asdict(policy), "total": len(decisions),
            "funnel": funnel, "reason_counts": dict(sorted(reason_counts.items())), "decisions": decisions}


def compare_policies(candidates: list[Mapping[str, Any]], baseline: SimPolicy, variant: SimPolicy,
                     *, evidence: str) -> dict[str, Any]:
    if evidence not in (EVIDENCE_SYNTHETIC, EVIDENCE_SUPPLIED):
        raise ValueError("INVALID_EVIDENCE_LABEL")
    base, var = run_policy(candidates, baseline), run_policy(candidates, variant)
    changes = []
    for b, v in zip(base["decisions"], var["decisions"]):
        if (b["status"], b["reasons"]) != (v["status"], v["reasons"]):
            changes.append({"symbol": b["symbol"], "direction": b["direction"],
                            "baseline": b["status"], "variant": v["status"],
                            "baseline_reasons": b["reasons"], "variant_reasons": v["reasons"]})
    return {
        "schema_version": SIM_SCHEMA_VERSION,
        "evidence_class": evidence,
        "notice": ("SIMULATION OUTPUT. Not a historical backtest, not forward paper results and not "
                   "realized trades. It does not show that either policy is profitable."),
        "authority": {"orders": False, "production_settings_changed": False},
        "candidate_count": len(candidates),
        "baseline": {k: base[k] for k in ("policy_digest", "policy", "funnel", "reason_counts")},
        "variant": {k: var[k] for k in ("policy_digest", "policy", "funnel", "reason_counts")},
        "changed_candidates": changes,
        "decisions": {"baseline": base["decisions"], "variant": var["decisions"]},
    }
