"""Independent scheduler entrypoint for O'Pip ML evidence capture."""
from __future__ import annotations

from app.services.opip_ml_evidence_capture import capture_ml_production_evidence


def main() -> None:
    """Run one bounded evidence-only capture pass."""
    summary = capture_ml_production_evidence()
    print("O'Pip ML Evidence Capture — SHADOW")
    print("Enabled:", summary.enabled)
    if not summary.enabled:
        return
    print("Ledger rows:", summary.ledger_rows_seen)
    print("Processed:", summary.processed)
    print("Legacy skipped:", summary.legacy_without_seed)
    print("Temporal violations:", summary.temporal_violations)
    print("Duplicate snapshots skipped:", summary.duplicate_snapshots_skipped)
    print("Malformed:", summary.malformed)
    print("Missing feature values:", summary.missing_feature_values)
    print("Feature values:", summary.feature_values)
    print("Checkpoint line:", summary.next_line)
    print(
        "P1 drain:",
        f"processed={summary.p1_drained}",
        f"duplicates={summary.p1_duplicates}",
        f"malformed={summary.p1_malformed}",
        f"stopped={summary.p1_stopped_on_error}",
    )
    print(
        "P1 index:",
        f"catchup={summary.p1_index_catchup_in_progress}",
        f"from={summary.p1_index_reconciled_from_offset}",
        f"to={summary.p1_index_reconciled_to_offset}",
        f"ledger_size={summary.p1_index_ledger_size}",
        f"rows={summary.p1_index_rows_scanned}",
        f"bytes={summary.p1_index_bytes_scanned}",
    )
    if summary.error_type:
        print("Degraded:", summary.error_type)


if __name__ == "__main__":
    main()
