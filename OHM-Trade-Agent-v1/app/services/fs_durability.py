"""Strict filesystem durability helpers.

On Linux, a failed file or directory fsync is a durability failure and must
abort the caller. It is never silently suppressed: a VERIFIED archive or a
safe-rollback assertion is only valid when the relevant bytes and directory
entries are known to be durable.

Windows cannot fsync directory handles and rejects fsync on some read-only
descriptors. Tests remain portable by tolerating only those platform-specific
errors on non-POSIX hosts; the fault-injection tests monkeypatch these helpers
directly, so strict failure behaviour is still exercised everywhere.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

_WINDOWS_TOLERATED = {errno.EBADF, errno.EINVAL, errno.EACCES}


class DurabilityError(RuntimeError):
    """A required file or directory fsync did not complete."""


def fsync_file(path: Path) -> None:
    try:
        with path.open("rb+") as handle:
            os.fsync(handle.fileno())
        return
    except OSError as exc:
        if os.name == "posix":
            raise DurabilityError(f"file fsync failed: {path}: {exc}") from exc
        if exc.errno not in _WINDOWS_TOLERATED:
            raise DurabilityError(f"file fsync failed: {path}: {exc}") from exc
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        # Windows has no durable directory/file-sync equivalent for these
        # descriptors; POSIX never reaches this branch.
        return


def fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY)
        os.fsync(fd)
    except OSError as exc:
        raise DurabilityError(f"directory fsync failed: {path}: {exc}") from exc
    finally:
        if fd is not None:
            os.close(fd)
