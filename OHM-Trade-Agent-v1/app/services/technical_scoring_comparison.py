"""Bounded, read-only V1/V2 scanner comparisons, never an admission source."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Sequence

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


def record_comparison_fail_soft(row: dict, path: Path = COMPARISON_FILE) -> bool:
    """No errors are propagated to the scanner; discard on I/O failure."""
    try:
        data = (json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(data) > 256 * 1024:
            raise ValueError("comparison record exceeds maximum size")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size + len(data) > MAX_BYTES:
            os.replace(path, path.with_suffix(".1.jsonl"))
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return True
    except Exception as exc:
        logger.warning("V2 comparison capture failed; production unchanged: %s", type(exc).__name__)
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
