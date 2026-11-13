INCREMENT:
ATDD-OPIP-V6-SCORECARD-REPORT-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. This v6
slice adds one read-only command that prints the Scorecard - the hit rate and
the count per strategy version - from the stamped qualification records and
outcome records already sitting in a data directory. It is a reader around the
approved pure core (`app/opip/analytics/scorecard.py::score_outcomes`): the tool
discovers the existing qualification and outcome JSONL streams, selects the rows
that carry both the version stamp (v6 slices A and D) and an explicit boolean
hit outcome, and hands them to the core. It adds no scoring rule of its own, no
UI, no store, no scheduler and no clock. It never writes, creates, renames,
truncates or deletes a file, and it never guesses an outcome: a row without a
boolean hit is reported as unscored instead of counted as a miss, and a row
whose stamp is not a string is reported as unscored instead of being attributed
to a version. Nothing in the scanner, qualification, ranking, alert, paper or
trading path is wired to it, and no gate, threshold or execution safeguard
changes.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 1 (the Scorecard makes v6 measurable), section 5 slice E (the Scorecard core this command reads), section 4 (additive first: new pure modules and read-only tools before any wiring), and section 7 (safety boundary).
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-CORE-v1.md: the approved pure core, its record contract (`strategy_version` plus a boolean `hit`) and its DEFERRED DISCOVERIES naming a read-only report and the evidence adapters as later increments.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-QUALIFICATION-STAMP-v1.md: the approved additive stamp that labels qualification records with the version block.
- OHM-Trade-Agent-v1/app/opip/analytics/scorecard.py: `score_outcomes`, `VersionScore`, `HIT_FIELD`, `VERSION_FIELD` and `UNVERSIONED`, reused unchanged.
- OHM-Trade-Agent-v1/app/opip/decision/store.py: the qualification plane (`opip/qualification/*.jsonl`) and its dead-letter stream.
- OHM-Trade-Agent-v1/app/opip/discovery/store.py and OHM-Trade-Agent-v1/app/jobs/build_phase3c_forward_outcomes.py: the outcome streams the report reads (`opip/discovery/*.jsonl` and `*_outcomes.jsonl` in the data root).
- OHM-Trade-Agent-v1/app/opip/storage/bounded_jsonl.py: `iter_lines` and `parse_json_object_line`, the bounded read-only JSONL reader reused unchanged.
- OHM-Trade-Agent-v1/app/opip/data_platform/retention_dry_run.py: the approved read-only report shape this command follows (explicit root, text plus JSON output, 64 on a missing root).
- OHM-Trade-Agent-v1/AGENTS.md: principle 8 (durable evidence is never destructively mutated), principle 9 (offline analytics stays separate from production and shadow authority) and principle 21 (evidence reaches a traceable disposition instead of being silently dropped).

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a data directory holding stamped qualification records and outcome records that state an explicit boolean hit outcome
WHEN: the Scorecard report command runs against that directory
THEN: it prints, for each strategy version found, the hit rate and the number of scored records for that version, and it reports per evidence file how many rows were scored, unscored or invalid

AC-002:
GIVEN: any input, including a data directory with records to score, a malformed evidence row, JSON output and a data directory that does not exist
WHEN: the Scorecard report command runs
THEN: no file or directory is created, changed or removed, and the on-disk state is identical before and after


EXPLICITLY OUT OF SCOPE:
- Dashboard, console page, Control Center endpoint, HTML, chart or any other UI.
- Scheduler, cron, worker, systemd timer, compose, Dockerfile, deploy script, workflow or service wiring; the command is invoked manually only.
- Adapters that translate a concrete evidence stream (screening ADVANCED/REJECTED rows, discovery barrier labels, paper/Freqtrade results) into the hit record shape; later slices own them.
- Reading or writing the canonical SQLite store, the analytics projection, archive segments, dead-letter streams, the network or any database.
- Deleting, moving, renaming, compressing, truncating or rewriting any data file, and any `--apply` or force flag.
- Changing, defining or loosening any gate, threshold, ranking, admission, risk, alert or paper behavior.
- Backfilling, rewriting, migrating or re-stamping existing evidence.
- Kraken order placement, modification, cancellation or confirmation of any kind.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.

FROZEN BOUNDARIES:
- Advisory and read-only: coding automation has no trading authority, no exchange permissions and no mutation authority over data.
- Deterministic gates keep failing closed; external AI stays advisory evidence and is not involved.
- The pure core stays the only scoring rule; the command only discovers, filters and prints.
- An unscorable record is reported as unscored, never counted as a miss and never silently dropped.
- Evidence is never attributed to a version stamp it does not carry, and a non-string stamp is never coerced.
- Existing modules, tests, deploy paths and other increments' contracts keep their current behavior.
- No secret, credential or `.env` value is read, printed or written; the report prints only stream names, strategy versions and counts.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_scorecard_report_v1.py::test_report_prints_hit_rate_and_count_per_strategy_version
AC-002 -> tests/test_v6_scorecard_report_v1.py::test_report_never_writes_changes_or_removes_a_file

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/scorecard_report.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_scorecard_report_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-REPORT-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/analytics/scorecard_report.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_scorecard_report_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-REPORT-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- No existing evidence stream writes a boolean hit outcome today: screening rows record ADVANCED/BELOW_THRESHOLD, discovery rows record barrier labels and paper outcomes record realised P/L. On today's data the report therefore prints the per-file record counts and the unscored total, and states that no scored outcome records were found, instead of inventing a rate.
- Paper outcome rows that write `UNKNOWN` when a strategy version was not captured form their own version group rather than joining `UNVERSIONED`; merging the two stays an adapter policy decision.
- The command reads each live JSONL plane in full, so on the production host it walks the whole qualification stream (442 MB at the last audit) only to report those rows as unscored. A bounded window, a projection or a cache is a later slice.
- Archive segments, dead-letter streams and the canonical SQLite evidence are not read; only the live JSONL planes are, so the report describes hot evidence only.
- Freqtrade and internal shadow results as Scorecard sources remain later slices and must keep surviving paper evidence labelled shadow-only.

UNAPPROVED SCOPE CHANGES:
NONE
