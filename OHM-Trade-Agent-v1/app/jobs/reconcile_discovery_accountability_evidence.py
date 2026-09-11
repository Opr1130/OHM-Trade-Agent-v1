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
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_INCOMPLETE,
    STATUS_RECONCILED,
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


def _disposition_for_status(status: str) -> str:
    if status == STATUS_RECONCILED:
        return CONSUMED_OK
    if status == STATUS_EMPTY:
        return CONSUMED_EMPTY
    # INCOMPLETE / ERROR: durable retryable disposition; not CONSUMED_OK.
    return FAILED_RETRYABLE


def main(data_root: Path | None = None) -> dict:
    root = data_root or DEFAULT_DATA_ROOT
    try:
        report = inspect_replica(root)
        report_path = DEFAULT_REPORT if root == DEFAULT_DATA_ROOT else (
            root / "opip/discovery/ef01_reconciliation_report.json"
        )
        persist_reconciliation_report(report, report_path)
    except Exception as exc:
        payload = {
            "status": STATUS_ERROR,
            "error": str(exc),
            "reconciliation_complete": False,
            "reconciliation_status": STATUS_ERROR,
            "reconciliation_blockers": ["RUNTIME_ERROR"],
            "replica_present": False,
            "replica_available": False,
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

    recon_status = str(report.get("reconciliation_status") or STATUS_INCOMPLETE)
    if report.get("reconciliation_complete"):
        recon_status = STATUS_RECONCILED
    elif not report.get("replica_present") and not report.get("replica_available"):
        recon_status = STATUS_EMPTY

    if recon_status == STATUS_RECONCILED:
        job_status = "OK"
    elif recon_status == STATUS_EMPTY:
        job_status = STATUS_EMPTY
    else:
        job_status = STATUS_INCOMPLETE

    disposition = _disposition_for_status(recon_status)
    payload = {
        "status": job_status,
        "report_path": str(report_path),
        "replica_present": bool(report.get("replica_present")),
        "replica_available": bool(
            report.get("replica_present", report.get("replica_available"))
        ),
        "reconciliation_complete": bool(report.get("reconciliation_complete")),
        "reconciliation_status": recon_status,
        "reconciliation_blockers": list(report.get("reconciliation_blockers") or []),
        "core_planes": report.get("core_planes", {}),
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
    # Exit 0 for OK / EMPTY / INCOMPLETE (operationally expected incomplete is
    # not a terminal infra failure). Exit 1 only for ERROR.
    result = main()
    sys.exit(0 if result.get("status") != STATUS_ERROR else 1)
