INCREMENT:
ATDD-OPIP-V6-R2-RETIREMENT-READINESS-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. V6 audit
theme R2 lists ten single-file `app/opip/*` modules and `app/backtesting` as the
strongest retirement candidates, and the audit's sequence requires every
retirement to prove that nothing still depends on the theme before code, tests
and config are deleted together. This increment adds the measurement half of
that rule and nothing more: one read-only report that, for each R2 module, lists
the source files that import it, the test files that reference it and the
documents that mention it. The report is evidence for a later, separately
approved retirement; it only reads text files and prints a report. No R2 module,
test, doc or audit is deleted, moved, rewritten or classified as dead, no
database is opened, no scheduler, compose, deploy or workflow changes, and no
trading, exchange, Telegram or learning authority is added or widened.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_AUDIT.md: section 4 row 57 (the ten single-file `app/opip/*` modules) and its `app/backtesting` row (theme R2), section 11 row 128 (R2 sequence: ten single-file modules plus `app/backtesting`, replay code kept) and the section 11 rule that every retirement disables, observes, then deletes code, tests and config together.
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 4 slice rules (additive pure modules and read-only tools before any wiring) and section 7 safety boundary.
- OHM-Trade-Agent-v1/AGENTS.md: principle 8 (durable evidence is not destructively mutated), principle 9 (separation between production, shadow and offline planes) and principle 21 (every persisted artifact reaches a traceable disposition).
- OHM-Trade-Agent-v1/CLAUDE.md: engineering operating model (smallest change, reuse existing read-only tool conventions, no new infrastructure) and trading safety boundary.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the repository described by the v6 audit, whose theme R2 is the ten single-file `app/opip/*` modules plus the `app/backtesting` package
WHEN: the retirement readiness report is generated for that repository
THEN: every R2 module from the audit is listed with its dotted module, its source path, the importer files that import it, the test files that reference it and the documentation files that mention it, and no module is listed as its own importer

AC-002:
GIVEN: any repository root, including one that does hold references, plus a missing root, text output and JSON output
WHEN: the retirement readiness report runs
THEN: nothing is created, changed or removed and the on-disk state is identical before and after

EXPLICITLY OUT OF SCOPE:
- Deleting, moving, renaming, rewriting, quarantining or ordering the retirement of any R2 module, test, doc, config entry or workflow, and any `--apply`, `--retire` or force flag.
- Declaring any R2 module dead code; the report is evidence and the retirement decision stays owner-approved in its own increment.
- Wiring the report into runtime, scheduler, compose, deploy, CI, Telegram, Paper or learning paths; the tool is invoked manually only.
- Database, Postgres, analytics-plane, canonical writer or learning evidence changes, and mutation of any durable store.
- Trading authority, Kraken order placement, modification, cancellation or confirmation of any kind, Telegram execution, and any widening of Paper or production admission.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.

FROZEN BOUNDARIES:
- Advisory and read-only: coding automation has no trading authority and no exchange permissions, and this tool has no mutation authority over any file either.
- Deterministic gates keep failing closed; external AI stays advisory evidence and is not involved.
- The R2 list in the audit is unchanged; this increment observes it and does not add, remove, reorder, promote or retire a module.
- Existing modules, tests, deploy paths and other increments' contracts keep their current behavior.
- No secret, credential or `.env` value is read, printed or written; only `.py`, `.md` and `.rst` files are opened, and the report prints only repository-relative paths and counts.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_r2_retirement_readiness_v1.py::test_report_lists_every_r2_module_with_importers_tests_and_docs
AC-002 -> tests/test_v6_r2_retirement_readiness_v1.py::test_report_never_creates_changes_or_removes_a_file


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/data_platform/retirement_readiness.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_r2_retirement_readiness_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-R2-RETIREMENT-READINESS-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/data_platform/retirement_readiness.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_r2_retirement_readiness_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-R2-RETIREMENT-READINESS-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- The audit's section 11 precondition reads "no test or doc depends on them", while this report already shows real test and doc references for every R2 module. The retirement decision must therefore reconcile the precondition with the measured references and record the disposition; that reconciliation is a separate owner-approved increment, not this tool.
- Importers are detected only from Python imports (including relative imports) and exact dotted or source-path references in docs. Dynamic imports built from strings, string concatenation and generated documentation are not resolved, so a retirement increment should re-run the report after any test or doc edits.
- The report scans the whole repository on each invocation and takes about a second on the current tree; a bounded, cached or per-module variant is not needed yet.
- Whether replay code that imports `app/backtesting` is kept or retired is an audit decision. The report only shows the current references and does not classify any of them.

UNAPPROVED SCOPE CHANGES:
NONE
