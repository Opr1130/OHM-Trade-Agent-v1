INCREMENT:
ATDD-R5-F10-source-census

OWNER-APPROVED INTENT:
This is the F10-0 increment of the owner-approved F10 mission: the unified decision cockpit, semantic read service and decision-first Telegram presentation inside frozen architecture v1.5.0. The owner approved F10 functionality and implementation within those frozen boundaries, while retaining the eight inherited pages, extending existing semantic and data infrastructure, preserving the legacy outcomes cockpit until verified parity, reusing actual canonical facts with explicit missingness, and keeping all economics, admission, protection, risk and release authority unchanged.

The mission requires the baseline assessment before coding, an implementation matrix classifying every capability, and complete traceability as the exit condition of F10-0. This increment delivers exactly that: the verified source census, the capability classification with the owner's five-value vocabulary, the page and panel to fact contract, the required fact grains and semantic response fields, the declared evidence gaps and absences, and the unratified threshold policy. It records what exists, what is missing, what is blocked by upstream evidence and what only the owner may decide.

It implements nothing. No page, route, projection, metric, service, scheduler or store is added, changed or removed, no legacy surface is touched, and no trading, Telegram, admission, promotion, deployment, release-profile or production authority is created or widened. Its deliverable surface is four paths: the active-increment pointer, this contract, one census document under docs/architecture/, and one test module that keeps the census honest and self-consistent against the repository.

KEY LAW - A CENSUS IS NOT AN IMPLEMENTATION. Recording that a surface exists, or that a capability is missing, authorizes nothing. The census cannot close a gap, cannot declare a projection authority, and cannot flip a page's parity state.

KEY LAW - A NAMED SURFACE THAT DOES NOT EXIST IS A DEFECT. Every existing surface the census names is asserted to exist, and every declared absence is asserted to be absent, so the census fails closed if it is edited to describe a repository that is not there.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md section 15: the F10 feature row, disposition EXTEND, "same eight pages and semantic model; expose plans, capital and horizons", and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md: the frozen eight-page inventory, the exact parity predicate, the ten negative cases and the retention decision this census preserves without modifying.
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md F10 row: the four overlapping surfaces, the target authority of one semantic read service, the cutover gate of fixture counts-and-P&L reconciliation, the rename blocker and the "do not redesign the dashboard" note.
- docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md row F10 (contracts SC-08 and SC-12, open input OQ-10, gate G4).
- app/opip/contracts/paper_metrics.py: the versioned registry (paper-metrics-v1) that is the only metric-semantics authority.
- app/opip/profit_intelligence/semantics.py: the fact-availability vocabulary, the lineage stages, the registered evidence-gap facts and the metric-authority map.
- app/opip/cockpit/trust.py and app/opip/data_platform/freshness.py: the independent trust dimensions and the freshness vocabulary the census reuses instead of inventing new words.
- docs/atdd/README.md and tests/atdd_scope.py: the scope mechanism this contract is written against.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the F10-0 census document
WHEN: its identity, classification vocabulary, frozen page inventory and follower increments are inspected
THEN: it declares the increment, records the five permitted status values and no sixth, lists exactly the eight frozen pages in the adopted order, and names the follower increments without authorizing any of them

AC-002:
GIVEN: the census's source list, its per-row claims about existing surfaces and its declared absences
WHEN: every named existing surface is resolved and every declared absence is probed
THEN: each named surface exists, each declared absence is genuinely absent, and each matrix row that names an existing implementation or evidence path has all eleven required columns and a status drawn from the permitted vocabulary

AC-003:
GIVEN: the metric identifiers the census names
WHEN: they are resolved against the versioned metric registry
THEN: every identifier exists in the registry, the census names that registry as the only metric-semantics authority and declares that no formula is restated outside it

AC-004:
GIVEN: the evidence gaps the census declares
WHEN: each gap is compared with the registered gap facts
THEN: every declared gap is a registered gap, and the census states that missing evidence is never rendered as a favorable state

AC-005:
GIVEN: the threshold policy and parity state in the census
WHEN: both are inspected
THEN: the threshold policy requires an owner decision, no threshold is ratified, and every page's parity verdict remains NOT_VERIFIED

AC-006:
GIVEN: this increment's implementation map and the census's authority statements
WHEN: the mapped paths are inspected
THEN: the increment changes no application, workflow or deployment path, and the census claims no trading, Telegram, admission, promotion, deployment or release authority

EXPLICITLY OUT OF SCOPE:
- Any page, route, projection, metric, read model, service, job, scheduler, workflow or store, including the semantic read service the census prepares.
- Any per-page declaration of which existing projection owns a page fact; the owner decides it.
- Any change to app/, .github/, deploy/ or any existing test module.
- Any change, flip or relaxation of the parity state, negative cases, retention decision or threshold policy recorded by the F10 parity specification.
- Any legacy cockpit change, retirement or deletion.
- Any trading, paper, Committee, Telegram, notification-threshold, release-profile, promotion, admission, deployment or production change.

FROZEN BOUNDARIES:
- The eight pages are frozen exactly as the parity specification records them.
- The versioned metric registry remains the only metric-semantics authority; a second registry or a restated formula is forbidden.
- The canonical writer remains the only writer; the census names no second producer for an existing fact.
- The five-status vocabulary is closed; no other value may classify a capability.
- The independent trust dimensions, the freshness vocabulary and the USD/USDT separation are reused, never redefined.
- No numeric threshold, gate value, tolerance or policy bound is ratified here.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_source_census.py::test_ac_001_census_declares_the_frozen_inventory_and_contract
AC-002 -> tests/test_opip_r5_f10_source_census.py::test_ac_002_named_surfaces_exist_and_declared_absences_are_absent
AC-003 -> tests/test_opip_r5_f10_source_census.py::test_ac_003_metric_ids_come_from_the_versioned_registry
AC-004 -> tests/test_opip_r5_f10_source_census.py::test_ac_004_declared_gaps_are_registered_gaps
AC-005 -> tests/test_opip_r5_f10_source_census.py::test_ac_005_no_threshold_is_ratified_and_parity_is_preserved
AC-006 -> tests/test_opip_r5_f10_source_census.py::test_ac_006_no_application_or_deployment_path_is_touched

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-source-census.md
AC-001 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_source_census.py
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_source_census.py
AC-004 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_source_census.py
AC-005 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_source_census.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-source-census.md
AC-006 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_source_census.py

DEFERRED DISCOVERIES:
- The census records more than seventy requirement rows and a majority are BLOCKED_BY_UPSTREAM_EVIDENCE, because the canonical detector, forecast, Committee, incident and learning facts are not on the plane a read service may read. The recommended page order in the mission is therefore constrained by evidence availability rather than by effort. Ordering the paper-plane pages first, with the blocked pages rendering truthful unavailable states, is a sequencing decision for the owner.
- The existing analytics exposure contract asserts an exact mounted path set, and two existing test modules pin it. The semantic read service therefore cannot be mounted as a side effect of implementing it; mounting is a deliberate, separately recorded exposure change. F10-A is prepared to land the service and its routes without mounting them and to record the widening as an owner-visible decision.
- The census's declared absences are intentionally self-invalidating: the increment that supplies one of those capabilities must re-record the corresponding line. That fail-closed behaviour is intended, not a defect.
- No numeric threshold in this repository is ratified by the census, including the notification volume and cooldown values the owner controls.

UNAPPROVED SCOPE CHANGES:
NONE
