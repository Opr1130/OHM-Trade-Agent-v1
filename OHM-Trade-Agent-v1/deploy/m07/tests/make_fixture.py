"""Build a self-contained synthetic V1 tree for R1 tests (incl. a >2000-file dataset).

Generated from drafts/v1-dataset-spec.json so every required dataset exists.
data/opip/events/segments/ deliberately holds 2600 files to prove the exporter
no longer truncates large datasets.

Canonical: a real store is written by the certified CanonicalWriter; the
generation manifest is marked synthetic because the certified publisher refuses
to run on Windows (durability proof). Export requires --allow-synthetic-canonical.

Run: python tests/make_fixture.py --root <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
LARGE_DATASET_FILES = 2600
CERT_SRC = Path(os.environ.get("OPIP_CERTIFIED_SRC", r"C:\Users\ohmpr\opip-mission-07\src")) / "OHM-Trade-Agent-v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def _make_canonical(data: Path) -> dict:
    if str(CERT_SRC) not in sys.path:
        sys.path.insert(0, str(CERT_SRC))
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION
    from app.opip.canonical.writer import CanonicalWriter

    live = data / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    live.parent.mkdir(parents=True, exist_ok=True)
    writer = CanonicalWriter(live)
    try:
        for idx in range(4):
            writer.submit(WriterIntent(
                schema_version=SCHEMA_VERSION, priority="NORMAL",
                idempotency_key=f"r1:seed:{idx}",
                event_type="alert_governor.transition.recorded",
                payload={"identity": f"EARLY_MOVER:R1-{idx}", "transition_key": f"READY:r1:{idx}",
                         "message_id": 3000 + idx, "created_new": True, "scan_id": "r1-scan"},
                ops_handoff={"operation": "RECORD", "identity": f"EARLY_MOVER:R1-{idx}",
                             "transition_key": f"READY:r1:{idx}", "message_id": 3000 + idx,
                             "created_new": True, "reservation_token": f"r1tok{idx}",
                             "state_family": "early_watch"},
            ))
        writer.checkpoint_wal()
    finally:
        writer.close()

    conn = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        epoch, next_seq = conn.execute("SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1").fetchone()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        max_seq = conn.execute("SELECT COALESCE(MAX(local_sequence),0) FROM events").fetchone()[0]
    finally:
        conn.close()

    backups = live.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    generation = backups / "opip_canonical_v1.gen-R1SYNTH.sqlite3"
    if generation.exists():
        generation.unlink()
    # VACUUM INTO produces a consistent single-file copy in rollback-journal
    # mode, which is the shape the certified restore contract expects.
    conn = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        conn.execute("VACUUM INTO ?", (str(generation),))
    finally:
        conn.close()
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backup_file": generation.name,
        "sha256": _sha(generation),
        "source_release_sha": CERTIFIED_SHA,
        "history_epoch": int(epoch),
        "next_local_sequence": int(next_seq),
        "max_local_sequence": int(max_seq),
        "event_count": int(events),
        "rpo_target_seconds": 300,
        "rto_target_seconds": 1800,
        "synthetic": True,
        "synthetic_note": "R1 TEST FIXTURE - not produced by publish_backup_generation",
    }
    _write(backups / "opip_canonical_v1.gen-R1SYNTH.manifest.json", manifest)
    return manifest
def build(root: Path, spec_path: Path) -> dict:
    root = Path(root)
    data = root / "data"
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    created: list[str] = []

    for dataset in spec["datasets"]:
        if dataset["classification"] in {"EXTERNAL_ANALYTICS", "SECRET_RECONFIGURE", "EPHEMERAL_EXCLUDE"}:
            continue
        rel = dataset.get("source_rel")
        if not rel or rel.startswith(("n/a", "/", "**", "data (")) or dataset["kind"] == "sqlite_native_backup":
            continue
        target = root / rel
        if target.exists():
            continue
        if dataset["kind"] == "dir":
            target.mkdir(parents=True, exist_ok=True)
            _write(target / "synthetic-index.json", {"synthetic": True, "dataset": dataset["id"]})
        elif rel.endswith(".jsonl"):
            _write(target, json.dumps({"synthetic": True, "dataset": dataset["id"],
                                       "timestamp_utc": datetime.now(timezone.utc).isoformat()}) + "\n")
        else:
            _write(target, {"synthetic": True, "dataset": dataset["id"]})
        created.append(rel)

    segments = data / "opip" / "events" / "segments"
    segments.mkdir(parents=True, exist_ok=True)
    for index in range(LARGE_DATASET_FILES):
        (segments / f"segment-{index:05d}.jsonl").write_text(
            json.dumps({"segment": index, "synthetic": True}) + "\n", encoding="utf-8"
        )

    canonical = _make_canonical(data)
    _write(root / "SYNTHETIC-FIXTURE.json", {
        "synthetic": True, "certified_sha": CERTIFIED_SHA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "large_dataset_files": LARGE_DATASET_FILES,
        "contains_no_real_data": True,
    })
    return {"root": str(root), "created": len(created), "canonical": canonical,
            "large_dataset_files": LARGE_DATASET_FILES}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--spec", default=str(Path(__file__).resolve().parents[1] / "drafts" / "v1-dataset-spec.json"))
    args = parser.parse_args()
    print(json.dumps(build(Path(args.root), Path(args.spec)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

