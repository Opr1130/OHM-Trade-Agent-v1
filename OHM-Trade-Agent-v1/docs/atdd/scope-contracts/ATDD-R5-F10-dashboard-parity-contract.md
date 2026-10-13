INCREMENT:
ATDD-R5-F10-dashboard-parity-contract

OWNER-APPROVED INTENT:
This is the OWNER-approved F10 contract-preparation increment. The OWNER directed that F10 requirements, traceability, negative cases and RED acceptance tests be finalized, that no threshold be invented, and that the legacy outcomes cockpit be preserved until verified dashboard parity. This increment does exactly that and nothing else.

It adds no page, route, panel, projection, metric, filter, saved view, service, scheduler, store, release profile or configuration key, and it changes no runtime behaviour. It grants no trading, admission, exit, Telegram, promotion, deployment or production authority, and it ratifies no numeric threshold. Its whole deliverable surface is three documents and one RED acceptance-test module: the active-increment pointer, this contract, the F10 parity specification under docs/architecture/, and the tests that prove the gap is still open and that close it only under a committed follower contract.

STARTING SHA. `origin/feature/opip-atdd-ml-dependency-policy-v1` = `dca841132b0dcb1469b94d0bdd3502483def040a` (the PR #373 head this increment was created from, on top of `fba8440f`; `main` is further behind the stacked R5/R6 work). If the stack moves under this increment, the branch is inspected and, where F10, the ATDD scope or the pointer is affected, reconciled before merge.

WHAT F10 IS, AND WHERE IT COMES FROM. Register row `F10` states the adopted requirement: "Dashboard: same eight pages and semantic model; expose plans, capital and horizons", clause `§15;§13`, status `PARTIAL`, evidence `app/opip/cockpit/portfolio.py`, contracts `SC-08,SC-12`, open input `OQ-10`, gate `G4`, note "EXTEND; read-only facts only, no favorable state from missing evidence". The conformance ledger's `F10` row names the eight pages, records `IMPLEMENTATION_STATUS PARTIAL`, records that "none of these are fact authorities", records four overlapping surfaces with "none covering all eight pages", and sets `TARGET_AUTHORITY` to "One semantic read service over canonical projections" with the cutover gate "Fixture reconciliation of counts and P&L, then retire duplicate pages". This increment records those requirements, freezes the page inventory, states the parity predicate, enumerates the negative cases, and leaves every implementation decision it is not authorized to take as `UNDECIDED`.

KEY LAW - PARITY IS RECONCILIATION, NOT PRESENTATION. A page does not have parity because it exists, because the legacy route answers the same question, because a semantic model is shared, or because a watermark is shared. Parity requires one declared canonical projection authority per displayed fact, no second source of truth, and a recorded fixture reconciliation of counts AND P&L at one watermark. Until that evidence exists, the page is `NOT_VERIFIED`.

KEY LAW - MISSING EVIDENCE IS NEVER FAVORABLE EVIDENCE, RESTATED FOR PRESENTATION. An unreadable projection is never "no discrepancy", an absent incident store is never an empty incident set, an unevaluated trust dimension is never healthy, and a partial reconciliation is never parity. Where a fact cannot be proven, the page says so.

KEY LAW - PREPARATION IS NOT ACTIVATION OR RETIREMENT. Preparing a contract activates nothing and retires nothing. The legacy outcomes cockpit stays mounted and untouched until parity is verified, and its eventual removal remains the retirement ledger's own gated decision (fixture counts and P&L matching canonical projections, a consumer census, and the OWNER's separate decision).

RED ACCEPTANCE TESTS, AND WHY THEY ARE RED WITHOUT FAILING CI. This increment prepares a contract, so it has no working code to prove and must not fake one. Its acceptance tests therefore assert the target criteria in their unproven direction and fail closed if the gap closes silently: the page set is the frozen eight, parity is `NOT_VERIFIED` for all eight pages, no canonical projection authority is declared yet, no counts-and-P&L reconciliation is recorded yet, no measurement is ratified, and no page may be flipped to verified without a committed follower increment contract that declares that page's parity criterion. They are the acceptance criteria of the follower increment and are superseded - not deleted - when it lands. Nothing is marked xfail or skipped, so the suite stays green while the criteria remain executable, and a silent flip or an invented threshold turns the suite red.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` section 15 `F10 Dashboard` ("EXTEND", "P2 alongside A-F: same eight pages and semantic model; expose plans, capital and horizons"), the retained-milestone clause ("Preserve that older milestone as R5-LEGACY-OUTCOMES-COCKPIT in tracking; ... its F10 work accompanies each evidence increment") and the evidence clause ("Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states.").
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` rows `F10`, `G4` and `OQ-10`, and its stated premise that the register ratifies no numeric policy and grants no delivery authority.
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` `F10` row: the eight pages, the shared semantic model, `PARTIAL`, the four overlapping surfaces, the target authority and the cutover gate.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-08` "Portfolio plan record and candidate comparison" (frozen `PortfolioPlan`, owning module `app/opip/contracts/portfolio.py`, sleeve caps stay `OQ-06`) and `SC-12` "Canonical ordering, temporal contract and shared writer" (frozen `CanonicalTemporalRecord`, exactly one canonical writer, "Gate thresholds and cockpit retention stay `OQ-07` and `OQ-10`"); `SC-13` keeps retirement behind a reviewed migration.
- `docs/architecture/OPIP_RETIREMENT_LEDGER.md` legacy `/dashboard` row: mounted in `app/main.py`, "No new cards on the legacy page during R5", cutover proof "Fixture counts match canonical projections", consumer census `/api/analytics/summary`, `/api/analytics/intelligence`, "Route stays until cutover", "Route removed after census".
- Read-only evidence of the honesty laws this increment relies on, none of which it changes: `app/opip/cockpit/trust.py` (four independent trust dimensions; `UNKNOWN` is never rendered as `0`, `healthy` or `complete`), `app/opip/profit_intelligence/semantics.py` (`KNOWN`, `DERIVED`, `UNAVAILABLE`, `NOT_APPLICABLE`; an unavailable fact must never be rendered as `0`), `app/opip/cockpit/ledger.py` (a derivation, not an authority; only `FINAL_VERIFIED` economics are settled), `app/opip/cockpit/portfolio.py` (marked-equity drawdown is `UNKNOWN`, never `0`), `app/api/cockpit_service.py` (read-only cockpit service on the analytics plane with no exchange, Telegram, order or trade capability), `app/main.py` (the legacy dashboard router is mounted; the B/C-4 cockpit analytics router is deliberately not).
- `docs/atdd/README.md`, `tests/atdd_scope.py` and `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: the scope mechanism this contract is written for.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable and a completed increment's tests must not pin it. The RED tests of this increment never read it.
- `docs/atdd/scope-contracts/ATDD-R5-0-contract-conformance.md` and `docs/atdd/scope-contracts/ATDD-R5-0-architecture-source-adoption.md`: the register and ledger rows cited above are evidence records, not authority grants.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the adopted F10 dashboard requirement and the prepared parity specification
WHEN:
the page inventory is inspected against the adopted sources
THEN:
it holds exactly the eight adopted pages in the adopted wording with stable identifiers, the inventory is recorded as frozen so adding, removing, renaming, splitting or merging a page requires a new authorized increment, and the set matches the conformance ledger's own enumeration

AC-002:
GIVEN:
the eight prepared pages and the parity predicate
WHEN:
each page's recorded parity state is read
THEN:
every page is `NOT_VERIFIED` with the verdict recording zero verified pages, every page carries its operator question, required facts, adopted extension, an explicit canonical-projection authority field that is `UNDECIDED` rather than guessed, an unrecorded counts-and-P&L reconciliation, the proof that would close it and the follower increment that must close it, and no page may be presented as parity

AC-003:
GIVEN:
the prepared parity states and the proposed follower increment
WHEN:
a page is marked as anything other than `NOT_VERIFIED`
THEN:
a committed follower increment scope contract must exist and must itself declare that page's parity criterion, while the follower increment remains recorded as `PROPOSED_NOT_AUTHORIZED`, so a silent or editorial green flip fails closed

AC-004:
GIVEN:
the enumerated negative cases and the honesty laws this plane already enforces
WHEN:
a fact is missing, unreadable, unevaluated or only partially reconciled
THEN:
no favorable page state may be produced, an unreadable projection is not "no discrepancy", an absent incident store is not an empty incident set, an unevaluated trust dimension is not healthy, a partial reconciliation is not parity, and the surviving gaps are stated explicitly rather than implied to be fine

AC-005:
GIVEN:
the owner-gated threshold inputs inherited from the frozen contracts
WHEN:
the prepared threshold policy is inspected
THEN:
it declares the threshold decision `OWNER_DECISION_REQUIRED`, ratifies no threshold, and contains no measurement such as a freshness window, latency bound, tolerance, capacity, sample size or cost value that could be read as an invented threshold

AC-006:
GIVEN:
the owner decision to preserve the legacy outcomes cockpit until verified dashboard parity
WHEN:
the recorded decision, the mounted legacy surfaces, the retirement ledger's cutover conditions and this increment's authorized paths are inspected
THEN:
the decision is recorded, the legacy `/dashboard` route and its `/api/analytics/*` consumers are still present, its removal still requires verified parity plus the consumer census, and this increment authorizes no runtime, workflow or deployment path


EXPLICITLY OUT OF SCOPE:
- Any runtime, application, service, API, job, scheduler, route, template, static asset, dashboard page or cockpit projection change
- Any new page, panel, filter, cross-filter, saved view, column, chart, metric or export
- Any second source of truth, second P&L, second position registry, new store, new event type, new database or new service
- Any release-profile, Feature Bus, canonical-writer, target-spine, Paper-v2, Committee or Telegram change, activation or widening
- Any trading, admission, exit, order, exchange or funded authority, and any promotion or deployment authority
- Retirement, removal, re-pointing or write-enablement of the legacy outcomes cockpit or of any duplicate surface
- Ratifying any numeric threshold, freshness window, latency bound, tolerance, capacity, sample size, RPO/RTO or cost limit
- Answering, pre-filling or scoring the `OQ-10` operator usefulness questionnaire
- Editing the adopted architecture, the R5 register, the conformance ledger, the contract freeze or the retirement ledger
- Changing `tests/atdd_scope.py`, the scope checker's parser, arrow syntax, exit codes or fail-closed behaviour
- Changing the release workflow, its trigger, its job list, branch protection or any deployment path

FROZEN BOUNDARIES:
- The adopted eight-page set and the shared semantic model are the requirement; this increment freezes the inventory and changes neither.
- The parity predicate is reconciliation of counts and P&L against one declared canonical projection authority at one watermark; presentation alone never satisfies it.
- A page whose parity is unproven stays `NOT_VERIFIED`, and a status flip requires a committed follower increment contract.
- The legacy outcomes cockpit stays mounted and untouched until verified parity, and its removal remains gated by the retirement ledger's own conditions and a separate owner decision.
- The register, ledger, contract freeze and retirement ledger are evidence and governance records; this increment reads them and edits none of them.
- No threshold, authority or production behaviour is created, widened or transferred anywhere in this increment.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f10_dashboard_parity_contract.py::test_ac_001_eight_page_inventory_is_frozen_and_complete
AC-001 -> tests/test_opip_r5_f10_dashboard_parity_contract.py::test_ac_001_page_set_matches_the_adopted_conformance_ledger
AC-002 -> tests/test_opip_r5_f10_dashboard_parity_contract.py::test_ac_002_parity_is_not_verified_for_every_page
AC-002 -> tests/test_opip_r5_f10_dashboard_parity_contract.py::test_ac_002_every_page_declares_source_proof_and_follower
AC-003 -> tests/test_opip_r5_f10_dashboard_parity_contract.py::test_ac_003_parity_cannot_flip_without_a_committed_follower_contract
AC-004 -> tests/test_opip_r5_f10_dashboard_parity_contract.py::test_ac_004_missing_evidence_can_never_produce_a_favorable_page
AC-005 -> tests/test_opip_r5_f10_dashboard_parity_contract.py::test_ac_005_no_numeric_threshold_is_ratified
AC-006 -> tests/test_opip_r5_f10_dashboard_parity_contract.py::test_ac_006_legacy_outcomes_cockpit_retention_is_preserved

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-contract.md
AC-001 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_contract.py
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_contract.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-contract.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_contract.py
AC-004 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_contract.py
AC-005 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_contract.py
AC-006 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_contract.py


DEFERRED DISCOVERIES:
- The RED reading above is this increment's interpretation of "RED acceptance tests" for a contract-preparation increment, chosen because the repository has no xfail or expected-failure convention and because a genuinely failing test would break the required `test` gate. The tests assert the open gap and fail closed when it closes silently. If the OWNER wants the follower's GREEN criteria carried as skipped placeholders instead, that is a new increment.
- Per-page fact ownership is `UNDECIDED` on purpose. `SC-08` freezes the `PortfolioPlan` record surface and `SC-12` freezes the canonical temporal record and the single-writer rule, but neither freezes which existing projection owns each page fact, and this increment is not authorized to decide it. Reusing an existing authority is mandatory; creating a second one is forbidden.
- The `F10` register row's evidence path is `app/opip/cockpit/portfolio.py`, a B/C-4 cockpit projection, while the conformance ledger records four overlapping surfaces with none covering all eight pages. Which surface becomes the single semantic read service therefore remains open, and this increment neither selects one nor retires any.
- The `F10` register row names contracts `SC-08` and `SC-12`, and `SC-12` records that cockpit retention stays `OQ-10`. The owner's retention decision recorded here resolves that half of `OQ-10` in one direction - keep the legacy cockpit until verified parity. The usefulness questionnaire half stays open, and no other `OQ` cell is touched. Whether the frozen contract-freeze document should later carry a narrower open-input note is an architecture question for a separate increment; that document is not edited here.
- Discovered while preparing this contract and relevant to where F10 stacks: the stacked R5/R6/ML-dependency pull-request chain (PRs #369-#373) has a dependency-aware merge-order interaction with the movable active-increment pointer. Landing a pull request into its base branch changes that base branch's pointer to the merged increment, so the pull request directly below it then fails the `atdd scope` gate (observed `SCOPE_CHANGE_REQUIRED`, exit 2) until its head is restored or rebuilt. The reproduction and the per-gate readiness evidence are recorded by the successor increment `ATDD-R5-STACK-MERGE-READINESS`; this increment merges nothing and re-points nothing.
- The eight page records declare `PROOF_REQUIRED` in the ledger's own terms ("fixture reconciliation of counts and P&L"). Which fixtures, which watermark and which tolerance count as sufficient is a follower-increment and owner question; no tolerance is invented here.
- `OQ-06`, `OQ-07`, `OQ-08` and `OQ-10` remain owner inputs, `G4` remains `PROPOSED_NOT_FROZEN`, and the `F10` register row remains `PARTIAL`. This increment changes no register, ledger or freeze row.

UNAPPROVED SCOPE CHANGES:
NONE

