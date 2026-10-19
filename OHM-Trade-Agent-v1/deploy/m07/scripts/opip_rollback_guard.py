"""R2 rollback guard: WAL-safe snapshot + full history comparison.

Corrects D03 (a raw `cp -a` of a WAL-mode SQLite directory is not a consistent
snapshot) and D07 (comparing only the history epoch misses writes committed at
the same epoch).

Snapshot: SQLite's online backup API, then integrity + foreign-key verification
on the produced file, then a SHA-256 of the snapshot.
Compare : refuse when the live database is newer in epoch, sequence OR event
count, or when its integrity/FK checks fail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _facts(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        epoch, seq = conn.execute("SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1").fetchone()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()
    return {"history_epoch": int(epoch), "next_local_sequence": int(seq), "event_count": int(events),
            "integrity_check": integrity, "foreign_key_violations": fk}


def snapshot_database(db: Path, out: Path) -> dict:
    """Consistent snapshot through the SQLite backup API (WAL-aware)."""
    db, out = Path(db), Path(out)
    if not db.is_file():
        raise SystemExit(f"refusing snapshot: source database missing: {db}")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise SystemExit(f"refusing snapshot: destination exists: {out}")
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(out)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    facts = _facts(out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {"snapshot": str(out), "sha256": digest, "taken_at_utc": _utc(), **facts}


def compare_history(*, plan: dict, live: dict) -> dict:
    """Refuse newer-or-equal-epoch-but-newer-sequence/count history."""
    problems: list[str] = []
    plan_live = plan.get("live_at_prepare") or {}
    for key in ("history_epoch", "next_local_sequence", "event_count"):
        planned = int(plan_live.get(key, -1))
        current = int(live.get(key, -1))
        if current < 0 or planned < 0:
            problems.append(f"{key} unavailable (plan={plan_live.get(key)} live={live.get(key)})")
        elif current > planned:
            problems.append(f"{key} advanced since prepare (live={current} plan={planned})")
    if live.get("integrity_check") != "ok":
        problems.append(f"live integrity_check={live.get('integrity_check')}")
    if int(live.get("foreign_key_violations", -1)) != 0:
        problems.append(f"live foreign_key_violations={live.get('foreign_key_violations')}")
    return {"verdict": "PASS" if not problems else "REFUSED", "problems": problems,
            "plan": plan_live, "live": live}


def writer_gate(*, receipt_path: Path, db: Path, max_age_hours: float = 24.0) -> dict:
    """Refuse canonical-writer start unless a PASS receipt is bound to this DB."""
    receipt_path, db = Path(receipt_path), Path(db)
    problems: list[str] = []
    if not receipt_path.is_file():
        return {"verdict": "REFUSED", "problems": [f"recovery receipt missing: {receipt_path}"]}
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("completed") is not True or receipt.get("verdict") != "PASS":
        problems.append(f"receipt is not a completed PASS (completed={receipt.get('completed')}, verdict={receipt.get('verdict')})")
    recorded_dest = str(receipt.get("destination_db") or "")
    if recorded_dest and Path(recorded_dest).resolve() != db.resolve():
        problems.append(f"receipt destination {recorded_dest} != live db {db.resolve()}")
    if not recorded_dest:
        problems.append("receipt does not record a destination database")
    restored = receipt.get("restored") or {}
    if not restored:
        problems.append("receipt does not record restored database facts")
    if receipt.get("integrity_check") not in (None, "ok"):
        problems.append(f"receipt integrity_check={receipt.get('integrity_check')}")
    if int(receipt.get("foreign_key_violations") or 0) != 0:
        problems.append(f"receipt foreign_key_violations={receipt.get('foreign_key_violations')}")
    try:
        taken = datetime.fromisoformat(str(receipt.get("restored_at_utc", "")).replace("Z", "+00:00"))
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - taken).total_seconds() / 3600.0
        if age_hours > max_age_hours:
            problems.append(f"receipt is stale ({age_hours:.1f}h > {max_age_hours}h)")
    except ValueError:
        problems.append("receipt has no usable restored_at_utc")
    if not db.is_file():
        problems.append(f"live database missing: {db}")
        return {"verdict": "REFUSED", "problems": problems, "receipt": str(receipt_path)}
    live = _facts(db)
    if live["integrity_check"] != "ok":
        problems.append(f"live integrity_check={live['integrity_check']}")
    if live["foreign_key_violations"] != 0:
        problems.append(f"live foreign_key_violations={live['foreign_key_violations']}")
    for key in ("history_epoch", "next_local_sequence", "event_count"):
        if int(restored.get(key, -1)) != int(live[key]):
            problems.append(f"{key} mismatch: receipt={restored.get(key)} live={live[key]}")
    return {"verdict": "PASS" if not problems else "REFUSED", "problems": problems,
            "receipt": str(receipt_path), "live": live}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R2 rollback guard")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot")
    snap.add_argument("--db", required=True)
    snap.add_argument("--out", required=True)

    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--plan", required=True)
    cmp_.add_argument("--db", required=True)

    wg = sub.add_parser("writer-gate")
    wg.add_argument("--receipt", required=True)
    wg.add_argument("--db", required=True)
    wg.add_argument("--max-age-hours", type=float, default=24.0)

    args = parser.parse_args(argv)

    if args.command == "writer-gate":
        result = writer_gate(receipt_path=Path(args.receipt), db=Path(args.db),
                             max_age_hours=args.max_age_hours)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["verdict"] == "PASS" else 3

    if args.command == "snapshot":
        result = snapshot_database(Path(args.db), Path(args.out))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["integrity_check"] == "ok" and result["foreign_key_violations"] == 0 else 1

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    live = _facts(Path(args.db))
    result = compare_history(plan=plan, live=live)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 3


if __name__ == "__main__":
    sys.exit(main())
