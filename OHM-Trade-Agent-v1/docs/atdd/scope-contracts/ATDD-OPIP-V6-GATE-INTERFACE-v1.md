INCREMENT:
ATDD-OPIP-V6-GATE-INTERFACE-v1

OWNER-APPROVED INTENT:
V6-D02 adds one isolated, pure, versioned after-cost economic gate interface under
`app/opip/gates/` so the scanner, simulator and Chief can later share one gate
definition instead of each re-implementing the production numeric semantics.
The interface is additive and unwired: nothing in the live scanner imports it,
`app.services.economic_quality_gate` and its callers are unchanged, and no gate,
threshold, ranking, alert, paper or trading behavior moves. Its numerics mirror
production exactly (equivalence is test-enforced) while refusing to raise on bad
data: missing, non-numeric, boolean, non-finite or malformed inputs fail closed
with explicit reason codes, and the versioned policy digest travels in the
result. This contract also declares the increment that the ATDD scope gate must
authorize: the V6-D02 slice was committed without a scope contract, so the
changed-file check rejected the new package, its ADR and its tests as unmapped.
No test is weakened, skipped or deleted by this increment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 4 (additive first: new pure modules and read-only tools before any wiring into the scanner path), section 7 (safety boundary: deterministic gates fail closed, no order authority) and the slice rules that require a scope contract and acceptance tests.
- OHM-Trade-Agent-v1/docs/v6/V6_D02_GATE_INTERFACE.md: the approved design decision, its properties and its deliberate known differences from production.
- OHM-Trade-Agent-v1/app/services/economic_quality_gate.py: the production `evaluate_economic_quality` gate whose numeric semantics are mirrored.
- OHM-Trade-Agent-v1/app/opip/gates/interface.py: the shared pure gate contract, its reason codes and the policy digest.
- OHM-Trade-Agent-v1/tests/test_opip_gate_interface_v1.py: the slice's contract, fail-closed, purity, isolation and equivalence evidence.
- OHM-Trade-Agent-v1/AGENTS.md: principle 5 (external AI is advisory; deterministic safety gates fail closed) and principle 6 (fixed risk sizing, minimum reward-to-risk and execution safeguards are preserved).

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a candidate whose economics are missing, non-numeric, boolean, non-finite or geometrically invalid, and a policy that may itself be malformed
WHEN: the shared gate interface evaluates it
THEN: it returns an immutable result carrying the interface version with `qualified=False` and an explicit ordered reason code, and never raises

AC-002:
GIVEN: seeded candidate economics across both directions, configured filters and boundary values
WHEN: the shared gate interface and the existing production economic gate evaluate the same inputs
THEN: their qualified outcomes agree, Target-2 net profit is cent-identical where qualified, account risk agrees within 0.005, and no module outside `app/opip/gates` imports the new package

EXPLICITLY OUT OF SCOPE:
- Wiring the interface into the scanner, simulator, Chief, capability or any live qualification, ranking, alert or admission path.
- Changing, defining or loosening any production gate, threshold, fixed risk sizing or minimum reward-to-risk default.
- Persisting, migrating, backfilling or rewriting any evidence, registry or production store.
- Kraken order placement, modification, cancellation or confirmation of any kind.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.
- Weakening, skipping or deleting any existing test.

FROZEN BOUNDARIES:
- Advisory-only: coding automation has no trading authority and no exchange permissions.
- Deterministic gates keep failing closed; external AI is not involved in this increment.
- The gate interface is pure: no I/O, clock, environment, randomness or module-level mutable state.
- Missing input is never treated as zero, and a rejection never becomes an exception.
- `app.services.economic_quality_gate`, its callers and every other increment's contract keep their current behavior.
- No secret, credential or `.env` value is read, printed or written.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_gate_interface_acceptance_v1.py::test_bad_input_fails_closed_with_a_reason_code_and_never_raises
AC-002 -> tests/test_v6_gate_interface_acceptance_v1.py::test_gate_agrees_with_production_and_stays_inside_its_package

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/gates/__init__.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/gates/interface.py
AC-001 -> OHM-Trade-Agent-v1/docs/v6/V6_D02_GATE_INTERFACE.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_gate_interface_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_gate_interface_acceptance_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-GATE-INTERFACE-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/gates/__init__.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/gates/interface.py
AC-002 -> OHM-Trade-Agent-v1/docs/v6/V6_D02_GATE_INTERFACE.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_gate_interface_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_gate_interface_acceptance_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-GATE-INTERFACE-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- Integration into the scanner, simulator or Chief is not authorized by this increment: it needs S1 evidence and a compatibility review, then a separate owner approval, before any live caller may consume the interface.
- Equivalence is proven on a seeded corpus and a boundary grid, not exhaustively; production raises `ValueError` for an invalid direction while the interface rejects, so a later integration must reconcile any caller relying on that exception.
- Production's formatted rejection strings are replaced by stable reason codes; a consumer that displayed those strings needs an explicit mapping before integration.

UNAPPROVED SCOPE CHANGES:
NONE
