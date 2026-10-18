INCREMENT:
ATDD-R5-F10-C-paper-portfolio-quality

OWNER-APPROVED INTENT:
This is the second delivered slice of the owner-approved F10 mission, inside frozen architecture v1.5.0. The F10 source census splits the mission into F10-A (the semantic read service), F10-B..F10-E (the page, interaction and per-page parity work) and F10-F/F10-G (Telegram and integrated certification). Its F10-B..F10-E portion is delivered as independently reviewable increments rather than as one change: the delivered first slice `ATDD-R5-F10-dashboard-parity-implementation` pinned its own boundary by requiring that its approved criteria stay disjoint from every criterion the census plans for that increment and that the whole planned set stay deferred verbatim, and a later slice that quietly moved a planned criterion into that contract would weaken a delivered boundary instead of extending it. This contract is therefore the succeeding increment for the next two frozen pages, and it changes nothing about the increment it follows.

What changes where. On the analytics plane this increment widens the page read model that already exists from the three pages of the first slice to the five pages the analytics plane can reach evidence for - System Pulse, Discovery and Regime, Decision Trace, Paper Portfolio and Quality and Missed Opportunity - and it teaches that one read model to read a registered metric through the metric read the service declares for it instead of only through the population read. Three pages is not a typo and five is not a claim: P4 and P5 join the served page set because their facts are on this plane, and P6, P7 and P8 stay undelivered because their facts are not. On the trading host, the learning plane, the shadow plane and the offline plane nothing changes. No route is added, so the analytics plane's externally reachable surface - an owner-visible property pinned by the exposure regression suite and authorized by the preceding increment alone - is untouched by this contract. No projection, store, table, writer, scheduler, migration or durable artifact is added or edited, and no trading, admission, protection, risk, Telegram, promotion, deployment or release authority is created or widened.

The census's verdicts are carried, not rewritten. All eight pages stay unclaimable on this plane, the parity predicate stays NOT_VERIFIED with zero verified pages and per-page projection authority stays UNDECIDED, the threshold policy stays unratified, per-page counts-and-P&L reconciliation stays NOT_RECORDED, and every criterion the census plans for the preceding increment stays exactly where that increment left it, deferred and verbatim. This contract approves only criteria of its own, and none of them is a census criterion.

KEY LAW - ONE READ MODEL, ONE FACT AUTHORITY, TWO READ ENTRY POINTS. The page read model stays the only presentation read model, the F10-A service stays the only fact authority, and the versioned registry `app.opip.contracts.paper_metrics` stays the only metric-semantics authority. Teaching the read model a second read entry point does not create a second authority: a registered metric is read through the service's metric read and everything else through its population read, and neither the read model nor this contract restates a formula, a population or an exclusion.

KEY LAW - A REQUIRED FILTER THAT IS ABSENT IS A REFUSAL, NOT A DEFAULT. Several Paper Portfolio facts are scoped to one quote currency, and the service refuses to answer them without one. Where a panel's fact family refuses for that registered reason the panel is published unavailable with that registered reason and no value, so a currency-scoped panel can never be answered by summing USD and USDT or by assuming one of them. Any other invalid filter value is still refused as a client error rather than absorbed into an unavailable panel.

KEY LAW - A PAGE IS STILL NOT A CLAIM OF PARITY. Serving two more pages says nothing about whether those pages match the frozen expectation, and the counts-and-P&L fixture reconciliation the conformance ledger names as the cutover gate is still not recorded for any page.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md section 15: the F10 Dashboard feature row, its EXTEND disposition, and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md sections 4, 7, 8, 9, 10 and 14: the Paper Portfolio and Quality and Missed Opportunity requirement rows (`F10-P4-01`..`F10-P4-07`, `F10-P5-01`..`F10-P5-07`), the panel rows `P4-PNL-01`..`P4-PNL-05` and `P5-PNL-01`..`P5-PNL-05`, the declared evidence gaps, the declared absences, the retained threshold policy, and the follower declaration this contract carries out. The census is not modified by this increment.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md sections 3, 5 and 6: the frozen identity and operator question of P4 and P5, the parity predicate this increment must not disturb, and the fail-closed negative cases NC-02, NC-03, NC-04, NC-09 and NC-10.
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md F10 row: the target authority of one semantic read service over canonical projections, and the cutover gate of fixture counts-and-P&L reconciliation that this increment does not meet.
- docs/atdd/scope-contracts/ATDD-R5-F10-semantic-read-service.md: the increment that produced the service, its page and panel registries, its dispositions, its typed filter contract and its metric bindings.
- docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md: the delivered first slice whose page read model, page set and mounting posture this increment extends without changing. Its approved criteria, its deferred planned criteria and its pinned surface all stay exactly as delivered.
- app/opip/cockpit/semantic.py: the single fact authority, its page and panel registries, its dispositions, its `PANEL_PAGE` map and its metric and population read entry points.
- app/opip/cockpit/semantic_pages.py: the presentation read model this increment extends, and the mounting posture it declares rather than infers.
- app/api/cockpit_semantic.py and tests/test_bc4_cockpit_exposure_v1.py: the mounted read surface this increment does not widen and the exposure pin that proves it.
- app/opip/cockpit/portfolio.py and app/opip/cockpit/ledger.py: the existing portfolio and reconciled-ledger projections the Paper Portfolio facts come from, reused rather than rebuilt.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the page read model over the F10-A semantic read service, the Paper Portfolio page of the frozen registry and a reconciled paper ledger
WHEN:
the page is read with the delivered page set, with and without the quote currency its currency-scoped panels declare, and a panel of that page is read directly
THEN:
the page publishes its five declared panels in the registry's own order, a panel whose disposition the service declares served publishes a value assembled only from that panel's declared served facts, a currency-scoped panel refuses to publish a value while the registered quote-currency requirement is unmet and reports that registered reason instead, and no panel is answered with a zero, an empty collection or an omitted key in place of evidence this plane cannot produce

AC-002:
GIVEN:
the Quality and Missed Opportunity page of the frozen registry, whose four census-declared upstream panels this plane cannot produce, and its one panel this plane can partly serve
WHEN:
that page is read with the delivered page set and with filters the upstream panels declare
THEN:
the four blocked panels are published withheld with the service's own state, its own reason and, where one is registered, its own evidence-gap identifier, carrying no value at all, while the one panel whose disposition the service declares partly served publishes only the facts the service declares served for it and keeps that panel's own declared qualification reasons in its answer instead of dropping them

AC-003:
GIVEN:
the service's registered metric bindings, its declared fact names and the page read model that assembles panel values
WHEN:
a panel whose declared served facts are registered metrics and a panel whose declared served facts are declared populations are read, and the read model's source is inspected
THEN:
a registered metric is read through the service's metric read and every other declared fact through its population read, each reading is the service's own reading rather than a locally computed value, a fact the service refuses to answer through one entry point is never answered through the other, and the read model restates no formula, population, unit or exclusion and holds no second registry

AC-004:
GIVEN:
the page read model's delivered page set, the first slice's own page set and the frozen eight-page registry
WHEN:
the read model is constructed as the first slice constructs it, constructed with the delivered set, asked for a page inside the delivered set, asked for a page the census still blocks, and asked for a panel of a page outside the delivered set
THEN:
the default construction keeps the first slice's three-page behaviour and still refuses P4 exactly as delivered, the delivered construction serves the five pages whose facts this plane can reach in registry order, a page outside the delivered set is refused rather than half-rendered, and every served page keeps the service's own state, reasons and claimability instead of a state this read model computed

AC-005:
GIVEN:
the analytics-plane application, the read router, the semantic service's declared route set and the exposure regression suite that pins the mounted surface
WHEN:
the mounted path set, the route methods, the service's own mounting declaration and the exposure pin are inspected
THEN:
the mounted path set is exactly what the preceding increment mounted, this increment adds no route, every mounted route is still a GET, the service module still declares itself unmounted with an empty mounted path set, and the mounted surface still names the preceding increment as the one that authorized it

AC-006:
GIVEN:
the delivered first slice's scope contract, the frozen source census, the page read model and this contract
WHEN:
the first slice's approved criteria, its deferred planned criteria and this contract's own approved criteria are compared, and the page and parity posture is read back
THEN:
every criterion the census plans for the preceding increment stays deferred verbatim in that contract, this contract approves no census criterion and cites none as its own, the eight pages stay unclaimable with the parity predicate NOT_VERIFIED and zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified

AC-007:
GIVEN:
the two pages this increment adds to the served set and the sections of those pages this plane cannot produce
WHEN:
each added page's withheld sections and its requirement rows are read
THEN:
every section of those pages that this plane cannot produce is declared with the census criterion it belongs to and the producer's own reason, no unproducible section is omitted or silently dropped, and the declared set is asserted against the frozen registry rather than against a hand-kept copy

AC-008:
GIVEN:
the autonomous build report, this increment's scope contract, the movable active-increment pointer and the acceptance-test index
WHEN:
the report, the contract, the pointer and the index are read together
THEN:
the report records this increment with the exact base head it stacks on, its own scope contract and its own test count, the pointer resolves to a declared contract rather than to a hard-coded increment, every approved criterion is traced to exactly the acceptance test that cites it and mapped to a path of this increment, and no criterion this contract approves is one the census plans under this increment's own identifier

EXPLICITLY OUT OF SCOPE:
- The three pages this increment does not add to the served set: P6 Committee Value and AI Cost, P7 Learning and Releases and P8 Incidents and Infrastructure. They stay undelivered here; no page outside the served set may be rendered, stubbed or declared, and none of their panels may be published even as an empty shape.
- The census criteria this increment does not approve. They keep their own dispositions and are cited here only as the identifiers the two added pages carry, exactly as the preceding increment carries them:
  P4 Paper Portfolio: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-050; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-051; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-052; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-053; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-054; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-055; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-056;
  P5 Quality and Missed Opportunity: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-060; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-061; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-062; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-063; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-064; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-065; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-066;
- The interaction contract the census plans for the pages after these: navigation, cross-filters, drill-down, breadcrumbs, presets, saved views, keyboard accessibility, narrow-screen rendering and deep links. This increment serves two more pages through the surface that already exists and does not restyle it.
- Any change to the served page asset `app/api/cockpit_semantic.html`, to the read router's mounted path set, to the exposure regression suite's pinned surface, to the legacy cockpit, to `app/main.py` or to any route of any plane.
- Any new page, panel, metric, formula, registry, projection, store, table, scheduler, writer, migration or durable artifact, and any second presentation read model beside the one this increment extends.
- Any trading, admission, protection, exit, notification, Telegram, promotion, deployment, release-profile, learning or production authority change, and any favorable rendering of missing, stale, contradictory, unsupported or currency-ambiguous evidence.

FROZEN BOUNDARIES:
- The F10-A semantic read service, its page and panel registries, its dispositions, its metric bindings and the versioned metric registry stay the only fact and metric-semantics authorities. The read model reads them; it never restates them and never adds a second registry.
- The page read model stays one coherent code path. A second read model, a page-specific value assembler or a copied panel map is a defect rather than an optimization.
- The slice is read-only by construction: it opens no writer, takes no lock, holds no exchange credential, schedules nothing and reuses the analytics plane's single replica read path and its fail-closed secret gate.
- The analytics plane remains the only mounting site and the mounted surface is not widened by this increment.
- The eight frozen pages, their questions, their panels and their order keep the census's identity: no page is added, removed, renamed, split, merged or reordered.
- Pages remain unclaimable on this plane; parity stays NOT_VERIFIED with zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified. This increment declares no projection authority and proposes none for owner ratification.
- USD and USDT remain distinct portfolios; nothing here sums, joins or defaults across them, and a currency-scoped fact without its currency is a refusal rather than an assumption.
- Legacy surfaces are untouched, and the preceding increment's contract, its approved criteria, its deferred planned criteria and its pinned surface are unchanged.
- The project's standing controls on risk sizing, minimum reward-to-risk, position verification, execution safeguards, AI non-authority, paper isolation and deployment authorization are neither relaxed nor referenced by any logic here.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_c_paper_portfolio_quality.py::test_ac_001_the_paper_portfolio_page_publishes_only_what_the_service_allows
AC-002 -> tests/test_opip_r5_f10_c_paper_portfolio_quality.py::test_ac_002_the_quality_page_withholds_its_blocked_panels_with_the_producers_reason
AC-003 -> tests/test_opip_r5_f10_c_paper_portfolio_quality.py::test_ac_003_a_metric_fact_is_read_through_the_metric_read_and_no_formula_is_restated
AC-004 -> tests/test_opip_r5_f10_c_paper_portfolio_quality.py::test_ac_004_the_delivered_page_set_extends_the_slice_without_changing_it
AC-005 -> tests/test_opip_r5_f10_c_paper_portfolio_quality.py::test_ac_005_the_mounted_surface_and_the_mount_posture_stay_unchanged
AC-006 -> tests/test_opip_r5_f10_c_paper_portfolio_quality.py::test_ac_006_the_planned_criteria_the_parity_and_the_threshold_policy_stay_untouched
AC-007 -> tests/test_opip_r5_f10_c_paper_portfolio_quality.py::test_ac_007_every_unproducible_section_of_the_added_pages_is_declared
AC-008 -> tests/test_opip_r5_f10_c_paper_portfolio_quality.py::test_ac_008_the_report_the_pointer_and_the_criteria_agree

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-005 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-008 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-C-paper-portfolio-quality.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py

DEFERRED DISCOVERIES:
- The census plans P4 and P5 under the preceding increment's identifier, but that increment's delivered contract requires its approved criteria to stay disjoint from every criterion the census plans for it and its deferred list to carry the planned set verbatim. Approving a planned criterion there would have weakened a delivered boundary, so this increment succeeds it under its own identifier and approves only criteria of its own. The census is unchanged and remains the mapping authority, and every one of its P4 and P5 criteria keeps the disposition the preceding increment gave it.
- Teaching the read model to read a registered metric was necessary rather than optional: the first slice only ever read declared populations, so a Paper Portfolio panel whose declared facts are registry metrics could not be assembled at all. The read model now selects the service's metric read for a fact the versioned registry binds and its population read for every other declared fact, with no local copy of the registry and no local formula.
- A currency-scoped fact without its quote currency is published unavailable with the service's own registered reason instead of failing the whole page. Failing the page would have hidden the panels that are readable, and assuming a currency would have answered a different question. A malformed value for a declared filter is still refused as a client error, and both behaviours are pinned by this increment's tests.
- A served panel's answer now carries the service's own declared qualification reasons beside the reading's own, because a qualified declaration that silently dropped its qualification would overstate what the panel proves. The first slice published only the reading's reasons.
- P4 and P5 are served while P6, P7 and P8 stay undelivered, and that split is evidence-driven: the Paper Portfolio and Quality facts exist on the reconciled replica this plane may read, and the Committee, learning and incident facts do not. Serving all eight pages at once would have rendered three pages whose every panel is unproducible, which presents an absence rather than a page.
- Serving a page is not proving it. P4's own page requirement still names the frozen SC-08 plan record as a plan revision, which this plane does not hold, so P4 stays unclaimable; P5's requirement names matured-outcome and missed-opportunity facts that mature on the learning plane. Both pages therefore publish their readable panels and an explicit record of what they cannot prove.
- No projection authority is proposed or ratified here. The parity specification records that decision as UNDECIDED and owner-gated, and this increment records only which existing read paths its panels read.
- The withheld-section declarations for the added pages are asserted against the frozen page and panel registries at import and in this increment's tests, so a section a later registry change makes producible fails closed rather than staying quietly withheld.
- Two Paper Portfolio capabilities the census itself classifies as missing stay missing: there is no canonical cash ledger on this plane, so cash and available capital stay withheld with that registered reason, and there is no mark evidence bound to open positions, so nothing here presents a marked or unrealized figure. Both are upstream evidence gaps, not read-side omissions.

UNAPPROVED SCOPE CHANGES:
NONE
