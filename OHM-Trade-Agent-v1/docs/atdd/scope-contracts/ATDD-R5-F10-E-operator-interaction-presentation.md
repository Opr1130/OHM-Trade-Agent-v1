INCREMENT:
ATDD-R5-F10-E-operator-interaction-presentation

OWNER-APPROVED INTENT:
This is the presentation half of the operator-interaction increment, inside frozen architecture v1.5.0, and it completes the interaction capability the previous increment began. The previous increment delivered the part a caller can be held to over HTTP: the filter algebra, the published catalog, the declared presets and the resolvable evidence links. This one delivers the served surface that an operator actually uses, and it changes no read contract to do it.

What changes where. One file changes on the analytics plane: the served page asset. It gains a named navigation landmark and a content landmark with a skip link, page tabs that declare themselves a tablist and state which tab is current, a breadcrumb that names the location, filter controls built from the catalog the read model publishes rather than from a list the asset keeps, preset buttons built from the read model's declared presets, evidence links rendered as real anchors from each panel's published drill target, an announced status line and an alert region, tables with a caption and scoped headers whose cells carry their column names, a focus style that is visible, arrow-key movement inside the tablist, a narrow-screen layout that reflows instead of scrolling sideways, and an explicit failure state that replaces a stale view rather than leaving it standing. No route is added, no response shape changes, no read model or service behavior changes, and the router is untouched.

The view state stays in the tab. It is a plain variable and it is never written to local or session storage, a cookie, a database or disk; the previous increments assert that storage-free invariant and this increment preserves it rather than trading it for a convenience. What the address bar keeps is the non-secret part of the question -- the page and its typed filters -- so a view can be bookmarked, shared or reloaded without the credential that protects the data ever appearing in a URL.

Durable saved views remain blocked. The census records that a saved view may not be added outside an approved non-trading preference boundary; a bookmarkable address bar and a session-only variable are neither of those things, and this increment does not present them as durable saved-view parity.

The census's verdicts are carried, not rewritten. All eight pages stay unclaimable, the parity predicate stays NOT_VERIFIED with zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified. This contract approves only criteria of its own.

KEY LAW - A STRUCTURAL ASSERTION IS NOT AN OPERATOR JOURNEY. No headless browser runs here, so this increment proves that the served asset carries the semantics an operator and an assistive technology depend on. It does not claim that a human completed a journey in a real browser, and its tests say so. The journey half is proven separately over HTTP by the previous increment.

KEY LAW - STATE IS ANNOUNCED, NOT ENCODED IN COLOUR. Current tab, status, failure, withheld panels and unresolved filter dimensions all carry text or an ARIA state as well as a visual treatment, because colour alone is not a status and the operator may not be seeing it.

KEY LAW - AN UNSUPPORTED CONTROL IS NOT OFFERED. The filter controls are generated from the published catalog, so a dimension the service would refuse has no control, and the asset holds no copy of a dimension name that could drift from the contract.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md section 15: the F10 Dashboard feature row, its EXTEND disposition, its interaction obligations, and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md sections 4, 8, 11 and 12: the interaction rows (`F10-FLT-02`, `F10-FLT-04`, `F10-FLT-05`), the panel drill targets, the integrity rules and the unratified threshold policy. The census is not modified by this increment.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md sections 5 and 6: the parity predicate this increment must not appear to satisfy and the fail-closed negative cases.
- docs/atdd/scope-contracts/ATDD-R5-F10-E-operator-interaction.md: the increment that published the catalog, the presets and the drill targets this asset consumes, and whose HTTP journeys this increment's structural assertions complement.
- docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md: the increment that mounted the asset route and pinned the asset's storage-free, secret-free and authority-free invariants, which this increment preserves.
- app/api/cockpit_semantic.html: the served asset this increment rewrites.
- app/api/cockpit_semantic.py and tests/test_bc4_cockpit_exposure_v1.py: the route that serves the asset as one static GET and the exposure pin that keeps it the only page route.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the page asset the read router serves and the reviewed file on disk
WHEN:
the bytes served, the asset text and its sources of truth are inspected
THEN:
the asset is served as one static GET of exactly the reviewed file, it persists nothing in the browser through local or session storage, a cookie or a database, it holds no order, exchange or mounting control, and its filter and preset controls are built from the declarations the read model published rather than from a list the asset keeps

AC-002:
GIVEN:
the served asset and the navigation the read model publishes
WHEN:
the asset's landmarks, tab semantics, breadcrumb and evidence links are inspected
THEN:
the page has a named navigation landmark and a content landmark reachable by a skip link, its page tabs declare themselves as a tablist and state which tab is current, its breadcrumb names the location, each panel's evidence link is a real anchor built from the published drill target, and its tables carry a caption and scoped headers whose cells name their column

AC-003:
GIVEN:
the served asset
WHEN:
its focus styling, control types, tablist keyboard handling and live regions are inspected
THEN:
focus is visible rather than implied, every control is a native control the platform activates, the tablist responds to arrow keys with only the current tab in the tab order, the content landmark takes focus after a page loads, the status region is announced politely, the error region is an alert, and every filter control has a label associated with it

AC-004:
GIVEN:
the served asset and the states the read model can report
WHEN:
its layout rules, failure handling, withheld rendering and view state are inspected
THEN:
the layout reflows on a narrow viewport, a failed read replaces the view with an explicit statement rather than leaving a stale one, a withheld panel is rendered as withheld with its reason and registered gap, an unresolved filter dimension is named, the view state is a session-only variable, and the address bar carries only the page and its typed filters and never the credential

AC-005:
GIVEN:
the autonomous build report, this increment's scope contract, the movable active-increment pointer and the acceptance-test index
WHEN:
the report, the contract, the pointer and the index are read together
THEN:
the report records this increment with the exact base head it stacks on, its own scope contract and its own assertion count, the pointer resolves to a declared contract, every approved criterion is traced to exactly the acceptance test that cites it and mapped to a path of this increment, and the parity row stays OUTSTANDING

EXPLICITLY OUT OF SCOPE:
- Any change to `app/opip/cockpit/semantic.py`, `app/opip/cockpit/semantic_pages.py` or `app/api/cockpit_semantic.py`. This increment consumes the contract the previous increment published and changes no part of it.
- Any new route, any change to a response shape, and any change to the analytics plane's mounted path set or its exposure pin. The asset is served by the route that already exists.
- Any durable saved view, preference store, cookie, browser or session storage, or any server-side view persistence. The census reserves that boundary to the owner, so this increment offers only a session-only variable and a bookmarkable non-secret address bar, and it does not present them as saved-view parity.
- Any new page, panel, metric, formula, registry, projection, store, table, scheduler, writer, migration or durable artifact, and any second P&L or population calculation.
- Any headless-browser end-to-end claim. No browser harness runs in this repository, so this increment asserts the asset's semantics and states plainly that it is not an operator journey in a real browser.
- Any census criterion. All 54 stay deferred and unclaimed in the increment that carries them; this increment approves criteria of its own only.
- Any trading, admission, protection, exit, notification, Telegram, promotion, deployment, release-profile or production authority change, and any control that implies execution authority.

FROZEN BOUNDARIES:
- The F10-A semantic read service, its page and panel registries, its declared filter contract and the versioned metric registry stay the only fact, page, panel, filter and metric-semantics authorities. The asset formats, filters and navigates; it computes nothing.
- The asset holds no credential beyond the value the operator typed, sends it only as a request header, and never places it in a URL, a log or a rendered value.
- The analytics plane remains the only mounting site; the mounted path set and the exposure pin are unchanged.
- The eight frozen pages, their questions, their panels and their order keep the census's identity.
- Pages remain unclaimable; parity stays NOT_VERIFIED with zero verified pages, projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified.
- USD and USDT remain distinct portfolios; the surface never presents a joined or summed figure.
- Legacy surfaces are untouched, and the earlier increments' contracts, criteria and pinned surfaces are unchanged.
- The project's standing controls on risk sizing, minimum reward-to-risk, position verification, execution safeguards, AI non-authority, paper isolation and deployment authorization are neither relaxed nor referenced by any logic here.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_e_presentation.py::test_ac_001_the_served_asset_is_the_reviewed_file_and_drives_no_state_of_its_own
AC-002 -> tests/test_opip_r5_f10_e_presentation.py::test_ac_002_navigation_breadcrumbs_and_evidence_links_are_labelled_elements
AC-003 -> tests/test_opip_r5_f10_e_presentation.py::test_ac_003_keyboard_focus_and_announced_state
AC-004 -> tests/test_opip_r5_f10_e_presentation.py::test_ac_004_responsive_failure_and_session_only_view_state
AC-005 -> tests/test_opip_r5_f10_e_presentation.py::test_ac_005_the_report_the_pointer_and_the_criteria_agree

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.html
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_presentation.py
AC-002 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.html
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_presentation.py
AC-003 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.html
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_presentation.py
AC-004 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.html
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_presentation.py
AC-005 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-E-operator-interaction-presentation.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_presentation.py

DEFERRED DISCOVERIES:
- The accessibility work was deliberately separated from the read contract. A single increment asserting both a filter algebra and a viewport layout would have had to either restyle a surface inside a contract change or claim presentation work it did not do; splitting them keeps each increment's evidence about one artifact.
- No headless browser runs in this repository, so the requirement for operator-validation evidence is met honestly rather than by relabelling: this increment asserts the asset's semantics, names itself a structural assertion, and points at the HTTP journeys that carry the behavioural half. The tooling gap is recorded rather than papered over.
- The asset's controls are generated from the published catalog so that a control cannot offer a filter the service would refuse. The cost is that a dimension the census names but the service rejects has no control at all, and the panel that declares it says so instead.
- The address bar is written with a non-secret query so a view can be bookmarked, shared and reloaded. This is deliberately not called a saved view: nothing persists on the server, nothing survives outside the tab's own history, and durable saved views remain blocked behind an owner decision.
- A failed read now replaces the visible page with a statement of the failure. Leaving the previous rendering in place would have shown an answer to the previous question beside an error for the current one, which is the stale-evidence failure the census forbids, in presentation form.
- The asset keeps its storage-free invariant by construction rather than by convention: the delivered increments assert it, and this increment's own criterion restates it, so a later convenience that reintroduced browser persistence would fail two suites rather than one.
- The asset's inputs accept one value per line for a dimension that may be repeated, and the catalog marks which dimensions those are. The service still decides whether a repeat widens or is refused; the control only describes what the contract allows, and an out-of-vocabulary value is refused by the service rather than filtered out by the page.
- The order in which the asset builds a query is the order the panel's drill target published, so the link an operator copies points at exactly the scope the reading was answered under.

UNAPPROVED SCOPE CHANGES:
NONE
