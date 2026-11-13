INCREMENT:
ATDD-OPIP-CI-CLEAN-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. Remove
permanently red advisory scans from the pull-request path and replace the
history-wide gitleaks advisory with a blocking scan of the PR's own commits.
This increment changes no application code, deploy workflow, trading, learning,
AI or notification behavior, and weakens no required gate.

ARCHITECTURE REFERENCES:
- .github/workflows/quality-security.yml: advisory scanners, now main-push only.
- .github/workflows/secret-scan-pr.yml: new blocking PR-commit secret scan.
- .github/workflows/deploy-learning-recovery.yml: requires an exact-SHA successful quality-security run on main.

APPROVED ACCEPTANCE CRITERIA:

AC-701:
GIVEN: the advisory quality-security workflow
WHEN: a pull request is opened or updated
THEN: the workflow does not run on pull requests, still runs on pushes to main, and every job stays non-blocking so the exact-SHA main run that deploy-learning-recovery requires still succeeds

AC-702:
GIVEN: a pull request adds commits
WHEN: the secret scan workflow runs
THEN: it scans only the commits between the PR base and head and fails the check when a secret is found

EXPLICITLY OUT OF SCOPE:
- Changing pytest.yml, semgrep.yml, the atdd scope gate or any deploy workflow.
- Making ruff, Bandit or pip-audit blocking, or removing them.
- Changing review-bot configuration or required-check settings on GitHub.

FROZEN BOUNDARIES:
- No application, tool or compose file changes.
- Trading authority, F11 admissions and fail-closed gates are unchanged.
- deploy-learning-recovery still finds exact-SHA main runs of pytest, quality-security and Semgrep.

ACCEPTANCE TEST TRACEABILITY:
AC-701 -> tests/test_ci_clean_v1.py::test_quality_security_is_main_push_only_and_non_blocking
AC-702 -> tests/test_ci_clean_v1.py::test_pr_secret_scan_scans_only_pr_range_and_blocks

IMPLEMENTATION MAP:
AC-701 -> .github/workflows/quality-security.yml
AC-701 -> OHM-Trade-Agent-v1/tests/test_ci_clean_v1.py
AC-701 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-701 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CI-CLEAN-v1.md
AC-702 -> .github/workflows/secret-scan-pr.yml
AC-702 -> OHM-Trade-Agent-v1/tests/test_ci_clean_v1.py

DEFERRED DISCOVERIES:
- The full-history gitleaks advisory fails on main; its findings need a baseline before it can be blocking.
- The six deploy workflows start on every PR comment and then skip; consolidating them is a separate owner-approved change.
- Review-bot reduction is a GitHub app setting, not code.

UNAPPROVED SCOPE CHANGES:
NONE
