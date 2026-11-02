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


def _atomic_write(directory: Path, target: str, content: bytes) -> None:
    """Restricted durable replace; caller must hold draft lock."""
    final = directory / target
    if final.is_symlink():
        raise ValueError("STRATEGY_ADMIN_UNSAFE_TARGET")
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".strategy-", dir=directory, delete=False) as fd:
            temp_name = fd.name
            os.chmod(fd.name, 0o600)
            fd.write(content)
            fd.flush()
            os.fsync(fd.fileno())
        os.replace(temp_name, final)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _audit_intent(directory: Path, action: str, before: str, after: str) -> None:
    """Durable intent receipt *before* activating; audit failures abort mutation."""
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "before_sha256": before,
        "after_sha256": after,
    }
    fd = os.open(directory / "audit.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        raw = (json.dumps(record, sort_keys=True) + "\n").encode()
        if os.write(fd, raw) != len(raw):
            raise OSError("STRATEGY_ADMIN_AUDIT_SHORT_WRITE")
        os.fsync(fd)
    finally:
        os.close(fd)


def activate_policy_draft(*, expected_digest: str, directory: Path = DRAFT_DIR) -> dict:
    """Promote a saved draft atomically; activated policy affects advisory only.

    Active profile selection is controlled by an independent runtime setting.
    No model switch or trading permission can be conveyed by the JSON.
    """
    with _locked(directory):
        draft = directory / "draft.json"
        if not draft.is_file() or draft.is_symlink():
            raise ValueError("STRATEGY_ADMIN_NO_SAVED_DRAFT")
        proposed = draft.read_bytes()
        validated = parse_strategy_policy(proposed)
        if validated.digest != expected_digest:
            raise StrategyDraftConflict("STRATEGY_ADMIN_DRAFT_STALE")
        active = directory / "active.json"
        if active.is_symlink():
            raise ValueError("STRATEGY_ADMIN_UNSAFE_ACTIVE")
        previous = active.read_bytes() if active.is_file() else DEFAULT_POLICY_PATH.read_bytes()
        previous_digest = parse_strategy_policy(previous).digest
        _audit_intent(directory, "ACTIVATE_ADVISORY_PROFILE_INTENT", previous_digest, validated.digest)
        _atomic_write(directory, "previous.json", previous)
        _atomic_write(directory, "active.json", proposed)
        _audit_intent(directory, "ACTIVATE_ADVISORY_PROFILE_COMPLETE", previous_digest, validated.digest)
        return {
            "status": "ACTIVATED_ADVISORY_PROFILE",
            "digest": validated.digest,
            "funded_trading_activated": False,
        }


def rollback_active_policy(*, expected_digest: str, directory: Path = DRAFT_DIR) -> dict:
    """Reapply the single previous version; never changes funded-trading state."""
    with _locked(directory):
        active, previous = directory / "active.json", directory / "previous.json"
        if not active.is_file() or not previous.is_file() or active.is_symlink() or previous.is_symlink():
            raise ValueError("STRATEGY_ADMIN_NO_SAFE_ROLLBACK")
        current_bytes, previous_bytes = active.read_bytes(), previous.read_bytes()
        current = parse_strategy_policy(current_bytes)
        restored = parse_strategy_policy(previous_bytes)
        if current.digest != expected_digest:
            raise StrategyDraftConflict("STRATEGY_ADMIN_ACTIVE_STALE")
        _audit_intent(directory, "ROLLBACK_ADVISORY_PROFILE_INTENT", current.digest, restored.digest)
        _atomic_write(directory, "active.json", previous_bytes)
        _audit_intent(directory, "ROLLBACK_ADVISORY_PROFILE_COMPLETE", current.digest, restored.digest)
        return {
            "status": "ROLLED_BACK_ADVISORY_PROFILE",
            "digest": restored.digest,
            "funded_trading_activated": False,
        }


def read_active_policy_status(directory: Path = DRAFT_DIR) -> dict:
    path = directory / "active.json"
    if path.is_symlink():
        raise ValueError("STRATEGY_ADMIN_UNSAFE_ACTIVE")
    content = path.read_bytes() if path.is_file() else DEFAULT_POLICY_PATH.read_bytes()
    policy = parse_strategy_policy(content)
    return {
        "status": "ACTIVE_PROFILE" if path.is_file() else "BUNDLED_DEFAULT",
        "digest": policy.digest,
        "document": json.loads(content),
        "funded_trading_activated": False,
    }
