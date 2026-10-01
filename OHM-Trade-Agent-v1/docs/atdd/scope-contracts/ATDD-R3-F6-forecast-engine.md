INCREMENT:
ATDD-R3-F6-forecast-engine

OWNER-APPROVED INTENT:
This is the OWNER-authorized R3-F6 forecast-engine increment. It creates one pure, deterministic, replayable Forecast Engine boundary that sits after the F5 Feasibility & Safety seam, consumes only an F5 decision whose disposition is FEASIBLE, and either produces an execution-aware FORECAST or abstains with INSUFFICIENT_EVIDENCE. It is SHADOW / NON-AUTHORITATIVE and is not wired into any runtime path.

ARCHITECTURE AUTHORITY. O'Pip Profit Intelligence Platform Architecture v1.4.3, repository authority copy `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`, and its extraction `docs/architecture/v1.4.3/ARCHITECTURE.md`. This increment changes no architecture document and does not vendor the v1.4.3 package.

STARTING SHA. `origin/main` = `abf00fc797683e7ac99a9220c5d40bbb0d712eaa` (the base this branch was created from). If `origin/main` moves, the branch is inspected; if compatible it is rebased, and if forecast/F5/ATDD scope is affected it is reconciled before merge.

LIVE CURRENT AUTHORITY (unchanged by this increment). F6 has no target engine module today. The conformance ledger records F6 CURRENT_OWNER = "None", CURRENT_WRITER = "None", CURRENT_CONSUMERS = "None", CURRENT_RUNTIME_AUTHORITY = "None", IMPLEMENTATION_STATUS = `MISSING`. Ranking score, alert confidence and the Committee 0-100 rubric exist but are ordinal and are not a forecast. The economic gate is pass/fail on move, net profit and reward-to-risk, and is not this engine.

TARGET AUTHORITY (recorded, not executed here). One calibrated forecast owner for probability, expected return, uncertainty, and validity horizon. The cutover gate is "Proper scores and a declared horizon, sealed from future labels". This increment creates the shadow engine and the model-artifact contract only; it registers no calibrated model, does not cut over, and does not delete or rewire any legacy path.

KEY LAW — MISSING EVIDENCE IS NEVER FAVORABLE EVIDENCE. With no qualified calibrated model the engine returns `INSUFFICIENT_EVIDENCE` with the machine-readable reason `NO_CALIBRATED_MODEL`. A `RESEARCH_ONLY` artifact yields `INSUFFICIENT_EVIDENCE` with `MODEL_NOT_CALIBRATED_FOR_SHADOW_AUTHORITY`. Only a structurally valid, compatible `CALIBRATED_SHADOW` artifact whose evaluation report is present in the supplied trusted metadata and whose kind has an explicitly registered trusted adapter may produce a `FORECAST`, and even then the result stays shadow / non-authoritative. An upstream F5 `VETO` or `INSUFFICIENT_EVIDENCE` fails closed with `ForecastContractError`; it is never converted into an F6 abstention.

NO-QUALIFIED-MODEL REALITY. The repository proves no ForecastEngine and no registered calibrated model. The trusted production registry ships empty. This increment does not claim a valid calibrated model exists, does not train from a mixed label store, and does not promote an ordinal score. An end state with zero calibrated artifacts and deterministic abstention is SUCCESS.

F5 ACTIVE_INCREMENT HANDOFF (OWNER-authorized, narrow). `tests/test_opip_r3_f5_feasibility.py::test_ac_027_f4_pointer_handoff` previously pinned the globally movable ATDD pointer to the F5 increment. This OWNER increment removes ONLY that permanent global-pointer ownership assertion. Every substantive F5 acceptance criterion is preserved: the F5 contract exists; the F5 increment identity is recorded; the F4 pointer pin is removed; the F4 substantive AC-014 protections remain; F5 runtime shadow; no consumers; Feature Bus OFF; no persistence; no F6/F7 leakage in F5 implementation. No F5 semantics, identity, or frozen F5 scope contract is changed; this F6 contract is the authorization record.

F6 COMPLETED-INCREMENT MOVABLE-POINTER HANDOFF (OWNER-authorized, `ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1`). AC-036 originally required the global `docs/atdd/ACTIVE_INCREMENT` pointer to equal this F6 increment. A completed increment must not permanently own the pointer, because only one increment can be active at a time; that ownership pin blocks every later approved increment. AC-036 now proves F6's own identity from this frozen contract and the F6 test module, records that neither the completed F5 increment nor the completed F6 increment pins the pointer, and requires only that the pointer resolve to an existing scope contract. Every substantive F6 guarantee is unchanged and still asserted: the engine stays pure/deterministic, SHADOW / NON-AUTHORITATIVE, with no runtime consumers, the Feature Bus off, no F7 allocation, no calibrated-model shortcut, no AI/Committee probability, no persistence, and no paper/funded authority. A structural guard in `tests/test_atdd_scope_control.py` fails closed if any increment test reintroduces a global-pointer comparison against its own increment identity. No forecast behavior, contract identity, or frozen F6 semantic is changed.

NO SCORE TO PROBABILITY. This increment does not relabel any existing score as a probability. The technical score, explosion score, opportunity score, tradeability score, ranking score, alert/Chief/AI/Committee confidence, the Committee 0-100 rubric, the economic-quality score, the target-attainability score and the F5 feasibility result stay ordinal. There is no `score/100` probability and no invented logistic mapping.

NO CALIBRATION WITHOUT EVIDENCE. A model is registered only if qualifying evidence proves it: a declared feature schema, a point-in-time training cutoff, a sealed evaluation population, declared entry and path horizons, execution-outcome labels, post-fill path labels, a missingness and fidelity treatment, proper scoring, reliability evidence, no future leakage, an explicit model version and an explicit already-approved calibrated-shadow status. Nothing is inferred. With no such artifact the registry stays empty.

MERGE / DEPLOY AUTHORIZATION. This OWNER prompt authorizes the full end-to-end path for this increment: local gates, a feature branch and push, a pull request, the review loop, exact-head CI, merge to `main` on the exact verified head, post-merge `pytest.yml` on `main` for the exact merge SHA, and exactly one owner `/deploy <40-char MERGE_SHA>` on issue #64 followed by the production deploy through the existing control plane, ending in a verified production receipt. It does not authorize F7, forecast operational integration, an F6 canonical writer, model promotion, strategy promotion, Paper-v2 activation, or funded trading. PR #294 (bridge) is unrelated and is not touched, merged, rebased, closed, cherry-picked, or depended on.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (spine): "Feasibility and calibrated statistical forecasts -> economic selector -> deterministic risk checks and atomic capital reservation -> realistic paper execution."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3 (point-in-time / missing evidence): "Missing evidence is never favorable. Feasibility can abstain with INSUFFICIENT_EVIDENCE." and "Evidence is eligible only if it was available by the cutoff, not merely dated before it." and "Learning artifacts carry training cutoff, dataset manifest, label policy and effective version; future outcomes cannot enter earlier training or inference."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 4 (component table): F6 "Forecast Engine", disposition "Forecasts; no allocation", requirement "Execution-aware probabilities, returns, uncertainty, and validity horizon."; section 4 also states "Forecast execution probabilities separately from conditional post-fill paths; the Committee cannot create or adjust either probability." and "Do not count no-fill intents as losing trades or omit them from the intent population."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 10 (Forecast quality): "Proper scoring rules and reliability by execution event and conditional post-fill outcome, with sample counts and uncertainty. LLM confidence is not forecast probability."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` Appendix A section 9 (Forecast, outcome, and statistical contract): execution and post-fill path are modeled separately; the policy horizon is anchored to first fill; a fully observed horizon expiry is a known TIMEOUT whose economic result uses the declared executable exit policy and does not imply a negative return; a coverage gap before resolution is INCOMPLETE_COVERAGE; calibration uses proper scoring rules and reliability analysis for execution probabilities and conditional path probabilities separately; dependence-aware evaluation resamples contiguous time blocks; no universal block length or N_eff threshold is an architecture constant.
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` F6 row: ARCHITECTURE_REQUIREMENT "Execution-aware probability, expected return, uncertainty, and validity horizon. No allocation. LLM confidence is not a probability."; CURRENT_IMPLEMENTATION "No `ForecastEngine`. Decision records set `calibrated_probability: False`."; IMPLEMENTATION_STATUS `MISSING`; CUTOVER_GATE "Proper scores and a declared horizon, sealed from future labels"; NOTES "Do not promote an ordinal score into this feature."
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R3 item 4: "One calibrated forecast owner for probability, expected return, uncertainty, and validity horizon. Scores and Committee confidence stay out of this owner."; R3 is shadow/evidence-first with no production runtime authority and the Feature Bus mode stays `off`; Phase 7 calibration is "R3 forecast plus R5 labels" and must not add a second calibration spine.
- `docs/architecture/v1.2/E_FORECAST_OUTCOME_CONTRACT.md`: entry execution outcomes `NO_FILL`/`PARTIAL_FILL`/`FULL_FILL`; post-fill path outcomes `TARGET`/`STOP`/`TIMEOUT`/`RISK_EXIT`; `INCOMPLETE_COVERAGE`; fidelity grades A/B/C; promotion reports disclose the full intent population including Grade C and missingness.
- `docs/architecture/v1.2/G_STATISTICAL_PROTOCOL.md`: proper scoring rules and reliability analysis separately for execution and conditional path probabilities; thin cohorts abstain; missingness and fidelity-grade sensitivity required; sampled-success-only evidence is forbidden; no universal block length or N_eff threshold is an architecture constant.
- Reused rather than duplicated: `app/opip/contracts/serialization.py` (`stable_hash`, `iso_z`), `app/opip/contracts/temporal.py` (`require_utc`, `TemporalIntegrityError`), and the frozen F4 `OpportunityEpisode` / F5 `FeasibilityDecision` contracts.
- `docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md`, `ATDD-R3-F4-opportunity-lifecycle.md`, `ATDD-R3-F4-opportunity-lifecycle-implementation.md`, `ATDD-R3-F4-opportunity-lifecycle-persistence.md`, and `ATDD-R3-F5-feasibility-safety.md`: F3, F4 and F5 are frozen and are not modified by this increment beyond the recorded F5 ACTIVE_INCREMENT handoff.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture; `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the F6 contract vocabularies and the sealed architecture
WHEN:
the forecast vocabulary module is inspected
THEN:
the forecast status tokens are exactly FORECAST and INSUFFICIENT_EVIDENCE, the entry-execution outcomes are exactly NO_FILL, PARTIAL_FILL and FULL_FILL, the post-fill path outcomes are exactly TARGET, STOP, TIMEOUT and RISK_EXIT, the model statuses are exactly RESEARCH_ONLY and CALIBRATED_SHADOW, and the version tokens are exactly forecast-decision-v1, forecast-engine-v1, forecast-shadow-policy-v1, forecast-model-artifact-v1 and forecast-evaluation-v1

AC-002:
GIVEN:
an F5 FeasibilityDecision whose overall disposition is not FEASIBLE
WHEN:
the pure F6 engine is called with it
THEN:
it fails closed with ForecastContractError and never converts the upstream VETO or INSUFFICIENT_EVIDENCE into an F6 abstention

AC-003:
GIVEN:
one ACTIVE F4 episode and its FEASIBLE F5 decision
WHEN:
the pure F6 engine produces a decision
THEN:
the decision retains the immutable episode id and feasibility decision id and input fingerprint lineage, mints no episode, mutates no F3/F4/F5 state, and a mismatched lineage fails closed

AC-004:
GIVEN:
an evaluation time input
WHEN:
the F6 engine validates it
THEN:
only an explicit timezone-aware UTC datetime is accepted, a naive timestamp or a non-datetime value raises ForecastContractError, and no datetime.now, utcnow, today or time.time is read anywhere in the F6 modules

AC-005:
GIVEN:
one declared input schema, source cutoff, horizon and normalized feature vector
WHEN:
the deterministic input fingerprint is derived
THEN:
it is a FIN: function of those inputs, identical inputs yield an identical fingerprint, changed inputs change it, and a forged fingerprint fails closed

AC-006:
GIVEN:
one model artifact provenance
WHEN:
the deterministic model artifact identity is derived
THEN:
it is an FMOD: function of the artifact provenance, created and released metadata do not influence it, identical provenance yields an identical identity, and a forged identity fails closed

AC-007:
GIVEN:
one declared forecast request
WHEN:
the deterministic decision identity is derived
THEN:
it is an FCST: function binding the decision schema version, the episode id, the feasibility decision id, the input fingerprint, the explicit evaluation time, the model artifact id, the horizon identity, the engine version and the policy version, no UUID or clock takes part, abstentions are equally deterministic, and a forged identity fails closed

AC-008:
GIVEN:
no registered calibrated model
WHEN:
the pure F6 engine evaluates a FEASIBLE request
THEN:
it returns a deterministic INSUFFICIENT_EVIDENCE decision with the machine-readable reason NO_CALIBRATED_MODEL and no fabricated outputs

AC-009:
GIVEN:
a structurally valid RESEARCH_ONLY model artifact
WHEN:
the pure F6 engine evaluates it
THEN:
it returns INSUFFICIENT_EVIDENCE with the machine-readable reason MODEL_NOT_CALIBRATED_FOR_SHADOW_AUTHORITY

AC-010:
GIVEN:
a SYNTHETIC_TEST_ONLY adapter and the trusted production registry
WHEN:
registration and evaluation are attempted
THEN:
the production registry holds no adapter and no known evaluation report, it refuses to hold a SYNTHETIC_TEST_ONLY adapter, and a CALIBRATED_SHADOW artifact cannot produce a FORECAST through it

AC-011:
GIVEN:
a structurally valid, compatible CALIBRATED_SHADOW test artifact whose evaluation report is present in the supplied trusted metadata and whose kind has a registered trusted adapter
WHEN:
the pure F6 engine evaluates it
THEN:
it produces a FORECAST in tests only, with the entry distribution, conditional post-fill distribution, unconditional expected return, explicit uncertainty and derived valid_until, while the production registry stays empty

AC-012:
GIVEN:
the entry-execution outcome family
WHEN:
the forecast entry distribution is built
THEN:
it is a separate NO_FILL/PARTIAL_FILL/FULL_FILL distribution that is never mixed with the post-fill family and is never collapsed into a single binary win probability

AC-013:
GIVEN:
the post-fill path outcome family
WHEN:
the conditional post-fill distribution is built
THEN:
it is a separate TARGET/STOP/TIMEOUT/RISK_EXIT distribution that is explicitly conditional on fill exposure and is never merged with the entry family

AC-014:
GIVEN:
a probability distribution
WHEN:
it is validated
THEN:
each probability is finite, not a bool and within [0, 1], the mass must sum to one within a tight serialization tolerance, and invalid mass or a wrong outcome key set is refused rather than normalized or clipped

AC-015:
GIVEN:
an expected net return
WHEN:
it is validated and serialized
THEN:
it must be a finite dimensionless decimal where 0.012 means plus one point two percent, a non-finite or boolean value is refused, and no percent conversion or scaling by one hundred occurs

AC-016:
GIVEN:
a FORECAST decision
WHEN:
uncertainty is validated
THEN:
an explicit uncertainty with return lower and upper bounds, an explicit coverage level, explicit interval semantics and an evidence reference is required, a missing or invalid interval is refused and never defaulted to zero width, a missing required interval yields INSUFFICIENT_EVIDENCE with reason UNCERTAINTY_UNAVAILABLE, and the expected return must lie within the interval

AC-017:
GIVEN:
the explicit horizon contract
WHEN:
it is validated
THEN:
entry-deadline, post-fill path and forecast-validity durations are explicit with no numeric default, zero or negative durations are refused, a FORECAST carries the horizon, and an abstention invents no horizon

AC-018:
GIVEN:
the post-fill path horizon
WHEN:
its anchor is set
THEN:
the only v1 anchor is FIRST_FILL, additional fills do not restart it, and an unknown anchor fails closed

AC-019:
GIVEN:
a FORECAST decision and its evaluation time
WHEN:
valid_until is derived
THEN:
it is deterministically evaluation_time plus the declared validity duration, it is strictly after the evaluation time, a non-positive or expired valid_until fails closed, and an abstention carries no valid_until

AC-020:
GIVEN:
a model artifact and an input vector
WHEN:
compatibility is checked
THEN:
a mismatched input schema id, schema fingerprint or horizon contract, an expired artifact, a model trained after the evaluation time, an unsupported artifact version or an unknown model kind fails closed

AC-021:
GIVEN:
an input feature or source cutoff
WHEN:
point-in-time eligibility is checked
THEN:
any feature whose availability, or any source cutoff, is later than the evaluation time fails closed as future leakage

AC-022:
GIVEN:
a sealed evaluation population
WHEN:
a resolved label was available at or before the prediction cutoff
THEN:
the population is refused as future-label leakage rather than scored, and a clean population seals deterministically

AC-023:
GIVEN:
an UNRESOLVED or INCOMPLETE_COVERAGE label
WHEN:
the evaluation report is built
THEN:
the label is never turned into a negative, only cleanly resolved labels are scored, the unresolved and incomplete counts remain visible, and a TIMEOUT is scored from its recorded realized return and never equated with a negative return

AC-024:
GIVEN:
a NO_FILL entry outcome
WHEN:
the evaluation example is built
THEN:
it carries no post-fill path label, no STOP or TIMEOUT is fabricated, and the report never scores a path for it

AC-025:
GIVEN:
a probability distribution and its observed outcome
WHEN:
the multi-class Brier score is computed
THEN:
a perfect one-hot forecast scores zero, a uniform distribution over K outcomes scores (K-1)/K, known examples match their hand-computed values, and a cross-family observation is refused

AC-026:
GIVEN:
a probability distribution and its observed outcome
WHEN:
the multi-class log loss is computed
THEN:
a perfect one-hot forecast scores zero, a uniform distribution over K outcomes scores log(K), log of zero is never evaluated because an explicit evaluation-only epsilon is applied, and an out-of-range epsilon fails closed

AC-027:
GIVEN:
the entry-execution and conditional post-fill outcome families
WHEN:
the evaluation report is built
THEN:
the two families are scored separately, their scores are never merged into one win probability, and changing the entry distribution leaves the path score unchanged

AC-028:
GIVEN:
an evaluation population
WHEN:
reliability diagnostics are produced
THEN:
an explicit bin configuration is required, the diagnostics are deterministic, an empty bin reports its predicted mean and observed frequency as unavailable rather than zero, and the report never emits a calibration-pass verdict from an arbitrary threshold

AC-029:
GIVEN:
the F6 production modules
WHEN:
their identifiers, imports and numeric literals are audited
THEN:
no probability is derived from a field named confidence, score, opportunity_score, technical_score, tradeability_score, explosion_potential_score, committee_confidence, ai_confidence or rank, and no value is scaled by one hundred

AC-030:
GIVEN:
the F6 production modules
WHEN:
their imports and identifiers are inspected
THEN:
they import no AI or Committee authority, and no AI or Committee confidence or recommendation can become a forecast probability

AC-031:
GIVEN:
the F6 production modules
WHEN:
their imports, identifiers and outputs are inspected
THEN:
they perform no F7 allocation, ranking, reservation, sizing or cash competition, import no selector, portfolio, optimizer or allocator module, and expose no allocation attribute

AC-032:
GIVEN:
the repository runtime surfaces
WHEN:
F6 integration is audited
THEN:
no run_cycle, scan_opportunities, Telegram, alert, paper, execution, risk, Committee or dashboard path invokes, imports or consumes F6, and legacy scan and service gates remain the live authority

AC-033:
GIVEN:
the F6 production modules
WHEN:
durable effects are audited
THEN:
F6 creates no persistence module, writer, database table or JSONL stream, imports no canonical writer or storage module, and its decisions round-trip through pure serialization

AC-034:
GIVEN:
the production Compose configuration
WHEN:
the Feature Bus mode is inspected
THEN:
OPIP_FEATURE_BUS_MODE remains off and F6 activates no Feature Bus behavior

AC-035:
GIVEN:
the frozen F3 detector, F4 lifecycle and F5 feasibility semantics
WHEN:
this F6 increment is applied
THEN:
F3 thresholds, claim identity, state and schemas are unmodified, the F4 pure lifecycle semantics and persistence are unmodified, the F5 seam is unmodified and produces identical decisions, and F6 reinterprets none of them

AC-036:
GIVEN:
the completed F5 feasibility increment, the completed F6 forecast-engine increment and the movable ATDD pointer
WHEN:
the F6 increment has completed and a later approved increment owns the pointer
THEN:
F6 proves its own identity from its own frozen scope contract and test module rather than from the global pointer, neither the completed F5 increment nor the completed F6 increment permanently requires the pointer to equal it, the pointer need only resolve to an existing scope contract, and every substantive F5 and F6 isolation assertion is preserved

EXPLICITLY OUT OF SCOPE:
- F7 economic/portfolio selector: allocation, net-dollars, ranking, cash/no-trade comparator, reservation, sizing or concentration
- Forecast operational integration into any runtime path, admission authority, or replacing any legacy gate
- An F6 canonical writer, persistence module, database table, JSONL stream, or scheduler
- Training, fitting, promoting, or registering any calibrated model, and any production or funded model status
- Relabeling any existing ordinal score, alert/Chief/AI/Committee confidence, Committee 0-100 rubric, economic-quality score, target-attainability score or F5 result as a probability
- Converting any ordinal score into a probability with score/100 or an invented logistic mapping
- Adding a second calibration spine (`app/opip/calibration/`) or a large new `app/opip/forecast/` subtree
- Adding AI/Committee imports or calls, or letting AI confidence become a forecast probability
- Arbitrary executable Python, pickle, eval/exec, dynamic import of attacker-controlled names, or an external model API
- Modifying architecture documents, the v1.4.3 DOCX, frozen F3/F4/F5 contracts, the ATDD checker, the scope-control contract, workflows, `pyproject.toml`, or any existing F3/F4/F5 test beyond the recorded F5 ACTIVE_INCREMENT handoff
- Touching, merging, rebasing, closing, cherry-picking or depending on PR #294 (bridge)
- Weakening, deleting, or skipping any existing test

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off` in `docker-compose.yml`.
- Production `run_cycle` and `scan_opportunities` do not call F6.
- F6 remains SHADOW / NON-AUTHORITATIVE and writes no canonical evidence; the canonical writer remains the single domain write path.
- The trusted production model registry ships empty; no calibrated model is registered and no model is auto-promoted.
- F3 detector, F4 lifecycle and F5 feasibility contracts and implementations remain frozen and unmodified except the recorded F5 ACTIVE_INCREMENT handoff.
- No `app/opip/forecast/` subtree and no `app/opip/calibration/` subtree are created.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled; Paper execution stays isolated from funded order endpoints.
- Committee authority is unchanged and remains shadow-only.
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- A normal push of this feature branch and its OWNER-authorized review, merge and deploy are permitted as recorded above.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_001_vocabulary_is_exact
AC-002 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_002_requires_feasible_f5
AC-003 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_003_lineage_retained_and_immutable
AC-004 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_004_evaluation_time_explicit_utc
AC-005 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_005_input_fingerprint_deterministic
AC-006 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_006_model_artifact_identity_deterministic
AC-007 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_007_decision_identity_deterministic
AC-008 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_008_no_model_abstains
AC-009 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_009_research_only_abstains
AC-010 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_010_synthetic_model_excluded_from_production
AC-011 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_011_calibrated_shadow_test_artifact_forecasts
AC-012 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_012_entry_outcomes_separate
AC-013 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_013_post_fill_path_separate
AC-014 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_014_probability_range_and_sum
AC-015 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_015_expected_return_finite_and_units
AC-016 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_016_uncertainty_required_and_valid
AC-017 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_017_horizon_explicit_no_default
AC-018 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_018_path_horizon_anchored_first_fill
AC-019 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_019_validity_expiry_explicit
AC-020 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_020_schema_compatibility_enforced
AC-021 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_021_future_feature_rejected
AC-022 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_022_future_label_leakage_rejected
AC-023 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_023_unresolved_incomplete_not_negatives
AC-024 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_024_no_fill_has_no_path_label
AC-025 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_025_brier_correct
AC-026 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_026_log_loss_correct
AC-027 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_027_families_scored_separately
AC-028 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_028_reliability_report_deterministic
AC-029 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_029_no_score_to_probability
AC-030 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_030_no_ai_or_committee_authority
AC-031 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_031_no_allocation_or_f7
AC-032 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_032_no_runtime_integration
AC-033 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_033_no_new_writer_db_jsonl
AC-034 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_034_feature_bus_off
AC-035 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_035_f3_f4_f5_semantics_unchanged
AC-036 -> tests/test_opip_r3_f6_forecast_engine.py::test_ac_036_f5_pointer_handoff

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/__init__.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F6-forecast-engine.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-014 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-015 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-015 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-018 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-019 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-020 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-020 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-021 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-021 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-022 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-022 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-023 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-023 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-024 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-024 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-025 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-025 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-026 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-026 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-027 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-027 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-028 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-028 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-029 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-029 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-029 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-029 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-030 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-030 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-030 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-031 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-031 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-031 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-032 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-033 -> OHM-Trade-Agent-v1/app/opip/forecast.py
AC-033 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-033 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-034 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-035 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-036 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-036 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F6-forecast-engine.md
AC-036 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-036 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py

DEFERRED DISCOVERIES:
- Registering a real calibrated-shadow model artifact requires a qualifying sealed evaluation population that the repository does not yet prove; that is a future OWNER increment, not this one.
- Whether the canonical outcome/label source can prove fidelity grade A for the entry and path families remains unresolved; F6 reports the limitation rather than inventing a grade.
- An F6 canonical writer and forecast operational integration remain unauthorized and are future increments.
- Dependence-aware block resampling is not implemented here; when it is, the block length must be experiment-derived and must not become an architecture constant.
- F7 economic/portfolio selection and Paper-v2 cutover remain future increments.

UNAPPROVED SCOPE CHANGES:
NONE
