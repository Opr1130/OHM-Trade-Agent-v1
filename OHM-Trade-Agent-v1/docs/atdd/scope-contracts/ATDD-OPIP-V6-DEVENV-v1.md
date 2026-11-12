INCREMENT:
ATDD-OPIP-V6-DEVENV-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. Add the
automated v6 build loop: an issue labeled `build` is implemented by a DeepSeek
builder on a v6/ branch and opened as a PR; failed pytest runs get at most two
automatic fix attempts; after the owner merges, the next queued slice starts.
The owner only approves and merges. This increment changes no application code,
deploy workflow, trading, learning, AI-review or notification behavior.

ARCHITECTURE REFERENCES:
- docs: V6 plan (Zero-handover workflow); CLAUDE.md and AGENTS.md guardrails.
- .github/workflows/pytest.yml: the check whose failure triggers an automatic fix.
- docs/atdd/ACTIVE_INCREMENT and tests/atdd_scope.py: the scope gate every builder PR must satisfy.

APPROVED ACCEPTANCE CRITERIA:

AC-801:
GIVEN: the v6-agent workflow
WHEN: any event other than an owner or bot `build` label, an owner `/fix` comment on a v6/ PR, a failed pytest run on a v6/ branch, or a merged v6/ PR occurs
THEN: no agent job runs, and the workflow has no pull_request or push trigger

AC-802:
GIVEN: the builder has produced changes
WHEN: the pipeline publishes them
THEN: it pushes only to a v6/ branch, never to main, and verification fails any change touching .github/, deploy/, CLAUDE.md, AGENTS.md, the scope gate or the agent tooling

AC-803:
GIVEN: the builder and its tests
WHEN: verification runs
THEN: no step that runs the application tests or the scope gate receives the DeepSeek credential

AC-804:
GIVEN: a slice issue
WHEN: it is created from the template
THEN: it requires goal, acceptance criteria, files, out-of-scope and dependencies

AC-805:
GIVEN: queued slices and a merged v6 PR
WHEN: the advance step runs
THEN: it labels the lowest-numbered queued slice whose dependencies are closed, and labels nothing while a v6 PR is open

AC-806:
GIVEN: a failing pytest run on a v6/ branch
WHEN: automatic fixing is considered
THEN: at most two automatic attempts are made per PR, after which the owner is asked to decide

EXPLICITLY OUT OF SCOPE:
- Staging deploy automation, slices 1-9 themselves and the V6 spec.
- Creating the GitHub App, its secrets or variables, or changing branch protection (owner setup).
- Merging PRs, production deployment or any change to deploy-*.yml.

FROZEN BOUNDARIES:
- No application code, compose, trading, Kraken, F11 or Telegram changes.
- Coding automation keeps no trading authority; the builder never gains merge or deploy rights.
- Existing opip-claude-code, deepseek-quant-review and deploy workflows are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-801 -> tests/test_v6_devenv_v1.py::test_agent_workflow_triggers_are_event_limited
AC-802 -> tests/test_v6_devenv_v1.py::test_builder_pushes_only_v6_branches_and_protects_paths
AC-803 -> tests/test_v6_devenv_v1.py::test_verification_steps_have_no_ai_credential
AC-804 -> tests/test_v6_devenv_v1.py::test_slice_template_requires_scope_fields
AC-805 -> tests/test_v6_devenv_v1.py::test_advance_picks_next_unblocked_slice_one_at_a_time
AC-806 -> tests/test_v6_devenv_v1.py::test_autofix_is_bounded

IMPLEMENTATION MAP:
AC-801 -> .github/workflows/v6-agent.yml
AC-801 -> OHM-Trade-Agent-v1/tests/test_v6_devenv_v1.py
AC-801 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-801 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-DEVENV-v1.md
AC-802 -> .github/actions/v6-run/action.yml
AC-802 -> OHM-Trade-Agent-v1/tools/v6_agent/verify.sh
AC-802 -> OHM-Trade-Agent-v1/tools/v6_agent/run_agent.sh
AC-802 -> OHM-Trade-Agent-v1/tools/v6_agent/builder_prompt.md
AC-803 -> .github/actions/v6-run/action.yml
AC-804 -> .github/ISSUE_TEMPLATE/v6-slice.yml
AC-805 -> OHM-Trade-Agent-v1/tools/v6_agent/advance.py
AC-806 -> .github/workflows/v6-agent.yml

DEFERRED DISCOVERIES:
- Without the GitHub App, PRs and labels created with the default token do not start other workflows, so the loop pauses until the owner labels or re-runs; the App is a one-time owner setup.
- The Aider version range and the `deepseek/deepseek-v4-pro` model name are assumptions to confirm on the first run.
- The first v6 slices and docs/V6_SPEC.md are separate PRs.

UNAPPROVED SCOPE CHANGES:
NONE
