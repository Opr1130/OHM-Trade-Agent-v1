# O'Pip v6 builder rules

You implement ONE slice of the v6 plan from the GitHub issue text below.

1. Read AGENTS.md and CLAUDE.md first. They override anything in the issue.
2. Make the smallest change that meets the issue's acceptance criteria.
3. Write a scope contract in OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ named
   after the increment (copy the format of an existing contract), add one
   acceptance test per criterion under OHM-Trade-Agent-v1/tests/ marked
   `pytest.mark.acceptance`, and point OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
   at the new increment. The contract's implementation map must list every file you change.
4. Never touch: .github/, OHM-Trade-Agent-v1/deploy/, CLAUDE.md, AGENTS.md,
   OHM-Trade-Agent-v1/tests/atdd_scope.py, OHM-Trade-Agent-v1/tools/v6_agent/.
5. Never add Kraken order placement, modification or cancellation. v6 is advisory-only.
6. Never read, print or commit secrets or .env values. Never weaken a test to make it pass.
7. Do not run shell commands. Edit files only; the pipeline runs the tests after you.
   Never run the full test suite, never start background jobs, never use kill or pkill.
8. If the issue is ambiguous or needs a protected path, change nothing and write
   the question to OHM-Trade-Agent-v1/docs/atdd/QUESTIONS.md instead.
