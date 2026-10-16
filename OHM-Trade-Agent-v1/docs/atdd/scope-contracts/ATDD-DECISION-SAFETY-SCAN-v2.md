INCREMENT:
ATDD-DECISION-SAFETY-SCAN-v2

OWNER-APPROVED INTENT:
Close the two false-negative gaps recorded against the accepted `ATDD-DECISION-SAFETY-SCAN-v1` scanner without replacing it: a dependency include that cannot be read must fail closed instead of silently declaring nothing, and a module reached through a literal dynamic import must be treated as evidence, with a dynamic import whose target cannot be proven refused rather than skipped. This increment extends the existing scanner in `tests/test_opip_decision_safety_v1.py`; it introduces no second scanner, no new module, no runtime change and no gate change, and it reconciles the fail-closed and dynamic-import obligations that PR #373 declared without adopting PR #373's implementation.

ARCHITECTURE REFERENCES:
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` and architecture v1.5.0: unchanged and not modified by this increment; the decision engine's no-model property is a frozen boundary, and this increment only strengthens the evidence that proves it.
- `docs/atdd/scope-contracts/ATDD-DECISION-SAFETY-SCAN-v1.md`: the accepted evidence-based scanner this increment extends. Its criteria, prose-acceptance behaviour and contract stay in force; nothing in v1 is replaced, renumbered or weakened.
- `AGENTS.md` and `docs/atdd/README.md`: smallest change that solves the proven problem, fail closed on missing evidence, no trading authority, no unratified policy, no unapproved criterion.
- PR #373 (`ATDD-ML-DEPENDENCY-POLICY-v1`): its declared AC-003 fail-closed include obligation and AC-004 dynamic-import obligation are what this increment reconciles. Its implementation (a separate helper module, its own contract and acceptance module, and a hunk that would remove the accepted scanner) is deliberately **not** adopted.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a requirement declaration that includes another file by `-r` or `--requirement`, covering a missing target, a target the reader cannot read, and a remote target
WHEN:
the scan resolves those declarations
THEN:
the scan fails closed with `UnresolvableDeclarationError` naming the unresolvable target instead of reporting nothing, a resolvable include is still followed and reports its prohibited dependency, and a circular include terminates and still reports the prohibited dependency it contains

AC-002:
GIVEN:
source that reaches a module through `importlib.import_module`, `__import__` or `importlib.util.find_spec` with a string-literal target, including an aliased spelling and a submodule literal
WHEN:
the scan parses that source
THEN:
every prohibited distribution reached that way is reported under its normalized name, and a benign literal target is not reported

AC-003:
GIVEN:
a dynamic-import call whose first argument is not a string literal - a variable, an f-string, a concatenation, or no argument at all
WHEN:
the scan evaluates that source
THEN:
the call is reported by name as unprovable so the caller refuses it, the real `app/opip` package is proven to contain no unprovable and no prohibited dynamic import, and a literal call is never reported as unprovable

AC-004:
GIVEN:
ordinary English and identifiers that contain `shape`, a lookalike call name, the real decision package, the real dependency declarations and every static import form
WHEN:
the widened scan reads them
THEN:
no false positive is introduced - the prose source and the lookalike call are accepted by all scans, the real package and the real declarations stay clean, and every static prohibited form (plain, aliased, from-import, submodule) is still refused

AC-005:
GIVEN:
this increment's contract, the acceptance index and the changed surfaces
WHEN:
the real scope gate evaluates them
THEN:
the movable pointer resolves to a declared increment, this increment's contract maps every changed surface to a criterion of its own, the gate passes with no traceability gap and no unapproved criterion, no changed surface is a runtime path, and the scan module pins no global pointer

EXPLICITLY OUT OF SCOPE:
- Any replacement of the accepted v1 scanner, any second or parallel scanner, and any adoption of PR #373's helper module, contract or acceptance module; PR #373 is not merged, closed, rebased or edited by this increment.
- Dependency discovery beyond `requirements*.txt` at the app root and `pyproject.toml` PEP 621 `[project]` dependencies: no nested requirements tree, no lock-file format, no build-system or tool section is read.
- Dynamic imports that cannot be written as a call the AST can see - `eval`, `exec`, `compile`, `getattr("import_module")` indirection, a module name assembled inside a data structure, or a model reached by subprocess, a vendored blob or a remote endpoint. Those remain a review responsibility, unchanged from v1.
- Any runtime, gate, workflow, dependency, release-profile or architecture change; this increment touches no `app/**` path, no `.github/**` path, no requirement file and no architecture document.
- Any change to risk sizing, R:R, execution safeguards, alert-only posture, or trading authority, and any promotion or policy ratification.

FROZEN BOUNDARIES:
- The scan stays a test-level evidence guard in `tests/test_opip_decision_safety_v1.py`; it grants no authority, writes no store, publishes no event, and no runtime root consumes it.
- Failing closed means refusing: `UnresolvableDeclarationError` is raised, and the unprovable-dynamic-import signal is asserted empty, so a scan that cannot prove the package clean fails rather than passes.
- The prohibition itself is never narrowed: every static form and every declared distribution stays refused, and no allowlist, exclusion or skip marker may replace detection.
- `EVIDENCE_SHADOW` remains the production posture and `TARGET_PAPER` remains blocked; this increment changes no profile.
- PR #373 remains open and unmodified until its unique obligations are reconciled by the owner's decision.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_decision_safety_v1.py::test_ac_001_an_unresolvable_dependency_include_fails_closed
AC-002 -> tests/test_opip_decision_safety_v1.py::test_ac_002_a_prohibited_module_reached_by_a_literal_dynamic_import_is_reported
AC-003 -> tests/test_opip_decision_safety_v1.py::test_ac_003_an_unprovable_dynamic_import_fails_closed
AC-004 -> tests/test_opip_decision_safety_v1.py::test_ac_004_prose_and_lookalike_calls_are_still_accepted
AC-005 -> tests/test_opip_decision_safety_v1.py::test_ac_005_the_v2_contract_authorizes_every_changed_surface

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-DECISION-SAFETY-SCAN-v2.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- The static-scan boundary recorded by v1 is unchanged: a model reached without a declared distribution and without a Python import or a literal dynamic-import call cannot be seen by this scan.
- Includes are still resolved relative to the working directory, matching v1 and this repository's flat layout; this increment adds fail-closed behaviour for unreadable and remote includes, not a new resolution rule for a nested tree.
- Only PEP 621 `[project]` dependencies and optional dependencies are read from `pyproject.toml`; build-system and tool sections are still not dependency declarations for this scan.
- PR #373's remaining unique artifacts (its helper module, its contract and its 405-line acceptance module) stay undeclared here; whether to salvage or retire them is an owner decision recorded outside this contract.
- The stacked R5-E/R5-F/R6 branches are not edited by this increment; they receive the scan when the normal bottom-up update reaches them, and no published branch is rewritten.

UNAPPROVED SCOPE CHANGES:
NONE
