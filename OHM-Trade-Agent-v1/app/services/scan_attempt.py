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
from typing import Any, Iterable, Mapping

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

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.ledger_file = self.root / "scan_attempts.jsonl"
        self.index_file = self.root / "scan_attempt_index.json"
        self.lock_file = self.root / ".scan_attempts.lock"

    # -- low level ---------------------------------------------------------

    def _append_event(self, event: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.ledger_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _load_index(self) -> dict[str, Any]:
        try:
            payload = load_json(self.index_file)
        except Exception:  # noqa: BLE001 - index is a projection, never authority
            payload = {}
        if not isinstance(payload, dict):
            return {"schema_version": SCHEMA_VERSION, "attempts": {}}
        attempts = payload.get("attempts")
        return {
            "schema_version": SCHEMA_VERSION,
            "attempts": attempts if isinstance(attempts, dict) else {},
        }

    def _save_index(self, index: dict[str, Any]) -> None:
        attempts = index.get("attempts") or {}
        if len(attempts) > MAX_INDEX_ATTEMPTS:
            ordered = sorted(
                attempts.items(),
                key=lambda item: str(item[1].get("created_at") or ""),
                reverse=True,
            )[:MAX_INDEX_ATTEMPTS]
            index["attempts"] = dict(ordered)
        save_json_atomic(self.index_file, index)

    def _next_fence(self, index: dict[str, Any]) -> int:
        current = [
            int(row.get("fence") or 0)
            for row in (index.get("attempts") or {}).values()
            if isinstance(row, dict)
        ]
        return (max(current) + 1) if current else 1

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
            self._append_event(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": "CREATE",
                    **record.as_dict(),
                    "at": moment.isoformat(),
                }
            )
            index.setdefault("attempts", {})[record.attempt_id] = record.as_dict()
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
            self._append_event(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": "TERMINAL",
                    **updated.as_dict(),
                    "at": moment.isoformat(),
                }
            )
            index["attempts"][updated.attempt_id] = updated.as_dict()
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
            self._append_event(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": "TRANSITION",
                    **updated.as_dict(),
                    "at": moment.isoformat(),
                }
            )
            index["attempts"][updated.attempt_id] = updated.as_dict()
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
            self._append_event(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": "SKIP",
                    **record.as_dict(),
                    "at": moment.isoformat(),
                }
            )
            index.setdefault("attempts", {})[record.attempt_id] = record.as_dict()
            self._save_index(index)
        return record


