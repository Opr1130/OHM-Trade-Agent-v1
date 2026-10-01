INCREMENT:
ATDD-R3-F7-economic-portfolio-selector

OWNER-APPROVED INTENT:
This is the OWNER-authorized R3-F7 economic / portfolio selector increment. It creates one pure, deterministic, replayable constrained economic selector that sits after the F6 Forecast Engine, consumes the frozen candidate panel and explicit capital/exposure/window/policy inputs, and either selects an admissible allocation that maximizes expected portfolio net dollars, deliberately holds cash/no-trade, or abstains with INSUFFICIENT_EVIDENCE. It is SHADOW / NON-AUTHORITATIVE and is not wired into any runtime path.

ARCHITECTURE AUTHORITY. O'Pip Profit Intelligence Platform Architecture v1.4.3, repository authority copy `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`, and its extraction `docs/architecture/v1.4.3/ARCHITECTURE.md`. This increment changes no architecture document and does not vendor the v1.4.3 package. Where the v1.2 contract and v1.4.3 disagree, v1.4.3 is canonical.

STARTING SHA. `origin/main` = `9bcf5737ba0e1580ef291d70ce59fbed6479d174` (the base this branch was created from). If `origin/main` moves, the branch is inspected; if compatible it is rebased, and if F7, F6, the frozen comparator or the ATDD scope is affected it is reconciled before merge.

LIVE CURRENT AUTHORITY (unchanged by this increment). There is no F7 selector, no portfolio allocator and no net-dollars objective in the repository today. The live economic path remains the frozen legacy comparator: `app/scanner/candidates.py` (`MIN_TECHNICAL_SCORE`, `MAX_CANDIDATES`, Top-8), `app/scanner/directional_candidates.py`, `app/services/profit_ranking.py` (`evaluate_profit_ranking`, `rank_profit_opportunities`) and `app/services/portfolio_risk.py` (`evaluate_portfolio_risk`). The live qualification numbers (0.35% risk, 2.5 minimum R:R) and the observed caps (gross 50%, same-direction 2, single-position capital fraction 20%) remain current practice and are NOT restated as permanent F7 invariants: the comparator reuses the live production constants by import, and the F7 policy has no numeric default at all.

KEY LAW — MISSING EVIDENCE IS NEVER FAVORABLE EVIDENCE. A candidate is economically usable only when it carries a structurally valid F6 `FORECAST` with an unconditional expected return, an uncertainty interval and an unexpired validity, and its data-quality and execution-evidence gates are `VALID`. A candidate without a usable forecast, with an F6 `INSUFFICIENT_EVIDENCE` decision, a `RESEARCH_ONLY` artifact outcome, a stale forecast or a failed evidence gate is never allocated capital and never improves a rank. When no candidate is economically usable the selector returns `INSUFFICIENT_EVIDENCE` with the machine-readable reason `NO_QUALIFIED_FORECAST`. The production F6 registry legitimately holds zero calibrated models, so a zero-model environment producing deterministic governed abstention is the correct trusted state and is SUCCESS, not a defect.

CASH IS A REAL COMPETING DECISION. `CASH_NO_TRADE` is selected whenever every admissible constrained portfolio has a non-positive expected net dollars versus cash, or when the constraint set excludes every candidate. The selector never forces a trade to fill a quota, and `SELECTED` requires a strictly positive objective.

SHADOW RESERVATION BOUNDARY. The selector may emit a deterministic selection/allocation/reservation PLAN bound to an explicit portfolio version. A plan applied against a stale portfolio version fails closed. Release, expiry and fill-adjustment are pure transforms that return a new plan. NO production reservation writer is invoked, NO Paper-v2 reservation is mutated, NO canonical operational writer authority is created and NO live capital is touched. Atomicity is not faked.

MERGE / DEPLOY AUTHORIZATION. This OWNER prompt authorizes the full end-to-end path for this increment: local gates, a feature branch and push, a pull request, the review loop, exact-head CI, merge to `main` on the exact verified head, post-merge `pytest.yml` on `main` for the exact merge SHA, and exactly one owner `/deploy <40-char MERGE_SHA>` on issue #64 followed by the production deploy through the existing control plane, ending in a verified production receipt. It does not authorize F7 runtime integration, Paper-v2 activation, Feature Bus activation, Committee authority, funded trading, legacy retirement, an F7 canonical writer, or F8/F9. PR #294 (bridge) is unrelated and is not touched, merged, rebased, closed, cherry-picked or depended on.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (spine): "Feasibility and calibrated statistical forecasts -> economic selector -> deterministic risk checks and atomic capital reservation -> realistic paper execution."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3 (point-in-time / missing evidence): "Missing evidence is never favorable." and "Evidence is eligible only if it was available by the cutoff, not merely dated before it."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 4 (component table): "Forecast execution probabilities separately from conditional post-fill paths; the Committee cannot create or adjust either probability." and "Do not count no-fill intents as losing trades or omit them from the intent population."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 10 (Forecast quality): "LLM confidence is not forecast probability."
- `docs/architecture/v1.2/F_ECONOMIC_PORTFOLIO_CONTRACT.md`: primary objective "Maximize expected portfolio net dollars over a common evaluation window"; subject to capital, concentration/common-shock, liquidity/capacity, data-quality, drawdown/loss and valid-execution-evidence constraints; benchmarks are cash/no-trade and the frozen legacy comparator (technical score / Top-8 + profit ranking, terminating at technical cutover); trading economics is simulated cash P&L after fees and explicit transaction charges and does not deduct embedded spread/slippage twice; operating economics subtracts attributable compute, storage, data, AI and recurring costs; reservation is atomic against a portfolio version, releases on cancellation or expiry and adjusts on fills, and research simulation does not share reservations with approved allocation; capital occupancy/time-to-exit is a diagnostic while total net dollars is primary; hit rate, profit factor and ECE are diagnostics; Kelly sizing, covariance optimizers, leverage and dynamic risk parity are forbidden in v1; current observed caps remain configurable current practice until a later ratified selector policy replaces them; the live qualification path stays frozen at 0.35% risk and 2.5 minimum R:R and those numbers are not permanent architecture invariants.
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md`: the F7 economic/portfolio row records no current implementation and the frozen legacy comparator as current practice; stale prose that records F6 as missing is historical evidence and is corrected in this increment's narrative rather than rewritten, because the canonical F6 engine, contract and evaluation modules exist and are frozen.
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R3: shadow/evidence-first with no production runtime authority; the Feature Bus mode stays `off`.
- `docs/architecture/v1.2/G_STATISTICAL_PROTOCOL.md`: no universal block length or N_eff threshold is an architecture constant, and no arbitrary promotion threshold may be invented.
- Reused rather than duplicated: `app/opip/contracts/serialization.py` (`stable_hash`, `iso_z`), `app/opip/contracts/temporal.py` (`require_utc`, `TemporalIntegrityError`), and the frozen F4/F5/F6 contracts.
- `docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md`, `ATDD-R3-F4-opportunity-lifecycle.md`, `ATDD-R3-F4-opportunity-lifecycle-implementation.md`, `ATDD-R3-F4-opportunity-lifecycle-persistence.md`, `ATDD-R3-F5-feasibility-safety.md` and `ATDD-R3-F6-forecast-engine.md`: F3, F4, F5 and F6 are frozen and are not modified by this increment.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: a completed increment must not permanently own the global pointer; this increment's AC-002 proves F7's own identity and requires only that the pointer resolve to an existing scope contract.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture; `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the F7 contract vocabularies and the sealed architecture
WHEN:
the portfolio vocabulary module is inspected
THEN:
the status tokens are exactly SELECTED, CASH_NO_TRADE and INSUFFICIENT_EVIDENCE, the cash reasons are exactly NO_POSITIVE_EXPECTED_NET_DOLLARS and CONSTRAINTS_EXCLUDE_ALL_CANDIDATES, the evidence statuses are exactly VALID, UNAVAILABLE and FAILED, the directions are exactly LONG and SHORT, the reservation statuses are exactly PLANNED, RELEASED, EXPIRED and FILL_ADJUSTED, the release reasons are exactly CANCELLED, EXPIRED and FILL_ADJUSTED, and the version tokens are exactly portfolio-decision-v1, portfolio-selector-v1, portfolio-shadow-policy-v1, portfolio-reservation-plan-v1 and portfolio-evaluation-v1

AC-002:
GIVEN:
this increment, the ATDD scope control and the movable active-increment pointer
WHEN:
the F7 contract and the pointer are inspected
THEN:
the contract exists and declares its own increment identity, the pointer resolves to an existing scope contract, and this increment's acceptance module never compares the global pointer to its own increment identity

AC-003:
GIVEN:
one candidate panel
WHEN:
candidate identities are derived and the panel is validated
THEN:
the identity is a deterministic PCAND: function of the candidate lineage and semantics, a forged identity fails closed, a duplicate candidate identity fails closed, and a duplicate symbol/direction collision fails closed

AC-004:
GIVEN:
one declared evaluation window
WHEN:
the selector validates it
THEN:
only an explicit timezone-aware UTC start and end are accepted with end strictly after start, a naive or inverted window fails closed, an evaluation instant outside the window fails closed, a candidate forecast that outlives the window fails closed, and there is exactly one window for the whole panel

AC-005:
GIVEN:
an explicit capital-state snapshot and an explicit exposure snapshot
WHEN:
they are validated and used
THEN:
their fingerprints are deterministic PCAP: and PEXP: functions of their inputs, a forged fingerprint fails closed, and a portfolio version that differs between the two snapshots fails closed

AC-006:
GIVEN:
the applied F7 policy
WHEN:
it is constructed and inspected
THEN:
no numeric limit has a default, every fraction is validated within its open interval, a boolean or non-finite value is refused, the identity is a deterministic PPOL: function of the limits, and an unratified policy version fails closed

AC-007:
GIVEN:
one candidate whose F6 evidence is absent, stale, untrusted or lineage-mismatched
WHEN:
the pure selector evaluates it
THEN:
a candidate without a usable FORECAST is never allocated capital, a stale forecast is never favorable, a non-VALID data-quality or execution-evidence gate is never favorable, and a lineage mismatch between the candidate and its forecast fails closed

AC-008:
GIVEN:
the trusted production registry and the zero-calibrated-model reality
WHEN:
the selector evaluates a panel whose only forecasts are F6 abstentions
THEN:
it returns a deterministic INSUFFICIENT_EVIDENCE decision with the reason NO_QUALIFIED_FORECAST, fabricates no allocation and no objective, and the production registry stays empty

AC-009:
GIVEN:
a panel whose admissible portfolios are all non-positive or entirely excluded
WHEN:
the selector compares them with cash
THEN:
it selects CASH_NO_TRADE with a machine-readable cash reason, commits no capital, and never forces a trade to fill a quota

AC-010:
GIVEN:
a missing capital-state or exposure snapshot, or an empty panel
WHEN:
the selector evaluates the request
THEN:
it returns a governed INSUFFICIENT_EVIDENCE abstention with the machine-readable reason CAPITAL_STATE_UNAVAILABLE, EXPOSURE_STATE_UNAVAILABLE or NO_ELIGIBLE_CANDIDATES, and it never substitutes a default state

AC-011:
GIVEN:
a usable candidate and its F6 forecast
WHEN:
its trading economics are derived
THEN:
expected net dollars are the allocated capital times the F6 unconditional expected net return, fees and spreads are never deducted a second time, NO_FILL is included in the intent population and contributes no loss, and the entry-execution and conditional post-fill families are never collapsed into one win probability

AC-012:
GIVEN:
a selected portfolio
WHEN:
its uncertainty is reported
THEN:
an explicit aggregate expected-net-dollars interval derived from the F6 uncertainty intervals is retained, the point estimate lies inside it, and a missing or inverted interval is refused rather than defaulted

AC-013:
GIVEN:
the F7 operating economics
WHEN:
the attributable operating cost is unavailable
THEN:
the decision reports the operating economics as unknown rather than silently assuming zero, and a supplied operating cost is reported explicitly while the primary objective remains trading economics

AC-014:
GIVEN:
a selected portfolio
WHEN:
the allocation and the reservation plan are built
THEN:
the total allocated capital never exceeds the explicit available capital or the gross cap, cash may remain partially or fully unallocated, the planned reservation capital equals the allocated capital and never exceeds the available capital, and selected candidate ids match the allocation ids

AC-015:
GIVEN:
a candidate panel and an explicit exposure snapshot
WHEN:
the constraints are evaluated
THEN:
the capital/gross limit, the position-count limit, the same-direction limit, the common-shock group limit, the per-symbol concentration limit, the liquidity/capacity limit, the drawdown/loss limit and the data-quality and execution-evidence gates all bind, and a constraint violation never improves a rank

AC-016:
GIVEN:
one panel in two different input orders
WHEN:
the pure selector is called
THEN:
the selected portfolio, the decision identity and the serialized decision are byte-identical, the panel fingerprint is independent of the input order, and the decision round-trips through serialization

AC-017:
GIVEN:
two admissible portfolios with an equal objective
WHEN:
the best portfolio is chosen
THEN:
an explicit, stable tie-break selects the smaller candidate-id tuple, and a reordered input yields the identical decision

AC-018:
GIVEN:
a synthetic panel on which a greedy per-row selection is admissible
WHEN:
the portfolio objective is optimized
THEN:
the selected portfolio's expected net dollars strictly exceed the greedy per-row result on the same panel, proving the selector is a portfolio objective rather than a per-row ranking

AC-019:
GIVEN:
a selected portfolio and its reservation plan
WHEN:
the plan is inspected and transformed
THEN:
the plan is deterministic and bound to the explicit portfolio version, applying it against a stale version fails closed, and release, expiry and fill-adjustment are pure deterministic transforms that return a new plan with a new identity and never invoke a production reservation writer

AC-020:
GIVEN:
the frozen legacy comparator and one shared candidate panel
WHEN:
the comparator runs
THEN:
the Top-8 technical threshold and limit, the profit-ranking weights and point tables, and the gross-exposure, position-count and same-direction risk limits are reused from the live production code, the comparator's ranking arithmetic and ordering match the live profit-ranking functions on the same observations, the population rule is preserved, the shared panel fingerprint is bound into the result, and no legacy weight, threshold or ordering is modified

AC-021:
GIVEN:
an F7 decision, cash and one frozen legacy comparator result
WHEN:
the comparison record is built
THEN:
all three are reported side by side on the same panel fingerprint, a comparison across different panels fails closed, and the record declares no winner and applies no promotion threshold

AC-022:
GIVEN:
the repository runtime surfaces and the pure selector
WHEN:
F7 integration and purity are audited
THEN:
no run_cycle, scan_opportunities, Telegram, alert, paper, execution, risk, Committee or dashboard path imports or invokes F7, the Feature Bus mode remains off, Paper-v2 and Committee activation are not enabled, and the pure selector reads no clock, environment, filesystem, network or database and holds no order or Committee authority

AC-023:
GIVEN:
the frozen F3, F4, F5 and F6 slices and the live legacy comparator
WHEN:
this increment is applied
THEN:
F3 thresholds, claim identity, state and schemas are unmodified, the F4 lifecycle and persistence are unmodified, the F5 seam is unmodified, the F6 engine, contract and evaluation are unmodified, the legacy Top-8 thresholds, ranking weights, ranking function and scan ordering remain live and unchanged, and F7 reinterprets none of them

AC-024:
GIVEN:
a usable candidate and the applied policy
WHEN:
its allocation is derived
THEN:
the allocation is the lesser of the candidate's requested capital fraction, the policy's per-candidate cap and its liquidity capacity notional, and no leverage, Kelly sizing, covariance optimizer or dynamic risk parity is used

AC-025:
GIVEN:
a candidate, allocation or forecast economic input
WHEN:
a value is malformed, non-finite, boolean, non-positive or out of range
THEN:
it fails closed with a contract error rather than being clipped, coerced, defaulted or ranked

AC-026:
GIVEN:
a candidate panel argument
WHEN:
it is not a list or tuple, contains a non-candidate member, or exceeds the deterministic enumeration bound
THEN:
the selector fails closed rather than degrading to a heuristic or a partial evaluation

AC-027:
GIVEN:
every durable F7 record
WHEN:
it is serialized and reconstituted
THEN:
it round-trips exactly through its canonical key set, and a tampered or drifted durable decision, status or objective fails closed

AC-028:
GIVEN:
the F7 production modules
WHEN:
their identifiers, imports and created tree are audited
THEN:
no ordinal score or confidence becomes a probability or an economic input, no second allocation or selector tree is created, and the decision exposes no probability, quota or top-n attribute

EXPLICITLY OUT OF SCOPE:
- Wiring F7 into run_cycle, scan_opportunities, Telegram, alerts, paper v1/Freqtrade, Paper-v2 or any dashboard
- Replacing, retiring, deleting or re-weighting the legacy comparator, the Top-8 population, the profit-ranking weights or the production risk thresholds
- Paper-v2 activation, Feature Bus activation, Committee activation or funded trading
- An F7 canonical writer, persistence module, database table, JSONL stream or scheduler
- Kelly sizing, leverage, covariance optimization, dynamic risk parity or an unconstrained numerical optimizer
- Converting any ordinal score, alert confidence, target-attainability score, economic-quality score or Committee rubric into a probability or an expected return
- A second calibration spine, a second forecast owner, or a large new app/opip/portfolio or app/opip/opportunity tree
- Modifying architecture documents, the v1.4.3 DOCX, frozen F3/F4/F5/F6 contracts, the ATDD checker, the scope-control contract, workflows, pyproject.toml or any existing F3/F4/F5/F6 test
- F8, F9, dashboard, Bridge or PR #294 work
- Weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off` in `docker-compose.yml`.
- Production `run_cycle` and `scan_opportunities` do not call F7.
- F7 remains SHADOW / NON-AUTHORITATIVE and writes no canonical evidence; the canonical writer remains the single domain write path.
- The frozen legacy comparator remains the live production authority; F7 does not replace it and no legacy path is retired.
- The production F6 model registry ships empty; no calibrated model is registered and no model is auto-promoted.
- F3, F4, F5 and F6 contracts and implementations remain frozen and unmodified.
- No `app/opip/portfolio/`, `app/opip/allocation/`, `app/opip/opportunity/` or second selector tree is created.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled; Paper execution stays isolated from funded order endpoints.
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- A normal push of this feature branch and its OWNER-authorized review, merge and deploy are permitted as recorded above.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_001_vocabulary_is_exact
AC-002 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_002_contract_first_and_movable_pointer
AC-003 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_003_candidate_panel_identity_and_collision
AC-004 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_004_common_evaluation_window_is_explicit_and_consistent
AC-005 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_005_capital_and_exposure_snapshots_are_explicit
AC-006 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_006_policy_has_no_invented_defaults
AC-007 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_007_forecast_evidence_boundary
AC-008 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_008_zero_calibrated_models_abstain
AC-009 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_009_cash_is_a_real_competing_decision
AC-010 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_010_missing_state_abstains
AC-011 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_011_economics_use_f6_returns_once_and_keep_families_separate
AC-012 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_012_uncertainty_is_retained
AC-013 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_013_operating_cost_unknown_is_represented
AC-014 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_014_selection_and_reservation_never_over_commit
AC-015 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_015_constraints_are_enforced
AC-016 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_016_permutation_invariance_and_byte_identity
AC-017 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_017_deterministic_tie_break
AC-018 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_018_objective_beats_greedy_per_row
AC-019 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_019_reservation_plan_is_deterministic_and_version_bound
AC-020 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_020_frozen_legacy_comparator_preserves_semantics
AC-021 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_021_comparison_declares_no_winner
AC-022 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_022_runtime_isolation_and_purity
AC-023 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_023_f3_f4_f5_f6_and_legacy_semantics_unchanged
AC-024 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_024_allocation_policy_is_explicit
AC-025 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_025_malformed_economics_fail_closed
AC-026 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_026_panel_bounds_and_input_types
AC-027 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_027_every_record_round_trips
AC-028 -> tests/test_opip_r3_f7_economic_portfolio_selector.py::test_ac_028_no_score_to_probability_or_allocation

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/__init__.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f7_economic_portfolio_selector.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F7-economic-portfolio-selector.md
AC-003 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-014 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-014 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-015 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-020 -> OHM-Trade-Agent-v1/app/opip/portfolio_comparator.py
AC-021 -> OHM-Trade-Agent-v1/app/opip/portfolio_comparator.py
AC-022 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-022 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f7_economic_portfolio_selector.py
AC-023 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f7_economic_portfolio_selector.py
AC-024 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-025 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-026 -> OHM-Trade-Agent-v1/app/opip/portfolio_selector.py
AC-027 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-028 -> OHM-Trade-Agent-v1/app/opip/portfolio_comparator.py

DEFERRED DISCOVERIES:
- Whether the capital/constraint state should eventually be sourced from the Paper-v2 portfolio authority is a future OWNER increment; this increment only accepts it explicitly and never reads it.
- A ratified selector policy that replaces the observed caps (gross 50%, same-direction 2, single-position capital fraction 20%) is not defined by this increment and must not be invented.
- Dependence-aware evaluation of the selector, an F7 canonical writer and F7 runtime integration remain unauthorized and are future increments.
- The conformance ledger records stale prose that F6 is missing; the live F6 engine, contract and evaluation exist and are frozen, and this increment records the correction in its narrative rather than rewriting canonical audit history.

UNAPPROVED SCOPE CHANGES:
NONE
