INCREMENT:
ATDD-OPIP-V6-SCORECARD-CORE-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. V6 slice E
adds one pure module that computes the hit rate and count per strategy version
from outcome records that already carry the version stamp (v6 slices A and D).
It is the measurement core only: no UI, no console page, no dashboard, no
scheduler, no store, no file and no clock. A record with no stamp is grouped as
unversioned evidence instead of being dropped or back-filled, so the Scorecard
counts what was recorded and attributes nothing it cannot prove. Nothing in the
scanner, qualification, ranking, alert, paper or trading path is wired to it,
and no gate, threshold or execution safeguard changes.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 5 slice E (Scorecard core: pure hit-rate per strategy version from outcome records), its slice A and D dependencies, section 4 (additive first: pure modules before any wiring), and section 7 (safety boundary).
- OHM-Trade-Agent-v1/docs/V6_AUDIT.md: section 7 (versioning and the Scorecard gap) and the audit use of the Scorecard to compare strategies and models.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-STAMP-v1.md and OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-QUALIFICATION-STAMP-v1.md: the approved version stamp and its DEFERRED DISCOVERIES entry naming this increment.
- OHM-Trade-Agent-v1/app/opip/decision/versioning.py: `STRATEGY_VERSION` and `version_stamp()`, the only version source, reused unchanged.
- OHM-Trade-Agent-v1/app/opip/analytics/__init__.py: the analytics package boundary; its projection never owns trading, ranking, alert or admission authority.
- OHM-Trade-Agent-v1/AGENTS.md: principle 9 (offline analytics stays separate from production and shadow authority) and principle 21 (counted evidence is not silently dropped).

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: scored outcome records that carry the version stamp and an explicit boolean hit outcome
WHEN: the Scorecard core scores those records
THEN: it returns, per strategy version, the hit rate and the number of scored records for exactly that version, and the input records are left unchanged

AC-002:
GIVEN: outcome records whose strategy version stamp is absent, null or blank, mixed with stamped records
WHEN: the Scorecard core scores them
THEN: the unstamped records are grouped together as unversioned with their own hit rate and count, while stamped records remain in their own per-version groups

EXPLICITLY OUT OF SCOPE:
- Dashboard, console page, Control Center endpoint, HTML, chart or any other UI.
- Scheduler, cron, worker, compose, Dockerfile, deploy script, workflow or service wiring; the core is invoked by a caller only.
- Reading files, streams, databases, canonical evidence, the clock or the network; records are passed in by the caller.
- Mapping a concrete evidence stream (screening evaluations, discovery forward outcomes, paper/Freqtrade results) into the scorecard record shape; later slices own the adapters.
- Changing, defining or loosening any gate, threshold, ranking, admission, risk, alert or paper behavior.
- Backfilling, rewriting, migrating or re-stamping existing evidence.
- Kraken order placement, modification, cancellation or confirmation of any kind.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.

FROZEN BOUNDARIES:
- Advisory and read-only: coding automation has no trading authority and no exchange permissions.
- Deterministic gates keep failing closed; external AI stays advisory evidence and is not involved.
- The core is pure: no I/O, no clock, no shared state, no mutation of the records it was handed.
- An unscorable record is refused, never counted as a miss and never silently dropped.
- Unstamped evidence is never attributed to the live strategy version.
- Existing modules, tests, deploy paths and other increments' contracts keep their current behavior.
- No secret, credential or `.env` value is read, printed or written.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_scorecard_core_v1.py::test_hit_rate_and_count_are_reported_per_strategy_version
AC-002 -> tests/test_v6_scorecard_core_v1.py::test_records_without_a_stamp_are_grouped_as_unversioned

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/scorecard.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_scorecard_core_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-CORE-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/analytics/scorecard.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_scorecard_core_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SCORECARD-CORE-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- The scorecard input contract is explicit (`strategy_version` plus a boolean `hit`) because no approved evidence stream states a hit outcome today: screening rows record ADVANCED/BELOW_THRESHOLD, discovery rows record a barrier label, and paper outcomes record realised P/L. Adapters that translate one of those streams into this record shape need their own contract before any real evidence is scored.
- Paper outcome records write `UNKNOWN` when a strategy version was not captured. That is a recorded stamp, so it currently forms its own version group rather than joining `UNVERSIONED`; merging the two is a policy decision for the adapter slice, not this core.
- Grouping by the full version stamp (model version and gate policy fingerprint) and per-horizon or per-direction breakdowns are later Scorecard increments; this slice groups by strategy version only.
- Freqtrade and internal shadow results as Scorecard sources are later slices and must keep surviving paper evidence labelled shadow-only.

UNAPPROVED SCOPE CHANGES:
NONE
