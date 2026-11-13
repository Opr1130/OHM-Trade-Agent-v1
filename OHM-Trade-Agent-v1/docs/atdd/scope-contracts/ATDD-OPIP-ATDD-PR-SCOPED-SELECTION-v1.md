INCREMENT:
ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1

OWNER-APPROVED INTENT:
Eliminate recurring ATDD collisions by making scope-contract selection PR-scoped and deterministic instead of a single mutable global pointer, per the owner's ATDD Framework Repair mandate (2026-11-13). Simplest sufficient design: the increment is the one contract the change set adds or modifies; the legacy pointer is only a fallback when no contract changed. No production behavior changes.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/atdd/README.md: the pointer is deliberately movable orchestration state; unknown, ambiguous or missing selection fails closed.
- OHM-Trade-Agent-v1/tests/atdd_scope.py: existing check_contracts semantics, preserved.
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
several pull requests each carry their own scope contract and none edits a shared pointer
WHEN:
the atdd scope job runs with --pr-scoped
THEN:
each pull request is checked against the single contract it adds or modifies, whatever the ACTIVE_INCREMENT pointer holds on its base, and a stale pointer cannot invalidate it

AC-002:
GIVEN:
a change set whose contract selection is missing, ambiguous, mismatched or unauthorized
WHEN:
the scoped selection runs
THEN:
it fails closed: two changed contracts, a contract file name that differs from its INCREMENT, an edited pointer that disagrees with the changed contract, no contract and no pointer, a changed file outside the selected contract map, and a contract that does not map itself are each SCOPE_CHANGE_REQUIRED

AC-003:
GIVEN:
existing approved increments and the legacy --increment mode
WHEN:
a change set touches no scope contract or the legacy flags are used
THEN:
the legacy pointer behavior is unchanged, --pr-scoped cannot be combined with --increment, and CI runs the scoped checker with no bypass or continue-on-error

AC-004:
GIVEN:
a pull request rebased onto a base whose pointer moved
WHEN:
its changed-file set is selected again
THEN:
the same increment is selected and the verdict is unchanged

EXPLICITLY OUT OF SCOPE:
- Any production trading, scanner, gate, alert or configuration behavior.
- Removing the legacy --increment mode or the ACTIVE_INCREMENT file.
- Changing the maintenance-increment allowance in check_contracts.
- Review-based authorization of contract content (still a human review step).

FROZEN BOUNDARIES:
- Existing contracts, acceptance tests and release architecture gate are unchanged except the two workflow-invocation assertions (ATDD-000 AC-004 and ATDD-RELEASE-PIPELINE-v1 AC-027), which pinned the old pointer-fed command; owner to confirm.
- No bypass, disabled check or permissive fallback.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_001_concurrent_increments_coexist_without_a_shared_pointer
AC-001 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_001_legacy_global_pointer_collision_is_reproduced_and_repaired
AC-001 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_001_single_increment_pr_resolves_its_own_contract
AC-001 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_001_stale_pointer_is_ignored_when_the_pr_has_its_own_contract
AC-002 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_002_ambiguous_changed_contracts_fail_closed
AC-002 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_002_contract_stem_must_match_increment
AC-002 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_002_edited_pointer_must_agree_with_the_changed_contract
AC-002 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_002_missing_contract_and_missing_pointer_fail_closed
AC-002 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_002_new_contract_cannot_skip_its_own_authorization
AC-002 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_002_unauthorized_changed_file_is_rejected
AC-003 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_003_legacy_fallback_and_cli_contracts_are_unchanged
AC-003 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_003_workflow_uses_pr_scoped_mode_with_no_bypass
AC-004 -> tests/test_atdd_pr_scoped_selection_v1.py::test_ac_004_selection_is_stable_across_rebase_and_merge_order

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/atdd_scope.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_atdd_pr_scoped_selection_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1.md
AC-002 -> OHM-Trade-Agent-v1/tests/atdd_scope.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_atdd_pr_scoped_selection_v1.py
AC-003 -> .github/workflows/pytest.yml
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/README.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_feature_stack_gates_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_atdd_pr_scoped_selection_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_atdd_pr_scoped_selection_v1.py

DEFERRED DISCOVERIES:
- Optionally retire ACTIVE_INCREMENT after all open PRs carry their own contracts.
- Optionally require a modified (not added) historical contract to be flagged for explicit owner review.

UNAPPROVED SCOPE CHANGES:
NONE
