from __future__ import annotations

import logging
import os
import tempfile
from collections import deque
from pathlib import Path


logger = logging.getLogger(__name__)


def compact_jsonl_recent(
    path: Path,
    *,
    max_bytes: int,
    keep_lines: int,
) -> bool:
    """Atomically retain only the most recent JSONL lines once a size cap is exceeded.

    Callers must hold the same writer lock used for appends while invoking this
    helper. Compaction is best-effort: an I/O failure is logged and leaves the
    original ledger in place so evidence capture can continue.
    """
    max_bytes = max(1, int(max_bytes))
    keep_lines = max(1, int(keep_lines))
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return False
    except OSError as exc:
        logger.warning("Could not inspect JSONL ledger %s for retention: %s", path, exc)
        return False

    recent: deque[str] = deque(maxlen=keep_lines)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                recent.append(line if line.endswith("\n") else line + "\n")
    except OSError as exc:
        logger.warning("Could not read JSONL ledger %s for retention: %s", path, exc)
        return False

    descriptor = -1
    temp_name: str | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".compact.tmp",
        )
        mode = path.stat().st_mode & 0o777
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.writelines(recent)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        temp_name = None
        return True
    except OSError as exc:
        logger.warning("Could not compact JSONL ledger %s: %s", path, exc)
        return False
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            except OSError:
                pass
