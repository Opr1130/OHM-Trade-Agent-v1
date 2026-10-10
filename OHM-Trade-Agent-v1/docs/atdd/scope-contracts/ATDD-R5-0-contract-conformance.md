INCREMENT:
ATDD-R5-0-contract-conformance

OWNER-APPROVED INTENT:
Complete the R5-0 contract-freeze slice of the adopted v1.5.0 architecture as a conformance register, a frozen structural-contract specification and their acceptance tests, with no runtime, trading, deployment, release-profile or configuration change.

The adopted body (`OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, 9 October 2026) states the R5 delivery direction and, in its section 14, keeps the sequence `R4 evidence closure → R5 contract freeze → R5-A Continuous Market Eye → R5-B Horizon Intelligence → R5-C Capital Intelligence → R5-D Portfolio Planning and Rotation → R5-E Autonomous Paper Portfolio Management → R5-F Horizon Learning and Strategy Evaluation → separate TARGET_PAPER readiness decision`. Its section 19 registers open inputs instead of settling them. The predecessor increment `ATDD-R5-0-architecture-source-adoption` adopted the sources and recorded, in its own deferred discoveries, that R5-0 remained only partly satisfied.

This increment performs the determinable part of R5-0. It audits the adopted architecture requirement by requirement, records one conformance row per requirement with source, status, evidence, authority, dependency and gate, freezes the structural contracts that the adopted body already determines, and records every numeric or ownership input that the adopted body leaves to the OWNER as an open decision rather than inventing a value. It creates no second architecture, no second register and no new runtime path; it reuses the adopted clause identifiers unchanged.

This increment grants no authority. It activates nothing, changes no runtime, risk, execution, deployment, release-profile or trading control, approves no R5 delivery increment, and freezes no numeric policy. Structural freezing means naming the record, grain, keys, identity and state values that the adopted clauses already name, and marking as `OWNER_DECISION_REQUIRED` everything the adopted body leaves open.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (adopted authority, SHA256 `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`) with its extraction `docs/architecture/v1.5.0/ARCHITECTURE.md` and identity pin `docs/architecture/v1.5.0/SOURCE.md`. Section 3 (Continuous Market Eye, ME-01–ME-07), section 4 (Horizon Intelligence, HZ-01–HZ-02), section 5 (Opportunity and position lifecycles, LC-01–LC-03), section 6 (Capital Intelligence and accounting, CI-01–CI-04), section 7 (Portfolio planning and selection, PP-01–PP-04), section 8 (Capital rotation, RO-01–RO-04), section 9 (Autonomous paper portfolio management, PM-01–PM-02), section 10 (Canonical records and temporal contracts, DC-01–DC-03), section 11 (Expected versus actual learning, LE-01–LE-03), section 12 (Economics and capital time metrics), section 13 (Cockpit alerts and inherited precision), section 14 (Delivery sequence and roadmap continuity), section 15 (Existing feature change map), section 16 (New capabilities and work to revisit), section 17 (Acceptance scenarios for R5), section 18 (Readiness gates, migration controls MG-01–MG-04) and section 19 (Sources clause mapping and open inputs).
- `docs/architecture/v1.4.4/ARCHITECTURE.md` Appendix A (precision agent acceptance contract, PA-01–PA-20) and Appendix B (signal quality measurement contract, SQ-01–SQ-10): the inherited clause registers that v1.5.0 section 13 declares remain applicable.
- `docs/architecture/v1.4.4/OPIP_Profit_Intelligence_Architecture_v1_4_4.docx` (SHA256 `9eb48784b540611e2eb538866365d506b1f0e07207ef3ea33502b796f8057768`) and `docs/architecture/v1.4.3/ARCHITECTURE.md`: retained prior authorities, unchanged.
- `docs/architecture/CURRENT_ARCHITECTURE_STATUS.md`, `docs/architecture/OPIP_CONFORMANCE_LEDGER.md`, `docs/architecture/OPIP_RECOVERY_ROADMAP.md`, `docs/architecture/OPIP_RETIREMENT_LEDGER.md`: the reconciled truth documents this register cites and does not rewrite.
- `docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.md` and `.csv`: owner priorities with owners `UNASSIGNED` and non-architecture dimensions `NOT_ASSESSED`; planning input only, no authority.
- `docs/atdd/ACTIVE_INCREMENT` and `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable, so this increment proves its identity from its own contract and never pins the global pointer.
- `app/services/release_profiles.py`, `docker-compose.yml` and `docs/release/README.md`: the release-profile contract and the repository-controlled production posture (`SAFE_BASELINE`, `EVIDENCE_SHADOW`, `TARGET_PAPER` blocked). Read-only references; no such file is changed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
The R5-0 conformance register contains one row for every adopted requirement and keeps a uniform record shape.
GIVEN: the adopted v1.5.0 body with its inherited v1.4.4 appendices defines the complete R5 obligation set.
WHEN: `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` is read.
THEN: it holds one row for each F1-F12 feature row, each of PA-01-PA-20 and SQ-01-SQ-10, the R5-0 contract item, the R5-A to R5-F delivery increments, R6, R7 and the G1-G6 readiness gates, and every row carries exactly the thirteen declared fields in the declared order.

AC-002:
Every requirement row is traceable to a frozen clause of the adopted sources.
GIVEN: the adopted sources are the v1.5.0 DOCX with its extraction and the retained v1.4.4 appendices.
WHEN: the register's source citations are compared with those sources.
THEN: every row cites the pinned v1.5.0 SHA256, a v1.5.0 section number that exists in `ARCHITECTURE.md`, or the v1.4.4 Appendix A or Appendix B clause register, and no row cites a source path that does not exist.

AC-003:
Inherited contracts are preserved, not renumbered or renamed.
GIVEN: v1.5.0 section 13 states that PA-01-PA-20 and SQ-01-SQ-10 remain applicable.
WHEN: the register's inherited rows are compared with the v1.4.4 clauses.
THEN: PA-01-PA-20 and SQ-01-SQ-10 are recorded as inherited and still applicable, their v1.4.4 clause titles are unchanged, and no other requirement row reuses an inherited identifier for a different meaning.

AC-004:
Implementation status is a closed vocabulary and carries resolvable evidence.
GIVEN: a requirement row records an implementation status and evidence references.
WHEN: the register rows are validated.
THEN: every status is one of the declared status values, every evidence reference other than `NONE` resolves to a file present in the repository, and every row that claims verified implementation names at least one repository evidence path.

AC-005:
Shadow and live authority stay separated in every row.
GIVEN: this increment grants no authority and the repository-controlled production posture is the shadow profile.
WHEN: the register's authority column is validated.
THEN: every row declares an authority value from the closed vocabulary, no row declares live, funded or exchange authority, and no row claims that the adopted architecture authorizes funded execution.

AC-006:
Each logical record has exactly one owning module, reusing existing modules.
GIVEN: v1.5.0 section 10 forbids a new event store, allocator database, outcome engine or position registry.
WHEN: the frozen owner table is validated.
THEN: every logical record names exactly one owning module that exists in the repository, no module owns two records, and every record declares its reuse status against the existing module family.

AC-007:
Requirement dependencies are declared, resolvable and acyclic, and match the adopted delivery sequence.
GIVEN: the adopted body fixes the R5 delivery order in its section 14.
WHEN: the register's dependency column is read as a graph.
THEN: every dependency names a declared requirement identifier, the graph contains no cycle, and the order R5-A, R5-B, R5-C, R5-D, R5-E, R5-F is consistent with the dependencies declared for those increments.

AC-008:
Frozen structural contracts derive from the adopted clauses rather than from invention.
GIVEN: the adopted clauses name the keys of their logical records.
WHEN: `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` is compared with those clauses.
THEN: every structural contract lists the keys named by its source clause for the attention trigger, thesis and horizon record, capital snapshot, portfolio plan, rotation group and canonical temporal record, and the freeze declares each contract frozen or open.

AC-009:
Numeric policy that the adopted body leaves open is recorded as an open owner decision.
GIVEN: the adopted body defers numeric policy such as rotation advantage, cooldown, turnover budget, workload and latency bounds, sleeve caps and readiness thresholds to the OWNER.
WHEN: the register and the freeze are read.
THEN: every such numeric item is a register row with status `OWNER_DECISION_REQUIRED`, and the freeze states that no such numeric policy is ratified by this increment.

AC-010:
This increment promotes no runtime, trading or deployment authority.
GIVEN: this increment is documentation, registry and test work only.
WHEN: the increment's own implementation map and changed-file set are inspected.
THEN: no mapped path is application runtime, deployment, workflow or Compose configuration, and the register and the freeze both state that zero trading and deployment authority is granted.

AC-011:
The increment scope is self-consistent and its mapped paths exist.
GIVEN: the ATDD checker authorizes changed files from this increment's implementation map.
WHEN: this scope contract is parsed and its mapped paths are resolved.
THEN: the increment identifier is a single token, all ten required sections appear in the required order, `UNAPPROVED SCOPE CHANGES` is exactly `NONE`, every criterion declares GIVEN, WHEN and THEN, and every implementation-map path exists in the repository.

AC-012:
Adopted release architecture safeguards do not regress.
GIVEN: the release-profile contract is exact and allowlisted, and `TARGET_PAPER` is blocked.
WHEN: the release-profile contract and the repository-controlled production posture are evaluated.
THEN: `TARGET_PAPER` is still declared `BLOCKED` and refuses to render, `EVIDENCE_SHADOW` still resolves to shadow and off modes only, and the Compose core-service literals still show `OPIP_PAPER_V2_MODE=off`.

EXPLICITLY OUT OF SCOPE:
- Any application runtime change under `OHM-Trade-Agent-v1/app/`, including new modules, new state machines, new stores, new allocators and any edit to the existing early, events, streaming, market, features, discovery, learning, risk, capital, rotation, paper or cockpit modules.
- Any change to risk sizing, minimum reward-to-risk, protection, admission, execution or position-verification behaviour, and any change to release profiles, Compose literals, deployment workflows, remote deploy scripts or the learning-worker contract.
- Any activation of `TARGET_PAPER`, any funded or live execution authority, any exchange order authority and any promotion of shadow or offline learning evidence.
- Approving the R5-A to R5-F implementation increments, selecting owners, ratifying numeric thresholds, or promising that any R5 capability is delivered by this increment.
- Rewriting the adopted architecture or its extraction, re-baselining the truth documents beyond the citations this register adds, and renumbering the inherited PA or SQ clauses.
- Building a second requirement register, a second architecture summary or a competing conformance ledger.

FROZEN BOUNDARIES:
- Documentation, registry and test artifacts only, plus the ATDD increment pointer and this contract.
- The adopted authority remains `OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` with SHA256 `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`; no adopted byte is edited and no pin changes.
- Requirement identifiers, clause identifiers and inherited PA and SQ identifiers keep their adopted values and meanings.
- Structural contracts freeze only what the adopted clauses determine; every undetermined value stays `OWNER_DECISION_REQUIRED` and no numeric default is invented.
- The production posture stays `EVIDENCE_SHADOW` with `TARGET_PAPER` blocked; this increment cannot widen authority, and the release-profile gate remains the authority for activation.
- Acceptance tests are read-only: they parse committed documents and the existing release-profile contract, and they never write runtime state, call an exchange, or read the active-increment pointer.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_001_requirement_inventory_is_complete
AC-002 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_002_clause_citations_resolve_to_adopted_sources
AC-003 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_003_inherited_clauses_are_preserved
AC-004 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_004_status_vocabulary_and_evidence_resolve
AC-005 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_005_authority_separation_holds
AC-006 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_006_single_owner_per_logical_record
AC-007 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_007_dependencies_are_resolvable_and_acyclic
AC-008 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_008_structural_contracts_derive_from_clauses
AC-009 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_009_open_numeric_policy_is_recorded
AC-010 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_010_no_authority_path_is_mapped
AC-011 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_011_scope_contract_is_self_consistent
AC-012 -> tests/test_opip_r5_0_contract_conformance.py::test_ac_012_release_profile_safeguards_hold

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-004 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-005 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-006 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-006 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-007 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-008 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-009 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-009 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-0-contract-conformance.md
AC-010 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md
AC-010 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-0-contract-conformance.md
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py
AC-012 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md
AC-012 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r5_0_contract_conformance.py

DEFERRED DISCOVERIES:
- Owner assignment is unresolved. The v1.5.0 feature-priority tracker still marks owners `UNASSIGNED`; this increment assigns no owner and the register records `UNASSIGNED` wherever the adopted body names none.
- Numeric policy is unresolved. Rotation minimum economic advantage, cooldown and turnover budget (RO-02), Market Eye workload, latency, freshness and queue limits (ME-05), sleeve caps and whether they are hard limits or soft preferences (PP-03), horizon boundary durations (section 4) and every readiness-gate threshold (section 18) remain open owner decisions.
- The R4 evidence-closure increment is a precondition of the R5 sequence. This increment records its status without performing or approving it.
- The horizon taxonomy has no repository enum yet. `app/opip/contracts/forecast.py` holds a forecast-horizon identity, not the tactical, swing and position intent classes of v1.5.0 section 4, so the horizon-class enum shape remains a design input for R5-B.
- The registry-side treatment of aggregate review-critical facts is not settled by the adopted clauses and is recorded as an open input rather than decided here.
- v1.5.0 section 17 acceptance scenarios and section 16 revisited capabilities include scale and capacity questions that documentation alone cannot answer and that need separately approved increments.
- The Windows-host Git Bash and shell-fixture failures observed while validating this increment are a pre-existing baseline condition; they are recorded as `BASELINE_ENVIRONMENT_FAILURE` and are not attributed to this increment.

UNAPPROVED SCOPE CHANGES:
NONE
