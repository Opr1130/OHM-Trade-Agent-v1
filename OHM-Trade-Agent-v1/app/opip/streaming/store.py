"""Bounded durable shadow storage for Sequence 4 BUILD 4.5."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Iterable

from app.opip.storage.bounded_jsonl import (
    encode_row,
    parse_json_object_line,
    read_lines,
    repair_truncated_tail,
)
from app.opip.streaming.feature_accumulator import StreamingFeatureSnapshot
from app.opip.streaming.telemetry import RuntimeTelemetrySnapshot
from app.services.registry_io import RegistryIOError, load_json, save_json_atomic


STREAMING_DATA_DIR = Path("/app/data/opip/streaming")
TELEMETRY_FILE = STREAMING_DATA_DIR / "telemetry.json"
HEALTH_FILE = STREAMING_DATA_DIR / "health.json"
LATEST_FEATURES_FILE = STREAMING_DATA_DIR / "latest_features.json"
_FEATURE_RE = re.compile(r"^features-(\d{8}T\d{2})\.jsonl$")


def _feature_identity_from_payload(payload: dict) -> str:
    """Deterministic identity for one canonical asset/window feature."""
    asset = str(payload.get("canonical_asset_id") or "").strip()
    start = str(payload.get("window_start_utc") or "").strip()
    end = str(payload.get("window_end_utc") or "").strip()
    if not asset or not start or not end:
        raise ValueError("feature identity requires asset/start/end")
    raw = f"{asset}|{start}|{end}".encode("utf-8")
    return "SF1:" + hashlib.sha256(raw).hexdigest()


def _feature_payload(row: StreamingFeatureSnapshot) -> dict:
    payload = row.as_dict()
    payload["feature_id"] = _feature_identity_from_payload(payload)
    return payload



class StreamingShadowStore:
    """Hourly aggregate JSONL plus atomic read-model files; no raw frames."""

    def __init__(
        self,
        *,
        base_dir: Path = STREAMING_DATA_DIR,
        retention_hours: int = 72,
    ) -> None:
        if int(retention_hours) < 1:
            raise ValueError("retention_hours must be positive")
        self.base_dir = base_dir
        self.retention_hours = int(retention_hours)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def append_features(
        self,
        rows: Iterable[StreamingFeatureSnapshot],
    ) -> int:
        items = tuple(rows)
        if not items:
            return 0
        by_hour: dict[str, list[dict]] = {}
        for row in items:
            stamp = row.window_end_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H")
            by_hour.setdefault(stamp, []).append(_feature_payload(row))

        for stamp, grouped in by_hour.items():
            path = self.base_dir / f"features-{stamp}.jsonl"
            # A previous interrupted attempt may have durably written some
            # rows before a later step failed. Repair only a truncated final
            # row, then use deterministic feature IDs to make retry idempotent.
            repair_truncated_tail(path, parse_line=parse_json_object_line)
            existing_ids: set[str] = set()
            for line in read_lines(path):
                payload = parse_json_object_line(line)
                feature_id = str(payload.get("feature_id") or "").strip()
                if not feature_id:
                    feature_id = _feature_identity_from_payload(payload)
                existing_ids.add(feature_id)

            unique_new: list[dict] = []
            batch_ids: set[str] = set()
            for payload in grouped:
                feature_id = str(payload["feature_id"])
                if feature_id in existing_ids or feature_id in batch_ids:
                    continue
                unique_new.append(payload)
                batch_ids.add(feature_id)

            if unique_new:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o640,
                )
                with os.fdopen(descriptor, "ab", closefd=True) as handle:
                    for payload in unique_new:
                        handle.write(encode_row(payload))
                    handle.flush()
                    os.fsync(handle.fileno())

        latest_path = (
            LATEST_FEATURES_FILE
            if LATEST_FEATURES_FILE.parent == self.base_dir
            else self.base_dir / "latest_features.json"
        )
        try:
            existing = load_json(latest_path)
        except (OSError, TimeoutError, RegistryIOError):
            existing = {}
        latest_by_asset = dict(existing.get("assets") or {})
        for row in sorted(items, key=lambda item: item.window_end_utc):
            latest_by_asset[row.canonical_asset_id] = _feature_payload(row)
        save_json_atomic(
            latest_path,
            {
                "schema_version": 1,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "assets": latest_by_asset,
            },
            mode=0o640,
        )
        return len(items)

    def write_telemetry(self, snapshot: RuntimeTelemetrySnapshot) -> None:
        path = (
            TELEMETRY_FILE
            if TELEMETRY_FILE.parent == self.base_dir
            else self.base_dir / "telemetry.json"
        )
        save_json_atomic(
            path,
            {
                "schema_version": 1,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "runtime": asdict(snapshot),
            },
            mode=0o640,
        )

    def write_health(self, payload: dict) -> None:
        path = (
            HEALTH_FILE
            if HEALTH_FILE.parent == self.base_dir
            else self.base_dir / "health.json"
        )
        save_json_atomic(path, dict(payload), mode=0o640)

    def prune(self, *, now_utc: datetime) -> int:
        cutoff = now_utc.astimezone(timezone.utc) - timedelta(
            hours=self.retention_hours
        )
        removed = 0
        for path in self.base_dir.glob("features-*.jsonl"):
            match = _FEATURE_RE.match(path.name)
            if match is None:
                continue
            try:
                hour = datetime.strptime(
                    match.group(1), "%Y%m%dT%H"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if hour + timedelta(hours=1) < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
        return removed
