INCREMENT:
ATDD-CI-STABILIZATION-M01

OWNER-APPROVED INTENT:
Stabilize GitHub Actions without merging, deploying, activating, or changing production runtime. Ensure exact-main-SHA Semgrep coverage, retire obsolete PR69 review automation, and permit this independently approved CI-only maintenance work alongside the active scanner increment without changing its pointer.

ARCHITECTURE REFERENCES:
- Architecture v1.5.0: EVIDENCE_SHADOW, canonical writer ownership, F11 independence
- .github/workflows/pytest.yml mandatory release and ATDD gates
- main-protection required checks: test, atdd scope, semgrep/ci
- deploy-learning-recovery.yml exact-SHA certification contract

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a new commit is pushed to main without changing the Semgrep workflow
WHEN:
GitHub Actions evaluates the mandatory Semgrep workflow triggers
THEN:
the workflow runs without a path filter and the semgrep/ci job name remains stable

AC-002:
GIVEN:
PR number 69 has been merged and its legacy auto-review workflows are obsolete
WHEN:
workflow trigger configuration is inspected
THEN:
obsolete PR69 workflows are absent and generic AI reviews require an explicit manual PR number

AC-003:
GIVEN:
the scanner reliability increment stays the active ATDD pointer
WHEN:
a changed-file set is checked against an independently approved CI maintenance contract
THEN:
only an entirely mapped CI maintenance change set is admitted and mixed or unapproved paths fail closed

EXPLICITLY OUT OF SCOPE:
- workflow deployment or owner authorization changes
- changes to GitHub branch rulesets
- trading, scanner, indicator, learning or committee runtime changes
- broad ATDD bypass or change of the active scanner increment
- changes to the release and recovery workflow certification logic (deferred)

FROZEN BOUNDARIES:
- EVIDENCE_SHADOW, F11 protection independence and canonical writer authority
- no funded trading authority, PAPER_V2 OFF, Committee OFF, learning frozen
- no merge or deploy without separate explicit owner approval

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_ci_stabilization_v1.py::test_semgrep_covers_every_main_commit
AC-002 -> tests/test_ci_stabilization_v1.py::test_obsolete_pr69_review_workflows_removed
AC-002 -> tests/test_ci_stabilization_v1.py::test_general_ai_reviews_require_explicit_pr_number
AC-003 -> tests/test_ci_stabilization_v1.py::test_ci_scope_coexists_without_repointing_active_increment

IMPLEMENTATION MAP:
AC-001 -> .github/workflows/semgrep.yml
AC-001 -> OHM-Trade-Agent-v1/tests/test_ci_stabilization_v1.py
AC-002 -> .github/workflows/gemini-review.yml
AC-002 -> .github/workflows/deepseek-quant-review.yml
AC-002 -> .github/workflows/pr69-ai-review-dispatch.yml
AC-002 -> .github/workflows/pr69-external-reviews.yml
AC-002 -> .github/workflows/pr69-external-reviews-manual.yml
AC-002 -> .github/workflows/pr69-external-reviews-repository-dispatch.yml
AC-002 -> OHM-Trade-Agent-v1/tests/test_ci_stabilization_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/atdd_scope.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-CI-STABILIZATION-M01.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_ci_stabilization_v1.py

DEFERRED DISCOVERIES:
- GitHub ruleset required check additions need a separate approved administrator change
- learning recovery exact-SHA CI query currently searches only the newest 100 runs and can miss busy-main certification
- advisory quality-security success does not certify clean scanner findings
- measure full-run runtime and investigate whether remaining invalid-review workflow failures persist after this change

UNAPPROVED SCOPE CHANGES:
NONE
