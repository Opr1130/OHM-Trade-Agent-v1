INCREMENT:
ATDD-OPIP-WRITER-HEALTH-WINDOW-V1

OWNER-APPROVED INTENT:
Owner-recommended, unmerged draft: widen the deploy's wait for the canonical writer to become healthy from 60 s to a fixed, bounded 180 s so a one-time index build against a 6 GB store cannot cause a false rollback, without hiding a genuinely failing writer. Deploy-script change only: no CPU or memory limit change, no restart, no deployment, no trading, learning, Committee, Early Watch or F11 change.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md: writer start-up risk R-1 and host evidence.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a writer whose start-up is slow but working, reporting starting or unhealthy until its socket listens
WHEN:
the deploy waits for writer health
THEN:
a writer that becomes healthy within 180 s succeeds, a writer that is healthy at once adds no delay, and the window is one fixed named constant with no environment override

AC-002:
GIVEN:
a writer that has exited, died, is restarting, was OOM-killed, or whose restart count rises during the wait
WHEN:
the deploy waits for writer health
THEN:
the wait fails on the first poll that observes it, without consuming the window, and names the reason

AC-003:
GIVEN:
a writer that never becomes healthy or a container that never appears
WHEN:
the deploy waits for writer health
THEN:
it fails after exactly 90 polls of 2 s (180 s), so the worst case is bounded, and both existing callers still fail the deploy and enter the existing rollback unchanged

EXPLICITLY OUT OF SCOPE:
- Writer CPU or memory limits, compose healthcheck timing, the activation script's own wait, any restart or deployment.
- The 2,700 s cycle budget, F11, funded trading, learning, Committee, Early Watch.

FROZEN BOUNDARIES:
- Docker 'unhealthy' alone is not fatal (the probe fails until start-up finishes) but a stopped, restarting, OOM-killed or restart-looping writer always is.
- A failed wait still fails the deploy and triggers the existing rollback.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_writer_health_window_v1.py::test_ac_001_the_window_is_a_fixed_180_second_constant_with_no_override
AC-001 -> tests/test_opip_writer_health_window_v1.py::test_ac_001_a_slow_but_working_start_up_inside_180_seconds_succeeds
AC-001 -> tests/test_opip_writer_health_window_v1.py::test_ac_001_an_immediately_healthy_writer_does_not_wait
AC-002 -> tests/test_opip_writer_health_window_v1.py::test_ac_002_a_crashed_or_oom_killed_writer_fails_immediately
AC-002 -> tests/test_opip_writer_health_window_v1.py::test_ac_002_a_restart_loop_is_detected_before_the_window_ends
AC-003 -> tests/test_opip_writer_health_window_v1.py::test_ac_002_a_writer_that_never_becomes_healthy_fails_after_exactly_180_seconds
AC-003 -> tests/test_opip_writer_health_window_v1.py::test_ac_002_a_missing_container_is_bounded_not_endless
AC-003 -> tests/test_opip_writer_health_window_v1.py::test_ac_003_both_call_sites_still_fail_the_deploy_and_the_state_template_is_pinned

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_writer_health_window_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-WRITER-HEALTH-WINDOW-V1.md
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_writer_health_window_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-WRITER-HEALTH-WINDOW-V1.md
AC-003 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_writer_health_window_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-WRITER-HEALTH-WINDOW-V1.md

DEFERRED DISCOVERIES:
- OPEN: the first start of the candidate writer against the 6.08 GB production store has not been observed; it must be timed on a separately owner-approved deployment (first-start timestamps).
- OPEN: candidate writer RSS after start-up (deployed code sits at 81% of 128 MiB) must be checked on that deployment.

UNAPPROVED SCOPE CHANGES:
NONE
