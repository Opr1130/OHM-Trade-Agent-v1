"""Canonical-store scale probe (read-only measurement tooling; no production authority).

Answers two release-gate questions for the canonical writer scale work (#516):

* how long does the first writer open take on a store that predates the two
  additive identity indexes (i.e. the one-time index build at deploy), and
* what is the writer's peak resident memory while it hydrates its projections.

It never touches a live store: ``build`` writes a *synthetic* store at a path you
choose, ``measure`` opens a *copy* you point it at, and both refuse the default
live canonical path. Run ``measure`` once per code tree (``PYTHONPATH``) to get a
before/after comparison on identical data.

    python tools/opip_canonical_scale_probe.py build   --db /tmp/p/c.sqlite3 --rows 1000000
    python tools/opip_canonical_scale_probe.py measure --db /tmp/p/c.sqlite3 --copy
    python tools/opip_canonical_scale_probe.py indexes --db /tmp/p/c.sqlite3 --copy

``build`` needs the repository test helpers (it reuses the F4 fixtures so every
row is a *valid* lifecycle transition); ``measure`` and ``indexes`` do not.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sqlite3
import sys
import time
from pathlib import Path

_LIVE_MARKERS = ("/app/data", "opip_canonical_live")

INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_events_role_result_identity "
    "ON events(json_extract(payload_json, '$.result_id')) "
    "WHERE event_type = 'decision_intelligence.role_result.recorded'",
    "CREATE INDEX IF NOT EXISTS idx_events_episode_identity "
    "ON events(json_extract(payload_json, '$.episode.episode_id'), history_epoch, local_sequence) "
    "WHERE event_type = 'opportunity_lifecycle.transition.recorded'",
    "CREATE INDEX IF NOT EXISTS idx_events_di_request_state "
    "ON events(json_extract(payload_json, '$.request_id'), history_epoch, local_sequence) "
    "WHERE event_type = 'decision_intelligence.transition.recorded'",
)


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


def _working_copy(db: Path, copy: bool) -> Path:
    _refuse_live(db)
    if not copy:
        return db
    target = db.with_name(db.stem + ".probe" + db.suffix)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(target) + suffix)
        if candidate.exists():
            candidate.unlink()
    shutil.copyfile(db, target)
    return target


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


def cmd_measure(args: argparse.Namespace) -> dict:
    """Open a writer on the store and report wall time and peak RSS."""
    db = _working_copy(Path(args.db), args.copy)
    sys.path.insert(0, _code_root())
    os.environ["OPIP_CANONICAL_DIR"] = str(db.parent)
    from app.opip.canonical import writer as writer_module

    baseline = _peak_rss_mb()
    started = time.monotonic()
    writer = writer_module.CanonicalWriter(db)
    opened = time.monotonic() - started
    peak = _peak_rss_mb()
    out = {
        "op": "measure",
        "code": str(Path(writer_module.__file__).resolve().parents[3]),
        "open_seconds": round(opened, 2),
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
    return out


def cmd_indexes(args: argparse.Namespace) -> dict:
    """Time the three additive index builds on a store that lacks them."""
    db = _working_copy(Path(args.db), args.copy)
    conn = sqlite3.connect(str(db))
    out: dict = {"op": "indexes", "events": conn.execute(
        "SELECT COUNT(*) FROM events").fetchone()[0]}
    before = db.stat().st_size
    for sql in INDEX_SQL:
        name = sql.split()[5]
        started = time.monotonic()
        conn.execute(sql)
        conn.commit()
        out[name] = round(time.monotonic() - started, 2)
    out["peak_rss_mb"] = round(_peak_rss_mb(), 1)
    out["db_growth_mb"] = round((db.stat().st_size - before) / 1048576, 1)
    conn.close()
    return out


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
    measure.add_argument("--copy", action="store_true")
    measure.add_argument("--rederive", type=int, default=0)
    indexes = sub.add_parser("indexes")
    indexes.add_argument("--db", required=True)
    indexes.add_argument("--copy", action="store_true")
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
