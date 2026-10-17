INCREMENT:
ATDD-R5-F10-dashboard-parity-implementation

OWNER-APPROVED INTENT:
This is the follower increment the F10 source census declares for page implementation, interaction contract and per-page parity proof: ``FOLLOWER: ATDD-R5-F10-dashboard-parity-implementation | F10-B..F10-E``. This contract records the increment's scope for its first slice, mission step F10-B, inside frozen architecture v1.5.0: the page read model over the F10-A semantic read service, the read router that mounts that service's declared read routes on the read-only analytics plane, and the served read page for System Pulse, Discovery and Regime, and Decision Trace. The owner-approved F10 mission authorizes the cockpit implementation within those boundaries; this document records that scope and creates no authority of its own.

What changes where. On the analytics plane this increment adds one presentation read model, one read router and one static read page, and mounts that router beside the existing read-only B/C-4 cockpit router: an owner-visible exposure widening recorded here and pinned by the exposure regression suite. On the trading host nothing changes; no projection, store, table, writer, scheduler, migration or durable artifact is added or edited. In shadow and offline learning planes nothing changes. Trading, admission, protection, risk, Telegram, promotion, deployment and release authority are untouched: the slice is read-only over the same replica, behind the same fail-closed secret gate, and it can override no deterministic gate.

The census's verdicts are carried, not rewritten. All eight pages stay unclaimable on this plane, the parity predicate stays NOT_VERIFIED with zero verified pages and per-page projection authority stays UNDECIDED, the threshold policy stays unratified, per-page counts-and-P&L reconciliation stays NOT_RECORDED, and every panel criterion the census plans for this increment stays deferred rather than satisfied. Those planned criteria are enumerated verbatim under EXPLICITLY OUT OF SCOPE, so none is silently dropped and none is silently claimed.

KEY LAW - A VALUE IS ONLY EVER PUBLISHED WHERE THE SERVICE SAYS ONE MAY BE. The F10-A service remains the single fact authority. Every panel disposition is read from the service's own registry, every withheld panel keeps the producer's own reason and registered gap, and a missing value is never rendered as a zero, an empty collection or an omitted key. Nothing here restates a metric formula or adds a second registry.

KEY LAW - A PAGE IS NOT A CLAIM OF PARITY. Rendering a panel that the service declares producible says nothing about whether the page matches the frozen expectation. Only the parity predicate may claim a page, and it claims none here.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md section 15: the F10 feature row, its EXTEND disposition, and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md: the frozen eight-page inventory, the panel map with each panel's planned criterion, the declared evidence gaps, the declared absences, the unratified threshold policy, and the follower increment declaration this contract implements. The census is not modified by this increment.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md: the parity predicate and the NOT_VERIFIED state this increment must not disturb.
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md F10 row: the target authority of one semantic read service, the cutover gate of fixture counts-and-P&L reconciliation, and the rename blocker.
- docs/atdd/scope-contracts/ATDD-R5-F10-semantic-read-service.md: the F10-A increment that produced the semantic read service, the typed filter vocabulary and the metric bindings this read model reads through, and that deliberately left the declared routes unmounted.
- app/opip/cockpit/semantic.py: the single fact authority, its page and panel registries, its dispositions, its declared route set and its declared mounting posture.
- app/opip/profit_intelligence/lineage.py: the existing execution-lineage projection the decision panels read, reused rather than rebuilt.
- app/api/cockpit.py and tests/test_bc4_cockpit_exposure_v1.py: the analytics plane's single replica read path, its fail-closed secret gate and the pinned mounted path set this increment widens deliberately.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the F10-A semantic read service over the reconciled paper ledger and the portfolio projection, and the page read model built over it for the three pages of the first slice
WHEN:
every panel of every page of the slice is read, with and without values, and a page or panel outside the slice is requested
THEN:
the read model publishes a value only for a panel whose disposition the service declares served and only when that panel's declared readings are all available, returns every other panel withheld or unavailable with the service's own state, reason, evidence gap and declared facts and with no value at all, refuses a page or panel outside the slice instead of half-rendering it, keeps every page's state, reasons and claimability exactly as the service reports them, preserves the registry's panel order and the service's page descriptor, substitutes no zero, empty collection or omitted key for evidence this plane cannot produce, and fails closed at construction and at import on a declaration it would misread

AC-002:
GIVEN:
the read router, the semantic read service's declared route set, and the analytics-plane application that mounts it
WHEN:
the router's mounted paths, route methods, accepted parameters and imports are inspected, and an unsupported query parameter, unknown subject or malformed filter value is presented
THEN:
the router mounts exactly the service's declared read routes plus its own page asset, every mounted route is a GET, the accepted parameter set is the service's typed filter allowlist plus bounded paging, an unsupported parameter or subject is refused as a client error rather than silently ignored or answered with an empty reading, the service module itself still mounts nothing and declares an empty mounted path set, and the router reaches only the read-only replica path, the semantic read service and the page read model, never an exchange, order, Telegram, scheduler, learning, writer or canonical module

AC-003:
GIVEN:
the page asset the read router serves and the reviewed file on disk
WHEN:
the asset text, its route and the bytes actually served are inspected
THEN:
the asset is served as one static GET of exactly the reviewed file, it persists nothing in the browser through local or session storage, cookies or IndexedDB, it names only this plane's own semantic read prefix, and it holds no order, exchange, promotion, release or mounting control

AC-004:
GIVEN:
the frozen source census with the criteria it plans for this increment, this contract, the parity specification and this increment's own acceptance test module
WHEN:
the planned criteria, the approved criteria, the deferred criteria, the traceability block and the implementation map are compared
THEN:
every planned criterion appears either as an approved criterion of this contract or verbatim as a deferred PLANNED entry under EXPLICITLY OUT OF SCOPE, no approved criterion has the identifier of a planned criterion so no planned panel-parity criterion can be claimed as satisfied here, every approved criterion carries at least one acceptance test trace and at least one implementation entry naming this increment's own files, this module cites no criterion outside the approved set, the installed contract stays parseable in the frozen section vocabulary with UNAPPROVED SCOPE CHANGES set to NONE, and the parity predicate still reads NOT_VERIFIED with zero verified pages

AC-005:
GIVEN:
the analytics-plane application, the semantic read service's declared mounting posture, the read model's declared mounted surface and the exposure regression tests that own the pinned mounted path set
WHEN:
the mounted path set of the app, the declared posture and the owning pins are inspected
THEN:
the mounted set is exactly the legacy read-only cockpit paths plus this increment's declared read routes and its page asset, the service module still declares MOUNTED false with an empty mounted path set, parity verification stays at zero with the threshold policy unratified, the read model publishes a mounted surface naming the mounting module, the mounting app and the authorizing increment, and the exposure regression tests pin the exact widened path set, the added router's exact import set and the narrowed rule the read-service increment's own test keeps for that one mounting site

AC-006:
GIVEN:
the autonomous build report, this increment's scope contract, the movable pointer and the acceptance-test index
WHEN:
the report, the contract, the pointer and the index are read together
THEN:
the report records the two increments this one stacks on by their own commits with the Linux CI result of the head it stacks on, records this increment as delivered with its acceptance-test count and names the entry carrying its own branch, commit and CI result, leaves the eight-page parity row OUTSTANDING with the parity predicate NOT_VERIFIED and R7 unstarted, the pointer resolves to this increment's contract, and every approved criterion is traced to a matching acceptance test and mapped to a changed path

EXPLICITLY OUT OF SCOPE:
- Parity in every form. No page is claimed, verified or declared ready by this increment: the parity predicate stays NOT_VERIFIED, the verified-page count stays zero, and per-page projection authority stays UNDECIDED.
- Every criterion the census plans for this increment. Each is carried forward verbatim below so that none is silently dropped, none is silently satisfied, and none may leave this list without an owner-approved contract change:
  cross-page and service rows: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-010; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-100; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-101; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-102; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-105;
  P1 System Pulse panels: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-020; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-021; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-022; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-023; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-024;
  P2 Discovery and Regime panels: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-030; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-031; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-032; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-033; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-034; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-035;
  P3 Decision Trace panels: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-040; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-041; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-042; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-043; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-044; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-045;
  P4 Paper Portfolio panels: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-050; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-051; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-052; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-053; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-054; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-055; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-056;
  P5 Quality and Missed Opportunity panels: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-060; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-061; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-062; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-063; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-064; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-065; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-066;
  P6 Committee Value and AI Cost panels: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-070; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-071; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-072; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-073; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-074;
  P7 Learning and Releases panels: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-080; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-081; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-082; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-083; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-084; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-085;
  P8 Incidents and Infrastructure panels: PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-090; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-091; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-092; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-093; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-094; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-095; PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-096;
- Any change to the source census, the parity specification, the eight-page inventory, the panel map, the panel and fact vocabulary or the versioned metric registry. They are references this increment reads, never files it edits.
- Any new page, panel, metric, formula, registry, projection, store, table, scheduler, writer, migration or durable artifact, and any second replica read path beside the one the analytics plane already owns.
- Any change to the pages this slice does not serve. P4 to P8 stay undelivered here, and no page outside the slice may be rendered, stubbed or declared.
- Any trading, admission, protection, exit, notification, Telegram, promotion, deployment, release-profile, learning or production authority change, and any widening of the analytics plane's mounted path set beyond the surface approved by this contract.
- Any ratified threshold, tolerance, gate value, sample size or cost bound, and any favorable rendering of missing, stale, contradictory or unsupported evidence.

FROZEN BOUNDARIES:
- The F10-A semantic read service, its page and panel registries, its metric bindings and the versioned metric registry stay the only fact and metric-semantics authorities. A second registry, a local definition copy or a restated formula is a defect.
- The slice is read-only by construction: it opens no writer, takes no lock, holds no exchange credential, schedules nothing and reuses the analytics plane's single replica read path and its fail-closed secret gate rather than adding a second reader.
- The analytics plane is the only mounting site. Nothing here reaches the trading host, installs a database where the trading host lives, or widens the mounted path set beyond the routes declared here and pinned by the exposure regression suite.
- The eight frozen pages, their questions and their panels keep the census's identity: no page is added, removed, renamed, split, merged or reordered.
- Pages are unclaimable on this plane, parity stays NOT_VERIFIED with zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED, and the threshold policy stays unratified.
- USD and USDT remain distinct portfolios; nothing here sums, joins or totals across them.
- Legacy surfaces are untouched: the existing read-only cockpit routes, their responses and the legacy outcomes cockpit keep working exactly as before, and the slice adds routes beside them rather than changing them.
- The project's standing controls on risk sizing, minimum reward-to-risk, position verification, execution safeguards, AI non-authority, paper isolation and deployment authorization are neither relaxed nor referenced by any logic here.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_dashboard_parity_implementation.py::test_ac_001_the_read_model_publishes_a_value_only_where_the_service_allows_one
AC-001 -> tests/test_opip_r5_f10_dashboard_parity_implementation.py::test_ac_001_a_slice_page_keeps_the_services_own_state_and_is_refused_outside_the_slice
AC-002 -> tests/test_opip_r5_f10_dashboard_parity_implementation.py::test_ac_002_the_read_router_mounts_only_the_declared_get_surface
AC-002 -> tests/test_opip_r5_f10_dashboard_parity_implementation.py::test_ac_002_unsupported_filters_and_subjects_fail_closed
AC-003 -> tests/test_opip_r5_f10_dashboard_parity_implementation.py::test_ac_003_the_page_asset_is_static_secret_free_and_authority_free
AC-004 -> tests/test_opip_r5_f10_dashboard_parity_implementation.py::test_ac_004_every_census_criterion_of_this_increment_is_approved_or_deferred
AC-005 -> tests/test_opip_r5_f10_dashboard_parity_implementation.py::test_ac_005_the_mounted_path_set_and_the_mount_posture_stay_pinned
AC-006 -> tests/test_opip_r5_f10_dashboard_parity_implementation.py::test_ac_006_the_report_the_pointer_and_the_criteria_agree

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_implementation.py
AC-002 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.py
AC-002 -> OHM-Trade-Agent-v1/app/api/cockpit_service.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-003 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.html
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_bc4_cockpit_exposure_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_semantic_read_service.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_profit_intelligence_dashboard_v2.py
AC-006 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_implementation.py

DEFERRED DISCOVERIES:
- This first slice serves P1, P2 and P3 because those are the pages whose remaining evidence the analytics plane is closest to holding; the census's other pages need trading-host and learning-worker facts that no read path on this plane can reach, so they stay undelivered rather than stubbed.
- "Served" here means the service declares the fact producible on this plane, not that a number exists: the read model's own trust, reconciliation and attention readings carry a served state with a null value and an explicit reason, and the page renders that distinction instead of collapsing it.
- Decision-grain panels over a replica whose rows retain no decision context answer unavailable with the producer's own reason (CANONICAL_PROJECTION_UNREADABLE) instead of joining lineage rows heuristically. Recording decision context on the reconciled rows would let those panels publish a value without a new join, and that is a writer-side decision, not a read-side one.
- Mounting the declared routes widened the analytics plane's externally reachable surface. That widening is recorded here, authorized for this increment alone, and pinned in the exposure regression suite, which now also pins the added router's exact import set; a future widening fails there rather than passing unnoticed.
- The page asset keeps no browser storage and no order control. Which secret the plane accepts, and how a caller presents it, stays the existing cockpit exposure criterion's business and is not restated by this increment.
- The read model's mounted-surface declaration names the mounting module and the authorizing increment as data, so a reader never has to infer the mounting posture from a comment.
- Parity is still NOT_VERIFIED with zero verified pages: proving parity for one panel needs the counts-and-P&L fixture reconciliation the conformance ledger's F10 cutover gate describes, and that proof is a separate owner-visible step from serving a reading.

UNAPPROVED SCOPE CHANGES:
NONE
