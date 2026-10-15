INCREMENT:
ATDD-R5-M07-readiness-evidence

OWNER-APPROVED INTENT:
This increment adds the readiness evidence Mission 07 asks for, and nothing else: it proves how effective ownership resolves from an append-only decision record without erasing frozen history, and it drives the real Paper-v2 recovery entry point to prove its orchestration contract.

Started from the verified top-of-stack recovery head. The branch is cut from `34901ef4` (PR #394), whose full Linux `test` suite is green, and not from the local ownership branch `eebbafb4`, whose `test` job failed on an unrelated timing-sensitive test.

Where it runs. Both contracts are read-only. Nothing activates, changes a mode, restarts a service, admits a trade, writes canonical evidence, or ratifies a threshold. No application module changes.

KEY LAW - A DECISION SUPERSEDES, IT NEVER ERASES. The frozen requirements register keeps its pre-decision owner cells as source history; the append-only owner-decision record supplies the effective value. A row with no decision resolves to its frozen value rather than to an invented one. `OD-001` is not reinterpreted, and no separate exact-release approval is supplied.

KEY LAW - UNREADABLE EVIDENCE IS NOT EMPTY EVIDENCE. When the recoverable-execution projection is not OK, or raises, recovery reports `UNAVAILABLE` naming the upstream failure, considers nothing and submits no canonical event.

KEY LAW - UNSAFE WORK KEEPS ITS RESERVATION. A trade that cannot safely proceed is counted as considered and reported retryable with its real failure reason; nothing is completed, nothing is released, and no canonical event is submitted.

KEY LAW - ONE BAD TRADE DOES NOT STOP THE SWEEP. A failure on one entry does not prevent a later entry from being considered.

KEY LAW - RECOVERY IS NOT ADMISSION. The recovery path resubmits only already-authorized work; the entry point's documented invariants forbid admitting, requalifying or reranking.

ARCHITECTURE REFERENCES:
- app/services/paper_v2_execution.py: `recover_outstanding_paper_v2_trades`, the real application-level recovery entry point, and `submit_canonical_event`, which requires durable-commit proof.
- app/opip/canonical/decision_context_bridge.py: `require_canonical_commit`, which fails closed on a missing acknowledgement or missing canonical identity.
- docs/architecture/OPIP_OWNER_DECISIONS_REGISTER.md: the append-only decision record that supplies effective ownership.
- docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md: the frozen register that retains source history and is deliberately not rewritten.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-440:
GIVEN:
the frozen register and the append-only owner-decision record
WHEN:
the effective owner is resolved for every feature and gate row and the decision record is read in identifier order
THEN:
every row resolves to the designated owner while the frozen cell it supersedes still reads its original pre-decision value, a row with no decision resolves to its frozen value, `OD-001` is the first and only decision, its ratified-number statement is NONE and its scope is accountability only, and it states that it claims no second approval

AC-441:
GIVEN:
a recoverable-execution projection that is not OK, and one whose accessor raises
WHEN:
recovery is run
THEN:
each reports UNAVAILABLE naming the upstream error code or failure type, considers nothing, completes nothing and submits no canonical event

AC-442:
GIVEN:
a work list of entries carrying no committed payloads
WHEN:
recovery is run
THEN:
every entry is counted as considered, each is reported retryable with the real failure type and reason, nothing is completed, no canonical event is submitted, and a later entry is still considered after an earlier one failed

EXPLICITLY OUT OF SCOPE:
- Any ratification of a threshold, duration, budget, cap, sleeve, horizon or calibration value.
- Any rewrite of the frozen requirements register, or any reinterpretation of `OD-001`.
- Any supply of `G6`'s separate exact-release approval.
- Any application module, contract, storage format, configuration key or release profile change.
- Any real client, credential, endpoint, container, service restart or mode change.
- Any merge, rebase, force-push, PR base change, ready-for-review marking or deployment.
- Any legacy drain, deletion or retirement.

FROZEN BOUNDARIES:
- `TARGET_PAPER` remains `BLOCKED`; posture remains `EVIDENCE_SHADOW`; Paper v2 and Committee modes remain off.
- The Committee remains advisory and shadow-only with no authority.
- Fixed risk sizing and the minimum reward-to-risk defaults remain binding and unmodified.
- The single canonical writer remains the only authority that commits operational domain events; this increment writes none.

ACCEPTANCE TEST TRACEABILITY:
AC-440 -> tests/test_opip_r5_m07_readiness_evidence.py::test_ac_440_effective_ownership_resolves_and_frozen_history_is_retained
AC-440 -> tests/test_opip_r5_m07_readiness_evidence.py::test_ac_440_decisions_are_append_only_and_the_first_decision_is_intact
AC-441 -> tests/test_opip_r5_m07_readiness_evidence.py::test_ac_441_an_unreadable_projection_is_unavailable_and_does_no_work
AC-441 -> tests/test_opip_r5_m07_readiness_evidence.py::test_ac_441_a_projection_that_raises_is_unavailable_not_a_crash
AC-442 -> tests/test_opip_r5_m07_readiness_evidence.py::test_ac_442_a_trade_that_cannot_safely_proceed_is_retryable_not_released

IMPLEMENTATION MAP:
AC-440 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M07-readiness-evidence.md
AC-440 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m07_readiness_evidence.py
AC-441 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m07_readiness_evidence.py
AC-442 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m07_readiness_evidence.py
AC-440 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-440 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- The resolution rule is implemented as an executable contract in the test module because no read-plane consumer of owner assignment exists yet. Promoting it into an application module is a follow-on for when one does; inventing the module now would add an unused surface.
- Recovery's happy path — completing a fill from a committed quote — is not proven here: it needs the committed quote-evidence payload shape and guessing it would be fabrication. Only the fail-closed, retryable and unavailable paths are proven. The happy path remains `INSUFFICIENT_EVIDENCE`.
- Two mismatches were found by the tests rather than relaxed: an entry missing its payload keys raises `KeyError` before the explicit guard, so the test now uses present-but-malformed payloads to exercise the real message; and the first draft asserted a string that did not match the register's table formatting.
- Operational rollback still requires authorized infrastructure; nothing here executes one.
- No headless browser harness exists, and no rendered-surface claim is made.

UNAPPROVED SCOPE CHANGES:
NONE

