"""Lightweight scan-level compute envelope.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

A failure here is logged by the caller and must never reject a candidate.
Unavailable process metrics stay ``None`` rather than zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def process_rss_bytes() -> int | None:
    """Best-effort current RSS. Linux ``/proc``, then ``resource``, else None."""
    status = Path(f"/proc/{os.getpid()}/status")
    try:
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = int(getattr(usage, "ru_maxrss", 0) or 0)
        if rss <= 0:
            return None
        # Linux reports KiB; macOS reports bytes.
        if os.name == "posix" and Path("/proc/self").exists():
            return rss * 1024
        return rss if rss > 1024 * 1024 else rss * 1024
    except (ImportError, OSError, ValueError, AttributeError):
        return None


def process_cpu_seconds() -> float | None:
    """User+system CPU seconds for this process, or None if unmeasurable."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        total = float(usage.ru_utime) + float(usage.ru_stime)
        return total if total >= 0 and total == total else None
    except (ImportError, OSError, ValueError, AttributeError):
        return None


@dataclass
class ScanComputeTracker:
    """Wall-clock and cheap process samples around universe observation."""

    started_at: datetime
    start_monotonic: float
    start_rss_bytes: int | None
    start_cpu_seconds: float | None

    @classmethod
    def start(cls) -> "ScanComputeTracker":
        rss: int | None
        cpu: float | None
        try:
            rss = process_rss_bytes()
        except Exception:
            rss = None
        try:
            cpu = process_cpu_seconds()
        except Exception:
            cpu = None
        return cls(
            started_at=_utc_now(),
            start_monotonic=time.perf_counter(),
            start_rss_bytes=rss,
            start_cpu_seconds=cpu,
        )

    def finish(
        self,
        *,
        scan: Any = None,
        shortlist: Any = None,
        scan_id: str | None = None,
        survivors_count: int | None = None,
    ) -> dict[str, Any]:
        """Return a persistable envelope. Never raises to the caller."""
        try:
            ended_at = _utc_now()
            duration_ms = int((time.perf_counter() - self.start_monotonic) * 1000)
            end_rss: int | None
            end_cpu: float | None
            try:
                end_rss = process_rss_bytes()
            except Exception:
                end_rss = None
            try:
                end_cpu = process_cpu_seconds()
            except Exception:
                end_cpu = None
            peak_rss = None
            samples = [item for item in (self.start_rss_bytes, end_rss) if item is not None]
            if samples:
                peak_rss = max(samples)
            cpu_delta = None
            if self.start_cpu_seconds is not None and end_cpu is not None:
                cpu_delta = round(end_cpu - self.start_cpu_seconds, 6)
                if cpu_delta < 0:
                    cpu_delta = None

            universe = getattr(scan, "universe", None)
            warnings = list(getattr(universe, "warnings", None) or [])
            requested = getattr(scan, "requested", None)
            analyzed = getattr(scan, "analyzed", None)
            failed = getattr(scan, "failed", None)
            skipped = getattr(scan, "skipped", None)
            data_rejected = getattr(scan, "data_quality_rejected", None)
            shortlist_count = len(shortlist) if shortlist is not None else None
            payload = {
                "schema_version": 1,
                "measurement_only": True,
                "trade_authority_changed": False,
                "scan_id": scan_id,
                "scan_started_at": self.started_at.isoformat(),
                "scan_ended_at": ended_at.isoformat(),
                "duration_ms": duration_ms,
                "universe_requested": requested,
                "instruments_analyzed": analyzed,
                "instruments_skipped": skipped,
                "instruments_with_data_failures": failed,
                "data_quality_rejected": data_rejected,
                "shortlist_count": shortlist_count,
                "survivors_count": survivors_count,
                "observation_freshness_seconds": None,
                "provider_api_failure_counts": {
                    "scan_failures": failed,
                    "universe_warnings": len(warnings) if warnings else 0,
                },
                "queue_backlog_age_seconds": None,
                "rss_bytes": end_rss,
                "peak_rss_bytes": peak_rss,
                "cpu_seconds": cpu_delta,
                "rss_status": "OK" if end_rss is not None else "UNAVAILABLE",
                "cpu_status": "OK" if cpu_delta is not None else "UNAVAILABLE",
            }
            return {"compute_envelope": payload}
        except Exception:
            return {
                "compute_envelope": {
                    "schema_version": 1,
                    "measurement_only": True,
                    "unavailable": True,
                    "rss_status": "UNAVAILABLE",
                    "cpu_status": "UNAVAILABLE",
                }
            }
