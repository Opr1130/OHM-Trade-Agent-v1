"""Durable scan-attempt identity, state machine and fencing (R01/R02/R08).

One owner writes this ledger: the core process. The isolated acquisition
worker never appends here, so there is no cross-process double writer. The
ledger is append-only; the index is a rebuildable projection.

An attempt is *active* only while it is in a non-terminal state. Termination is
never inferred from a timestamp: `active()` reports the durable state, and
`container_is_live` evidence is supplied by the caller. An expired deadline
alone must never authorise a second attempt (R02).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from app.opip.storage.bounded_jsonl import iter_lines, read_tail_lines
from app.services.registry_io import load_json, registry_lock, save_json_atomic


SCHEMA_VERSION = 1

STATE_RESERVED = "RESERVED"
STATE_RUNNING = "RUNNING"
STATE_VALIDATING = "VALIDATING"
STATE_TERMINAL = "TERMINAL"

OUTCOME_COMPLETED = "COMPLETED"
OUTCOME_PARTIAL = "PARTIAL"
OUTCOME_FAILED = "FAILED"
OUTCOME_TIMED_OUT = "TIMED_OUT"
OUTCOME_OOM_KILLED = "OOM_KILLED"
OUTCOME_CANCELLED = "CANCELLED"
OUTCOME_SKIPPED = "SKIPPED"

TERMINAL_OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_COMPLETED,
        OUTCOME_PARTIAL,
        OUTCOME_FAILED,
        OUTCOME_TIMED_OUT,
        OUTCOME_OOM_KILLED,
        OUTCOME_CANCELLED,
        OUTCOME_SKIPPED,
    }
)

ACTIVE_STATES: frozenset[str] = frozenset({STATE_RESERVED, STATE_RUNNING, STATE_VALIDATING})

#: A completed attempt only. PARTIAL is deliberately excluded: a partial scan
#: may never authorise an actionable entry (R07).
SUCCESSFUL_OUTCOMES: frozenset[str] = frozenset({OUTCOME_COMPLETED})

MAX_INDEX_ATTEMPTS = 50
MAX_REASON_CHARS = 96

#: Consecutive isolated-workflow failures before discovery is suspended, and
#: for how long. A suspended discovery never falls back to the in-process
#: scanner; it simply stops until the breaker expires or an operator acts.
BREAKER_FAILURE_THRESHOLD = 3
BREAKER_COOLDOWN_SECONDS = 900
BREAKER_OUTCOMES: frozenset[str] = frozenset(
    {
        "FAILED",
        "TIMED_OUT",
        "OOM_KILLED",
        "CANCELLED",
    }
)


class ScanAttemptError(RuntimeError):
    """An attempt transition was rejected."""


def _utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scan attempt timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_reason(code: str | None) -> str | None:
    text = "".join(ch for ch in str(code or "").strip().upper() if ch.isalnum() or ch == "_")
    return text[:MAX_REASON_CHARS] or None


@dataclass(frozen=True)
class ScanAttempt:
    """Immutable projection of one durable attempt."""

    attempt_id: str
    state: str
    outcome: str | None
    reason_code: str | None
    fence: int
    created_at: str
    deadline_at: str | None = None
    finished_at: str | None = None
    container_id: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        # Defence in depth: a row that carries a terminal outcome is never
        # active, even if a legacy or hand-edited record still says otherwise.
        return self.state in ACTIVE_STATES and self.outcome not in TERMINAL_OUTCOMES

    @property
    def terminal(self) -> bool:
        return self.outcome in TERMINAL_OUTCOMES

    @property
    def successful(self) -> bool:
        return self.outcome in SUCCESSFUL_OUTCOMES

    def deadline_passed(self, now: datetime | None = None) -> bool:
        deadline = _parse(self.deadline_at)
        return deadline is not None and _utc(now) >= deadline

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = dict(self.evidence)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScanAttempt":
        return cls(
            attempt_id=str(payload["attempt_id"]),
            state=str(payload.get("state") or STATE_RESERVED),
            outcome=payload.get("outcome"),
            reason_code=payload.get("reason_code"),
            fence=int(payload.get("fence") or 0),
            created_at=str(payload.get("created_at") or ""),
            deadline_at=payload.get("deadline_at"),
            finished_at=payload.get("finished_at"),
            container_id=payload.get("container_id"),
            evidence=dict(payload.get("evidence") or {}),
        )


class ScanAttemptStore:
    """Append-only attempt ledger plus a rebuildable projection index."""

    def __init__(self, root: Path | str, *, read_only: bool = False) -> None:
        self.root = Path(root)
        self.ledger_file = self.root / "scan_attempts.jsonl"
        self.index_file = self.root / "scan_attempt_index.json"
        self.lock_file = self.root / ".scan_attempts.lock"
        # The acquisition worker reads attempt metadata but must never mutate
        # operational state. In read-only mode a stale or missing index is
        # resolved in memory and nothing is persisted or locked.
        self.read_only = bool(read_only)

    def _require_writable(self) -> None:
        if self.read_only:
            raise ScanAttemptError("attempt store is read-only in this process")

    # -- low level ---------------------------------------------------------

    def _append_event(self, event: dict[str, Any]) -> int:
        """Append one event, assigning the next monotonic sequence number."""
        self._require_writable()
        self.root.mkdir(parents=True, exist_ok=True)
        seq = self._next_seq()
        payload = dict(event)
        payload["seq"] = seq
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.ledger_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return seq

    def _next_seq(self) -> int:
        """Return the next ledger sequence number.

        Derived from the ledger itself, never from the index, so a crash after
        an event append but before an index save cannot reuse a sequence number.
        """
        last = self._last_ledger_seq()
        return 1 if last <= 0 else last + 1

    def _last_ledger_seq(self) -> int:
        """Return the highest committed ``seq``, or -1 when undeterminable."""
        if not self.ledger_file.exists():
            return 0
        try:
            rows = read_tail_lines(self.ledger_file, 8)
        except OSError:
            return -1
        for row in reversed(rows):
            try:
                parsed = json.loads(row)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("seq"), int):
                return int(parsed["seq"])
        return -1

    def _read_index_file(self) -> dict[str, Any] | None:
        try:
            payload = load_json(self.index_file)
        except Exception:  # noqa: BLE001 - index is a projection, never authority
            return None
        if not isinstance(payload, dict):
            return None
        attempts = payload.get("attempts")
        if not isinstance(attempts, dict):
            return None
        return {
            "schema_version": SCHEMA_VERSION,
            "last_seq": int(payload.get("last_seq") or 0),
            "fence_high_water": int(payload.get("fence_high_water") or 0),
            "attempts": attempts,
            "discovery_blocked_until": payload.get("discovery_blocked_until"),
            "consecutive_failures": int(payload.get("consecutive_failures") or 0),
        }

    def _load_index(self) -> dict[str, Any]:
        """Return the projection, rebuilding it from the ledger when stale.

        The ledger is the authority. A crash between "event appended" and
        "index saved" leaves the index behind by one sequence number, which is
        detected here and repaired by replay rather than silently trusted.
        """
        last_seq = self._last_ledger_seq()
        index = self._read_index_file()
        if last_seq < 0:
            # The tail is unreadable (torn write). Replay, which skips
            # unparseable rows, so a single torn line cannot wedge the ledger.
            return self._rebuild_index()
        if index is None or int(index.get("last_seq") or 0) != last_seq:
            return self._rebuild_index()
        return index

    def _rebuild_index(self) -> dict[str, Any]:
        """Replay the append-only ledger into a fresh projection."""
        attempts: dict[str, Any] = {}
        last_seq = 0
        fence_high = 0
        blocked_until: str | None = None
        failures = 0

        def apply(event: dict[str, Any]) -> None:
            nonlocal fence_high, blocked_until, failures
            attempt_id = event.get("attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id:
                return
            if event.get("event") == "SKIP":
                fence_high = max(fence_high, int(event.get("fence") or 0))
                return
            attempts[attempt_id] = {
                key: event.get(key)
                for key in (
                    "attempt_id",
                    "state",
                    "outcome",
                    "reason_code",
                    "fence",
                    "created_at",
                    "deadline_at",
                    "finished_at",
                    "container_id",
                    "evidence",
                )
            }
            fence_high = max(fence_high, int(event.get("fence") or 0))
            blocked_until = event.get("discovery_blocked_until") or blocked_until
            failures = int(event.get("consecutive_failures") or failures)

        if self.ledger_file.exists():
            for row in iter_lines(self.ledger_file):
                try:
                    parsed = json.loads(row)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(parsed, dict):
                    continue
                seq = parsed.get("seq")
                if isinstance(seq, int):
                    last_seq = max(last_seq, seq)
                apply(parsed)

        index = {
            "schema_version": SCHEMA_VERSION,
            "last_seq": last_seq,
            "fence_high_water": fence_high,
            "attempts": attempts,
            "discovery_blocked_until": blocked_until,
            "consecutive_failures": failures,
        }
        self._write_index(index)
        return index

    def _write_index(self, index: dict[str, Any]) -> None:
        if self.read_only:
            # Read-only processes must not create or mutate the projection.
            return
        attempts = index.get("attempts") or {}
        if len(attempts) > MAX_INDEX_ATTEMPTS:
            ordered = sorted(
                attempts.items(),
                key=lambda item: str(item[1].get("created_at") or ""),
                reverse=True,
            )[:MAX_INDEX_ATTEMPTS]
            index["attempts"] = dict(ordered)
        save_json_atomic(self.index_file, index)

    def _save_index(self, index: dict[str, Any]) -> None:
        self._write_index(index)

    def _next_fence(self, index: dict[str, Any]) -> int:
        current = [
            int(row.get("fence") or 0)
            for row in (index.get("attempts") or {}).values()
            if isinstance(row, dict)
        ]
        current.append(int(index.get("fence_high_water") or 0))
        return max(current) + 1

    def discovery_blocked_until(self, now: datetime | None = None) -> datetime | None:
        """Return the circuit-breaker deadline, or None when closed."""
        raw = self._load_index().get("discovery_blocked_until")
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def is_discovery_blocked(self, now: datetime | None = None) -> bool:
        blocked_until = self.discovery_blocked_until()
        return blocked_until is not None and _utc(now) < blocked_until

    def consecutive_failures(self) -> int:
        return int(self._load_index().get("consecutive_failures") or 0)

    def _rows(self, index: dict[str, Any]) -> list[ScanAttempt]:
        return [
            ScanAttempt.from_dict(row)
            for row in (index.get("attempts") or {}).values()
            if isinstance(row, dict)
        ]

    # -- queries -----------------------------------------------------------

    def get(self, attempt_id: str) -> ScanAttempt | None:
        row = (self._load_index().get("attempts") or {}).get(str(attempt_id))
        return ScanAttempt.from_dict(row) if isinstance(row, dict) else None

    def active(self) -> ScanAttempt | None:
        """Return the single active attempt, if any.

        If more than one active row ever exists the invariant has already been
        violated; the oldest is returned so the caller quarantines instead of
        starting another attempt.
        """
        active = [item for item in self._rows(self._load_index()) if item.active]
        if not active:
            return None
        return sorted(active, key=lambda item: item.created_at)[0]

    def latest(self) -> ScanAttempt | None:
        rows = self._rows(self._load_index())
        return sorted(rows, key=lambda item: item.created_at)[-1] if rows else None

    def has_active(self) -> bool:
        return self.active() is not None

    # -- mutations ---------------------------------------------------------

    def create(
        self,
        *,
        attempt_id: str,
        deadline_seconds: int,
        now: datetime | None = None,
        container_id: str | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> ScanAttempt:
        """Reserve a new attempt. Refuses while any attempt is active."""
        moment = _utc(now)
        with registry_lock(self.lock_file):
            index = self._load_index()
            for row in (index.get("attempts") or {}).values():
                if isinstance(row, dict) and str(row.get("state")) in ACTIVE_STATES:
                    raise ScanAttemptError(
                        f"attempt {row.get('attempt_id')} is still {row.get('state')}"
                    )
            record = ScanAttempt(
                attempt_id=str(attempt_id),
                state=STATE_RESERVED,
                outcome=None,
                reason_code=None,
                fence=self._next_fence(index),
                created_at=moment.isoformat(),
                deadline_at=(moment + timedelta(seconds=int(deadline_seconds))).isoformat(),
                container_id=container_id,
                evidence=dict(evidence or {}),
            )
            seq = self._append_event(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": "CREATE",
                    **record.as_dict(),
                    "at": moment.isoformat(),
                }
            )
            index.setdefault("attempts", {})[record.attempt_id] = record.as_dict()
            index["last_seq"] = seq
            index["fence_high_water"] = max(
                int(index.get("fence_high_water") or 0), record.fence
            )
            self._save_index(index)
        return record

    def terminalize(
        self,
        attempt_id: str,
        outcome: str,
        reason_code: str | None = None,
        *,
        evidence: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ScanAttempt:
        normalized = str(outcome or "").strip().upper()
        if normalized not in TERMINAL_OUTCOMES:
            raise ScanAttemptError(f"unsupported terminal outcome {outcome}")
        moment = _utc(now)
        with registry_lock(self.lock_file):
            index = self._load_index()
            row = (index.get("attempts") or {}).get(str(attempt_id))
            if not isinstance(row, dict):
                raise ScanAttemptError(f"unknown attempt {attempt_id}")
            current = ScanAttempt.from_dict(row)
            if current.terminal:
                # Idempotent: repeating a terminalization never produces a
                # second, conflicting outcome.
                return current
            merged = dict(current.evidence)
            merged.update(dict(evidence or {}))
            updated = ScanAttempt(
                attempt_id=current.attempt_id,
                state=STATE_TERMINAL,
                outcome=normalized,
                reason_code=_clean_reason(reason_code),
                fence=current.fence,
                created_at=current.created_at,
                deadline_at=current.deadline_at,
                finished_at=moment.isoformat(),
                container_id=current.container_id,
                evidence=merged,
            )
            event = {
                "schema_version": SCHEMA_VERSION,
                "event": "TERMINAL",
                **updated.as_dict(),
                "at": moment.isoformat(),
            }
            failures = int(index.get("consecutive_failures") or 0)
            blocked = index.get("discovery_blocked_until")
            if normalized == OUTCOME_COMPLETED:
                failures = 0
                blocked = None
            elif normalized in BREAKER_OUTCOMES:
                failures += 1
                if failures >= BREAKER_FAILURE_THRESHOLD:
                    blocked = (
                        moment + timedelta(seconds=BREAKER_COOLDOWN_SECONDS)
                    ).isoformat()
            event["consecutive_failures"] = failures
            event["discovery_blocked_until"] = blocked
            seq = self._append_event(event)
            index["attempts"][updated.attempt_id] = updated.as_dict()
            index["last_seq"] = seq
            index["consecutive_failures"] = failures
            index["discovery_blocked_until"] = blocked
            self._save_index(index)
        return updated

    def transition(
        self,
        attempt_id: str,
        *,
        state: str | None = None,
        container_id: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ScanAttempt:
        moment = _utc(now)
        with registry_lock(self.lock_file):
            index = self._load_index()
            row = (index.get("attempts") or {}).get(str(attempt_id))
            if not isinstance(row, dict):
                raise ScanAttemptError(f"unknown attempt {attempt_id}")
            current = ScanAttempt.from_dict(row)
            if current.terminal:
                raise ScanAttemptError(f"attempt {attempt_id} is already terminal")
            merged = dict(current.evidence)
            merged.update(dict(evidence or {}))
            updated = ScanAttempt(
                attempt_id=current.attempt_id,
                state=state or current.state,
                outcome=current.outcome,
                reason_code=current.reason_code,
                fence=current.fence,
                created_at=current.created_at,
                deadline_at=current.deadline_at,
                finished_at=current.finished_at,
                container_id=container_id or current.container_id,
                evidence=merged,
            )
            seq = self._append_event(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": "TRANSITION",
                    **updated.as_dict(),
                    "at": moment.isoformat(),
                }
            )
            index["attempts"][updated.attempt_id] = updated.as_dict()
            index["last_seq"] = seq
            self._save_index(index)
        return updated

    def record_skip(
        self,
        *,
        attempt_id: str,
        reason_code: str,
        now: datetime | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> ScanAttempt:
        """Record that discovery was considered and not admitted.

        A skip is terminal and never active, so it cannot block the next
        attempt and cannot be mistaken for work in flight.
        """
        moment = _utc(now)
        with registry_lock(self.lock_file):
            index = self._load_index()
            record = ScanAttempt(
                attempt_id=str(attempt_id),
                state="TERMINAL",
                outcome=OUTCOME_SKIPPED,
                reason_code=_clean_reason(reason_code),
                fence=self._next_fence(index),
                created_at=moment.isoformat(),
                finished_at=moment.isoformat(),
                evidence=dict(evidence or {}),
            )
            seq = self._append_event(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": "SKIP",
                    **record.as_dict(),
                    "at": moment.isoformat(),
                }
            )
            index.setdefault("attempts", {})[record.attempt_id] = record.as_dict()
            index["last_seq"] = seq
            index["fence_high_water"] = max(
                int(index.get("fence_high_water") or 0), record.fence
            )
            self._save_index(index)
        return record


