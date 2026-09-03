"""Repair derived qualification archive window indexes under writer locks.

This maintenance job never changes HOT evidence, trading policy, ranking,
alerts, paper admission, or exchange authority. It only repairs canonical
archive-manifest visibility metadata and rebuilds the derived day-sharded
window index after checksum verification.
"""

from __future__ import annotations

from pathlib import Path

from app.opip.decision.store import (
    FUNNEL_EVENTS_FILE,
    SCAN_SUMMARIES_FILE,
    SCREENING_EVALUATIONS_FILE,
    funnel_events_archive,
    scan_summaries_archive,
    screening_evaluations_archive,
)
from app.services.registry_io import registry_lock


def _repair_one(path: Path, archive) -> bool:
    """Repair one archive while holding the same lock used by its writer."""
    lock = path.parent / f".{path.name}.lock"
    with registry_lock(lock):
        return bool(archive.repair_window_index_locked())


def main() -> None:
    """Repair all exported qualification archives or fail closed."""
    checks = (
        (
            "screening",
            SCREENING_EVALUATIONS_FILE,
            screening_evaluations_archive(),
        ),
        (
            "funnel",
            FUNNEL_EVENTS_FILE,
            funnel_events_archive(),
        ),
        (
            "summaries",
            SCAN_SUMMARIES_FILE,
            scan_summaries_archive(),
        ),
    )
    failed: list[str] = []
    for name, path, archive in checks:
        try:
            repaired = _repair_one(path, archive)
        except Exception as exc:
            print(
                "O'Pip archive window-index repair failed:",
                f"stream={name}",
                f"error={type(exc).__name__}",
            )
            repaired = False
        if not repaired:
            failed.append(name)
    if failed:
        raise RuntimeError(
            "OPIP_ARCHIVE_WINDOW_INDEX_REPAIR_FAILED:" + ",".join(failed)
        )
    print("O'Pip archive window-index repair: OK")


if __name__ == "__main__":
    main()
