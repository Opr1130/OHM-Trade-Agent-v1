import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


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


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.critical("Registry read failed at %s: %s", path, exc)
        raise RegistryIOError(f"Registry read failed at {path}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        quarantine = _quarantine_corrupt_registry(path)
        logger.critical(
            "Malformed registry JSON at %s: %s; quarantine=%s",
            path,
            exc,
            quarantine,
        )
        raise RegistryCorruptionError(path, quarantine, str(exc)) from exc

    if not isinstance(payload, dict):
        quarantine = _quarantine_corrupt_registry(path)
        reason = f"expected JSON object, got {type(payload).__name__}"
        logger.critical(
            "Structurally invalid registry at %s: %s; quarantine=%s",
            path,
            reason,
            quarantine,
        )
        raise RegistryCorruptionError(path, quarantine, reason)
    return payload


def save_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            # Python's JSON encoder otherwise serializes NaN/Infinity even
            # though those are not valid JSON and can poison learning/state.
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
