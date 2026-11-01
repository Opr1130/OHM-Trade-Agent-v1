"""Bounded strategy policy draft storage. Never changes runtime or trading authority."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator

from app.core.strategy_policy import DEFAULT_POLICY_PATH, parse_strategy_policy

DRAFT_DIR = Path("/app/data/opip/strategy-admin")
_EDITABLE = frozenset({
    ("scoring", "v2_threshold"),
    ("advisory_economics", "min_net_profit_usd"),
    ("advisory_economics", "min_target_2_move_pct"),
    ("advisory_economics", "min_reward_to_risk"),
    ("advisory_economics", "estimated_round_trip_cost_pct"),
})


class StrategyDraftConflict(ValueError):
    pass


@contextmanager
def _locked(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError("STRATEGY_ADMIN_UNSAFE_DIRECTORY")
    lock = directory / ".draft.lock"
    if lock.is_symlink():
        raise ValueError("STRATEGY_ADMIN_UNSAFE_LOCK")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_current(directory: Path) -> bytes:
    path = directory / "draft.json"
    if path.is_symlink():
        raise ValueError("STRATEGY_ADMIN_UNSAFE_DRAFT")
    return path.read_bytes() if path.exists() else DEFAULT_POLICY_PATH.read_bytes()


def read_policy_draft(directory: Path = DRAFT_DIR) -> dict:
    """Read either the last saved draft or repository reference proposal."""
    source = _read_current(directory)
    parsed = parse_strategy_policy(source)
    return {
        "status": "DRAFT_SAVED" if (directory / "draft.json").exists() else "REFERENCE_ONLY",
        "digest": parsed.digest,
        "document": json.loads(source),
        "active_runtime_changed": False,
    }


def save_policy_draft(
    proposed: dict, *, expected_digest: str, directory: Path = DRAFT_DIR
) -> dict:
    """Atomic draft update requiring optimistic concurrency and editable-only diff."""
    if not isinstance(proposed, dict):
        raise ValueError("INVALID_STRATEGY_DRAFT")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError("STRATEGY_DRAFT_EXPECTED_DIGEST_REQUIRED")
    with _locked(directory):
        current = _read_current(directory)
        before = parse_strategy_policy(current)
        if before.digest != expected_digest:
            raise StrategyDraftConflict("STRATEGY_DRAFT_STALE")
        initial = json.loads(current)
        if any(not isinstance(proposed.get(section), dict) for section in (
            'scoring', 'advisory_economics', 'ai_advisory', 'protection'
        )):
            raise ValueError('INVALID_STRATEGY_DRAFT_SECTION')
        allowed_shape = deepcopy(initial)
        for section, field in _EDITABLE:
            allowed_shape[section][field] = proposed.get(section, {}).get(field)
        if proposed != allowed_shape:
            raise ValueError("STRATEGY_DRAFT_PROTECTED_SETTING_MODIFIED")
        payload = (json.dumps(proposed, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > 16 * 1024:
            raise ValueError("STRATEGY_DRAFT_TOO_LARGE")
        after = parse_strategy_policy(payload)
        name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".draft-", suffix=".tmp", dir=directory,
                delete=False
            ) as tmp:
                name = tmp.name
                os.chmod(name, 0o600)
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(name, directory / "draft.json")
            dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
            audit_record = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "action": "SAVE_DRAFT_NO_RUNTIME_EFFECT",
                "before_sha256": hashlib.sha256(current).hexdigest(),
                "after_sha256": after.digest,
            }
            auditfd = os.open(
                directory / "audit.jsonl",
                os.O_APPEND | os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600,
            )
            try:
                os.write(auditfd, (json.dumps(audit_record, sort_keys=True) + "\n").encode())
                os.fsync(auditfd)
            finally:
                os.close(auditfd)
        finally:
            if name is not None and os.path.exists(name):
                os.unlink(name)
        return {
            "status": "DRAFT_SAVED",
            "digest": after.digest,
            "active_runtime_changed": False,
        }
