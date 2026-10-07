"""Load latest FeatureStateCheckpoint payloads from canonical evidence (PR3).

Additive reads from the generic events table — no physical schema bump.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping, Sequence

from app.opip.contracts.enums import RestartState
from app.opip.contracts.events import FEATURE_CHECKPOINT_RECORDED
from app.opip.contracts.features import FeatureStateCheckpoint
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.contracts.temporal import require_utc
from app.opip.features.state import RollingState, from_checkpoint

#: SQLite VM instruction interval for the deadline progress handler. Our explicit
#: engineering constant (NOT a SQLite default): setup deadlines are measured in
#: seconds, so 1000 keeps Python out of the VM hot path while still making a long
#: scan/sort deadline-interruptible at a fine enough granularity for the release
#: budget. PRIVATE: this is internal tuning, not public API. A focused test may
#: monkeypatch this module attribute to 1 for determinism.
_SQLITE_DEADLINE_PROGRESS_OPS = 1000


class CheckpointIntegrityError(ValueError):
    """Committed checkpoint payload is corrupt or non-reconstructable."""


class CheckpointDeadlineExceeded(TimeoutError):
    """A durable checkpoint read could not finish inside its absolute deadline.

    Raised only when a caller supplied ``deadline_monotonic``. Subclasses
    ``TimeoutError`` so existing timeout handling treats it the same way, while
    staying a DISTINCT type a producer can name in a durable disposition.
    """


def _require_exact_watermark(raw: Any) -> ConsumedInputWatermark:
    """Rebuild the commit watermark, rejecting coerced components.

    A committed payload storing ``history_epoch``/``local_sequence`` as a
    boolean, float, or numeric string would otherwise be silently normalized
    by ``int()`` into a commit position that was never declared as an integer.
    """
    if not isinstance(raw, Mapping):
        raise CheckpointIntegrityError(
            "committed feature checkpoint consumed_input_watermark must be a "
            "JSON object declaring history_epoch and local_sequence"
        )
    for name in ("history_epoch", "local_sequence"):
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise CheckpointIntegrityError(
                "committed feature checkpoint consumed_input_watermark."
                f"{name} must be an exact integer (got {value!r}); refusing "
                "to normalize a coerced commit position"
            )
    return ConsumedInputWatermark(
        history_epoch=raw["history_epoch"],
        local_sequence=raw["local_sequence"],
    )


def checkpoint_from_payload(payload: Mapping[str, Any]) -> FeatureStateCheckpoint:
    """Rebuild a FeatureStateCheckpoint from a committed canonical payload."""
    consumed_input_watermark = _require_exact_watermark(
        payload.get("consumed_input_watermark")
    )
    created = payload.get("created_at_utc")
    created_at = None
    if created is not None:
        created_at = require_utc(
            datetime.fromisoformat(str(created).replace("Z", "+00:00")),
            field_name="created_at_utc",
        )
    return FeatureStateCheckpoint(
        instrument_version_id=str(payload["instrument_version_id"]),
        venue_instrument_id=str(payload["venue_instrument_id"]),
        feature_version=str(payload["feature_version"]),
        consumed_input_watermark=consumed_input_watermark,
        rolling_state=dict(payload.get("rolling_state") or {}),
        restart_state=RestartState(str(payload["restart_state"])),
        reconstruction_dependencies=tuple(
            str(item) for item in (payload.get("reconstruction_dependencies") or ())
        )
        or ("fixed_interval_aggregate:60s",),
        created_at_utc=created_at,
        schema_version=int(payload.get("schema_version") or 1),
    )


def _scan_checkpoint_payloads(
    *,
    db_path: Path | None,
    deadline_monotonic: float | None,
    clock: Callable[[], float] | None,
) -> list[dict[str, Any]]:
    """Scan FEATURE_CHECKPOINT_RECORDED ONCE, in canonical commit order.

    Shared by the single-instrument and batch loaders so both observe the SAME
    query, the SAME ordering and the SAME deadline contract. Returns the raw
    decoded payloads in commit order; per-instrument filtering and validation
    happen in the callers, preserving the existing validation/filter order.
    """
    from app.opip.canonical.schema import connect

    if db_path is None:
        from app.opip.canonical.paths import db_path as default_db_path

        db_path = default_db_path()
    target = Path(db_path)
    if not target.exists():
        return []
    tick = clock or monotonic
    if deadline_monotonic is not None and tick() >= deadline_monotonic:
        raise CheckpointDeadlineExceeded(
            "checkpoint read deadline already elapsed before query"
        )
    conn = connect(target, read_only=True)
    deadline_triggered = False

    def _progress_handler() -> int:
        nonlocal deadline_triggered
        if deadline_monotonic is not None and tick() >= deadline_monotonic:
            deadline_triggered = True
            return 1
        return 0

    try:
        if deadline_monotonic is not None:
            conn.set_progress_handler(_progress_handler, _SQLITE_DEADLINE_PROGRESS_OPS)
        try:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM events
                WHERE event_type = ?
                ORDER BY history_epoch ASC, local_sequence ASC
                """,
                (FEATURE_CHECKPOINT_RECORDED,),
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            if deadline_triggered:
                raise CheckpointDeadlineExceeded(
                    "checkpoint read deadline exceeded during query"
                ) from exc
            raise
    finally:
        if deadline_monotonic is not None:
            try:
                conn.set_progress_handler(None, 0)
            except sqlite3.Error:
                pass
        conn.close()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        # The SQLite progress handler bounds the VM, but fetchall() is followed by
        # Python reconstruction. Bound that work too, with the SAME clock domain,
        # so a large committed history cannot outlive the setup envelope after the
        # query itself returned. No partial result is ever returned.
        if deadline_monotonic is not None and tick() >= deadline_monotonic:
            raise CheckpointDeadlineExceeded(
                "checkpoint read deadline exceeded during row processing"
            )
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise CheckpointIntegrityError(
                "committed feature checkpoint payload_json did not decode to "
                f"a JSON object (got {type(payload).__name__}); refusing to "
                "silently skip malformed canonical evidence"
            )
        # Canonical per-row validation order: identity/version validation happens
        # HERE, in commit order, BEFORE the payload is appended and BEFORE the
        # next canonical row is examined. This preserves the historical failure
        # precedence across rows (AC-018(b)): an earlier malformed-identity row
        # fails before a later differently-malformed row is ever decoded.
        payload_instrument_id = payload.get("instrument_version_id")
        payload_feature_version = payload.get("feature_version")
        if (
            not isinstance(payload_instrument_id, str)
            or not payload_instrument_id.strip()
            or not isinstance(payload_feature_version, str)
            or not payload_feature_version.strip()
        ):
            raise CheckpointIntegrityError(
                "committed feature checkpoint must declare non-empty string "
                "instrument_version_id and feature_version"
            )
        payloads.append(payload)
    return payloads


def _select_latest_checkpoint_payload(
    payloads: list[dict[str, Any]],
    instrument_version_id: str,
    *,
    feature_version: str | None,
    deadline_monotonic: float | None = None,
    tick: Callable[[], float] | None = None,
) -> dict[str, Any] | None:
    """Select the latest matching payload, preserving the existing filter order.

    Validation of the identity/version fields happens BEFORE the target filter,
    exactly as the single-instrument loader did, so a malformed payload for an
    unrelated instrument still fails closed.

    ``deadline_monotonic``/``tick`` (optional) bound the POST-SCAN selection
    loop: the scan's own deadline only covers the query and decode, so this
    selection is new Python work that must stay inside the SAME absolute
    deadline. No partial result is ever returned.
    """
    latest: dict[str, Any] | None = None
    for payload in payloads:
        if deadline_monotonic is not None and tick is not None and tick() >= deadline_monotonic:
            raise CheckpointDeadlineExceeded(
                "checkpoint read deadline exceeded during payload selection"
            )
        # Identity/version validation already happened in canonical commit order
        # inside ``_scan_checkpoint_payloads``; the selector relies on those
        # already-validated payloads rather than re-validating the same payload.
        payload_instrument_id = payload.get("instrument_version_id")
        payload_feature_version = payload.get("feature_version")
        if payload_instrument_id != instrument_version_id:
            continue
        if (
            feature_version is not None
            and payload_feature_version != feature_version
        ):
            continue
        latest = payload
    return latest


def load_latest_checkpoint_payload(
    instrument_version_id: str,
    db_path: Path | None = None,
    *,
    feature_version: str | None = None,
    deadline_monotonic: float | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any] | None:
    """Latest committed checkpoint for one instrument_version_id, or None.

    When ``feature_version`` is provided, older-version checkpoints are ignored
    so a feature-engine bump cold-starts instead of restoring incompatible
    retained-window assumptions under a new snapshot stamp.

    Structurally malformed committed checkpoint payloads fail closed before
    identity/version filtering so corrupt history cannot be hidden behind an
    older apparently valid checkpoint.

    ``deadline_monotonic`` (optional) bounds the durable read: it MUST be
    expressed in the same clock domain as ``clock`` (default ``time.monotonic``).
    The SQLite VM is interrupted via a progress handler once the deadline
    elapses, so a long scan/sort cannot silently continue past it. No partial
    state is ever returned.
    """
    payloads = _scan_checkpoint_payloads(
        db_path=db_path,
        deadline_monotonic=deadline_monotonic,
        clock=clock,
    )
    return _select_latest_checkpoint_payload(
        payloads,
        instrument_version_id,
        feature_version=feature_version,
        deadline_monotonic=deadline_monotonic,
        tick=clock or monotonic,
    )


def load_latest_checkpoint_payloads_batch(
    instrument_version_ids: Sequence[str],
    db_path: Path | None = None,
    *,
    feature_version: str | None = None,
    deadline_monotonic: float | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Latest committed checkpoint per requested instrument, in ONE scan.

    Scans FEATURE_CHECKPOINT_RECORDED exactly once for the whole batch and
    reconstructs each requested instrument's latest payload from that single
    ordered scan. The per-instrument validation/filter order is IDENTICAL to
    :func:`load_latest_checkpoint_payload`: identity/version validation happens
    before the target filter, so a malformed payload for an unrelated instrument
    still fails closed.

    The returned mapping contains an entry ONLY for instruments that have a
    matching committed checkpoint; a requested instrument with no checkpoint is
    simply absent (mirroring the single-instrument ``None`` return).

    ``deadline_monotonic``/``clock`` follow the same absolute-deadline contract
    as the single-instrument loader. No partial mapping is ever returned: an
    integrity or deadline failure raises before the caller sees any result.
    """
    requested = list(dict.fromkeys(str(item) for item in instrument_version_ids))
    if not requested:
        return {}
    payloads = _scan_checkpoint_payloads(
        db_path=db_path,
        deadline_monotonic=deadline_monotonic,
        clock=clock,
    )
    tick = clock or monotonic
    # O(1) target membership: a set is built ONCE for the whole batch so the
    # per-row target filter is not an O(N) list scan over every historical
    # checkpoint row.
    requested_set = set(requested)
    latest_by_instrument: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        # The scan's deadline covers the query and decode; this post-scan
        # identity/version selection is new Python work that must stay inside the
        # SAME absolute deadline. No partial mapping is ever returned.
        if deadline_monotonic is not None and tick() >= deadline_monotonic:
            raise CheckpointDeadlineExceeded(
                "checkpoint read deadline exceeded during batch payload selection"
            )
        # Identity/version validation already happened in canonical commit order
        # inside ``_scan_checkpoint_payloads``; the selector relies on those
        # already-validated payloads rather than re-validating the same payload.
        payload_instrument_id = payload.get("instrument_version_id")
        payload_feature_version = payload.get("feature_version")
        if payload_instrument_id not in requested_set:
            continue
        if (
            feature_version is not None
            and payload_feature_version != feature_version
        ):
            continue
        latest_by_instrument[payload_instrument_id] = payload
    return latest_by_instrument


def load_rolling_state(
    instrument_version_id: str,
    db_path: Path | None = None,
    *,
    feature_version: str | None = None,
    deadline_monotonic: float | None = None,
    clock: Callable[[], float] | None = None,
) -> RollingState | None:
    """Resume RollingState from the latest committed checkpoint, if any.

    ``deadline_monotonic``/``clock`` are forwarded to the durable read; see
    :func:`load_latest_checkpoint_payload` for the clock-domain invariant.
    """
    from app.opip.features.engine import FEATURE_VERSION

    required_version = FEATURE_VERSION if feature_version is None else feature_version
    payload = load_latest_checkpoint_payload(
        instrument_version_id,
        db_path=db_path,
        feature_version=required_version,
        deadline_monotonic=deadline_monotonic,
        clock=clock,
    )
    if payload is None:
        return None
    return from_checkpoint(checkpoint_from_payload(payload))


def load_rolling_states_batch(
    instrument_version_ids: Sequence[str],
    db_path: Path | None = None,
    *,
    feature_version: str | None = None,
    deadline_monotonic: float | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, RollingState]:
    """Resume RollingState per requested instrument from ONE checkpoint scan.

    Batch analogue of :func:`load_rolling_state`: scans
    FEATURE_CHECKPOINT_RECORDED once and reconstructs each requested
    instrument's RollingState. Instruments with no matching committed checkpoint
    are absent from the returned mapping (mirroring the single-instrument
    ``None`` return). No partial mapping is ever returned on failure.
    """
    from app.opip.features.engine import FEATURE_VERSION

    required_version = FEATURE_VERSION if feature_version is None else feature_version
    payloads = load_latest_checkpoint_payloads_batch(
        instrument_version_ids,
        db_path=db_path,
        feature_version=required_version,
        deadline_monotonic=deadline_monotonic,
        clock=clock,
    )
    return {
        instrument_version_id: from_checkpoint(checkpoint_from_payload(payload))
        for instrument_version_id, payload in payloads.items()
    }


__all__ = [
    "CheckpointDeadlineExceeded",
    "CheckpointIntegrityError",
    "checkpoint_from_payload",
    "load_latest_checkpoint_payload",
    "load_latest_checkpoint_payloads_batch",
    "load_rolling_state",
    "load_rolling_states_batch",
]
