import json
import logging
import math
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class RegistryIOError(RuntimeError):
    """A persistent registry could not be read safely."""


class RegistryCorruptionError(RegistryIOError):
    """A registry contained malformed or structurally invalid JSON."""

    def __init__(self, path: Path, quarantine_path: Path | None, reason: str) -> None:
        self.path = path
        self.quarantine_path = quarantine_path
        self.reason = reason
        location = f"; quarantined at {quarantine_path}" if quarantine_path else ""
        super().__init__(f"Registry corruption at {path}: {reason}{location}")


@contextmanager
def registry_lock(lock_file: Path, timeout: float = 10.0):
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()

    deadline = time.monotonic() + timeout
    while True:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise TimeoutError(f"Timed out acquiring registry lock {lock_file}")
            time.sleep(0.05)

    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _quarantine_corrupt_registry(path: Path) -> Path | None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    quarantine = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, quarantine)
        return quarantine
    except OSError as exc:
        logger.critical(
            "Failed to quarantine corrupt registry %s: %s",
            path,
            exc,
        )
        return None


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON numeric token {token}")


def _first_nonfinite_path(value: Any, path: str = "$") -> str | None:
    if isinstance(value, float) and not math.isfinite(value):
        return path
    if isinstance(value, dict):
        for key, item in value.items():
            found = _first_nonfinite_path(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _first_nonfinite_path(item, f"{path}[{index}]")
            if found:
                return found
    return None


def _raise_corrupt(path: Path, reason: str, exc: Exception | None = None) -> None:
    quarantine = _quarantine_corrupt_registry(path)
    # Keep the historical log phrase so operators/tests have one searchable
    # corruption signature for syntax, structure and legacy non-finite state.
    logger.critical(
        "Malformed registry JSON at %s: %s; quarantine=%s",
        path,
        reason,
        quarantine,
    )
    error = RegistryCorruptionError(path, quarantine, reason)
    if exc is not None:
        raise error from exc
    raise error


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.critical("Registry read failed at %s: %s", path, exc)
        raise RegistryIOError(f"Registry read failed at {path}: {exc}") from exc

    try:
        payload = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        _raise_corrupt(path, str(exc), exc)

    if not isinstance(payload, dict):
        _raise_corrupt(path, f"expected JSON object, got {type(payload).__name__}")

    nonfinite_path = _first_nonfinite_path(payload)
    if nonfinite_path is not None:
        _raise_corrupt(path, f"non-finite numeric value at {nonfinite_path}")
    return payload


def save_json_atomic(path: Path, data: dict, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
