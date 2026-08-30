"""Bounded durable shadow storage for Sequence 4 BUILD 4.5."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from typing import Iterable

from app.opip.storage.bounded_jsonl import encode_row
from app.opip.streaming.feature_accumulator import StreamingFeatureSnapshot
from app.opip.streaming.telemetry import RuntimeTelemetrySnapshot
from app.services.registry_io import save_json_atomic


STREAMING_DATA_DIR = Path("/app/data/opip/streaming")
TELEMETRY_FILE = STREAMING_DATA_DIR / "telemetry.json"
HEALTH_FILE = STREAMING_DATA_DIR / "health.json"
LATEST_FEATURES_FILE = STREAMING_DATA_DIR / "latest_features.json"
_FEATURE_RE = re.compile(r"^features-(\d{8}T\d{2})\.jsonl$")


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
        by_hour: dict[str, list[StreamingFeatureSnapshot]] = {}
        for row in items:
            stamp = row.window_end_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H")
            by_hour.setdefault(stamp, []).append(row)

        for stamp, grouped in by_hour.items():
            path = self.base_dir / f"features-{stamp}.jsonl"
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o640,
            )
            try:
                with os.fdopen(descriptor, "ab", closefd=True) as handle:
                    for row in grouped:
                        handle.write(encode_row(row.as_dict()))
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                raise

        latest_by_asset: dict[str, dict] = {}
        if LATEST_FEATURES_FILE.parent == self.base_dir:
            latest_path = LATEST_FEATURES_FILE
        else:
            latest_path = self.base_dir / "latest_features.json"
        for row in sorted(items, key=lambda item: item.window_end_utc):
            latest_by_asset[row.canonical_asset_id] = row.as_dict()
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
