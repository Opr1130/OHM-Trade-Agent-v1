from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.registry_io import registry_lock


USAGE_FILE = Path("/app/data/openai_usage.jsonl")
LOCK_FILE = USAGE_FILE.parent / ".openai_usage.lock"


def _nested_int(obj: Any, parent: str, child: str) -> int:
    parent_value = getattr(obj, parent, None)
    return int(getattr(parent_value, child, 0) or 0)


def append_usage_record(
    *,
    model: str,
    reasoning_effort: str,
    candidate_count: int,
    usage: Any,
) -> dict[str, Any]:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "candidate_count": int(candidate_count),
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "cached_input_tokens": _nested_int(
            usage, "input_tokens_details", "cached_tokens"
        ),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "reasoning_tokens": _nested_int(
            usage, "output_tokens_details", "reasoning_tokens"
        ),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with registry_lock(LOCK_FILE):
        with USAGE_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
    return record
