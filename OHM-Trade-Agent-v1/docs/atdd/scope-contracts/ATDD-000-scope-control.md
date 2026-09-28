INCREMENT:
ATDD-000-scope-control

OWNER-APPROVED INTENT:
Establish a pytest scope-control mechanism so an increment may change only behavior named by OWNER-approved acceptance criteria.

ARCHITECTURE REFERENCES:
- OPIP Profit Intelligence Architecture v1.4.3 (22 September 2026), section 1: architecture does not grant funded authority or certify a release; paper execution stays isolated from funded order endpoints.
- OPIP Profit Intelligence Architecture v1.4.3, section 13: PR #237 stays on its approved Feature Bus foundation; platform acceptance scenarios are separate work packages.
- OPIP Profit Intelligence Architecture v1.4.3, section 15: unchanged v1.2 constraints remain binding. Checked-in baseline is docs/architecture/v1.2/.
- docs/architecture/v1.2/CODING_BOUNDARY_CONTRACT.md: contract tests do not import app.services trading modules.
- docs/architecture/v1.2/A_PAPER_MANDATE.md: paper mandate remains in force.
- AGENTS.md: ATDD is below OWNER rulings and approved architecture. PR #237 stays isolated.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
an OWNER-approved scope contract lists an acceptance criterion
WHEN:
no pytest acceptance test cites that criterion
THEN:
the scope check does not pass and the increment is not complete
WHEN:
every approved criterion is cited by a matching acceptance test as INCREMENT/AC-NNN
THEN:
the scope check can pass
WHEN:
a criterion has an empty GIVEN, WHEN, or THEN label
THEN:
the scope check does not pass
WHEN:
two increments each define AC-001 and cite INCREMENT/AC-001 on their own tests
THEN:
each increment validates independently

AC-002:
GIVEN:
a scope contract records anything other than NONE as unapproved scope
WHEN:
the scope check runs
THEN:
the result is SCOPE_CHANGE_REQUIRED and that behavior is not approved

AC-003:
GIVEN:
a discovery is recorded only under deferred discoveries
WHEN:
approved acceptance criteria are enumerated
THEN:
the discovery is not an acceptance criterion and does not authorize implementation

AC-004:
GIVEN:
a changed file is absent from the approved implementation map
WHEN:
the scope check compares that file with the active increment contract
THEN:
the result is SCOPE_CHANGE_REQUIRED and the file is not authorized
WHEN:
an older contract mapped a file and the active increment did not
THEN:
changing that older file is SCOPE_CHANGE_REQUIRED
WHEN:
the check runs without an explicit changed-file set
THEN:
the result is SCOPE_CHANGE_REQUIRED

AC-005:
GIVEN:
the scope-control checker, its acceptance tests, and the approved file map
WHEN:
imports and mapped paths are inspected
THEN:
they do not import application runtime, trading, or exchange modules and they do not modify architecture documents

EXPLICITLY OUT OF SCOPE:
- Product features, including Decision Intelligence, Committee, dashboard, detector, and execution behavior
- v1.4.3 section 13 platform acceptance scenarios (replay, duplicate intents, AI disabled, dashboard reconciliation)
- Copying the v1.4.3 architecture package into the repository
- A BDD framework, a separate CI system, SonarQube changes, or new Semgrep/Pyright installation. The atdd-scope job in the existing pytest workflow is the changed-file gate for this increment.
- Weakening existing tests or changing production runtime behavior

FROZEN BOUNDARIES:
- Paper execution stays isolated from funded order endpoints and funded credentials.
- PR #237 Feature Bus scope stays isolated.
- Coding-boundary contract tests stay free of app.services trading imports.
- The canonical writer remains the single domain write path.
- docs/architecture/v1.2/ is not modified by this increment.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_atdd_scope_control.py::test_ac_001_unmapped_criterion_fails_and_mapped_contract_passes
AC-001 -> tests/test_atdd_scope_control.py::test_ac_001_reused_criterion_numbers_stay_independent
AC-001 -> tests/test_atdd_scope_control.py::test_ac_001_discovers_pytest_acceptance_forms
AC-001 -> tests/test_atdd_scope_control.py::test_ac_001_empty_behavior_labels_fail
AC-002 -> tests/test_atdd_scope_control.py::test_ac_002_unapproved_scope_change_stops
AC-003 -> tests/test_atdd_scope_control.py::test_ac_003_deferred_discovery_is_not_approved
AC-004 -> tests/test_atdd_scope_control.py::test_ac_004_unmapped_changed_file_requires_scope_change
AC-004 -> tests/test_atdd_scope_control.py::test_ac_004_only_active_increment_authorizes_files
AC-004 -> tests/test_atdd_scope_control.py::test_ac_004_omitted_changed_file_set_fails_closed
AC-004 -> tests/test_atdd_scope_control.py::test_ac_004_git_diff_includes_rename_and_delete
AC-005 -> tests/test_atdd_scope_control.py::test_ac_005_checker_stays_outside_runtime_and_architecture

IMPLEMENTATION MAP:
AC-001 -> docs/atdd/README.md
AC-001 -> docs/atdd/scope-contracts/ATDD-000-scope-control.md
AC-001 -> tests/atdd_scope.py
AC-001 -> tests/test_atdd_scope_control.py
AC-001 -> pyproject.toml
AC-002 -> tests/atdd_scope.py
AC-002 -> tests/test_atdd_scope_control.py
AC-003 -> tests/atdd_scope.py
AC-003 -> tests/test_atdd_scope_control.py
AC-004 -> tests/atdd_scope.py
AC-004 -> tests/test_atdd_scope_control.py
AC-004 -> docs/atdd/ACTIVE_INCREMENT
AC-004 -> .github/workflows/pytest.yml
AC-005 -> tests/atdd_scope.py
AC-005 -> tests/test_atdd_scope_control.py
AC-005 -> pyproject.toml

DEFERRED DISCOVERIES:
- The checked-in architecture package is v1.2. Attached v1.4.3, and the v1.4.2 document named by AGENTS.md, are not in the repository. Vendoring them needs a separate OWNER decision.
- quality-security.yml does not run Semgrep or Pyright. Adding those jobs is a separate decision.
- v1.4.3 section 13 platform acceptance scenarios remain future increments.
- `pytest -m acceptance` still loads `tests/conftest.py` with the rest of the suite. Isolating acceptance tests from those existing autouse fixtures is a separate decision.

UNAPPROVED SCOPE CHANGES:
NONE
