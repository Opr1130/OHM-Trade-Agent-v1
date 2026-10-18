INCREMENT:
ATDD-R5-F10-E-operator-interaction

OWNER-APPROVED INTENT:
This is the interaction increment of the owner-approved F10 mission, inside frozen architecture v1.5.0. It is the first half of the operator-interaction capability: the part a caller can be held to over HTTP. The presentation half -- breadcrumbs, filter controls, preset buttons, session-only view state, keyboard accessibility, responsive rendering and the asset's own error states -- follows as the second half under its own contract, because a served asset is a different artifact from a read contract and one increment cannot honestly assert both at once.

What changes where. On the analytics plane this increment teaches the typed filter contract to express OR within a dimension and to refuse to resolve a repeated dimension to its last value; it publishes the declared filter catalog and the declared analysis presets on the page index; and it gives every page and every panel a resolvable evidence link that carries the filters it was read under. On the trading host, the learning plane, the shadow plane and the offline plane nothing changes. No route is added: the catalog, the presets and the links all ride on routes the first slice already mounted, so the analytics plane's externally reachable surface is unchanged and the exposure pin stays exactly as delivered.

The defect this increment repairs is a silent scope change rather than a missing feature. The declared filter contract says a request is answered under the scope it stated, and that an unsupported or impossible filter is refused rather than absorbed. Reading a repeated query dimension with a single lookup keeps only the caller's last value, so a request naming two instruments was answered about one of them and the other was dropped without a word: a differently-scoped answer under the caller's assumed scope. Repeated dimensions are now read as all of their values, the service decides whether a repeat widens or is refused, and only the dimensions that can mean OR accept one.

The census's verdicts are carried, not rewritten. The census records this semantics as a planned criterion of the semantic-read-service and dashboard increments; neither is modified and neither claims it here. All eight pages stay unclaimable, the parity predicate stays NOT_VERIFIED with zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified. This contract approves only criteria of its own.

KEY LAW - A STATED VALUE IS NEVER DROPPED. Every value a caller states reaches the answer or is refused by name. A dimension that cannot widen is refused when it is stated twice, and two different spellings of one dimension that disagree stay refused as ambiguous, because the service must never choose which of two scopes the caller meant.

KEY LAW - A PRESET IS A QUESTION, NOT A RESULT. A preset names filters the service declares and the declared facts that answer it. It carries no value of its own and it evaluates by being followed: the panels' own states and reasons are how insufficiency is reported, so a preset can never assert a result the reading could not publish.

KEY LAW - A LINK ASKS THE SAME QUESTION. Every drill target carries the effective filters the reading was answered under, minus the paging controls, which are transport rather than evidence identity. Following a link therefore keeps the operator inside the question they asked instead of silently widening it.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md section 15: the F10 Dashboard feature row, its EXTEND disposition, its typed-filter and cross-filter obligations, and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md sections 4, 6, 8, 11 and 12: the filter and cross-filter rows (`F10-FLT-01`, `F10-FLT-02`, `F10-FLT-04`), the semantic response contract, the panel drill targets, the integrity rules and the unratified threshold policy. The census is not modified by this increment.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md sections 5, 6 and 7: the parity predicate this increment must not appear to satisfy, the fail-closed negative cases, and the threshold policy that stays unratified.
- docs/atdd/scope-contracts/ATDD-R5-F10-semantic-read-service.md: the increment that declared the typed filter contract, the vocabulary and the routes this increment extends without adding to.
- docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md: the delivered first slice whose mounted surface this increment reuses unchanged.
- docs/atdd/scope-contracts/ATDD-R5-F10-C-paper-portfolio-quality.md and docs/atdd/scope-contracts/ATDD-R5-F10-D-trust-learning-incident-pages.md: the increments whose served page sets this increment links across.
- docs/atdd/evidence/F10_CRITERION_MATRIX.md: the criterion matrix whose cross-filter and preset rows this increment's evidence changes.
- app/opip/cockpit/semantic.py: the single fact authority whose typed filter contract this increment extends and whose declared vocabularies the catalog publishes.
- app/opip/cockpit/semantic_pages.py: the read model that declares the presets, the catalog projection and the drill targets.
- app/api/cockpit_semantic.py: the mounted read router whose query parsing this increment fixes.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the mounted read surface over a known population of reconciled lifecycles
WHEN:
a population is read unfiltered, with one value in one dimension, with that dimension stated twice, with two dimensions, and with both at once
THEN:
the population narrows to exactly the rows the stated algebra selects, with AND across dimensions and OR within a dimension, every reading echoes the scope it was answered under, and the two quote currencies are never summed or joined

AC-002:
GIVEN:
the mounted read surface and the service's typed filter contract
WHEN:
a dimension is stated more than once and can widen, a dimension that cannot widen is stated twice, a repeated value equals itself, two spellings of one dimension disagree, and a dimension is stated as an empty list
THEN:
every stated value of a widening dimension reaches the answer, a repeated non-widening dimension is refused with a reason that names the rule, an ambiguous pair of spellings stays refused as conflicting, an idempotent repeat collapses to the scalar, and an empty statement is refused

AC-003:
GIVEN:
the page index, the service's declared filter contract and the read model's declared presets
WHEN:
the published catalog and presets are compared with those declarations and every preset's link is followed
THEN:
the catalog is exactly the declared dimensions in declared order with their declared vocabularies, constrained fields and widen-capability, every preset names declared dimensions, in-vocabulary values, a declared page and declared facts, following a preset resolves to a real page read, and a preset whose population this plane cannot produce reports insufficiency instead of a result

AC-004:
GIVEN:
a page read under a filter, the drill targets that page and its panels publish, and a paged row fact
WHEN:
the page's and a panel's links are followed, the watermarks are compared across the index, the page and the drilled evidence, and the paged fact is walked twice
THEN:
a followed link asks the same question under the same watermark, the evidence reading reports the filters the page was read under, and two identical walks return the same rows with no duplicate and no omission across pages

AC-005:
GIVEN:
the mounted read surface, its secret gate and the routes it declares
WHEN:
a request arrives without the secret, with a wrong secret, with an unsupported dimension, with an out-of-vocabulary value, with a malformed paging or window value, for an unknown subject, as a write, and when the replica is unreadable
THEN:
each refusal is a client error that names its reason, no write reaches a read route, an unreadable replica answers unavailable with no value rather than a zero, and a failed page still carries every panel it declares

AC-006:
GIVEN:
the autonomous build report, this increment's scope contract, the movable active-increment pointer and the acceptance-test index
WHEN:
the report, the contract, the pointer and the index are read together
THEN:
the report records this increment with the exact base head it stacks on, its own scope contract and its own test count, the pointer resolves to a declared contract, every approved criterion is traced to exactly the acceptance test that cites it and mapped to a path of this increment, and the parity row stays OUTSTANDING

EXPLICITLY OUT OF SCOPE:
- The presentation half of the interaction contract, which is the next increment: breadcrumbs, filter controls and their AND/OR affordances, preset buttons, session-only view state, deep links rendered as links, keyboard focus management, ARIA labelling and table relationships, responsive layout, and the asset's own loading, stale, withheld and error states. This increment serves the contract those controls will be built on; it does not restyle the surface.
- Any change to the served page asset `app/api/cockpit_semantic.html`. The storage-free, authority-free invariants the delivered increments assert for that asset are unchanged.
- Any new route, any widening of the analytics plane's mounted path set, and any change to the exposure regression suite's pinned surface. The catalog, the presets and the drill targets ride on the existing routes.
- Any durable saved view, preference store, cookie, browser or session storage. The census records that a saved view may not be added outside an approved non-trading preference boundary, so durable saved views stay blocked and this increment adds no store.
- Any new page, panel, metric, formula, registry, projection, store, table, scheduler, writer, migration or durable artifact, and any second P&L or population calculation. A filter narrows the service's own population; it never recomputes one.
- Any census criterion. All 54 stay deferred and unclaimed in the increment that carries them; this increment approves criteria of its own only.
- Any trading, admission, protection, exit, notification, Telegram, promotion, deployment, release-profile or production authority change.

FROZEN BOUNDARIES:
- The F10-A semantic read service, its page and panel registries, its declared filter vocabulary and the versioned metric registry stay the only fact, page, panel, filter and metric-semantics authorities. This increment extends the declared filter contract; it does not add a second one.
- A filter narrows the service's own population. Nothing here recomputes a count, a ratio or P&L, and no aggregate is assembled from per-value reads.
- The analytics plane remains the only mounting site; the mounted path set is not widened and the exposure pin is unchanged.
- The eight frozen pages, their questions, their panels and their order keep the census's identity.
- Pages remain unclaimable on this plane; parity stays NOT_VERIFIED with zero verified pages, projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified. No row of the criterion matrix is moved to certified by this increment.
- USD and USDT remain distinct portfolios; a filter never joins or sums them, and a currency question is answered per currency.
- The page asset keeps its storage-free and authority-free invariants, and the legacy cockpit is untouched.
- The project's standing controls on risk sizing, minimum reward-to-risk, position verification, execution safeguards, AI non-authority, paper isolation and deployment authorization are neither relaxed nor referenced by any logic here.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_e_operator_interaction.py::test_ac_001_and_across_dimensions_and_or_within_a_dimension
AC-002 -> tests/test_opip_r5_f10_e_operator_interaction.py::test_ac_002_a_stated_value_is_never_silently_dropped
AC-003 -> tests/test_opip_r5_f10_e_operator_interaction.py::test_ac_003_the_filter_catalog_and_the_presets_come_from_the_declarations
AC-004 -> tests/test_opip_r5_f10_e_operator_interaction.py::test_ac_004_drill_links_preserve_context_and_paging_stays_deterministic
AC-005 -> tests/test_opip_r5_f10_e_operator_interaction.py::test_ac_005_every_refusal_and_failure_mode_is_explicit
AC-006 -> tests/test_opip_r5_f10_e_operator_interaction.py::test_ac_006_the_report_the_pointer_and_the_criteria_agree

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_operator_interaction.py
AC-002 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_operator_interaction.py
AC-003 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_operator_interaction.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_operator_interaction.py
AC-005 -> OHM-Trade-Agent-v1/app/api/cockpit_semantic.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_operator_interaction.py
AC-006 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F10_CRITERION_MATRIX.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-E-operator-interaction.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_e_operator_interaction.py

DEFERRED DISCOVERIES:
- The defect this increment repairs was found while writing the journey tests, not while reading the code: the declared filter contract promises that a request is answered under the scope it stated and that an unsupported filter is refused, and the router contradicted that promise by reading a repeated dimension with a single lookup. A request naming two instruments was silently answered about one, which is precisely the failure mode the service's own docstring says it must never have. The workaround that "already worked" was that no surface had yet offered a repeatable control.
- OR within a dimension was recorded by the census as a capability of the semantic-read-service increment (`F10-FLT-01`) and was never delivered; the contract was single-valued per dimension. This increment supplies the semantics because the interaction contract cannot express "compare these two instruments" without it. The census criterion stays deferred and unclaimed in the increment that owns it, and the matrix records the functional state rather than transferring ownership.
- Widening the value type was rejected in favour of a distinct collection type that cannot hold one member. A dimension stated once therefore keeps exactly the shape it had before, the delivered acceptance tests for the filter contract pass unmodified, and no consumer has to handle two spellings of one scope.
- Refusing a repeated non-widening dimension was chosen over ignoring the repeat and over taking the last value. Ignoring it would answer a scope the caller did not state, and taking the last value is the defect itself; a paging control or a window edge stated twice describes no single coherent request.
- An alias that disagrees with its canonical spelling is still refused as a conflict, because two different *names* for one dimension are ambiguous in a way that one name stated twice is not. The delivered acceptance test that pins this was left untouched rather than adjusted to fit the new behaviour.
- A preset carries no value and is evaluated by following it. Giving a preset a server-side result would have created a second place where a number about a population is computed, and the panels already report their own insufficiency with the producer's reason.
- The presentation half of the interaction contract is deliberately not in this increment. A served asset is a different artifact from a read contract: the controls, the breadcrumbs, the session-only view state and the accessibility assertions belong with the asset, and asserting them here would have meant either restyling the surface in a contract increment or claiming presentation work that was not done.
- Durable saved views remain blocked rather than approximated. The census records that a saved view may not be added outside an approved non-trading preference boundary, so no increment in this mission may add the store it would need, and this increment adds none.
- The three criteria the matrix recorded as not implemented are reduced to one by this increment: the cross-filter, drill-down and deep-link criterion and the preset criterion now have a delivered capability and an asserting test, so their disposition and the matrix totals move. The remaining not-implemented row concerns a projection-failure panel whose fact is on the plane but whose panel no increment has declared, and it stays recorded as an undeclared owner rather than inheriting one.

UNAPPROVED SCOPE CHANGES:
NONE
