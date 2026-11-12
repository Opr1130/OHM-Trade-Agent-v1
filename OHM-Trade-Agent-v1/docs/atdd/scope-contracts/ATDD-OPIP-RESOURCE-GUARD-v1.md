INCREMENT:
ATDD-OPIP-RESOURCE-GUARD-v1

OWNER-APPROVED INTENT:
Preserve the owner's November 12, 2026 manual production cleanup by preventing an
automatically reinstalled evidence export cron from copying a multi-GB canonical
database unless the root-owned operator deliberately enables export. Bound optional
capture/inbox/shadow jobs under low host MemAvailable and stagger only the
one-minute workers with proven 55s in-container termination. Keep the existing
scheduler installer and the protected unified cycle unchanged; learning and paper
trading remain off. This change requires review and separate release approval.

ARCHITECTURE REFERENCES:
- deploy/remote/export-opip-learning-evidence.sh: early, silent, regular-file authorization check.
- deploy/remote/opip-run-guarded: one MemAvailable gate for optional cron commands.
- deploy/cron.d/opip-feature-bus-capture and opip-feasibility-evidence-capture: bounded in-container capture.
- deploy/cron.d/ohm-tradingview-inbox: operator-installed optional inbox with explicit bounded in-container drain.
- deploy/cron.d/opip-irr-shadow: owner-authorized isolated shadow worker, no stagger due to its >60s worker limit.
- deploy/cron.d/ohm-unified-cycle and deploy/remote/reconcile-scheduler.sh: unchanged protected cycle and installation allowlist.
- tests/test_opip_resource_guard_v1.py: hermetic regression evidence.
- tests/test_canonical_learning_replica_transfer_e2e_v1.py: root-only CI fixture opts into export for the existing real transfer contract.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the scheduler reconciles and reinstalls the legacy learning-export cron
WHEN: the export shell is invoked and /etc/opip/learning-export.enabled is not a real regular file
THEN: the exporter exits 0 silently before any source reads, locks, file writes or SQLite copy

AC-002:
GIVEN: an optional cron process wrapped in the memory guard
WHEN: MemAvailable is below the default 512 MiB threshold or cannot be read
THEN: the guard writes one SKIPPED log entry and exits 0 without running the child; with sufficient memory it execs the unchanged command

AC-003:
GIVEN: the existing scheduler with independent host flock and container bounds
WHEN: optional per-minute capture, inbox or shadow cron jobs are scheduled
THEN: each is memory guarded, flock -n is retained, bounded captures and inbox are offset by 2, 3 or 4 seconds with offset plus 55s <=59s, the 150s isolated IRR is not staggered, and unified-cycle cadence and the installed-cron allowlist remain unchanged

EXPLICITLY OUT OF SCOPE:
- No edits to deploy-*.yml, reconcile-scheduler.sh, production Compose, scheduler inventory or installation markers.
- No trading, Kraken, F11, Telegram, alert admission, scoring, provider, learning activation, paper trading activation, data deletion or production restart.
- No independent scheduler, service, persistent daemon or new network listener.
- No automatic re-enablement of export and no merge or deployment by this PR.

FROZEN BOUNDARIES:
- Existing cron entries and flock retain one non-overlapping invocation.
- Protected unified-cycle (including F11 monitoring) is not memory gated or retimed.
- Capture timeouts remain inside the container; no host timeout can orphan a container process.
- Export authorization is fail-closed and is checked before any side effect.
- Host memory guard is advisory admission for optional jobs, never a proxy for container-cgroup memory limits.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_resource_guard_v1.py::test_export_exits_silently_without_explicit_enable_file
AC-002 -> tests/test_opip_resource_guard_v1.py::test_guard_skips_under_low_memory_without_running_child
AC-002 -> tests/test_opip_resource_guard_v1.py::test_guard_execs_command_when_memory_is_available
AC-002 -> tests/test_opip_resource_guard_v1.py::test_guard_fails_closed_when_meminfo_is_unavailable
AC-003 -> tests/test_opip_resource_guard_v1.py::test_crons_keep_flock_and_stagger_only_bounded_optional_jobs

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/export-opip-learning-evidence.sh
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_resource_guard_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_canonical_learning_replica_transfer_e2e_v1.py
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/opip-run-guarded
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_resource_guard_v1.py
AC-003 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-feature-bus-capture
AC-003 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-feasibility-evidence-capture
AC-003 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-irr-shadow
AC-003 -> OHM-Trade-Agent-v1/deploy/cron.d/ohm-tradingview-inbox
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_resource_guard_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-GUARD-v1.md

DEFERRED DISCOVERIES:
- On next authorized release, cron reinstall is expected but export should remain dark unless the flag exists; validate this independently on host.
- Guard threshold evaluates host MemAvailable, not Docker memory cgroups; skipping optional capture may create an explicit evidence gap.
- Optional inbox is not installed by reconcile-scheduler; changing its source does not install it.
- IRR has a longer 150-second isolated worker timeout, so it keeps zero offset and its existing flock.
- Exact PR-head CI is required after push; full local test suite was not available in this connector-only environment.

UNAPPROVED SCOPE CHANGES:
NONE
