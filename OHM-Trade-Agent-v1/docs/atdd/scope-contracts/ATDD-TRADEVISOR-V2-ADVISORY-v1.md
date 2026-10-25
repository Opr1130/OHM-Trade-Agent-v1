INCREMENT:
ATDD-TRADEVISOR-V2-ADVISORY-v1

OWNER-APPROVED INTENT:
Authorize, and only authorize, the Tradevisor V2 advisory integration on `feature/tradevisor-v2-ingress`: default-disabled Tradevisor TradingView webhook ingress, deterministic evaluation, notifications and learning. The owner subsequently approved three *policy dispositions* on 2026-10-23: G-02, unavailable F6 permits a recorded shadow hypothesis but never actionable qualification without an independently approved trusted forecast or deterministic fallback; G-03, the explicitly classified noncanonical inbox is an approved shadow-observation/learning exception with no canonical admission or execution rights; and G-04, actionable SWING qualification requires independently trusted completed 4H context, never inferred from 1H. This increment implements fail-closed qualification and retains shadow plan evidence. No F6 model or fallback, no 4H data provider and no canonical handoff are authorized or created. ATDD-29 is now exercised by an explicit F6 abstention gate; ATDD-38 is an approved scope exception **not an outage PASS**; ATDD-43 remains structurally partial pending soak. This contract grants no trading authority, changes no risk default, no fixed sizing, no minimum reward-to-risk, no order path, no canonical writer, no selector, no release-profile key and no production configuration. It does not authorize deployment, activation, credentials, Telegram production delivery, paper or funded execution, or merging PR #444.

ADDENDUM — TV2-FR-01 advisory observability (owner-authorized mission):

The owner confirmed the Tradevisor V2 indicator is an external, invite-only
TradingView script whose Pine source is inaccessible, and authorized the
following additions on a separate local branch off this increment's head:
external-observation provenance (which records the vendor identity and any
declared script version as UNVERIFIED, never as certification), read-only
native-context reuse (BTC regime, breadth, chase risk, liquidity, instrument
coverage), final-allocation economic qualification (the same F5 owner evaluated
at the F7-permitted notional), an opt-in bounded advisory observation digest,
and clearer approved-format presentation. F6 and completed-4H remain blocking
for actionable output; WATCH/SHADOW evidence never becomes an instruction; the
observation digest is disabled by default and carries no entry, stop, target or
size. No threshold, risk default, geometry formula, capital algorithm, canonical
path, learning file or deployment control is changed.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/atdd/README.md: the ATDD scope-control mechanism, its precedence, the required contract sections and the movable `ACTIVE_INCREMENT` pointer.
- OHM-Trade-Agent-v1/app/services/tradevisor_native_context.py: read-only native evidence (BTC regime, breadth, chase risk, liquidity, coverage) delegated to existing owners.
- OHM-Trade-Agent-v1/app/services/tradevisor_observation.py: separately classified, bounded, opt-in advisory observation digest.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md: the canonical rule that `ACTIVE_INCREMENT` is mutable orchestration state naming the single increment currently authorized to change files, and that exact current-increment enforcement belongs to `tests/atdd_scope.py` and CI.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-LEARNING-RECOVERY-v1.md: the increment this contract succeeds. Its AC-005 acceptance test verifies that the pointer resolves to an existing contract and declares its own increment; it does not pin the pointer to itself, so the pointer is formally movable without breaking that increment.
- OHM-Trade-Agent-v1/tests/atdd_scope.py: the canonical parser and checker, including exit 0 PASS, 1 TRACEABILITY_GAP and 2 SCOPE_CHANGE_REQUIRED.
- .github/workflows/pytest.yml `atdd-scope` job: reads `docs/atdd/ACTIVE_INCREMENT` and compares the pull-request diff against that increment only.
- OHM-Trade-Agent-v1/TRADEVISOR_V2.md, OHM-Trade-Agent-v1/docs/atdd/tradevisor_v2_atdd.md and OHM-Trade-Agent-v1/docs/reviews/tradevisor_v2_certification.md: the operator documentation, the 52-scenario matrix and the certification record this increment makes authoritative for the integration.
- AGENTS.md and CLAUDE.md: smallest change that solves the proven problem; no trading authority; no new infrastructure; never weaken a gate.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the Tradevisor V2 integration as shipped, with no operator configuration
WHEN:
the installed posture is inspected
THEN:
the ingress is disabled by default, no committed Compose environment sets OPIP_TRADEVISOR_ENABLED, a request to the webhook route returns 404 and no event is durably recorded; and this increment is authorized only by this contract together with the ACTIVE_INCREMENT pointer that names it, which the scope checker proves

AC-002:
GIVEN:
an authenticated, well-formed Tradevisor BUY or SELL alert for a configured source instrument
WHEN:
it is delivered to the versioned webhook
THEN:
the route answers 202 with status accepted, trade_authority_changed false, and one durable row recording the source facts; acceptance means durably received and never means authorized, evaluated or approved

AC-003:
GIVEN:
a delivery whose edge verification stamp is wrong or absent, whose immediate peer is untrusted, or whose body token is missing or wrong
WHEN:
the route handles it
THEN:
it is refused with 404 before any schema detail is disclosed, no row is recorded, and the ingestion token is never persisted, logged or echoed

AC-004:
GIVEN:
a payload with an unsupported schema version, a non-positive or unparseable price, an unmapped source symbol, or a body larger than the configured cap
WHEN:
the route handles it
THEN:
it is refused with 422 or 413, no durable row is created, and no market, AI or Telegram call is made on the acknowledgement path

AC-005:
GIVEN:
an alert whose {{time}} describes a bar that has not closed, whose trigger is older than the freshness window, or whose trigger is in the future beyond the skew allowance
WHEN:
ingress validation runs
THEN:
it is refused with 422 because {{time}} is the bar open and bar close is derived as open plus interval, so an unfinished, stale or future-dated bar can never be treated as closed evidence

AC-006:
GIVEN:
the same alert delivered twice, including a replay whose receipt time differs
WHEN:
both deliveries are processed
THEN:
the first answers accepted and the second answers duplicate with the same dedup_id, and exactly one durable row exists because the dedup identity is built from source facts only

AC-007:
GIVEN:
a BUY alert for which native market evidence or executable spread and depth cannot be obtained
WHEN:
the advisory pipeline evaluates it
THEN:
the decision is non-actionable with no plan, recorded as MARKET_DATA_UNAVAILABLE or EXECUTION_EVIDENCE_UNAVAILABLE, and no recommendation is rendered

AC-008:
GIVEN:
a Tradevisor SELL alert
WHEN:
the advisory pipeline evaluates it
THEN:
the outcome is WATCH with actionable false and no plan, the evidence records short_order NOT_PERMITTED and auto_close_funded_position NOT_PERFORMED, and no short is opened and no funded position is closed

AC-009:
GIVEN:
a source payload that asserts risk, sizing, order type or trading authority, or an evaluated decision
WHEN:
the boundary is exercised
THEN:
the authority-asserting payload is refused with 422 because the schema forbids unknown fields, and any evaluated decision records trade_authority_changed false and native_admission NOT_PERFORMED

AC-010:
GIVEN:
an event whose evaluation keeps raising
WHEN:
it exceeds the configured attempt limit
THEN:
it reaches the terminal POISONED disposition, the durable row states it, and the recorded failure reason carries the exception type only and never its message text

AC-011:
GIVEN:
a frozen actionable plan that was never delivered
WHEN:
the delivery or retry pass runs after the plan has expired
THEN:
the plan is no longer a retry candidate, nothing is sent, and stale actionable advice cannot be resurrected because a retry re-evaluates fresh evidence instead of re-sending a stored plan

AC-012:
GIVEN:
persisted advisory decision evidence and a learning worker whose deployed SHA does not equal the expected production SHA
WHEN:
the consumption job builds its report
THEN:
it fails closed with RELEASE_DRIFT or RELEASE_UNVERIFIED, publishes no cohort metrics, records policy_promotion NONE and trade_authority_changed false, and the release classifier is the learning plane's own function object rather than a copy

AC-013:
GIVEN:
the operator cycle and the Tradevisor advisory phase
WHEN:
the cycle runs with the ingress disabled, enabled, or with a failing advisory pass
THEN:
the phase is inert while disabled, runs strictly after active-position protection, and any failure is contained so it never delays or aborts the cycle; this is structural independence only and is not overload or soak certification

AC-014:
GIVEN:
the durable advisory store and the integration's documentation
WHEN:
the evidence disposition is read
THEN:
it declares evidence_class ADVISORY_NON_CANONICAL, canonical_handoff NOT_PERFORMED and consumption app.jobs.report_tradevisor_learning with no admission or execution authority; the owner-approved G-03 shadow exception is explicit, ATDD-38's canonical-outage scenario remains UNEXERCISED (not PASS), and ATDD-43 overload proof is pending

AC-015:
GIVEN:
an otherwise qualifying BUY with native 1H, R4, F5, F7 and known exposure, but no trusted F6 forecast/approved fallback or completed trusted SWING 4H context
WHEN:
the evaluator finishes the proposed trade hypothesis and the notifier receives the decision
THEN:
the decision is WATCH and nonactionable, records ADVISORY_EVIDENCE_INCOMPLETE with explicit F6 and (for SWING only) 4H blockers, preserves a frozen hypothetical plan for prospective shadow measurement without an actionable expiry, and emits no actionable Telegram notification; forward-only candle observations are measured as SHADOW_HYPOTHESIS with no fill attribution, but unrelated WATCH rejections are not treated as trade hypotheses; no 1H approximation or missing-status substitution creates an authorization

AC-016:
GIVEN:
an authenticated Tradevisor alert, optionally declaring a version of the external invite-only indicator
WHEN:
the ingress admits it
THEN:
the normalized signal carries provenance that records the external indicator identity, the source exchange and symbol, the timeframe, the direction and the source bar open, together with the operator-declared integration version of THIS contract and the body-supplied declared version, the declared version is marked not verified, and the algorithm verification is recorded as UNVERIFIED_EXTERNAL_SCRIPT because a webhook body can never prove the contents of the external Pine script

AC-017:
GIVEN:
the same source bar delivered with and without a declared indicator version, and payloads with empty, over-long or non-conforming version values
WHEN:
each is admitted or replayed at a different receipt time
THEN:
every valid variant produces the same dedup identity so provenance can never defeat source-fact idempotency, an absent version is recorded as null rather than invented, and each malformed or conflicting value is refused before any durable row is created

AC-018:
GIVEN:
a Tradevisor BUY with native market evidence available, and a BTC context provider that fails
WHEN:
the advisory evaluation runs
THEN:
the native context block carries BTC regime, market breadth, chase risk, signal quality, ranking and liquidity fields, each with its source, observed timestamp, freshness and availability state, the BTC field delegates to the native scanner on the configured market, breadth and ranking report UNAVAILABLE with an explicit input-contract reason instead of an approximation, and a failing BTC provider produces UNAVAILABLE with its reason and never a PASS or corroboration

AC-019:
GIVEN:
a native snapshot whose executable price is at or above its recent high
WHEN:
the native context is assembled
THEN:
chase-risk band, score, late-entry and retest state come from the existing chase-risk owner marked advisory only, every input that was not supplied is disclosed as a limitation, and the field neither grants nor removes trading authority

AC-020:
GIVEN:
the configured Tradevisor instrument mapping, with and without a supplied Kraken venue catalogue
WHEN:
instrument coverage is reported
THEN:
each configured symbol is classified SUPPORTED, UNSUPPORTED or UNVERIFIED_NO_VENUE_CATALOGUE, support is never assumed without a catalogue, and the aggregate status distinguishes verified, partially supported and unverified coverage

AC-021:
GIVEN:
a plan that clears the F5 economic bar at the account envelope but whose F7-permitted allocation implies a smaller notional
WHEN:
the qualified-buy composition finalizes
THEN:
the economic gate is re-evaluated against the F7-permitted allocation using the unchanged F5 owner and thresholds, a candidate whose final net profit falls below the unchanged minimum is withheld with an explicit final-allocation rejection and reports no economic pass, a candidate that still passes reports one basis for both the gate and the displayed figures, repeated evaluation is byte identical, and missing execution economics still fails closed

AC-022:
GIVEN:
a candidate that passed the market, attainability and economic gates but was withheld by the F6 or 4H authority gate, and separately a candidate rejected for unavailable market data or for being SELL evidence
WHEN:
advisory observability is composed
THEN:
only the withheld-but-substantive candidate produces an observation that states the symbol, source exchange and Kraken venue, the external observation, the source-bar and evaluation timestamps, native corroboration or contradiction, BTC and chase-risk context when available, the missing evidence and the abstention reason, and it is explicitly labelled WATCH / NO TRADE with NO TRADE RECOMMENDATION and no entry, limit, stop, target, size or qualified wording, while the other candidates produce nothing

AC-023:
GIVEN:
more observations than one digest may carry, the same observation set offered twice, and a transport that refuses the message
WHEN:
the digest is delivered
THEN:
at most the bounded number of items is included with the remainder counted as bounded, a single digest is sent rather than one message per event, a repeat of the same evidence is recognised as an already confirmed emission, its missing acknowledgement is repaired without a second send, and the existing notification policy still owns the cooldown and budget decision, and a transport refusal is reported as a send failure that is never claimed as delivered

AC-024:
GIVEN:
the shipped configuration with no operator override
WHEN:
observation delivery is attempted
THEN:
the digest returns a disabled status without contacting the transport, the observation flag defaults to false, and the ingress itself remains disabled by default

AC-025:
GIVEN:
an actionable decision formatted for delivery
WHEN:
the message is rendered
THEN:
it states the evidence-as-of, source-bar-close, receipt and evaluation timestamps, the execution book age or its unavailability, the source exchange and the Kraken evaluation venue, the F7-permitted capital basis that the economic gates passed, the horizon classification with its native ATR and an explicit statement that no holding duration is modelled, while retaining the expiry, the invalidation limit and the human-approval footer

AC-026:
GIVEN:
a decision that is actionable and a decision that is withheld by an authority gate
WHEN:
the pipeline composes delivery for each
THEN:
an actionable decision yields only an actionable notification and never an observation, a withheld decision yields only an observation and never a notification, and the two delivery classes can never both describe the same decision

AC-027:
GIVEN:
a withheld candidate whose internal decision reason and shadow proposal literally contain an internal instruction token, such as an immediate-entry or pullback-entry proposal
WHEN:
the operator-facing observation is rendered, singly and as a digest
THEN:
no internal instruction token appears in the rendered text, the abstention is still explained in operator language together with the missing evidence, the durable decision record and shadow evidence remain byte-identical to a fresh evaluation, and any candidate whose text would carry such a phrase is refused rather than rendered

AC-028:
GIVEN:
a final economic qualification that re-runs the F5 owner at the F7-permitted allocation with a full capital fraction, and a replaced or malformed allocation of NaN, infinity, a negative, zero, a below-minimum value, or an amount exceeding the permitted position cap or risk budget
WHEN:
the qualified-buy composition finalizes
THEN:
the permitted allocation is proved to remain inside the unchanged position cap and fixed risky-trade budget for every evaluated equity and volatility, a passing case reports one basis for both the gate and the displayed figures with a final notional no larger than the envelope, and every unusable allocation fails closed as an explicit rejection without raising and without reporting an economic pass

AC-029:
GIVEN:
a native eligible market universe, the operator's configured TradingView instrument map, and the markets that appear in durable Tradevisor observation rows, and separately no native universe at all
WHEN:
discovery coverage is reported
THEN:
each market is classified into exactly one visible population of native-discovered and TradingView-configured, configured but not native, actually observed, or native with no TradingView coverage, observed markets come only from durable records so no external signal is invented, a missing native universe is reported as unavailable rather than assumed, and native opportunity ranking is declared UNAVAILABLE with its input-contract reason because reusing it would require an owner architecture decision

AC-030:
GIVEN:
an advisory observation cohort that has already been settled into durable inbox rows, and a delivery attempt that fails by refusal, by transport exception, by cooldown suppression, by process crash or by a concurrent worker taking the reservation
WHEN:
the next drain cycle runs
THEN:
the eligible observations are re-derived read-only from the durable rows and delivered at most once, a confirmed delivery writes a durable per-row acknowledgement so later cycles neither repeat nor forget, a failed or suppressed attempt writes nothing and leaves the rows pending for a bounded recovery window, more eligible rows than one digest may carry leave the remainder recoverable after the cooldown, and no competing queue, database or scheduler is introduced

AC-031:
GIVEN:
an admitted Tradevisor alert carrying provenance, and separately a legacy row written before provenance existed and a row stranded in PROCESSING by a crashed worker
WHEN:
it is enqueued, reloaded, reclaimed and evaluated
THEN:
the persisted row contains the provenance, the reloaded signal reproduces it exactly, the frozen decision evidence carries it, dedup identity is unchanged, a legacy row reloads as unknown provenance rather than a fabricated verification, and a reclaimed row is evaluated with its provenance intact

AC-032:
GIVEN:
a BTC context provider that returns a snapshot for a different market, a snapshot with no market-data validation, an unqualified validation, a validation without a source timestamp, and a qualified validation with a source candle timestamp
WHEN:
the native context is assembled
THEN:
only the identity-matching, qualified, source-stamped snapshot is AVAILABLE, its observed timestamp is the source candle time rather than the evaluation time, and every other case is UNAVAILABLE with a specific reason and no observed timestamp

AC-033:
GIVEN:
a chase-risk assessment produced from the native owner with only a subset of its inputs supplied
WHEN:
the native context and the operator observation are rendered
THEN:
the record exposes input completeness and the missing input groups, the conclusion is marked indeterminate and the band indicative-only rather than a complete low-risk finding, the unsupplied inputs remain listed as limitations, and the rendered line says that the inputs are partial and indicative

AC-034:
GIVEN:
the configured observation minimum interval set longer than the notification-policy default, set shorter than it, and a cooldown already in force for the exact evidence set
WHEN:
observations are offered for delivery
THEN:
the stricter of the configured interval and the policy cooldown governs, the configuration can never shorten the shared cooldown, suppression reports the remaining retry delay, and a suppressed attempt marks nothing so the durable observation remains eligible

AC-035:
GIVEN:
F7 owner results that are negative in risk dollars, inconsistent between position percentage and capital, missing required fields, non-numeric, non-finite, or an unexpected object or exception
WHEN:
the qualified-buy composition finalizes
THEN:
every such result fails closed as an explicit rejection with a machine-readable reason code, no exception escapes the evaluator, no economic pass is reported, no F5 or F7 algorithm, threshold or risk limit is changed, and a valid allocation still qualifies on the unchanged basis

AC-036:
GIVEN:
a durable observation cohort exposed to every ambiguous delivery outcome: a crash before the transport call, a crash or ledger fault after the transport accepted the digest but before the per-row acknowledgement was written, a transport timeout whose delivery outcome is unknown, a transport refusal, and a concurrent worker whose reservation later expires
WHEN:
the subsequent cycles run
THEN:
no outcome silently loses eligible evidence, content that a confirmed emission already covers is recognised and its missing acknowledgement repaired without a second send, content whose delivery outcome is genuinely unknown is retried and may therefore be delivered more than once, the documented guarantee is at-least-once for unknown outcomes and never exactly-once, and an abandoned reservation stops blocking delivery once its lease expires

AC-037:
GIVEN:
a backlog of settled observations, a row older than the recovery horizon, ingress that exceeds digest throughput, and an inbox that has reached its configured capacity
WHEN:
the delivery cycle and the backlog report run
THEN:
the recovery horizon covers at least a full per-call backlog at the governed cadence, each call surfaces a bounded number of rows without deleting any, every row is accounted for as eligible, already reported, aged out or not observable so nothing disappears silently, a sustained backlog drains across successive cooldowns with each row acknowledged exactly once, and capacity backpressure refuses new ingress while leaving existing evidence intact and deliverable

AC-038:
GIVEN:
acknowledgement markers that are ancient, recent, digest-level, alert-level and numerous, together with a saturated observation backlog
WHEN:
acknowledgements are written, compaction runs, and a protection notification is requested
THEN:
markers older than the configured age are pruned while recent markers, digest entries and unrelated alert entries are preserved, marker volume does not change the outcome or unbounded latency of an observation lookup, and a protection alert is still authorised and reserved regardless of the observation backlog or marker population because the observation class remains noncritical
AC-039:
GIVEN:
rendered observation digests whose combined prose can exceed Telegram's message limit, including verbose, oversized, and non-BMP content
WHEN:
an advisory observation digest is delivered
THEN:
the rendered message is bounded by a conservative budget counted in the UTF-16 units the transport enforces, the number of included observations is reduced before any included observation's meaning is shortened, a single record that cannot fit at full detail is rendered compactly with its identity, market, bar close, rejection classification and an explicit detail-omission note, a rendering that still cannot fit fails closed with an explicit non-delivery outcome, and the fingerprint, policy reservation, transport confirmation and durable acknowledgement refer to exactly the observations transmitted while every excluded observation remains unacknowledged and retryable



EXPLICITLY OUT OF SCOPE:
- Any order placement, modification, cancellation or confirmation path, and any execution authority
- Any change to fixed risk sizing, minimum reward-to-risk, position verification or execution safeguards
- Registering a trusted F6 forecast, approving a deterministic F6 actionable fallback, or activating a forecast-dependent qualification pathway (G-02's fail-closed policy *is* approved)
- Introducing a canonical handoff or promoting this shadow observation to canonical admission/execution (G-03's noncanonical shadow exception *is* approved)
- Fetching or deriving trustworthy completed 4H data, or changing the native 1H regime basis (G-04's SWING fail-closed policy *is* approved)
- Enabling the ingress in any environment, activating TradingView alerts, creating or rotating credentials, enabling Telegram production delivery, or enabling paper or funded execution
- Deployment, production configuration, branch protection, rulesets or workflow changes
- Any change to the Wave 8.2 TradingView bridge, the release-profile key set, the canonical writer, the selector or the learning-worker SHA contract
- Changing the scope checker's parser, arrow syntax, status codes or fail-closed behaviour
- Editing, retiring or weakening another increment's contract, acceptance traceability or assertions
- Adding a second active increment, a concurrent-increment mechanism, or a second scope-control mechanism
- Certifying, reproducing or reverse-engineering the invite-only external indicator's Pine source, or treating a body-supplied version string as proof of its contents
- Raising an observation, a WATCH decision or a shadow hypothesis to actionable or qualified status, or bypassing the F6/4H authority gates to do so

FROZEN BOUNDARIES:
- `docs/atdd/ACTIVE_INCREMENT` names exactly one increment and that increment's contract must exist; `tests/atdd_scope.py` remains the sole owner of exact current-increment enforcement.
- `ATDD-OPIP-LEARNING-RECOVERY-v1` remains a valid, complete, unmodified contract whose acceptance tests continue to pass; its AC-005 expression of the pointer rule is the movable form and is not weakened by this handoff.
- `OPIP_TRADEVISOR_ENABLED` stays false unless an operator explicitly sets it; no committed environment may set it.
- Advisory evidence never acquires admission, execution, Telegram-production or canonical-writer authority.
- ATDD-29 is an exercised fail-closed F6 abstention check; ATDD-38 remains an **owner-approved shadow-only scope exception**, not a successful canonical outage test; ATDD-43 remains partial, without load/soak certification.
- No production, trading, Feature Bus, paper or deployment authority is created, widened or transferred by this increment.
- Learning-evidence retention is fail-closed (N-01): a terminal *transport* status never authorises deletion. Retention requires a non-learning terminal state (`POISONED`) or a valid durable acknowledgement naming the exact `dedup_id`, an allowed disposition, a definitive measurement status and the current release SHA. Unacknowledged evidence is retained; capacity enforces existing 503 backpressure rather than evicting it; legacy rows without consumption metadata are treated as unconsumed.
- A definitive outcome requires provably complete observation (N-02): expected bars are the UTC epoch-grid multiples of the real candle interval starting strictly after the decision instant and closing at or before the window end. Missing, duplicated-conflicting, misaligned, unfinished or non-finite bars yield `PARTIAL_COVERAGE`, which never contributes to measured counts, false-positive rates or profitability statistics. A touch is never a fill.


ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_001_default_installation_exposes_nothing
AC-002 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_002_authenticated_buy_and_sell_are_durably_accepted
AC-003 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_003_unauthenticated_alerts_learn_nothing
AC-004 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_004_malformed_and_oversized_payloads_are_refused
AC-005 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_005_unfinished_stale_and_future_events_are_refused
AC-006 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_006_duplicate_delivery_creates_exactly_one_record
AC-007 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_007_missing_evidence_abstains_without_a_plan
AC-008 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_008_sell_is_evidence_only
AC-009 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_009_source_cannot_assert_authority
AC-010 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_010_poison_payload_reaches_a_terminal_disposition
AC-011 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_011_expired_plan_is_never_delivered
AC-012 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_012_learning_fails_closed_on_release_drift
AC-013 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_013_advisory_phase_is_last_inert_and_contained
AC-014 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_014_evidence_is_noncanonical_and_has_a_consumer
AC-015 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_015_untrusted_f6_and_missing_swing_4h_never_send_actionable_alert
AC-016 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_016_provenance_records_external_identity_and_unverified_algorithm
AC-017 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_017_provenance_never_changes_replay_identity_and_bad_values_fail_closed
AC-018 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_018_native_btc_context_carries_source_timestamp_and_availability
AC-018 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_018_unavailable_btc_context_is_never_reported_as_a_pass
AC-019 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_019_chase_risk_is_reused_from_the_native_owner_as_context_only
AC-020 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_020_instrument_coverage_distinguishes_supported_unsupported_unknown
AC-021 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_021_final_economics_pass_uses_the_same_basis_for_display
AC-021 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_021_final_economics_rejects_when_f7_allocation_falls_below_minimum
AC-021 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_021_missing_execution_economics_still_fails_closed
AC-021 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_021_repeated_evaluation_is_byte_identical
AC-022 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_022_authority_blocked_candidate_yields_only_an_observation
AC-022 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_022_non_substantive_rejections_are_not_observed
AC-023 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_023_digest_is_bounded_and_deduplicated_by_policy
AC-023 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_023_transport_failure_is_reported_and_never_claimed_delivered
AC-024 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_024_observation_digest_is_disabled_by_default
AC-025 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_025_actionable_alert_states_timing_venue_and_capital_basis
AC-026 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_026_observation_and_actionable_delivery_are_mutually_exclusive
AC-027 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_027_contradictory_instruction_bearing_observation_is_refused
AC-027 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_027_internal_shadow_evidence_is_preserved_unchanged
AC-027 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_027_watch_observation_carries_no_internal_instruction_tokens
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_account_risk_scales_with_the_allocation_fraction
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_account_risk_uses_total_equity_denominator
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_allocation_never_exceeds_position_or_account_risk_limits
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_economic_decision_is_unchanged_by_the_denominator
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_non_finite_or_negative_allocation_fails_closed_without_raising
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_oversized_allocation_is_refused_as_inconsistent
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_oversized_allocation_still_refused_with_equity_basis
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_unusable_allocation_never_reports_an_economic_pass
AC-028 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_028_zero_or_non_finite_equity_fails_closed
AC-029 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_029_discovery_coverage_distinguishes_the_four_coin_sets
AC-029 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_029_missing_native_universe_is_never_assumed
AC-029 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_029_observed_markets_come_from_durable_records_only
AC-029 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_029_opportunity_ranking_reuse_is_declared_unavailable
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_bounded_digest_leaves_the_remainder_recoverable
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_consecutive_cycles_and_concurrent_workers_send_once
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_crash_after_settlement_is_recovered_from_durable_rows
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_delivery_never_acknowledges_a_different_pending_row
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_fingerprint_separates_rows_that_differ_only_by_identity
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_missing_or_malformed_identity_fails_closed
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_retry_repairs_the_missing_acknowledgement_of_the_same_identity
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_successful_retry_delivers_exactly_once
AC-030 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_030_transport_refusal_or_exception_keeps_rows_pending
AC-031 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_031_legacy_and_reclaimed_rows_stay_honest
AC-031 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_031_provenance_survives_enqueue_reload_and_evaluation
AC-032 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_032_btc_context_requires_identity_and_verified_freshness
AC-033 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_033_chase_risk_never_presents_partial_inputs_as_complete
AC-034 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_034_configuration_never_shortens_the_shared_cooldown
AC-034 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_034_configured_interval_governs_the_cooldown
AC-034 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_034_suppression_never_discards_durable_observations
AC-035 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_035_missing_or_nonnumeric_allocation_fields_fail_closed
AC-035 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_035_negative_risk_and_inconsistent_position_percentage_fail_closed
AC-035 -> tests/test_tradevisor_advisory_observability_v1.py::test_ac_035_non_finite_risk_and_unexpected_owner_results_fail_closed
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_concurrent_worker_reservation_expires
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_crash_before_transport_leaves_evidence_available
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_formatting_refusal_is_a_reported_non_delivery
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_missing_acknowledgement_after_delivery_is_repaired_not_duplicated
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_policy_state_write_failure_after_delivery_is_repairable
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_production_sender_contract_is_satisfied_end_to_end
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_production_sender_negative_outcomes_never_acknowledge
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_standalone_drain_survives_an_observation_formatting_refusal
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_transport_refusal_keeps_evidence_pending
AC-036 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_036_transport_timeout_is_an_unknown_outcome_and_is_retried
AC-037 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_037_backlog_is_capped_per_call_without_losing_rows
AC-037 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_037_inbox_capacity_rejects_ingress_without_losing_pending
AC-037 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_037_recovery_window_expiry_is_counted_not_silent
AC-037 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_037_sustained_ingress_drains_across_cycles_without_loss
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_acknowledgement_write_compacts_in_the_same_pass
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_confirmed_emission_without_marker_keeps_rows_pending
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_cross_row_acknowledgement_filters_only_its_own_row
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_disabled_observation_path_keeps_drain_and_lease_recovery
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_disabled_observation_path_skips_inbox_and_policy_reads
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_malformed_markers_never_acknowledge_unrelated_rows
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_marker_compaction_prunes_old_and_keeps_active
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_observation_lookup_stays_bounded_with_many_markers
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_protection_alerts_survive_saturated_observation_backlog
AC-038 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_038_recovery_lookup_reads_policy_state_once_at_any_scale
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_digest_exactly_at_the_budget_keeps_full_detail
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_disabled_feature_performs_no_rendering
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_excluded_rows_are_delivered_on_a_later_cycle
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_fingerprint_and_acks_cover_only_transmitted_rows
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_message_that_cannot_fit_fails_closed
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_one_unit_over_the_budget_uses_the_compact_rendering
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_selection_is_deterministic
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_unicode_heavy_digest_respects_the_utf16_budget
AC-039 -> tests/test_tradevisor_delivery_reliability_v1.py::test_ac_039_verbose_rows_are_reduced_not_truncated


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md
AC-001 -> OHM-Trade-Agent-v1/app/core/config.py
AC-001 -> OHM-Trade-Agent-v1/app/main.py
AC-001 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_boundaries_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/models/tradevisor.py
AC-002 -> OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py
AC-002 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-002 -> OHM-Trade-Agent-v1/tools/tradevisor_alert_preview.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_ingress_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/api/tradevisor.py
AC-003 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.example
AC-003 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_ingress_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_ingress_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_market.py
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_pipeline.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_tradevisor_evaluation_v1.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-008 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_tradevisor_evaluation_v1.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-009 -> OHM-Trade-Agent-v1/app/api/tradevisor.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_boundaries_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-010 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_tradevisor_inbox_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-011 -> OHM-Trade-Agent-v1/app/services/tradevisor_notifier.py
AC-011 -> OHM-Trade-Agent-v1/app/services/tradevisor_pipeline.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_notifier_v1.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_recovery_v1.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-012 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-012 -> OHM-Trade-Agent-v1/app/jobs/report_tradevisor_learning.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_v1.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-013 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-013 -> OHM-Trade-Agent-v1/app/jobs/drain_tradevisor_inbox.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_tradevisor_boundaries_v1.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-014 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-014 -> OHM-Trade-Agent-v1/app/jobs/report_tradevisor_learning.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/tradevisor_v2_atdd.md
AC-014 -> OHM-Trade-Agent-v1/docs/reviews/tradevisor_v2_certification.md
AC-014 -> OHM-Trade-Agent-v1/tests/test_tradevisor_inbox_v1.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_tradevisor_recovery_v1.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-015 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-015 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-015 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/tradevisor_v2_atdd.md
AC-015 -> OHM-Trade-Agent-v1/docs/reviews/tradevisor_v2_certification.md
AC-015 -> OHM-Trade-Agent-v1/tests/test_tradevisor_evaluation_v1.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_v1.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-016 -> OHM-Trade-Agent-v1/app/core/config.py
AC-016 -> OHM-Trade-Agent-v1/app/models/tradevisor.py
AC-016 -> OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py
AC-016 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-016 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-017 -> OHM-Trade-Agent-v1/app/models/tradevisor.py
AC-017 -> OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-018 -> OHM-Trade-Agent-v1/app/core/config.py
AC-018 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-018 -> OHM-Trade-Agent-v1/app/services/tradevisor_native_context.py
AC-018 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-019 -> OHM-Trade-Agent-v1/app/services/tradevisor_native_context.py
AC-019 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-020 -> OHM-Trade-Agent-v1/app/services/tradevisor_native_context.py
AC-020 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-021 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-021 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-022 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-022 -> OHM-Trade-Agent-v1/app/services/tradevisor_pipeline.py
AC-022 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-023 -> OHM-Trade-Agent-v1/app/jobs/drain_tradevisor_inbox.py
AC-023 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-023 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-024 -> OHM-Trade-Agent-v1/app/core/config.py
AC-024 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-024 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-025 -> OHM-Trade-Agent-v1/app/services/tradevisor_notifier.py
AC-025 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-025 -> OHM-Trade-Agent-v1/tools/tradevisor_alert_preview.py
AC-026 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-026 -> OHM-Trade-Agent-v1/app/services/tradevisor_pipeline.py
AC-026 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-027 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-027 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-027 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-028 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-028 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-029 -> OHM-Trade-Agent-v1/app/services/tradevisor_native_context.py
AC-029 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-029 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-030 -> OHM-Trade-Agent-v1/app/jobs/drain_tradevisor_inbox.py
AC-030 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-030 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-030 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-030 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-031 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-031 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-031 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-032 -> OHM-Trade-Agent-v1/app/services/tradevisor_native_context.py
AC-032 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-033 -> OHM-Trade-Agent-v1/app/services/tradevisor_native_context.py
AC-033 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-033 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-034 -> OHM-Trade-Agent-v1/app/core/config.py
AC-034 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-034 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-035 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-035 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-036 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-036 -> OHM-Trade-Agent-v1/tests/test_tradevisor_delivery_reliability_v1.py
AC-036 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-037 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-037 -> OHM-Trade-Agent-v1/tests/test_tradevisor_delivery_reliability_v1.py
AC-037 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-038 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-038 -> OHM-Trade-Agent-v1/tests/test_tradevisor_delivery_reliability_v1.py
AC-038 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-039 -> OHM-Trade-Agent-v1/app/services/tradevisor_observation.py
AC-039 -> OHM-Trade-Agent-v1/tests/test_tradevisor_delivery_reliability_v1.py
AC-039 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md

DEFERRED DISCOVERIES:
- G-01: no genuine TradingView to O'Pip delivery has been observed; the source-to-production certification remains open and blocks activation, not this increment.
- G-02 POLICY RESOLVED 2026-10-23: F6 is missing; no unapproved actionable fallback. This increment blocks actionable qualification and preserves shadow hypotheses. A trusted model/fallback and validation remain future work.
- G-03 POLICY RESOLVED 2026-10-23: explicitly noncanonical shadow observation and learning are permitted. The original ATDD-38 canonical-outage scenario is not exercised and cannot be claimed PASS; any promotion needs separate approval.
- G-04 POLICY RESOLVED 2026-10-23: a SWING needs completed trusted 4H regime context to become actionable. No 4H provider exists yet; all current otherwise qualified SWING proposals remain WATCH, independently of the F6 block.
- G-05: F11 independence is proven structurally; no overload, burst or soak measurement exists, so ATDD-43 stays partial.
- TradingView documents an SSL client certificate that the recipient may verify. This integration does not verify it; adopting recipient-side verification is an edge change plus a staging verification, tracked as a security follow-up.
- Whether the Tradevisor scenario suite should carry acceptance markers per scenario, rather than the grouped criteria used here, is a documentation question for a future increment.
- N-01 limitation (2026-10-24): no deployment unit invokes `report_tradevisor_learning` and the learning worker holds no reverse-write authority into production storage, so no production process records a consumption acknowledgement. Eligible evidence is therefore retained until the inbox reaches capacity and ingress returns 503. Ending that state requires an owner-approved acknowledgement writer or durable archival handoff; until then the consumption lifecycle must not be described as fully operational.
- N-02 limitation (2026-10-24): coverage uses the interval the caller declares (60 minutes from the report job). A source that changes interval, or returns bars on a different grid, will be classified `PARTIAL_COVERAGE` rather than measured - a deliberate fail-closed default.
- N-03 limitation (2026-10-25, overnight certification): candle coverage validates finiteness and `high < low` only. A finite bar whose `open` or `close` lies outside `[low, high]`, or whose `open` is non-positive, is still counted as a valid bar and can produce a `MEASURED` outcome, while the native owner (`app/scanner/market_data_validation.py`) already rejects the same bar as invalid OHLC geometry. Closing this needs an owner-approved measurement increment; it changes no authority, no retention and no criterion in this contract.

UNAPPROVED SCOPE CHANGES:
NONE
