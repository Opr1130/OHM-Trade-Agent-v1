from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.scanner.market_scanner import analyze_symbol
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.entry_watch_queue import (
    defer_entry_watch,
    due_entry_watch,
    remove_entry_watch,
    remove_entry_watch_key,
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
    becomes entry-valid remains in the bounded fast queue while requesting the
    complete opportunity pipeline. The accelerated request receives a longer
    cooldown and the original watch expiry is preserved, so downstream ranking
    or capital vetoes cannot create an indefinite fast-rescan loop.
    """
    due = due_entry_watch(now=now)[: max(1, int(max_items))]
    checked = 0
    deferred = 0
    ready: list[str] = []
    failures: list[str] = []

    for row in due:
        watch_key = row.get("_watch_key")
        raw_symbol = str(row.get("symbol") or "")
        raw_direction = str(row.get("direction") or "LONG")
        symbol = raw_symbol.strip().upper()
        direction = raw_direction.strip().upper()
        risk_level = str(row.get("risk_level") or "low").lower()
        if not symbol or direction not in {"LONG", "SHORT"}:
            failures.append(f"{symbol or 'UNKNOWN'}: malformed entry-watch row")
            # A structurally invalid row is terminal: quarantine it so it can
            # never survive to consume a future recheck slot, even when it
            # has no usable symbol. Prefer the queue's own key (exact match);
            # fall back to reconstructing the key the row was most likely
            # stored under.
            removed = bool(watch_key) and remove_entry_watch_key(str(watch_key))
            if not removed:
                remove_entry_watch(raw_symbol.upper(), raw_direction.upper())
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
            # capital gate accepts it, but prevent a downstream veto from
            # triggering another accelerated full scan every two minutes.
            defer_entry_watch(
                symbol,
                direction,
                now=now,
                recheck_seconds=300,
                accelerated_scan=True,
            )
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