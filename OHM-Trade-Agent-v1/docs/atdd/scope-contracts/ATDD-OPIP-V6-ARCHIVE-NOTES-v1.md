INCREMENT:
ATDD-OPIP-V6-ARCHIVE-NOTES-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. V6 slice B
(retirement theme R1) moves the root-level design notes named by
`docs/V6_AUDIT.md` section 10 into `docs/archive/` and adds
`docs/archive/INDEX.md` so every moved note keeps a traceable disposition. The
notes are moved unchanged and grant no authority: they stay historical design
records. The README and the two safety contracts (`AI_EXECUTION_GATEWAY.md`,
`OHM_P1_PROMOTION_GATES.md`) stay at the root, and so does every note that
application code or a test reads by name (`AUTONOMOUS_BUILD_REPORT.md`,
`SIGNAL_QUALITY_PHASE3A.md`, `TRADEVISOR_V2.md`, `TRADINGVIEW_WAVE8_2.md`,
`WAVES_3_7_COMPLETION.md`, `WAVE_CHECKPOINTS.md`). No application module,
runtime behavior, threshold, schema, deploy path or trading authority changes;
the only edit outside `docs/` is one README link repointed to the archived
location of the note it cites.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 5 slice B (archive root design notes to `docs/archive/` with an index, audit R1) and section 7 (safety boundary).
- OHM-Trade-Agent-v1/docs/V6_AUDIT.md: section 10 (about 28 root design notes, keep the README, the safety contracts and anything code or tests cite) and the R1 row of section 11.
- OHM-Trade-Agent-v1/AGENTS.md: the existing-controls table that names `README.md`, `AI_EXECUTION_GATEWAY.md` and `OHM_P1_PROMOTION_GATES.md` as contracts to preserve.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the root design notes listed by this increment
WHEN: the increment is applied
THEN: each one exists under `docs/archive/`, is absent from the application root, and is listed in `docs/archive/INDEX.md`

AC-002:
GIVEN: application code and tests that cite root documents
WHEN: the full test suite runs after the move
THEN: no test fails because a cited document moved — every cited note still resolves, and no source cites an archived note at its old root path

EXPLICITLY OUT OF SCOPE:
- Any change to `app/`, runtime behavior, configuration, thresholds, evidence schemas or deploy files.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.
- Retiring or deleting any note; this increment only moves and indexes them.
- Rewriting the historical design content of the moved notes.
- Kraken order placement, modification, cancellation or confirmation of any kind.

FROZEN BOUNDARIES:
- Advisory-only: coding automation has no trading authority and no exchange permissions.
- Deterministic gates keep failing closed; external AI stays advisory evidence.
- The README, the safety contracts and every note that code or tests read stay at the application root.
- Archived notes are historical records, not a second architecture or policy source.
- Other increments' contracts, tests and behavior are unchanged.
- No secret, credential or `.env` value is read, printed or written.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_archive_root_notes_v1.py::test_archived_notes_live_under_docs_archive_and_are_indexed
AC-002 -> tests/test_v6_archive_root_notes_v1.py::test_no_cited_document_is_broken_by_the_move


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/archive/INDEX.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OHM_P1_PHASE3C_IMPLEMENTATION.md
AC-001 -> OHM-Trade-Agent-v1/OHM_P1_PHASE3C_IMPLEMENTATION.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OHM_P1_TARGET_ARCHITECTURE.md
AC-001 -> OHM-Trade-Agent-v1/OHM_P1_TARGET_ARCHITECTURE.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_BUILD0_VERIFICATION.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_BUILD0_VERIFICATION.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_DECISION_ENGINE_5_2A.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_DECISION_ENGINE_5_2A.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_DECISION_ENGINE_V1.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_DECISION_ENGINE_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_DECISION_ENGINE_V2.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_DECISION_ENGINE_V2.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_EVENT_INTELLIGENCE_V1.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_EVENT_INTELLIGENCE_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_REALTIME_MARKET_INTELLIGENCE_4_2.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_REALTIME_MARKET_INTELLIGENCE_4_2.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_REALTIME_MARKET_INTELLIGENCE_4_3_4_5.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_REALTIME_MARKET_INTELLIGENCE_4_3_4_5.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_REALTIME_MARKET_INTELLIGENCE_V1.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_REALTIME_MARKET_INTELLIGENCE_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_SEQUENCE5_WAVE_A1.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_SEQUENCE5_WAVE_A1.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_SEQUENCE5_WAVE_A2.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_SEQUENCE5_WAVE_A2.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_SEQUENCE5_WAVE_A3.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_SEQUENCE5_WAVE_A3.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/OPIP_TWO_STAGE_ADVISORY_AI_V1.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/PRICE_MOVEMENT_RADAR.md
AC-001 -> OHM-Trade-Agent-v1/PRICE_MOVEMENT_RADAR.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/SIGNAL_QUALITY_PHASE2.md
AC-001 -> OHM-Trade-Agent-v1/SIGNAL_QUALITY_PHASE2.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/SIGNAL_QUALITY_PHASE3B.md
AC-001 -> OHM-Trade-Agent-v1/SIGNAL_QUALITY_PHASE3B.md
AC-001 -> OHM-Trade-Agent-v1/docs/archive/SIGNAL_QUALITY_V1.md
AC-001 -> OHM-Trade-Agent-v1/SIGNAL_QUALITY_V1.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_archive_root_notes_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-ARCHIVE-NOTES-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/README.md
AC-002 -> OHM-Trade-Agent-v1/docs/archive/PRICE_MOVEMENT_RADAR.md
AC-002 -> OHM-Trade-Agent-v1/PRICE_MOVEMENT_RADAR.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_archive_root_notes_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-ARCHIVE-NOTES-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- Live architecture documents (`docs/architecture/CURRENT_ARCHITECTURE_STATUS.md`, `docs/architecture/OPIP_RECOVERY_ROADMAP.md`) and older scope contracts and reviews still mention archived notes as plain code spans rather than links. Those mentions are historical text and no link breaks; repointing them is left to a later documentation pass rather than bundled here.
- `docs/V6_AUDIT.md` section 10 also lists 16 workflows to verify (`m07-cutover-toolkit.yml`, `deploy-learning-recovery.yml`); workflow review is outside this increment and outside the builder's allowed paths.
- Later work that needs to cite a moved note must use its `docs/archive/` path.

UNAPPROVED SCOPE CHANGES:
NONE
