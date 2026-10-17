INCREMENT:
ATDD-R4-F11-caller-census-remediation

OWNER-APPROVED INTENT:
Repair the static caller census that guards the protective-mutation surface, and nothing else. `ATDD-R4-F11-precutover-protection` introduced `_source_calls_tokens` to prove that no module outside the approved protection authority calls `admit_paper_opportunity` or `trigger_paper_protection_action`. That census recognised direct and qualified calls but missed two statically identifiable forms: an aliased import (`from m import n as x`) and a scheduled-job callback (`scheduler.add_job(n, ...)`). An aliased or scheduled mutator call is exactly the kind of caller the guard exists to find, so the guard was stricter in appearance than in fact. This increment resolves aliased imports, imported names and scheduled-job callback arguments, keeps the same-name and dynamic-resolution negatives outside the census, and records the boundary that dynamic resolution (`getattr`) cannot be proven statically. It changes test evidence only: no application module, protection runtime, canonical writer, scheduler, event type, release profile, configuration key or trading authority is touched, and no runtime behaviour changes. The owner selected disposition D-404-A: this evidence belongs to the R4-F11 precutover lineage, not to `ATDD-R5-E-F11-independent-protection-validation`, so it is delivered here as its own increment instead of being mapped onto an R5-E criterion it does not prove.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` section 9 and `LC-01`/`LC-03`: protective authority is independent, and protection precedes profit seeking. A census that misses real callers would understate who can reach that authority, so the guard must be complete for every statically identifiable form.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-05` "Protective precedence and lifecycle state contract" (`FROZEN`): the one approved protection authority and its admission suspension stay unchanged; this increment adds no second authority and removes none.
- `docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md`: the increment that owns `tests/test_opip_f11_precutover_protection.py` and the census this increment repairs.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the protective-mutation census helper and a source text that reaches an approved mutator through an aliased import, an imported name, a qualified attribute or a scheduled-job callback argument
WHEN:
the census evaluates that source
THEN:
it reports the source as a caller, while a same-name value that is not a call target, a same-name local binding, an unrelated method call and a dynamically resolved `getattr` call are not reported, so the census is complete for every statically identifiable form and its negatives stay closed
AC-002:
GIVEN:
this increment's own scope contract, its active-increment pointer and its changed-file set
WHEN:
they are inspected
THEN:
the contract parses, declares `NONE` for unapproved scope changes, traces every criterion to its matching acceptance test, maps only test and documentation paths, and the active-increment pointer resolves to an existing contract rather than being pinned to this increment, so the increment is provably test-only and self-consistent

EXPLICITLY OUT OF SCOPE:
- Changing the protection runtime, the canonical writer, the scheduler, the scan job or any application module. No file under `app/` is in the implementation map.
- Changing `ATDD-R4-F11-precutover-protection`'s contract, its acceptance criteria or its runtime claims; only the census helper inside its test file is repaired.
- Adding, widening or removing protection authority, admission suspension, release profiles, configuration keys or trading permissions.
- Claiming the R5-E F11 increment's evidence. This increment proves no `ATDD-R5-E-F11-independent-protection-validation` criterion and is not mapped to one.
- Static detection of dynamic resolution. `getattr`-style calls are declared unsupported rather than guessed.

FROZEN BOUNDARIES:
1. Test-only. The changed files are the precutover census helper, its review regression, this contract, the active-increment pointer and this increment's acceptance tests.
2. One census, one meaning. `_source_calls_tokens` keeps its existing signature and its existing token semantics; only additional statically identifiable call forms are resolved.
3. Negatives stay negative. A value that merely shares a mutator's name, an unrelated attribute call and a dynamic `getattr` call must not be reported.
4. No widening. The set of mutator tokens is unchanged, and no new caller allowance is introduced.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_f11_caller_census.py::test_ac_001_the_census_detects_aliased_imports_and_scheduled_callbacks
AC-001 -> tests/test_opip_r4_f11_caller_census.py::test_ac_001_the_census_stays_closed_for_dynamic_and_same_name_controls
AC-002 -> tests/test_opip_r4_f11_caller_census.py::test_ac_002_the_increment_is_test_only_and_pointer_consistent

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_review_c1_f11_census.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r4_f11_caller_census.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-caller-census-remediation.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r4_f11_caller_census.py

DEFERRED DISCOVERIES:
- The census remains static by design. A caller that resolves a mutator dynamically (`getattr`, a mapping lookup, an import hook) is still invisible, and that limit is now stated in the helper's own contract rather than left implicit. Closing it would require a runtime guard, which is a different increment with different authority.
- The census is still invoked from test evidence only. Nothing under `app/` runs it, so it detects a committed code shape rather than preventing a live call.
- `tests/test_review_c1_f11_census.py` stays a review regression and deliberately carries no acceptance marker; `AC-001` is proved by `tests/test_opip_r4_f11_caller_census.py`.

UNAPPROVED SCOPE CHANGES:
NONE
