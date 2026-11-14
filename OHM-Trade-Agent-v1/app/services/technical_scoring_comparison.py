"""Bounded, read-only V1/V2 scanner comparisons, never an admission source."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Sequence

from app.services.registry_io import registry_lock
from app.scanner.short_technical_scorer import score_short_snapshot
from app.scanner.directional_candidates import select_directional_candidates

logger = logging.getLogger(__name__)
COMPARISON_FILE = Path("/app/data/opip/qualification/scoring_comparison.jsonl")
MAX_BYTES = 8 * 1024 * 1024


def _v2_shortlist(snapshots: Sequence[Any], *, threshold: float = 75.0) -> list[dict]:
    best: dict[str, dict] = {}
    for snap in snapshots:
        options = [
            (float(value), direction)
            for direction, value in (
                ("LONG", getattr(snap, "v2_long_score", None)),
                ("SHORT", getattr(snap, "v2_short_score", None)),
            )
            if isinstance(value, (float, int)) and not isinstance(value, bool)
            and threshold <= float(value) <= 100.0
        ]
        if not options:
            continue
        score, direction = max(options, key=lambda item: (item[0], item[1] == "LONG"))
        underlying = str(getattr(snap, "underlying_asset", None) or snap.symbol)
        candidate = {"symbol": str(snap.symbol), "underlying": underlying, "direction": direction, "score": score}
        if underlying not in best or best[underlying]["score"] < score:
            best[underlying] = candidate
    ordered = sorted(best.values(), key=lambda c: (-c["score"], c["symbol"], c["direction"]))
    chosen: list[dict] = []
    by_direction = {"LONG": 0, "SHORT": 0}
    for item in ordered:
        if by_direction[item["direction"]] >= 5:
            continue
        chosen.append(item)
        by_direction[item["direction"]] += 1
        if len(chosen) >= 8:
            break
    return chosen


def build_scoring_comparison(
    snapshots: Sequence[Any],
    production_selected: Sequence[Any],
    *,
    scan_id: str,
    observed_at: datetime,
    v2_threshold: float = 75.0,
    production_version: str = "v1",
) -> dict:
    """Compare actual selected scorer to its independently computed shadow."""
    if production_version not in {"v1", "v2"}:
        raise ValueError("INVALID_COMPARISON_SCORING_VERSION")
    v1_selection = (
        production_selected if production_version == "v1"
        else select_directional_candidates(list(snapshots))
    )
    v1 = [
        {"symbol": str(x.symbol), "direction": str(x.trade_direction), "score": int(x.technical_score)}
        for x in v1_selection[:8]
    ]
    v2 = (
        _v2_shortlist(snapshots, threshold=v2_threshold)
        if production_version == "v1"
        else [
            {"symbol": str(x.symbol), "direction": str(x.trade_direction),
             "score": int(x.technical_score)}
            for x in production_selected[:8]
        ]
    )
    v1_pairs = {(x["symbol"], x["direction"]) for x in v1}
    v2_pairs = {(x["symbol"], x["direction"]) for x in v2}
    by_symbol = []
    symbols = {
        str(x["symbol"]) for x in v1 + v2
    }
    for snap in sorted(snapshots, key=lambda x: str(x.symbol)):
        if str(snap.symbol) not in symbols:
            continue
        by_symbol.append({
            "symbol": str(snap.symbol),
            "v1_long": int(snap.technical_score),
            "v1_short": int(score_short_snapshot(snap).score),
            "v2_long": getattr(snap, "v2_long_score", None),
            "v2_short": getattr(snap, "v2_short_score", None),
            "v2_status": getattr(snap, "v2_feature_status", "NOT_COMPUTED"),
        })
    return {
        "schema_version": 1,
        "scan_id": str(scan_id),
        "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
        "model_authority": ("V1_PRODUCTION_V2_OBSERVATION" if production_version == "v1"
                            else "V2_PRODUCTION_V1_OBSERVATION"),
        "v1_threshold": 80,
        "v2_threshold": v2_threshold,
        "scanned": len(snapshots),
        "v2_ready": sum(getattr(x, "v2_feature_status", "") == "READY" for x in snapshots),
        "v1_top": v1,
        "v2_top": v2,
        "v1_only": sorted(list(v1_pairs - v2_pairs)),
        "v2_only": sorted(list(v2_pairs - v1_pairs)),
        "candidates": by_symbol[:16],
        "outcome_status": "NOT_MATURED",
    }


COMPARISON_LOCK_TIMEOUT_S = 2.0
COMPARISON_WRITE_STATS: dict[str, Any] = {
    "written": 0, "dropped": 0, "rotation_unavailable": 0, "last_error": None,
}  # process-local: resets on restart; not durable drop accounting


class RotationUnavailable(OSError):
    """Hard-link rotation is not possible here; evidence must stay in place."""


def comparison_write_stats() -> dict[str, Any]:
    """Counters so a dropped comparison record is observable, never silent."""
    return dict(COMPARISON_WRITE_STATS)


def comparison_archive_stats(path: Path = COMPARISON_FILE) -> dict[str, Any]:
    """Archive count and bytes, so disk growth is observable (read-only)."""
    sizes = []
    for item in _sibling_archives(path):
        try:
            sizes.append(item.stat().st_size)
        except OSError:
            continue
    hot = path.stat().st_size if path.exists() else 0
    return {"hot_bytes": hot, "archive_count": len(sizes), "archive_bytes": sum(sizes),
            "max_bytes": MAX_BYTES}


def _fsync_dir(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("comparison record write made no progress")
        view = view[written:]


def _sibling_archives(path: Path) -> list[Path]:
    return [
        item for item in path.parent.glob(f"{path.stem}.*{path.suffix}")
        if item != path and item.is_file()
    ]


def _recover_half_rotation(path: Path) -> None:
    """Finish a rotation that died between link and unlink (lock held).

    The hot name and an archive then share one inode, so the data is already
    safe under the archive name and only the hot name is removed.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return
    if stat.st_nlink < 2:
        return
    for archive in _sibling_archives(path):
        other = archive.stat()
        if (other.st_dev, other.st_ino) == (stat.st_dev, stat.st_ino):
            os.unlink(path)
            _fsync_dir(path.parent)
            return


def _rotate_locked(path: Path, now: datetime) -> None:
    """Move the hot file to a unique archive name without ever overwriting."""
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n = 0
    while True:
        archive = path.with_name(f"{path.stem}.{stamp}.{n}{path.suffix}")
        try:
            os.link(path, archive)
            break
        except FileExistsError:
            n += 1
        except OSError as exc:
            # No portable no-replace rename exists: rename() would silently
            # replace a file a non-cooperating actor created after any check.
            # Fail closed; the caller keeps appending to the hot file.
            raise RotationUnavailable(type(exc).__name__) from exc
    _fsync_dir(path.parent)
    os.unlink(path)
    _fsync_dir(path.parent)


def _append_locked(data: bytes, path: Path) -> None:
    _recover_half_rotation(path)
    fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        size = os.fstat(fd).st_size
        if size and size + len(data) > MAX_BYTES:
            os.fsync(fd)
            os.close(fd)
            fd = -1
            try:
                _rotate_locked(path, datetime.now(timezone.utc))
                size = 0
            except RotationUnavailable as exc:
                # Never overwrite and never drop: keep appending to the hot file.
                COMPARISON_WRITE_STATS["rotation_unavailable"] += 1
                logger.warning("comparison rotation unavailable (%s); hot file keeps growing", exc)
            fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
            if size == 0:
                size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            # A torn tail from an earlier crash stays on its own line.
            data = b"\n" + data
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        if fd >= 0:
            os.close(fd)


def record_comparison_fail_soft(row: dict, path: Path = COMPARISON_FILE) -> bool:
    """Lock-protected, fsynced append. Never raises into the scanner.

    Rotation never overwrites: archives are created with a no-replace hard link
    under a unique name, the legacy ``.1.jsonl`` is never touched, and if links
    are unavailable the hot file keeps growing instead of being renamed. The
    lock serialises cooperating writers only (same host and mount). A dropped
    record (for example lock timeout) is logged and counted in process-local
    counters that reset on restart; that is observability, not durable accounting.
    """
    try:
        data = (json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(data) > 256 * 1024:
            raise ValueError("comparison record exceeds maximum size")
        path.parent.mkdir(parents=True, exist_ok=True)
        with registry_lock(
            path.with_name(path.name + ".lock"), timeout=COMPARISON_LOCK_TIMEOUT_S
        ):
            _append_locked(data, path)
        COMPARISON_WRITE_STATS["written"] += 1
        return True
    except Exception as exc:
        COMPARISON_WRITE_STATS["dropped"] += 1
        COMPARISON_WRITE_STATS["last_error"] = type(exc).__name__
        logger.warning(
            "V2 comparison capture failed; production unchanged: %s (dropped=%d)",
            type(exc).__name__, COMPARISON_WRITE_STATS["dropped"],
        )
        return False



def read_recent_comparisons(path: Path = COMPARISON_FILE, limit: int = 12) -> dict:
    """Bounded tail read for an authenticated read-only dashboard."""
    if not path.exists():
        return {"status": "NO_EVIDENCE", "rows": []}
    limit = max(1, min(int(limit), 12))
    try:
        with path.open("rb") as fd:
            fd.seek(0, os.SEEK_END)
            size = fd.tell()
            fd.seek(max(0, size - 256 * 1024))
            data = fd.read(256 * 1024)
        lines = data.splitlines()
        if size > 256 * 1024:
            lines = lines[1:]
        rows = []
        for line in lines[-limit:]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
            except (ValueError, UnicodeDecodeError):
                continue
        return {"status": "RECORDED" if rows else "NO_EVIDENCE", "rows": list(reversed(rows))}
    except OSError:
        return {"status": "UNAVAILABLE", "rows": []}
