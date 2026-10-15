INCREMENT:
ATDD-R5-F10-G

OWNER-APPROVED INTENT:
This is the census-declared integrated-certification increment of the owner-approved F10 mission, inside frozen architecture v1.5.0. The census plans one criterion for it -- `AC-110`, accessibility checks and inspectable operator-usefulness evidence for an eight-page experience -- and this contract approves that criterion under its own identifier together with five of its own for the integrated work the census asks the increment to perform: cross-page consistency at one watermark, the route inventory with write refusal and legacy coexistence, uniform behaviour under missing, empty and unreadable evidence, representative integrated journeys, and this increment's delivery record.

What changes where. Nothing in the application changes. This increment adds one acceptance-test module and the records that accompany it; it edits no runtime module, adds no route, changes no response shape and touches no store, writer or scheduler. That is the point: certification is an assertion about what has already been delivered, and an increment that changed behaviour while certifying it would be certifying something else.

What it certifies, and what it does not. It certifies that the eight frozen pages are reachable over HTTP, unclaimed, and consistent at one watermark; that the read surface is exactly the declared GET routes with no write path and that the legacy cockpit still exists beside it; that missing, empty and unreadable evidence produce three distinguishable outcomes rather than one favourable one; that the declared presets and drill targets lead an operator through journeys that publish what the evidence supports and withhold the rest with reasons; and that the served asset carries the accessibility and viewport semantics the census asks for. It certifies **no page's parity**. The six-clause predicate requires reconciled counts and P&L against a declared projection authority at one watermark, that authority is still `UNDECIDED` and that reconciliation is still `NOT_RECORDED`, so every page stays `NOT_VERIFIED` and this increment's tests assert that they do.

The accessibility evidence is structural and is labelled as such. No headless browser runs in this repository; the asset's semantics are asserted and the operator-journey half is proven over HTTP. Nothing here is presented as a browser end-to-end test, and the tooling gap is recorded rather than closed by relabelling.

The census's verdicts are carried, not rewritten. All eight pages stay unclaimable on the read plane, the parity predicate stays NOT_VERIFIED with zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified. The 54 dashboard criteria and their matrix rows and totals are untouched, and the Telegram criteria belong to their own increment.

KEY LAW - REACHABLE IS NOT CERTIFIED. A page that answers is not a page that has been verified. Every assertion in this increment is about reachability, consistency, refusal or evidence, and none of them may be read as a parity certificate for any page.

KEY LAW - THREE ABSENCES ARE THREE DIFFERENT THINGS. An empty valid population is a real zero; unavailable evidence carries no value and a reason; a withheld panel carries the producer's own reason. This increment asserts that those three stay distinguishable under load on every page, because collapsing them is how a missing feed becomes a healthy one.

KEY LAW - CERTIFICATION MAY NOT CHANGE WHAT IT CERTIFIES. This increment adds tests and records only. If a test had required a behaviour change, the change would belong to the increment that owns that behaviour, not to the certification of it.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md sections 13, 15 and 18: the inherited cockpit-alert clause, the F10 feature row with its EXTEND disposition, the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states", and the readiness-gate framing this increment's materials support.
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md sections 4, 8 and 14: the `F10-FLT-05` criterion (`AC-110`) this contract approves, the panel drill targets it exercises, the integrity rules it asserts, and the follower declaration that names this increment. The census is not modified.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md sections 5, 6 and 7: the six-clause parity predicate this increment must not appear to satisfy, the fail-closed negative cases it exercises, and the threshold policy that stays unratified.
- docs/architecture/OPIP_RETIREMENT_LEDGER.md: the legacy cockpit's disposition, which this increment leaves exactly where it is.
- docs/atdd/scope-contracts/ATDD-R5-F10-E-operator-interaction.md and ATDD-R5-F10-E-operator-interaction-presentation.md: the increments whose interaction contract, accessibility semantics and viewport rules this increment certifies.
- docs/atdd/evidence/F10_CRITERION_MATRIX.md: the per-criterion dispositions this increment's evidence relies on and does not alter.
- app/api/cockpit_service.py and app/api/cockpit_semantic.py: the mounted read surface whose exact route inventory and refusal behaviour are asserted here.
- app/api/dashboard.py and app/main.py: the legacy outcomes cockpit, which must still exist and still be mounted where it always was.
- app/api/cockpit_semantic.html: the served asset whose accessibility and viewport semantics are asserted here.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-110:
GIVEN:
the served asset, the mounted read surface and the recorded evidence
WHEN:
every frozen page is reached, the asset's accessibility and viewport semantics are inspected, and the evidence artifacts are located
THEN:
every page is reachable and reports itself unclaimed, the asset carries keyboard, focus, labelling, table and viewport semantics, the operator-usefulness evidence is inspectable in the repository rather than asserted in prose, and the assertion is stated as structural rather than as a browser end-to-end test

AC-128:
GIVEN:
the mounted read surface over one reconciled replica
WHEN:
every page, the page index and a row-grain fact are read, and the two quote currencies are read separately
THEN:
every reading reports the same watermark, a page's panel reading agrees with the row population it publishes at that watermark, and the two currencies are answered as separate partitions whose counts sum to the whole rather than being joined

AC-129:
GIVEN:
the mounted analytics-plane application and the legacy dashboard surface
WHEN:
the mounted path set, the methods each path accepts and the legacy router are inspected
THEN:
the mounted set is exactly the legacy cockpit reads plus the declared semantic reads and the page asset, every route is a GET, a write is refused with a method error, and the legacy cockpit still exists and is still mounted by the trading entry point rather than replaced or re-pointed

AC-130:
GIVEN:
the mounted read surface and a replica that is empty, then unreadable
WHEN:
every page is read in both states, and an impossible filter and a missing credential are presented to every page
THEN:
every page still renders with its declared panels, an empty valid population is a real zero, unreadable evidence is unavailable with no value and a reason, no panel substitutes a zero for evidence it does not hold, and both refusals hold uniformly for every page

AC-131:
GIVEN:
the mounted read surface, the declared presets and the drill targets
WHEN:
a settled-economics journey, a non-definitive-economics journey, a missing-population journey and a decision-trace link are taken in sequence
THEN:
each journey reaches its page over HTTP, publishes what the evidence supports, reports the rest as withheld or unavailable with reasons, and never presents an absence as a result

AC-132:
GIVEN:
the autonomous build report, this increment's scope contract, the movable active-increment pointer and the acceptance-test index
WHEN:
the report, the contract, the pointer and the index are read together
THEN:
the report records this increment with the exact base head it stacks on, its own scope contract and its own test count, the pointer resolves to a declared contract, every approved criterion is traced to exactly the acceptance test that cites it and mapped to a path of this increment, the criterion this increment owns in the census is exactly the accessibility criterion, and the parity row stays OUTSTANDING

EXPLICITLY OUT OF SCOPE:
- Any change to any module under `app/`. This increment adds tests and records only; it certifies what has already been delivered rather than changing it.
- Any new route, response shape, page, panel, metric, formula, registry, projection, store, table, scheduler, writer, migration or durable artifact, and any widening of the analytics plane's mounted path set or its exposure pin.
- Any parity verdict, projection-authority decision, counts-and-P&L reconciliation, threshold ratification, or G4 approval. The predicate's evidence clauses are unmet and this increment says so.
- Any change to the 54 dashboard criteria, to the criterion matrix's rows or totals, or to the criteria the Telegram increment owns.
- Any retirement, weakening or re-pointing of the legacy outcomes cockpit or its `/api/analytics/*` consumers.
- Any browser end-to-end claim. No headless browser harness is introduced, and no structural assertion is relabelled as operator validation in a real browser.
- Any trading, admission, protection, exit, notification, Telegram, promotion, deployment, release-profile or production authority change, and any funded-execution path.

FROZEN BOUNDARIES:
- The F10-A semantic read service and its registries stay the only fact, page, panel, filter and metric-semantics authorities; the read model stays the only presentation read model.
- Pages remain unclaimable; parity stays NOT_VERIFIED with zero verified pages, projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified. This increment declares no projection authority and proposes none for ratification.
- The analytics plane remains the only mounting site; the trading host is untouched and no analytics workload moves onto it.
- USD and USDT remain distinct portfolios; the certification asserts they are answered separately rather than joined.
- Legacy surfaces are preserved exactly, and their eventual removal stays the retirement ledger's own gated decision.
- The project's standing controls on risk sizing, minimum reward-to-risk, position verification, execution safeguards, AI non-authority, paper isolation and deployment authorization are neither relaxed nor referenced by any logic here.

ACCEPTANCE TEST TRACEABILITY:
AC-110 -> tests/test_opip_r5_f10_g_integrated_certification.py::test_ac_110_accessibility_and_operator_usefulness_evidence
AC-128 -> tests/test_opip_r5_f10_g_integrated_certification.py::test_ac_128_one_watermark_and_consistent_populations_across_pages
AC-129 -> tests/test_opip_r5_f10_g_integrated_certification.py::test_ac_129_route_inventory_write_refusal_and_legacy_coexistence
AC-130 -> tests/test_opip_r5_f10_g_integrated_certification.py::test_ac_130_missing_evidence_is_uniform_and_never_favorable
AC-131 -> tests/test_opip_r5_f10_g_integrated_certification.py::test_ac_131_representative_integrated_journeys
AC-132 -> tests/test_opip_r5_f10_g_integrated_certification.py::test_ac_132_the_report_the_pointer_and_the_criteria_agree

IMPLEMENTATION MAP:
AC-110 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_g_integrated_certification.py
AC-128 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_g_integrated_certification.py
AC-129 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_g_integrated_certification.py
AC-130 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_g_integrated_certification.py
AC-131 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_g_integrated_certification.py
AC-132 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-132 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-132 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-G.md
AC-132 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_g_integrated_certification.py

DEFERRED DISCOVERIES:
- Certification was kept to tests and records on purpose. An increment that changed behaviour while asserting it would be certifying something other than what was delivered, and the behaviour it would have changed belongs to the increments that own it.
- The three absence outcomes are asserted as three distinguishable things on every page: an empty valid population is a real zero, unavailable evidence is a value-less reading with a reason, and a withheld panel keeps the producer's own reason. Collapsing any two of them is how a missing feed starts looking healthy, which is the failure this certification exists to catch.
- The accessibility criterion the census plans for this increment is asserted structurally and labelled as such. No headless browser harness exists here, so the operator-journey half is proven over HTTP and the browser gap is recorded rather than disguised. Adding a browser harness would be an architecture and dependency decision, not a certification one.
- Legacy coexistence is asserted from the sources that mount the legacy surface, not by importing the trading entry point: importing it starts production subsystems, which a read-only certification must not do. The assertion is therefore narrower than a runtime check and is labelled as what it is.
- The watermark consistency check is limited to what one replica can prove: the index, the eight pages and a row-grain fact must agree, and a panel's own reading must agree with the population it publishes. It does not claim the counts-and-P&L reconciliation the parity predicate requires, which needs a declared projection authority and fixtures that do not exist yet.
- Five of this increment's six criteria are its own identifiers rather than census identifiers. The census plans only the accessibility criterion for this increment, so reusing the other planning identifiers would have changed what they mean; the extra identifiers record work the census asks for without appropriating anything.
- G4 readiness is prepared but not decided. The questionnaire, the walkthrough and the evidence manifest belong to the owner, and the two predicate clauses that remain unmet are named rather than worked around.

UNAPPROVED SCOPE CHANGES:
NONE
