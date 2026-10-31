INCREMENT:
ATDD-PYTEST-ISOLATION-M01

OWNER-APPROVED INTENT:
Remove the vulnerable pytest test runner from O'Pip production dependencies, keep a patched and reproducible test runner in CI and manual agent test environments, and prove image isolation without weakening any release gate. Maintain a separately reviewed PR stacked on CI-01.

ARCHITECTURE REFERENCES:
- Architecture v1.5.0 EVIDENCE_SHADOW, F11 independent protection, canonical writer
- Dockerfile production dependency installation boundary
- pytest.yml mandatory tests, ATDD scope, release security and architecture gates
- ATDD-CI-STABILIZATION-M01 CI-only maintenance contract

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the production Dockerfile installs only requirements.txt
WHEN:
the application image is built and inspected
THEN:
pytest and test-only requirement files are absent from the application image

AC-002:
GIVEN:
CI and manual agent workflows must still run pytest
WHEN:
test dependencies are installed
THEN:
a pinned patched pytest is installed in the test environment only and all existing test invocations remain available

AC-003:
GIVEN:
the scanner reliability increment remains the active ATDD scope
WHEN:
dependency-isolation file changes are validated
THEN:
only files exclusively mapped to this approved scope pass and mixed or unmapped application changes fail closed

EXPLICITLY OUT OF SCOPE:
- production deploy or activation; changes to runtime authority
- disabling or weakening security scanners, mandatory CI or tests
- upgrading other application dependencies
- modifying scanner, learning, paper, funded trading, committee or canonical writer logic

FROZEN BOUNDARIES:
- EVIDENCE_SHADOW, canonical writer ownership, F11 independence and funded credential isolation
- Paper-v2 OFF; Committee OFF; learning frozen
- no merges, deployments or activations without separate owner approval

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_production_dependency_isolation_v1.py::test_runtime_dependencies_exclude_pytest_and_image_installs_only_runtime
AC-002 -> tests/test_production_dependency_isolation_v1.py::test_patched_pytest_is_explicitly_test_only
AC-003 -> tests/test_production_dependency_isolation_v1.py::test_scope_remains_fail_closed_for_mixed_or_unmapped_paths

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/requirements.txt
AC-001 -> .github/workflows/pytest.yml
AC-001 -> OHM-Trade-Agent-v1/tests/test_production_dependency_isolation_v1.py
AC-002 -> OHM-Trade-Agent-v1/requirements-test.txt
AC-002 -> .github/workflows/pytest.yml
AC-002 -> .github/workflows/m07-cutover-toolkit.yml
AC-002 -> .github/workflows/opip-claude-code.yml
AC-002 -> OHM-Trade-Agent-v1/tests/test_production_dependency_isolation_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/atdd_scope.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-PYTEST-ISOLATION-M01.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_production_dependency_isolation_v1.py

DEFERRED DISCOVERIES:
- full pytest 9.0.3 compatibility verified by exact-head GitHub Actions after PR creation
- production image build and no-pytest probe verified by exact-head GitHub Actions
- unrelated Ruff/Bandit baseline advisory findings addressed in a separate review

UNAPPROVED SCOPE CHANGES:
NONE
