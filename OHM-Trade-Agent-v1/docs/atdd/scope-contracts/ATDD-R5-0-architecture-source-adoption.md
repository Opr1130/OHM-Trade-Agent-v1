INCREMENT:
ATDD-R5-0-architecture-source-adoption

OWNER-APPROVED INTENT:
Adopt the owner-supplied architecture sources v1.4.4 and v1.5.0, and re-baseline the repository truth documents onto them, as documentation and governance only.

The OWNER supplied `OPIP_Profit_Intelligence_Architecture_v1_4_4.docx` (4 October 2026) and `OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (9 October 2026), together with the v1.5.0 feature-priority tracker (`OPIP_Feature_Priorities_v1_5_0.md`, `.csv`). This increment copies those files into the repository byte-for-byte, pins each SHA256 and size in a `SOURCE.md` beside it, adds a paragraph extraction of each DOCX, and records the current architecture source and adoption baseline in `CURRENT_ARCHITECTURE_STATUS.md`, `OPIP_CONFORMANCE_LEDGER.md` and `OPIP_RECOVERY_ROADMAP.md`. It corrects only discrepancies that were verified against the repository at the adoption revision: the F1/F2/F3 rows that still claimed the Feature Bus was "pinned off" although the core-service `OPIP_FEATURE_BUS_MODE` literal is `shadow`, the description of the target spine as a recorded no-op although `run_cycle` invokes it in `shadow` every cycle, the stale current-authority statement and current-increment statement, and the R5 milestone naming that the adopted body itself settles in its section 14.

This increment grants no authority. It activates nothing, changes no runtime, risk, execution, deployment, release-profile or trading control, approves no v1.5.0 delivery increment, and leaves every owner assignment and numeric freeze in the adopted body open. It is a bounded slice of the adopted R5-0 contract item (pin sources and clause map, and record the adoption) and explicitly not the whole of it.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (adopted authority, SHA256 `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`) with `ARCHITECTURE.md` and `SOURCE.md`. Section 1 preserves the inherited contracts; section 14 defines the delivery sequence and the `R5-LEGACY-OUTCOMES-COCKPIT` retention rule; section 19 registers its sources, including the observation that at repository commit `8b3cc271` the status file still pinned v1.4.3.
- `docs/architecture/v1.4.4/OPIP_Profit_Intelligence_Architecture_v1_4_4.docx` (adopted amendment, SHA256 `9eb48784b540611e2eb538866365d506b1f0e07207ef3ea33502b796f8057768`) with `ARCHITECTURE.md` and `SOURCE.md`. Its section 1 preserves v1.4.3 history and its Appendix C is the source register and adoption record.
- `docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.md` and `.csv` (owner priority tracker; owners `UNASSIGNED`, non-architecture dimensions `NOT_ASSESSED`, no authority conveyed).
- `docs/architecture/v1.4.3/SOURCE.md` and the v1.4.3 DOCX (`ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`): the retained prior authority, unchanged.
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md`, `CURRENT_ARCHITECTURE_STATUS.md`, `OPIP_RECOVERY_ROADMAP.md`: the reconciled truth documents this increment re-baselines.
- `docker-compose.yml` core-service literals as the repository-controlled production posture: `OPIP_FEATURE_BUS_MODE=shadow`, `OPIP_CANONICAL_WRITER_MODE=shadow`, `OPIP_TARGET_SPINE_MODE=shadow`, `OPIP_PAPER_V2_MODE=off`, `OPIP_COMMITTEE_MODE=off` under `EVIDENCE_SHADOW` (`docs/release/README.md`, `ATDD-RELEASE-PIPELINE-v1`).
- `app/jobs/run_cycle.py` (`TARGET_SPINE` phase) and `app/services/target_spine_cycle.py` (`TARGET_SPINE_MODES`, `REASON_NO_SNAPSHOT_SOURCE`): the read-only runtime facts behind the corrected target-spine wording. No runtime file is changed.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable, so this increment's acceptance tests prove their own identity and never pin the pointer.
- `AGENTS.md` release-profile contract: `EVIDENCE_SHADOW` stays shadow for the feature bus, canonical writer and target spine; Paper-v2, Committee and funded authority stay off.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the owner-supplied v1.4.4 amendment
WHEN:
the repository copy and its pin are inspected
THEN:
the copy is byte-identical to the owner file, its SHA256 and size are recorded in `docs/architecture/v1.4.4/SOURCE.md`, and a paragraph extraction exists beside it

AC-002:
GIVEN:
the owner-supplied v1.5.0 body and its feature-priority tracker
WHEN:
the repository copies and their pin are inspected
THEN:
every copy is byte-identical to its owner file with its SHA256 recorded in `docs/architecture/v1.5.0/SOURCE.md`, the two owner tracker text files keep those owner bytes because `docs/architecture/v1.5.0/.gitattributes` marks them non-text so no add or checkout normalises their line endings, and the tracker is stored beside the v1.5.0 package as planning input that conveys no authority

AC-003:
GIVEN:
the two adopted paragraph extractions
WHEN:
they are read
THEN:
each carries its own version and date identity and the section markers of its source, and both state that the DOCX is the architecture authority

AC-004:
GIVEN:
the baselined v1.4.3 package
WHEN:
the adoption is applied
THEN:
its DOCX bytes still hash to the recorded value and the adopted packages were added beside it rather than replacing it

AC-005:
GIVEN:
the reconciled truth documents
WHEN:
the adoption is recorded
THEN:
the ledger, the status document and the roadmap name the adopted sources and the adoption baseline, while the earlier baselines, reconciliation dates and superseded statements remain identifiable as history

AC-006:
GIVEN:
the Feature Bus rows of the ledger and the status document
WHEN:
they are read
THEN:
they record the owner-authorized `shadow` posture with `run_cycle` still not calling the bus, and they retain the earlier `OPIP_FEATURE_BUS_MODE=off` record as history

AC-007:
GIVEN:
the target-spine description in the truth documents
WHEN:
it is compared with the runtime it describes
THEN:
it states that the spine is invoked on every unified cycle in `shadow` with no snapshot source and therefore records a non-authoritative no-op, that this is distinct from a disabled gate, and the runtime still admits only the `off` and `shadow` modes

AC-008:
GIVEN:
the R5 milestone naming in the adopted section 14
WHEN:
the roadmap R5 sections are read
THEN:
the earlier outcome-consolidation and cockpit milestone is preserved under `R5-LEGACY-OUTCOMES-COCKPIT`, and the new R5 program is recorded as unapproved direction rather than as an authorized increment

AC-009:
GIVEN:
this increment's scope contract
WHEN:
its implementation map is compared with the adoption inventory
THEN:
every adopted, corrected and pointer path is mapped to an approved criterion, the map contains only documentation and test paths, and `UNAPPROVED SCOPE CHANGES` is exactly `NONE`

AC-010:
GIVEN:
the adoption change set and the repository-controlled production posture
WHEN:
authority is assessed
THEN:
the core-service mode literals are unchanged, the adoption's own authority statements are all `NO`, and no runtime, deployment, workflow or configuration path is part of the change

EXPLICITLY OUT OF SCOPE:
- Any runtime, scanner, detector, forecast, selector, execution, risk, protection, strategy or trading change; `app/**` is untouched
- Any deployment, production configuration, `.github/**` workflow, release-profile, compose, deploy-script or ruleset change
- Naming, approving or freezing R5-0's owners, or the record, horizon, accounting and performance-budget freezes the adopted section 14 lists
- Approving R5-A Market Eye, R5-B Horizons, R5-C Capital, R5-D portfolio and rotation, R5-E autonomous paper, R5-F learning, F9/F10 work, `TARGET_PAPER`, cutover or any activation
- Changing any status value, blocker or evidence claim beyond the verified discrepancies listed in `OWNER-APPROVED INTENT`
- Editing the adopted DOCX bytes, the v1.4.3 DOCX, `OPIP_RETIREMENT_LEDGER.md`, the dated runtime-truth observations, `OPIP_F6_OWNER_ENABLEMENT_PACKETS.md`, `docs/architecture/v1.2/**` or any historical scope contract
- Removing or weakening any historical ATDD evidence, historical contract statement or historical test
- The code observation that Paper-v2 fail-open protection is invoked twice in one cycle; it is a runtime matter for a separately scoped increment and is not changed here

FROZEN BOUNDARIES:
- No funded, exchange, order, Committee, Telegram, Paper-v2 or cutover authority is created, widened or transferred; `PAPER V2 ACTIVATED BY THIS ADOPTION = NO`.
- The v1.4.3 DOCX and the v1.4.4 and v1.5.0 DOCX copies are byte-exact and are never edited; the DOCX controls over any extraction.
- The core-service compose literals remain the sole activation authority for the evidence plane, and this increment does not change them.
- Historical ATDD evidence and every historical contract statement remain intact; a superseded statement is preserved as quoted history, never erased.
- `tests/atdd_scope.py`, its parser, exit codes and fail-closed behaviour are unchanged.
- The active-increment pointer is movable; this increment's acceptance tests prove their own identity and must not pin it.
- Adoption alone approves nothing: publishing or adopting an architecture source authorizes no implementation and no activation.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_001_v1_4_4_source_bytes_and_hash_are_adopted
AC-002 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_002_v1_5_0_source_and_trackers_are_adopted
AC-003 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_003_extracted_text_carries_the_source_identity
AC-004 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_004_v1_4_3_baseline_is_retained_not_replaced
AC-005 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_005_truth_documents_record_the_adoption_and_keep_history
AC-006 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_006_feature_bus_shadow_posture_is_recorded_with_its_history
AC-007 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_007_target_spine_is_invoked_shadow_not_a_disabled_gate
AC-008 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_008_legacy_r5_milestone_is_preserved_under_its_alias
AC-009 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_009_adoption_inventory_is_fully_mapped_and_documentary
AC-010 -> tests/test_opip_r5_0_architecture_source_adoption.py::test_ac_010_adoption_grants_no_runtime_or_trading_authority

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.4/OPIP_Profit_Intelligence_Architecture_v1_4_4.docx
AC-001 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.4/ARCHITECTURE.md
AC-001 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.4/SOURCE.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/ARCHITECTURE.md
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/SOURCE.md
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.md
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.csv
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/.gitattributes
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.4/ARCHITECTURE.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.4/SOURCE.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/ARCHITECTURE.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/SOURCE.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-004 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.3/ARCHITECTURE.md
AC-004 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.3/SOURCE.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-005 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-005 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md
AC-005 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_RECOVERY_ROADMAP.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-006 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-006 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-007 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-007 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-008 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-008 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_RECOVERY_ROADMAP.md
AC-008 -> OHM-Trade-Agent-v1/docs/architecture/v1.5.0/ARCHITECTURE.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/README.md
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-0-architecture-source-adoption.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py
AC-010 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py

DEFERRED DISCOVERIES:
- R5-0 is only partly satisfied. The adopted section 14 also requires naming owners and freezing records, horizon policy, accounting, performance budgets and the remaining acceptance scope. Those owner assignments and numeric freezes stay open and are not approved here; the v1.5.0 tracker still marks owners `UNASSIGNED`.
- The v1.4.4 Appendix C adoption record asks for accountable business and technical owners and for unresolved numeric acceptance decisions to be completed. This increment records identity, hash, size and repository path only; the ownership and numeric fields remain open.
- The v1.4.4 and v1.5.0 extractions concatenate a line break inside a paragraph without a separator, so a title split across two lines reads as one word. The committed v1.4.3 extraction uses the same rule and was reproduced byte-for-byte by the same method; changing the rendering rule for a shared artefact is a separate, separately approved decision.
- Paper-v2 fail-open protection is invoked twice in one cycle in `app/jobs/run_cycle.py`. That is a runtime observation recorded here, not a documentation error and not a defect claim: it is not changed, and it needs a separately scoped increment with its own criteria before any edit.
- Whether the two bounded Feature Bus capture cron entries are currently installed on the production host is not observed by this increment. The documents therefore state the repository-controlled posture and the deploy-time verification path, and claim no capture activity.
- The adopted section 14 still requires an assessment of the existing EVIDENCE_SHADOW alert-quality evidence against its frozen contract, and section 18 lists readiness-gate, migration and rollback decisions. None of that is assessed here.
- The v1.5.0 feature-priority tracker's non-architecture dimensions are `NOT_ASSESSED` and its owners are `UNASSIGNED`; the adopted copy is stored as source evidence only and changes no status value.

UNAPPROVED SCOPE CHANGES:
NONE


