INCREMENT:
ATDD-OPIP-STABILITY-EVIDENCE-v1

OWNER-APPROVED INTENT:
Implement the owner-approved read-only, resource-bounded stability observation
increment for scanner, AI and Telegram evidence; add non-activated log rotation
policy. This increment changes no trading, learning, AI pricing, notification
gates or F11 protection policy. Runtime configuration remains unchanged.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/tools/opip_stability_snapshot.py: read-only bounded offline CLI.
- OHM-Trade-Agent-v1/deploy/logrotate.d/opip: host retention policy, not installed.
- OHM-Trade-Agent-v1/docs/operations/stability-evidence-readonly-2026-11.md: explicit operational limits.

APPROVED ACCEPTANCE CRITERIA:

AC-201:
GIVEN: a set of timestamped scanner, AI-router, OpenAI usage and Telegram ledgers
WHEN: an operator requests an offline 24-hour observation summary
THEN: the output contains only aggregates and does not disclose source record payloads

AC-202:
GIVEN: missing or unreadable monitoring ledgers
WHEN: the observation summary is produced
THEN: coverage is INCOMPLETE rather than a fabricated complete production measurement

AC-203:
GIVEN: a source ledger larger than the configured read limit
WHEN: the bounded snapshot reads the source
THEN: only the tail is examined and truncation is explicitly reported

AC-204:
GIVEN: cycle log entries with valid timestamps and stale or unstamped messages
WHEN: the cycle observation window is evaluated
THEN: only fresh timestamped cycle completions are counted

EXPLICITLY OUT OF SCOPE:
- Merging or deploying code; installing the logrotate policy; restarting a service.
- Editing any live host runtime configuration or provider budgets.
- Activating learning, Committee, paper or funded trading.
- Changing F11 admissions, Telegram eligibility or Chief authorization semantics.
- Deleting historical data, export snapshots, or changing any other increment's contract.
- Claiming actual dollar cost from token counts or host RSS from container measurements.

FROZEN BOUNDARIES:
- The snapshot is read-only, has bounded source reads and emits aggregate evidence.
- Production and deploy SSH forced-command policies are unchanged.
- Existing strategy, notification and F11 fail-closed controls are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-201 -> tests/test_opip_stability_snapshot_v1.py::test_counts_are_aggregates_not_raw_records
AC-202 -> tests/test_opip_stability_snapshot_v1.py::test_missing_ledgers_are_unknown_not_zero_coverage
AC-203 -> tests/test_opip_stability_snapshot_v1.py::test_large_ledger_tail_is_bounded_and_declares_truncation
AC-204 -> tests/test_opip_stability_snapshot_v1.py::test_cycle_liveness_only_counts_timestamped_completions

IMPLEMENTATION MAP:
AC-201 -> OHM-Trade-Agent-v1/tools/opip_stability_snapshot.py
AC-201 -> OHM-Trade-Agent-v1/tests/test_opip_stability_snapshot_v1.py
AC-202 -> OHM-Trade-Agent-v1/tools/opip_stability_snapshot.py
AC-202 -> OHM-Trade-Agent-v1/tests/test_opip_stability_snapshot_v1.py
AC-203 -> OHM-Trade-Agent-v1/tools/opip_stability_snapshot.py
AC-203 -> OHM-Trade-Agent-v1/tests/test_opip_stability_snapshot_v1.py
AC-204 -> OHM-Trade-Agent-v1/tools/opip_stability_snapshot.py
AC-204 -> OHM-Trade-Agent-v1/tests/test_opip_stability_snapshot_v1.py
AC-201 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-201 -> OHM-Trade-Agent-v1/deploy/logrotate.d/opip
AC-201 -> OHM-Trade-Agent-v1/docs/operations/stability-evidence-readonly-2026-11.md
AC-201 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-STABILITY-EVIDENCE-v1.md

DEFERRED DISCOVERIES:
- Operator-authorized host probes are needed to verify per-container RSS, restarts and CPU.
- AI cache and token-budget controls already exist but effective values are unverified.
- AI outage-to-alert behavior is safety-critical and requires a distinct authorized change.
- Existing host controller must separately approve installation of log rotation.
- Current learning export drift remains intentionally outside this increment.

UNAPPROVED SCOPE CHANGES:
NONE
