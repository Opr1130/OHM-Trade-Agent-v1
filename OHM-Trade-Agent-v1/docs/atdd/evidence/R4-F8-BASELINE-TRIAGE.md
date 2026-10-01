# R4-F8 baseline failure triage

Increment: `ATDD-R4-F8-paper-v2-cutover-readiness`

This is the R4 baseline evidence required before an authority-changing phase may
proceed. It classifies every observed full-suite failure on the verified starting
`main` (`12e7428639e989de06a7579953c8a95efb5e5bbe`) so no blanket waiver hides a
production-relevant defect.

## Reproduction

- Revision: `origin/main` = `12e7428639e989de06a7579953c8a95efb5e5bbe` (clean 0/0 base).
- Command: `python -m pytest -q -p no:cacheprovider` from `OHM-Trade-Agent-v1/`.
- Result: **51 failed, 6618 passed, 259 skipped, 7 warnings in 604.63s (0:10:04)**.
- Host: Windows developer workstation (Python 3.13), **not** the Linux CI image
  (Python 3.12). CI is the authority; this local run is triage evidence only.

## Classification

Every observed failure family is one of the non-authority classes below. None
touches paper authority, protection, drain, the canonical writer, `run_cycle`,
deployment control, recovery, Freqtrade, Paper-v1, Paper-v2, or universe
metadata.

| # | Family (representative test) | Class | Observed cause |
| --- | --- | --- | --- |
| 1 | `test_deploy_self_replacement_r1_5`, `test_maintenance_shell_exit_precedence_v1`, `test_platform_foundation_v1`, `test_opip_ml_scheduler_isolation_v1`, `test_learning_oneshot_env_export_v1` | environment/platform-only | `subprocess.run(["bash", ...])` → `FileNotFoundError [WinError 2]`; no `bash` on Windows. The Linux CI job runs `bash -n` on every deploy script and passes. |
| 2 | `test_learning_worker_consumption_v1`, `test_opip_ml_production_evidence_v1`, `test_opip_ml_directory_durability_v1` | environment/platform-only | `PermissionError` writing atomic chunk/dir-sync paths under Windows filesystem semantics; POSIX `fsync`/rename semantics are exercised on Linux CI. |
| 3 | `test_telegram_delivery_closure_v2::test_telegram_alert_state_writers_use_atomic_registry_io` | unrelated test-harness defect | `Path(...).read_text()` decodes with the Windows default cp1252 codec and raises `UnicodeDecodeError` on UTF-8 source. Not a product defect; the file is valid UTF-8. |
| 4 | `test_opip_committee_host_runtime_v1` (all), `test_grafana_final_hardening_v1`, `test_analytics_deploy_workflow_v1`, `test_opip_data_platform_v1` | environment/platform-only | Shell/host-runtime harnesses that require a Linux host, `bash`, and (for Grafana) TLS tooling. These are learning/analytics/committee plane surfaces, which are explicitly outside the trading host. |
| 5 | `test_p1_shadow_outbox_retirement_v1::test_retirement_shells_parse` | environment/platform-only | `bash -n` shell parse of a retirement script; no `bash` on Windows. |

## Disposition

- No failure in the baseline touches an R4-critical subsystem. Specifically, no
  failure family exercises paper authority, protection, drain, the canonical
  writer, `run_cycle`, deployment control, recovery, Freqtrade, Paper-v1,
  Paper-v2, or universe metadata.
- There is therefore no `BASELINE_PRODUCTION_FAILURE` blocker from this local run.
- CI remains the authority for the 51 failures: the exact-head `pytest` and
  `atdd scope` jobs run on Linux/Python 3.12 and are the gate that must be green
  before merge. The local count is not used to waive any R4-critical test.

## Readiness consequence

The independently verifiable technical cutover gates are evaluated separately by
`app.services.paper_v2_cutover_readiness.evaluate_cutover_readiness`. On the
current code the remaining blocker is:

- `SHORT_AUTHORITY_MISSING` — Paper-v2 has no short engine and architecture does
  not ratify a LONG-only paper mandate, so full cutover stays blocked until a
  future increment supplies short authority or the owner approves a scope change.
