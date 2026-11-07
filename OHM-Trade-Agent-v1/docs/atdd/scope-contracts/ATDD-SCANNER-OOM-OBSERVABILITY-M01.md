INCREMENT:
ATDD-SCANNER-OOM-OBSERVABILITY-M01

OWNER-APPROVED INTENT:
The owner directs a bounded correction of the single post-merge CI failure on
10c9e805: tests/test_opip_scan_docker_fault_injection_v1.py::test_oom_killed_flag_is_observable.
The same test passed on the same ubuntu-24.04 runner image 54 minutes earlier
with an identical collection (10053 passed, 15 skipped), and the failing run
recorded only `assert 'true' in {'false'}` - sixty `docker inspect` reads over
about thirty seconds all returned `OOMKilled=false`, with no record of the exit
code, the applied memory limit or the kernel's own OOM counter. The test's
environment assumptions are therefore the defect: it polls a flag that Docker
publishes only once the container has exited, it lets the workload allocate
immediately at start so the OOM kill races the daemon's attribution of it, and
it never verifies that the memory cap was applied. This increment corrects those
assumptions, keeps the OOM assertion genuine, and adds the measured evidence a
future failure needs. It changes no product code: the scanner supervisor's
fail-closed OOM classification is frozen and must keep working. No deployment,
merge, funded trading, paper admission, learning or IRR activation is authorized.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/services/scan_supervisor.py: classify_exit maps State.OOMKilled to OOM_KILLED/CGROUP_OOM_KILL and 137/143 to DEADLINE_KILLED, so the flag is production-critical.
- OHM-Trade-Agent-v1/tests/test_opip_scan_lifecycle_v1.py: the parser-level OOM classification contract (unchanged).
- OHM-Trade-Agent-v1/OPIP_SCANNER_RELIABILITY_M01.md: `docker compose run` without `--rm` so exit code and OOMKilled stay observable.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1.md: the increment this one follows.
- AGENTS.md: fail-closed gates, no test weakening, evidence separation.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: a container runs under a memory cap far below what its workload allocates
WHEN: the workload allocates only after the daemon has observed the container running, and the observation then waits for the container to exit
THEN: the measured state proves the cap was applied, the container exited, the kernel killed PID 1 with SIGKILL, and Docker reported OOMKilled true.

AC-002:
GIVEN: the OOM observation does not see the OOM flag, or the container state is unavailable
WHEN: the observation reports its result
THEN: the failure records the exit code, the OOMKilled value, the running state, the applied memory limit and the kernel cgroup oom_kill counter, and the assertion is never skipped, deleted or relaxed to obtain green CI.

AC-003:
GIVEN: this increment is the active ATDD increment
WHEN: the scope checker evaluates the contract and the changed-file set
THEN: the contract declares every required section in order with GIVEN, WHEN and THEN criteria, every acceptance test is traced to its criterion, and only files mapped to an approved criterion may change.

AC-004:
GIVEN: a completed increment's acceptance test observes the global ATDD pointer
WHEN: the pointer is deliberately moved to a later approved increment
THEN: that test resolves the pointer to an existing scope contract and proves its own identity from its own frozen contract, instead of pinning the pointer to its own increment and blocking the next one.

EXPLICITLY OUT OF SCOPE:
- Any change to app/services/scan_supervisor.py, classify_exit, the OOM outcome vocabulary or the scanner's fail-closed behavior.
- Any change to the dispatcher, the isolated worker, resource controls, or the Compose memory limits.
- Skipping, deleting, xfailing or weakening test_oom_killed_flag_is_observable, or replacing its Docker evidence with a mock.
- Any change to providers, AI routing, scoring, thresholds, ranking, F11, paper admission, learning or execution authority.
- Deployment, merge or production configuration change.

FROZEN BOUNDARIES:
- The OOM assertion stays real-Docker and fail-closed: a host that cannot observe the flag fails the test.
- The skip condition for both Docker-dependent checks is unchanged in effect: they run only where a Docker daemon exists.
- classify_exit and the scanner supervisor are untouched.
- No test is skipped, removed or relaxed to obtain green CI.
- The added evidence is recorded in the failure message, never asserted, so it cannot mask a genuine failure.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_scan_docker_fault_injection_v1.py::test_oom_killed_flag_is_observable
AC-002 -> tests/test_opip_scan_docker_fault_injection_v1.py::test_oom_evidence_reports_exit_limit_and_cgroup_counter
AC-002 -> tests/test_opip_scan_docker_fault_injection_v1.py::test_oom_evidence_tolerates_an_unreadable_container
AC-003 -> tests/test_opip_scan_docker_fault_injection_v1.py::test_scope_contract_declares_the_active_increment
AC-004 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_012_scope_contract_declares_the_active_increment

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_docker_fault_injection_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_scan_docker_fault_injection_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_scan_docker_fault_injection_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCANNER-OOM-OBSERVABILITY-M01.md
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
Local execution of the Docker-dependent assertion was impossible in the
development environment: this host has no Docker CLI or daemon and no WSL, so the
focused test skips here exactly as its skip condition specifies. The correction is
therefore validated by the daemon-free diagnostics tests locally and by CI for the
Docker assertion itself: that run executed both Docker checks and they passed on
ubuntu-24.04. The same run exposed a second defect of the same class - the
governance check in tests/test_opip_ai_integration_reliability_v1.py pinned the
global ACTIVE_INCREMENT pointer to its own completed increment, which the movable
pointer doctrine forbids because it blocks the next approved increment - and
correcting that pin is part of this increment. Two mechanisms remain candidates for
the observed false negative and the added evidence separates them: if the next
occurrence reports ExitCode=137 with a non-zero cgroup_oom_kill counter and
OOMKilled=False, the daemon dropped the flag and the production risk is in
classify_exit's dependence on it, which then needs its own increment; if it
reports LIMIT_NOT_ENFORCED_EXIT=66 or Running=True, the daemon did not apply the
memory cap and the fault is in the runner, not in the scanner. The cgroup counter
read is best-effort because it depends on the host cgroup layout and on read
permission, and it is deliberately not asserted.

UNAPPROVED SCOPE CHANGES:
NONE
