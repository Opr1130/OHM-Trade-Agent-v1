# Module 2 — Intelligence Committee & Learning Plane

> **MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.**
>
> The Intelligence Committee is shadow-only, read-only, and non-authoritative.
> A committee result is research evidence. A model emitting an opinion never
> makes that opinion canonical trading truth, and no aggregate of opinions is an
> instruction.

## Purpose

O'Pip can obtain, preserve, evaluate, compare, and learn from independent model
opinions — without giving any model trading authority, and without letting
future knowledge flatter a past decision.

The plane is deliberately **not** a voting system. It never implements
"five models vote, majority wins, execute". The flow is:

```
canonical evidence snapshot
        |
        v
independent model opinions
        |
        v
normalized structured observations
        |
        v
disagreement analysis
        |
        v
evaluation / attribution
        |
        v
human / governed downstream interpretation
```

## Authority boundaries

The committee plane has **no path** to:

- submitting, modifying, resizing, or cancelling an order;
- admitting or reserving capital;
- changing a risk limit, protection level, or forced exit;
- activating Paper v2 or altering funded/live state;
- promoting a model, strategy, threshold, or ranking influence;
- sending a notification or influencing production ranking.

These are structural, not documentary. `tests/test_opip_committee_safety_v1.py`
proves that the package cannot import an exchange, order, registry, notification,
scanner, API, or scheduler module; that no runtime root (`app/services`,
`app/jobs`, `app/api`, `app/opip/{discovery,decision,risk}`) imports it; that
`AUTHORITATIVE` and `CAN_PLACE_ORDERS` are `False`; that no contract exposes an
authority field; that the plane schedules nothing and evaluates no model text;
and that no production module reads a committee report.

## Placement and the frozen import boundary

The plane lives in `app/opip/committee/`, deliberately **outside** the frozen
runtime-import boundary that `app/opip/decision_intelligence/` is protected by.
It **extends** those contracts (reusing `Provenance`, canonical serialization,
and content-derived identity) rather than replacing them, and it depends on them
in the sanctioned direction only.

Consequence: the committee cannot persist to the canonical writer's
`decision_intelligence.*` streams, which is why it keeps its own immutable
evidence stream. When a governed bridge to the canonical plane is approved, it
belongs in `app/opip/canonical/`, following the existing adapter pattern.

## Increments

### 2A — Committee shadow foundation (`contracts.py`, `evidence.py`, `providers.py`, `opinion.py`, `outbound.py`, `runtime.py`, `ledger.py`, `store.py`, `settings.py`)

- Immutable, auditable committee cases with content-derived identities.
- A sealed point-in-time evidence snapshot. The cutoff is enforced at assembly,
  so evidence that became available afterwards cannot enter a prospective case.
- Provider isolation behind one adapter interface. A seated family with no
  adapter becomes an explicit unavailable seat; no model is ever silently
  substituted, and a response whose served identity does not match the request
  is rejected rather than attributed.
- A versioned structured opinion contract. Non-JSON, markdown-fenced,
  undeclared, or out-of-range output becomes an explicit invalid observation;
  hallucinated evidence references are refused. Every declared list field must be
  present — an omitted field is a schema failure rather than a silent empty list
  — and authenticated evidence metadata cannot be overridden by a payload.
- Fail-closed secret screening of everything permitted to leave the process.
- Idempotent, bounded execution. A committed logical observation is never
  re-queried, and a divergent replay fails explicitly instead of overwriting
  sealed evidence. A duplicate acknowledgement reuses the original call's timings
  rather than stamping a synthetic request time, so an ACK-loss replay that
  arrives after the original response stays contract-valid. A seat that has used
  every recordable attempt (5) returns a governed unavailable disposition rather
  than being invoked again to build an outcome the contract would reject.
- Enablement gate at the execution API: `run_case` refuses to run while
  `OPIP_COMMITTEE_MODE` is `off`, so the switch governs real model egress and
  spend rather than being merely advertised.

### 2B — Model bake-off / evaluation (`metrics.py`, `pricing.py`, `evaluation.py`)

- Arms compared on identical cases: individual models, a documented committee
  research signal, the existing deterministic baseline, and an always-positive
  null baseline.
- Metrics with **explicit applicability**. A metric that cannot be computed is
  reported as not applicable with a reason code, never as zero.
- Cost accounting is configuration: prices are microunits per million tokens,
  an unconfigured price yields `UNKNOWN` (never zero), and a malformed price
  specification raises rather than being ignored. The ceiling reservation is
  rechecked for every provider invocation, so a permitted retry cannot push
  cumulative spend past a declared ceiling.
- Unknown propagates: if any contributing seat's token usage is unknown, the
  aggregate is unknown rather than a partial total presented as complete. A
  recorded zero is a known value.
- Input integrity: duplicate case observations, duplicate resolved outcomes,
  duplicate baseline calls, and duplicate per-case forecasts are rejected before
  evaluation, so a repeated case cannot inflate a sample size, overwrite an
  earlier seat result, or distort a metric population. A probabilistic forecast
  is accepted only for a case type that defines one.
- An arm below the minimum scored-case count is `INSUFFICIENT_SAMPLE` and must
  not be read as a winner.
- Proper scoring rules and calibration exist and are covered by tests, but they
  are not applied to ordinal confidence (see below).

### 2C — Prospective shadow experiment (`prospective.py`)

Strict three-point protocol: **T0** seal → **T1** outcome → **T2** compare.

- A sealed prediction records the content hash of every sealed opinion plus the cutoff. The cutoff is **derived from the authenticated evidence snapshot** the committee actually ran on, not accepted from the caller: a caller-supplied earlier cutoff could otherwise admit an outcome that overlaps evidence already present at T0. Verification recomputes the sealed hashes from the recorded T0 evidence, so a later rewrite of a sealed opinion fails closed.
- An outcome may only be joined when its whole measurement window lies after the
  cutoff. An overlapping window, an outcome observed before the cutoff, or an
  outcome observed before sealing raise `HindsightLeakageError`.
- A retrospective case cannot be relabelled prospective.
- Provisional outcome evidence must state why it is not final and can never
  carry a final seat score.
- Retrospective and prospective metrics are never mixed.
- Seat accounting is exhaustive: a seat is scored, abstaining, unavailable, or
  explicitly unscored because the outcome carried no direction.
- Pending work is never silently dropped. A prediction whose outcome was observed
  but whose evaluation failed or was never persisted stays visible as
  `awaiting_evaluation` rather than disappearing from the pending counters.

### 2D — Learning & attribution (`attribution.py`)

- A disagreement matrix that separates unanimous agreement, directional
  agreement with confidence disagreement, evidence disagreement, assumption
  disagreement, single-model dissent, split decision, insufficient evidence,
  provider failure, schema-invalid response, and seat unavailability. Dissent is
  preserved, never averaged away.
- A provider failure is **not** a vote, an abstention is **not** a negative vote,
  and a missing opinion is never agreement.
- Attribution separates genuine incremental information from agreement with the
  existing baseline (`independent_incremental_correct` requires disagreeing with
  the baseline, being right, and the baseline being wrong), reports accuracy per
  case class, compares contested against unanimous accuracy, and reports
  chronological stability over both halves of the observed period.
- Every result carries an explicit non-promotion disposition: consumption is
  `ADVISORY`, and influencing production requires a separate, future,
  human-governed promotion gate.

### 2E — Canonical decision linkage by reference (`contracts.py`, `store.py`, `serialization.py`)

Closes the learning-loop edge **recommendation → canonical decision** without
granting authority and without writing Decision Intelligence streams.

`CanonicalDecisionBinding` is an **opaque by-reference** link to an existing
canonical decision and/or episode:

| Field | Rule |
| --- | --- |
| `decision_id` | Optional opaque string |
| `episode_id` | Optional opaque string |
| presence | At least one must be set; empty or whitespace-only fails closed |

The binding carries **no semantics of its own**. It is not a decision, not an
instruction, and not a second decision authority; it is a lineage reference that
lets a committee result be traced back to the decision it was advisory on.

**Identity.** The binding participates in `CommitteeCase` and
`CommitteeCaseOutcome` identity, so a bound artifact and an unbound artifact are
distinct records. Rows written before the field existed keep the identity they
were written with, and legacy unbound payloads still verify.

**No silent re-binding.** The store refuses to change a case's binding once it
has been recorded:

- **reattribution** (`D1` → `D2`) is refused;
- **unbinding** (`D1` → none) is refused;
- `recorded_case_binding()` returns `(was_recorded, binding)` so a caller can
  distinguish a case that was never run from one that was run without a binding.

**Not on `SealedPrediction`.** `SealedPrediction` deliberately does **not** carry
the binding. It previously did, which was a defect: the field participated in
`prediction_id` but was not persisted, so a bound sealed prediction failed its
own content-identity check on read-back. Do not re-add it without persisting it
in `_SEAL_FIELDS`, `sealed_prediction_to_dict`, and `sealed_prediction_from_dict`
and adding a bound round-trip test.

**No authority.** The binding is advisory linkage only. It does not write to
Decision Intelligence streams, cannot admit, rank, size, execute, or promote
anything, and a governed DI/canonical writer bridge remains a separate, future,
human-approved change.

### 2F — Durable replay-refusal evidence (`contracts.py`, `ledger.py`, `store.py`)

A committed logical observation is immutable: a replay carrying a materially
different opinion is refused rather than overwriting history, and it is never
admitted as a second accepted observation.

Refusing the replay while discarding the refusal would leave an unexplained gap
between what a worker attempted and what the store holds, so the refusal itself
is now durable:

- `CallReplayRejection` records the committed and refused opinion hashes, the
  logical seat, the case, and the refusal reason (`DIVERGENT_REPLAY`).
- It is written to its own `call_rejections.jsonl` stream, **not** the call
  stream, so it can never be read as an observation, a vote, or spend. Canonical
  outcome semantics are unchanged, and no case outcome may be published from it.
- The runtime's own divergence path — which raises before reaching the store —
  records the refusal first, so neither detection path can leave a silent gap.
- Identity is content-derived from the committed and refused opinion hashes, so
  replaying the same divergence is recognised as the same refusal and cannot
  multiply evidence. The refusal remains readable after a restart.

### 3A — Role identity, governed model registry, and role budgets (`roles.py`, `registry.py`, `role_execution.py`)

A committee seat is a **role**, not a model vendor. The role states what question
is being answered; the registry states which governed route is permitted to
answer it. Keeping those identities separate is what makes a result attributable
to a responsibility rather than to whichever vendor happened to serve it.

**Roles (IC-006).** Seven governed roles: `REGIME_ANALYST`,
`LIQUIDITY_STRUCTURE_ANALYST`, `EVENT_SENTIMENT_ANALYST`, `BULL_ADVOCATE`,
`BEAR_ADVOCATE`, `RISK_CRITIC`, `DECISION_SYNTHESIZER`.

- Six are **required**: a case missing any of them is incomplete and
  `validate_complete()` fails closed. The bull case, the bear case, and the risk
  critique are deliberately three roles — collapsing them would erase the
  disagreement structure the attribution layer measures.
- `EVENT_SENTIMENT_ANALYST` is **optional** because the qualified retained
  event/sentiment evidence it needs may genuinely not exist. Optional does not
  mean omittable: an optional role with no evidence must report `UNKNOWN`, so the
  gap is visible in the case outcome rather than absent from it.
- Each role carries its own `role_version`, prompt template, prompt version, and
  output-schema version, so one role's instructions can be revised without
  silently changing another's meaning.
- A provider family is never a role. `ProviderFamily.INDEPENDENT_REVIEWER` is a
  reserved *provider* seat and is not a role here.

**Governed model registry (IC-009/IC-010).** A versioned release of role routes.
Each entry records the role, provider family, exact model id, endpoint, prompt
and schema hashes, owner, approval state, `effective_from`/`review_by`, reasoning
mode, token/deadline/cost limits, data-retention route, and optional rollback
reference.

- A role routes to **one primary plus at most one approved fallback**. There is no
  chain of escalating models, because an unbounded chain is unbounded spend and
  an unbounded change of reasoning effort.
- The fallback receives **the same request, deadline, token reservation, and
  monetary reservation** as the primary: `shared_budget` derives from the primary,
  so failing over cannot double a case's ceiling.
- Only `APPROVED` entries route. `PROVISIONAL` is a bake-off state and is refused
  rather than usable by accident; `SUSPENDED`/`ROLLED_BACK` are refused; an entry
  past its `review_by` is refused so "approved once" cannot mean "approved
  forever"; an entry before `effective_from` is refused.
- An **unusable fallback is dropped, never substituted** by an unregistered
  model. An unregistered alias in a route is rejected at construction.
- `assert_result_served_by_route` refuses a response whose served provider/model
  is not on the role's route, so an opinion cannot be credited to a model the role
  was never approved to use.

**Role budgets and results (IC-011).** `RoleBudget` holds deadline, token, cost,
and concurrency limits for the role (concurrency capped at 4). Deadlines and
ceilings fail closed. An unknown cost is **not** treated as free: a ceiling that
cannot be evaluated cannot be enforced.

`RoleSeatResult` carries everything needed to audit one role's advisory output —
role and role version, stance, thesis, risks, evidence references, missing
evidence, research action, ordinal confidence and rubric score, status, provider,
requested and resolved model, prompt version/hash, output-schema version,
logical observation id, attempt, timing, and the recorded call-outcome
reference. Concretely:

- an `ANSWERED` result must carry a stance, a thesis, and a resolved model — a
  status claiming "answered" with nothing behind it would read as agreement;
- a non-`ANSWERED` result may **not** carry a stance, because a failure is not a
  vote;
- a missing confidence or rubric score stays `None` and is never written as zero;
  an ordinal self-report is never rescaled into a probability;
- action-bearing fields (`size`, `stop_loss`, `side`, `order`, …) are refused
  outright, case-insensitively, so an instruction cannot masquerade as research;
- an evidence citation outside the screened view is refused as a hallucinated
  reference rather than stored as evidence;
- `FAILED`, `INVALID`, `UNAVAILABLE`, and `SKIPPED_BUDGET` remain four distinct
  statuses, so a budget skip and a provider failure never look identical.

### 3B — Role routing: governed route execution (`role_router.py`)

Where the three contracts meet: `CommitteeRole` -> `RoleRoute` -> `RoleBudget` ->
`RoleSeatResult`. `RoleRouter.execute` resolves nothing itself; the caller passes
the already-resolved route, so route resolution stays a registry concern.

- **One shared reservation.** Primary and the single fallback draw on the same
  deadline, token ceiling, and monetary ceiling, so failing over cannot double
  what a case costs. The route's own limits are the request's limits.
- **Bounded failover.** At most two attempts, and a second attempt happens only
  for a retryable failure class (`TIMEOUT`, `RATE_LIMIT`, `PROVIDER_UNAVAILABLE`,
  `INTERNAL_ERROR`). A schema-invalid answer is **not** retried: the provider
  answered, the contract was not met, and asking again spends money without
  changing that. A served-identity mismatch is likewise not retried.
- **No silent substitution.** A response whose served model differs from the
  requested registry entry is refused with `PROVIDER_IDENTITY_MISMATCH` rather
  than credited to the role.
- **Honest cost.** If any attempt's cost is unknown, `cost_completeness` is
  `UNKNOWN` and `ceiling_verified` is `False`. An unverifiable ceiling is never
  reported as satisfied, and a real overrun is recorded with
  `exceeded_ceiling` rather than hidden.
- **No shared state.** Every attempt returns what it did; the router holds no
  per-run state, so concurrent role executions cannot observe each other.
- **Single wire-construction point.** The router does **not** build the outbound
  wire request; the caller supplies `build_wire_request`. Outbound screening must
  happen in exactly one place, and
  `test_committee_never_constructs_a_wire_request_outside_the_runtime` enforces
  that only `runtime.py` constructs it. The router decides *which* governed entry
  answers and *what it may spend*, and has no opinion on payload content.

### 3D — Durable scheduling from committed evidence (IC-016, `scheduler.py`)

The scheduler decides **which already-committed evidence items become committee
cases**. It calls no model, reaches no provider, and holds no provider surface at
all — a test asserts the module source contains no wire-request, provider,
screening, network, or environment access. An injected `CaseExecutor` performs any
downstream work, so scheduling is separable from execution and from authority.

- **Committed evidence only.** An item that is not durably committed, not sealed,
  or whose cutoff is in the future is recorded `INVALID` rather than scheduled,
  because a case derived from unsettled evidence cannot be reproduced.
- **Deterministic identity, idempotent delivery.** A logical case is keyed by
  `(case_id, evidence_snapshot_hash, policy_version)`. Redelivering the same
  committed evidence returns the *existing* disposition, executes nothing, and
  creates no second logical case, so at-least-once delivery cannot multiply
  committee work.
- **Restart-safe.** The cursor and every decided key live in an injected durable
  checkpoint, and each disposition is persisted **before** the cycle continues, so
  a crash mid-cycle loses no decision that was already made.
- **Exhaustive accounting.** Ten dispositions: `ELIGIBLE`, `SELECTED`,
  `SKIPPED_BUDGET`, `SKIPPED_CAPACITY`, `EXPIRED`, `INVALID`, `FAILED`,
  `UNAVAILABLE`, `LATE`, `COMPLETED`. `PopulationTally` reports every state even
  at zero, and the cycle's considered count is checked against the accounted
  total, so a skip that disappears fails the cycle rather than understating the
  population.
- **Distinct reasons stay distinct.** `SKIPPED_BUDGET` (a spending decision) and
  `SKIPPED_CAPACITY` (a concurrency decision) are separate states; `UNAVAILABLE`
  (a missing dependency or an unbounded cost) is separate from `FAILED` (a broken
  attempt); `LATE` is recorded for accountability and not treated as timely
  evidence.
- **Unknown is never favourable.** An item whose cost is unknown is refused
  (`UNAVAILABLE`), because an unverifiable budget is not a satisfied budget.
- **Failure is contained.** An executor exception becomes a recorded `FAILED`
  disposition with the exception type, never an exception into a caller, so a
  scheduler fault cannot become a trading-path fault.
- **Dark by default.** With committee mode `off` — or any unrecognised value — the
  cycle does not run, selects nothing, persists nothing, and creates no case.

### 3F — Phase B retrospective corpus and Phase C sealed experiment (IC-019, IC-020)

**Phase B (`retrospective.py`).** A role/model comparison runs on a *frozen*
retrospective corpus, and three properties are enforced rather than described:

- **Frozen.** The corpus carries a content-derived identity over every case, so an
  edit after results are seen changes the identity, and a comparison names the
  corpus hash it actually ran on.
- **Breadth checked.** The required diagnostic classes — baseline accepts, baseline
  rejects, wins, losses, late extensions, no-fills, missing evidence, grade B/C
  evidence, incidents — must all be present, and the corpus must meet
  `MINIMUM_CORPUS_CASES` (120). A corpus of easy cases is refused rather than used
  with a caveat nobody reads.
- **Answer hidden.** A case whose model-bound payload contains its own resolved
  outcome is refused, because the comparison would then measure recall of the
  fixture rather than analysis. The check is recursive, so a nested `label` cannot
  smuggle it through.

Every Phase-B result carries the explicit disposition
`RESEARCH_ONLY_NOT_PORTFOLIO_EVIDENCE`, and the type refuses to be constructed with
a prospective phase, `automatic_promotion=True`, or a trading-authority change. A
retrospective win rate is not realised profitability, and the record says so.

**Phase C (`experiment.py`).** A prospective experiment is *sealed before results
are seen*:

- **Registration freezes what would otherwise be chosen later**: corpus identity,
  role routes with prompt/schema hashes, the research mapping, the horizon, the
  stopping rule, and the exact release identity permitted to score it. The
  registration participates in its own identity, and `verify_unchanged` refuses an
  edited registration, so a post-hoc change is a new experiment or an explicit
  `COMPROMISED` — never a quiet edit.
- **Automatic selection is impossible**: two routes for one role are refused as
  ambiguous.
- **Maturity is enforced.** A record before its sealed horizon is
  `PENDING_MATURITY`, never scored; a drifted release is `INELIGIBLE`; only
  `MATURED` records are scorable.
- **Populations stay separate.** A record declares its scope, and the type refuses
  a retrospective record with a prospective phase (and vice versa), refuses a
  prospective record that does not declare its maturity state — so a pending record
  cannot be counted as matured by omission — and `ProspectiveTally` **excludes**
  retrospective and ineligible records by construction rather than by a filter a
  caller might forget.

### 3G — Weakness Finding Registry (IC-029 to IC-034, `weakness.py`)

A weakness is not prose. It is a durable, structured record that can be validated
against a realised outcome, attributed to the role and model that discovered it,
and later checked for recurrence.

- **The original finding is immutable.** A finding is never edited. Validation,
  remediation, and post-change evidence are *appended*, so history reads as a
  sequence rather than a state that silently changes meaning. A duplicate
  `finding_id` is refused rather than replaced.
- **The committee cannot certify itself.** `ValidatorKind.COMMITTEE_MODEL` is
  declared and explicitly refused: a model asserting its own finding is not
  evidence. A `VALIDATED` or `REJECTED` validation must reference the realised
  outcome it was judged against, and `PENDING` is the *absence* of a validation
  rather than a kind of one.
- **Taxonomy is versioned and additive.** Every finding records the taxonomy
  version in force when it was raised, on both the finding and its recurrence key,
  so a later taxonomy change cannot retroactively re-label history. `OTHER` must
  carry a specific structured statement — it is an escape hatch, not a way to
  avoid categorising.
- **Recurrence is scope-bound.** A recurrence key carries an explicit scope and
  subject, so two slippage incidents on different instruments are two incidents,
  not one recurring pattern. Recurrence requires at least two occurrences.
- **Unknown stays unknown.** An economic effect that was not legitimately measured
  is `None`, never zero, and `validated_economic_effect` returns `None` for an
  unvalidated finding. Reporting an unmeasured weakness as costing nothing would
  make the weakest evidence look like the strongest.
- **Attribution is recorded.** A finding carries its committee role, provider,
  model, prompt/policy version, case id, paper trade id, and baseline decision
  reference, which is what makes a role's discovery contribution measurable.
- **State is derived, not stored**: the current state comes from appended history
  (latest validation wins; none means `PENDING`), and `summary()` reports every
  state even at zero.

### 3H — Matched portfolio economics (IC-021 to IC-028, `economics.py`)

The committee's contribution is the difference between what a committee-informed
research policy would have produced and what the frozen deterministic baseline
actually produces, on the same opportunities under the same conditions.

- **Matching is a precondition.** Two arms may only be subtracted when they share
  the eligible population, capital, window, execution model, fees, slippage,
  liquidity assumption, coverage, and capital-occupancy semantics. A comparison
  across mismatched conditions is refused, and the error names the differing
  fields, because otherwise the difference measures the conditions.
- **Nothing may be quietly excluded.** Each arm must declare a count for all seven
  populations — filled, no-fill, baseline reject, failed, late, missing committee
  call, and retry. An arm that reports only its successful calls cannot be
  constructed, so survivorship bias cannot enter through omission.
- **Incremental Trading Net** = committee research-policy net − frozen baseline
  net. **Incremental Operating Net** = Incremental Trading Net − attributable
  operating cost.
- **Unknown is never zero.** An unmeasured arm net is `null`, `net_completeness`
  states it, an unknown operating cost makes the operating net unknown, and an
  unknown baseline makes the incremental net unknown. `has_negative_operating_value`
  is `None` rather than healthy when either metric is unknown — which is how
  "positive gross value, negative operating value" stays visible instead of looking
  like a win.
- **False intervention is tracked separately** as a four-way comparison
  (committee-right/baseline-wrong, baseline-right/committee-wrong, both-right,
  both-wrong), because a net-positive total can still hide a harmful pattern. An
  undefined rate is `None`, not zero.
- **Counterfactuals are labelled.** An avoided-loss or missed-gain claim requires a
  preregistered policy and feasible timing — an opinion that arrived after the
  opportunity expired cannot be credited with avoiding it — and carries
  `SIMULATED_NOT_REALISED_CASH`, so it can never be read as realised cash.

### 3I — Integration checkpoint: scheduler vocabulary vs the frozen DI lifecycle (`serialization.py`, `test_opip_committee_scheduler_vocabulary_v1.py`)

The committee scheduler keeps its **own** population-disposition vocabulary rather
than borrowing the frozen Decision Intelligence `RequestState`. That is the right
separation — one is "what happened to this committee case", the other is "where did
this DI request reach in its lifecycle" — but it creates a concrete hazard: **eight
of the ten committee disposition names are string-identical to `RequestState`
values** (`ELIGIBLE`, `SELECTED`, `SKIPPED_BUDGET`, `SKIPPED_CAPACITY`, `EXPIRED`,
`FAILED`, `INVALID`, `COMPLETED`).

The checkpoint therefore closed a real gap rather than asserting the separation:

- **A durable row must declare a record kind.** `kind = "COMMITTEE_SCHEDULE_DISPOSITION"`
  is required, and a row with no kind is refused, because an unlabelled `COMPLETED`
  is ambiguous across the two vocabularies. A row naming a different kind is refused
  rather than reinterpreted, so there is no implicit conversion in either direction.
- **A persisted tally must state every disposition.** A missing state is refused, so
  a reloaded tally cannot be read as one that never had that state.
- **The separation is enforced structurally.** A test parses imports with `ast` and
  asserts the committee plane imports neither `RequestState` nor
  `decision_intelligence.contracts`. Parsing rather than grepping means an
  explanatory comment cannot fail the check while a real import cannot hide.
- **The overlap itself is pinned.** The test asserts the exact shared set, so if the
  overlap changes, the justification for the kind guard is revisited rather than
  silently outliving its reason.
- **Distinct meanings stay distinct.** `UNAVAILABLE` and `LATE` are committee-only
  and are asserted distinct from `FAILED`, `INVALID`, `EXPIRED`, `SKIPPED_BUDGET`,
  and `SKIPPED_CAPACITY`.
- **Reload is lossless and deterministic.** Re-encoding a reloaded record is
  byte-identical, the disposition identity is unchanged, and a timestamp with an
  offset decodes to the same instant.

No mapping between the two vocabularies was added: none is genuinely needed, and
inventing one would create the coupling this checkpoint exists to prevent.

### 3J — Learning lineage (IC-035 to IC-041, `learning.py`)

The chain that turns an observation into governed knowledge:

```text
observation -> validated weakness -> hypothesis -> registered experiment
-> sealed prospective evaluation -> ACCEPTED/REJECTED/INCONCLUSIVE
-> separate human release decision -> post-change effectiveness
```

- **A hypothesis is falsifiable and grounded** (IC-035). It names a suspected
  mechanism, an eligible cohort, a falsifiable prediction, and the expected effect
  direction and size, and it cites the findings it came from. A hypothesis whose
  only grounding is a PENDING, REJECTED, or INCONCLUSIVE finding is refused, because
  the finding itself has not been established.
- **A registered experiment is frozen** (IC-036). Dataset manifest, knowledge
  cutoff, policy/route/model versions, prompt and schema hashes, endpoint, effect
  definition, experiment family, statistical method, stopping rule, and holdout are
  all committed before results. `verify_unchanged` refuses an edit, so a post-hoc
  change becomes a new experiment or an explicit `COMPROMISED` reason — and because
  the compromise participates in the identity, a compromised experiment cannot pass
  as the sealed design it deviated from.
- **A conclusion is ACCEPTED, REJECTED, or INCONCLUSIVE** (IC-037). Inconclusive is
  first class: forcing an unresolved experiment into accepted or rejected would
  manufacture a finding. A resolved conclusion must reference the sealed evaluation
  it came from, and `authorises_policy_change` is always false.
- **Promotion stays human and SHA-bound** (IC-038/039). A `ReleaseRecord` requires
  the conclusion it releases, a **full 40-character lowercase SHA** of the approved
  artifact, and who decided. No link in this module can change a threshold, a route,
  or a weight by itself.
- **Effectiveness is measured, not asserted** (IC-040). An incomparable cohort must
  state why and must not report a before/after metric, because a comparison over
  unlike populations produces a confident wrong answer. Unmeasured deltas stay
  `None`.
- **Release SHA compatibility** (IC-041) is carried by the release record's exact
  SHA, so the artifact a change was measured on is always identifiable.

### 3K — Committee Trust Report and investment (IC-043, IC-044, `trust.py`)

The durable authoritative summary Cockpit v2 will consume, so a UI displays
authoritative facts rather than computing trust itself.

- **Counts are derived, never restated.** Population comes from the scheduler's
  tally; economics from matched arms; weakness counts from the registry. The report
  cannot disagree with its sources because it does not duplicate them.
- **Unknown stays unknown.** An absent comparison, unknown operating cost, or
  unmeasured net yields `None` with a stated reason, never zero. An empty report is
  stage T0, not a clean one.
- **Trust stage follows gates, not outcomes.** Six gates (evidence integrity,
  coverage, baseline comparison, economics, operating-cost-known, weakness
  precision) are computed from evidence, and each blocked gate states why. A stage
  cannot advance on an unknown key metric, on integrity violations, or on
  insufficient matured coverage — a stage reachable by an unmeasured result would be
  meaningless.
- **Investment is reported** (IC-044): attributable cost, token totals, call and
  failure counts, with unknown cost remaining unknown rather than free.
- **No stage grants influence.** The highest stage this module computes is a
  *review* stage; trading or paper influence requires a separate human decision
  outside this module.

### 3M — Per-attempt latency (IC-012, `role_router.py`, `role_execution.py`, `serialization.py`, `trust.py`)

Latency is now measured rather than absent, under rules that keep it honest:

- **Measured, not inferred.** The router takes an injected `monotonic` clock
  (defaulting to `time.monotonic`) and measures the duration of each attempt. Wall
  clock timestamps are deliberately not used: an NTP adjustment could make a slow
  call look instant, and the wall clock is already the identity's business.
- **Recorded on every invoked attempt**: success, retryable failure, invalid
  response, served-identity mismatch, and each attempt of a failover. A timeout is
  exactly the latency an operator needs to see.
- **Null when nothing ran.** An unavailable seat, an unconfigured adapter, and a
  dead-letter outcome report no duration rather than zero, because zero would read
  as an instantaneous call.
- **Aggregation uses measured observations only.** `total_latency_micros` and
  `mean_latency_micros` are `None` when nothing was measured, and
  `latency_sample_complete` reports a partial sample as partial. A zero-latency
  placeholder can neither pad nor deflate an average.
- **Durable.** A role-result codec round-trips the value; a legacy row written
  before the field existed decodes to `None` and keeps its original identity; a
  corrupted value is refused on read. Latency participates in the role-result
  identity, since a different duration is a different observation.
- **Observable.** The Trust Report exposes mean latency and the measured/unmeasured
  split through its investment and maturity views.

Note on "repair": repair is applied by the conformance harness, not by the router.
The router admits only a payload that already satisfies the contract, so a
form-repair case appears there as a recorded `INVALID` attempt — and its duration is
still measured, which is what keeps a repair round visible in latency.

### 3N — Contribution to the profitability loop

This slice supplies the **role-attribution** substrate the profitability loop
requires. It is not yet wired to a live case pipeline, so the loop above is not
yet closed end-to-end; what it changes is that a role opinion can now be
attributed to a governed route rather than to an ambient model.

### 2G — Release-drift refusal on prospective evaluation (`prospective.py`, `store.py`)

Prospective evidence is only comparable when the release that scores it is the
release that sealed it. A drifted worker must not be able to score an outcome
for a prediction it did not seal, so the release identity is part of the
contract rather than an ambient property:

- `SealedPrediction.release_sha` is **required**, is committed at T0 with the
  horizon, participates in `prediction_id`, and is persisted — so a bound
  prediction round-trips and a changed release changes the prediction identity.
- `evaluate_prospective` takes the scoring release and refuses a mismatch with
  the typed `ProspectiveReleaseDriftError`. A drifted outcome is never scored.
- `admit_prospective_outcome` is the governed boundary. On drift it returns a
  `ProspectiveIneligibility` carrying the explicit `RELEASE_DRIFT` reason, the
  expected and observed release identities, and the detection time — rather than
  raising into a generic failure bucket.
- An ineligibility is **not** a `ProspectiveEvaluation`. It exposes no
  evaluation identity and no seat scores, so it cannot enter prospective trust
  metrics or economic attribution by construction.
- Ineligibilities are persisted durably in `prospective_ineligible.jsonl`,
  idempotent by content-derived identity, and readable after a restart.

## Calibration prohibition

A seat's `confidence` is an **ordinal 0–100 self-report**. It is never converted
into a probability. Brier score, log loss, and calibration are computed only
from an explicit `ProbabilityForecast` that a case type genuinely defines;
otherwise they are reported as not applicable. Deriving a probability from an
ordinal score would be exactly the conflation the repository's statistical
protocol forbids.

## Configuration

| Variable | Values | Default | Meaning |
| --- | --- | --- | --- |
| `OPIP_COMMITTEE_MODE` | `off`, `shadow` | `off` | `off` disables the plane; `shadow` is the only value that permits committee work. Any other value fails Settings parsing. |
| `OPIP_COMMITTEE_MAX_ESTIMATED_COST_MICROUNITS` | integer ≥ 0 | `0` | Optional per-case cost ceiling. `0` means no declared ceiling. A seat is skipped once the ceiling would be exceeded, **and also when its cost cannot be bounded at all** — a declared ceiling that cannot be enforced would permit exactly the spend it exists to prevent. |
| `OPIP_COMMITTEE_PRICES` | `provider:model=in/out;...` | unset | Prices in microunits per million tokens. Absent or unmatched prices yield `UNKNOWN` cost. |

**No credential is read, constructed, or stored by this plane.** Provider
adapters receive an injected transport; whoever owns credentials and network
policy supplies it. Adding a live egress path is an architecture decision that
belongs outside this module.

## Failure behaviour and performance isolation

Committee failure must never impair the trading runtime. A provider timeout, an
auth failure, a rate limit, a malformed response, an unavailable seat, or a
rejection at the persistence layer all become typed, recorded dispositions. No
committee failure can stop the scheduler, skip a risk control, or mutate
canonical state.

No synchronous model call exists in a latency-sensitive path: the plane is not
imported by `app/services`, `app/jobs`, `app/api`, or the streaming/execution
roots, and the runtime takes every timestamp as an argument rather than reading a
clock.

## Evidence and durability

Committee evidence is written through the repository's shared bounded JSONL
archive, inheriting its durability semantics: an fsynced HOT append, verified
gzip archives before compaction, and a quarantined truncated tail rather than a
silently dropped row. Nothing is overwritten: a committed observation is
immutable, a logical re-execution is acknowledged as a duplicate, and a
divergent replay is reported rather than applied. Every retry attempt is recorded
before the next attempt starts, so the audit trail keeps each try rather than
only the last one. If a record-id index is lost, it is rebuilt from the durable
log rather than treated as empty, so a re-delivered observation is still
acknowledged as a duplicate instead of being appended a second time.

Streams live under `/app/data/opip/committee/`:

| Stream | Contents |
| --- | --- |
| `call_outcomes.jsonl` | One immutable record per provider call, including failed and unavailable seats. |
| `case_outcomes.jsonl` | Aggregated committee case outcomes. |
| `evaluations.jsonl` | Bake-off reports. |
| `prospective.jsonl` | Sealed predictions, outcome observations, prospective evaluations. |
| `attributions.jsonl` | Attribution reports. |
| `call_rejections.jsonl` | Refused divergent replays: durable evidence that a replay was rejected, kept out of the call stream so it can never be read as an observation or as spend. |
| `prospective_ineligible.jsonl` | Ineligible prospective dispositions (e.g. `RELEASE_DRIFT`), kept out of the evaluation stream so a drifted case cannot enter trust or economic metrics. |

## Known limitations

- **No live provider integration is wired.** Adapters exist and are exercised by
  deterministic fakes; a real provider transport, with credentials and a network
  policy, is a separate approved change.
- **The committee is not scheduled.** Deliberately: a job that activates it would
  need its own review, and the plane currently has no production consumer.
- **`case_type` support is `MARKET_OPPORTUNITY` only.** Other declared case types
  lack a genuine probabilistic forecast definition, so probabilistic metrics are
  reported as not applicable rather than approximated.
- **No canonical-writer integration.** Committee evidence stays in its own
  stream because the frozen DI import boundary forbids runtime roots from
  reaching the DI plane.
- **The 2E canonical binding is a lineage reference only, and it is not yet
  populated for a live decision.** A governed bridge that resolves a real
  canonical `decision_id`/`episode_id` onto a committee case belongs in
  `app/opip/canonical/` and remains a separate, future, human-approved change.
- **No promotion mechanism exists here, by design.** Any promotion requires a
  separate, human-governed gate with its own reviewed SHA.
- **`MIN_ATTRIBUTION_SAMPLES` / `MIN_EVALUATION_SAMPLES` (30) are conventions,
  not inferential guarantees.** They prevent anecdotal claims; they do not make a
  passing sample statistically conclusive.

## Contributor caution: the frozen boundary token scan

`tests/test_opip_decision_safety_v1.py::test_no_ml_dependency_is_introduced`
lowercases every file under `app/opip/**` and asserts that the substrings
`xgboost`, `lightgbm`, `shap`, `sklearn`, `scikit`, `torch`, and `tensorflow`
appear **nowhere**. Ordinary English words trip it — most often
"authority-*shap*ed" or "action-*shap*ed" in a docstring, which contains `shap`.
This has broken the suite three times during Module 2 development.

Write **"authority-bearing"** / **"action-bearing"** instead, and run
`tests/test_opip_decision_safety_v1.py` after any change that touches prose under
`app/opip/`. The scan is a frozen contract and must not be weakened to
accommodate wording.

## IC-001 to IC-045 reconciliation

Status vocabulary, applied strictly:

- **GREEN** - the requirement is implemented and its lifecycle is proved by tests at
  this revision.
- **PARTIAL** - the contract, lifecycle, and tests exist, but the requirement is not
  yet exercised end to end against real data or a real deployment.
- **MISSING** - not implemented.
- **OUT_OF_SCOPE** - deliberately not built in this revision, with the reason.

A module or class existing is **not** sufficient for GREEN; the evidence column
names the lifecycle behaviour that is actually tested.

| IC | Requirement | Status | Evidence at this revision |
| --- | --- | --- | --- |
| IC-001 | Zero trading/order/risk authority | **GREEN** | Structural tests assert the plane cannot import exchange, order, registry, or notification modules; `AUTHORITATIVE` and `CAN_PLACE_ORDERS` are False; no runtime root imports the plane. |
| IC-002 | Dark-by-default mode enforcement | **GREEN** | `Settings` defaults to `off`; `CommitteeShadowSettings` defaults to `off`; an unrecognised mode resolves to `off`; `run_case` raises unless enabled; scheduler cycle does not run when disabled. |
| IC-003 | Point-in-time evidence with explicit cutoff | **GREEN** | `EvidenceSnapshot` carries the cutoff; `seal_prediction` derives the cutoff from the authenticated snapshot rather than a caller argument. |
| IC-004 | Future/hindsight exclusion | **GREEN** | `HindsightLeakageError` on an overlapping window, an outcome observed before sealing, or a horizon mismatch; adversarial tests cover each. |
| IC-005 | DecisionContext to Committee linkage | **PARTIAL** | `CanonicalDecisionBinding` is opaque by reference, participates in case/case-outcome identity, is persisted, and refuses reattribution and unbinding, with legacy-identity tests. Not yet populated from a real canonical decision, which needs the governed DI bridge. |
| IC-006 | Explicit role-based Committee | **GREEN** | Seven roles; six required and enforced by `validate_complete`; the optional role must report `UNKNOWN` rather than invent a view; a provider family is asserted never to be a role. |
| IC-007 | Provider-neutral adapter boundary | **GREEN** | `CommitteeProvider` plus injected `ProviderTransport`; the router never branches on vendor; an absent adapter is `UNAVAILABLE` rather than substituted. |
| IC-008 | Real governed provider transports | **MISSING** | Adapters exist and are exercised by deterministic fakes; a real transport with credentials and network policy is a separate approved change and is documented as such. |
| IC-009 | Versioned Model Registry | **GREEN** | Versioned registry of role routes with prompt/schema hashes, owner, approval state, effective and review dates, reasoning mode, and budget limits. |
| IC-010 | Primary plus at most one approved fallback | **GREEN** | Two routes for one role are refused as ambiguous; the fallback shares the primary's request, deadline, and reservations; an unusable fallback is dropped, never substituted; only `APPROVED` routes. |
| IC-011 | Deadline/token/cost/concurrency budgets | **GREEN** | `RoleBudget` validates and enforces deadline and cost ceilings, bounds concurrency, and refuses an unknown cost rather than treating it as free. |
| IC-012 | Per-attempt latency/token/model/cost accounting | **GREEN** | Call outcomes persist served provider/model, tokens, cost, and completeness. `RoleAttempt` and `RoleSeatResult` now carry a latency measured with an injected monotonic clock, recorded on every invoked attempt including failures, invalid responses, identity mismatches, and fallback attempts, and left null when nothing was invoked. Aggregation (`total_latency_micros`, `mean_latency_micros`, `latency_sample_complete`) uses measured observations only and reports unknown rather than zero; latency is durable through a role-result codec with legacy rows decoding to null, and is surfaced in the Trust Report investment and maturity views. Tested for success, failure, invalid, unavailable, missing-adapter, failover, repair-path, aggregation, corruption, and legacy reload. |
| IC-013 | Strict structured-response validation | **GREEN** | Non-JSON, fenced, undeclared, out-of-range, non-finite, and missing-field responses are refused; one bounded repair path only. |
| IC-014 | Evidence-reference validation | **GREEN** | A citation outside the screened manifest is refused, including after whitespace normalisation; tested against path-like and URL-like references. |
| IC-015 | Immutable/durable evidence store | **GREEN** | Append-only bounded JSONL with durable idempotency ledger; sidecars are caches reconciled against the authoritative log; divergent replays are refused with durable rejection records. |
| IC-016 | Durable scheduler from committed evidence | **GREEN** | Deterministic schedule key; redelivery returns the existing disposition and executes nothing; restart resumes from the checkpoint; each disposition is persisted before the cycle continues; executor faults are contained. |
| IC-017 | Population accounting | **GREEN** | Ten dispositions; `PopulationTally` reports every state even at zero and the cycle's considered count is checked against the accounted total; `SKIPPED_BUDGET` and `SKIPPED_CAPACITY` remain distinct from `UNAVAILABLE`, `FAILED`, `INVALID`, `EXPIRED`, and `LATE`. |
| IC-018 | Phase-A conformance/security bake-off | **GREEN** | 542 deterministic fixtures exceed the 500 minimum; all four required directions are asserted at zero (unauthorised action fields, citations outside the manifest, embedded instructions, unhandled malformed payloads); repair is bounded to one registered strategy. |
| IC-019 | Retrospective role/model bake-off | **PARTIAL** | Frozen corpus identity, required diagnostic-class breadth, a 120-case minimum, and answer-hiding are enforced, and every result carries `RESEARCH_ONLY_NOT_PORTFOLIO_EVIDENCE`. Not yet run against a real retrospective corpus. |
| IC-020 | Sealed prospective experiment | **PARTIAL** | Registration freezes corpus, routes, mapping, horizon, stopping rule, and exact release identity; edits fail closed; maturity and release drift are enforced; populations cannot mix. Not yet running against live cases. |
| IC-021 | Deterministic O'Pip baseline comparison | **PARTIAL** | The bake-off compares a deterministic baseline arm; economics requires a `DETERMINISTIC_BASELINE` arm under matched conditions. Not yet populated from production baseline results. |
| IC-022 | Cash/no-trade comparator | **PARTIAL** | A `CASH_NO_TRADE` arm is a declared type, must share conditions, and is carried into the report. Not yet populated from real data. |
| IC-023 | Disagreement taxonomy | **GREEN** | The attribution matrix separates unanimous agreement, confidence, evidence, and assumption disagreement, single dissent, split, insufficient evidence, provider failure, schema-invalid, and unavailability. |
| IC-024 | Incremental-information attribution | **GREEN** | Independent incremental correctness requires disagreeing with the baseline, being right, and the baseline being wrong; paired-population restriction is enforced. |
| IC-025 | Matched portfolio economic comparison | **GREEN** | Two arms may only be subtracted under identical conditions, and the error names the differing fields. |
| IC-026 | Incremental Trading Net | **GREEN** | Committee net minus frozen baseline net; unknown when either side is unknown. |
| IC-027 | Incremental Operating Net | **GREEN** | Incremental trading net minus attributable operating cost; unknown cost leaves it unknown; negative operating value with positive gross value is surfaced. |
| IC-028 | Avoided-loss / missed-gain semantics | **GREEN** | Requires a preregistered policy and feasible timing, is labelled `SIMULATED_NOT_REALISED_CASH`, and cannot be relabelled as realised. |
| IC-029 | Durable Weakness Finding Registry | **GREEN** | Findings are immutable; duplicate ids are refused; validation and follow-up records are appended rather than folded in; state is derived from appended history. |
| IC-030 | Controlled weakness taxonomy | **GREEN** | Seventeen categories plus a constrained `OTHER`; the taxonomy version is recorded on both the finding and its recurrence key so history cannot be re-labelled. |
| IC-031 | Weakness outcome validation | **GREEN** | `PENDING`, `VALIDATED`, `REJECTED`, `INCONCLUSIVE`; a resolved validation must reference realised outcome evidence; the committee plane is refused as a validator. |
| IC-032 | Weakness recurrence tracking | **GREEN** | Recurrence keys are scope- and subject-bound, so unrelated incidents are never merged; recurrence requires at least two occurrences. |
| IC-033 | Economic impact of validated weakness | **GREEN** | Reported only for a validated finding and only when measured; an unmeasured effect stays null rather than zero. |
| IC-034 | Role/model attribution to weakness | **GREEN** | Findings carry committee role, provider, model, prompt/policy version, case id, paper trade id, and baseline decision reference. |
| IC-035 | Hypothesis registry | **GREEN** | Hypothesis records mechanism, cohort, falsifiable prediction, expected effect, and source findings; grounding in a `VALIDATED` finding is enforced. |
| IC-036 | Registered experiment | **GREEN** | Every axis is frozen and required; post-sealing edits fail closed; compromise is explicit, reasoned, and identity-bearing. |
| IC-037 | ACCEPTED / REJECTED / INCONCLUSIVE | **GREEN** | Exactly three values; a resolved conclusion must reference its sealed evaluation; inconclusive is first class; `authorises_policy_change` is always false. |
| IC-038 | Human-governed promotion only | **GREEN** | A release requires the conclusion, an approving identity, and a full 40-character lowercase SHA; no module in the plane can change a threshold, route, or weight. |
| IC-039 | Release/artifact linkage | **GREEN** | `ReleaseRecord` links conclusion to released SHA and artifact reference; the chain refuses a release without a conclusion. |
| IC-040 | Post-change effectiveness | **GREEN** | Reports before/after windows, metric delta, and recurrence delta; an incomparable cohort must state why and must not report a metric. |
| IC-041 | Exact release SHA binding | **PARTIAL** | Releases carry an exact SHA, and prospective evaluation fails closed on release drift with an explicit ineligible disposition. Not yet verified against a deployed release SHA. |
| IC-042 | Isolated shadow deployment architecture | **MISSING** | Not deployed, by instruction. The contracts it depends on (off-by-default mode, advisory-only persistence, contained failure) exist and are tested. |
| IC-043 | Learning observability | **PARTIAL** | Prospective pending counters and a six-gate trust report with explicit insufficiency reasons exist. No deployed dashboard, which is out of scope for this revision. |
| IC-044 | Committee investment measurement | **GREEN** | Attributable cost, token totals, call and failure counts, with unknown cost remaining unknown and a failure rate that is null when nothing was called. |
| IC-045 | No credentials or executable tools to models | **GREEN** | Fail-closed outbound screening by prohibited key name, credential-formed value, and environment-dump field; the plane exposes no tool, shell, or browsing surface to a model. |

### Summary

| Status | Count |
| --- | --- |
| GREEN | 36 |
| PARTIAL | 7 |
| MISSING | 2 |
| OUT_OF_SCOPE | 0 |

### What blocks READY_TO_FREEZE

The two MISSING requirements are the honest blockers, and both are deliberate:

1. **IC-008 real governed provider transports** requires credentials and a network
   policy. That is an owner-authorised change, and no credential may be placed on
   this plane by an agent.
2. **IC-042 isolated shadow deployment** requires a deployment decision that this
   mandate explicitly withholds.

Neither is a correctness defect. The seven PARTIAL requirements are contracts with
proven lifecycles that have not yet been exercised against real evidence or a real
release; every one of them is blocked on live providers, live cases, or a deployed
release rather than on missing implementation:

- **IC-005** the canonical binding exists and is enforced, but is not yet populated
  from a real canonical decision (needs the governed DI bridge).
- **IC-019**, **IC-020** the retrospective and prospective infrastructures are
  complete and tested, but have not been run against a real corpus or live cases.
- **IC-021**, **IC-022** the baseline and cash comparator arms are declared, matched,
  and carried into the report, but are not yet populated from production results.
- **IC-041** releases carry an exact SHA and prospective evaluation fails closed on
  drift, but neither has been verified against a deployed release.
- **IC-043** pending counters and the six-gate trust report exist; there is no
  deployed dashboard, which is out of scope for this revision.

Per the reconciliation rule, "awaiting prospective data" is classified above as
*implementation complete but awaiting real evidence*, not as an implementation
defect.

### Three-way classification of the remaining requirements

| Class | Requirements | Meaning |
| --- | --- | --- |
| **1. Implementation complete (GREEN)** | IC-001 to IC-004, IC-006, IC-007, IC-009 to IC-020, IC-023 to IC-040, IC-044, IC-045 | Implemented with lifecycle evidence and tests at this revision. |
| **2. Implementation complete, awaiting real evidence** | IC-005, IC-019, IC-020, IC-021, IC-022, IC-041, IC-043 | The contract, the lifecycle, and the tests exist and pass. Each needs live providers, live cases, a real corpus, or a deployed release before it can be *exercised* end to end. Not an implementation defect, and not repairable by more agent work. |
| **3. Requires OWNER-authorised external action** | IC-008, IC-042 | Cannot be completed without an owner decision: real provider transports with credentials and egress (IC-008), and a deployed isolated shadow worker (IC-042). |

The concrete plans for class 3 are in
[`MODULE2_EXTERNAL_ACTION_PROPOSALS.md`](MODULE2_EXTERNAL_ACTION_PROPOSALS.md):
providers and exact model IDs, transport shape, credential placement, egress, cost and
timeout limits, shadow-only enforcement, and the no-tools/no-trading-credential
guarantee for IC-008; host and service choice, isolation, read-only evidence, default
OFF, resource limits, network policy, rollback path, observability, and release-SHA
binding for IC-042.

**Nothing in either proposal has been activated.** No credential was created, read, or
moved; no process was deployed; no provider was contacted.
