INCREMENT:
ATDD-CI-OPTIMIZATION-M01

OWNER-APPROVED INTENT:
Stop automatic GitHub Actions runs from executing the complete 10k-test pytest suite on every pull request and every main push, while preserving every financial and production safety protection. Automatic release CI runs only the tests that reference the changed modules plus a small mandatory safety suite covering F11 protection, funded-trading authorization, financial calculations, portfolio protection and configuration validation. The complete suite and the real-Docker OOM/fault-injection proofs become manual-only evidence, and the exact-SHA deployment gate requires the focused release CI with executed-and-passed mandatory safety evidence. No production configuration, no trading authority and no deployed behaviour changes, and every existing test source file is preserved.

ARCHITECTURE REFERENCES:
- Architecture v1.5.0: EVIDENCE_SHADOW, canonical writer ownership, F11 independence, funded authority absent
- .github/workflows/pytest.yml mandatory release, ATDD scope, architecture and security gates
- .github/workflows/deploy-production.yml owner-gated exact-SHA production and UI release control
- .github/workflows/deploy-learning.yml, deploy-committee.yml, deploy-analytics.yml and committee-shadow-activation.yml exact-SHA CI certification
- main-protection required checks: test, atdd scope, semgrep/ci

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a pull request or a main push is evaluated by automatic release CI
WHEN:
the pytest workflow test job is inspected
THEN:
it runs the changed-module selection and the mandatory safety suite as bounded explicit targets, the unconditional full-suite invocation is absent, the trigger and five-gate job set are unchanged, and no gate becomes advisory or skips its targets

AC-002:
GIVEN:
the complete pytest suite and the real-Docker OOM/fault-injection proofs are expensive and rarely change behaviour
WHEN:
they are required as evidence
THEN:
a manual-dispatch-only workflow runs them, the selector never returns them automatically, and no pull request, main push or schedule can start them

AC-003:
GIVEN:
a change to a production module, a workflow, a deployment script or documentation
WHEN:
the focused selection and the mandatory safety suite are resolved
THEN:
the selection is deterministic, references only existing test sources, never runs vacuously, and the mandatory suite still covers F11 protection, funded-trading authorization, financial calculations, portfolio protection and configuration validation through node ids that resolve to real tests

AC-004:
GIVEN:
a mandatory safety test that was skipped, failed, removed or never executed
WHEN:
the JUnit receipt is verified
THEN:
certification fails closed with an explicit reason and no deployment can treat the run as successful

AC-005:
GIVEN:
an owner-approved exact main SHA
WHEN:
the production release controller or the UI release controller evaluates the exact-SHA gate
THEN:
it requires a completed successful focused release CI run whose job and step evidence proves the selection, the mandatory safety suite and its fail-closed receipt actually ran, and any missing or skipped evidence refuses the release

EXPLICITLY OUT OF SCOPE:
- production deploy, activation or rollback; changes to trading authority or risk defaults
- changes to the release profile contract, funded credential isolation, F11 protection logic or portfolio logic
- deleting, rewriting or weakening any existing test module or acceptance criterion
- changing the branch ruleset, required checks, owner gating or manual-only AI review workflows
- fixing the duplicate scenario executions inside the ATDD-M07 cutover toolkit workflow (deferred)

FROZEN BOUNDARIES:
- EVIDENCE_SHADOW profile, canonical writer ownership, F11 independence and funded credential isolation
- Paper-v2 OFF, Committee OFF, learning frozen, no funded trading authority
- no merge, deployment or activation without separate explicit owner approval

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_ci_optimization_v1.py::test_ac_001_automatic_release_ci_runs_focused_selection_and_mandatory_safety
AC-002 -> tests/test_opip_ci_optimization_v1.py::test_ac_002_full_suite_and_docker_oom_proofs_are_manual_only
AC-003 -> tests/test_opip_ci_optimization_v1.py::test_ac_003_mandatory_safety_suite_covers_the_protected_surfaces
AC-004 -> tests/test_opip_ci_optimization_v1.py::test_ac_004_receipt_fails_closed_when_safety_tests_did_not_execute
AC-005 -> tests/test_opip_ci_optimization_v1.py::test_ac_005_exact_sha_deployment_gate_requires_focused_release_ci_evidence

IMPLEMENTATION MAP:
AC-001 -> .github/workflows/pytest.yml
AC-001 -> OHM-Trade-Agent-v1/tests/ci_release_selection.py
AC-001 -> OHM-Trade-Agent-v1/tests/ci_mandatory_safety_receipt.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_ci_optimization_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/release/OPIP_CI_OPTIMIZATION_V1.md
AC-002 -> .github/workflows/pytest-full-manual.yml
AC-002 -> OHM-Trade-Agent-v1/tests/ci_release_selection.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_ci_optimization_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/ci_release_selection.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_ci_optimization_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/atdd_scope.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-CI-OPTIMIZATION-M01.md
AC-004 -> OHM-Trade-Agent-v1/tests/ci_mandatory_safety_receipt.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_ci_optimization_v1.py
AC-005 -> .github/workflows/deploy-production.yml
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_ci_optimization_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/release/OPIP_CI_OPTIMIZATION_V1.md

DEFERRED DISCOVERIES:
- the ATDD-M07 cutover toolkit workflow re-runs two scenario modules that its whole-tree scenario run already covers; removing that duplication needs a separate approved amendment of ATDD-M07-V2-cutover-toolkit-v1
- deploy-learning.yml, deploy-committee.yml, deploy-analytics.yml and committee-shadow-activation.yml still certify an exact SHA from the pytest.yml run conclusion only; extending the job-and-step evidence check to them is a separate approved change
- the local pre-PR gate description in the VS Code task set still calls pytest.yml the canonical full Linux regression; re-wording it needs an ATDD-RELEASE-PIPELINE-v1 amendment
- focused selection is a reference index of changed modules and paths, so a conftest-only or cross-cutting change may still require a manual pytest-full-manual dispatch

UNAPPROVED SCOPE CHANGES:
NONE
