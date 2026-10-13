# O'Pip F10 dashboard parity specification (contract preparation)

Status: `CONTRACT_PREPARED_NOT_VERIFIED`

Increment: `ATDD-R5-F10-dashboard-parity-contract`

Authority granted by this document: `NONE`

Parity verdict: `NOT_VERIFIED` (0 of 8 pages verified)

This document finalizes the F10 dashboard requirements, their traceability, their
negative cases and the RED acceptance tests that prove the gap is still open. It
prepares the contract for the increment that will later build and prove dashboard
parity. It changes no runtime behaviour, adds no page, route, projection, metric,
service, scheduler or store, removes no legacy surface, and grants no trading,
Telegram, admission, promotion, deployment or production authority. It ratifies no
numeric threshold.

## 1. Adopted requirement (verbatim sources)

F10 is not invented here. It is the adopted requirement recorded in the R5
register and the conformance ledger, expressed in the adopted clauses.

- Register row `F10` (`docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`):
  "Dashboard: same eight pages and semantic model; expose plans, capital and
  horizons", source `v1.5.0`, clause `§15;§13`, status `PARTIAL`, evidence
  `OHM-Trade-Agent-v1/app/opip/cockpit/portfolio.py`, contracts `SC-08,SC-12`,
  open input `OQ-10`, gate `G4`, note "EXTEND; read-only facts only, no favorable
  state from missing evidence".
- Adopted architecture section 15 (`docs/architecture/v1.5.0/ARCHITECTURE.md`,
  feature table): `F10 Dashboard`, disposition `EXTEND`, "P2 alongside A-F: same
  eight pages and semantic model; expose plans, capital and horizons."
- Adopted architecture, retained-milestone clause: "Preserve that older milestone
  as R5-LEGACY-OUTCOMES-COCKPIT in tracking; its F9 consolidation work is a
  prerequisite for trustworthy R5-C/E/F, and its F10 work accompanies each
  evidence increment."
- Adopted architecture, evidence clause: "Unsupported source claims, incomplete
  cost and missing evidence cannot be converted to favorable dashboard states."
- Conformance ledger `F10` row (`docs/architecture/OPIP_CONFORMANCE_LEDGER.md`):
  `ARCHITECTURE_REQUIREMENT` names the eight pages and the shared semantic model;
  `IMPLEMENTATION_STATUS` is `PARTIAL`; `CURRENT_WRITER` states that none of the
  current surfaces is a fact authority; `DUPLICATE_OR_OVERLAPPING_PATHS` is "Four
  surfaces, none covering all eight pages"; `TARGET_AUTHORITY` is "One semantic
  read service over canonical projections"; `CUTOVER_GATE` is "Fixture
  reconciliation of counts and P&L, then retire duplicate pages".
- `SC-08` "Portfolio plan record and candidate comparison": the frozen
  `PortfolioPlan` record and its keys (candidate-panel manifest, portfolio
  version, plan expiry, policy/forecast IDs, alternatives including cash, action
  sequence, expected economics, constraints); owning module
  `app/opip/contracts/portfolio.py`; `FROZEN`.
- `SC-12` "Canonical ordering, temporal contract and shared writer": the frozen
  `CanonicalTemporalRecord` (`(history_epoch, local_sequence)`, source event,
  receipt, availability, evaluation, commit times, hidden wall clock); exactly
  one canonical writer owns durable domain records; "Gate thresholds and cockpit
  retention stay `OQ-07` and `OQ-10`"; `FROZEN`.
- Register gate row `G4`: "Operator usefulness and trace gate: reconciled
  read-only facts and complete decision trace", status `PROPOSED_NOT_FROZEN`,
  open input `OQ-10`, note "usefulness evidence is an operator judgement".
- Register open-input row `OQ-10`: "Operator usefulness questionnaire and legacy
  outcomes cockpit retention", `OWNER_DECISION_REQUIRED`, owner `OWNER`, gate
  `G4`.

## 2. Owner decision recorded by this increment

OWNER_DECISION_RETENTION: The legacy outcomes cockpit is preserved until verified dashboard parity.

Consequences, exactly as approved and no wider:

- The legacy `/dashboard` route and its `/api/analytics/*` consumers stay mounted
  and untouched until parity is verified for the pages they serve.
- Retention does not authorize removal after parity by itself: the retirement
  ledger's own conditions (fixture counts and P&L match canonical projections, a
  consumer census, and the owner's separate retirement decision) still govern.
- The operator usefulness questionnaire half of `OQ-10` remains an owner input.
  It is not answered, pre-filled, sampled or scored by this specification.

## 3. The eight pages (frozen inventory)

The page set is the adopted eight. It is frozen: adding, removing, renaming,
splitting or merging a page requires a new authorized increment, and this
specification carries no authority to do so.

```text
PAGE_ID: P1_SYSTEM_PULSE
ADOPTED_NAME: System Pulse
OPERATOR_QUESTION: is the platform live, fresh and continuous now, and at what watermark?
REQUIRED_FACTS: pipeline liveness and freshness, coverage and gaps, consumed watermark, scheduler and lock health
ADOPTED_EXTENSION: horizons and the capital cycle are visible beside the pipeline that feeds them
CANONICAL_PROJECTION_AUTHORITY: UNDECIDED
COUNTS_AND_PNL_RECONCILIATION: NOT_RECORDED
PARITY_STATUS: NOT_VERIFIED
PROOF_REQUIRED: fixture reconciliation of counts and P&L at one watermark against the declared canonical projection
FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation
```

```text
PAGE_ID: P2_DISCOVERY_REGIME
ADOPTED_NAME: Discovery and Regime
OPERATOR_QUESTION: what is observed, what regime is it in, and what is unobserved?
REQUIRED_FACTS: continuous observation coverage, observed pairs and windows, regime context, explicit unknown coverage
ADOPTED_EXTENSION: coverage and availability are reported as facts, never inferred from silence
CANONICAL_PROJECTION_AUTHORITY: UNDECIDED
COUNTS_AND_PNL_RECONCILIATION: NOT_RECORDED
PARITY_STATUS: NOT_VERIFIED
PROOF_REQUIRED: fixture reconciliation of counts and P&L at one watermark against the declared canonical projection
FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation
```

```text
PAGE_ID: P3_DECISION_TRACE
ADOPTED_NAME: Decision Trace
OPERATOR_QUESTION: why was this admitted, held, reduced, exited or refused, and on what evidence?
REQUIRED_FACTS: complete decision trace per decision, eligible evidence at its own cutoff and watermark, explicit refusal reasons
ADOPTED_EXTENSION: the same trace covers plan revisions, capital reservations and horizon decisions
CANONICAL_PROJECTION_AUTHORITY: UNDECIDED
COUNTS_AND_PNL_RECONCILIATION: NOT_RECORDED
PARITY_STATUS: NOT_VERIFIED
PROOF_REQUIRED: fixture reconciliation of counts and P&L at one watermark against the declared canonical projection
FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation
```

```text
PAGE_ID: P4_PAPER_PORTFOLIO
ADOPTED_NAME: Paper Portfolio
OPERATOR_QUESTION: what does the paper portfolio hold and plan, and what did it earn net of cost?
REQUIRED_FACTS: the frozen PortfolioPlan record (SC-08) as a plan revision, positions and lifecycle, reconciled counts and net economics
ADOPTED_EXTENSION: plans, capital and horizons are exposed together on this page
CANONICAL_PROJECTION_AUTHORITY: UNDECIDED
COUNTS_AND_PNL_RECONCILIATION: NOT_RECORDED
PARITY_STATUS: NOT_VERIFIED
PROOF_REQUIRED: fixture reconciliation of counts and P&L at one watermark against the declared canonical projection
FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation
```


```text
PAGE_ID: P5_QUALITY_MISSED_OPPORTUNITY
ADOPTED_NAME: Quality and Missed Opportunity
OPERATOR_QUESTION: what was rejected, skipped or left unfilled, and what did that cost?
REQUIRED_FACTS: rejection and no-fill reasons, incomplete outcomes stated as incomplete, missed-opportunity facts
ADOPTED_EXTENSION: refusal and miss facts come from the same canonical trace as the decisions themselves
CANONICAL_PROJECTION_AUTHORITY: UNDECIDED
COUNTS_AND_PNL_RECONCILIATION: NOT_RECORDED
PARITY_STATUS: NOT_VERIFIED
PROOF_REQUIRED: fixture reconciliation of counts and P&L at one watermark against the declared canonical projection
FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation
```

```text
PAGE_ID: P6_COMMITTEE_VALUE_AI_COST
ADOPTED_NAME: Committee Value and AI Cost
OPERATOR_QUESTION: what did the advisory research cost, and what value did it add?
REQUIRED_FACTS: Committee analysis and its cost, advisory-only status, calibrated resolution, section 21 Committee trust panels
ADOPTED_EXTENSION: cost and value carry their own uncertainty; no favorable state is derived from an unmeasured case
CANONICAL_PROJECTION_AUTHORITY: UNDECIDED
COUNTS_AND_PNL_RECONCILIATION: NOT_RECORDED
PARITY_STATUS: NOT_VERIFIED
PROOF_REQUIRED: fixture reconciliation of counts and P&L at one watermark against the declared canonical projection
FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation
```

```text
PAGE_ID: P7_LEARNING_RELEASES
ADOPTED_NAME: Learning and Releases
OPERATOR_QUESTION: what did the platform expect, what actually happened, and which release produced it?
REQUIRED_FACTS: expectations versus realised outcomes, capital-time and horizon evaluation, release identity and receipts
ADOPTED_EXTENSION: learning evidence is labelled with the exact release that produced it; learning cannot edit active policy
CANONICAL_PROJECTION_AUTHORITY: UNDECIDED
COUNTS_AND_PNL_RECONCILIATION: NOT_RECORDED
PARITY_STATUS: NOT_VERIFIED
PROOF_REQUIRED: fixture reconciliation of counts and P&L at one watermark against the declared canonical projection
FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation
```

```text
PAGE_ID: P8_INCIDENTS_INFRASTRUCTURE
ADOPTED_NAME: Incidents and Infrastructure
OPERATOR_QUESTION: what is broken, degraded or unverified, and is protection still independent?
REQUIRED_FACTS: incidents and their resolution state, coverage-degradation scopes, protection and infrastructure health
ADOPTED_EXTENSION: an unreadable store is never reported as an empty incident set
CANONICAL_PROJECTION_AUTHORITY: UNDECIDED
COUNTS_AND_PNL_RECONCILIATION: NOT_RECORDED
PARITY_STATUS: NOT_VERIFIED
PROOF_REQUIRED: fixture reconciliation of counts and P&L at one watermark against the declared canonical projection
FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation
```


## 4. Current surfaces (verified evidence, no authority)

Four presentation surfaces exist today, and the ledger already records that none
of them covers all eight pages. This section cites them as evidence of the
current state; this specification declares none of them a fact authority.

- Legacy outcomes cockpit: route `/dashboard` plus the consumers
  `/api/analytics/summary` and `/api/analytics/intelligence`, declared in
  `app/api/dashboard.py`, mounted by `app/main.py`, rendered from the static HTML
  snapshot `app/api/dashboard.html` and built by
  `app/services/dashboard_read_model.py`.
- B/C-4 cockpit API: `app/api/cockpit.py` projections over
  `app/opip/cockpit/ledger.py` (reconciled Paper-v2 economic ledger),
  `app/opip/cockpit/portfolio.py` and `app/opip/cockpit/trust.py`, served by
  `app/api/cockpit_service.py`. It is deliberately NOT mounted in `app/main.py`.
- Grafana cockpit: analytics-plane dashboard JSON; the ledger records that the
  last observed analytics install is not this core SHA, so it is not evidence for
  a core release.
- Profit Intelligence library: `app/opip/profit_intelligence/semantics.py` and
  `app/opip/profit_intelligence/read_model.py`; the ledger records "Profit
  Intelligence library with no route".

Evidence notes this specification relies on (read-only, unchanged):

- `app/opip/cockpit/trust.py` keeps four trust dimensions independent and states
  that `UNKNOWN` is never rendered as `0`, `healthy` or `complete`.
- `app/opip/profit_intelligence/semantics.py` defines the availability
  vocabulary `KNOWN`, `DERIVED`, `UNAVAILABLE`, `NOT_APPLICABLE` and states that
  an unavailable fact must never be rendered as `0`.
- `app/opip/cockpit/portfolio.py` reports marked-equity drawdown as `UNKNOWN`
  rather than zero and computes no cross-currency total.
- `app/opip/cockpit/ledger.py` is a derivation and not an authority: only
  `FINAL_VERIFIED` economics may be presented as a settled result.
- `app/main.py` mounts the legacy dashboard router and deliberately does not
  mount the B/C-4 cockpit analytics router, because that router reads the
  canonical replica that exists only on the analytics plane.

## 5. Parity predicate (exact)

A page has parity only when all of the following hold together, at one watermark,
with recorded evidence:

1. Identity: the page is one of the eight frozen pages, carrying the page and
   semantic-model identity the adopted requirement names.
2. Single truth: every displayed fact is read from the one declared canonical
   projection authority for that fact. No second source of truth is created, and
   the presentation layer may format, filter and navigate but never recalculate.
3. Reconciliation: counts AND P&L reconcile against that projection in fixture
   reconciliation, recorded as evidence together with its watermark.
4. Honest unknown: a fact that is unavailable, not evaluated or not applicable is
   rendered as explicit unknown, never as `0`, `healthy`, `complete` or another
   favorable state.
5. No authority: the page holds no trading, admission, exit, Telegram, promotion,
   deployment or production authority.
6. Evidence: the reconciliation evidence exists in the repository at the exact
   release that claims parity.

Until every one of those holds for a page, that page's parity is `NOT_VERIFIED`.
Parity is never inferred from a page existing, from a shared UI, from a shared
semantic model, or from the legacy route answering the same question.

PARITY_VERDICT: NOT_VERIFIED (0 of 8 pages verified)

## 6. Negative cases (fail-closed)

NC-01: A page served by the legacy route is not parity. Answering the same
question in another surface proves nothing about canonical reconciliation.

NC-02: Parity is never claimed from missing, unreadable, stale or unverified
evidence. An unreadable projection is not "no discrepancy", an absent incident
store is not an empty incident set, and an unevaluated trust dimension is not
healthy.

NC-03: A partial reconciliation is not parity. Counts without P&L, or P&L
without counts, leaves parity `NOT_VERIFIED`.

NC-04: A shared semantic model, a shared watermark, typed filters, cross-filters,
saved views and a decision trace are necessary and not sufficient: parity still
requires the counts-and-P&L reconciliation of section 5 clause 3.

NC-05: A page may not be marked verified without a committed follower increment
contract that declares that page's parity criterion. The status flip is gated,
not editorial.

NC-06: No numeric freshness window, latency bound, tolerance, sample size,
capacity, RPO/RTO or cost limit is ratified by this specification. Each remains
an owner decision.

NC-07: Parity grants no authority. It authorizes no release-profile change, no
activation, no cutover, no legacy retirement and no funded or exchange
capability.

NC-08: The legacy outcomes cockpit is not removed, weakened, re-pointed or made
write-capable before verified parity, and its removal afterwards remains the
retirement ledger's own gated decision.

NC-09: Unsupported source claims and incomplete cost are never converted into a
favorable page state, and no page may present a second P&L or a second position
registry.

NC-10: Any design decision that is not already frozen - which projection owns
which fact, page layout, panel composition, column set, colour, sort order or
default filter - is out of scope here and is not decided by this increment.

## 7. Threshold policy

```text
THRESHOLD_POLICY: OWNER_DECISION_REQUIRED
INHERITED_OPEN_INPUTS: OQ-10 (operator usefulness questionnaire and cockpit retention), OQ-07 (gate thresholds)
RATIFIED_THRESHOLDS: NONE
```

No numeric freshness window, latency bound, reconciliation tolerance, capacity,
sample size, RPO/RTO or cost value is ratified by this specification, and none
may be inferred from it. The references above are open-input identifiers, not
measurements.


## 8. Traceability

| F10 element | Adopted source | Structural contract | Register row / gate | Open input | This spec | Acceptance test |
| --- | --- | --- | --- | --- | --- | --- |
| Eight pages and the shared semantic model | register `F10`; ledger `F10` row; architecture section 15 `F10` line | `SC-08`, `SC-12` | `F10`, `G4` | `OQ-10` | 3, 5, 6 | AC-001, AC-002 |
| Plans exposed to the operator | `SC-08` frozen `PortfolioPlan` record keys | `SC-08` | `F10`, `R5-D` | `OQ-06` | 1, 3 (P4) | AC-002 |
| Capital and horizons exposed | architecture section 15 `F10` line; register rows `R5-B`, `R5-C` | `SC-04`, `SC-06`, `SC-07` | `F10` | not declared on `F10` | 1, 3 (P1, P4) | AC-002 |
| Counts and P&L reconciliation as the cutover gate | ledger `F10` `CUTOVER_GATE` | `SC-12` (single canonical writer) | `G4` | `OQ-10` | 5, 6 (NC-02, NC-03) | AC-002, AC-003 |
| Missing evidence is never a favorable page state | architecture evidence clause; `app/opip/cockpit/trust.py`; `app/opip/profit_intelligence/semantics.py` | `SC-12` (`DC-03` unknown stays unknown) | `F10` | not declared on `F10` | 4, 5, 6 (NC-02, NC-09) | AC-004 |
| Legacy outcomes cockpit retention | owner decision of this increment; retirement ledger legacy `/dashboard` row | `SC-12` (cockpit retention), `SC-13` (legacy disposition) | `G4`, `R7` | `OQ-10`, `OQ-08` | 2, 6 (NC-08) | AC-006 |
| No numeric threshold is ratified | `SC-12` frozen invariant ("gate thresholds ... stay `OQ-07`") | `SC-12` | `G4` | `OQ-07`, `OQ-10` | 7 | AC-005 |
| Gated status flip | this specification; ATDD scope control | `SC-12` | `G4` | `OQ-10` | 5, 6 (NC-05), 10 | AC-003 |

Chains this specification adds nothing to and changes nothing in:

```text
F10 -> SC-08, SC-12 -> G4 -> OQ-10 (retention decided by the owner; questionnaire open)
F10 -> R5-LEGACY-OUTCOMES-COCKPIT retention -> R7 retirement ledger (separately gated, OQ-08, OQ-10)
```

## 9. What remains open (owner inputs)

- `OQ-10` operator usefulness questionnaire: `OPEN`, owner only. Retention is
  decided (section 2); usefulness is not measured, scored or assumed here.
- `OQ-07` gate thresholds: `OPEN`, owner only. Section 7 ratifies none.
- `OQ-06` sleeve caps and hard-versus-soft constraint behaviour: `OPEN`, owner
  only; `SC-08` freezes the record surface, not the cap values.
- Which existing canonical projection owns each page fact, and whether any page
  needs an additional projection: `UNDECIDED`. That decision belongs to the
  follower increment and must reuse an existing authority rather than create a
  second one.
- Any release-profile, activation, cutover, retirement or production change:
  `NOT REQUESTED` and not authorized by this specification.

## 10. Follower increment

FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation (PROPOSED_NOT_AUTHORIZED)

The follower increment is the authorized scope that turns this contract into
proven parity. Until the owner approves that increment, the identifier above is a
proposal used only so the status-flip guard of section 6 (NC-05) can be checked
deterministically. It grants no authority now.

Obligations the follower increment inherits:

1. Declare, per page, the one existing canonical projection authority for every
   displayed fact, and prove there is no second source of truth.
2. Record the counts-and-P&L fixture reconciliation per page at one watermark, and
   only then change that page's `PARITY_STATUS`.
3. List this specification file in its own implementation map, so the status flip
   is an authorized change rather than an editorial one.
4. Prove the negative cases of section 6, including the unreadable-evidence,
   partial-reconciliation and partial-availability cases.
5. Keep the legacy outcomes cockpit untouched until parity is verified, and leave
   its eventual removal to the retirement ledger's own gated decision.
6. Add no trading, admission, Telegram, promotion or deployment authority, and
   ratify no numeric threshold.

## 11. How this specification is validated

- Structure, inventory, parity state, negative cases and the threshold policy are
  asserted by `tests/test_opip_r5_f10_dashboard_parity_contract.py`, whose tests
  map to `AC-001`-`AC-006` of
  `docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-contract.md`.
- The RED reading of those tests: each target criterion is asserted in its
  unproven direction (the gap is open) and each guard fails closed if the gap is
  closed silently. They are the acceptance criteria for the follower increment
  and are superseded - not deleted - when it lands.
- This document states no production root cause, claims no parity, and conveys no
  trading, deployment or release authority. Where it was read in this repository,
  it says so; where the requirement is not yet realized, it says `NOT_VERIFIED`.

