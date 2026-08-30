"""Deterministic version attribution for O'Pip qualification evidence.

The repository convention for evidence streams is an explicit integer
``SCHEMA_VERSION`` plus a named, non-semantic build identifier. Nothing here
is a semantic version: none of these values imply compatibility guarantees or
a release cadence. They exist so a future analysis can partition evidence by
the exact policy that produced it.

``gate_policy_fingerprint()`` is the important one. A named version can drift
away from the constants it claims to describe; the fingerprint is derived from
the production threshold values themselves, so any threshold change is
detectable in the evidence even if nobody remembers to bump a label.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any


# Append-only JSONL contract version for the O'Pip qualification streams.
FUNNEL_SCHEMA_VERSION = 1

# Named build identifiers. Deliberately non-semantic.
STRATEGY_VERSION = "OPIP-STRATEGY-V1"
INTELLIGENCE_VERSION = "OPIP-INTELLIGENCE-V1"
GATE_POLICY_VERSION = "OPIP-GATE-POLICY-V1"

# Prepared for a future ML build, not required by Build 1. They are emitted as
# explicit nulls so a consumer can distinguish "not yet versioned" from
# "field absent".
FEATURE_SCHEMA_VERSION: str | None = None
MODEL_VERSION: str | None = None


def gate_policy_fingerprint() -> str:
    """Return a short deterministic hash of the live production thresholds.

    Imported lazily so that ``versioning`` stays free of gate dependencies and
    can be used by pure identity code.
    """
    from app.opip.decision.thresholds import gate_policy_constants

    payload = json.dumps(
        gate_policy_constants(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "GPF:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)
def app_code_fingerprint() -> str:
    """Return a conservative SHA-256 fingerprint of the shipped app source.

    Any Python source change anywhere under the application tree changes this
    fingerprint. Exact historical replay should refuse an uncertain checkout
    rather than certify compatibility from a manually maintained label.
    """
    app_root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted(
        app_root.rglob("*.py"),
        key=lambda item: item.relative_to(app_root).as_posix(),
    ):
        relative = path.relative_to(app_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "ACF:" + digest.hexdigest()


def version_stamp() -> dict[str, Any]:
    """Return the version block carried by every O'Pip qualification event."""
    return {
        "schema_version": FUNNEL_SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "intelligence_version": INTELLIGENCE_VERSION,
        "gate_policy_version": GATE_POLICY_VERSION,
        "gate_policy_fingerprint": gate_policy_fingerprint(),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
    }
