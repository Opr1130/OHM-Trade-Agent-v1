INCREMENT:
ATDD-R5-F10-traceability-matrix

OWNER-APPROVED INTENT:
This is a documentation-and-test increment of the owner-approved F10 mission, inside frozen architecture v1.5.0, and it is the traceability repair the mission's Phase 0 asks for. Three page increments have been delivered (F10-B, F10-C, F10-D) against a census that plans 54 criteria for their shared follower increment, and the evidence of what each of those 54 criteria actually is, where it is produced, what the delivered stack publishes for it and what remains blocked has until now existed only in narrative form. A narrative matrix can be mis-transcribed, and one was: the report written after the F10-D increment described AC-042 as a forecast criterion when the census records it as the frozen PortfolioPlan alternatives including the cash and no-trade alternative, and described the cross-page criteria AC-100, AC-101 and AC-102 as filter, cross-filter and paging criteria when the census records them as the cross-filter and drill-down contract, the saved-view contract and the analysis-preset contract. No repository artifact carried that error - the census, the three scope contracts and the service registry all cite those criteria correctly - but nothing in the repository would have caught the error either, because no artifact binds each criterion to its requirement, its page, its disposition and its evidence.

What changes where. This increment adds one evidence document that binds all 54 criteria to the census requirement that defines them, the frozen page that presents them, a governed functional disposition, the increment that owns the remaining work and an evidence reference that exists in the repository; and it adds the acceptance test that fails closed if any of those bindings stops being true. It adds no runtime code, no route, no page, no panel, no metric, no projection, no store, no scheduler, no writer and no artifact on any plane; it changes no application file. The trading host, the learning plane, the shadow plane and the offline plane are untouched, and no trading, admission, protection, risk, Telegram, promotion, deployment, release-profile or learning authority is created, widened or referenced.

The census is the authority and is not modified. This increment does not restate a census meaning, re-label a criterion, renumber a criterion, move a criterion between pages, move a criterion between increments, or ratify anything the census leaves open. Each row references its requirement by identifier and the test reads the meaning, the producer and the census status out of the census itself, so the matrix cannot drift from its authority: if the census changes, the test fails rather than the matrix quietly becoming false.

KEY LAW - THE MATRIX BINDS, IT DOES NOT RESTATE. A row names a criterion, the census requirement that defines it, the page, a disposition, an owner and a piece of evidence. It never copies the census's own wording, so a re-worded claim cannot masquerade as a citation, and the increment's test fails closed if any census meaning text appears in the document.

KEY LAW - A DISPOSITION IS NOT A CERTIFICATION. The functional disposition records what the delivered read-only stack does today for the fact family the criterion asks about. It is not a parity verdict, it transfers no ownership, and the deferred criterion it describes stays deferred and unclaimed in the increment that owns it. Parity stays NOT_VERIFIED with zero verified pages and this document adds no page to that count.

KEY LAW - NO FAVORABLE READING OF MISSING EVIDENCE. A criterion whose canonical evidence is not on the read plane is recorded as blocked with the census evidence gap that explains it; a criterion the census reserves to the owner is recorded as blocked with that owner input named; a criterion that is not implemented is recorded as not implemented with the increment that would take it. Nothing is recorded as satisfied because a page answers, and nothing is recorded as blocked without a reason that a reader can follow.

ARCHITECTURE REFERENCES:
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md sections 4, 8, 9, 11, 12 and 14: the 54 criteria this matrix binds, the panel map, the registered evidence gaps, the integrity rules the dispositions respect, the unratified threshold policy and the follower declarations. The census is not modified by this increment.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md sections 5, 6 and 7: the six-clause parity predicate this document must not appear to satisfy, the fail-closed negative cases against which a disposition is read, and the threshold policy that stays unratified.
- docs/architecture/v1.5.0/ARCHITECTURE.md section 15: the F10 feature row, its EXTEND disposition and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md F10 row: the target authority of one semantic read service and the cutover gate of fixture counts-and-P&L reconciliation that no page has met.
- docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md: the increment that owns all 54 criteria, whose approved criteria stay disjoint from them and whose deferred list carries them verbatim.
- docs/atdd/scope-contracts/ATDD-R5-F10-C-paper-portfolio-quality.md and docs/atdd/scope-contracts/ATDD-R5-F10-D-trust-learning-incident-pages.md: the two delivered page increments whose served page sets the page column reflects.
- app/opip/cockpit/semantic.py: the service whose panel registry supplies, for every panel-backed criterion, the panel identifier and the state the appended evidence describes.
- app/opip/cockpit/semantic_pages.py: the read model whose served page sets decide which criteria are presented on which page today.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the frozen source census with the criteria it plans for the first page increment, and the criterion matrix this increment adds
WHEN:
the matrix's rows are compared with the census's planned criterion set and with the census requirement each row names
THEN:
every planned criterion appears exactly once, no criterion appears that the census does not plan, each row's requirement identifier is the census row whose ATDD column names exactly that criterion, and the matrix's own totals agree with the rows it carries

AC-002:
GIVEN:
the matrix's page column and the frozen page and panel registries of the semantic read service
WHEN:
each row is compared with the page that serves the criterion's panel
THEN:
a criterion whose panel a frozen page declares names that page, a criterion no panel declares is marked as having no panel rather than being attributed to a page, and the matrix's page column is derived from the registry rather than asserted by hand

AC-003:
GIVEN:
the matrix's disposition, owner and evidence columns, the governed disposition vocabulary and the repository
WHEN:
every row is read
THEN:
each disposition is one of the governed values, each owner names either a declared scope contract increment or states that no increment is declared, each evidence reference resolves to a path that exists in the repository or is explicitly recorded as absent, and no row records a satisfied state for a fact family whose canonical evidence the census records as absent from this plane

AC-004:
GIVEN:
the matrix, the frozen census and the semantic read service's parity posture
WHEN:
the matrix's text is compared with the census's own wording and the service's parity constants
THEN:
no census meaning text is copied into the matrix, the document states that it certifies no criterion and claims no page, and the parity predicate stays NOT_VERIFIED with zero verified pages, per-page projection authority UNDECIDED, the counts-and-P&L reconciliation NOT_RECORDED and the threshold policy unratified

AC-005:
GIVEN:
the autonomous build report, this increment's scope contract, the movable active-increment pointer and the acceptance-test index
WHEN:
the report, the contract, the pointer and the index are read together
THEN:
the report records this increment with the exact base head it stacks on, its own scope contract and its own test count, the pointer resolves to a declared contract, every approved criterion is traced to the acceptance test that cites it and mapped to a path of this increment, and the parity row stays OUTSTANDING

EXPLICITLY OUT OF SCOPE:
- Any change to the source census, the parity specification, the conformance ledger, the requirements register, the retirement ledger or any other recorded architecture evidence. They are authorities this increment reads.
- Any change to any file under `app/`, and any new route, page, panel, metric, formula, registry, projection, store, table, scheduler, writer, migration, durable artifact or second read surface.
- Any change to the census criterion identifier, requirement identifier, page identity, panel identity or increment identifier of an existing criterion, and any move of a criterion between increments. The 54 criteria stay with the increment that owns them.
- Any parity verdict, projection-authority decision, counts-and-P&L reconciliation, threshold ratification, or netting of the two axes into a single score. The matrix reports a governance status and a functional disposition and never collapses them.
- Any deliverable of the F10-E, F10-F or F10-G increments, all of which stay owned by their own contracts, including the accessibility and operator-usefulness criterion the census plans for integrated certification.
- Any trading, admission, protection, exit, notification, promotion, deployment, release-profile or production authority change.

FROZEN BOUNDARIES:
- The census stays the only authority for what a criterion means, who produces it and what the census classifies it as. The matrix references those facts and never restates them.
- The F10-A semantic read service, its page and panel registries and the versioned metric registry stay the only fact, page, panel and metric-semantics authorities.
- This increment is documentation and tests only: it adds no runtime behavior, mounts nothing and changes no application file.
- The census's criterion ownership is unchanged: all 54 criteria stay deferred and unclaimed in the increment whose contract carries them verbatim.
- Parity stays NOT_VERIFIED with zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified.
- The project's standing controls on risk sizing, minimum reward-to-risk, position verification, execution safeguards, AI non-authority, paper isolation and deployment authorization are neither relaxed nor referenced by any logic here.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_criterion_matrix.py::test_ac_001_the_matrix_covers_exactly_the_planned_criteria_and_binds_each_to_its_requirement
AC-002 -> tests/test_opip_r5_f10_criterion_matrix.py::test_ac_002_each_planned_criterion_names_the_page_the_registry_serves_it_on
AC-003 -> tests/test_opip_r5_f10_criterion_matrix.py::test_ac_003_every_disposition_owner_and_evidence_reference_is_governed_and_resolves
AC-004 -> tests/test_opip_r5_f10_criterion_matrix.py::test_ac_004_the_matrix_restates_no_census_text_and_claims_no_parity
AC-005 -> tests/test_opip_r5_f10_criterion_matrix.py::test_ac_005_the_report_the_pointer_and_the_criteria_agree

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F10_CRITERION_MATRIX.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_criterion_matrix.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F10_CRITERION_MATRIX.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_criterion_matrix.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F10_CRITERION_MATRIX.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_criterion_matrix.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F10_CRITERION_MATRIX.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_criterion_matrix.py
AC-005 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-traceability-matrix.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_criterion_matrix.py

DEFERRED DISCOVERIES:
- The traceability error this increment repairs was found in a narrative report and not in a repository artifact, but the hazard is real: before this document, nothing in the repository bound a criterion identifier to its requirement, page, disposition and evidence. The three delivered page contracts cite criterion identifiers as bare identifiers on purpose, because they must not claim them; the consequence was that a mis-transcribed narrative had no test to fail against. The matrix now supplies that binding and this increment's test asserts it.
- The census maps the 54 criteria to one increment whose first slice is already delivered, while the two later page increments delivered different pages under their own identifiers. The matrix therefore separates two axes the census's own vocabulary does not separate: the criterion's governance status, which is still deferred and unclaimed in the increment that owns it, and the functional disposition of the fact family today, which the delivered read-only stack does decide. Collapsing them would either overstate a deferred criterion or understate a delivered page, so no row carries a single score.
- Criteria the census reserves to the owner - the per-page counts-and-P&L reconciliation and the saved-view contract - are recorded as blocked with the owner input named, not as work an increment can finish. The census records that a saved view may not be added outside an approved non-trading preference boundary, so no increment in this mission can deliver one and durable saved views stay blocked rather than approximated.
- Two criteria are recorded as satisfied by construction rather than by a value: the page-level invariant that no cockpit control can impair detection, risk or protection, and the page-level invariant that no promotion, release or activation control is exposed. Both are asserted structurally by the delivered increments, and neither authorizes anything: a read-only surface that cannot act is not a governance capability.
- The accessibility and operator-usefulness criterion the census plans is not one of the 54. The census records it as planned for the integrated-certification increment, and this matrix names it as out of scope rather than folding it in, so a later increment cannot inherit it by accident.
- Three criteria whose facts are already on this plane are recorded as not implemented because no panel presents them, and the matrix names no increment for them. Naming an increment that has no approved contract would invent authorization, so the gap is recorded as a declared absence of an owner instead.

UNAPPROVED SCOPE CHANGES:
NONE
