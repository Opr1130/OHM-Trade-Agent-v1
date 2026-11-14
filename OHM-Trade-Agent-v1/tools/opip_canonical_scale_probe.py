"""Canonical-store scale probe (read-only measurement tooling; no production authority).

Answers two release-gate questions for the canonical writer scale work (#516):

* how long does the first writer open take on a store that predates the two
  additive identity indexes (i.e. the one-time index build at deploy), and
* what is the writer's peak resident memory while it hydrates its projections.

It never touches a live store: ``build`` writes a *synthetic* store at a path you
choose, while ``measure`` and ``indexes`` ALWAYS work on an isolated snapshot taken
with SQLite's online backup API (so committed-but-uncheckpointed WAL frames are
included, the source is opened read-only and never checkpointed, and the snapshot's
integrity and per-table row counts are verified before it is used). All three refuse
live-looking canonical paths. Run ``measure`` once per code tree (``OPIP_PROBE_CODE_ROOT``)
to get a before/after comparison on identical data.

    python tools/opip_canonical_scale_probe.py build   --db /tmp/p/c.sqlite3 --rows 1000000
    python tools/opip_canonical_scale_probe.py measure --db /tmp/p/c.sqlite3
    python tools/opip_canonical_scale_probe.py indexes --db /tmp/p/c.sqlite3 --prepare

``indexes`` refuses to report a build time for an index that already existed: pass
``--prepare`` to drop the target indexes in the isolated snapshot first (never in the
source), so the time reported is a real ``CREATE INDEX``.

``build`` needs the repository test helpers (it reuses the F4 fixtures so every
row is a *valid* lifecycle transition); ``measure`` and ``indexes`` do not.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_LIVE_MARKERS = ("/app/data", "opip_canonical_live")

# name -> CREATE statement (no IF NOT EXISTS: the probe must know it really built it).
# Kept equal to the production definitions by tests/test_opip_probe_snapshot_v1.py.
INDEXES: dict[str, str] = {
    "idx_events_role_result_identity": (
        "CREATE INDEX idx_events_role_result_identity "
        "ON events(json_extract(payload_json, '$.result_id')) "
        "WHERE event_type = 'decision_intelligence.role_result.recorded'"
    ),
    "idx_events_episode_identity": (
        "CREATE INDEX idx_events_episode_identity "
        "ON events(json_extract(payload_json, '$.episode.episode_id'), "
        "history_epoch, local_sequence) "
        "WHERE event_type = 'opportunity_lifecycle.transition.recorded'"
    ),
    "idx_events_di_request_state": (
        "CREATE INDEX idx_events_di_request_state "
        "ON events(json_extract(payload_json, '$.request_id'), "
        "history_epoch, local_sequence) "
        "WHERE event_type = 'decision_intelligence.transition.recorded'"
    ),
}


class SnapshotError(RuntimeError):
    """A reliable, verified snapshot could not be obtained (always fail closed)."""


def _code_root() -> str:
    """Code tree to measure: ``OPIP_PROBE_CODE_ROOT`` or this checkout."""
    return os.environ.get("OPIP_PROBE_CODE_ROOT") or str(Path(__file__).resolve().parents[1])


def _refuse_live(path: Path) -> None:
    text = str(path.resolve())
    if any(marker in text for marker in _LIVE_MARKERS):
        raise SystemExit(f"refusing to touch a live-looking path: {text}")


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB, macOS bytes.
    return peak / 1024.0 if sys.platform != "darwin" else peak / (1024.0 * 1024.0)


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _table_counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    return {
        name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        for name in tables
    }


def snapshot_database(source: Path, destination: Path) -> dict:
    """Take a verified, self-contained snapshot of ``source`` at ``destination``.

    * SQLite's online backup API, so committed transactions that still live only in
      uncheckpointed WAL frames are included (a raw file copy silently omits them).
    * The source is opened ``mode=ro`` and is never written, checkpointed or
      locked beyond an ordinary read transaction; live writers keep committing.
    * The per-table counts are taken inside the same read transaction the backup
      reads from, so source and destination describe one snapshot even while the
      source is being written.
    * ``integrity_check`` and the count comparison must both pass; otherwise the
      destination is removed and :class:`SnapshotError` is raised. There is no
      degraded fallback to a file copy.
    """
    _refuse_live(source)
    _refuse_live(destination)
    if not source.is_file():
        raise SnapshotError(f"source database does not exist: {source}")
    if destination.exists():
        raise SnapshotError(f"refusing to overwrite an existing snapshot: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = dst = None
    try:
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30.0)
        src.execute("BEGIN")
        tables = _user_tables(src)
        if "events" not in tables:
            raise SnapshotError("source has no events table; not a canonical store")
        source_counts = _table_counts(src, tables)
        dst = sqlite3.connect(str(destination))
        src.backup(dst)
        dst.commit()
        # Make the copy a single self-contained file: no sidecars to lose or confuse.
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst.execute("PRAGMA journal_mode=DELETE")
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SnapshotError(f"snapshot integrity_check failed: {integrity}")
        destination_counts = _table_counts(dst, _user_tables(dst))
        if destination_counts != source_counts:
            raise SnapshotError(
                f"snapshot counts differ: source={source_counts} "
                f"destination={destination_counts}"
            )
        return {
            "snapshot": str(destination),
            "tables": source_counts,
            "integrity": integrity,
            "events": source_counts["events"],
        }
    except SnapshotError:
        _discard(src, dst, destination)
        raise
    except Exception as exc:  # noqa: BLE001 - every failure must fail closed
        _discard(src, dst, destination)
        raise SnapshotError(f"could not obtain a reliable snapshot: {exc}") from exc
    finally:
        for conn in (src, dst):
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass


def _discard(src, dst, destination: Path) -> None:
    for conn in (src, dst):
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(destination) + suffix)
        if candidate.exists():
            candidate.unlink()


def _isolated_copy(db: Path, workdir: str | None) -> tuple[Path, dict, tempfile.TemporaryDirectory | None]:
    """Snapshot ``db`` into a private directory; the caller removes it unless kept."""
    _refuse_live(db)
    holder = None
    if workdir:
        base = Path(workdir)
        base.mkdir(parents=True, exist_ok=True)
        target_dir = Path(tempfile.mkdtemp(prefix="probe-", dir=str(base)))
    else:
        holder = tempfile.TemporaryDirectory(prefix="opip-probe-")
        target_dir = Path(holder.name)
    target = target_dir / "snapshot.sqlite3"
    try:
        info = snapshot_database(db, target)
    except SnapshotError as exc:
        raise SystemExit(f"SNAPSHOT_FAILED: {exc}") from exc
    return target, info, holder


def cmd_build(args: argparse.Namespace) -> dict:
    """Write a synthetic store of valid opportunity-lifecycle transitions."""
    db = Path(args.db)
    _refuse_live(db)
    sys.path.insert(0, _code_root())
    from app.opip.canonical import writer as writer_module
    from app.opip.canonical.paths import EVENT_SCHEMA_VERSION
    from app.opip.contracts import opportunity_persistence as persistence
    from tests.test_opip_r3_f4_opportunity_persistence import (
        build_claim,
        canonical_intent,
        deferred_result,
        expired_result,
        payload_for,
    )

    if db.exists():
        db.unlink()
    db.parent.mkdir(parents=True, exist_ok=True)
    os.environ["OPIP_CANONICAL_DIR"] = str(db.parent)
    writer = writer_module.CanonicalWriter(db)
    conn = writer._conn
    started = time.monotonic()
    rows = 0
    episode = 0
    sequence = 1
    now = "2026-01-01T00:00:00+00:00"
    conn.execute("BEGIN")
    while rows < args.rows:
        claim = build_claim(snapshot_id=f"SNAP:probe-{episode}")
        results = [deferred_result(claim)]
        if episode % 2 == 0:
            results.append(expired_result(claim))
        for result in results:
            payload = payload_for(result)
            intent = canonical_intent(payload)
            event_id = f"probe-{sequence:012d}"
            conn.execute(
                "INSERT INTO events (event_id, schema_version, event_type, "
                "history_epoch, local_sequence, recorded_at, event_time, "
                "causation_id, correlation_id, idempotency_key, payload_json) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    EVENT_SCHEMA_VERSION,
                    intent.event_type,
                    sequence,
                    now,
                    intent.event_time,
                    intent.causation_id,
                    intent.correlation_id,
                    intent.idempotency_key,
                    writer._serialized_committed_payload(intent.event_type, payload),
                ),
            )
            conn.execute(
                "INSERT INTO idempotency_keys (idempotency_key, event_id, committed_at) "
                "VALUES (?, ?, ?)",
                (intent.idempotency_key, event_id, now),
            )
            sequence += 1
            rows += 1
        episode += 1
        if rows % 50000 < 2:
            conn.commit()
            conn.execute("BEGIN")
    conn.execute(
        "UPDATE meta SET next_local_sequence = ? WHERE id = 1", (sequence,)
    )
    conn.commit()
    writer.close()
    del persistence
    return {
        "op": "build",
        "rows": rows,
        "episodes": episode,
        "seconds": round(time.monotonic() - started, 1),
        "db_mb": round(db.stat().st_size / 1048576, 1),
    }


def _instrument(writer_module) -> dict:
    """Wrap the writer's schema and hydration steps so each phase is timed alone."""
    timings: dict[str, float] = {}

    def wrap(owner, attr: str, label: str) -> None:
        original = getattr(owner, attr, None)
        if original is None:
            return

        def timed(*a, **k):
            t0 = time.monotonic()
            try:
                return original(*a, **k)
            finally:
                timings[label] = timings.get(label, 0.0) + (time.monotonic() - t0)

        setattr(owner, attr, timed)

    wrap(writer_module, "initialize_schema", "schema_initialization_seconds")
    cls = writer_module.CanonicalWriter
    wrap(cls, "_hydrate_request_lifecycle_projection", "hydrate_request_seconds")
    wrap(cls, "_hydrate_role_result_identity_projection", "hydrate_role_result_seconds")
    wrap(cls, "_hydrate_opportunity_transition_projection", "hydrate_opportunity_seconds")
    return timings


def cmd_measure(args: argparse.Namespace) -> dict:
    """Open a writer on an isolated snapshot; report phase timings and peak RSS."""
    db, snapshot, holder = _isolated_copy(Path(args.db), args.workdir)
    sys.path.insert(0, _code_root())
    os.environ["OPIP_CANONICAL_DIR"] = str(db.parent)
    from app.opip.canonical import writer as writer_module

    timings = _instrument(writer_module)
    baseline = _peak_rss_mb()
    started = time.monotonic()
    writer = writer_module.CanonicalWriter(db)
    opened = time.monotonic() - started
    peak = _peak_rss_mb()
    phases = {k: round(v, 2) for k, v in sorted(timings.items())}
    accounted = sum(timings.values())
    out = {
        "op": "measure",
        "code": str(Path(writer_module.__file__).resolve().parents[3]),
        "snapshot_events": snapshot["events"],
        "open_seconds": round(opened, 2),
        # Index creation, store lock and connection are in the remainder; the
        # ``indexes`` command measures index creation alone.
        "phases": {**phases, "other_init_seconds": round(max(0.0, opened - accounted), 2)},
        "baseline_rss_mb": round(baseline, 1),
        "peak_rss_mb": round(peak, 1),
        "retained": {
            "opportunity": len(
                getattr(writer, "_opportunity_transition_projection", {})
            ),
            "request": len(getattr(writer, "_request_lifecycle_projection", {})),
        },
    }
    if args.rederive:
        # Latency of answering for an identity the bounded projection released.
        from app.opip.contracts import opportunity_persistence as persistence
        from tests.test_opip_r3_f4_opportunity_persistence import (
            active_result,
            build_claim,
            payload_for,
        )

        samples = []
        for index in range(0, args.rederive * 2, 2):
            claim = build_claim(snapshot_id=f"SNAP:probe-{index}")
            episode_id = persistence.opportunity_transition_episode_id(
                payload_for(active_result(claim))
            )
            for name in (
                "_opportunity_transition_projection",
                "_opportunity_transition_count",
                "_opportunity_transition_last",
            ):
                getattr(writer, name).pop(episode_id, None)
            t0 = time.perf_counter()
            writer._opportunity_episode_state(episode_id)
            samples.append((time.perf_counter() - t0) * 1000.0)
        samples.sort()
        out["rederive_ms"] = {
            "n": len(samples),
            "p50": round(samples[len(samples) // 2], 3),
            "p95": round(samples[int(len(samples) * 0.95) - 1], 3),
            "max": round(samples[-1], 3),
        }
    writer.close()
    if holder is not None:
        holder.cleanup()
    return out


def _existing_indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }


def cmd_indexes(args: argparse.Namespace) -> dict:
    """Time real ``CREATE INDEX`` statements on an isolated snapshot.

    A time is reported only for an index this command actually created. An index
    that already existed is reported as such with no time, and if nothing was
    created the command refuses (exit 2) rather than print a number that would
    describe the cost of a no-op.
    """
    db, snapshot, holder = _isolated_copy(Path(args.db), args.workdir)
    conn = sqlite3.connect(str(db))
    try:
        present = _existing_indexes(conn)
        dropped: list[str] = []
        if args.prepare:
            # Only ever in the isolated snapshot: the source is read-only above.
            for name in INDEXES:
                if name in present:
                    conn.execute(f"DROP INDEX {name}")
                    dropped.append(name)
            conn.commit()
            present = _existing_indexes(conn)
        before = db.stat().st_size
        results: dict[str, dict] = {}
        created = 0
        for name, sql in INDEXES.items():
            if name in present:
                results[name] = {"status": "ALREADY_EXISTS", "build_seconds": None}
                continue
            started = time.monotonic()
            conn.execute(sql)
            conn.commit()
            elapsed = time.monotonic() - started
            if name not in _existing_indexes(conn):
                raise SystemExit(f"INDEX_NOT_CREATED: {name}")
            results[name] = {"status": "CREATED", "build_seconds": round(elapsed, 2)}
            created += 1
        out = {
            "op": "indexes",
            "snapshot_events": snapshot["events"],
            "prepared_dropped": dropped,
            "indexes": results,
            "created": created,
            "db_growth_mb": round((db.stat().st_size - before) / 1048576, 1),
            "peak_rss_mb": round(_peak_rss_mb(), 1),
        }
        if created == 0:
            print(json.dumps(out, sort_keys=True))
            raise SystemExit(
                "NO_INDEX_CREATED: every target index already exists, so no build "
                "time is reported; rerun with --prepare to measure a real build"
            ) from None
        return out
    finally:
        conn.close()
        if holder is not None:
            holder.cleanup()


def cmd_aux(args: argparse.Namespace) -> dict:
    """Append synthetic role-result / DI-transition rows (index realism only).

    These rows are *not* valid for hydration; the store is for ``indexes`` only.
    """
    db = Path(args.db)
    _refuse_live(db)
    conn = sqlite3.connect(str(db))
    start = conn.execute("SELECT COALESCE(MAX(local_sequence), 0) FROM events").fetchone()[0]
    kinds = (
        ("decision_intelligence.role_result.recorded", "result_id"),
        ("decision_intelligence.transition.recorded", "request_id"),
    )
    conn.execute("BEGIN")
    for offset in range(args.rows):
        kind, key = kinds[offset % 2]
        seq = start + 1 + offset
        payload = json.dumps({key: f"{key}-{offset}", "pad": "x" * 600},
                             separators=(",", ":"))
        conn.execute(
            "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
            "local_sequence, recorded_at, idempotency_key, payload_json) "
            "VALUES (?, 1, ?, 1, ?, '2026-01-01T00:00:00+00:00', ?, ?)",
            (f"aux-{seq:012d}", kind, seq, f"aux-key-{seq}", payload),
        )
        if offset % 50000 == 0:
            conn.commit()
            conn.execute("BEGIN")
    conn.commit()
    conn.close()
    return {"op": "aux", "rows": args.rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build")
    build.add_argument("--db", required=True)
    build.add_argument("--rows", type=int, required=True)
    measure = sub.add_parser("measure")
    measure.add_argument("--db", required=True)
    measure.add_argument("--workdir", default=None)
    measure.add_argument("--rederive", type=int, default=0)
    indexes = sub.add_parser("indexes")
    indexes.add_argument("--db", required=True)
    indexes.add_argument("--workdir", default=None)
    indexes.add_argument("--prepare", action="store_true")
    aux = sub.add_parser("aux")
    aux.add_argument("--db", required=True)
    aux.add_argument("--rows", type=int, required=True)
    args = parser.parse_args(argv)
    result = {"build": cmd_build, "measure": cmd_measure,
              "indexes": cmd_indexes, "aux": cmd_aux}[args.cmd](args)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
