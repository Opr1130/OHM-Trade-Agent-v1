from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.active_trade_registry import ActiveTrade
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.registry_io import load_json, registry_lock, save_json_atomic


OUTCOME_FILE = Path("/app/data/trade_outcomes.json")
SCHEMA_VERSION = 2


def registry_lock_file() -> Path:
    return OUTCOME_FILE.parent / ".trade_outcomes.lock"


def _load_raw() -> dict[str, dict[str, Any]]:
    return load_json(OUTCOME_FILE)


def _save_raw(data: dict[str, dict[str, Any]]) -> None:
    save_json_atomic(OUTCOME_FILE, data)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _legacy_key(symbol: str, timestamp: str | None = None) -> str:
    return f"legacy:{symbol.upper()}:{timestamp or 'unknown'}"


def _record_key(*, trade_id: str | None, symbol: str, timestamp: str | None = None) -> str:
    return trade_id or _legacy_key(symbol, timestamp)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elapsed_seconds(start: str | None, end: str | None) -> float | None:
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def _find_key(
    data: dict[str, dict[str, Any]], *, trade_id: str | None, symbol: str
) -> str | None:
    if trade_id and trade_id in data:
        return trade_id
    for key, item in data.items():
        if trade_id and item.get("trade_id") == trade_id:
            return key
        if not trade_id and item.get("symbol") == symbol.upper() and not item.get("terminal_status"):
            return key
    return None


def record_recommendation(
    *,
    trade_id: str | None,
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    action: str,
) -> dict[str, Any]:
    symbol = plan.symbol.upper()
    recommended_at = _now()
    key = _record_key(trade_id=trade_id, symbol=symbol, timestamp=recommended_at)
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        existing = data.get(key)
        if existing is not None:
            return existing
        direction = (candidate.get("direction") or getattr(plan, "direction", "LONG") or "LONG").upper()
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "trade_id": trade_id or "",
            "record_key": key,
            "symbol": symbol,
            "direction": direction,
            "margin_leverage": float(candidate.get("margin_leverage") or (2.0 if direction == "SHORT" else 1.0)),
            "underlying_asset": candidate.get("underlying_asset") or symbol,
            "primary_pair": candidate.get("primary_pair") or symbol,
            "recommendation_timestamp": recommended_at,
            "recommendation_action": action,
            "chief_confidence": int(candidate.get("confidence", 0)),
            "chief_confidence_semantics": "comparative_review_confidence_not_probability",
            "chief_decision": candidate.get("decision"),
            "risk_level": plan.risk_level,
            "technical_score": candidate.get("technical_score"),
            "target_attainability_score": candidate.get("target_attainability_score"),
            "profit_rank": candidate.get("profit_rank"),
            "profit_rank_score": candidate.get("profit_rank_score"),
            "planned_entry_low": plan.entry_low,
            "planned_entry_high": plan.entry_high,
            "planned_chase_limit": plan.chase_limit,
            "planned_stop": plan.stop_price,
            "planned_target_1": plan.target_1,
            "planned_target_2": plan.target_2,
            "planned_reward_to_risk_1": plan.reward_to_risk_1,
            "planned_reward_to_risk_2": plan.reward_to_risk_2,
            "target_quality_qualified": candidate.get("target_quality_qualified"),
            "economic_qualified": candidate.get("economic_qualified"),
            "economic_target_2_move_pct": candidate.get("economic_target_2_move_pct"),
            "economic_validation_net_t2": candidate.get("economic_validation_net_t2"),
            "market_regime": candidate.get("market_regime"),
            "entered_trade": False,
            "setup_status": "recommended",
            "entered_at": None,
            "entry_price": None,
            "entry_price_source": None,
            "last_observed_at": None,
            "last_observed_price": None,
            "maximum_favorable_price": None,
            "maximum_adverse_price": None,
            "mfe_pct": None,
            "mae_pct": None,
            "target_1_observed": False,
            "target_1_first_observed_at": None,
            "target_2_observed": False,
            "target_2_first_observed_at": None,
            "stop_observed": False,
            "stop_first_observed_at": None,
            "observation_warnings": [],
            "terminal_status": None,
            "terminal_timestamp": None,
            "terminal_reason": None,
            "elapsed_from_recommendation_seconds": None,
            "elapsed_from_entry_seconds": None,
            "final_observed_price": None,
        }
        data[key] = record
        _save_raw(data)
        return record


def mark_trade_entered(trade: ActiveTrade, *, entry_price_source: str) -> dict[str, Any]:
    now = trade.opened_at or _now()
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        key = _find_key(data, trade_id=trade.trade_id or None, symbol=trade.symbol)
        if key is None:
            key = _record_key(
                trade_id=trade.trade_id or None,
                symbol=trade.symbol,
                timestamp=trade.opened_at or "unknown",
            )
            data[key] = {
                "schema_version": SCHEMA_VERSION,
                "trade_id": trade.trade_id,
                "record_key": key,
                "symbol": trade.symbol.upper(),
                "direction": (trade.direction or "LONG").upper(),
                "margin_leverage": trade.margin_leverage,
                "recommendation_timestamp": None,
                "recommendation_action": "LEGACY_ACTIVE",
                "chief_confidence": None,
                "chief_confidence_semantics": "comparative_review_confidence_not_probability",
                "profit_rank": None,
                "profit_rank_score": None,
                "observation_warnings": ["Legacy active trade had no captured recommendation snapshot"],
                "terminal_status": None,
            }
        item = data[key]
        item.setdefault("direction", (trade.direction or "LONG").upper())
        item.setdefault("margin_leverage", trade.margin_leverage)
        item["entered_trade"] = True
        item["setup_status"] = "entered"
        item["entered_at"] = item.get("entered_at") or now
        item["entry_price"] = trade.entry_price
        item["entry_price_source"] = item.get("entry_price_source") or entry_price_source
        item["maximum_favorable_price"] = item.get("maximum_favorable_price") or trade.entry_price
        item["maximum_adverse_price"] = item.get("maximum_adverse_price") or trade.entry_price
        item["mfe_pct"] = item.get("mfe_pct") if item.get("mfe_pct") is not None else 0.0
        item["mae_pct"] = item.get("mae_pct") if item.get("mae_pct") is not None else 0.0
        for field, default in (
            ("target_1_observed", False), ("target_1_first_observed_at", None),
            ("target_2_observed", False), ("target_2_first_observed_at", None),
            ("stop_observed", False), ("stop_first_observed_at", None),
            ("last_observed_at", None), ("last_observed_price", None),
            ("final_observed_price", None),
        ):
            item.setdefault(field, default)
        _save_raw(data)
        return item


def update_active_observation(
    trade: ActiveTrade,
    current_price: float,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed_at = observed_at or _now()
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        key = _find_key(data, trade_id=trade.trade_id or None, symbol=trade.symbol)
        if key is None:
            key = _record_key(trade_id=None, symbol=trade.symbol, timestamp=trade.opened_at or "unknown")
            data[key] = {
                "schema_version": SCHEMA_VERSION,
                "trade_id": "",
                "record_key": key,
                "symbol": trade.symbol.upper(),
                "direction": (trade.direction or "LONG").upper(),
                "margin_leverage": trade.margin_leverage,
                "recommendation_timestamp": None,
                "recommendation_action": "LEGACY_ACTIVE",
                "chief_confidence": None,
                "chief_confidence_semantics": "comparative_review_confidence_not_probability",
                "profit_rank": None,
                "profit_rank_score": None,
                "entered_trade": True,
                "entered_at": trade.opened_at or None,
                "entry_price": trade.entry_price,
                "entry_price_source": "legacy_registry",
                "observation_warnings": ["Legacy active trade had no captured recommendation snapshot"],
                "terminal_status": None,
            }
        item = data[key]
        direction = str(item.get("direction") or trade.direction or "LONG").upper()
        item["direction"] = direction
        item["entered_trade"] = True
        item["entry_price"] = item.get("entry_price") or trade.entry_price
        entry = float(item["entry_price"])
        prev_fav = float(item.get("maximum_favorable_price") or entry)
        prev_adv = float(item.get("maximum_adverse_price") or entry)

        if direction == "SHORT":
            favorable = min(prev_fav, current_price)
            adverse = max(prev_adv, current_price)
            mfe_pct = max(0.0, (entry - favorable) / entry * 100)
            mae_pct = max(0.0, (adverse - entry) / entry * 100)
            hit_t1 = current_price <= trade.target_1
            hit_t2 = current_price <= trade.target_2
            hit_stop = current_price >= trade.stop_price
        else:
            favorable = max(prev_fav, current_price)
            adverse = min(prev_adv, current_price)
            mfe_pct = max(0.0, (favorable - entry) / entry * 100)
            mae_pct = max(0.0, (entry - adverse) / entry * 100)
            hit_t1 = current_price >= trade.target_1
            hit_t2 = current_price >= trade.target_2
            hit_stop = current_price <= trade.stop_price

        item["maximum_favorable_price"] = favorable
        item["maximum_adverse_price"] = adverse
        item["mfe_pct"] = round(mfe_pct, 4)
        item["mae_pct"] = round(mae_pct, 4)
        item["last_observed_at"] = observed_at
        item["last_observed_price"] = current_price

        if hit_t1 and not item.get("target_1_observed"):
            item["target_1_observed"] = True
            item["target_1_first_observed_at"] = observed_at
        if hit_t2 and not item.get("target_2_observed"):
            item["target_2_observed"] = True
            item["target_2_first_observed_at"] = observed_at
        if hit_stop and not item.get("stop_observed"):
            item["stop_observed"] = True
            item["stop_first_observed_at"] = observed_at
        if hit_t1 and hit_t2:
            warnings = item.setdefault("observation_warnings", [])
            warning = (
                "One sampled observation was already beyond both T1 and T2; "
                "intra-observation crossing order was not inferred"
            )
            if warning not in warnings:
                warnings.append(warning)
        _save_raw(data)
        return item


def terminalize_setup_outcome(trade_id: str, status: str) -> bool:
    terminal_at = _now()
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        key = _find_key(data, trade_id=trade_id, symbol="")
        if key is None:
            return False
        item = data[key]
        if item.get("terminal_status"):
            return True
        item["setup_status"] = status
        item["terminal_status"] = status
        item["terminal_timestamp"] = terminal_at
        item["terminal_reason"] = status
        item["entered_trade"] = False
        item["elapsed_from_recommendation_seconds"] = _elapsed_seconds(item.get("recommendation_timestamp"), terminal_at)
        _save_raw(data)
        return True


def terminalize_active_outcome(
    *,
    trade_id: str | None,
    symbol: str,
    status: str,
    reason: str,
    final_price: float | None = None,
) -> bool:
    terminal_at = _now()
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        key = _find_key(data, trade_id=trade_id, symbol=symbol)
        if key is None:
            return False
        item = data[key]
        if item.get("terminal_status"):
            return True
        item["terminal_status"] = status
        item["terminal_timestamp"] = terminal_at
        item["terminal_reason"] = reason
        item["entered_trade"] = True
        item["final_observed_price"] = final_price if final_price is not None else item.get("last_observed_price")
        item["elapsed_from_recommendation_seconds"] = _elapsed_seconds(item.get("recommendation_timestamp"), terminal_at)
        item["elapsed_from_entry_seconds"] = _elapsed_seconds(item.get("entered_at"), terminal_at)
        _save_raw(data)
        return True


def get_outcomes() -> list[dict[str, Any]]:
    with registry_lock(registry_lock_file()):
        return list(_load_raw().values())


def calibration_summary(min_resolved_entered: int = 30) -> dict[str, Any]:
    outcomes = get_outcomes()
    resolved_entered = [
        item for item in outcomes if item.get("entered_trade") and item.get("terminal_status")
    ]
    if len(resolved_entered) < min_resolved_entered:
        return {
            "status": "INSUFFICIENT_DATA",
            "resolved_entered_trades": len(resolved_entered),
            "minimum_required": min_resolved_entered,
            "confidence_is_probability": False,
        }

    confidence_bins: dict[str, dict[str, int]] = {}
    rank_bins: dict[str, dict[str, int]] = {}
    direction_bins: dict[str, dict[str, int]] = {}
    for item in resolved_entered:
        confidence = item.get("chief_confidence")
        if isinstance(confidence, (int, float)):
            low = int(confidence // 10) * 10
            label = f"{low:02d}-{min(low + 9, 100):02d}"
            bucket = confidence_bins.setdefault(label, {"count": 0, "t2_observed": 0})
            bucket["count"] += 1
            bucket["t2_observed"] += int(bool(item.get("target_2_observed")))
        rank = item.get("profit_rank")
        if isinstance(rank, int):
            bucket = rank_bins.setdefault(str(rank), {"count": 0, "t2_observed": 0})
            bucket["count"] += 1
            bucket["t2_observed"] += int(bool(item.get("target_2_observed")))
        direction = str(item.get("direction") or "LONG").upper()
        bucket = direction_bins.setdefault(direction, {"count": 0, "t2_observed": 0})
        bucket["count"] += 1
        bucket["t2_observed"] += int(bool(item.get("target_2_observed")))

    return {
        "status": "AVAILABLE",
        "resolved_entered_trades": len(resolved_entered),
        "confidence_is_probability": False,
        "confidence_bins": confidence_bins,
        "profit_rank_bins": rank_bins,
        "direction_bins": direction_bins,
    }
