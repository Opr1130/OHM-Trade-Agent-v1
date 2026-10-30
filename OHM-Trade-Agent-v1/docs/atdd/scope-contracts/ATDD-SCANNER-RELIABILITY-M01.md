INCREMENT:

ATDD-SCANNER-RELIABILITY-M01

OWNER-APPROVED INTENT:

Repair the independent-review findings on the scanner reliability increment
(R01-R08): attempt-specific liveness evidence, real deadline enforcement,
rebuildable durable attempt state, a verified result-promotion seam, worker
volume isolation, bounded acquisition and telemetry, and safe suspension that
never restores the legacy in-process scanner. No trading authority is widened.

ARCHITECTURE REFERENCES:

- v1.5.0 section 1 (canonical ownership, F11 independence)
- v1.5.0 ME-05 (reserve capacity for protection before discovery)
- OPIP_Production_Reliability_Requirements_and_Solutions_v2.docx R01-R08
- docs/engineering/OPIP_SCANNER_RELIABILITY_M01.md

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
an attempt has been launched and no exit record exists for it
WHEN:
liveness reconciliation runs, however long the attempt has been open
THEN:
the attempt stays active, because absence of a running container is not proof
of termination

AC-002:
GIVEN:
the dispatcher captured an exit state for the attempt after its launch
WHEN:
reconciliation classifies that evidence
THEN:
an OOM kill and a deadline kill are recorded as distinct terminal outcomes

AC-003:
GIVEN:
a crash occurred after an event was appended but before the index was saved
WHEN:
the attempt store is read again
THEN:
the index is rebuilt from the append-only ledger and fencing stays monotonic

AC-004:
GIVEN:
a completed acquisition manifest on disk
WHEN:
result promotion validates it
THEN:
only a complete, fresh, identity- and fence-matching, checksum-verified manifest
is consumable, and a partial one is rejected

AC-005:
GIVEN:
the isolated scanner service definition and a producer that never emits a
newline
WHEN:
their bounds are inspected
THEN:
the scanner can only write acquisition staging, and telemetry stays bounded

AC-006:
GIVEN:
repeated isolated-workflow failures
WHEN:
admission is evaluated
THEN:
discovery is suspended with an explicit reason and the in-process scanner is
never restored

EXPLICITLY OUT OF SCOPE:

- any change to ranking, candle windows, universe size or AI review inputs
- any trade, paper, canonical-writer or notification authority change
- USDT retirement (audited only; the container is untouched)
- deployment, activation or production configuration

FROZEN BOUNDARIES:

- EVIDENCE_SHADOW release profile unchanged
- F11 protection independence unchanged
- canonical writer ownership unchanged
- funded credential isolation unchanged
- Paper-v2, Committee and learning authorities stay disabled

ACCEPTANCE TEST TRACEABILITY:

AC-001 -> tests/test_opip_scan_lifecycle_v1.py::test_reconcile_withholds_without_exit_evidence
AC-001 -> tests/test_opip_scan_lifecycle_v1.py::test_reconcile_terminalises_on_post_launch_exit_evidence
AC-002 -> tests/test_opip_scan_lifecycle_v1.py::test_reconcile_classifies_oom_kill
AC-002 -> tests/test_opip_scan_lifecycle_v1.py::test_reconcile_classifies_deadline_kill_as_timeout
AC-003 -> tests/test_opip_scan_lifecycle_v1.py::test_index_rebuilds_from_ledger_after_lost_save
AC-003 -> tests/test_opip_scan_lifecycle_v1.py::test_stale_index_is_repaired_and_fence_stays_monotonic
AC-004 -> tests/test_opip_scan_remediation_v1.py::test_promotion_rejects_partial_scan
AC-004 -> tests/test_opip_scan_remediation_v1.py::test_promotion_accepts_verified_manifest
AC-005 -> tests/test_opip_scan_remediation_v1.py::test_worker_service_mounts_only_scan_staging
AC-005 -> tests/test_opip_scan_remediation_v1.py::test_telemetry_bounds_producer_without_newline
AC-005 -> tests/test_opip_scan_remediation_v1.py::test_batching_is_bounded_and_ordered
AC-006 -> tests/test_opip_scan_lifecycle_v1.py::test_admission_refused_while_breaker_is_open

IMPLEMENTATION MAP:

AC-001 -> OHM-Trade-Agent-v1/app/services/scan_supervisor.py
AC-001 -> OHM-Trade-Agent-v1/app/services/scan_preflight.py
AC-001 -> OHM-Trade-Agent-v1/app/services/scan_manifest.py
AC-001 -> OHM-Trade-Agent-v1/deploy/bin/opip-scan-dispatch
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_SCANNER_RELIABILITY_M01.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCANNER-RELIABILITY-M01.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_lifecycle_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_resource_controls_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_manifest_and_recovery_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_docker_fault_injection_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/scan_supervisor.py
AC-002 -> OHM-Trade-Agent-v1/app/services/scan_manifest.py
AC-002 -> OHM-Trade-Agent-v1/deploy/bin/opip-scan-dispatch
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_scan_lifecycle_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/scan_attempt.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/storage/bounded_jsonl.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_scan_lifecycle_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/scan_result_promotion.py
AC-004 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-004 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-004 -> OHM-Trade-Agent-v1/app/jobs/scan_worker.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_scan_remediation_v1.py
AC-005 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-005 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-005 -> OHM-Trade-Agent-v1/app/services/operations_analytics.py
AC-005 -> OHM-Trade-Agent-v1/app/core/config.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_scan_remediation_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/scan_attempt.py
AC-006 -> OHM-Trade-Agent-v1/app/services/scan_supervisor.py
AC-006 -> OHM-Trade-Agent-v1/app/services/operator_control.py
AC-006 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-006 -> OHM-Trade-Agent-v1/deploy/cron.d/ohm-unified-cycle
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_scan_lifecycle_v1.py

DEFERRED DISCOVERIES:

- per-attempt peak memory remains unmeasured until the isolated worker runs in
  production; the 224 MiB ceiling stays provisional
- real Docker fault injection is authored but gated on a Docker host

UNAPPROVED SCOPE CHANGES:

NONE
