INCREMENT:
ATDD-ML-DEPENDENCY-POLICY-v1

OWNER-APPROVED INTENT:
Repair the false-positive no-ML-dependency guard without weakening the
prohibition it protects.

Root cause (observed, not hypothesised). The Build-1 guard
`tests/test_opip_decision_safety_v1.py::test_no_ml_dependency_is_introduced`
asserted that the lowercased *text* of `app/opip/**/*.py` contained none of
`xgboost`, `lightgbm`, `shap`, `sklearn`, `scikit`, `torch`, `tensorflow`, and
that the lowercased text of `requirements.txt` contained none of `xgboost`,
`lightgbm`, `shap`, `scikit-learn`, `torch`, `tensorflow`. A substring test
cannot tell a dependency from prose: `shap` is contained in the ordinary words
"shape", "shaped" and "reshape", which occur in
`app/opip/contracts/paper_execution.py`, `app/opip/contracts/portfolio.py` and
`app/opip/learning/evaluation.py`. No ML import and no ML dependency existed in
the package; the guard failed on vocabulary alone.

Before evidence (base SHA `fba8440f`, before this change): the focused run of
that node id failed on `shap` while a search of `app/opip` for the token
returned only the words `shape`, `shaped` and `reshape`. After evidence (this
increment): the same node id passes, the package scan reports zero violations,
and the criteria below prove that prohibited imports, prohibited declared
dependencies and unprovable inputs still fail.

Deliverables, all inside the guard surface and none in production code: a
stdlib-only syntax-aware policy module that reads imports from the module AST,
resolves each import to its root name, evaluates the supported dynamic-import
paths from literal targets and fails closed on anything it cannot prove, and
parses the relevant dependency manifests as requirements; the repaired guard
delegating to that policy under its existing node name; and this contract plus
the movable active-increment pointer.

Explicitly no trading authority, no risk gate, no execution safeguard, no
release profile, no deployment path, no workflow gate and no application module
changes in this increment.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/SOURCE.md`: the architecture identity this repository already tracks; this increment changes no architectural component.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`: the register states that AI/ML output is advisory evidence and cannot override deterministic risk rejection, which is why the no-ML-dependency guard exists and why it must keep failing on a real dependency.
- `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `docs/atdd/README.md`: the scope-control and movable-pointer rules that require this increment to declare its acceptance criteria, traceability and implementation map.
- `AGENTS.md`: principle 13 (do not silence a failing test), principle 15 (smallest change that solves the proven problem), principle 20 (state behavioural impact), and the release-profile review criteria for missing fail-closed behaviour and authority widening.
- `OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py`: the Build-1 decision-isolation guard family that owns the repaired prohibition.
- `OHM-Trade-Agent-v1/requirements.txt` and `OHM-Trade-Agent-v1/requirements-dev.txt`: the dependency manifests the policy evaluates.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the real `app/opip` package, whose source contains the ordinary words "shape", "shaped" and "reshape" in `app/opip/contracts/paper_execution.py`, `app/opip/contracts/portfolio.py` and `app/opip/learning/evaluation.py`
WHEN: the repaired guard evaluates that package's static and dynamic imports, and the policy evaluates synthetic modules whose docstring, comment, identifier, class or string constant contains those words
THEN: no violation is reported, while the same files still contain the token that made the previous text scan fail

AC-002:
GIVEN: the token list the original guard forbade in package source and in the dependency manifests, plus every static import form that reaches an ML library (plain, aliased, submodule, from-import and same-family distribution such as `torchvision`) and declared requirements including an editable requirement and a same-family distribution
WHEN: the policy evaluates each form and each manifest line
THEN: every one of them is still reported as a prohibited import or a prohibited requirement, the prohibition is not narrowed to allow an ML library, and an unrelated lookalike name such as `shapefile-tools` or `shapely` is not reported

AC-003:
GIVEN: the relevant dependency manifests, a manifest whose comment names a prohibited library, and a manifest that includes another manifest by `-r`
WHEN: each manifest is parsed as requirements, and each include resolves, is missing, loops or is remote
THEN: comments, extras, specifiers and markers cannot trigger a violation, the real manifests are clean, an included prohibited dependency is still reported, and an include that cannot be enumerated reports a violation instead of being skipped

AC-004:
GIVEN: the supported dynamic-import paths (`importlib.import_module`, `__import__`, `importlib.util.find_spec`) with a prohibited literal target, with a benign literal target and with a target that is not a literal string, plus a module that cannot be parsed
WHEN: the policy evaluates that source
THEN: a prohibited dynamic import is reported, a benign dynamic import is not, and an unprovable dynamic import or unparseable module is reported rather than accepted, so the guard fails closed

AC-005:
GIVEN: this increment's changed surfaces and the pre-existing decision-safety guard module
WHEN: the guard source and this contract's implementation map are inspected
THEN: the guard keeps its node name, its `app/opip` package and no skip or xfail marker, delegates to the shared policy instead of scanning text, the implementation map authorizes exactly the declared guard, policy, test and scope-control paths, no application, deployment or workflow path is authorized, and no unapproved scope change is recorded

EXPLICITLY OUT OF SCOPE:
- Any change to application code, runtime behaviour, deployment, Compose files, workflows or CI gates.
- Any change to trading authority, risk sizing, minimum reward-to-risk, position verification, execution safeguards, release profiles or the paper/Freqtrade isolation contract.
- The `test_opip_modules_reference_no_futures_surface` text guard, which passes today and is left exactly as it is.
- Any new dependency, any change to `requirements.txt` or `requirements-dev.txt`, and any ML/AI capability of any kind.
- The F10 dashboard contract and the R7 legacy-retirement contract, both of which remain owner-gated and are recorded under deferred discoveries.
- Widening the ML policy to packages outside `app/opip` (for example `app/services` or `tools/`), which would be a coverage widening requiring its own approved increment.
- Dependency-name enforcement for pip option lines (`--index-url`, `--find-links`, hash and constraint options) and for private index hosting, which this policy does not evaluate.

FROZEN BOUNDARIES:
- The prohibition is unchanged: every token the original guard forbade stays forbidden, including submodules and same-family distributions.
- The guard keeps its node id `tests/test_opip_decision_safety_v1.py::test_no_ml_dependency_is_introduced` and its `OPIP_PACKAGE = Path("app/opip")` package; it is repaired, never deleted, renamed, skipped or xfailed.
- `docs/atdd/ACTIVE_INCREMENT` remains a single movable pointer line; this contract declares no fixed pointer pin and the pointer may move to any later approved contract.
- Every other existing scope contract, acceptance criterion and test node id is untouched, and no existing test is edited except the guard this increment repairs.
- No authority, gate, runner, scheduler, telemetry, learning or deployment path is added, widened or activated.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_001_the_real_package_passes_where_the_text_guard_failed
AC-001 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_001_ordinary_words_never_trigger_the_policy
AC-002 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_002_every_prohibited_import_form_still_fails
AC-002 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_002_every_prohibited_declared_dependency_still_fails
AC-003 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_003_manifests_are_read_as_requirements_not_text
AC-003 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_003_manifest_includes_are_followed_and_fail_closed
AC-004 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_004_supported_dynamic_imports_are_evaluated
AC-004 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_004_unprovable_input_fails_closed
AC-005 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_005_the_repair_keeps_the_guard_identity_and_its_package
AC-005 -> tests/test_opip_ml_dependency_policy_v1.py::test_ac_005_the_repair_touches_no_runtime_gate_or_authority_surface

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/ml_dependency_policy.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_ml_dependency_policy_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/ml_dependency_policy.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_ml_dependency_policy_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/ml_dependency_policy.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_ml_dependency_policy_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/ml_dependency_policy.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_ml_dependency_policy_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_ml_dependency_policy_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-ML-DEPENDENCY-POLICY-v1.md

DEFERRED DISCOVERIES:
- F10 dashboard contract preparation remains owner-gated on `OQ-10` in `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`. No contract can be drafted for F10 because the register lists it as UNASSIGNED and the open question asks the owner for operator usefulness and for the legacy outcomes cockpit retention decision. Preparing it therefore needs owner input, not code: which pages and which semantic model are in scope, whether the legacy cockpit is retired or mirrored, and whether the dashboard is advisory-only. Until the owner answers, this increment records the blocker and drafts nothing.
- R7 legacy retirement stays owner-gated under `OQ-08` and `OQ-10`; this increment neither implements nor anticipates it.
- The futures-surface guard still uses lowercased substring matching. It passes today, so it is left untouched; a later reviewed increment may give it the same syntax-aware treatment if a false positive is ever proven.
- Extending the ML policy beyond `app/opip` (for example `app/services`, `app/jobs`, `tools/`, `scripts/`) is deliberately not done here: it would widen a guard's coverage and needs its own approved increment with its own evidence.
- Pip option lines such as `--index-url`, `--find-links`, `--hash` and `--constraint` are skipped by the policy because they declare no distribution name; a private index that served a prohibited wheel under an ordinary name would not be caught by name comparison. This policy proves declared names and imports, nothing more.
- Post-commit values that cannot exist before the commit (the increment SHA and the Linux regression-run identifiers) are recorded in the draft pull-request body and in the stack's evidence trail, not in this contract.

UNAPPROVED SCOPE CHANGES:
NONE
