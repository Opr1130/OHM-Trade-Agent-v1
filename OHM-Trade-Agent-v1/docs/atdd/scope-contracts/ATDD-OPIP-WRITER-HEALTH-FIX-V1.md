INCREMENT:
ATDD-OPIP-WRITER-HEALTH-FIX-V1

OWNER-APPROVED INTENT:
Owner-reported P0 correction of two defects in the writer health wait merged as #575 (`5fa1bddc`): the container listing omitted stopped containers, and a "healthy" reading was accepted before the fatal container states were evaluated. Deploy-script change only: no CPU or memory limit change, no restart, no deployment, no trading, learning, Committee, Early Watch or F11 change.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md: writer start-up risk R-1 and host evidence.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a canonical writer container that has exited or died during start-up, which `docker compose ps -q` does not list
WHEN:
the deploy waits for writer health
THEN:
the container is still found because the listing includes stopped containers, and the failure is reported on the first poll instead of consuming the window

AC-002:
GIVEN:
a writer whose reported health is `healthy` while the container is exited, restarting, OOM-killed, or its restart count has risen
WHEN:
the deploy waits for writer health
THEN:
the fatal state is evaluated first and fails the wait, so a stale or racing health reading cannot be accepted

AC-003:
GIVEN:
a writer that never becomes healthy
WHEN:
the deploy waits for writer health
THEN:
the fixed 180-second bound and both unchanged callers still fail the deploy into the existing rollback

EXPLICITLY OUT OF SCOPE:
- Writer CPU or memory limits, compose healthcheck timing, the activation script's own wait, any restart or deployment.
- Other `docker compose ps` call sites in the deploy script; the 2,700 s cycle budget, F11, funded trading, learning, Committee, Early Watch.

FROZEN BOUNDARIES:
- Docker `unhealthy` alone stays non-fatal (the probe fails until start-up finishes); a stopped, restarting, OOM-killed or restart-looping writer always fails.
- The health window stays a fixed 180 s constant with no environment override.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_writer_health_window_v1.py::test_fix_001_the_writer_is_listed_with_all_so_a_stopped_container_is_visible
AC-001 -> tests/test_opip_writer_health_window_v1.py::test_fix_001_a_stopped_writer_is_reported_at_once_not_after_180_seconds
AC-002 -> tests/test_opip_writer_health_window_v1.py::test_fix_002_a_fatal_state_wins_over_a_stale_healthy_reading
AC-002 -> tests/test_opip_writer_health_window_v1.py::test_fix_002_a_restart_that_looks_healthy_is_still_a_loop
AC-003 -> tests/test_opip_writer_health_window_v1.py::test_fix_003_the_180_second_bound_and_rollback_contract_are_preserved

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_writer_health_window_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-WRITER-HEALTH-FIX-V1.md
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_writer_health_window_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-WRITER-HEALTH-FIX-V1.md
AC-003 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_writer_health_window_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-WRITER-HEALTH-FIX-V1.md

DEFERRED DISCOVERIES:
- OPEN: the candidate writer's first start against the 6.08 GB production store is still unobserved; first-start timestamps and RSS must be captured on a separately owner-approved deployment.
- NOTED: other `docker compose ps -q` call sites in the deploy script read already-running services, not a service under start-up; deliberately unchanged here.

UNAPPROVED SCOPE CHANGES:
NONE
