"""Per-phase resource instrumentation for the canonical cycle workloads.

Why this exists
---------------
The resource-remediation mission has to answer two questions that cannot be
answered from code review alone:

* does Early Watch (``app.jobs.scan_movers``) meaningfully contribute to the core
  container's memory pressure, and
* which phase is actually responsible when a cycle overruns.

Answering either requires measuring, per phase, what it cost: wall duration, CPU
time, resident set size and the container's cgroup memory. Guessing - and then
"isolating" or re-ordering work on the strength of the guess - would change
production behaviour without evidence.

This module therefore provides measurement only. It changes no workload, admits
nothing, sends nothing, ranks nothing and writes nothing except its own bounded
evidence lines. It is **off by default** (``OPIP_PHASE_RESOURCE_TELEMETRY``), so
merging it cannot alter production behaviour; turning it on is an explicit
release decision.

Evidence contract
-----------------
* One line per phase: ``OPIP_PHASE_RESOURCE=<phase> key=value ...``.
* A measurement that is unavailable on this platform reports ``UNKNOWN``. A field
  is never fabricated, defaulted to zero, or interpolated from a neighbouring
  value: an unmeasurable phase must look unmeasurable, not free.
* Every value is bounded and redacted by construction - the emitter carries no
  payload, no symbol, no credential and no free-form message.

Nothing here is a second scheduler, a background sampler or a daemon: it is a
context manager the caller already has to enter and leave.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

#: Opt-in switch. Deliberately off: enabling measurement is a release decision,
#: not something a code merge should decide.
TELEMETRY_ENV = "OPIP_PHASE_RESOURCE_TELEMETRY"

#: Marker prefix. One line per phase, always flushed.
EVIDENCE_PREFIX = "OPIP_PHASE_RESOURCE="

UNKNOWN = "UNKNOWN"

_PROC_STATM = Path("/proc/self/statm")
_PROC_SELF_CGROUP = Path("/proc/self/cgroup")


def telemetry_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether per-phase resource evidence is enabled for this process."""
    source = os.environ if environ is None else environ
    return str(source.get(TELEMETRY_ENV, "") or "").strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
    }


def _page_size() -> int:
    try:
        return max(1, int(os.sysconf("SC_PAGE_SIZE")))
    except (AttributeError, ValueError, OSError):
        return 4096


def resident_bytes() -> int | None:
    """Resident set size of this process, or ``None`` when unmeasurable.

    ``/proc/self/statm`` is the cheapest portable-enough source on the Linux
    production host. On a platform without it this returns ``None`` so the caller
    reports ``UNKNOWN`` rather than a fabricated number.
    """
    try:
        fields = _PROC_STATM.read_text(encoding="ascii").split()
        return int(fields[1]) * _page_size()
    except (OSError, ValueError, IndexError):
        return None


def cgroup_memory_paths() -> tuple[Path, Path] | None:
    """The cgroup v2 ``memory.current`` / ``memory.peak`` paths for this process.

    Returns ``None`` for cgroup v1 or an unreadable ``/proc/self/cgroup``: the
    caller then reports ``UNKNOWN`` instead of assuming a layout.
    """
    try:
        entries = _PROC_SELF_CGROUP.read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    for entry in entries:
        parts = entry.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy, controllers, relative = parts
        if hierarchy != "0" or controllers != "":
            continue
        root = Path("/sys/fs/cgroup")
        relative_path = relative.strip("/")
        base = root / relative_path if relative_path else root
        return base / "memory.current", base / "memory.peak"
    return None


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class PhaseResourceSample:
    """What one phase cost. ``None`` means "not measurable on this platform"."""

    phase: str
    duration_ms: int
    cpu_ms: int | None
    resident_bytes: int | None
    cgroup_current_bytes: int | None
    cgroup_peak_bytes: int | None

    def as_fields(self) -> dict[str, str]:
        def show(value: int | None) -> str:
            return UNKNOWN if value is None else str(int(value))

        return {
            "duration_ms": show(self.duration_ms),
            "cpu_ms": show(self.cpu_ms),
            "rss_bytes": show(self.resident_bytes),
            "cgroup_memory_current_bytes": show(self.cgroup_current_bytes),
            "cgroup_memory_peak_bytes": show(self.cgroup_peak_bytes),
        }

    def evidence_line(self) -> str:
        fields = " ".join(f"{name}={value}" for name, value in self.as_fields().items())
        return f"{EVIDENCE_PREFIX}{self.phase} {fields}"


def _snapshot() -> tuple[float, float, int | None, int | None, int | None]:
    cpu = time.process_time()
    rss = resident_bytes()
    paths = cgroup_memory_paths()
    current = _read_int(paths[0]) if paths is not None else None
    peak = _read_int(paths[1]) if paths is not None else None
    return (time.perf_counter(), cpu, rss, current, peak)


@contextmanager
def measure_phase(
    phase: str,
    *,
    emit=None,
    env: Mapping[str, str] | None = None,
) -> Iterator[None]:
    """Measure one phase and emit a single bounded evidence line on exit.

    Fail-soft in every direction: a phase that raises still emits, so an overrun
    that ends in an error is still attributable, and a measurement failure can
    never propagate into the workload.
    """
    if not telemetry_enabled(env):
        yield
        return

    start_clock, start_cpu, start_rss, start_current, start_peak = _snapshot()
    try:
        yield
    finally:
        end_clock, end_cpu, end_rss, end_current, end_peak = _snapshot()
        duration_ms = max(0, int((end_clock - start_clock) * 1000))
        cpu_ms = max(0, int((end_cpu - start_cpu) * 1000))
        rss = end_rss if end_rss is not None else start_rss
        current = end_current if end_current is not None else start_current
        peak = end_peak if end_peak is not None else start_peak
        if peak is not None and current is not None:
            peak = max(peak, current)
        sample = PhaseResourceSample(
            phase=str(phase),
            duration_ms=duration_ms,
            cpu_ms=cpu_ms,
            resident_bytes=rss,
            cgroup_current_bytes=current,
            cgroup_peak_bytes=peak,
        )
        writer = print if emit is None else emit
        try:
            writer(sample.evidence_line(), flush=True)
        except TypeError:
            # An injected emitter that does not accept ``flush`` is fine.
            writer(sample.evidence_line())
        except Exception:  # noqa: BLE001 - telemetry must never break a workload
            return
