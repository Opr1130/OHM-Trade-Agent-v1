INCREMENT:
ATDD-R4-F8A-readiness-observability

OWNER-APPROVED INTENT:
This is a narrow, owner-authorized observability follow-up to the merged and dormant-deployed R4-A cutover-readiness increment (`ATDD-R4-F8-paper-v2-cutover-readiness`, merged as `a3b7d57e5d189c2741cfd4d6dda0136a42554113`). R4-A added a bounded read-only readiness probe but exposed no sanctioned way to observe it in production; the only forced-command entry points are `deploy <sha>` and `diagnose-learning`, and the latter runs only fixed read-only probes. This increment makes the deployed probe observable through that existing audited read-only path by invoking it from `deploy/remote/diagnose-opip-learning.sh`, and adds the wrapper to the CI shell-syntax gate. It adds NO authority, NO activation, NO mutation, NO new remote command dispatch, and NO change to the readiness job itself.

STARTING SHA. `origin/main` = `a3b7d57e5d189c2741cfd4d6dda0136a42554113`. If `origin/main` moves, the branch is inspected and reconciled if R4-A, the diagnostics wrapper, the forced-command gateway or the ATDD scope is affected.

WHY THIS IS NOT A REMOTE EXECUTION WIDENING. The forced-command gateway (`deploy/remote/ohm-deploy-ssh`) is unchanged and still accepts exactly two entry points. The caller can never supply a command: the wrapper reads no `$SSH_ORIGINAL_COMMAND`, takes no positional argument, and the probe it runs is a fixed, deployed, read-only module. The caller can only choose to run the existing `diagnose-learning` diagnostic; this increment changes what that fixed diagnostic reports, not who may run what.

WHY THE PROBE IS SAFE TO RUN AUTOMATICALLY. `app.jobs.report_paper_v2_cutover_readiness` is read-only by construction and proven so by the frozen R4-F8 acceptance tests: it performs no environment mutation, imports no exchange, Committee or Feature-Bus module, advances no Paper-v2 lifecycle work, and reports a status rather than granting authority. It is time-boxed and byte-bounded in the wrapper so it cannot stall or flood diagnostics.

WHY `NOT_READY` MUST NOT DEGRADE. The expected current verdict is `NOT_READY` (the SHORT-authority gap). Treating a readiness verdict as a diagnostic failure would turn expected evidence into noise and would push an owner toward "fixing" it by activating something. Only an *unavailable* probe degrades, because an unreadable readiness probe is not a proven-clear one.

ARCHITECTURE REFERENCES:
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R4: "Cutover stays blocked until ... Protection sweep is healthy before new admissions ... Universe metadata is present." The readiness probe reports those gates; this increment only makes its evidence observable.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3: "Missing evidence is never favorable." An unavailable probe degrades rather than reading as clear.
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` F8 row: paper v2 mode default `off`, live mode `UNKNOWN_NEEDS_EVIDENCE`. Observing it is the point of this increment; nothing here selects an engine.
- `docs/atdd/scope-contracts/ATDD-R4-F8-paper-v2-cutover-readiness.md`: the frozen R4-A increment whose probe this increments observes. Its semantics, contract and tests are not modified.
- `tests/test_opip_export_diagnostics_readonly_v1.py`: the existing read-only diagnostics contract (bounded, redacted, no mutation, no signalling, no argv, exactly two gateway entry points) is preserved.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture; `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the sanctioned read-only diagnostics wrapper
WHEN:
it runs its production probes
THEN:
it invokes the deployed readiness job through the existing core-container exec path exactly once, emits bounded section markers, is time-boxed on both the host and container side, bounds the probe's output while it streams, rejects a failed, timed-out or truncated probe as UNAVAILABLE rather than printing a fragment, reports UNAVAILABLE when the container is absent, and emits no raw argv or environment

AC-002:
GIVEN:
the readiness section
WHEN:
the probe reports a verdict
THEN:
a NOT_READY verdict is reported as expected evidence and never degrades diagnostics, only an unavailable probe degrades, the section parses no verdict text, it precedes the final status line so it survives the bounded output window, and its byte bound stays a minority of the published window so the diagnostics it accompanies are not crowded out

AC-003:
GIVEN:
this follow-up
WHEN:
mutation, activation and remote authority are audited
THEN:
the readiness section writes nothing, activates no mode and signals no process, the forced-command gateway keeps exactly its two entry points with no shell passthrough and no caller-supplied command, the wrapper remains argument- and argv-free, and the deployed job starts only the read-only readiness report

AC-004:
GIVEN:
the modified wrapper
WHEN:
its structure and CI coverage are inspected
THEN:
the section is balanced and inside a single container guard with no nested function or heredoc, and the wrapper is added to the CI shell-syntax gate

EXPLICITLY OUT OF SCOPE:
- Activating Paper v2, the Feature Bus, the Committee, or any AI runtime authority
- Funded trading, live exchange execution, or Kraken order placement, modification, cancellation or confirmation
- Any new SSH entry point, shell passthrough, caller-supplied remote command, or widened remote execution authority
- Changing the readiness probe's semantics, verdicts, reason codes or tests (R4-A is frozen)
- Wiring F7 as the live admission authority or retiring any legacy paper path
- A Paper-v2 short engine or pending-limit state machine
- Modifying architecture documents, the v1.4.3 DOCX, the frozen ATDD contracts, or the ATDD checker
- Weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
- Forced-command gateway entry points remain exactly `deploy <40-hex>` and `diagnose-learning`.
- The caller can never supply a command; the wrapper reads no `$SSH_ORIGINAL_COMMAND` and no positional argument.
- `OPIP_PAPER_V2_MODE` remains unset/`off`; nothing here activates or configures it.
- The R4-A readiness job, contracts and tests are unchanged.
- The diagnostics wrapper remains read-only: bounded, redacted, no mutation, no signalling, no lock acquisition.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled.
- A normal push of this feature branch and its review, merge and dormant deploy are permitted as recorded by the owner.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_001_wrapper_runs_the_deployed_readiness_probe
AC-001 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_001_probe_is_time_boxed_and_byte_bounded
AC-001 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_001_probe_reports_unavailable_when_the_container_is_not_running
AC-001 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_001_incomplete_probe_is_rejected_not_printed
AC-001 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_001_probe_emits_no_raw_argv_or_environment
AC-002 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_002_unavailable_probe_degrades_but_a_verdict_does_not
AC-002 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_002_readiness_section_precedes_the_final_status_line
AC-002 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_002_published_window_leaves_room_for_earlier_diagnostics
AC-003 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_003_section_performs_no_mutation_or_activation
AC-003 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_003_forced_command_gateway_is_unchanged
AC-003 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_003_readiness_job_has_no_activation_or_write_surface
AC-003 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_003_wrapper_remains_argument_and_argv_free
AC-004 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_004_wrapper_is_syntax_checked_by_ci
AC-004 -> tests/test_opip_r4_f8a_readiness_observability.py::test_ac_004_readiness_section_is_well_formed

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/diagnose-opip-learning.sh
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/diagnose-opip-learning.sh
AC-002 -> .github/workflows/deploy-production.yml
AC-003 -> OHM-Trade-Agent-v1/deploy/remote/diagnose-opip-learning.sh
AC-004 -> OHM-Trade-Agent-v1/deploy/remote/diagnose-opip-learning.sh
AC-004 -> .github/workflows/pytest.yml
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F8A-readiness-observability.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r4_f8a_readiness_observability.py

DEFERRED DISCOVERIES:
- A `diagnose-readiness`-style dedicated entry point is not added: a new forced-command entry point would widen remote execution authority, so the probe rides the existing `diagnose-learning` diagnostic instead.
- If the readiness report ever grows beyond the bounded window, the wrapper's byte bound must be revisited rather than removing the truncation.
- The readiness probe's own live verdict remains to be observed after this increment deploys.

UNAPPROVED SCOPE CHANGES:
NONE
