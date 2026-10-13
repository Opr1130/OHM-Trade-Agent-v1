INCREMENT:
ATDD-R5-F10-semantic-read-service

OWNER-APPROVED INTENT:
This is the F10-A increment of the owner-approved F10 mission: the unified decision cockpit, semantic read service and decision-first Telegram presentation inside frozen architecture v1.5.0. The owner approved F10 functionality and implementation within those frozen boundaries, while retaining the eight inherited pages, extending existing semantic and data infrastructure, preserving the legacy outcomes cockpit until verified parity, reusing actual canonical facts with explicit missingness, and keeping all economics, admission, protection, risk and release authority unchanged.

F10-0 recorded the census: the eight frozen pages, the capability classification, the page and panel to fact contract, the required fact grains, the declared evidence gaps and absences, and the unratified threshold policy. F10-A delivers the semantic read service that census prepared: one versioned, read-only service that answers the semantic questions the eight pages ask from the projections that already exist on the read-only analytics plane (the reconciled Paper-v2 ledger and the portfolio/overview projection), plus the typed filter contract, the per-metric response envelope, and a page-level predicate that refuses to claim a page whose required facts this plane cannot produce.

It serves no page. Every page's required facts include evidence that is not on this plane, so all eight pages are declared unclaimable with an explicit reason while the individual facts that are available stay reachable through the metric and fact reads. It mounts nothing: the routes are declared, deliberately absent from both applications, and unreferenced by any module under app/api/, because the analytics plane's mounted path set is pinned by an existing exposure contract and widening it is an owner-visible decision. No route, page, projection, metric, store, scheduler or writer is added; no legacy surface is touched; and no trading, Telegram, admission, promotion, deployment, release-profile or production authority is created or widened. Its deliverable surface is four paths: the active-increment pointer, this contract, one service module under app/opip/cockpit/, and one test module.

KEY LAW - ONE SEMANTIC MODEL, ONE METRIC REGISTRY. The versioned registry app.opip.contracts.paper_metrics remains the only metric-semantics authority. A response references a registry entry (identifier, definition version, declared population, exclusions, unit, uncertainty requirement, source); it never restates a formula. A second registry, a local definition copy or a restated formula is a defect, not an optimization.

KEY LAW - DECLARATION IS NOT ACTIVATION. Declaring a route, a page or a metric changes no reachable surface, closes no gap, ratifies no threshold and claims no parity.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md section 15: the F10 feature row, disposition EXTEND, and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md: the frozen eight-page inventory, the panel map, the fact grains, the registered evidence gaps, the declared absences and the unratified threshold policy this increment implements against and does not modify.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md: the exact parity predicate and the NOT_VERIFIED parity state this increment must not disturb.
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md F10 row: the target authority of one semantic read service, the cutover gate of fixture counts-and-P&L reconciliation, and the rename blocker.
- app/opip/contracts/paper_metrics.py: the versioned registry (paper-metrics-v1) that is the only metric-semantics authority.
- app/opip/cockpit/ledger.py and app/opip/cockpit/portfolio.py: the reconciled ledger and portfolio projections every served value is read from, and the only producers this service binds to.
- app/opip/cockpit/trust.py: the independent trust dimensions and the unavailable envelope the responses carry instead of a collapsed health score.
- app/opip/contracts/paper_outcome.py: the USD/USDT quote-currency separation that forbids a cross-currency total.
- app/opip/profit_intelligence/semantics.py: the registered evidence-gap facts a blocked fact may cite.
- docs/atdd/README.md and tests/atdd_scope.py: the scope mechanism this contract is written against.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the service's declared contract and the versioned metric registry
WHEN: the frozen page inventory, the metric set and a served metric response are
inspected
THEN: the service declares exactly the eight frozen pages and exactly the registry's
metrics, references the registry without restating a formula, and pins the versions
it was built against
AC-002:
GIVEN: a healthy reconciled ledger and an unreadable one
WHEN: every registered metric, every page and the blocked panels are read
THEN: a fact this plane cannot produce carries an explicit state, a reason and, where
one is registered, a gap identifier - never a value, an empty collection or a
favourable stand-in, and a page whose required facts are not producible serves no
page value
AC-003:
GIVEN: the declared filter allowlist, its vocabularies, its aliases and the declared
row field subsets
WHEN: supported filters, an undeclared filter, a declared-but-unsupported filter and
out-of-vocabulary values are sent, and a row population is paged
THEN: supported filters narrow the population and are echoed as effective, a window
excludes and counts rows it cannot place, a cursor is bound to its watermark, and
every other request is refused rather than silently ignored
AC-004:
GIVEN: the declared read routes, the analytics API package and the service source
WHEN: the mounted surface and the source are inspected
THEN: nothing is mounted, no API module names or mounts a semantic route, and the
source imports and contains no HTTP, write, order, exchange or release-authority path
AC-005:
GIVEN: a reconciled ledger and the portfolio projection built from it
WHEN: the declared populations and the currency-scoped metrics are read, and a
projection count is made to disagree with its own rows
THEN: every count reconciles at one watermark per currency, a disagreement is
published as PROJECTION_COUNT_DOES_NOT_RECONCILE, and USD and USDT stay separate
portfolios with no cross-currency total
AC-006:
GIVEN: the service's contract, every kind of reading it can produce, and the census
and parity documents of the preceding increment
WHEN: the threshold policy, the parity state and the authority fields are inspected
THEN: no threshold is ratified, parity stays NOT_VERIFIED with zero verified pages
and projection authority stays UNDECIDED, and the preceding documents are unchanged

EXPLICITLY OUT OF SCOPE:
- Mounting any route. The semantic read routes are declared and not mounted; adding them to either application is a separate, owner-visible exposure decision.
- Implementing any page. Page implementation, the interaction contract and per-page parity proof are the follower increment's obligation; this increment claims no parity and verifies zero pages.
- Recording per-page counts-and-P&L reconciliation against one declared projection authority at one watermark. The service recounts populations and publishes the comparison, which is not that proof.
- Any new projection, store, scheduler, cache, migration, worker or second metric registry.
- Plan, capital, horizon, Committee, learning, incident, AI-cost, regime, missed-opportunity, forecast, ranking or decision-trace reads: the evidence is not on this plane and this increment does not approximate it.
- Any marked (unrealized) valuation, any cross-currency total, and any threshold, tolerance, capacity, sample-size or cost bound.
- Any change to notification, Telegram, alerting, admission, protection, execution, release-profile, promotion or deployment behaviour.

FROZEN BOUNDARIES:
- The eight pages are frozen exactly as the parity specification records them.
- The versioned metric registry remains the only metric-semantics authority; a second registry or a restated formula is forbidden.
- The reconciled ledger and the portfolio projection remain the only producers this service binds to; it adds no second source of truth.
- The service is read-only by construction: it takes no lock, opens no writer, holds no credentials and has no route to an exchange, an order path, protection, admission, notification or release.
- USD and USDT remain distinct portfolios; nothing here sums, joins or totals across them.
- The five-value census vocabulary, the independent trust dimensions and the registered evidence gaps are reused, never redefined or extended.
- The declared surface stays unmounted until an explicitly owner-approved exposure change says otherwise.
- No numeric threshold, tolerance, gate value, sample size or cost bound is ratified.
- Parity stays NOT_VERIFIED with zero verified pages and per-page projection authority stays UNDECIDED.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_001_one_versioned_model_over_the_frozen_inventory_and_registry
AC-001 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_001_inventory_totals_and_panel_totality_are_frozen
AC-001 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_001_the_projection_is_built_at_most_once_per_service
AC-002 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_002_unserved_facts_and_pages_are_explicit_never_favorable
AC-002 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_002_a_served_state_is_only_ever_reached_with_its_value
AC-002 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_002_an_empty_projection_withholds_rather_than_zeroes_a_portfolio
AC-003 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_003_typed_filters_are_enforced_and_never_silently_ignored
AC-004 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_004_declaration_mounts_nothing_and_adds_no_authority
AC-005 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_005_counts_reconcile_with_the_projection_per_currency
AC-005 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_005_a_metric_free_of_a_currency_requirement_is_answered_without_one
AC-006 -> tests/test_opip_r5_f10_semantic_read_service.py::test_ac_006_thresholds_parity_and_authority_are_preserved

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-semantic-read-service.md
AC-001 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_semantic_read_service.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_semantic_read_service.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_semantic_read_service.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_semantic_read_service.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_semantic_read_service.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_semantic_read_service.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-semantic-read-service.md

DEFERRED DISCOVERIES:
- The ledger and portfolio projections publish projection versions but no watermark, so the service derives a deterministic watermark from the rows and publishes it with every reading. A canonical watermark on the projections would let a consumer compare readings without recomputing it.
- Both EXISTING_NEEDS_EXTENSION pages (P1 and P4) are unclaimable here for an evidence reason rather than a coding reason: P1 needs scheduler and lock health from the trading host, and P4 needs the frozen PortfolioPlan record as a plan revision. Their panels' readable facts are still served individually.
- Two registered metrics that require an interval (paper.net_expectancy and the two latencies) are withheld rather than point-estimated, because no interval estimator is registered and the threshold policy ratifies none. Registering an estimator is an owner decision, not a read-side one.
- paper.fill_ratio is blocked rather than approximated even though per-trade quantities exist: the registered population is canonical order intents and the replica carries no intent enumeration, so a trade-grain ratio would answer a different question under the registry's name.
- The declared route set follows the existing cockpit path prefix (GET /api/cockpit/...) so the follower increment can mount it without renumbering the API surface; that choice is a declaration, not a mounting.
- Recounting a projection count is not the counts-and-P&L cutover proof the conformance ledger requires; each reading says so explicitly rather than implying the gate is met.
- Two page citations are deliberately reworded rather than copied from the census and parity specification. An existing dormancy guard (R3-F3 AC-010) asserts that no application module outside the owning modules names the shadow detector package, and another (R5-D AC-009) asserts the same for the plan record class. This service consumes neither, so the P2 detector panel names the detector package and P4 cites the frozen plan record by its SC-08 contract identifier. The reworded rows are asserted against the guarded symbols by the increment's own AC-004 test, so the deviation cannot silently widen into a consumption claim.
- The parity specification's REQUIRED_FACTS line for P4 and the census's panel row for the detector producer remain the verbatim authorities; only this module's citation wording differs, and the census is unchanged.
- Implementing the filter contract exposed a completeness defect in this increment's own declarations: three panel-declared filters (candidate, episode, lifecycle status) were neither resolvable nor declared rejected, so a panel rendering silently dropped them. The vocabulary now resolves lifecycle status to the supported lifecycle filter, declares candidate and episode as upstream-plane rejections, and reports any future unresolvable declared filter as FILTER_NOT_DECLARED instead of omitting it. The increment's AC-003 test asserts the totality, so the omission cannot return.

UNAPPROVED SCOPE CHANGES:
NONE

