INCREMENT:
ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1

OWNER-APPROVED INTENT:
Make the movable ACTIVE_INCREMENT lifecycle rule canonical scope-control governance. `docs/atdd/ACTIVE_INCREMENT` is mutable orchestration state that names the single increment currently authorized to change files; exact current-increment enforcement belongs to `tests/atdd_scope.py` and the pull-request CI job, never to a completed increment's acceptance test. Three product increments have already needed a one-off OWNER authorization to remove a stale pointer pin (R3-F3 AC-014, R3-F4 AC-017, R3-F5 AC-027), and the completed R3-F6 forecast-engine increment reintroduced the same obsolete pattern when its AC-036 permanently required the pointer to equal F6. This increment removes that F6 ownership pin, rewrites AC-036 to prove F6's own contract and test identity and require only that the pointer resolve to an existing scope contract, and strengthens the structural guard so it catches pointer pins expressed through direct pointer expressions and canonical `read_increment_pointer()` calls without flagging generic namespace checks. It authorizes only the minimum governance, documentation and scope-control test paths required: the ATDD pointer, the ATDD README, this contract, the scope-control test module, the F6 acceptance test whose pointer pin is removed, the F6 contract that records the handoff, and the F5 contract whose AC-027 wording records the corrected rule. It changes no runtime, trading, Feature Bus, paper, deployment or production behaviour, and it grants no authority to any increment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/atdd/README.md: the ATDD scope-control mechanism, its precedence, and the `ACTIVE_INCREMENT` pointer.
- OHM-Trade-Agent-v1/tests/atdd_scope.py: the canonical parser and checker. It already fails closed on a missing, ambiguous or unknown active increment, and it is the sole owner of exact current-increment enforcement.
- OHM-Trade-Agent-v1/tests/test_opip_r0r1_audit_reconciliation.py: the existing durable pattern. Its AC-001 test states that the pointer is expected to move, verifies only that it names an existing scope contract, and never pins its value.
- OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py and OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F6-forecast-engine.md: the completed F6 increment whose AC-036 reintroduced a completed-increment pointer pin; this increment removes it and records the handoff, changing no forecast semantic.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md AC-014 and OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F5-feasibility-safety.md "F4 GOVERNANCE HANDOFF": the prior one-off OWNER authorizations this increment replaces with a general rule.
- AGENTS.md and CLAUDE.md: smallest change that solves the proven problem, no runtime/trading authority, no new infrastructure.
- .github/workflows/pytest.yml: the `atdd scope` job reads `docs/atdd/ACTIVE_INCREMENT` and compares the pull-request diff with that increment only.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
this governance increment, the movable ATDD pointer, and the scope checker
WHEN:
the pointer and this contract are inspected
THEN:
the pointer names an existing scope contract, this contract exists and declares its own increment, and this contract's implementation map authorizes exactly the seven governance paths listed here and no runtime, deployment, architecture or application path

AC-002:
GIVEN:
the ATDD documentation
WHEN:
the movable-pointer rule is read
THEN:
it states explicitly that ACTIVE_INCREMENT is mutable orchestration state naming the one increment currently authorized to change files, that it is deliberately movable, that the scope checker and CI own exact current-increment enforcement, that a completed product increment's acceptance tests must never permanently require the global pointer to equal that increment, that a historical test may verify only that the current pointer resolves to an existing scope contract, and that increment identity is proven from that increment's own contract and test identity rather than by the global pointer

AC-003:
GIVEN:
the completed R3-F5 increment and the movable ATDD pointer
WHEN:
the F5 AC-027 acceptance test runs after the pointer has advanced
THEN:
it proves F5's own contract and test identity without requiring the global pointer to remain F5, it still proves that the F4 lifecycle test no longer owns or pins the global pointer and that every substantive F4 isolation assertion is preserved, and it proves that when F5 is the selected increment the scope control requires the F5 contract

AC-004:
GIVEN:
any increment acceptance test module in the repository
WHEN:
a structural scope-control guard inspects it
THEN:
the guard fails closed when the module reads the global ACTIVE_INCREMENT value — through a pointer-derived local (including annotated and walrus bindings), a helper that returns the pointer, a direct pointer expression, a direct or attribute-qualified `read_increment_pointer()` call, or any expression derived from them — and compares it to that module's own increment identity by equality, inequality, membership, startswith or endswith, including a substantive own-lineage prefix or a literal tuple/list/set of own identifiers, anywhere in the module (in an assertion or a conditional guard), while accepting the scope checker's own pointer mechanics, scope-control fixtures, generic namespace checks such as `startswith("ATDD-")` or a non-empty check, unrelated lineages, and a movable-pointer test that only verifies the current pointer resolves to an existing scope contract

AC-005:
GIVEN:
the completed R3-F6 forecast-engine increment and the movable ATDD pointer
WHEN:
the F6 AC-036 acceptance test runs after the pointer has advanced
THEN:
it proves F6's own contract and test identity without requiring the global pointer to remain F6, it proves that neither the completed F5 increment nor the completed F6 increment owns or pins the global pointer, and it preserves every substantive F6 isolation guarantee (shadow/non-authoritative, no runtime consumers, Feature Bus off, no F7 allocation, no calibrated-model shortcut, no persistence, no paper or funded authority)

EXPLICITLY OUT OF SCOPE:
- Any runtime, scanner, execution, risk, strategy or trading behaviour change
- Any F5 feasibility/safety semantics, identity, validation or evidence change
- Any F6 forecast-engine semantics, engine, contract-module, evaluation or runtime change beyond removing the AC-036 pointer pin and recording the handoff
- Any F4 opportunity-lifecycle semantics, identity, persistence, policy or runtime change
- Feature Bus activation, Paper-v2 activation, cutover or funded authority
- Deployment, production configuration, branch protection, rulesets or workflow changes
- Changing the scope checker's parser, arrow syntax, status codes or fail-closed behaviour
- Removing or weakening any historical increment's contract identity or substantive assertion
- Adding a second scope-control mechanism, a new runtime service, or a filename-only F5/F6 special case

FROZEN BOUNDARIES:
- `tests/atdd_scope.py` continues to own exact current-increment enforcement and keeps failing closed on a missing, ambiguous or unknown active increment.
- The pointer must always name an existing scope contract.
- Every historical increment's contract, acceptance traceability and substantive assertions remain intact; only a stale global-pointer ownership assertion may be removed.
- No production, trading, Feature Bus, paper or deployment authority is created, widened or transferred.
- The scope checker's external contract (exit 0 PASS, 2 SCOPE_CHANGE_REQUIRED, 1 TRACEABILITY_GAP) is unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_atdd_scope_control.py::test_movable_pointer_governance_contract_and_map
AC-002 -> tests/test_atdd_scope_control.py::test_movable_pointer_semantics_documented
AC-003 -> tests/test_atdd_scope_control.py::test_f5_pointer_handoff_is_lifecycle_safe
AC-004 -> tests/test_atdd_scope_control.py::test_global_pointer_pin_guard
AC-005 -> tests/test_atdd_scope_control.py::test_f6_pointer_handoff_is_lifecycle_safe

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/README.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F5-feasibility-safety.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F6-forecast-engine.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py

DEFERRED DISCOVERIES:
- Future R3 and F7 increments should adopt the R0/R1 movable-pointer pattern directly rather than pinning the global pointer; if one does pin it, the AC-004 guard now fails closed at pull-request time. The completed F5 and F6 increments were brought onto that pattern by this increment.
- The ATDD README would eventually benefit from a short worked example of a lifecycle-safe historical acceptance test; documentation-only, and not required by this increment.
- Whether the pointer mechanics should move from `docs/atdd/ACTIVE_INCREMENT` to a structured registry is a separate architecture question and is not addressed here.

UNAPPROVED SCOPE CHANGES:
NONE
