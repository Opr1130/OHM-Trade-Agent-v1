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

### 3E — Contribution to the profitability loop

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
