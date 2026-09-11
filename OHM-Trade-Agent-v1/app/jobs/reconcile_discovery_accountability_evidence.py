"""One-shot EF-01 evidence reconciliation on the learning worker.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Reads the replicated discovery and accountability ledgers, writes a join
report, and never mutates ranking, alerts, paper admission, or execution.
"""

from __future__ import annotations

from pathlib import Path
import json
import sys

from app.opip.discovery.reconciliation import (
    DEFAULT_DATA_ROOT,
    inspect_replica,
    persist_reconciliation_report,
)
from app.opip.learning.job_disposition import (
    CONSUMED_EMPTY,
    CONSUMED_OK,
    FAILED_RETRYABLE,
    write_consumption_summary,
)


DEFAULT_REPORT = Path("/app/data/opip/discovery/ef01_reconciliation_report.json")


def main(data_root: Path | None = None) -> dict:
    root = data_root or DEFAULT_DATA_ROOT
    try:
        report = inspect_replica(root)
    except Exception as exc:
        payload = {
            "status": "ERROR",
            "error": str(exc),
            "measurement_only": True,
            "trade_authority_changed": False,
            "policy_change_authorized": False,
        }
        if root.is_dir():
            write_consumption_summary(
                root,
                job="reconcile",
                disposition=FAILED_RETRYABLE,
                payload=payload,
            )
        print(json.dumps(payload, sort_keys=True))
        raise

    report_path = DEFAULT_REPORT if root == DEFAULT_DATA_ROOT else (
        root / "opip/discovery/ef01_reconciliation_report.json"
    )
    persist_reconciliation_report(report, report_path)
    empty = not report.get("replica_available")
    disposition = CONSUMED_EMPTY if empty else CONSUMED_OK
    payload = {
        "status": "EMPTY" if empty else "OK",
        "report_path": str(report_path),
        "replica_available": bool(report.get("replica_available")),
        "population_counts": report.get("population_counts", {}),
        "stage0_reconciliation": report.get("stage0_reconciliation", {}),
        "admission_mapping": report.get("admission_mapping", {}),
        "classification_mismatches": {
            "count": (report.get("classification_mismatches") or {}).get("count"),
        },
        "identity_reconciliation": report.get("identity_reconciliation", {}),
        "outcome_reconciliation": report.get("outcome_reconciliation", {}),
        "maturity_reconciliation": report.get("maturity_reconciliation", {}),
        "threshold_authorities": report.get("threshold_authorities", {}),
        "winner_definitions": report.get("winner_definitions", {}),
        "resource_usage": report.get("resource_usage", {}),
        "measurement_only": True,
        "trade_authority_changed": False,
        "policy_change_authorized": False,
    }
    if root.is_dir():
        write_consumption_summary(
            root, job="reconcile", disposition=disposition, payload=payload
        )
    print(json.dumps(payload, sort_keys=True))
    return payload


if __name__ == "__main__":
    sys.exit(0 if main().get("status") != "ERROR" else 1)
