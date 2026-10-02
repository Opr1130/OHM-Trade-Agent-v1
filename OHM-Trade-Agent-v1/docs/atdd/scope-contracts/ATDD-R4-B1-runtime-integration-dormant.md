INCREMENT:
ATDD-R4-B1-runtime-integration-dormant

OWNER-APPROVED INTENT:
This is the OWNER-authorized R4-B1 runtime-integration increment. R4-B0 proved the F3-F7 target spine composable in process; the R4-B1 contract freeze (`ATDD-R4-B1-contract-freeze`, merged) fixed the boundary, posture, authority, rollback, exclusions and the retry/requalification semantics the integration must preserve. This increment wires the already-proven target spine into the real production orchestration (`app.jobs.run_cycle`) so the composition is reachable when explicitly test-enabled, while remaining non-authoritative and inert when off.

THIS INCREMENT IS DORMANT. It does not activate the Feature Bus, Paper-v2, F7 admission, the Committee or any funded/exchange authority. Its completion posture is: runtime wiring PRESENT; target F3-F7 path NON-AUTHORITATIVE; Feature Bus `off`; Paper-v2 unset; F7 not the admission authority; legacy Top-8 plus profit-ranking and Freqtrade dry-run plus Paper-v1 remain the authoritative paper engines. The target composition terminates at the Paper-v2 admission seam and holds no writer, reservation or execution authority.

THIS PR IS THE CONTRACT FREEZE FOR THE INCREMENT. It writes the scope contract, its freeze-provable acceptance criteria and the movable pointer. It writes no production runtime behavior. The runtime wiring itself (the composition module, the gate setting, the `run_cycle` hook and their behavioral acceptance criteria) is implemented in a later commit of this same increment, at which point this contract's acceptance criteria and implementation map are extended. No runtime behavior is authorized by this freeze.

AUTHORIZATION PROVENANCE. Authorized by the owner's standing master-orchestrator directive to create and freeze `ATDD-R4-B1-runtime-integration-dormant` before implementing its production wiring.

STARTING SHA. `origin/main` = `a9121390c961ac16ce4657c2bdfab4d7243ccb5c` (R4-B1 contract freeze plus hardening, PRs #308 and #309). `docs/atdd/ACTIVE_INCREMENT` named `ATDD-R4-B1-contract-freeze` at the start of this increment.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (unified architecture): "Feasibility and calibrated statistical forecasts -> economic selector -> deterministic risk checks and atomic capital reservation -> realistic paper execution. Only approved deterministic or statistical artifacts participate." R4-B1 puts that path into the real orchestration graph without granting it authority.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (Safety plane): "Independent protection -> priority canonical intents. Safety can suspend automatically." Protection must run before the target spine and must not depend on it.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3: "Acknowledge intents only after durable commit; retry unacknowledged operations with stable keys." and "Timestamp alone is not an idempotency key." The dormant wiring must not mint canonical identities or intents.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 5: "A single canonical writer process is the only authority that commits operational domain events and projections." The composition in this increment writes nothing canonical.
- `docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md`: the frozen contract this increment implements - single orchestration boundary, dormant posture, authority boundary, exact rollback, exclusions and the retry/requalification classification.
- `docs/atdd/scope-contracts/ATDD-R4-B0-spine-contract-closure.md`: the proven spine and the closed increment this builds on.
- `deploy/cron.d/ohm-unified-cycle`: the host entry point of the single orchestration boundary this increment extends. The boundary this increment wires into is `app.jobs.run_cycle`; the cron unit file itself is not modified.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable; a completed increment must not permanently own it.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture; `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the runtime-integration increment and the repository's movable active-increment pointer
WHEN:
the scope contract and the pointer are inspected
THEN:
the contract exists, declares its own increment identity, the pointer resolves to an existing scope contract, and this increment never pins the global pointer to its own identity
AC-002:
GIVEN:
the production orchestration boundary
WHEN:
the scheduler set is inspected
THEN:
exactly one active scheduler command invokes the unified cycle, it is in `deploy/cron.d/ohm-unified-cycle`, the contract names that single boundary as the only path this increment may modify, and the contract forbids a second scheduler, timer, cron entry or orchestration loop
AC-003:
GIVEN:
the frozen runtime activation posture and the composition seam
WHEN:
they are inspected
THEN:
the contract freezes an off-by-default target-spine gate (invalid values fail closed), states that OFF means the hook is not invoked and the legacy path is unchanged, states that the target composition terminates at the Paper-v2 admission seam, and states that the composition holds no writer, reservation or execution authority
AC-004:
GIVEN:
the authority boundary
WHEN:
it is inspected
THEN:
the FROZEN BOUNDARIES section enumerates each authority that must remain unchanged and each authority class this increment may not create, including funded trading, funded exchange order authority, Kraken order authority, Committee runtime authority, dashboard and Telegram trading authority, a second scheduler, and a second reservation, allocation, execution, outcome or evidence authority
AC-005:
GIVEN:
the rollback and exclusion requirements
WHEN:
they are inspected
THEN:
the contract names an exact rollback to the pre-integration orchestration (remove the hook and the composition module; no canonical-data migration) and lists explicit exclusions including no Feature Bus activation, no Paper-v2 activation, no F7 admission authority, no Committee runtime authority, no legacy retirement, no F11 bypass and no funded/exchange authority
AC-006:
GIVEN:
this increment's frozen artifact and the scheduler set
WHEN:
the freeze posture is audited
THEN:
the contract records the B1 completion posture (Feature Bus off and Paper-v2 unset at completion), exactly one active scheduler command invokes the unified cycle, this increment's contract and acceptance module introduce no funded, exchange, order-placement or Committee authority import, and the contract names the R4-B1 retry freeze as the identity/idempotency authority it preserves

EXPLICITLY OUT OF SCOPE:
- Activating the Feature Bus, Paper-v2, F7 runtime admission, or the Committee
- Any canonical admission, reservation, order intent, fill or protection write from the target path
- Funded or live trading, exchange order placement, modification, cancellation or confirmation, margin, asset borrow or leverage
- Retiring, deleting or re-weighting the legacy selector, Top-8, profit-ranking, Freqtrade dry-run or Paper-v1
- A second scheduler, timer, cron entry, orchestration loop, Feature Bus, detector authority, opportunity lifecycle, forecast authority, allocation authority, reservation authority, paper engine, outcome truth system, semantic read model or evidence store
- Modifying `deploy/cron.d/ohm-unified-cycle` or any scheduler unit
- Changing F3-F7 economics, selector behavior, forecast behavior, geometry behavior, P&L sign, protection semantics, handoff semantics or deployment behavior
- Modifying architecture documents, the v1.4.3 DOCX, the ATDD checker, workflows or `pyproject.toml`
- Weakening, deleting or skipping any existing test, including the R4-B0 posture assertion, which this increment reconciles explicitly rather than deleting

FROZEN BOUNDARIES:
Runtime orchestration boundary.
The single orchestration boundary is `app.jobs.run_cycle`, invoked by `deploy/cron.d/ohm-unified-cycle`. The target-spine hook rides the existing cycle: it runs after the active-position and Paper-v2 protection phase and never before it, and it never aborts the cycle (fail-open reporting). No second scheduler, timer, cron entry, daemon or orchestration loop is introduced, and `deploy/cron.d/ohm-unified-cycle` is not modified.

Activation gate.
The increment adds one target-spine gate setting, off by default, accepting only `off` and `shadow`; any other value fails Settings parsing rather than silently enabling the path. When the gate is `off` the hook is not invoked at all, so the target composition does not run and the legacy path is unchanged. The gate is not activated by this increment.

Composition seam and authority.
The target composition reuses the already-proven production functions of the R4-B0 spine (F3 `ignition.evaluate`, F4 `lifecycle.apply_claim`, F5 `evaluate_feasibility` over `FeasibilityEvidence`, F6 `evaluate_forecast` with approved artifacts only, F7 `select_portfolio`) and the deterministic F7 to Paper-v2 handoff. It terminates at the Paper-v2 admission seam: it composes and reports a disposition per opportunity and builds the deterministic handoff, but it never calls the canonical writer's admission, reservation, order-intent, attempt, fill or protection surfaces, and it therefore holds no writer, reservation or execution authority. Missing evidence fails closed: a zero-admissible-model environment abstains (F6 `INSUFFICIENT_EVIDENCE`) and produces no allocation, reservation plan or handoff, and F5 `VETO` remains distinct from abstention and from cash/no-trade.

Snapshot source.
The composition consumes feature snapshots from a read-only provider over already-committed evidence. It writes no canonical evidence and creates no second Feature Bus. When the provider yields nothing - including while the Feature Bus is `off`, which is the completion posture - the composition is a recorded no-op and the spine is inert.

Identity and idempotency.
The dormant composition mints no canonical identity: it reads and composes, and it does not create a disposition, context, admission, reservation, order intent, attempt or fill. The retry and requalification semantics of `ATDD-R4-B1-contract-freeze` remain the authority for the admission seam and are unexercised by this increment; the composition must not create a second identity derivation.

Legacy coexistence.
Legacy Top-8 plus profit-ranking and Freqtrade dry-run plus Paper-v1 remain the live admission and paper authorities. The target path is a non-authoritative shadow composition and cannot admit, reserve, execute, protect, alert or rank.

Failure semantics.
A missing or unreadable snapshot source, invalid lineage, F5 `VETO`, zero-model abstention, capacity or capital rejection, conflicting requalification, or restart must each fail closed: no admission, no reservation, no mutation and no fallback to a legacy authority. A failure in the hook is reported and never aborts the unified cycle.

Observability.
The hook reports machine-readable evidence that the target composition is reachable when explicitly test-enabled and inert when off, so the difference between wired, test-enabled and authoritative is visible and cannot be inferred from the mere existence of a module.

Authority boundary.
R4-B1 runtime integration must not create, widen or imply: funded trading; funded exchange order authority; Kraken order placement, modification, cancellation or confirmation; margin, asset borrow or leverage; Committee runtime authority; dashboard trading authority; Telegram trading authority; or a second reservation, allocation, execution, outcome or evidence authority. Risk, strategy, execution, protection and admission authority are unchanged, and legacy remains the sole live admission and paper authority.

Rollback.
Rollback is exact: remove the `run_cycle` hook and the composition module, restoring the pre-integration `app.jobs.run_cycle` sequence with the gate still off and no canonical-data migration. Because the target path is non-authoritative and writes nothing canonical, rollback is a code revert and restores exactly one authority (the legacy path).

Exclusions.
R4-B1 runtime integration explicitly excludes: Feature Bus activation; Paper-v2 activation; F7 admission authority; any Committee runtime authority; any funded or exchange authority; any Kraken order authority; any legacy retirement or deletion; any F11 bypass; any hidden mode activation; any second scheduler; any second allocation, reservation, execution, outcome or evidence authority; and any change to F3-F7 economics.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_b1_runtime_integration_freeze.py::test_ac_001_contract_and_pointer_are_consistent
AC-002 -> tests/test_opip_r4_b1_runtime_integration_freeze.py::test_ac_002_single_orchestration_boundary
AC-003 -> tests/test_opip_r4_b1_runtime_integration_freeze.py::test_ac_003_gate_posture_and_composition_seam
AC-004 -> tests/test_opip_r4_b1_runtime_integration_freeze.py::test_ac_004_authority_boundary
AC-005 -> tests/test_opip_r4_b1_runtime_integration_freeze.py::test_ac_005_rollback_and_exclusions
AC-006 -> tests/test_opip_r4_b1_runtime_integration_freeze.py::test_ac_006_current_posture_and_no_authority_import

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_runtime_integration_freeze.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_runtime_integration_freeze.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_runtime_integration_freeze.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_runtime_integration_freeze.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_runtime_integration_freeze.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_runtime_integration_freeze.py

DEFERRED DISCOVERIES:
- The runtime wiring itself (the composition module, the gate setting, the `run_cycle` hook and their behavioral acceptance criteria and implementation map) is the next commit of this same increment and is not authorized by this freeze PR.
- The R4-B0 assertion `test_composition_authority_targets_are_unchanged_on_disk` (in `tests/test_opip_r4_b0_spine_composition.py`) asserts that `run_cycle` and `scan_opportunities` do not mention the target spine. Wiring will necessarily trip it. It must be reconciled as an explicitly recorded posture change (wiring present, authority unchanged) in the wiring commit, not deleted or weakened.
- Whether the real production snapshot source becomes a scheduled feature-bus capture or a read-only committed-snapshot provider is an owner decision; this increment forbids activating a capture source.
- F11 protection ordering is a prerequisite for R4-B2 activation, not for this dormant wiring. This increment must not bypass it.
- The exact provider that reads committed feature snapshots does not exist as a public read seam today; the wiring commit must define a read-only seam that creates no second evidence history.

UNAPPROVED SCOPE CHANGES:
NONE
