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


def latest_checkpoint_batch_sql(instrument_count: int) -> str:
    """One statement: an index seek of the latest checkpoint per instrument.

    No ``feature_version`` predicate. A malformed tip stays visible so Python
    can fail closed instead of restoring an older matching checkpoint.
    """
    if instrument_count < 1:
        raise ValueError("instrument_count must be positive")
    arm = (
        "SELECT ? AS instrument_version_id, ("
        "SELECT payload_json FROM events "
        "WHERE event_type = ? "
        "AND json_extract(payload_json, '$.instrument_version_id') = ? "
        "ORDER BY history_epoch DESC, local_sequence DESC LIMIT 1"
        ") AS payload_json"
    )
    return " UNION ALL ".join(arm for _ in range(instrument_count))


def _matching_or_malformed_checkpoint_sql(instrument_count: int) -> str:
    """Latest row that matches the requested version or is not version text.

    Used only after the absolute latest row was a different non-empty version
    string. A non-text or blank version newer than the match still fails closed.
    """
    arm = (
        "SELECT ? AS instrument_version_id, ("
        "SELECT payload_json FROM events "
        "WHERE event_type = ? "
        "AND json_extract(payload_json, '$.instrument_version_id') = ? "
        "AND ("
        "json_type(payload_json, '$.feature_version') IS NULL "
        "OR json_type(payload_json, '$.feature_version') != 'text' "
        "OR json_extract(payload_json, '$.feature_version') = ? "
        "OR json_extract(payload_json, '$.feature_version') = ''"
        ") "
        "ORDER BY history_epoch DESC, local_sequence DESC LIMIT 1"
        ") AS payload_json"
    )
    return " UNION ALL ".join(arm for _ in range(instrument_count))


def _validated_checkpoint_payload(raw: str) -> dict[str, Any]:
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise CheckpointIntegrityError(
            "committed feature checkpoint payload_json did not decode to "
            f"a JSON object (got {type(payload).__name__}); refusing to "
            "silently skip malformed canonical evidence"
        )
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
    return payload


def _scan_latest_checkpoint_payloads(
    *,
    instrument_version_ids: Sequence[str],
    feature_version: str | None,
    db_path: Path | None,
    deadline_monotonic: float | None,
    clock: Callable[[], float] | None,
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Return the latest validated checkpoint payload for each requested instrument.

    One indexed statement seeks the latest committed row per instrument. That
    tip is validated before any older row can be restored. A different
    non-empty version string is skipped, and the next matching-or-malformed
    candidate is sought, so a feature-version bump still cold-starts when no
    matching checkpoint exists. The single-instrument loader keeps the
    full-family scan.
    """
    from app.opip.canonical.schema import connect

    requested = list(dict.fromkeys(str(item) for item in instrument_version_ids))
    if not requested:
        if stats is not None:
            stats["rows_returned"] = 0
        return []
    if db_path is None:
        from app.opip.canonical.paths import db_path as default_db_path

        db_path = default_db_path()
    target = Path(db_path)
    if not target.exists():
        if stats is not None:
            stats["rows_returned"] = 0
        return []
    tick = clock or monotonic
    if deadline_monotonic is not None and tick() >= deadline_monotonic:
        raise CheckpointDeadlineExceeded(
            "checkpoint read deadline already elapsed before query"
        )
    params: list[Any] = []
    for instrument_version_id in requested:
        params.extend(
            (instrument_version_id, FEATURE_CHECKPOINT_RECORDED, instrument_version_id)
        )
    rows = _execute_checkpoint_query(
        target,
        latest_checkpoint_batch_sql(len(requested)),
        params,
        deadline_monotonic=deadline_monotonic,
        tick=tick,
        stats=stats,
    )
    payloads: list[dict[str, Any]] = []
    deferred: list[str] = []
    returned = 0
    for row in rows:
        if deadline_monotonic is not None and tick() >= deadline_monotonic:
            raise CheckpointDeadlineExceeded(
                "checkpoint read deadline exceeded during row processing"
            )
        raw = row["payload_json"]
        if raw is None:
            continue
        returned += 1
        payload = _validated_checkpoint_payload(str(raw))
        if (
            feature_version is not None
            and payload.get("feature_version") != feature_version
        ):
            deferred.append(str(payload["instrument_version_id"]))
            continue
        payloads.append(payload)
    if deferred:
        fallback_params: list[Any] = []
        for instrument_version_id in deferred:
            fallback_params.extend(
                (
                    instrument_version_id,
                    FEATURE_CHECKPOINT_RECORDED,
                    instrument_version_id,
                    feature_version,
                )
            )
        fallback_rows = _execute_checkpoint_query(
            target,
            _matching_or_malformed_checkpoint_sql(len(deferred)),
            fallback_params,
            deadline_monotonic=deadline_monotonic,
            tick=tick,
            stats=stats,
        )
        for row in fallback_rows:
            if deadline_monotonic is not None and tick() >= deadline_monotonic:
                raise CheckpointDeadlineExceeded(
                    "checkpoint read deadline exceeded during row processing"
                )
            raw = row["payload_json"]
            if raw is None:
                continue
            returned += 1
            payloads.append(_validated_checkpoint_payload(str(raw)))
    if stats is not None:
        stats["rows_returned"] = returned
    return payloads


def _execute_checkpoint_query(
    target: Path,
    sql: str,
    params: Sequence[Any],
    *,
    deadline_monotonic: float | None,
    tick: Callable[[], float],
    stats: dict[str, int] | None,
) -> list[Any]:
    from app.opip.canonical.schema import connect

    measure = bool(stats and stats.get("measure_vm_steps"))
    conn = connect(target, read_only=True)
    deadline_triggered = False

    def _progress_handler() -> int:
        nonlocal deadline_triggered
        if stats is not None and measure:
            stats["vm_steps"] = int(stats.get("vm_steps", 0)) + 1
        if deadline_monotonic is not None and tick() >= deadline_monotonic:
            deadline_triggered = True
            return 1
        return 0

    try:
        if deadline_monotonic is not None or measure:
            conn.set_progress_handler(
                _progress_handler,
                1 if measure else _SQLITE_DEADLINE_PROGRESS_OPS,
            )
        try:
            return list(conn.execute(sql, params).fetchall())
        except sqlite3.DatabaseError as exc:
            if deadline_triggered:
                raise CheckpointDeadlineExceeded(
                    "checkpoint read deadline exceeded during query"
                ) from exc
            raise
    finally:
        if deadline_monotonic is not None or measure:
            try:
                conn.set_progress_handler(None, 0)
            except sqlite3.Error:
                pass
        conn.close()


def load_latest_checkpoint_payloads_batch(
    instrument_version_ids: Sequence[str],
    db_path: Path | None = None,
    *,
    feature_version: str | None = None,
    deadline_monotonic: float | None = None,
    clock: Callable[[], float] | None = None,
    stats: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Latest committed checkpoint per requested instrument, in ONE bounded read.

    Seeks the latest FEATURE_CHECKPOINT_RECORDED row for each requested
    instrument. Unrelated instruments are not fetched. The latest row for a
    requested instrument is still validated in commit order across the batch,
    so a malformed tip fails closed. A requested instrument with no matching
    checkpoint is absent (mirroring the single-instrument ``None`` return).

    ``deadline_monotonic``/``clock`` follow the same absolute-deadline contract
    as the single-instrument loader. No partial mapping is ever returned: an
    integrity or deadline failure raises before the caller sees any result.
    ``stats['rows_returned']`` counts rows fetched for this batch when provided.
    """
    requested = list(dict.fromkeys(str(item) for item in instrument_version_ids))
    if not requested:
        if stats is not None:
            stats["rows_returned"] = 0
        return {}
    payloads = _scan_latest_checkpoint_payloads(
        instrument_version_ids=requested,
        feature_version=feature_version,
        db_path=db_path,
        deadline_monotonic=deadline_monotonic,
        clock=clock,
        stats=stats,
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
    stats: dict[str, int] | None = None,
) -> dict[str, RollingState]:
    """Resume RollingState per requested instrument from ONE bounded read.

    Batch analogue of :func:`load_rolling_state`: reads the latest
    FEATURE_CHECKPOINT_RECORDED row for each requested instrument and
    reconstructs that RollingState. Instruments with no matching committed
    checkpoint are absent (mirroring the single-instrument ``None`` return).
    No partial mapping is ever returned on failure.
    """
    from app.opip.features.engine import FEATURE_VERSION

    required_version = FEATURE_VERSION if feature_version is None else feature_version
    payloads = load_latest_checkpoint_payloads_batch(
        instrument_version_ids,
        db_path=db_path,
        feature_version=required_version,
        deadline_monotonic=deadline_monotonic,
        clock=clock,
        stats=stats,
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
