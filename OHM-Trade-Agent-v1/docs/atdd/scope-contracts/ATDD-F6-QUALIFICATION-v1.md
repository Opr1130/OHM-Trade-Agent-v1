INCREMENT:
ATDD-F6-QUALIFICATION-v1

OWNER-APPROVED INTENT:
Advance F6 from a sound fail-closed shadow contract toward an independently qualifiable forecasting capability *without* promoting anything: implement the qualification gate a future owner decision would need, plus deterministic offline scoring rules, and prove that neither can register, trust or wire a model. The production registry stays empty, the engine stays unwired, and no dataset exists, so the honest verdict is DATASET_BLOCKED rather than a fabricated certification. Candidate thresholds are recorded as UNAPPROVED.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/opip/forecast.py: the pure engine, `TrustedForecastModelRegistry` and `PRODUCTION_MODEL_REGISTRY`, all unchanged.
- OHM-Trade-Agent-v1/app/opip/contracts/forecast.py: the artifact, input-vector, policy and decision vocabulary reused by the qualification record.
- OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py: still records `UNAVAILABLE_NO_TRUSTED_MODEL`; no forecast is consumed.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F6-forecast-engine.md: the engine's original fail-closed contract.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md: G-02, the owner policy that withholds actionable qualification without a trusted forecast.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a qualification record carrying every required field, native point-in-time provenance, ordered training/validation/test periods, a purge and an embargo longer than the label horizon, a reproducibility manifest, a drift monitor and a cost model including spread
WHEN:
the record is qualified
THEN:
the result is QUALIFICATION_CANDIDATE with no reasons, and its evidence states trusted false, model_registered false and trade_authority_changed false with candidate thresholds marked UNAPPROVED

AC-002:
GIVEN:
a record missing a required field, a record with no provenance, a record whose provenance kind is synthetic, fixture, imputed or backfilled, and a record whose provenance is not point-in-time verified
WHEN:
the record is qualified
THEN:
the result is INCOMPLETE with the specific missing or forbidden reasons, and nothing is trusted or registered

AC-003:
GIVEN:
a record whose validation or test period overlaps training, a record whose embargo is missing or shorter than the label horizon, a record with purging unproven, a record with an unusable reproducibility manifest, an undefined drift monitor, a cost model without spread, or a naive timestamp
WHEN:
the record is qualified
THEN:
the result is REJECTED with the specific leakage, reproducibility or cost reasons

AC-004:
GIVEN:
a set of probability/outcome pairs, an empty set, and sets containing NaN, out-of-range, boolean or malformed probabilities
WHEN:
the Brier score and expected calibration error are computed
THEN:
perfect and coin-flip sets score exactly, every unscoreable set returns no score rather than a guess, and no metric is invented

AC-005:
GIVEN:
a temporally ordered observation sequence, a naive timestamp, an unsorted sequence, a sequence too short for the requested folds, and a scored sample set
WHEN:
walk-forward splits and the offline evaluation run
THEN:
every training index precedes every test index with at least the embargo gap, invalid input raises instead of producing a leaking fold, and the evaluation reports DATASET_BLOCKED with promotable false and leak detection false

AC-006:
GIVEN:
the qualification module and the production forecast engine
WHEN:
the module is inspected and the registry is read
THEN:
the registry still holds zero adapters and zero vouched reports, and the module contains no registry mutation, no registration call and no forecast evaluation, so qualification can never become promotion

AC-007:
GIVEN:
a qualification record whose model kind, version, artifact identity and horizon either match or contradict the real `ForecastModelArtifact` the engine would execute, and separately no artifact at all
WHEN:
the record is reconciled with that artifact
THEN:
a matching record returns no reasons, every contradiction returns its specific mismatch reason, a missing artifact is refused, and the production registry is still empty - so a record can never qualify a different model than the one that would run

EXPLICITLY OUT OF SCOPE:
- Registering, calibrating, training, promoting or trusting any model
- Changing `PRODUCTION_MODEL_REGISTRY`, the artifact vocabulary or the engine
- Wiring a forecast into Tradevisor qualification, notification or execution
- Supplying, downloading or fabricating a dataset
- Setting production promotion thresholds (candidates are UNAPPROVED)
- Any deployment unit, scheduler, credential or network provider

FROZEN BOUNDARIES:
- `PRODUCTION_MODEL_REGISTRY` remains empty (`0` adapters, `frozenset()` reports).
- No model becomes trusted because a qualification record is complete.
- No numeric promotion threshold is presented as approved.
- Synthetic fixtures prove code contracts only, never predictive performance.
- The engine's abstention behaviour and every existing negative path are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_f6_qualification_v1.py::test_ac_001_a_complete_record_is_only_a_candidate
AC-002 -> tests/test_opip_f6_qualification_v1.py::test_ac_002_incomplete_or_forbidden_provenance_is_refused
AC-003 -> tests/test_opip_f6_qualification_v1.py::test_ac_003_leakage_and_reproducibility_gaps_are_rejected
AC-004 -> tests/test_opip_f6_qualification_v1.py::test_ac_004_scoring_is_exact_and_refuses_unscoreable_input
AC-005 -> tests/test_opip_f6_qualification_v1.py::test_ac_005_walk_forward_splits_never_leak_and_reject_bad_input
AC-005 -> tests/test_opip_f6_qualification_v1.py::test_ac_005_evaluation_reports_dataset_blocked_and_never_promotes
AC-006 -> tests/test_opip_f6_qualification_v1.py::test_ac_006_the_production_registry_stays_empty_and_nothing_is_wired
AC-007 -> tests/test_opip_f6_qualification_v1.py::test_ac_007_a_record_must_describe_the_artifact_that_would_execute
AC-007 -> tests/test_opip_f6_qualification_v1.py::test_ac_007_a_mismatched_record_never_qualifies_the_artifact
AC-007 -> tests/test_opip_f6_qualification_v1.py::test_ac_007_a_missing_artifact_is_refused

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/forecast_qualification.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_f6_qualification_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/forecast_qualification.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_f6_qualification_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/forecast_qualification.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_f6_qualification_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/forecast_qualification.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_f6_qualification_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/forecast_qualification.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_f6_qualification_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/forecast_qualification.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_f6_qualification_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-F6-QUALIFICATION-v1.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-007 -> OHM-Trade-Agent-v1/app/opip/forecast_qualification.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_f6_qualification_v1.py

DEFERRED DISCOVERIES:
- No dataset is available, so no out-of-sample performance is claimed. `DATASET_BLOCKED` is the correct verdict until a versioned, reproducible dataset exists.
- Regime-specific performance, per-symbol robustness, sample-size confidence bounds and drift PSI monitoring are described but not implemented; they need a dataset and an owner-approved metric policy.
- Candidate thresholds are deliberately not enforced as gates: enforcing them would silently promote an unapproved policy. An owner decision must set them first.
- The qualification record duplicates some fields the artifact already carries; consolidating them would change the artifact contract and is not authorized here.

UNAPPROVED SCOPE CHANGES:
NONE

