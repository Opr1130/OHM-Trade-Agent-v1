from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.registry_io import registry_lock


TRACE_FILE = Path("/app/data/candidate_trace.jsonl")
LOCK_FILE = TRACE_FILE.parent / ".candidate_trace.lock"


def candidate_id(
    *,
    symbol: str,
    direction: str,
    observed_at: str | None = None,
    strategy_version: str = "",
) -> str:
    basis = "|".join(
        (
            symbol.upper(),
            direction.upper(),
            observed_at or "",
            strategy_version,
        )
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def trace_candidate_event(
    *,
    symbol: str,
    direction: str,
    stage: str,
    reason_code: str,
    details: dict[str, Any] | None = None,
    run_id: str | None = None,
    candidate_id_value: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Append a secret-free candidate/run audit event.

    Trace failures are fail-soft by design and can never alter a trading decision.
    """
    target = path or TRACE_FILE
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "observed_at": now,
        "run_id": run_id,
        "candidate_id": candidate_id_value
        or candidate_id(
            symbol=symbol,
            direction=direction,
            observed_at=now,
            strategy_version=stage,
        ),
        "symbol": symbol.upper(),
        "direction": direction.upper(),
        "stage": stage,
        "reason_code": reason_code,
        "details": details or {},
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = target.parent / f".{target.name}.lock"
        with registry_lock(lock):
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                handle.flush()
    except OSError:
        pass
    return record
