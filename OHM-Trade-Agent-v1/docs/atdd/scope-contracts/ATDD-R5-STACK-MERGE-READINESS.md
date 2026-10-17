INCREMENT:
ATDD-R5-STACK-MERGE-READINESS

OWNER-APPROVED INTENT:
This is the OWNER-directed dependency-aware merge-readiness verification increment for the stacked pull-request chain #369-#373. The OWNER directed that merge readiness be verified for those pull requests, including upstream dependencies, required CI gates, CircleCI status and regression evidence, and that only unresolved blockers and decisions requiring owner approval be reported.

It verifies and records. It merges nothing, deploys nothing, re-points nothing and changes no gate: no application, service, API, job, scheduler, workflow, release-profile, deployment, branch-protection or configuration file is touched, and no trading, paper, Committee, Telegram or exchange authority is created or widened. Its deliverable surface is four paths: the active-increment pointer, this contract, one evidence document under `docs/atdd/evidence/`, and one test module that keeps the record honest and self-consistent.

WHAT WAS VERIFIED. The chain's dependency-aware order (the base of each pull request is exactly the head branch of the one below it, proved with `git merge-base --is-ancestor`), the upstream dependency that explains why three of the five heads have no gate evidence at all, the contractual required-gate set and the absence of any GitHub branch protection on `main`, the gate evidence that exists at each head with its workflow run identifiers, the CircleCI status on all five heads and on `main`, the regression evidence at each head where a suite ran, and the pre-existing advisory failures that are not required.

WHAT WAS FOUND, ONE LINE EACH. #373 has complete, green required-gate evidence at its exact head and is blocked only by the merge mechanics below. #372 is red at its own head (`test`: 1 failed, 8157 passed) because the false-positive ML-dependency guard repaired by #373 is still present there. #369, #370 and #371 have never run the required gates, because the widened pull-request trigger only exists from #372's head onward, so their readiness is unproven rather than proven. CircleCI fails identically on `main` and is contractually not required. And - the substantive discovery - landing any pull request of this chain into its base branch invalidates the `atdd scope` gate of the pull request directly below it, because that gate reads the active-increment pointer from the new head and the changed-file diff from the base; this was reproduced twice with the real checker.

KEY LAW - VERIFICATION IS NOT AUTHORIZATION. Recording that a gate passed authorizes no merge, no deploy, no activation and no retirement. The merge order, the pointer-restoration procedure and every pull-request state change remain owner-gated decisions, and this increment reports them instead of taking them.

KEY LAW - ABSENT EVIDENCE IS NOT GREEN EVIDENCE. A required gate that never ran is recorded as `NOT RUN`, and a pull request whose required gates did not run is recorded as `NOT READY`, never as passed by association with a green head above it.

ARCHITECTURE REFERENCES:
- `.github/workflows/pytest.yml`: the release workflow whose jobs define the gate set (`test`, `atdd scope`, `release architecture gate`, `release security gate (Bandit)`, and the `main`-only `non-deploying main release candidate`), and the `atdd scope` job that evaluates the pull-request diff against `docs/atdd/ACTIVE_INCREMENT` read from the head.
- `docs/engineering/LOCAL_AGENT_BRIDGE_V1.md` section 12.2: `test`, `atdd scope` and `semgrep/ci` are structurally mandatory; a missing protected context is pending, never satisfied; skipped checks are not success; advisory jobs and "unrelated failures such as `CircleCI Pipeline`" are deliberately not required; the reviewed pull request must stay a draft with auto-merge disabled.
- `docs/atdd/README.md` and `tests/atdd_scope.py`: the scope mechanism whose head-read pointer rule produces the merge-order interaction recorded here.
- `docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md` `AC-027` and `tests/test_opip_feature_stack_gates_v1.py`: the increment that made stacked `feature/**` pull requests start the same gates, and the reason three heads still have no gate evidence.
- `docs/atdd/scope-contracts/ATDD-ML-DEPENDENCY-POLICY-v1.md` and `docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-contract.md`: the two increments above this one in the stack, both of which inherit the merge-order constraint.
- `docs/atdd/evidence/R4-F8-BASELINE-TRIAGE.md`: the repository's existing baseline-triage evidence pattern this document follows (starting revision, exact command, exact counts, CI as the authority).
- `AGENTS.md` principles 12, 17 and 18: run the relevant validation, do not create or push commits without instruction, and do not merge or deploy without explicit instruction.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the stacked pull-request chain and its base/head construction
WHEN:
the recorded chain is inspected
THEN:
one row exists per pull request from #362 to #373 in ascending order, each with its base branch, head branch and head SHA, `base(n)` is exactly `head(n-1)` for every consecutive pair, the first row's base is `main`, and the record states that merging a pull request lands its content into the head branch below it

AC-002:
GIVEN:
the required gate set and the recorded gate evidence per head
WHEN:
each in-scope pull request's evidence is read
THEN:
every one of #369-#374 records all five required contexts - `test`, `atdd scope`, `release architecture gate` and `release security gate (Bandit)` as the release workflow names them, plus `semgrep/ci` as the separate security-workflow context the bridge contract treats as structurally mandatory - from the closed disposition vocabulary `SUCCESS`, `FAILURE` and `NOT RUN`, a row may be marked gate-ready only when all five are `SUCCESS`, no row whose gates did not all pass is presented as ready, and this increment authorizes no merge, deploy, re-point or gate change

AC-003:
GIVEN:
the upstream dependency that decides when the gates start
WHEN:
the three heads without gate evidence are inspected
THEN:
the record shows that #369, #370 and #371 have no `pytest` workflow run at all because their heads still carry the `branches: [main]` pull-request trigger while the widened `[main, 'feature/**']` trigger exists only from #372's head, so their readiness is unproven rather than proven

AC-004:
GIVEN:
the CircleCI status and the other non-required checks
WHEN:
they are classified
THEN:
CircleCI is recorded as an external project-level configuration failure with its exact description on all five heads and on `main`, classified as pre-existing and deliberately not required, and the other advisory or draft-skipped checks are recorded as not required

AC-005:
GIVEN:
the movable active-increment pointer and the merge mechanics of a linear stack
WHEN:
the merge-order interaction is reported
THEN:
the record contains the reproduction, the observed `PASS` baseline and the two `SCOPE_CHANGE_REQUIRED` (exit 2) outcomes in both directions, states that the failure is a false scope violation caused by merge mechanics, and lists the merge-order and pointer-restoration decision as owner-gated rather than deciding it

AC-006:
GIVEN:
the regression evidence at each head
WHEN:
the suite results are compared
THEN:
the red base head with its exact counts and its single repaired failure is recorded, the green head with its exact counts is recorded, the three heads where no suite ever ran are recorded as absent evidence, and no green result is transferred to a head where no suite ran

EXPLICITLY OUT OF SCOPE:
- Merging, squashing, rebasing, force-pushing, re-pointing, retargeting or closing any pull request or branch
- Re-pointing or editing `docs/atdd/ACTIVE_INCREMENT` for any purpose other than naming this increment
- Branch protection, rulesets, repository settings, workflow triggers, job lists, gate definitions or advisory workflows
- Any application, service, API, job, scheduler, template, test of product behaviour, release-profile, deployment or configuration change
- Any change to `tests/atdd_scope.py`, its parser, its arrow syntax, its exit codes or its fail-closed behaviour
- Any trading, paper, Committee, Telegram, admission, exit, promotion or exchange authority
- Any deployment, activation, cutover, retirement or production action
- Weakening, skipping, xfailing or deleting any existing test, gate or contract

FROZEN BOUNDARIES:
- This increment records evidence; it authorizes no state change in GitHub, in CI, in production or in any branch.
- The record's dispositions are a snapshot at the stated date and SHAs; a stale record is re-verified by an authorized file change, never silently trusted.
- A required gate that did not run is `NOT RUN` and cannot be reported as passed, skipped-as-success or waived.
- CircleCI and the advisory review workflows remain outside the required set; this increment neither adds nor removes a required context.
- The merge-order and pointer-restoration decision stays with the OWNER, and no branch is modified to make any landing option easier.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_stack_merge_readiness.py::test_ac_001_recorded_chain_is_dependency_aware
AC-002 -> tests/test_opip_r5_stack_merge_readiness.py::test_ac_002_gate_evidence_matches_the_workflow_and_the_verdicts
AC-003 -> tests/test_opip_r5_stack_merge_readiness.py::test_ac_003_missing_gate_evidence_is_recorded_not_assumed
AC-004 -> tests/test_opip_r5_stack_merge_readiness.py::test_ac_004_circleci_is_recorded_as_not_required
AC-005 -> tests/test_opip_r5_stack_merge_readiness.py::test_ac_005_pointer_merge_order_interaction_is_recorded
AC-006 -> tests/test_opip_r5_stack_merge_readiness.py::test_ac_006_regression_evidence_is_per_head

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-STACK-MERGE-READINESS.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5-STACK-MERGE-READINESS-369-373.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5-STACK-MERGE-READINESS-369-373.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_stack_merge_readiness.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5-STACK-MERGE-READINESS-369-373.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_stack_merge_readiness.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5-STACK-MERGE-READINESS-369-373.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_stack_merge_readiness.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-STACK-MERGE-READINESS.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5-STACK-MERGE-READINESS-369-373.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_stack_merge_readiness.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5-STACK-MERGE-READINESS-369-373.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_stack_merge_readiness.py


DEFERRED DISCOVERIES:
- The merge-order interaction (`AC-005`) is a real governance gap between "one increment owns the movable pointer" and "a multi-level stack lands into base branches". It is recorded here and not fixed; changing the pointer mechanism, the scope checker or the landing procedure needs its own authorized increment, and the OWNER must decide the landing procedure for this chain.
- `main` carries no branch protection, so no required context is GitHub-enforced. Whether `main` should be protected with the contractual required contexts is a repository-settings and owner decision, not a code change, and it is out of scope here.
- The evidence record's gate outcomes and head SHAs are a snapshot. If a head is refreshed, a gate is re-run or a pull request state changes, the record goes stale and re-verifying it is an authorized file change under a new increment, exactly as the RED tests of `ATDD-R5-F10-dashboard-parity-contract` require for a parity flip.
- Whether #372 should absorb the ML-dependency repair, or be landed only together with #373, is a merge-order decision for the OWNER; no branch was changed to make either option true.
- CircleCI has no configuration for this project at all, so its `error` status carries no information about any branch. Removing CircleCI from the repository's expectations, or adding a real CircleCI configuration, is a separate owner decision.
- The three heads without gate evidence are not defective; they simply predate the widened trigger. Refreshing them onto it would start the gates, but that is a branch operation and therefore out of scope here.
- The four-path implementation map here shows the mechanical cost of the current pointer rule: every increment stacked above this chain must record the same interaction until the OWNER resolves it.

UNAPPROVED SCOPE CHANGES:
NONE

