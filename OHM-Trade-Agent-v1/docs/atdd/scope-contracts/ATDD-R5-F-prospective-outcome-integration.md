INCREMENT:
ATDD-R5-F-prospective-outcome-integration

OWNER-APPROVED INTENT:
Complete the learning half of the research-paper chain by joining the frozen prospective expectation that R5-E already captures to the committed canonical terminal paper outcome that the canonical writer already owns, and by making the join economic, governed and non-authoritative. This increment adds no engine, no ledger, no scheduler, no writer, no store, no event type, no release profile, no configuration key, no order path and no trading authority, and it changes no application behaviour: the surface it adds is dormant evidence code with no runtime consumer, exactly as SC-10 freezes it. It answers six questions and only those six. First, is a prospective expectation captured ex ante from decision evidence, and is a decision clock that falls after the cutoff refused as hindsight instead of being attached later. Second, is a verified canonical terminal outcome the only thing that becomes an attachment, with committed fill evidence supplied by the execution plane rather than inferred, and with a defective or unverifiable payload refused rather than defaulted to zero. Third, is the economic truth of a retained population a ratio of sums over the population rather than a mean of per-episode ratios, with operating costs deducted exactly once and with a payload whose own net disagrees with its gross minus its fees refused. Fourth, is capital time evaluated as occupied capital multiplied by realized duration against the expectation's own declared horizon, with a breach named rather than smoothed away. Fifth, are the LE-01 populations, the LE-02 measure kinds and the maturity vocabulary still separate, so a counterfactual or hindsight measure can never enter a realized total and an immature window can never be matured. Sixth, does every eligible persisted learning artifact reach exactly one named consumption disposition, with repair lineage that resolves to its root and fails closed, and with a prospective champion/challenger comparison that is paired on one frozen baseline and can never promote a policy. It records exactly what remains unproven: the dispositions are computed from the evidence in hand and are not durably registered, all numeric gate thresholds and the review-critical registry facts stay the owner's OQ-09 and OQ-07 inputs, and no production caller is added.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `LE-01`: retain `TAKEN`, `REJECTED`, `MISSED`, `EXPIRED`, `NO_FILL` and `NO_TRADE` populations at a deduplicated episode/intent grain; outcomes are `PENDING`, `MATURED`, `CENSORED` or `INSUFFICIENT_EVIDENCE` with explicit reasons; an open position cohort cannot be declared a success or a failure using an immature tactical window. This increment proves the join over all six populations and the maturity vocabulary through behaviour, and adds a population-mixing refusal.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `LE-02`: realized paper outcomes, declared-policy feasible counterfactuals and hindsight MFE/MAE use separate measures; portfolios are compared with the same capital and timing and mutually incompatible "missed winners" are never summed; a legacy experience without a contemporaneous record is not a frozen expectation. This increment keeps one measure kind per grain slot, builds the realized measure kind and nothing else, refuses a mixed-kind total, and refuses a decision clock that post-dates the cutoff.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `LE-03`: evidence of learning is prospective improvement against a frozen baseline after operating costs, with coverage, cohort size and uncertainty, and it also measures calibration, weakness recurrence and post-fix economic harm. This increment requires a paired, single-baseline, single-horizon prospective comparison, reports coverage, support and uncertainty, keeps `can_promote` false, and classifies the named root cause of a shortfall.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` section 12 `DC-01`, `DC-02`, `DC-03`: the canonical record preserves `(history_epoch, local_sequence)` order and separates source event, receipt, availability, evaluation and commit times; every linked decision uses eligible evidence at its own cutoff and consumed watermark; units, precision, rounding, enum mappings, retention, compatibility and additive migration rules are frozen before coding each increment and derived views rebuild and reconcile at the same watermark before readers switch.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-10` "Expectation and outcome learning record" (`FROZEN`, open input `OQ-09`): one record per entity, policy, horizon and revision with an immutable expected payload and hash and a preserved cutoff; separate populations at a deduplicated grain; separate measure kinds; learning means prospective improvement against a frozen baseline after operating costs with coverage; learning cannot edit active policy autonomously and cannot write to the trading host's production stores; the owning module is `app/opip/learning/evaluation.py` with reuse status `EXTEND_EXISTING_MODULE`, and this contract introduces no second outcome engine.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-12` "Canonical ordering, temporal contract and shared writer" (`FROZEN`, open inputs `OQ-07`, `OQ-10`, owning module `app/opip/canonical/writer.py`, reuse status `REUSE_EXISTING_MODULE`): exactly one canonical writer owns durable domain records and no new event store, allocator database, outcome engine or position registry is introduced; learning cannot read the live SQLite file over the network. This increment reuses the committed canonical terminal outcome as the only outcome authority, reads it only through the existing read-only reader, writes nothing and introduces no reader of the live store.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-11` "Paper isolation and research environment" and `PM-01`, `PM-02`: research paper management runs in a separately identified offline or research environment with no funded write and no exchange order authority, `TARGET_PAPER` remains a later owner-gated cutover, and paper cannot generate its own proof. This increment adds no profile, no activation, no funded path and no exchange surface.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-13` and `docs/architecture/v1.5.0/ARCHITECTURE.md` section 14 and section 18: delivery order is declared and acyclic, `R5-F` follows `R5-A` and `R5-E`, and gates consume increments rather than the reverse. This increment consumes the already delivered `R5-A`, `R5-D` and `R5-E` chain and closes nothing that stays open.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` row `R5-F` "Learning: canonical expectations, capital-time and horizon evaluation, repair lineage and named dispositions", authority `SHADOW`, contracts `SC-10`,`SC-12`, open input `OQ-09`, gate `G6`, note "learning stays non-authoritative", and row `G1` whose outcome population must be complete with frozen expectations before outcomes.
- `AGENTS.md`: fail-closed behaviour is preserved, fixed risk sizing, minimum reward-to-risk, position verification and execution safeguards are untouched, external AI cannot override a deterministic gate, production stores and canonical learning evidence are never destructively mutated, and one scheduler, one store and one control plane are preserved. Principle 21 governs the dispositions: every eligible persisted learning artifact must reach a traceable consumption disposition, and consumption never authorizes an autonomous policy or trading change.
APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a decision evidence record captured ex ante for one deduplicated grain of one entity, policy, horizon and revision, carrying its own decision clock, its cutoff, its maturity horizon and its expected payload
WHEN:
the capture runs at or before the cutoff, and separately a decision whose clock falls after the cutoff is offered
THEN:
the first freezes one immutable expectation whose expected payload hash is computed from that payload, whose identity is derived from its own content and which carries no authority; the second is refused as hindsight contamination rather than being attached later, an unknown or missing key is refused instead of defaulted, and refining the expectation with committed outcome facts is accepted only through the frozen attachment seam with an explicit committed fill count and only at or after the maturity horizon
AC-002:
GIVEN:
committed canonical terminal paper outcomes read back from the canonical store through the existing read-only reader, and a defective payload whose own net disagrees with its gross minus its fees
WHEN:
the realized economic truth of one retained population is evaluated
THEN:
the population total is a ratio of sums over that population and not a mean of per-episode ratios, operating costs are deducted exactly once so the realized net is the gross minus the fees, the committed fill evidence is supplied by the caller and a zero or non-integral fill count is refused rather than inferred, an unverifiable, cancelled or unresolved payload yields no facts at all instead of zeros, the defective payload is refused, and the realized measure series carries the realized kind and never a counterfactual or hindsight kind
AC-003:
GIVEN:
matured attachments whose frozen expectations each declare their own expected duration and expected horizon, one of them held well beyond the horizon it declared
WHEN:
capital time is evaluated for that population
THEN:
occupied capital time is measured as committed capital multiplied by realized duration and compared with the capital time the frozen expectations themselves declared, the mean holding time and its uncertainty are reported with coverage and support, the over-held episode is named as a horizon breach with its share of the population instead of being smoothed away, and no numeric holding, rotation or horizon threshold is invented by this increment
AC-004:
GIVEN:
retained expectations across the six LE-01 populations and, separately, a set of attachments that spans more than one population
WHEN:
each population is measured and a mixed total is requested
THEN:
every retained population keeps its own slot, each population is evaluated only against attachments of that same population, a mixed-population economic total is refused rather than producing one combined number, and no population is silently dropped from the retained set
AC-005:
GIVEN:
a frozen expectation and, in turn, no attachment at all, an attachment whose window is still immature, an explicitly censored reading, a missing coverage figure, a zero coverage figure and a coverage figure beyond the declared window
WHEN:
the maturity vocabulary is resolved for each of them
THEN:
an immature window is never matured and reports its own pending or immature reason, a censored reading carries only a censoring reason, a missing or empty coverage is insufficient evidence rather than a result, a coverage beyond the declared window is refused, and every reported status carries a reason from the closed vocabulary that belongs to that status alone
AC-006:
GIVEN:
matured attachments whose realized economics fall short of, meet or exceed what their own frozen expectation declared, and separately a set of corrections whose lineage is dangling, self-referential or cyclic
WHEN:
a root cause is classified for each attachment and the repair lineage of the corrections is resolved
THEN:
each classification is one named root cause chosen by the frozen precedence and carrying its own reasons, a shortfall beyond the declared horizon is a horizon breach before it is anything else, a cost overrun is named as cost drag, an early exit is named as execution timing, a full-horizon shortfall is named as an adverse move and a met expectation is named as met, while a dangling parent, a self-reference, a cycle or a correction recorded before the thing it corrects each fail closed
AC-007:
GIVEN:
a retained set holding a matured taken attachment, a matured attachment from a non-taken population, an unresolved attachment, an artifact whose declared measure kinds could never produce realized truth, a quarantined artifact and a superseded revision
WHEN:
consumption dispositions are assigned and the retained set is checked for completeness
THEN:
every artifact receives exactly one named disposition from the closed vocabulary, the retained set loses nothing and no artifact is consumed twice, an unusable or defective artifact is quarantined rather than dropped, an artifact that could never carry realized truth is rejected rather than measured as one, and no disposition grants authority, changes a policy, changes a trade decision or reports itself as anything other than evidence
AC-008:
GIVEN:
a champion arm and a challenger arm of frozen expectations that share one frozen baseline and one horizon, evaluated over the same grains, with one grain present in only one arm, one grain taken by both arms and, separately, a cohort whose support falls below the declared minimum
WHEN:
the prospective comparison runs
THEN:
the comparison is refused unless it is paired on the same grains with the same baseline and horizon and distinct policies, a grain taken by both arms or present in only one arm is refused instead of being imputed, a realized return is taken only from the arm that actually took the episode and never from a counterfactual measure, and the comparison reports its cohort size, coverage and uncertainty while its promotion flags stay false, so insufficient support is reported as insufficient and can never promote a policy
AC-009:
GIVEN:
this contract, the movable `ACTIVE_INCREMENT` pointer, the acceptance-test index, the release-profile surface and the rest of the application package
WHEN:
this increment's own bookkeeping is inspected
THEN:
the contract parses with every criterion traced to this module's acceptance-marked tests and mapped to a changed path, the pointer resolves to exactly one declared contract instead of being pinned to this increment, the increment this one follows stays declared, `UNAPPROVED SCOPE CHANGES` is `NONE`, the release profiles are unchanged with `EVIDENCE_SHADOW` active, `TARGET_PAPER` blocked and `OPIP_PAPER_V2_MODE` off, no new mode key, engine, scheduler, writer, store or event type is introduced, and the learning surface this increment adds is reachable from no runtime root at all

EXPLICITLY OUT OF SCOPE:
- Any durable expectation store, outcome store, disposition registry, learning database, queue, projection, cache or second source of truth: the frozen SC-10 record and the computed dispositions are evidence in hand, not a new persisted authority, and the canonical writer stays the only durable domain writer.
- Any new canonical event type, stream, priority class, schema version or writer method: the increment reads the committed `paper_outcome.terminal.recorded` evidence through the existing read-only reader and writes nothing.
- Any production caller, job, scheduler, scan hook, dashboard, alert or Telegram path: the surface is dormant evidence code with no runtime consumer, and adding one is a separate owner-authorized increment.
- Every numeric gate threshold, coverage requirement, minimum population size, minimum economic advantage, holding or horizon boundary, rotation cooldown, requalification rule and turnover budget, which stay `OQ-07`, `OQ-03`, `OQ-05` and `OQ-09` owner inputs. The minimum support used by the prospective comparison stays a caller-declared parameter with the value the existing evaluation already defaults to, and no new default is invented here.
- Policy promotion, policy editing, model training, ML framework dependencies, autonomous retirement and any change to admission, risk, sizing, execution or protection behaviour.
- Funded or live authority, exchange order placement, modification, cancellation or confirmation, `TARGET_PAPER` activation, and any change to the release profiles or the paper-v2 mode.
- Declared-policy counterfactual simulation and hindsight MFE/MAE derivation beyond retaining their measure kind separate from the realized one.
- Legacy engine retirement and the R4 coexistence window, which belong to `R7` under `OQ-08`.

FROZEN BOUNDARIES:
- Exactly one canonical writer owns durable domain records. This increment adds no writer call, no event type, no second outcome engine and no position registry.
- The `SC-10` expectation record keys, the expected payload hash, the identity derivation, the closed population vocabulary, the closed outcome status vocabulary, the reason-to-status mapping and the closed measure kind vocabulary are unchanged. The R5-E acceptance suite still passes unmodified.
- The expectation surface stays consumable only from its owning module: no file under `app/` other than `app/opip/learning/evaluation.py` may reference the expectation record types, so every new capability is added inside that module and no new module is created.
- The new section carries no clock of its own, reads no file, opens no socket, uses no randomness, holds no environment key and imports no venue, network, storage, subprocess or ML primitive.
- Fixed risk sizing, minimum reward-to-risk, position verification, execution safeguards, alert-only defaults and the paper-isolation boundary are untouched.
- The release profiles, Compose literals, and the frozen production posture with `EVIDENCE_SHADOW` active, `TARGET_PAPER` blocked and `OPIP_PAPER_V2_MODE` off are unchanged.
- Money is measured in quote-currency units, duration in seconds and shares are dimensionless; the unit of every reported figure is declared on its own record, and no rounding, currency conversion or precision policy is invented.
ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_001_an_ex_ante_decision_capture_is_frozen_at_or_before_its_cutoff
AC-001 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_001_a_decision_clock_after_the_cutoff_is_refused_as_hindsight
AC-001 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_001_a_canonical_outcome_attaches_only_with_committed_fill_evidence
AC-001 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_001_a_defective_canonical_payload_is_refused_and_quarantined
AC-002 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_002_population_economics_is_a_ratio_of_sums_not_a_mean_of_ratios
AC-002 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_002_a_payload_whose_net_disagrees_with_gross_minus_fees_is_refused
AC-002 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_002_canonical_outcomes_reach_economics_end_to_end
AC-002 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_002_realized_measures_never_carry_a_counterfactual_or_hindsight_kind
AC-003 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_003_capital_time_is_occupied_capital_times_realized_duration
AC-003 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_003_a_position_beyond_its_declared_horizon_is_a_named_breach
AC-004 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_004_a_mixed_population_economic_total_is_refused
AC-004 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_004_every_retained_population_is_measured_in_its_own_slot
AC-005 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_005_an_unresolved_or_censored_outcome_is_never_matured
AC-005 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_005_missing_or_zero_coverage_is_insufficient_evidence
AC-005 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_005_coverage_beyond_the_declared_window_is_refused
AC-006 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_006_a_root_cause_is_classified_by_frozen_precedence
AC-006 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_006_repair_lineage_resolves_to_its_root_and_refuses_a_dangling_parent
AC-006 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_006_a_cyclic_or_self_repair_lineage_fails_closed
AC-007 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_007_every_artifact_receives_exactly_one_named_disposition
AC-007 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_007_an_unusable_or_never_realized_artifact_is_quarantined_or_rejected
AC-007 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_007_no_disposition_grants_authority_or_changes_a_policy
AC-008 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_008_a_prospective_comparison_is_paired_on_one_frozen_baseline
AC-008 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_008_an_unpaired_or_double_taken_grain_is_refused
AC-008 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_008_insufficient_support_is_reported_and_can_never_promote
AC-009 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_009_the_increment_contract_and_pointer_are_self_consistent
AC-009 -> tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_009_no_new_consumer_profile_or_trading_authority_is_introduced

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F-prospective-outcome-integration.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py

DEFERRED DISCOVERIES:
- The dispositions this increment computes are evidence in hand: they are not durably registered, so an artifact's consumption disposition is reproducible from the same evidence but is not itself a persisted registry row. Where a durable disposition registry or learning-consumption ledger should live, and which registry facts are review-critical, stay `OQ-09`; no store is added here.
- The committed fill count is supplied by the caller because the canonical terminal outcome record does not retain a per-fill count. Fill-level evidence, partial fills, latency, cancellation and stop-gap modelling stay `NOT_IMPLEMENTED` on the execution plane and are not added here.
- Horizon boundaries, expected holding ranges per horizon class and the rotation, requalification and turnover budgets stay `OQ-03` and `OQ-05`; the increment compares realized duration with the expectation's own declared horizon and invents no boundary of its own.
- The coverage figure is a caller-declared measurement of the declared window; which measurement establishes coverage, and whether it should be derived from committed evidence instead, stays `OQ-07`.
- Gate thresholds for section 18 and the section 12 metric definitions stay `OQ-07`; this increment reports support, coverage and uncertainty and asserts no threshold.
- Calibration, weakness recurrence and post-fix economic harm are named by `LE-03` and are measured by the increments that own calibration evidence; root-cause classes here are structural, derived only from a frozen expectation's own declared figures and its committed outcome facts, and no calibration threshold is introduced.
- Windows-only full-suite failures observed while validating this increment reproduce at the parent commit and are not attributed to this increment; Linux CI remains the final-validation prerequisite. Ruff is not installed in the local Windows toolchain, so Linux CI stays authoritative for lint.
- `OQ-04`, `OQ-05`, `OQ-06` and `OQ-08` remain owner inputs. `OQ-08` still governs the legacy paper-engine coexistence window, and the legacy retirement this increment does not perform belongs to `R7`.

UNAPPROVED SCOPE CHANGES:
NONE

