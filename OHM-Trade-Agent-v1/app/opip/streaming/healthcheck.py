"""Container healthcheck for the isolated O'Pip stream worker."""
from __future__ import annotations

from datetime import datetime, timezone
import sys

from app.opip.events.contract import parse_utc
from app.opip.streaming.store import HEALTH_FILE
from app.services.registry_io import RegistryIOError, load_json


MAX_HEALTH_AGE_SECONDS = 20.0


def main() -> int:
    try:
        payload = load_json(HEALTH_FILE)
        updated = parse_utc(
            payload.get("updated_at_utc"),
            field_name="updated_at_utc",
        )
    except (OSError, TimeoutError, RegistryIOError, ValueError):
        return 1
    if updated is None:
        return 1
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    if age < 0 or age > MAX_HEALTH_AGE_SECONDS:
        return 1
    if payload.get("status") not in {"RUNNING", "DEGRADED"}:
        return 1
    if bool(payload.get("runtime_failed")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
