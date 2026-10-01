"""R4-A bounded Paper-v2 cutover-readiness evidence report.

Read-only. Prints one machine-readable readiness report and exits 0. It never
activates Paper v2, never wires the F7 selector as an admission authority, never
mutates canonical evidence, and never prints secrets or arbitrary environment
values: it reports only the specific typed facts the cutover gates name.

Usage: ``python -m app.jobs.report_paper_v2_cutover_readiness``
"""

from __future__ import annotations

import json

from app.services.paper_v2_cutover_readiness import cutover_readiness_report


def main() -> None:
    report = cutover_readiness_report()
    print("===== O'PIP PAPER V2 CUTOVER READINESS (R4-A) =====")
    print(json.dumps(report.to_dict(), sort_keys=True, indent=2))
    print("Readiness:", report.overall)
    print("Reason codes:", ",".join(report.reason_codes) or "NONE")
    print(
        "Activation prerequisites:",
        ",".join(report.activation_prerequisites) or "NONE",
    )


if __name__ == "__main__":
    main()
