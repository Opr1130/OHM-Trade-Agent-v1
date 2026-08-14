from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DrawdownPolicy:
    soft_limit_pct: float = 5.0
    hard_limit_pct: float = 8.0
    soft_size_multiplier: float = 0.50


@dataclass(frozen=True)
class DrawdownDecision:
    state: str
    drawdown_pct: float
    size_multiplier: float
    allow_new_entries: bool
    reason: str


def evaluate_drawdown_guard(
    *, current_equity: float, peak_equity: float, policy: DrawdownPolicy = DrawdownPolicy()
) -> DrawdownDecision:
    """Apply portfolio-level loss containment independently of signal quality."""
    if current_equity < 0 or peak_equity <= 0:
        raise ValueError("equity values must be valid and peak_equity must be positive")
    if policy.soft_limit_pct < 0 or policy.hard_limit_pct <= policy.soft_limit_pct:
        raise ValueError("hard drawdown limit must be greater than soft limit")
    if not 0 < policy.soft_size_multiplier <= 1:
        raise ValueError("soft_size_multiplier must be in (0, 1]")

    drawdown = max(0.0, (peak_equity - current_equity) / peak_equity * 100.0)
    if drawdown >= policy.hard_limit_pct:
        return DrawdownDecision(
            state="HALT",
            drawdown_pct=round(drawdown, 4),
            size_multiplier=0.0,
            allow_new_entries=False,
            reason="hard_drawdown_limit_reached",
        )
    if drawdown >= policy.soft_limit_pct:
        return DrawdownDecision(
            state="REDUCED_RISK",
            drawdown_pct=round(drawdown, 4),
            size_multiplier=policy.soft_size_multiplier,
            allow_new_entries=True,
            reason="soft_drawdown_limit_reached",
        )
    return DrawdownDecision(
        state="NORMAL",
        drawdown_pct=round(drawdown, 4),
        size_multiplier=1.0,
        allow_new_entries=True,
        reason="within_drawdown_budget",
    )
