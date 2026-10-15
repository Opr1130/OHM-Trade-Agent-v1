INCREMENT:
ATDD-R5-M06-owner-decision-oq01

OWNER-APPROVED INTENT:
This is the dedicated, scoped governance increment the owner directed, and it does exactly one thing: it records the owner's confirmation that the same human operator serves as both **Business Decision Owner** and **Technical Release Owner**, and it maps F1–F12 and G1–G6 accountability to that owner.

What it changes where. No application module changes. It adds one governance register, one scoped acceptance-test module and the accompanying records. It modifies no frozen document, ratifies no number, and activates nothing.

Why the decision is recorded in a new register rather than by editing the requirements register. The frozen requirements register pins its own state in its conformance contract: `test_opip_r5_0_contract_conformance.py` requires the rows whose status is `OWNER_DECISION_REQUIRED` to be exactly `OQ-01`…`OQ-10`, so `OQ-01`'s status must remain `OWNER_DECISION_REQUIRED` there. Although the register's owner vocabulary does accept the value `OWNER`, rewriting an owner cell into a frozen register would ratify a decision *into* a freeze rather than recording it *beside* one, and it would leave the register's own prose ("tracker owners remain UNASSIGNED") contradicting its cells. The owner-decisions register therefore records the decision, and the frozen register is deliberately left unmodified — the same principle the conformance and retirement ledgers already use, that a later value never erases an earlier recorded one.

KEY LAW - RECORDING A DECISION IS NOT RATIFYING POLICY. OD-001 assigns accountability. It declares `NUMERIC_POLICY_RATIFIED_BY_THIS_DECISION = NONE`. It does not ratify `OQ-02`…`OQ-10`, `SC-13`, `C17` or `PM-02`, and it says so explicitly and machine-readably.

KEY LAW - NAMING AN OWNER IS NOT APPROVING A RELEASE. Architecture v1.5.0 §19 requires all readiness gates to pass "with separate exact-release owner approval, legacy drain and rollback". `G6`/`PM-02` therefore keeps `separate_approver=YES`: OD-001 assigns accountability for the gate without supplying the exact-release approval, the legacy drain authorization or the rollback authorization.

KEY LAW - NO PERSONAL IDENTIFIER AND NO CLAIMED SECOND APPROVAL. `DECIDED_BY` names roles only. The register states plainly that one operator holds both roles and that no independent human approval is claimed beyond the authorization actually given.

Two corrections are recorded rather than hidden. First, an earlier probe of mine appeared to confirm that remapping the frozen register was contract-safe: the probe wrote no change (it reassigned a loop variable without writing it back), so both its output and the passing test were meaningless. It was fixed, re-run, and the real answer obtained — the register's owner vocabulary does accept `OWNER`, and the register is nonetheless left unmodified for the reason above. Second, a first draft of the mapping test compared the wrong register column and then a wrongly-prefixed value; both were corrected against the register's real cell order rather than by relaxing the assertion.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md §19: the owner-control clause requiring separate exact-release owner approval, legacy drain and rollback before gate passage and activation.
- docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md: the frozen requirements register carrying the F1–F12, G1–G6 and OQ-01…OQ-10 rows this decision maps and deliberately does not edit.
- docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md: the freeze whose `SC-13` row remains open and is not closed here.
- tests/test_opip_r5_0_contract_conformance.py: the contract that pins the register's shapes, vocabularies and open-input set.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-420:
GIVEN:
the owner-decisions register and its OD-001 record
WHEN:
the decision is read
THEN:
it carries the confirmed status, both named roles, an explicit authority source, an accountability-only scope, an explicit statement that no number was ratified, and no personal identifier, and it states that no independent human approval is claimed

AC-421:
GIVEN:
the F1–F12 and G1–G6 accountability mapping
WHEN:
every mapped row is compared with the frozen register
THEN:
each of the eighteen rows is mapped exactly once, every mapped owner is the designated owner, each feature's mapped gate equals the gate the register already assigns, and the register's own owner cell for that row is still unassigned

AC-422:
GIVEN:
the mapping's separate-approver flags and the architecture's owner-control clause
WHEN:
they are compared
THEN:
G6 is the only row requiring a separate approver, the register explains that the assignment does not supply the exact-release approval, and the cited architecture clause is the one that actually demands it

AC-423:
GIVEN:
the register's non-ratification block and the frozen requirements register
WHEN:
both are inspected
THEN:
exactly OQ-02…OQ-10, SC-13, C17 and PM-02 are declared not ratified without duplication, the register states that no gate can pass until the thresholds decision is taken, and all eighteen feature and gate owner cells in the frozen register still read unassigned with no owner value present

EXPLICITLY OUT OF SCOPE:
- Any ratification of OQ-02 through OQ-10, SC-13, C17 or PM-02.
- Filling, guessing or proposing any numeric threshold, duration, budget, cap, sleeve, rotation, horizon or calibration value.
- Any edit to the frozen requirements register, the contract freeze, the conformance ledger or the retirement ledger.
- Any application module, contract, route, producer, schema, store, writer, job, cron unit, configuration key or release profile.
- Any merge, deployment, activation, mode change, retirement or cutover.
- Any claim of a second or independent human approval.
- Any personal identifier for the operator.

FROZEN BOUNDARIES:
- `TARGET_PAPER` remains `BLOCKED`; the release posture remains `EVIDENCE_SHADOW`; `OPIP_PAPER_V2_MODE` and `OPIP_COMMITTEE_MODE` remain off.
- The Committee remains advisory and shadow-only with no order, risk, execution or protection authority.
- Fixed risk sizing and the minimum reward-to-risk defaults remain binding and unmodified.
- The single canonical writer remains the only authority that commits operational domain events, and this increment writes none.
- No legacy component is retired and no drain is authorized.

ACCEPTANCE TEST TRACEABILITY:
AC-420 -> tests/test_opip_r5_m06_owner_decision.py::test_ac_420_od_001_is_recorded_by_role_and_ratifies_no_number
AC-421 -> tests/test_opip_r5_m06_owner_decision.py::test_ac_421_every_feature_and_gate_is_mapped_to_the_designated_owner
AC-422 -> tests/test_opip_r5_m06_owner_decision.py::test_ac_422_only_g6_carries_a_separate_approver_requirement
AC-423 -> tests/test_opip_r5_m06_owner_decision.py::test_ac_423_nothing_beyond_oq_01_is_ratified
AC-423 -> tests/test_opip_r5_m06_owner_decision.py::test_ac_423_the_frozen_requirements_register_is_left_unmodified

IMPLEMENTATION MAP:
AC-420 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_OWNER_DECISIONS_REGISTER.md
AC-420 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M06-owner-decision-oq01.md
AC-420 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_owner_decision.py
AC-421 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_OWNER_DECISIONS_REGISTER.md
AC-421 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_owner_decision.py
AC-422 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_OWNER_DECISIONS_REGISTER.md
AC-422 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_owner_decision.py
AC-423 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_OWNER_DECISIONS_REGISTER.md
AC-423 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_owner_decision.py
AC-420 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-420 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- The frozen register's owner vocabulary already accepts `OWNER`, so the register is not the obstacle to recording accountability. The obstacle is that rewriting a cell would ratify the decision into a freeze whose conformance contract pins the pre-decision state; the decision is therefore recorded beside it. This distinction is recorded because a future increment could otherwise "helpfully" edit the register and silently pre-empt `SC-13` closure.
- OQ-07 remains the single most consequential open decision: while it is undecided no gate can pass, whatever the code does, because PASS requires approved thresholds.
- One operator holding both roles means the `G6` separate-approval requirement cannot be satisfied by role separation; it must be satisfied by a distinct, separately recorded approval act.
- No headless browser harness exists in this repository, and no rendered-surface claim is made.

UNAPPROVED SCOPE CHANGES:
NONE

