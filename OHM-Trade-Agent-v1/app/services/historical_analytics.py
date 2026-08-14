from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Iterable

from app.services.trade_outcome_registry import get_outcomes


MIN_SEGMENT_SAMPLES = 5


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _resolved(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r.get("entered_trade") and r.get("terminal_status")]


def _bucket_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    net = [_num(r.get("net_pnl")) for r in rows]
    net = [v for v in net if v is not None]
    wins = [v for v in net if v > 0]
    losses = [v for v in net if v < 0]
    return {
        "count": len(rows),
        "financial_samples": len(net),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(net) * 100, 2) if net else None,
        "net_pnl": round(sum(net), 8) if net else None,
        "avg_net_pnl": round(mean(net), 8) if net else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else (None if not wins else float("inf")),
        "expectancy": round(mean(net), 8) if net else None,
        "sufficient_samples": len(net) >= MIN_SEGMENT_SAMPLES,
        "t1_rate_pct": round(sum(bool(r.get("target_1_observed")) for r in rows) / len(rows) * 100, 2) if rows else None,
        "t2_rate_pct": round(sum(bool(r.get("target_2_observed")) for r in rows) / len(rows) * 100, 2) if rows else None,
        "avg_mfe_pct": round(mean(v for r in rows if (v := _num(r.get("mfe_pct"))) is not None), 4) if any(_num(r.get("mfe_pct")) is not None for r in rows) else None,
        "avg_mae_pct": round(mean(v for r in rows if (v := _num(r.get("mae_pct"))) is not None), 4) if any(_num(r.get("mae_pct")) is not None for r in rows) else None,
    }


def historical_summary(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = _resolved(records if records is not None else get_outcomes())
    by_direction: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_setup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_regime_setup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        direction = str(row.get("direction") or "UNKNOWN").upper()
        regime = str(row.get("market_regime") or "UNKNOWN").upper()
        setup = str(row.get("setup_type") or row.get("signal_type") or "UNKNOWN").upper()
        by_direction[direction].append(row)
        by_regime[regime].append(row)
        by_setup[setup].append(row)
        by_regime_setup[f"{regime}|{setup}"].append(row)
    return {
        "status": "OK" if rows else "NO_RESOLVED_TRADES",
        "resolved_entered_trades": len(rows),
        "overall": _bucket_summary(rows),
        "by_direction": {k: _bucket_summary(v) for k, v in sorted(by_direction.items())},
        "by_regime": {k: _bucket_summary(v) for k, v in sorted(by_regime.items())},
        "by_setup": {k: _bucket_summary(v) for k, v in sorted(by_setup.items())},
        "by_regime_setup": {k: _bucket_summary(v) for k, v in sorted(by_regime_setup.items())},
        "minimum_segment_samples": MIN_SEGMENT_SAMPLES,
    }
