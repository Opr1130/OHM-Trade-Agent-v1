"""Read-only, bounded O'Pip stability snapshot from existing host-local evidence.

No network, model calls, database writes, trading authority or background workers.
Run from the repo root with: python tools/opip_stability_snapshot.py --root data
The command emits only aggregate counts, never raw trade records or credentials.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
LEDGERS = {
    "scan_activity": "scan_activity.jsonl",
    "openai_usage": "openai_usage.jsonl",
    "ai_router_usage": "opip_ai_router_usage.jsonl",
    "telegram_delivery": "telegram_delivery_events.jsonl",
}


def _timestamp(row: dict[str, Any]) -> datetime | None:
    for field in ("attempted_at", "timestamp_utc", "completed_at_utc", "created_at", "observed_at"):
        value = row.get(field)
        if not value:
            continue
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if stamp.tzinfo is not None and stamp.utcoffset() is not None:
            return stamp.astimezone(timezone.utc)
    return None


def recent_rows(path: Path, *, cutoff: datetime, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a bounded tail; report truncation rather than pretend 24h completeness."""
    meta: dict[str, Any] = {"present": False, "truncated": False, "malformed": 0, "rows_in_window": 0}
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            meta["present"] = True
            meta["size_bytes"] = size
            start = max(0, size - max(1024, max_bytes))
            meta["truncated"] = start > 0
            handle.seek(start)
            if start:
                handle.readline()  # discard a possibly partial line
            rows = []
            for line in handle:
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    meta["malformed"] += 1
                    continue
                if not isinstance(row, dict):
                    meta["malformed"] += 1
                    continue
                stamp = _timestamp(row)
                if stamp is not None and stamp >= cutoff:
                    rows.append(row)
            meta["rows_in_window"] = len(rows)
            return rows, meta
    except OSError as exc:
        meta["error"] = type(exc).__name__
        return [], meta


def _counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field) or "UNKNOWN")[:96] for row in rows).items()))


def _total(rows: list[dict[str, Any]], field: str) -> int:
    return sum(int(value) for row in rows if isinstance((value := row.get(field)), (int, float)) and not isinstance(value, bool))


def cycle_liveness(path: Path, *, cutoff: datetime, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """Count explicitly terminal cycle statuses in a bounded host log tail.

    Log lines must carry an ISO timestamp to be assigned to the window.
    Without stamps, report the fact as unmeasured instead of assuming health.
    """
    result: dict[str, Any] = {"present": False, "truncated": False, "completed": 0, "degraded": None, "skipped_lock": None, "timestamped_events": 0}
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            result["present"] = True
            result["size_bytes"] = size
            start = max(0, size - max(1024, max_bytes))
            result["truncated"] = start > 0
            handle.seek(start)
            if start:
                handle.readline()
            for raw in handle:
                line = raw.decode("utf-8", "replace").strip()
                # The host's cron log can be un-stamped. A status without a
                # timestamp cannot be attributed to the last N hours.
                if "OPIP_UNIFIED_CYCLE_COMPLETED_AT=" not in line:
                    continue
                try:
                    value = line.split("OPIP_UNIFIED_CYCLE_COMPLETED_AT=", 1)[1].split()[0]
                    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if stamp.tzinfo is None or stamp.utcoffset() is None or stamp < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue
                result["timestamped_events"] += 1
                result["completed"] += 1
    except OSError as exc:
        result["error"] = type(exc).__name__
    result["measurement"] = "INCOMPLETE" if not result["present"] or result["truncated"] or not result["timestamped_events"] else "TIMESTAMPED_LOG_WINDOW"
    return result


def _runtime() -> dict[str, Any]:
    # /proc is host-specific: absent inside containers and never represented as
    # host-wide truth if mounted inside a different namespace.
    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:")):
                key, value = line.split(":", 1)
                memory[key] = int(value.strip().split()[0])
        load = os.getloadavg()
        return {"scope": "CURRENT_PROCESS_NAMESPACE", "meminfo_kib": memory, "load_averages": list(load)}
    except (OSError, ValueError, IndexError):
        return {"scope": "UNAVAILABLE"}


def snapshot(root: Path, *, now: datetime | None = None, hours: int = 24, max_bytes: int = DEFAULT_MAX_BYTES, cycle_log: Path | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    if hours < 1 or hours > 168:
        raise ValueError("hours must be 1..168")
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=hours)
    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "window_hours": hours,
        "source": "LOCAL_READ_ONLY",
        "host_runtime": _runtime(),
        "ledgers": {},
        "metrics": {},
    }
    rows_by_source = {}
    for name, filename in LEDGERS.items():
        rows, meta = recent_rows(root / filename, cutoff=cutoff, max_bytes=max_bytes)
        rows_by_source[name] = rows
        result["ledgers"][name] = meta

    scans = rows_by_source["scan_activity"]
    usage = rows_by_source["openai_usage"]
    router = rows_by_source["ai_router_usage"]
    delivery = [row for row in rows_by_source["telegram_delivery"] if row.get("record_type") == "TELEGRAM_DELIVERY_EVENT"]
    result["metrics"]["scans"] = {"count": len(scans), "by_outcome": _counts(scans, "outcome")}
    # Hosts without an explicitly timestamped cycle log cannot establish
    # cycle-health rates, regardless of whether the release probe passed.
    result["metrics"]["cycles"] = cycle_liveness(
        cycle_log or Path("/var/log/ohm-unified-cycle.log"), cutoff=cutoff, max_bytes=max_bytes
    )
    try:
        disk = os.statvfs(root)
        result["filesystem"] = {
            "scope": "DATA_PATH_FILESYSTEM",
            "total_bytes": disk.f_blocks * disk.f_frsize,
            "available_bytes": disk.f_bavail * disk.f_frsize,
        }
    except OSError:
        result["filesystem"] = {"scope": "UNAVAILABLE"}
    result["metrics"]["chief_openai"] = {
        "calls_recorded": len(usage),
        "by_model": _counts(usage, "model"),
        "total_tokens": _total(usage, "total_tokens"),
        "input_tokens": _total(usage, "input_tokens"),
        "output_tokens": _total(usage, "output_tokens"),
        "usd_spend": None,  # token usage is not a provider billing receipt
    }
    result["metrics"]["ai_router"] = {
        "attempts_recorded": len(router),
        "by_provider": _counts(router, "provider"),
        "by_status": _counts(router, "status"),
        "by_error_code": _counts([r for r in router if r.get("error_code")], "error_code"),
    }
    result["metrics"]["telegram"] = {
        "events_recorded": len(delivery),
        "by_status": _counts(delivery, "status"),
        "by_family": _counts(delivery, "alert_family"),
        "by_suppression_reason": _counts([r for r in delivery if r.get("suppression_reason")], "suppression_reason"),
    }
    result["coverage"] = "INCOMPLETE" if any(
        not meta["present"] or meta["truncated"] or meta.get("error")
        for meta in result["ledgers"].values()
    ) else "SAMPLED_AVAILABLE_LEDGER_WINDOW"
    result["limitations"] = [
        "Missing records and disabled instrumentation cannot be counted as zero activity",
        "A tail-limited ledger may exclude earlier events in the requested window",
        "No live Docker/container RSS, restart, OOM or PostgreSQL sizes are measured",
        "This read-only command never enables or alters model learning or trading authority",
    ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/app/data"))
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--cycle-log", type=Path, default=Path("/var/log/ohm-unified-cycle.log"))
    parser.add_argument("--max-ledger-mib", type=int, default=32)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.root, hours=args.hours, max_bytes=max(1, args.max_ledger_mib) * 1024 * 1024, cycle_log=args.cycle_log), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
