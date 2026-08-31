from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.scanner.market_scanner import analyze_symbol
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.entry_watch_queue import (
    defer_entry_watch,
    due_entry_watch,
    remove_entry_watch,
)


@dataclass(frozen=True)
class EntryWatchRecheckSummary:
    due: int
    checked: int
    ready_symbols: tuple[str, ...]
    deferred: int
    failures: tuple[str, ...]

    @property
    def full_scan_required(self) -> bool:
        return bool(self.ready_symbols)


def recheck_due_entry_watch(
    *,
    now: datetime | None = None,
    max_items: int = 5,
) -> EntryWatchRecheckSummary:
    """Cheap fast recheck that can only request a fresh full qualification scan.

    It emits no external message and grants no trade authority. A candidate that
    becomes entry-valid is removed from the fast queue and causes the unified
    cycle to rerun the complete opportunity pipeline, including intelligence,
    target/economic gates, global ranking and capital allocation.
    """
    due = due_entry_watch(now=now)[: max(1, int(max_items))]
    checked = 0
    deferred = 0
    ready: list[str] = []
    failures: list[str] = []

    for row in due:
        symbol = str(row.get("symbol") or "").upper()
        direction = str(row.get("direction") or "LONG").upper()
        risk_level = str(row.get("risk_level") or "low").lower()
        if not symbol or direction not in {"LONG", "SHORT"}:
            failures.append(f"{symbol or 'UNKNOWN'}: malformed entry-watch row")
            if symbol:
                remove_entry_watch(symbol, direction)
            continue

        try:
            status, snapshot, reason = analyze_symbol(symbol)
        except Exception as exc:
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
            defer_entry_watch(symbol, direction, now=now, recheck_seconds=120)
            deferred += 1
            continue

        checked += 1
        if status != "ok" or snapshot is None:
            failures.append(f"{symbol}: {reason or status}")
            defer_entry_watch(symbol, direction, now=now, recheck_seconds=120)
            deferred += 1
            continue

        try:
            plan = build_entry_exit_plan(
                snapshot,
                risk_level if risk_level in {"low", "medium"} else "low",
                direction=direction,
            )
        except Exception as exc:
            failures.append(f"{symbol}: plan {type(exc).__name__}: {exc}")
            defer_entry_watch(symbol, direction, now=now, recheck_seconds=120)
            deferred += 1
            continue

        if plan.valid_now:
            # Preserve the watch until the complete qualification + global
            # capital gate accepts it. If the full scan fails transiently, the
            # candidate remains eligible for another bounded fast recheck.
            defer_entry_watch(symbol, direction, now=now, recheck_seconds=120)
            ready.append(symbol)
        else:
            defer_entry_watch(symbol, direction, now=now, recheck_seconds=90)
            deferred += 1

    return EntryWatchRecheckSummary(
        due=len(due),
        checked=checked,
        ready_symbols=tuple(sorted(set(ready))),
        deferred=deferred,
        failures=tuple(failures),
    )