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
  hallucinated evidence references are refused.
- Fail-closed secret screening of everything permitted to leave the process.
- Idempotent, bounded execution. A committed logical observation is never
  re-queried, and a divergent replay fails explicitly instead of overwriting
  sealed evidence.

### 2B — Model bake-off / evaluation (`metrics.py`, `pricing.py`, `evaluation.py`)

- Arms compared on identical cases: individual models, a documented committee
  research signal, the existing deterministic baseline, and an always-positive
  null baseline.
- Metrics with **explicit applicability**. A metric that cannot be computed is
  reported as not applicable with a reason code, never as zero.
- Cost accounting is configuration: prices are microunits per million tokens,
  an unconfigured price yields `UNKNOWN` (never zero), and a malformed price
  specification raises rather than being ignored.
- An arm below the minimum scored-case count is `INSUFFICIENT_SAMPLE` and must
  not be read as a winner.
- Proper scoring rules and calibration exist and are covered by tests, but they
  are not applied to ordinal confidence (see below).

### 2C — Prospective shadow experiment (`prospective.py`)

Strict three-point protocol: **T0** seal → **T1** outcome → **T2** compare.

- A sealed prediction records the content hash of every sealed opinion and the
  cutoff. Verification recomputes those hashes from the recorded T0 evidence, so
  a later rewrite of a sealed opinion fails closed.
- An outcome may only be joined when its whole measurement window lies after the
  cutoff. An overlapping window, an outcome observed before the cutoff, or an
  outcome observed before sealing raise `HindsightLeakageError`.
- A retrospective case cannot be relabelled prospective.
- Provisional outcome evidence must state why it is not final and can never
  carry a final seat score.
- Retrospective and prospective metrics are never mixed.
- Seat accounting is exhaustive: a seat is scored, abstaining, unavailable, or
  explicitly unscored because the outcome carried no direction.

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
| `OPIP_COMMITTEE_MAX_ESTIMATED_COST_MICROUNITS` | integer ≥ 0 | `0` | Optional per-case cost ceiling. `0` means no declared ceiling. A seat is skipped once the ceiling would be exceeded. |
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
divergent replay is reported rather than applied.

Streams live under `/app/data/opip/committee/`:

| Stream | Contents |
| --- | --- |
| `call_outcomes.jsonl` | One immutable record per provider call, including failed and unavailable seats. |
| `case_outcomes.jsonl` | Aggregated committee case outcomes. |
| `evaluations.jsonl` | Bake-off reports. |
| `prospective.jsonl` | Sealed predictions, outcome observations, prospective evaluations. |
| `attributions.jsonl` | Attribution reports. |

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
- **No promotion mechanism exists here, by design.** Any promotion requires a
  separate, human-governed gate with its own reviewed SHA.
- **`MIN_ATTRIBUTION_SAMPLES` / `MIN_EVALUATION_SAMPLES` (30) are conventions,
  not inferential guarantees.** They prevent anecdotal claims; they do not make a
  passing sample statistically conclusive.
