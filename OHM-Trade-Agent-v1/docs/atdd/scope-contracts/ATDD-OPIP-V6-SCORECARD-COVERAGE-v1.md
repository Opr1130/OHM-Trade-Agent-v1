INCREMENT:
ATDD-OPIP-V6-SCORECARD-COVERAGE-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. This v6
slice adds one printed coverage header line to the already-approved read-only
Scorecard report (`app/opip/analytics/scorecard_report.py`). The first line of
the text report now states the total number of records the report read and how
many of those carry no usable strategy version stamp, so a reader sees evidence
coverage before the per-version rows instead of inferring it from the trailing
counts. The count reuses the pure core's `UNVERSIONED` rule rather than restating
it, so the header and the per-version rows cannot disagree. This is printed
output only: no scoring rule, hit rate, grouping, stream discovery or selection
changes, nothing is written, created, renamed, truncated or deleted, and no
scheduler, compose, deploy, trading, exchange, Telegram, database or learning
authority is added or widened.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 1 (v6 makes the scanner measurable), section 2 (hard limits: advisory only, no trading authority), section 4 (one small change per slice with GIVEN/WHEN/THEN criteria, a scope contract and acceptance tests) and section 7 (safety boundary).
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-REPORT-v1.md: the approved read-only report this slice extends, its record contract and its AC-002 promise that no file is created, changed or removed.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-CORE-v1.md: the approved pure core and its `UNVERSIONED` grouping rule, which the header reuses through `version_of`.
- OHM-Trade-Agent-v1/app/opip/analytics/scorecard.py: `UNVERSIONED` and `version_of`, reused unchanged; no scoring behavior is modified.
- OHM-Trade-Agent-v1/app/opip/storage/bounded_jsonl.py: the bounded read-only JSONL reader reused unchanged.
- OHM-Trade-Agent-v1/AGENTS.md: principle 8 (durable evidence is never destructively mutated), principle 9 (offline analytics stays separate from production and shadow authority) and principle 21 (evidence reaches a traceable disposition instead of being silently dropped).
- OHM-Trade-Agent-v1/CLAUDE.md: engineering operating model (smallest change, preserve existing public contracts, focused tests) and trading safety boundary.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a data directory holding stamped outcome records and records with no usable strategy version stamp
WHEN: the Scorecard report command runs
THEN: its first printed line states the total number of records read and the number of those records that are unversioned, before any per-version row, and the per-version rows and their hit rates are unchanged

AC-002:
GIVEN: any input, including a data directory with stamped and unstamped records, a malformed evidence row, JSON output and a data directory that does not exist
WHEN: the Scorecard report command runs
THEN: no file or directory is created, changed or removed, and the on-disk state is identical before and after

EXPLICITLY OUT OF SCOPE:
- Any change to the scoring rule, hit rate, grouping, `UNVERSIONED` semantics, stream discovery, row selection or the pure core; the header is a count of what was already read.
- Rewriting, backfilling, re-stamping or deleting evidence so coverage improves, and any migration of the counted records.
- Dashboard, Operations Center, control-center page, scheduler, cron, worker, systemd timer, compose, Dockerfile, deploy script, workflow or service wiring.
- Adapters that translate a concrete evidence stream into the hit record shape; later slices own them.
- Changing, defining or loosening any gate, threshold, ranking, admission, risk, alert, paper or shadow behavior.
- Kraken order placement, modification, cancellation or confirmation of any kind, and any widening of production or Paper authority.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.

FROZEN BOUNDARIES:
- Advisory and read-only: coding automation has no trading authority, no exchange permissions and no mutation authority over data.
- The pure core stays the only scoring rule; the report only discovers, filters, counts and prints.
- The header never invents coverage: a record with no stamp is counted as unversioned, a malformed stamp is not coerced into a version, and an unparsed line is counted as invalid rather than as a record.
- The header total equals the sum of the per-file record counts the same run prints, so coverage is never a second, independently computed number.
- The previously printed report lines keep their current text; the header is additive and printed first.
- Deterministic gates keep failing closed; external AI stays advisory evidence and is not involved.
- Existing modules, tests, deploy paths and other increments' contracts keep their current behavior.
- No secret, credential or `.env` value is read, printed or written; the report prints only stream names, strategy versions and counts.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_scorecard_report_coverage_v1.py::test_report_first_line_reports_total_and_unversioned_records
AC-002 -> tests/test_v6_scorecard_report_coverage_v1.py::test_report_coverage_header_never_writes_changes_or_removes_a_file

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/scorecard_report.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_scorecard_report_coverage_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-COVERAGE-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/analytics/scorecard_report.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_scorecard_report_coverage_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-COVERAGE-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- The header counts a record whose stamp is a non-string value as malformed rather than unversioned, because the report already reports that row as unscored; whether malformed stamps deserve their own coverage bucket is a separate contract.
- Coverage is reported for the whole data root in one line. A per-plane or per-window coverage breakdown, and the same coverage line for archive segments and dead-letter streams, needs its own slice because those streams are still unread.
- The report still reads each live JSONL plane in full, so the header states coverage of hot evidence only; a bounded window or a projection remains a later slice.
- Nothing consumes the coverage line automatically: it is human-readable output, and no dashboard, alert or gate reads it.

UNAPPROVED SCOPE CHANGES:
NONE
