from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.services.movement_discovery_v2 import CoarseMover, EarlyMoverSignal
from app.services.registry_io import load_json, registry_lock, save_json_atomic


DETECTION_FILE = Path("/app/data/movement_discovery_v2_1.jsonl")
STATE_FILE = Path("/app/data/movement_discovery_v2_1_capture_state.json")
LOCK_FILE = DETECTION_FILE.parent / ".movement_discovery_v2_1_capture.lock"
DETECTION_BUCKET_SECONDS = 30 * 60
MAX_DEDUP_IDS = 5000


def _detection_id(signal: EarlyMoverSignal, observed_at: datetime) -> str:
    epoch_bucket = int(observed_at.timestamp()) // DETECTION_BUCKET_SECONDS
    raw = "|".join(
        (
            signal.version,
            signal.symbol.upper(),
            signal.direction.upper(),
            signal.fingerprint,
            str(epoch_bucket),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def capture_movement_detections(
    signals: Iterable[EarlyMoverSignal],
    coarse_movers: Iterable[CoarseMover],
    *,
    now: datetime | None = None,
    detection_file: Path | None = None,
    state_file: Path | None = None,
) -> int:
    """Persist immutable decision-time evidence for Movement Discovery v2.1.

    This function records only information available at detection time. Future
    prices and outcomes are written by a separate observer after each horizon
    becomes due.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = detection_file or DETECTION_FILE
    state_target = state_file or STATE_FILE
    lock_target = target.parent / f".{target.name}.capture.lock"

    price_by_asset = {
        mover.base_asset.upper(): float(mover.last_price)
        for mover in coarse_movers
        if float(mover.last_price) > 0
    }
    rows = list(signals)
    if not rows:
        return 0

    written = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with registry_lock(lock_target):
        state = load_json(state_target)
        seen = list(state.get("seen_detection_ids") or [])
        seen_set = set(str(item) for item in seen)
        with target.open("a", encoding="utf-8") as handle:
            for signal in rows:
                detection_id = _detection_id(signal, now)
                if detection_id in seen_set:
                    continue
                reference_price = price_by_asset.get(signal.base_asset.upper())
                if reference_price is None or reference_price <= 0:
                    continue
                payload = signal.as_dict()
                payload.update(
                    {
                        "record_type": "DETECTION",
                        "detection_id": detection_id,
                        "observed_at": now.isoformat(),
                        "reference_price": round(reference_price, 12),
                        "shadow_only": True,
                        "production_decision_changed": False,
                    }
                )
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
                handle.flush()
                seen.append(detection_id)
                seen_set.add(detection_id)
                written += 1

        if len(seen) > MAX_DEDUP_IDS:
            seen = seen[-MAX_DEDUP_IDS:]
        state["seen_detection_ids"] = seen
        state["last_capture_at"] = now.isoformat()
        save_json_atomic(state_target, state)
    return written
