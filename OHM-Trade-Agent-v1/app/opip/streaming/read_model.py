"""Read-only Sequence 4 shadow evidence surface for future consumers."""
from __future__ import annotations

from pathlib import Path

from app.opip.streaming.store import (
    HEALTH_FILE,
    LATEST_FEATURES_FILE,
    TELEMETRY_FILE,
)
from app.services.registry_io import RegistryIOError, load_json


def _safe_read(path: Path) -> dict:
    try:
        return load_json(path)
    except (OSError, TimeoutError, RegistryIOError):
        return {}


def read_streaming_shadow_status(
    *,
    health_path: Path = HEALTH_FILE,
    telemetry_path: Path = TELEMETRY_FILE,
    latest_features_path: Path = LATEST_FEATURES_FILE,
) -> dict:
    """Read measurement-only streaming state without decision coupling."""
    return {
        "health": _safe_read(health_path),
        "telemetry": _safe_read(telemetry_path),
        "latest_features": _safe_read(latest_features_path),
        "authoritative": False,
        "can_trade": False,
        "can_change_policy": False,
    }
