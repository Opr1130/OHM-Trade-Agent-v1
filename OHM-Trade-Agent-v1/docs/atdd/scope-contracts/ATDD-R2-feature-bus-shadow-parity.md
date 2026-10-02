INCREMENT:
ATDD-R2-feature-bus-shadow-parity

OWNER-APPROVED INTENT:
Prove the existing F2 Shared Feature Bus through deterministic, point-in-time correct, fail-closed shadow replay and parity against available legacy reference evidence, without activating the Feature Bus or changing trading authority. R2 may capture enough evidence and state metadata to reproduce the FeatureSnapshot that the existing production Feature Bus implementation would produce. R2 may expose already-existing alignment classifications necessary to prove which captured inputs affected snapshot values, coverage, freshness, missingness, lateness, provenance, restart state, retained state, or consumed input watermark. R2 must not create a second Feature Bus implementation.

ARCHITECTURE REFERENCES:
- O'Pip Profit Intelligence Platform Architecture v1.4.3: F2 Shared Feature Bus, and its point-in-time and replay requirements.
- docs/architecture/v1.2/BASELINE.md: F2 Shared Feature Bus is bounded and reproducible feature calculation over retained inputs and checkpoints producing versioned feature values.
- docs/architecture/v1.2/B_MARKET_DATA_CONTRACT.md: fixed-interval aggregates, FeatureStateCheckpoint tied to consumed-input watermarks, every detector-evaluation FeatureSnapshot, and gap, reset and restart evidence.
- Existing Feature Bus contracts and pipeline semantics: FeatureSnapshot, FeatureStateCheckpoint, RollingState, consumed_input_watermark, restart_state, instrument and reference identity, and point-in-time evidence.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a valid frozen evidence envelope for an existing Feature Bus evaluation
WHEN:
the envelope is replayed more than once
THEN:
the resulting FeatureSnapshot identity and content are deterministic, and the parity report describes the exact sealed FeatureSnapshot values rather than independently recomputing a competing Feature Bus value

AC-002:
GIVEN:
captured evidence containing an invalid or inconsistent instrument, observation identity, schema, type, aggregate domain value, revision or content identity, or ambiguous revision rank
WHEN:
R2 replay validates the evidence
THEN:
replay fails closed and does not silently coerce, relabel, filter into a different fact, or seal a trusted snapshot

AC-003:
GIVEN:
captured evidence and reference metadata together with a declared evaluation instant, evaluation cutoff, and consumed-input watermark
WHEN:
any fact that can affect the sealed snapshot was not visible or valid at the declared point in time, falls outside the permitted cutoff semantics, or carries a commit order beyond the declared consumed watermark
THEN:
replay fails closed, and evidence that provably cannot affect the sealed snapshot is not falsely treated as consumed merely to make the gate pass

AC-004:
GIVEN:
a production-equivalent resumed Feature Bus cycle with retained RollingState, fresh evidence, coverage-only evidence, and a short tip re-poll
WHEN:
R2 captures and replays that evaluation
THEN:
the replay preserves the canonical evidence roles and the retained state needed to produce the same FeatureSnapshot semantics as the existing pipeline, and replay fails closed rather than synthesizing history when required retained-state context is absent or corrupt

AC-005:
GIVEN:
a Feature Bus evaluation carrying a canonical restart state such as cold start, warm, or checkpoint recovery
WHEN:
R2 captures and replays the evaluation
THEN:
the original restart state is validated and reproduced rather than silently defaulted or inferred

AC-006:
GIVEN:
an InstrumentVersion or reference identity used by a historical evaluation
WHEN:
R2 validates the replay envelope
THEN:
the captured reference observation time is mandatory, matches the supplied canonical reference version, and was known by the declared evaluated-at instant, and a null, mismatched, or future reference observation time fails closed

AC-007:
GIVEN:
retained checkpoint metadata containing interval epochs or other typed identity or state values
WHEN:
R2 reconstructs retained state
THEN:
every value satisfies its canonical declared type exactly, and a fractional first_interval_epoch is refused rather than truncated into an apparently valid integer

AC-008:
GIVEN:
the completed R2 increment
WHEN:
repository and runtime activation surfaces are inspected
THEN:
OPIP_FEATURE_BUS_MODE remains off, production run_cycle does not activate the Feature Bus (it never schedules the manual pilot, never runs the R4-B2 SHADOW capture, and never constructs a publisher), no trading, risk, or strategy authority changes, no deploy occurs, and R3 has not started. R4-B2 later placed its bounded SHADOW capture on its own cron entry, precisely so the protected cycle never runs it.

EXPLICITLY OUT OF SCOPE:
- F3 IGNITION detector implementation
- opportunity lifecycle implementation
- feasibility redesign
- forecast engine
- economic or portfolio selector
- Feature Bus activation
- production scheduler integration
- Paper-v2 activation
- Committee activation
- dashboard work
- Profit Intelligence expansion
- trading, risk, or strategy authority changes
- deployment
- funded trading authority
- legacy retirement
- R3 work
- unrelated refactors
- scanner or gate redesign
- modifications to ATDD-000 itself

FROZEN BOUNDARIES:
- OPIP_FEATURE_BUS_MODE remains off.
- Production run_cycle does not call the Feature Bus.
- Canonical writer authority is unchanged.
- Funded and paper isolation is unchanged.
- Risk, strategy, and trading authority are unchanged.
- Committee authority is unchanged.
- No deploy occurs, and no merge happens without OWNER approval.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_complete_observation_matches_indicator_math_and_replays
AC-001 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_same_evidence_replayed_twice_is_byte_identical
AC-001 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_parity_uses_snapshot_values_when_the_last_bar_precedes_cutoff
AC-002 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_replay_refuses_foreign_instrument_version_id
AC-002 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_missing_observation_id_is_rejected
AC-002 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_observation_schema_version_must_be_supported
AC-002 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_domain_invalid_aggregate_values_are_rejected
AC-002 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_conflicting_content_for_one_interval_revision_is_rejected
AC-003 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_input_not_visible_at_the_replay_instant_is_rejected
AC-003 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_watermark_later_than_the_captured_position_is_refused
AC-003 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_uncommitted_misaligned_row_fails_closed
AC-004 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_roll_forward_uses_retained_state_not_the_flat_fetch
AC-004 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_replay_matches_the_production_cycle_snapshot
AC-004 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_coverage_only_rows_are_not_treated_as_feature_evidence
AC-004 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_resumed_cycle_without_retained_state_fails_closed
AC-005 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_checkpoint_resume_reports_restart_warmup
AC-005 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_in_process_resume_keeps_its_cold_start_provenance
AC-005 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_corrupt_restart_state_evidence_fails_closed
AC-006 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_reference_metadata_is_bound_to_the_capture
AC-006 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_future_reference_metadata_is_refused
AC-006 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_missing_reference_binding_fails_closed
AC-007 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_unsupported_nested_rolling_state_is_refused
AC-008 -> tests/test_opip_feature_bus_r2_shadow_parity.py::test_feature_bus_mode_stays_off_and_cycle_does_not_run_capture

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/features/r2_shadow_parity.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R2-feature-bus-shadow-parity.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/features/r2_shadow_parity.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/features/r2_shadow_parity.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/market/aggregates.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/features/r2_shadow_parity.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/market/aggregates.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/features/r2_shadow_parity.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/features/r2_shadow_parity.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/features/r2_shadow_parity.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py

DEFERRED DISCOVERIES:
- Whether production snapshot availability should span the whole alignment window rather than the contiguous feature tail is a canonical-evidence contract decision affecting the live path, and is not changed by R2.
- Retaining a maximum receipt per retained slot would strengthen availability bounds beyond the state creation clock, but changing the meaning of last_receipt_epoch would alter canonical checkpoint idempotency and needs a separate decision.
- Stored content_fingerprints cannot be recomputed from retained state because vwap and trade_count are not retained, so retained fingerprints are not re-derived.
- v1.4.3 section 13 platform acceptance scenarios remain future increments.
- R3 IGNITION detector, opportunity lifecycle, feasibility, forecast, and selector remain future increments.

UNAPPROVED SCOPE CHANGES:
NONE
