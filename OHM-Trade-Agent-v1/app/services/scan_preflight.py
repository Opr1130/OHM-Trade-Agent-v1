"""Resource preflight admission for isolated scanning (R03/R05).

Every read is best-effort and *fails closed*: if the host or cgroup headroom
cannot be established, discovery is not admitted. Protection is unaffected —
this module only ever decides whether a scan may start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import os


MIB = 1024 * 1024

REASON_APPROVED = "APPROVED"
REASON_READ_ERROR = "PREFLIGHT_READ_ERROR"
REASON_HOST_RESERVE = "INSUFFICIENT_HOST_RESERVE"
REASON_CORE_CEILING = "CORE_CGROUP_CEILING"
REASON_CPU_PRESSURE = "CPU_PRESSURE"
REASON_DISK = "INSUFFICIENT_DISK"
REASON_INODES = "INSUFFICIENT_INODES"

#: Discovery is withheld once CPU pressure reaches this 10s average (%).
DEFAULT_MAX_CPU_PRESSURE = 80.0
DEFAULT_MIN_DISK_MIB = 2048
DEFAULT_MIN_INODES = 10_000


@dataclass(frozen=True)
class ResourceSnapshot:
    host_available_mib: float | None = None
    core_current_mib: float | None = None
    core_max_mib: float | None = None
    cpu_pressure_avg10: float | None = None
    memory_pressure_avg10: float | None = None
    disk_free_mib: float | None = None
    inodes_free: int | None = None
    read_errors: tuple[str, ...] = ()
    source: Mapping[str, Any] = field(default_factory=dict)

    @property
    def core_utilization_pct(self) -> float | None:
        if not self.core_current_mib or not self.core_max_mib:
            return None
        return (self.core_current_mib / self.core_max_mib) * 100.0

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "host_available_mib": self.host_available_mib,
            "core_current_mib": self.core_current_mib,
            "core_max_mib": self.core_max_mib,
            "core_utilization_pct": self.core_utilization_pct,
            "cpu_pressure_avg10": self.cpu_pressure_avg10,
            "memory_pressure_avg10": self.memory_pressure_avg10,
            "disk_free_mib": self.disk_free_mib,
            "inodes_free": self.inodes_free,
            "read_errors": list(self.read_errors),
        }
        payload.update(dict(self.source))
        return payload


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason_code: str
    snapshot: ResourceSnapshot


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        return handle.read()


def read_mem_available_mib(meminfo: Path | str = "/proc/meminfo") -> float:
    for line in _read_text(Path(meminfo)).splitlines():
        if line.startswith("MemAvailable:"):
            return float(line.split()[1]) / 1024.0
    raise ValueError("MemAvailable missing from meminfo")


def read_pressure_avg10(scope: str, base: Path | str = "/proc/pressure") -> float | None:
    """Return PSI ``some avg10`` for scope, or None when PSI is unavailable."""
    path = Path(base) / scope
    if not path.exists():
        return None
    for line in _read_text(path).splitlines():
        if line.startswith("some"):
            for token in line.split():
                if token.startswith("avg10="):
                    return float(token.split("=", 1)[1])
    return None


def _cgroup_dir(pid: int | None = None, base: Path | str = "/sys/fs/cgroup") -> Path:
    """Resolve the cgroup directory for ``pid`` from /proc/<pid>/cgroup."""
    pid = pid or os.getpid()
    line = _read_text(Path(f"/proc/{pid}/cgroup")).strip().splitlines()[-1]
    relative = line.split("::", 1)[-1] if "::" in line else ""
    relative = relative or "/"
    return Path(base) / relative.lstrip("/")


def _cgroup_mib(directory: Path, name: str) -> float | None:
    path = directory / name
    if not path.exists():
        return None
    raw = _read_text(path).strip()
    if raw == "max":
        return None
    return float(raw) / MIB


def read_cgroup_metrics(
    *, base: Path | str = "/sys/fs/cgroup", pid: int | None = None
) -> tuple[float | None, float | None]:
    directory = _cgroup_dir(pid=pid, base=base)
    return _cgroup_mib(directory, "memory.current"), _cgroup_mib(directory, "memory.max")


def collect_snapshot(
    *,
    data_dir: Path | str = "/app/data",
    cgroup_base: Path | str = "/sys/fs/cgroup",
    pressure_base: Path | str = "/proc/pressure",
    meminfo: Path | str = "/proc/meminfo",
    pid: int | None = None,
) -> ResourceSnapshot:
    errors: list[str] = []
    host_available: float | None = None
    core_current: float | None = None
    core_max: float | None = None
    cpu_pressure: float | None = None
    memory_pressure: float | None = None
    disk_free: float | None = None
    inodes_free: int | None = None

    try:
        host_available = read_mem_available_mib(meminfo)
    except (OSError, ValueError):
        errors.append("MEMINFO")
    try:
        core_current, core_max = read_cgroup_metrics(base=cgroup_base, pid=pid)
        if core_current is None or core_max is None:
            errors.append("CGROUP_LIMIT_UNKNOWN")
    except (OSError, ValueError, IndexError):
        errors.append("CGROUP")
    try:
        cpu_pressure = read_pressure_avg10("cpu", base=pressure_base)
        memory_pressure = read_pressure_avg10("memory", base=pressure_base)
    except (OSError, ValueError):
        errors.append("PRESSURE")
    try:
        if Path(data_dir).exists():
            stats = os.statvfs(str(data_dir))
            disk_free = (stats.f_bavail * stats.f_frsize) / MIB
            inodes_free = int(stats.f_favail)
    except OSError:
        errors.append("STATVFS")

    return ResourceSnapshot(
        host_available_mib=host_available,
        core_current_mib=core_current,
        core_max_mib=core_max,
        cpu_pressure_avg10=cpu_pressure,
        memory_pressure_avg10=memory_pressure,
        disk_free_mib=disk_free,
        inodes_free=inodes_free,
        read_errors=tuple(errors),
        source={"data_dir": str(data_dir)},
    )


def evaluate(
    snapshot: ResourceSnapshot,
    *,
    scan_cap_mib: int,
    host_reserve_mib: int,
    core_ceiling_pct: float,
    max_cpu_pressure: float = DEFAULT_MAX_CPU_PRESSURE,
    min_disk_mib: int = DEFAULT_MIN_DISK_MIB,
    min_inodes: int = DEFAULT_MIN_INODES,
) -> AdmissionDecision:
    """Decide whether discovery may start. Any doubt fails closed."""
    if snapshot.read_errors:
        return AdmissionDecision(False, REASON_READ_ERROR, snapshot)

    required = float(scan_cap_mib) + float(host_reserve_mib)
    if snapshot.host_available_mib is None or snapshot.host_available_mib < required:
        return AdmissionDecision(False, REASON_HOST_RESERVE, snapshot)

    utilization = snapshot.core_utilization_pct
    if utilization is not None and utilization > float(core_ceiling_pct):
        return AdmissionDecision(False, REASON_CORE_CEILING, snapshot)

    if snapshot.cpu_pressure_avg10 is not None and snapshot.cpu_pressure_avg10 > float(
        max_cpu_pressure
    ):
        return AdmissionDecision(False, REASON_CPU_PRESSURE, snapshot)

    if snapshot.disk_free_mib is not None and snapshot.disk_free_mib < float(min_disk_mib):
        return AdmissionDecision(False, REASON_DISK, snapshot)
    if snapshot.inodes_free is not None and snapshot.inodes_free < int(min_inodes):
        return AdmissionDecision(False, REASON_INODES, snapshot)

    return AdmissionDecision(True, REASON_APPROVED, snapshot)

