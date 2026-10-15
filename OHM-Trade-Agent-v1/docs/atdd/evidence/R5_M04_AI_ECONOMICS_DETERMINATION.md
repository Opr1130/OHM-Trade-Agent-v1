# AI economics determination: what attributable cost evidence exists, and what does not

Increment: `ATDD-R5-M04-ai-economics-evidence`.
Status: **DETERMINATION. Nothing in this document is implemented.** No producer,
registry, store, scheduler, publication boundary, route or price was added.

This document answers Workstream D and Workstream E of Mission 04. The verdict for a
complete **canonical** attributable cost basis is **`INSUFFICIENT_EVIDENCE`**, and no
favourable or unfavourable AI net value is estimated anywhere.

## 1. What exists, measured and durable

`app/opip/committee/contracts.py::ProviderCallOutcome` is the per-invocation record. It
carries, per attempt:

| Fact | Field |
| --- | --- |
| provider identity | `provider_family`, `reported_provider` |
| model identity | `requested_model`, `reported_model` |
| attempt number | `attempt` |
| input tokens | `input_tokens` |
| output tokens | `output_tokens` |
| estimated cost | `estimated_cost_microunits` |
| runtime charge | `charge_microunits` (the per-attempt maximum of the pre-flight estimate and the reported cost) |
| cost completeness | `cost_completeness` (`COMPLETE`, `INCOMPLETE`, `UNKNOWN`) |
| latency | `latency_micros`, `request_at`, `response_at` |
| failure | `failure_class`, `detail`, `raw_response_ref` |

The identity is content-derived over all of the above, so a retry that re-delivers the
same logical observation is recorded as `DUPLICATE_OK` and never becomes a second
opinion; and because cost and latency participate in the identity, a changed cost cannot
hide behind the same artifact identity.

Matched-policy incremental economic value is implemented and proven:
`build_incremental_economics` requires matched conditions and named arms
(`DETERMINISTIC_BASELINE`, `COMMITTEE_RESEARCH_POLICY`, optional `CASH_NO_TRADE`), and
`test_an_unknown_operating_cost_makes_the_operating_net_unknown` proves that an unknown
operating cost yields an **unknown** operating net rather than a zero-cost one.

Per-attempt attribution is now proven too: a failover's attributed spend is the
answering attempt's cost alone, the timed-out attempt contributes nothing, and the
aggregate completeness is `UNKNOWN` because one attempt's cost was never reported. An
unreported cost is recorded as **null**, never as zero and never as the pre-flight
estimate.

## 2. What is declared rather than measured, and where

The committee plane's own accounting refuses to credit prompt-cache savings.
`app/opip/committee/registry.py` carries:

```text
CACHE_SAVINGS_CREDITED = False
```

with the recorded reason that cache usage is not measured there, so Committee cost is
accounted at **uncached rates** and crediting an unmeasured saving would be inventing one.

**This is a per-plane choice, not a repository-wide absence.** The decision-intelligence
plane's `ModelInvocation` contract does carry `cached_tokens`, alongside
`input_tokens` and `output_tokens`. So cache usage **is** measured and captured on the
canonical invocation record; only the Committee's own bake-off refuses to credit it.

## 3. What does not exist

**A canonical attributable cost basis as a whole.** Billed cost and estimated cost both
exist as distinct fields and are read raw and never totalled:

```text
app/opip/decision_intelligence/contracts.py::ModelInvocation
    billed_cost_microunits: int | None
    estimated_cost_microunits: int | None
    cached_tokens: int | None
    price_version: str
    currency: str
    reconciliation_status: str
    cost_completeness: str
```

and the contract **fails closed** when an invocation has no cost at all but is not marked
`UNKNOWN` or `INCOMPLETE`. `app/opip/decision_intelligence/evidence_reader.py`
reconstructs invocations from it, and `test_invocation_cost_evidence_is_raw_and_never_totalled`
proves the cost evidence is never aggregated into a number the raw rows do not support.

What does **not** exist is a single canonical attributable cost basis spanning the whole
AI surface. The Committee's own per-attempt evidence is written to the committee plane's
bounded JSONL archive (`app/opip/committee/store.py`) rather than through the single
canonical writer, so it is durable but not canonical, and the F10 read plane therefore
cannot present it (section 4).

**Correction.** This determination's first pass searched only the committee package and
concluded that no billed cost existed anywhere and that cache usage was unmeasured. Both
were wrong: both are present in the decision-intelligence plane. The correction is
recorded rather than quietly edited, because the mistake was exactly the kind this
mission exists to catch — a conclusion drawn from a partial search.


## 4. Workstream E: why no read integration was implemented

The read plane already states the same verdict. `app/opip/cockpit/semantic.py` defines
`REASON_NO_CANONICAL_AI_COST = "NO_CANONICAL_AI_COST_EVIDENCE"`, and
`semantic_pages.py` renders the panel *"attributable AI cost by provider, model and
attempt"* against `AC-073` with exactly that reason. The census records `AI usage | one
provider invocation attempt | BLOCKED_BY_UPSTREAM_EVIDENCE`, and
`app/opip/cockpit/ai_cost.py` is a declared absence.

Closing that would require a **new producer or publication boundary** - publishing the
committee-local archive onto the canonical plane, or declaring a new read-model fact
family plus a frozen panel decision. Mission 04 forbids exactly that without owner
authorization, so none was created and no read path changed.

## 5. The decisions this determination requests

```text
DECISION: M04-E1-CANONICALISE-COMMITTEE-INVOCATION-EVIDENCE | producer=app.opip.committee (ProviderCallOutcome, currently a committee-local bounded JSONL archive) | consumer=app.opip.cockpit.semantic.SemanticReadService | schema=new-canonical-event-family-with-idempotency-key-and-watermark-stream-plus-a-declared-read-model-fact-and-a-frozen-panel-decision | failure=withhold-the-value-and-publish-the-reason-never-a-zero | security=no-new-external-surface-and-no-new-route | migration=none-additive-declaration-over-existing-committee-rows-or-an-explicit-exclusion
DECISION: M04-E2-UNIFY-THE-ATTRIBUTABLE-COST-BASIS | producer=the-existing-invocation-records-billed_cost_microunits-estimated_cost_microunits-cached_tokens-price_version-currency-reconciliation_status | consumer=a-single-canonical-attributable-cost-basis | schema=no-new-capture-field-is-needed-only-canonicalisation-of-what-is-already-recorded | failure=an-absent-figure-stays-null-and-completeness-stays-INCOMPLETE-or-UNKNOWN | security=no-new-external-surface-and-no-new-provider-billing-credential | migration=none-existing-invocations-keep-their-recorded-identity-and-supersession-lineage
DECISION: M04-E3-SETTLE-THE-COMMITTEE-CACHE-ACCOUNTING-POLICY | producer=the-already-captured-cached_tokens-on-the-invocation-record | consumer=committee-cost-accounting | schema=a-reviewed-change-to-CACHE_SAVINGS_CREDITED-alone-no-new-field-is-needed | failure=until-settled-committee-cost-stays-at-uncached-rates-exactly-as-it-does-today | security=none | migration=none-historical-executions-stay-bound-to-the-coefficients-they-ran-under
```

`migration=none` on all three means the change adds evidence and reads existing rows; it
rewrites, replays and truncates nothing, and existing `ProviderCallOutcome` identities
are preserved.

## 6. What this determination refuses to say

- It does not estimate an AI net value. With no billed cost and no canonical basis, the
  honest verdict is `INSUFFICIENT_EVIDENCE`, and the read plane already publishes exactly
  that reason.
- It does not treat Cline or DeepSeek engineering-development spend as Committee
  trading-operating expense. They are different cost domains and are never summed.
- It does not treat the committee-local archive as canonical evidence, and it does not
  treat its durability as publication.
- It does not claim the Committee has any authority, and it enabled no provider call,
  timer, credential or endpoint.

