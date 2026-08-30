"""Lightweight resource sampling and guard semantics for BUILD 4.2."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path


@dataclass(frozen=True)
class ResourceGuardConfig:
    memory_soft_limit_bytes: int = 150 * 1024 * 1024
    loop_lag_soft_limit_seconds: float = 0.25
    queue_utilization_soft_limit_pct: float = 90.0

    def __post_init__(self) -> None:
        if int(self.memory_soft_limit_bytes) <= 0:
            raise ValueError("memory_soft_limit_bytes must be positive")
        if (
            not math.isfinite(float(self.loop_lag_soft_limit_seconds))
            or self.loop_lag_soft_limit_seconds < 0
        ):
            raise ValueError("loop lag limit must be finite and non-negative")
        if (
            not math.isfinite(float(self.queue_utilization_soft_limit_pct))
            or not 0 <= self.queue_utilization_soft_limit_pct <= 100
        ):
            raise ValueError("queue utilization limit must be in [0, 100]")


@dataclass(frozen=True)
class ResourceAssessment:
    rss_bytes: int | None
    loop_lag_seconds: float
    queue_utilization_pct: float
    degraded: bool
    reasons: tuple[str, ...]


def current_rss_bytes() -> int | None:
    """Best-effort current RSS on Linux without adding a dependency."""
    status = Path(f"/proc/{os.getpid()}/status")
    try:
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def assess_resources(
    *,
    config: ResourceGuardConfig,
    rss_bytes: int | None,
    loop_lag_seconds: float,
    queue_utilization_pct: float,
) -> ResourceAssessment:
    if not math.isfinite(float(loop_lag_seconds)) or loop_lag_seconds < 0:
        raise ValueError("loop_lag_seconds must be finite and non-negative")
    if (
        not math.isfinite(float(queue_utilization_pct))
        or not 0 <= queue_utilization_pct <= 100
    ):
        raise ValueError("queue_utilization_pct must be in [0, 100]")
    reasons: list[str] = []
    if rss_bytes is not None and rss_bytes > config.memory_soft_limit_bytes:
        reasons.append("MEMORY_SOFT_LIMIT_EXCEEDED")
    if loop_lag_seconds > config.loop_lag_soft_limit_seconds:
        reasons.append("EVENT_LOOP_LAG_SOFT_LIMIT_EXCEEDED")
    if queue_utilization_pct > config.queue_utilization_soft_limit_pct:
        reasons.append("QUEUE_UTILIZATION_SOFT_LIMIT_EXCEEDED")
    return ResourceAssessment(
        rss_bytes=rss_bytes,
        loop_lag_seconds=float(loop_lag_seconds),
        queue_utilization_pct=float(queue_utilization_pct),
        degraded=bool(reasons),
        reasons=tuple(reasons),
    )
