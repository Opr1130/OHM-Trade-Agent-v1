"""Filesystem paths for the PR 2 canonical writer."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_CANONICAL_DIR = Path("/app/data/opip/canonical")


def canonical_dir() -> Path:
    override = os.environ.get("OPIP_CANONICAL_DIR", "").strip()
    return Path(override) if override else _DEFAULT_CANONICAL_DIR


def db_path() -> Path:
    return canonical_dir() / "opip_canonical_v1.sqlite3"


def socket_path() -> Path:
    return canonical_dir() / "writer.sock"


def gap_spool_path() -> Path:
    return canonical_dir() / "capture_gap_spool.json"


# Module-level aliases for import convenience; resolve at use time via helpers
# when tests override OPIP_CANONICAL_DIR.
CANONICAL_DIR = _DEFAULT_CANONICAL_DIR
DB_PATH = _DEFAULT_CANONICAL_DIR / "opip_canonical_v1.sqlite3"
SOCKET_PATH = _DEFAULT_CANONICAL_DIR / "writer.sock"
GAP_SPOOL_PATH = _DEFAULT_CANONICAL_DIR / "capture_gap_spool.json"

STREAM_EARLY_WATCH = "alert_governor.early_watch"
STATE_FAMILY_EARLY_WATCH = "early_watch"

SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1

# N8 activation gate (provisional). Do not raise without an approved contract change.
# CI shared runners are not a production-like clock; keep this constant intact and
# separate noisy CI timing from the activation measurement gate in tests.
N8_WRITER_TXN_P99_MS = 50.0
N8_CI_SANITY_TXN_P99_MS = 250.0
