INCREMENT:
ATDD-DECISION-SAFETY-SCAN-v1

OWNER-APPROVED INTENT:
Narrow the frozen decision-safety ML-dependency scan so that it detects real model dependencies by evidence instead of by raw substring. `tests/test_opip_decision_safety_v1.py::test_no_ml_dependency_is_introduced` currently lowercases every `app/opip/**/*.py` file and asserts the four-letter sequence `shap` occurs nowhere, so an ordinary English word (`shape`, `shaped`, `authority-shaped`) is reported as a model dependency. The debt is already recorded twice: `ATDD-R5-B-horizon-intelligence.md` records the brittle-test technical debt and states that narrowing the scan to an import/identifier check is a separate reviewed test change, and `docs/MODULE2_INTELLIGENCE_COMMITTEE.md` documents that the scan "has broken the suite three times" while requiring that it "must not be weakened to accommodate wording". This increment therefore changes *how* the scan detects - declared distribution names parsed from every requirement file and `pyproject.toml`, and real imports parsed with `ast` - and never *what* it forbids. No prohibited distribution becomes acceptable, no file is excluded from the scan, and no gate is relaxed.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (adopted authority) with extraction `docs/architecture/v1.5.0/ARCHITECTURE.md`: no model may influence a trading decision, and the decision plane remains an observer/read-side package.
- `docs/MODULE2_INTELLIGENCE_COMMITTEE.md`: the contributor caution on the frozen boundary token scan, which this increment satisfies by evidence rather than by wording.
- `docs/atdd/scope-contracts/ATDD-R5-B-horizon-intelligence.md`: its `DEFERRED DISCOVERIES` record of the brittle-test technical debt, which this increment closes.
- `tests/atdd_scope.py` and `docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md`: the scope-control machinery and the increment-pointer discipline this contract obeys.
- `AGENTS.md`: prefer the smallest change that solves the proven problem; never weaken a gate.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: declared dependency forms (plain, pinned, extra, specifier or included) in a requirement file, a `pyproject.toml` that declares a dependency in `project.dependencies` or `project.optional-dependencies`, and module source that imports a prohibited distribution plainly, aliased, by `from`-import or as a submodule
WHEN: the evidence scan reads each declaration and each import
THEN: every prohibited distribution is reported under its PEP 503 normalized name, because the scan reads declared names and parsed imports rather than raw text, so a real model dependency can never pass this increment by being written in an unusual but valid form and the detection is never replaced by an exclusion

AC-002:
GIVEN: the ordinary English and identifiers a substring scan rejected - `shape`, `shaped`, `authority-shaped`, `_timing_shape(`, docstrings and string literals - together with unrelated real dependencies such as `numpy`, `pandas` and `pytest`
WHEN: the evidence scan reads that source and the real decision package
THEN: nothing is reported, because only declared distributions and parsed imports count as evidence, so prose can no longer fail a dependency check

AC-003:
GIVEN: the real decision package, the discovered dependency declarations, and a synthetic module and an included requirement file that each do declare a prohibited form
WHEN: the scan enumerates and inspects them
THEN: the enumeration covers every `app/opip/**/*.py` file and every discovered `requirements*.txt` and `pyproject.toml`, a `-r` included requirement file is followed rather than skipped, and a violating file is reported wherever it lives, so no blanket exclusion, allowlist or blanket pass replaces the detection

AC-004:
GIVEN: this increment's contract, the repository acceptance index and the changed files
WHEN: the real scope gate evaluates them
THEN: the movable pointer resolves to a declared contract and this increment's contract is present exactly once, every changed surface is mapped to an AC of this contract, the gate passes with no traceability gap and no unapproved criterion, this module pins no global pointer, and the increment grants no authority: no dependency is added, no runtime surface is wired, and fixed risk sizing, minimum reward-to-risk, alert-only defaults and the release posture are unchanged

EXPLICITLY OUT OF SCOPE:
- Allowing, installing, pinning or importing any model package; the prohibited set is unchanged and no dependency file is edited by this increment
- Adding or weakening any gate: `test`, `atdd scope`, `semgrep/ci` and every job of `.github/workflows/pytest.yml` keep their exact behaviour and set
- Excluding any module, directory or declaration file from the scan, or replacing detection with an allowlist
- Any change to `app/**` production code, to the decision package or to the decision plane's authority
- Any R5-E, R5-F, R6 or stacked-branch change; the stacked increments are not edited, rebased or re-pointed here
- Any new store, ledger, scheduler, runner, secret, environment, deployment path or trading authority
- Editing a frozen criterion of another increment; the R5-B debt record is annotated as closed, not rewritten

FROZEN BOUNDARIES:
- The test name `test_no_ml_dependency_is_introduced` and its criterion citation are preserved, and the assertion is narrowed only in *how* it detects evidence.
- The prohibited set (xgboost, lightgbm, shap, sklearn, scikit-learn, torch, tensorflow) is preserved exactly; the scan never returns fewer real violations than before for declared or imported forms.
- The scan still covers every `app/opip/**/*.py` module and every discovered dependency declaration, discovered by globbing rather than by a hand-kept list, so coverage widens with the repository.
- `tomllib` is required: if the interpreter cannot parse `pyproject.toml`, the scan raises instead of silently skipping declarations (fail closed).
- Architecture v1.5.0, the release profile (`SAFE_BASELINE`, `EVIDENCE_SHADOW`, blocked `TARGET_PAPER`), the funding-policy posture and every other frozen contract are untouched.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_decision_safety_v1.py::test_ac_001_a_real_ml_dependency_is_rejected_in_every_declared_and_imported_form
AC-002 -> tests/test_opip_decision_safety_v1.py::test_ac_002_harmless_prose_identifiers_and_strings_are_accepted
AC-003 -> tests/test_opip_decision_safety_v1.py::test_ac_003_every_module_and_declaration_is_still_examined
AC-004 -> tests/test_opip_decision_safety_v1.py::test_ac_004_the_scan_contract_authorizes_every_changed_surface

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-DECISION-SAFETY-SCAN-v1.md
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-B-horizon-intelligence.md
AC-004 -> OHM-Trade-Agent-v1/docs/MODULE2_INTELLIGENCE_COMMITTEE.md

DEFERRED DISCOVERIES:
- The scan covers dependency declarations and Python imports. A model reached without a declared distribution and without a Python import (for example a vendored blob fetched at runtime, a subprocess, or a remote inference endpoint) is not something a static scan can see; that boundary is unchanged by this increment and remains a review responsibility.
- Requirement files are read at the app root and `-r` includes are resolved relative to the working directory, which matches this repository's flat layout; a nested requirements tree would need its own resolution rule.
- Only PEP 621 `[project]` dependencies and optional dependencies are read from `pyproject.toml`; build-system requirements and tool sections are not dependency declarations for this scan.
- The stacked R5-E/R5-F/R6 branches carry prose that the previous substring scan rejected. They are not edited here: once this increment reaches them through the normal bottom-up update, their wording passes, and no branch, commit or PR is rewritten by this increment.
- This increment closes the brittle-test debt recorded by `ATDD-R5-B-horizon-intelligence.md`; it does not re-audit R5-B's own criteria or claim any part of R5-B's remaining obligations.

UNAPPROVED SCOPE CHANGES:
NONE
