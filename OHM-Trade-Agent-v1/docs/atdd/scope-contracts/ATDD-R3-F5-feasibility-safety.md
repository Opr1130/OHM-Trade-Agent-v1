INCREMENT:
ATDD-R3-F5-feasibility-safety

OWNER-APPROVED INTENT:
This is the OWNER-authorized R3-F5 feasibility & safety increment. It creates one pure, deterministic, replayable Feasibility & Safety seam that sits in front of the (not yet built) forecast, calls the existing pre-forecast vetoes, and can abstain with INSUFFICIENT_EVIDENCE. It is SHADOW / NON-AUTHORITATIVE and is not wired into any runtime path.

ARCHITECTURE AUTHORITY. O'Pip Profit Intelligence Platform Architecture v1.4.3, repository authority copy `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`, and its extraction `docs/architecture/v1.4.3/ARCHITECTURE.md`. This increment changes no architecture document and does not vendor the v1.4.3 package.

STARTING SHA. `origin/main` = `3953a3e207ef6b9530f721fcb9513c2e4e9ca03a` (the base this branch was created from). If `origin/main` moves, the branch is inspected; if compatible it is rebased, and if F4/F5/ATDD scope is affected it is reconciled before merge.

LIVE CURRENT AUTHORITY (unchanged by this increment). F5 has no target seam module today. Pre-forecast feasibility is spread across the legacy scanner and service gates; the conformance ledger records CURRENT_OWNER = "Scanner and service gates", CURRENT_WRITER = scan decisions and funnel telemetry, CURRENT_CONSUMERS = alert and paper admission, CURRENT_RUNTIME_AUTHORITY = "Legacy scan", IMPLEMENTATION_STATUS = `LEGACY_ACTIVE`. The shadow decision engine (`app/opip/decision/gates.py`) re-calls the same production evaluators; it is not an authority.

TARGET AUTHORITY (recorded, not executed here). One feasibility seam in front of the forecast. The cutover gate is "Same veto results as the live gates on a frozen candidate set, including abstention". This increment creates the shadow seam only; it does not transfer authority, does not cut over, and does not delete the live vetoes.

KEY LAW. MISSING EVIDENCE IS NEVER FAVORABLE EVIDENCE. A required check whose evidence is absent or unavailable yields `INSUFFICIENT_EVIDENCE`, never `FEASIBLE`. A present-but-malformed required evidence structure raises `FeasibilityContractError` and never becomes zero, default, or pass.

F5 SCOPE (v1) — EXACTLY THREE REQUIRED CHECKS. (1) `MARKET_DATA`, (2) `MARGIN_ELIGIBILITY`, (3) `EXECUTION_LIQUIDITY`. No fourth required check is added; adding one requires concrete architecture evidence and justification recorded in a future contract. F5 owns ONLY pre-forecast feasibility. F5 does NOT own target probability/attainability-as-forecast, calibrated probability, expected return, uncertainty, validity horizon, economic ranking/optimization, allocation, cash competition, Top-N, capital reservation, concentration, sizing, risk-plan construction, AI/Committee opinion, Paper-v2, or funded trading. No mixed "god gate" is built: each check is one narrow mapping over one existing evaluator.

EXPLICITLY EXCLUDED LEGACY GATES (recorded as downstream/legacy overlaps; NOT called from F5). `evaluate_recommendation_gate_item` (AI), `evaluate_deterministic_quality_gate` (mixes target/economic plus equity), `target_quality_gate_from_result` / `evaluate_target_quality_gate`, `economic_quality_gate_from_result` / `evaluate_economic_quality_gate`, `portfolio_risk` as a selector, `trade_action_gate` as an F5 rule, `build_risk_plan` as an F5 rule. Informational-only shadow adapters stay informational only: `evaluate_cross_market_gate`, `evaluate_reference_gate`, `evaluate_market_intelligence_gate`. Missing optional enrichment must NOT make a candidate favorable and must NOT become a new veto. Chase risk is advisory only (ledger) and is NOT added as an F5 veto.

VOCABULARIES (exact). Overall disposition: `FEASIBLE` | `VETO` | `INSUFFICIENT_EVIDENCE` (three tokens only; never PASS/FAIL/ERROR/WATCH/REJECT/NO_TRADE as an overall disposition). Component status: `PASS` | `VETO` | `INSUFFICIENT_EVIDENCE` | `NOT_APPLICABLE`. Version tokens: `FEASIBILITY_DECISION_SCHEMA_VERSION="feasibility-decision-v1"`, `FEASIBILITY_VERSION="feasibility-seam-v1"`, `FEASIBILITY_POLICY_VERSION="feasibility-shadow-policy-v1"`.

AGGREGATION ORDER. Deterministic sequential evaluation in the recorded live hard-filter order: `MARKET_DATA` -> `MARGIN_ELIGIBILITY` -> `EXECUTION_LIQUIDITY`. This matches the live scan, which rejects on market-data invalidity inside `app/scanner/market_scanner.analyze_symbol` (`if not data_validation.qualified: return "data_reject"`), then drops SHORT candidates in `keep_margin_tradeable_candidates`, then applies `deep_validate_candidates` plus the SHORT execution-quality check in `app/jobs/scan_opportunities.py`. No live hard-filter ordering nuance requires a different order.

AGGREGATION RULE. Sequential; any evaluated check with an explicit hard `VETO` yields overall `VETO` and short-circuits the remaining checks (live-order short-circuit), so a proven hard veto is never downgraded to abstention because a later unneeded check is absent. Otherwise, if any evaluated required check is `INSUFFICIENT_EVIDENCE`, overall is `INSUFFICIENT_EVIDENCE`. Otherwise, when every applicable required check is positively proven `PASS`, overall is `FEASIBLE`. `NOT_APPLICABLE` never blocks.

MISSING-VS-MALFORMED RULE. ABSENT / UNAVAILABLE required evidence yields `INSUFFICIENT_EVIDENCE`. PRESENT-BUT-INVALID-STRUCTURE raises `FeasibilityContractError` (invalid enum/status token, bool where numeric is required, non-finite numeric, wrong object type, contradictory identity, unsupported version, naive timestamp). Operational failure is not a policy veto: explicit INELIGIBLE/INVALID evidence yields `VETO`; discovery/evidence UNAVAILABLE/MISSING yields `INSUFFICIENT_EVIDENCE`. Never turn a malformed structure into zero, default, or pass. A malformed field is detected when, and only when, its component is evaluated: a proven earlier hard veto short-circuits and is returned as `VETO`, so unevaluated later evidence cannot pre-empt or mask the veto, and the evidence fingerprint is deliberately lenient so it never raises before the ordered checks run.

DECISION IDENTITY AND TAMPER RESISTANCE. The deterministic `FEAS:<digest>` decision identity binds the decision schema version, the preserved F4 lineage (episode id, source claim id, instrument version id, venue instrument id, detector snapshot id), the source evidence fingerprint, the explicit evaluation time, the F5 version, the F5 policy version, the overall disposition and the canonical ordered `(name, status)` check sequence, using the existing canonical serialization and `stable_hash` helpers. No UUID, receipt timestamp, retry count, process identity or database sequence participates. Because the identity binds the outcome and lineage, a durable record whose disposition, recorded checks or copied lineage fields are altered fails closed on reconstruction. The recorded checks must be a non-empty ordered prefix of the required checks with no duplicate component; only the last recorded check may be a `VETO` (the short-circuit), and a decision with no `VETO` must carry every required check.

EVIDENCE FINGERPRINT. The `FEASEV:<digest>` evidence fingerprint is a pure function of the normalized F5-required inputs only: the direction, the market-validation status/qualification and its validated measurements (candle count/timestamp/age, duplicate/gap/largest-gap/invalid/non-finite counts, ticker and latest OHLC close, ticker-vs-OHLC divergence, the spike flag, warnings and rejection reasons), the margin status/flag/venue/leverage, and the execution status/coverage/spread/drag/coverage-completeness fields. It changes when the evaluated evidence changes and never depends on an object repr.

INSTRUMENT CORRESPONDENCE. Before evaluation, the evidence snapshot must correspond to the episode's venue instrument by normalized instrument-token comparison: uppercase alphanumerics, then the repository's existing Kraken base-alias normalization (`app/scanner/universe.py` `BASE_ALIASES`, e.g. `XBT` -> `BTC`, `XDG` -> `DOGE`) applied to the base of a USD/USDT pair, so F4's Kraken `altname` (`XBTUSD`) equals the scanner's canonical snapshot symbol (`BTC/USD`). This is an identity-consistency guard, not a trading threshold: every populated snapshot identifier (`symbol`, `kraken_public_symbol`, `primary_pair`) must agree with the episode venue instrument, so a snapshot whose symbol matches but whose public/primary pair identifies a different instrument fails closed rather than being stamped with the episode's lineage. No F5-level instrument-normalization policy beyond this reuse is invented.

STRUCTURAL VALIDATION OF EVALUATED EVIDENCE. When a component is evaluated, its concrete evidence type and required fields are validated: `MARKET_DATA` requires a concrete `MarketDataValidation` with its required numeric/boolean/list fields well-typed (a duck-typed object or a `PASS` record with a malformed measurement fails closed); `EXECUTION_LIQUIDITY` requires a concrete `ExecutionValidation` with a known `status`, `book_coverage_status` and `recent_trade_status`, plus well-typed numeric/boolean fields; a `VALID` record must carry measured (COMPLETE/PARTIAL/INSUFFICIENT) book coverage and the spread/coverage fields the quality route reads, while an `UNAVAILABLE`/`INVALID` record must carry UNAVAILABLE coverage; the coverage token must agree with `buy_fully_covered`/`sell_fully_covered` (COMPLETE/PARTIAL requires both fully covered; INSUFFICIENT requires an uncovered side). The exact uppercase `trade_direction` token (`LONG`/`SHORT`) the producer emits is required. `MARGIN_ELIGIBILITY` requires the recorded SHORT status (the exact uppercase `ELIGIBLE`/`INELIGIBLE`/`UNAVAILABLE` token the producer emits) to agree with the `margin_eligible` flag, a non-empty text `margin_venue_symbol`, and a finite numeric `margin_max_leverage` on an `ELIGIBLE` record. A SHORT `EXECUTION_LIQUIDITY` result additionally requires the BTNL margin-venue provenance marker the live route writes onto the snapshot; without it the attached evidence cannot be trusted to be the BTNL book the SHORT quality thresholds are defined for, so it is `INSUFFICIENT_EVIDENCE` (missing provenance is never favorable) rather than a `PASS`. A value that cannot be represented as a finite float (including a huge integer) is `FeasibilityContractError` when its component is evaluated and is never allowed to pre-empt an earlier proven veto during fingerprint normalization. Every market measurement except `ticker_last` must be finite on every record. A record the live validator has already rejected (`REJECT`) may carry the raw non-finite `ticker_last` it stores and still maps to `VETO` (an explicit invalidity is a policy veto, not a structural error); that accepted non-finite state is preserved as a canonical fingerprint token so it is distinct from an absent ticker. `NOT_APPLICABLE` is valid only for the SHORT-only `MARGIN_ELIGIBILITY` check, so `MARKET_DATA` and `EXECUTION_LIQUIDITY` can never be recorded inapplicable to reach `FEASIBLE` without being proven.

INSTRUMENT IDENTIFIERS. Every populated snapshot identifier (`symbol`, `kraken_public_symbol`, `primary_pair`) must normalize to a usable instrument token and agree with the episode venue instrument; a populated identifier that is non-string or has no alphanumeric content fails closed rather than being discarded, so a partially malformed snapshot cannot be accepted on one matching field.

MARGIN CONSISTENCY. For a SHORT candidate the explicit `margin_validation_status` and the live `margin_eligible` flag must agree (`ELIGIBLE` requires `margin_eligible` true; `INELIGIBLE`/`UNAVAILABLE` require false). The exact uppercase token the producer emits is required; a case-folded or unknown token fails closed. Contradictory margin evidence fails closed, so an `ELIGIBLE` status cannot override the live margin safeguard that keeps a SHORT only when `margin_eligible` is true.

SOURCE EVALUATORS (reused; not duplicated). `MARKET_DATA` reads the existing `MarketDataValidation` produced by `app/scanner/market_data_validation.validate_market_data` (status vocabulary `PASS`/`WARN`/`REJECT`, `qualified` flag; live absent-evidence sentinel `UNAVAILABLE`). `MARGIN_ELIGIBILITY` reuses the thin adapter `app/opip/decision/gates.evaluate_margin_gate` (over `app/scanner/margin_eligibility.validate_short_margin_eligibility`, status vocabulary `ELIGIBLE`/`INELIGIBLE`/`UNAVAILABLE`). `EXECUTION_LIQUIDITY` reuses the thin adapter `app/opip/decision/gates.evaluate_execution_gate` (over `app/scanner/execution_validation.evaluate_execution`, status vocabulary `VALID`/`UNAVAILABLE`/`INVALID`, plus the OFFLINE SHORT route `app/scanner/short_execution_quality.short_execution_is_tradeable(..., refresh_margin_book=False)`). No exchange/network refresh occurs in F5. Every adapter call passes the explicit `evaluated_at=evaluation_time` and never relies on a `GateResult` default clock. Only a thin market-data mapping adapter is added, inside the F5 seam itself.

PARITY FIXTURE POPULATION (deterministic, offline, real repo types/evaluator outputs, no network): `LONG_VALID`, `SHORT_VALID`, `MARKET_INVALID`, `MARKET_UNAVAILABLE`, `MARKET_MISSING`, `SHORT_MARGIN_INELIGIBLE`, `SHORT_MARGIN_UNAVAILABLE`, `EXECUTION_INVALID`, `EXECUTION_MISSING`, `SHORT_EXECUTION_REJECTED`, `MALFORMED_MARKET`, `MALFORMED_MARGIN`, `MALFORMED_EXECUTION`. Per-record parity proof: legacy policy/evaluator source, legacy hard-gate disposition, F5 component result, and F5 overall. PARITY BLOCKERS: a known live hard veto becoming `FEASIBLE`; missing required evidence becoming `FEASIBLE`; F5 inventing a hard veto absent from live policy/architecture; duplicated numeric thresholds; network refresh.

CUTOVER GATE (recorded, not executed). Same veto results as the live gates on a frozen candidate set, including abstention.

SHADOW / NON-AUTHORITATIVE STATUS. This increment grants no production, admission, allocation, risk, paper, order, or funded authority. It is not wired into `run_cycle`, `scan_opportunities`, Telegram, alerts, paper, execution, risk, the Committee, or the dashboard. It activates no Feature Bus. It writes no canonical evidence.

F4 GOVERNANCE HANDOFF (OWNER-authorized, narrow). `tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_014_current_vs_target_authority` previously pinned the globally movable ATDD pointer to the F4 opportunity-lifecycle lineage. This OWNER increment removes ONLY that permanent global-pointer ownership assertion. Every substantive AC-014 assertion is preserved: the F4 implementation contract exists; the frozen increment identity is recorded; the contract names the correct increment; the lifecycle module is shadow; the contract module is non-authoritative; there is no production F4 authority; no Feature Bus activation; no consumer cutover. No F4 state semantics, identity, persistence, policy, or runtime is changed.

NO-NEW-THRESHOLD RULE. F5 introduces no new numeric trading policy and duplicates no threshold. No spread, liquidity, volume, depth, leverage, slippage, confidence, expected-return, or profit threshold is introduced; the seam only re-maps existing evaluator outcomes. Fixtures may use representative values solely to exercise the existing policies.

NO AI. No OpenAI/Anthropic/DeepSeek/Committee import or call, and no AI confidence or recommendation authority.
NO F6. No forecast, return, probability, calibration, confidence interval, uncertainty, or validity-horizon behavior.
NO F7. No optimization, net-dollars, ranking, cash/no-trade comparator, reservation, sizing, or concentration behavior.
NO PERSISTENCE. No F5 persistence, writer, table, JSONL, or scheduler.
NO RUNTIME WIRING. No `run_cycle`, `scan_opportunities`, Telegram, alert, paper, execution, risk, Committee, or dashboard integration.

MERGE / DEPLOY AUTHORIZATION. This OWNER prompt authorizes the full end-to-end path for this increment: local gates, a feature branch and push, a pull request, the review loop, exact-head CI, merge to `main` on the exact verified head, post-merge `pytest.yml` on `main` for the exact merge SHA, and exactly one owner `/deploy <40-char MERGE_SHA>` on issue #64 followed by the production deploy through the existing control plane, ending in a verified production receipt. It does not authorize F5 cutover, F5 runtime orchestration, an F5 persistence writer, F6, F7, alert cutover, Telegram filtering, paper cutover, portfolio sizing, funded trading, legacy deletion, a risk rewrite, or a Committee veto. PR #294 (bridge) is unrelated and is not touched, merged, rebased, closed, cherry-picked, or depended on.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (spine): "Feasibility and calibrated statistical forecasts -> economic selector -> deterministic risk checks and atomic capital reservation -> realistic paper execution."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3 (point-in-time / missing evidence): "Missing evidence is never favorable. Feasibility can abstain with INSUFFICIENT_EVIDENCE."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 4 (component table): F5 "Feasibility & Safety", disposition "Veto / abstention", requirement "Enforce market, data, liquidity, and execution constraints."; section 4 pipeline places "FEASIBILITY + CALIBRATED FORECASTS" after "IGNITION DETECTOR + OPPORTUNITY LIFECYCLE" and before "ECONOMIC SELECTION + CAPITAL RESERVATION".
- `docs/architecture/v1.4.3/ARCHITECTURE.md` line 446: "Missing evidence is never favorable evidence. The approved selector can abstain with INSUFFICIENT_EVIDENCE."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` line 118 and section on AI: a Committee recommendation is advisory classification, not an executable order or veto; it cannot create or adjust a probability, so it cannot become an F5 hard veto.
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` F5 row: ARCHITECTURE_REQUIREMENT "Market, data, liquidity, and execution constraints. Veto or abstain. Missing evidence is never favorable. INSUFFICIENT_EVIDENCE is an allowed abstention."; CURRENT_IMPLEMENTATION lists `market_data_validation.py`, margin and short tradeability checks, `execution_validation.py`, `target_attainability.py`, `economic_quality_gate.py`, `portfolio_risk.py`, `trade_action_gate.py`, `app/services/risk.py`, shadow-adapted by `app/opip/decision/gates.py`; TARGET_AUTHORITY "One feasibility seam in front of the forecast"; CUTOVER_GATE "Same veto results as the live gates on a frozen candidate set, including abstention"; DELETE_GATE "Do not delete the live vetoes first"; BLOCKERS "No target seam module".
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R3 item 3: "One feasibility seam that calls the existing vetoes and can abstain with INSUFFICIENT_EVIDENCE."; R3 is shadow/evidence-first: no new production runtime authority, no Paper v2 cutover, no legacy deletion, and the Feature Bus mode stays `off`.
- Reused rather than duplicated: `app/opip/contracts/serialization.py` (`stable_hash`, `iso_z`), `app/opip/contracts/temporal.py` (`require_utc`, `TemporalIntegrityError`), `app/opip/decision/gates.py` (`evaluate_margin_gate`, `evaluate_execution_gate`), `app/opip/decision/models.py` (`GateStatus`, `ReasonCode`), `app/scanner/market_data_validation.py`, `app/scanner/margin_eligibility.py`, `app/scanner/execution_validation.py`, `app/scanner/short_execution_quality.py`, and the frozen F4 lifecycle types `app/opip/contracts/opportunity.py` / `app/opip/opportunity_lifecycle.py`.
- `docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md`, `ATDD-R3-F3-ignition-implementation.md`, `ATDD-R3-F4-opportunity-lifecycle.md`, `ATDD-R3-F4-opportunity-lifecycle-implementation.md`, and `ATDD-R3-F4-opportunity-lifecycle-persistence.md`: F3 and F4 are frozen and are not modified by this increment beyond the recorded AC-014 global-pointer handoff.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture; `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the F5 contract vocabularies and the sealed architecture
WHEN:
the F5 vocabulary module is inspected
THEN:
the overall disposition tokens are exactly FEASIBLE, VETO and INSUFFICIENT_EVIDENCE, the component status tokens are exactly PASS, VETO, INSUFFICIENT_EVIDENCE and NOT_APPLICABLE, the check names are exactly MARKET_DATA, MARGIN_ELIGIBILITY and EXECUTION_LIQUIDITY, and the version tokens are exactly feasibility-decision-v1, feasibility-seam-v1 and feasibility-shadow-policy-v1

AC-002:
GIVEN:
one ACTIVE F4 OpportunityEpisode with its frozen claim lineage
WHEN:
the pure F5 seam evaluates it
THEN:
the decision preserves the immutable lineage (episode_id, source claim id, instrument version id, venue instrument id, detector snapshot id) and the explicit F5 evaluation time, and F5 mints no episode, mutates no lifecycle state, deadline or terminal reason, and reinterprets no detector transition

AC-003:
GIVEN:
a DEFERRED or TERMINAL F4 OpportunityEpisode
WHEN:
the pure F5 seam evaluates it
THEN:
it fails closed with FeasibilityContractError, lifecycle states are never mapped into F5 dispositions, and F4 remains the owner of lifecycle readiness

AC-004:
GIVEN:
one ACTIVE episode, one evidence snapshot and one explicit evaluation time
WHEN:
the F5 decision is produced more than once
THEN:
the FEAS: decision identity is a deterministic function of the decision schema version, the preserved F4 lineage, the source evidence fingerprint, the explicit evaluation time, the F5 version, the F5 policy version, the overall disposition and the canonical ordered check sequence, it uses no UUID, receipt timestamp, retry, pid or database sequence, and a forged decision identity or a durable record whose disposition, recorded checks or copied lineage fields have been altered fails closed

AC-005:
GIVEN:
an evaluation time input
WHEN:
the F5 seam validates it
THEN:
only an explicit timezone-aware UTC datetime is accepted, a naive timestamp or a non-datetime value raises FeasibilityContractError, no datetime.now, utcnow or time.time is read, and the thin adapters are called with an explicit evaluated_at equal to the supplied evaluation time

AC-006:
GIVEN:
required market-data evidence that the live scanner accepts as usable
WHEN:
the MARKET_DATA component runs
THEN:
the component status is PASS

AC-007:
GIVEN:
market-data evidence that the live scanner rejects as explicitly invalid
WHEN:
the MARKET_DATA component runs
THEN:
the component status is VETO

AC-008:
GIVEN:
absent, missing or explicitly unavailable required market-data evidence
WHEN:
the MARKET_DATA component runs
THEN:
the component status is INSUFFICIENT_EVIDENCE and never PASS or FEASIBLE

AC-009:
GIVEN:
a LONG candidate that does not use the margin venue
WHEN:
the MARGIN_ELIGIBILITY component runs
THEN:
the component status is NOT_APPLICABLE and never PASS

AC-010:
GIVEN:
a SHORT candidate with explicit live margin eligibility
WHEN:
the MARGIN_ELIGIBILITY component runs
THEN:
the component status is PASS

AC-011:
GIVEN:
a SHORT candidate explicitly ineligible for margin
WHEN:
the MARGIN_ELIGIBILITY component runs
THEN:
the component status is VETO

AC-012:
GIVEN:
a SHORT candidate whose margin eligibility discovery is unavailable
WHEN:
the MARGIN_ELIGIBILITY component runs
THEN:
the component status is INSUFFICIENT_EVIDENCE, not VETO, and no new leverage threshold is introduced

AC-013:
GIVEN:
usable validated execution evidence for a candidate
WHEN:
the EXECUTION_LIQUIDITY component runs
THEN:
the component status is PASS

AC-014:
GIVEN:
execution evidence the live structural validator marked INVALID
WHEN:
the EXECUTION_LIQUIDITY component runs
THEN:
the component status is VETO

AC-015:
GIVEN:
missing or explicitly unavailable required execution evidence
WHEN:
the EXECUTION_LIQUIDITY component runs
THEN:
the component status is INSUFFICIENT_EVIDENCE and never PASS or FEASIBLE

AC-016:
GIVEN:
a SHORT candidate with structurally valid execution evidence that the offline SHORT execution-quality evaluator rejects
WHEN:
the EXECUTION_LIQUIDITY component runs on the precomputed OFFLINE route
THEN:
the component status is VETO, the rejection is reproduced by reusing the existing evaluator with no exchange or network refresh, and no new numeric quality threshold is introduced

AC-017:
GIVEN:
the three required components in the recorded live hard-filter order
WHEN:
overall feasibility is aggregated
THEN:
aggregation is deterministic and sequential, any evaluated hard VETO yields overall VETO and short-circuits the remaining checks, otherwise any INSUFFICIENT_EVIDENCE yields overall INSUFFICIENT_EVIDENCE, otherwise all applicable PASS yields FEASIBLE, and NOT_APPLICABLE never blocks or downgrades a proven veto

AC-018:
GIVEN:
present-but-malformed required evidence structure
WHEN:
the F5 seam evaluates it
THEN:
it raises FeasibilityContractError for an invalid enum or status token, a bool where a numeric is required, a non-finite numeric, a wrong object type (including a duck-typed or non-concrete evidence object), a contradictory identity (including contradictory SHORT margin evidence and a snapshot that does not correspond to the episode venue instrument), a non-applicable always-required check, an unsupported version or a naive timestamp, and it never converts malformed evidence into zero, default or pass; a malformed field is detected only when its component is evaluated, so a proven earlier hard veto short-circuits and returns VETO rather than being pre-empted by malformed later evidence

AC-019:
GIVEN:
the F5 production modules
WHEN:
their thresholds and imports are audited
THEN:
they introduce no new independent numeric trading threshold, duplicate no threshold, and only re-map the existing evaluator outcomes, with fixtures using values solely to exercise the existing policies

AC-020:
GIVEN:
the frozen reproduction fixture population LONG_VALID, SHORT_VALID, MARKET_INVALID, MARKET_UNAVAILABLE, MARKET_MISSING, SHORT_MARGIN_INELIGIBLE, SHORT_MARGIN_UNAVAILABLE, EXECUTION_INVALID, EXECUTION_MISSING, SHORT_EXECUTION_REJECTED, MALFORMED_MARKET, MALFORMED_MARGIN and MALFORMED_EXECUTION
WHEN:
the parity matrix is evaluated offline
THEN:
every live hard veto is reproduced as F5 VETO, unavailable or missing required evidence is reproduced as INSUFFICIENT_EVIDENCE, applicable valid evidence is reproduced as FEASIBLE, no known live hard veto becomes FEASIBLE, and no fixture introduces a network refresh

AC-021:
GIVEN:
absent optional informational enrichment (cross-market, reference or market-intelligence evidence, and advisory chase risk)
WHEN:
the F5 seam evaluates a candidate whose required checks are otherwise proven
THEN:
the absent enrichment neither makes the candidate favorable nor becomes a new veto, and the informational-only shadow adapters remain informational only

AC-022:
GIVEN:
the F5 decision path
WHEN:
its authorities and imports are inspected
THEN:
it performs no AI/Committee call and imports no AI or Committee authority, and no AI confidence or recommendation can become an F5 hard veto

AC-023:
GIVEN:
the F5 decision path
WHEN:
its behavior and vocabulary are inspected
THEN:
it produces no F6 forecast, return, probability, calibration, confidence-interval, uncertainty or validity-horizon behavior and no F7 optimization, net-dollars, ranking, cash/no-trade, reservation, sizing or concentration behavior

AC-024:
GIVEN:
the repository runtime surfaces
WHEN:
F5 integration is audited
THEN:
no run_cycle, scan_opportunities, Telegram, alert, paper, execution, risk, Committee or dashboard path invokes, imports or consumes F5, and legacy scan and service gates remain the live authority

AC-025:
GIVEN:
the production Compose configuration
WHEN:
the Feature Bus mode is inspected
THEN:
OPIP_FEATURE_BUS_MODE remains off and F5 activates no Feature Bus behavior

AC-026:
GIVEN:
the frozen F3 detector and F4 lifecycle semantics
WHEN:
this F5 increment is applied
THEN:
F3 thresholds, claim identity, state and schemas are unmodified, the F4 pure lifecycle semantics and persistence are unmodified, and F5 reinterprets neither

AC-027:
GIVEN:
the completed F4 opportunity-lifecycle increments and the movable ATDD pointer
WHEN:
the F5 increment becomes active
THEN:
the ATDD pointer names this F5 increment, the F4 lifecycle test no longer owns or pins the global pointer value, and every substantive F4 AC-014 isolation assertion is preserved

AC-028:
GIVEN:
the F5 production modules
WHEN:
durable effects are audited
THEN:
F5 creates no persistence module, writer, table, JSONL stream or scheduler, writes no canonical evidence, and reuses no canonical writer

EXPLICITLY OUT OF SCOPE:
- F5 cutover, runtime orchestration, admission authority, or replacing the live scanner/service gates
- An F5 persistence module, writer, table, JSONL stream, or scheduler
- F6 forecast (probability, expected return, uncertainty, validity horizon, calibration)
- F7 economic/portfolio selector (net dollars, ranking, cash/no-trade comparator, reservation, sizing, concentration)
- An alert cutover, Telegram filtering, paper cutover, portfolio sizing, funded trading, legacy deletion, a risk rewrite, or a Committee veto
- Wiring F5 into `run_cycle`, `scan_opportunities`, alerts, paper, execution, risk, the Committee, or the dashboard
- Activating the Feature Bus or changing `OPIP_FEATURE_BUS_MODE`
- Adding a fourth required F5 check, inventing a new hard veto, or adding a numeric trading threshold
- Adding AI/Committee imports or calls, or letting AI confidence become an F5 veto
- Modifying architecture documents, the v1.4.3 DOCX, the frozen F3/F4 contracts, the ATDD checker, the scope-control contract, workflows, `pyproject.toml`, or any existing F3/F4 test beyond the recorded AC-014 handoff
- Touching, merging, rebasing, closing, cherry-picking or depending on PR #294 (bridge)
- Weakening, deleting, or skipping any existing test

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off` in `docker-compose.yml`.
- Production `run_cycle` and `scan_opportunities` do not call F5 and do not call any F3 -> F4 -> F5 wiring.
- F5 remains SHADOW / NON-AUTHORITATIVE and writes no canonical evidence; the canonical writer remains the single domain write path.
- F3 detector contracts and implementation remain frozen and unmodified.
- F4 lifecycle state semantics, identity, persistence, policy and runtime remain unmodified except the recorded AC-014 global-pointer handoff.
- No `app/opip/feasibility/` subtree is created; the seam is the single module `app/opip/feasibility.py`.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled; Paper execution stays isolated from funded order endpoints.
- Committee authority is unchanged and remains shadow-only.
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- A normal push of this feature branch and its OWNER-authorized review, merge and deploy are permitted as recorded above.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r3_f5_feasibility.py::test_ac_001_vocabulary_is_exact
AC-002 -> tests/test_opip_r3_f5_feasibility.py::test_ac_002_active_episode_lineage_preserved
AC-003 -> tests/test_opip_r3_f5_feasibility.py::test_ac_003_non_active_episode_rejected
AC-004 -> tests/test_opip_r3_f5_feasibility.py::test_ac_004_decision_identity_is_deterministic
AC-005 -> tests/test_opip_r3_f5_feasibility.py::test_ac_005_evaluation_time_is_explicit_aware_utc
AC-006 -> tests/test_opip_r3_f5_feasibility.py::test_ac_006_market_valid_is_pass
AC-007 -> tests/test_opip_r3_f5_feasibility.py::test_ac_007_market_explicit_invalid_is_veto
AC-008 -> tests/test_opip_r3_f5_feasibility.py::test_ac_008_market_missing_or_unavailable_is_insufficient
AC-009 -> tests/test_opip_r3_f5_feasibility.py::test_ac_009_long_margin_is_not_applicable
AC-010 -> tests/test_opip_r3_f5_feasibility.py::test_ac_010_short_margin_eligible_is_pass
AC-011 -> tests/test_opip_r3_f5_feasibility.py::test_ac_011_short_margin_ineligible_is_veto
AC-012 -> tests/test_opip_r3_f5_feasibility.py::test_ac_012_short_margin_unavailable_is_insufficient
AC-013 -> tests/test_opip_r3_f5_feasibility.py::test_ac_013_execution_valid_is_pass
AC-014 -> tests/test_opip_r3_f5_feasibility.py::test_ac_014_execution_explicit_invalid_is_veto
AC-015 -> tests/test_opip_r3_f5_feasibility.py::test_ac_015_execution_missing_or_unavailable_is_insufficient
AC-016 -> tests/test_opip_r3_f5_feasibility.py::test_ac_016_short_execution_quality_reject_is_reproduced
AC-017 -> tests/test_opip_r3_f5_feasibility.py::test_ac_017_aggregation_is_deterministic
AC-018 -> tests/test_opip_r3_f5_feasibility.py::test_ac_018_malformed_required_evidence_fails_structurally
AC-019 -> tests/test_opip_r3_f5_feasibility.py::test_ac_019_no_new_threshold_ownership
AC-020 -> tests/test_opip_r3_f5_feasibility.py::test_ac_020_frozen_population_parity
AC-021 -> tests/test_opip_r3_f5_feasibility.py::test_ac_021_optional_enrichment_is_not_a_new_veto
AC-022 -> tests/test_opip_r3_f5_feasibility.py::test_ac_022_no_ai_or_committee_authority
AC-023 -> tests/test_opip_r3_f5_feasibility.py::test_ac_023_no_f6_or_f7_behavior
AC-024 -> tests/test_opip_r3_f5_feasibility.py::test_ac_024_no_runtime_consumer
AC-025 -> tests/test_opip_r3_f5_feasibility.py::test_ac_025_feature_bus_off
AC-026 -> tests/test_opip_r3_f5_feasibility.py::test_ac_026_f3_f4_semantics_unchanged
AC-027 -> tests/test_opip_r3_f5_feasibility.py::test_ac_027_f4_pointer_handoff
AC-028 -> tests/test_opip_r3_f5_feasibility.py::test_ac_028_no_new_persistence

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F5-feasibility-safety.md
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/feasibility.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/__init__.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/feasibility.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-014 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-015 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-018 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-019 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-020 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-020 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-021 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-022 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-022 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-023 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-023 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-024 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-025 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-026 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-027 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-027 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-027 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-028 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-028 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py

DEFERRED DISCOVERIES:
- F5 cutover to become the admission path, replacing the live scanner/service gates, is a future OWNER increment gated on the recorded cutover gate.
- F5 runtime orchestration and any runtime consumer remain unauthorized.
- The F4 numeric validity-horizon source (F6) remains unresolved and is not invented here.
- Whether a future F5 policy needs an ABSTAIN-adjacent reason taxonomy beyond the three recorded dispositions is not authorized here.
- Consumer migration order and per-clock stop times for the F4 clocks remain a future cutover increment.
- Richer venue-instrument normalization beyond uppercase-alphanumeric token equality, and whether a canonical instrument-identity mapping is needed, are not invented by this increment.
- Exact execution-evidence venue provenance (proving the attached `ExecutionValidation` came from the refreshed BTNL book rather than the spot book) would require a provenance field on the production `ExecutionValidation` type and the live SHORT refresh path. `app/scanner/execution_validation.py` is frozen by this increment, so F5 only enforces the snapshot-level BTNL margin-venue marker as a conservative guard; true execution-record provenance is deferred to a future increment that may widen the authorized paths.

UNAPPROVED SCOPE CHANGES:
NONE
