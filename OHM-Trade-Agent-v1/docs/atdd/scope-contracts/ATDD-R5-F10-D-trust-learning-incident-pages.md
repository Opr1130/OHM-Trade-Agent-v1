INCREMENT:
ATDD-R5-F10-D-trust-learning-incident-pages

OWNER-APPROVED INTENT:
This is the third delivered slice of the owner-approved F10 mission, inside frozen architecture v1.5.0. The F10 source census splits the mission into F10-A (the semantic read service), F10-B..F10-E (the page, interaction and per-page parity work) and F10-F/F10-G (Telegram and integrated certification). This contract carries out the remaining page work of that middle portion and succeeds the two increments before it, exactly as they succeeded each other.

What changes where. On the analytics plane this increment widens the page read model's served set from the five pages the previous increment served to all eight frozen pages, and it declares the unproducible sections of the three pages it adds: Committee Value and AI Cost, Learning and Releases, and Incidents and Infrastructure. It adds no new read entry point, no new metric, no new panel and no new route: the pages it adds are served by the routes the first slice mounted, over the same read-only replica, behind the same fail-closed secret gate, and it changes nothing about the pages the earlier increments served. On the trading host, the learning plane, the shadow plane and the offline plane nothing changes, and no projection, store, table, writer, scheduler, migration or durable artifact is added or edited.

Why a page whose every required fact is missing is still served. Six of the three added pages' panels are declared by the service as blocked or absent, and the seventh publishes only the read model's own trust and reconciliation posture because that is the only evidence about them this plane holds. Serving those pages is the honest presentation: the operator sees the page, sees which panels are withheld, and sees the producer's own reason and registered evidence gap for each one. Dropping the pages instead would make an absent page indistinguishable from a healthy one, and inventing the missing Committee, learning, incident or host-runtime facts would create a second fact authority. Neither is done here.

The census's verdicts are carried, not rewritten. All eight pages stay unclaimable on this plane, the parity predicate stays NOT_VERIFIED with zero verified pages and per-page projection authority stays UNDECIDED, the threshold policy stays unratified, per-page counts-and-P&L reconciliation stays NOT_RECORDED, and every criterion the census plans for the first page increment stays exactly where that increment left it, deferred and verbatim. This contract approves only criteria of its own, and none of them is a census criterion.

KEY LAW - SERVING A PAGE IS NOT CLAIMING IT. Eight reachable pages are a navigation surface, not eight parity certificates. A page is claimable only when the service says every fact family the page requires is producible on this plane, and the service says that for none of the eight.

KEY LAW - AN UNPRODUCIBLE PANEL IS PUBLISHED WITH ITS PRODUCER'S OWN REASON. Every withheld panel of the three added pages keeps the service's own state, its own reason and, where one is registered, its own evidence-gap identifier, and carries no value at all. Nothing here substitutes a zero, an empty collection, an omitted key, a neutral regime, an assumed healthy incident set or a guessed cost for evidence this plane does not hold.

KEY LAW - A PAGE WHOSE EVERY PANEL IS WITHHELD PUBLISHES NO ECONOMICS. Where a page can publish nothing, it publishes nothing: an empty result is not a favorable result, and a page that cannot answer its operator question says so rather than answering it with a green shape.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md sections 12, 13, 15 and 21: the F10 Dashboard feature row with its EXTEND disposition, the inherited economic-metric and cockpit-alert clauses, the Committee trust panels, and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md sections 4, 7, 8, 9, 10 and 14: the Committee Value and AI Cost, Learning and Releases and Incidents and Infrastructure requirement rows (`F10-P6-01`..`F10-P6-05`, `F10-P7-01`..`F10-P7-06`, `F10-P8-01`..`F10-P8-07`), the panel rows `P6-PNL-01`..`P6-PNL-04`, `P7-PNL-01`..`P7-PNL-04` and `P8-PNL-01`..`P8-PNL-04`, the declared evidence gaps, the declared absences, the retained threshold policy, and the follower declaration this contract carries out. The census is not modified by this increment.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md sections 3, 5 and 6: the frozen identity and operator question of P6, P7 and P8, the parity predicate this increment must not disturb, and the fail-closed negative cases NC-02, NC-04, NC-05, NC-06, NC-07, NC-09 and NC-10.
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md F10 row: the record that canonical detector, forecast and Committee facts are not on the live path, and the cutover gate of fixture counts-and-P&L reconciliation that this increment does not meet.
- docs/atdd/scope-contracts/ATDD-R5-F10-semantic-read-service.md: the increment that produced the service, its page and panel registries and its dispositions.
- docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md: the delivered first slice whose page read model, mounted surface and pinned exposure this increment extends without changing.
- docs/atdd/scope-contracts/ATDD-R5-F10-C-paper-portfolio-quality.md: the delivered second slice whose served page set this increment widens, whose behaviour on its own five pages stays unchanged, and whose metric and population read selection this increment reuses unchanged.
- app/opip/cockpit/semantic.py: the single fact authority, its page and panel registries, its dispositions, its `PANEL_PAGE` map, its registered reason vocabulary and its read entry points.
- app/opip/cockpit/semantic_pages.py: the presentation read model this increment extends.
- app/api/cockpit_semantic.py and tests/test_bc4_cockpit_exposure_v1.py: the mounted read surface this increment does not widen and the exposure pin that proves it.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the page read model over the F10-A semantic read service and the three frozen pages this increment adds to its served set
WHEN:
each added page is read with the delivered page set and its panels are read directly
THEN:
every added page is served with its panels in the frozen registry's own order, its own state and its own claimability read from the service and never recomputed, the panels whose disposition the service declares blocked or absent are published withheld with the service's own state, reason and registered evidence-gap identifier and carry no value at all, and the one panel the service declares partly served publishes only the read-model posture facts it declares served

AC-002:
GIVEN:
the sections of the three added pages this plane cannot produce and the frozen registry, census and registered reason vocabulary
WHEN:
each added page's withheld sections are compared with its own panel declarations and its own census criteria
THEN:
every unproducible section is declared with a criterion the census records for that page and a reason the service itself registers, every panel whose disposition the service qualifies has its qualification declared, and no declared reason or criterion is invented

AC-003:
GIVEN:
the read model's served page set, the frozen eight-page registry and the two page sets the earlier increments delivered
WHEN:
the read model is constructed with each of the three page sets and asked for a page inside and outside each one
THEN:
the default construction still serves the three-page first slice, the second increment's set still serves its own five pages, the widened set serves exactly the eight frozen pages in inventory order and claims none of them, and a page or panel outside the registry is refused rather than half-rendered

AC-004:
GIVEN:
the analytics-plane application, the read router, the semantic service's declared route set and the exposure regression suite that pins the mounted surface
WHEN:
the mounted path set, the route methods, the accepted parameters, the service's own mounting declaration and the exposure pin are inspected
THEN:
the mounted path set is exactly what the first slice mounted, this increment adds no route, every mounted route is still a GET, the service module still declares itself unmounted with an empty mounted path set, and the mounting stays attributed to the increment that authorized it

AC-005:
GIVEN:
the first page increment's scope contract, the frozen census, the semantic read service and this contract
WHEN:
the planned criteria, the approved criteria and the page, parity, authority, reconciliation and threshold posture are compared
THEN:
every criterion the census plans for the first page increment stays deferred verbatim in that contract, this contract approves no census criterion, and the eight pages stay unclaimable with the parity predicate NOT_VERIFIED and zero verified pages, per-page projection authority UNDECIDED, the counts-and-P&L reconciliation NOT_RECORDED and the threshold policy unratified

AC-006:
GIVEN:
the autonomous build report, this increment's scope contract, the movable active-increment pointer and the acceptance-test index
WHEN:
the report, the contract, the pointer and the index are read together
THEN:
the report records this increment with the exact base head it stacks on, its own scope contract and its own test count, the pointer resolves to a declared contract rather than to a hard-coded increment, every approved criterion is traced to exactly the acceptance test that cites it and mapped to a path of this increment, and the parity row stays OUTSTANDING and R7 unstarted

EXPLICITLY OUT OF SCOPE:
- The interaction contract the census plans after these pages: cross-filters, drill-down, breadcrumbs, presets, saved views, keyboard accessibility, narrow-screen rendering and deep links. This increment widens the served page set; it does not restyle the surface or add view state.
- Any change to the served page asset `app/api/cockpit_semantic.html`, to the read router's mounted path set, to the exposure regression suite's pinned surface, to the legacy cockpit, to `app/main.py` or to any route of any plane.
- Any census criterion. They keep their dispositions and are cited here only as the identifiers the three added pages carry, exactly as the earlier increments carry them:
  P6 Committee Value and AI Cost: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-070; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-071; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-072; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-073; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-074;
  P7 Learning and Releases: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-080; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-081; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-082; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-083; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-084; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-085;
  P8 Incidents and Infrastructure: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-090; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-091; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-092; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-093; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-094; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-095; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-096;
- Any new page, panel, metric, formula, registry, projection, store, table, scheduler, writer, migration or durable artifact, any second presentation read model, and any change to the pages the two earlier slices already served.
- Any Committee, learning, Telegram, incident, host-runtime or protection producer. This increment consumes no such evidence because none of it is published to the read plane; inventing it would create a second fact authority.
- Any trading, admission, protection, exit, notification, promotion, deployment, release-profile or production authority change, and any favorable rendering of missing, stale, contradictory or unsupported evidence.

FROZEN BOUNDARIES:
- The F10-A semantic read service, its page and panel registries, its dispositions, its metric bindings and the versioned metric registry stay the only fact and metric-semantics authorities.
- The page read model stays one coherent code path. A page-specific value assembler or a copied panel map is a defect rather than an optimization.
- The slice is read-only by construction: it opens no writer, takes no lock, holds no exchange credential and schedules nothing.
- The analytics plane remains the only mounting site and the mounted surface is not widened by this increment.
- The eight frozen pages, their questions, their panels and their order keep the census's identity: no page is added, removed, renamed, split, merged or reordered.
- Pages remain unclaimable on this plane; parity stays NOT_VERIFIED with zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified. This increment declares no projection authority and proposes none for owner ratification.
- USD and USDT remain distinct portfolios; nothing here sums, joins or defaults across them.
- Legacy surfaces are untouched, and the earlier increments' contracts, criteria, deferred lists and pinned surfaces are unchanged.
- The project's standing controls on risk sizing, minimum reward-to-risk, position verification, execution safeguards, AI non-authority, paper isolation and deployment authorization are neither relaxed nor referenced by any logic here.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_d_trust_learning_incident_pages.py::test_ac_001_the_added_pages_publish_only_what_the_service_allows
AC-002 -> tests/test_opip_r5_f10_d_trust_learning_incident_pages.py::test_ac_002_every_unproducible_section_of_the_added_pages_is_declared
AC-003 -> tests/test_opip_r5_f10_d_trust_learning_incident_pages.py::test_ac_003_the_eight_page_set_widens_the_earlier_sets_without_changing_them
AC-004 -> tests/test_opip_r5_f10_d_trust_learning_incident_pages.py::test_ac_004_the_mounted_surface_and_the_mount_posture_stay_unchanged
AC-005 -> tests/test_opip_r5_f10_d_trust_learning_incident_pages.py::test_ac_005_the_planned_criteria_the_parity_and_the_threshold_policy_stay_untouched
AC-006 -> tests/test_opip_r5_f10_d_trust_learning_incident_pages.py::test_ac_006_the_report_the_pointer_and_the_criteria_agree

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_d_trust_learning_incident_pages.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_d_trust_learning_incident_pages.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_d_trust_learning_incident_pages.py
AC-004 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_d_trust_learning_incident_pages.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_d_trust_learning_incident_pages.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md
AC-006 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-D-trust-learning-incident-pages.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_d_trust_learning_incident_pages.py

DEFERRED DISCOVERIES:
- All three added pages are served and none of them is claimed. Six of their seven panels are blocked or absent upstream and one publishes only the read model's own trust and reconciliation posture, so the honest operator result is: the page exists, the panels are listed, and each withheld panel states the producer's own reason and the registered gap that would have to be closed upstream.
- Serving a page whose panels are all withheld is a deliberate presentation choice, not an implementation gap. The registry already declares these pages and their panels, the service already declares each disposition, and an absent page would be indistinguishable from a healthy one.
- No Committee, learning, incident or host-runtime producer is added, reached or approximated. The census classifies those facts as blocked because the canonical evidence is not on the plane this read service may read, and a read service that produced them would be a second writer rather than a cockpit.
- The three newly declared withheld-section sets are validated at import against the service's registered reason vocabulary, and in this increment's tests against the census's own rows for those pages, so a reason or criterion that is invented or that stops matching the census fails closed.
- Serving every frozen page means the served set and the frozen registry now agree exactly: `ALL_PAGES` is asserted to equal the registry's page inventory, so a future registry change cannot leave a page silently unserved.
- The interaction contract remains undelivered. Eight reachable pages are a navigation surface; cross-filters, drill-down, breadcrumbs, presets, permitted saved-view semantics, keyboard accessibility and deep links are the next increment's work, and durable saved-view persistence would need a store this mission is not authorized to add.
- Parity is still NOT_VERIFIED with zero verified pages, and serving eight pages does not move it: the counts-and-P&L fixture reconciliation the conformance ledger names as the cutover gate is not recorded for any page, and per-page projection authority is still the owner's decision.

UNAPPROVED SCOPE CHANGES:
NONE
