"""One-shot operator entrypoint for legacy coverage discontinuity epoch.

Default-off. Verifies the expected legacy ``complete=false`` HOT-present
archive condition before writing the learning-side epoch. Does not mutate
production evidence, rewrite ``state.json``, or mint empty attestation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.opip.decision.store import (
    funnel_events_archive,
    scan_summaries_archive,
    screening_evaluations_archive,
)
from app.opip.learning.coverage_discontinuity import (
    establish_coverage_discontinuity_epoch,
)


def _archive_for_prefix(data_root: Path, prefix: str):
    qualification = data_root / "opip" / "qualification"
    mapping = {
        "screening_evaluations": screening_evaluations_archive(
            qualification / "screening_evaluations.jsonl"
        ),
        "funnel_events": funnel_events_archive(
            qualification / "funnel_events.jsonl"
        ),
        "scan_summaries": scan_summaries_archive(
            qualification / "scan_summaries.jsonl"
        ),
    }
    archive = mapping.get(prefix)
    if archive is None:
        raise SystemExit(f"unsupported archive prefix: {prefix}")
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Establish learning-side legacy_coverage_discontinuity_v1 epoch "
            "(measurement-only; one-shot)."
        )
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("/app/data"),
        help="Learning replica data root (never production DATA_ROOT)",
    )
    parser.add_argument(
        "--archive-prefix",
        required=True,
        help="Qualification archive prefix (e.g. screening_evaluations)",
    )
    parser.add_argument(
        "--expected-legacy-state-sha256",
        required=True,
        help="Exact SHA256 of the legacy complete=false window_index state.json",
    )
    args = parser.parse_args(argv)
    archive = _archive_for_prefix(args.data_root, args.archive_prefix)
    epoch = establish_coverage_discontinuity_epoch(
        args.data_root,
        archive,
        expected_legacy_state_sha256=args.expected_legacy_state_sha256,
    )
    print(json.dumps(epoch.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
