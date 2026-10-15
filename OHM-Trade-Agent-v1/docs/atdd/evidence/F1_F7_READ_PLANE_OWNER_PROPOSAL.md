# Owner-decision proposal: exposing canonical pipeline evidence to the analytics read plane

Increment: `ATDD-R5-M02-source-of-truth-audit`.
Status: **PROPOSAL. Nothing in this document is implemented.** No producer,
publication mechanism, schema, job, route, panel or trust decision was added by
this mission. Each item below needs an owner decision before any increment may
implement it.

This document exists because Mission 02 asked which facts already exist in
canonical records but are not exposed through the approved analytics read plane.
The answer is not the one the dashboard census assumed, so it is recorded exactly.

## 0. Decisions requested, in machine-readable form

Every line names the same six fields, and the acceptance test fails if any is
missing. `migration=none-additive-declaration` means the change adds a declared
fact and reads existing canonical rows; it rewrites, replays and truncates nothing.

```text
DECISION: M02-D1-EXPOSE-FEATURE-BUS-EVIDENCE | producer=app.opip.canonical.writer (FEATURE_BUS_EVENT_TYPES) | consumer=app.opip.cockpit.semantic.SemanticReadService | schema=new-declared-read-model-fact-and-grain-plus-frozen-panel-decision | failure=withhold-value-and-report-the-reason-never-a-zero | security=no-new-external-surface-and-no-new-route | migration=none-additive-declaration-over-existing-canonical-rows
DECISION: M02-D2-EXPOSE-F5-FEASIBILITY-EVIDENCE | producer=app.opip.fev_evidence_event (FEASIBILITY_EVIDENCE_RECORDED) | consumer=app.opip.cockpit.semantic.SemanticReadService | schema=new-declared-read-model-fact-and-grain-plus-frozen-panel-decision | failure=withhold-value-and-report-the-reason-never-a-zero | security=no-new-external-surface-and-no-new-route | migration=none-additive-declaration-over-existing-canonical-rows
DECISION: M02-D3-NEW-CANONICAL-PRODUCERS-FOR-CANDIDATE-DECISION-FAMILIES | producer=to-be-named-single-canonical-writer-increment | consumer=app.opip.cockpit.semantic.SemanticReadService | schema=new-canonical-event-family-with-idempotency-key-and-watermark-stream | failure=fail-closed-on-writer-unavailability-never-a-synthetic-row | security=new-writer-responsibility-under-the-existing-single-writer-invariant | migration=backfill-of-existing-trading-host-file-wal-rows-or-an-explicit-exclusion
DECISION: M02-D4-CLAIM-OBJECT-ON-THE-ENGINE-OUTCOME | producer=app.opip.events.opportunity_engine.ContinuousOpportunityEngine | consumer=the-F5-F6-F7-spine-lineage-binder | schema=additive-field-on-EvaluationOutcome | failure=refuse-to-build-a-lineage-without-a-committed-claim | security=no-authority-change | migration=none-no-existing-consumer-reads-the-field
NOT-A-DECISION: M02-D5-F4-EPISODE-IDENTITY | outcome=examined-and-found-consistent | producer=app.opip.contracts.opportunity.OpportunityLifecyclePolicy | consumer=both-F4-entry-points | schema=policy-fails-closed-on-unratified-episode-schema-lifecycle-and-policy-versions | failure=refuses-an-unratified-policy-instead-of-forking-lineage | security=no-change-required | migration=none-this-mission-changes-nothing
```

## 1. The two different kinds of gap

The F10 census records most pipeline families as `BLOCKED_BY_UPSTREAM_EVIDENCE`.
That phrasing hides two materially different situations, and the difference
decides who can act:

- **Gap type 1 - canonical but undeclared.** The family is durably produced by
  the canonical writer and is point-in-time capable; the read plane declares no
  fact for it. This is an *exposure* decision, and it is the owner's to make
  because it changes the analytics read surface.
- **Gap type 2 - no canonical producer.** No canonical record of the family
  exists at all. This is a *producer* decision and a new canonical writer
  responsibility, which is a higher-impact change again.

An exposure gap is not the same as a missing fact, and treating them as one is
how a reader concludes that the pipeline holds no evidence when it holds some.

## 2. Type 1: canonical and undeclared (exposure decisions)

Verified in this mission: `app/opip/contracts/events.py` declares six feature-bus
event types and `app/opip/fev_evidence_event.py` declares a seventh, each with an
idempotency key, a canonical stream and its own watermark stream, and the
canonical writer routes them (`app/opip/canonical/writer.py`). The read plane's
declared facts (`app/opip/cockpit/semantic.py`) name only ledger, paper-trade and
projection-posture facts - nothing from these families.

| FAMILY | CANONICAL EVENT TYPE | WHAT IT WOULD ANSWER | WHY IT IS NOT FREE |
| --- | --- | --- | --- |
| F1 observation arrival | `market.observation.recorded`, `market.instrument_version.recorded` | was the instrument observed, on what reference version, and when did it arrive | adds facts to a pinned read surface |
| F1 coverage failure | `coverage.gap.recorded` | which instrument windows were not observed, and why | same; and a gap count is the kind of figure a reader over-reads |
| F2 feature state | `feature.snapshot.recorded`, `feature.checkpoint.recorded`, `feature.restart.recorded` | which snapshot was current at a decision instant, and whether a restart interrupted it | same |
| F5 feasibility evidence | `feasibility.evidence.recorded` | what feasibility evidence existed when a decision was taken | same |

**Decision requested:** whether the analytics read plane may declare any of these
families. If yes, it needs its own increment with: the declared fact and grain;
the eligible population and exclusions; the watermark and freshness semantics;
the fail-closed behaviour when the family is empty versus unavailable; and a
panel/page decision, because the page and panel inventories are frozen by the
census and its parity specification. This mission did none of that.

## 3. Type 2: no canonical producer (producer decisions)

| FAMILY | WHERE THE EVIDENCE LIVES TODAY | WHY THERE IS NO EXPOSURE TO MAKE |
| --- | --- | --- |
| Candidate lifecycle events | trading-host file WAL (`/app/data/opip/qualification`) | it is not on the canonical plane, so the read plane cannot read it |
| Detector evaluations including no-claim | not persisted beyond the engine's in-memory cycle report | no record exists to expose |
| Frozen decisions | trading-host file WAL via `app/opip/decision/store.py` | same as candidates |
| Committee assessments and AI usage | not canonical | no producer exists |
| Outcomes and learning events | learning plane | deliberately separate; publishing would cross a plane boundary |
| Incidents | no incident store exists | no producer exists |
| Portfolio marks | no canonical mark evidence | an unrealized figure would need an assumed price |

For each of these the required change is a **new canonical producer**, not a read
model. That is a new canonical-writer responsibility and touches the single-writer
invariant, so it is explicitly beyond this mission.

**Decision requested:** whether any of these families should gain a canonical
producer, and if so which single increment owns it. Until then the census's
`MISSING_CANONICAL_EVIDENCE` / `BLOCKED_BY_UPSTREAM_EVIDENCE` rows stay true.

## 4. The two seam decisions

**4.1 The engine does not expose the claim object it evaluated.** A committed
`EvaluationOutcome` carries `claim_id`, `episode`, `thesis`, `snapshot_id` and
`detector_phase`, but not the `DetectorClaim`. A consumer that needs the claim
itself - for example to build the F5/F6/F7 lineage record that binds
`detector_claim_id` - must re-evaluate the detector or be handed the claim
separately. Re-evaluating to recover a claim is a second evaluation of the same
input, which is exactly the kind of duplicate authority the architecture refuses.

**Decision requested:** whether the engine's public outcome should carry the
committed claim, or whether a committed-snapshot read seam should instead supply
claims. `ATDD-R4-B1-runtime-integration-dormant` records the same gap: *"A
committed-feature-snapshot read seam does not exist as a public API today;
defining one that creates no second evidence history is required before a real
production source is enabled."* This mission adds no such API.

**4.2 F4 episode identity: examined, found consistent, no decision required.** The
two F4 entry points derive the episode identity on their face differently:
`app/opip/opportunity_lifecycle._create_episode` passes the caller's
`policy.episode_schema_version` into `opportunity_episode_identity`, while
`app/opip/contracts/opportunity_phases.episode_from_claim` omits it and takes the
module default. That reads like a fork and was briefly recorded as one. It is not
one: `OpportunityLifecyclePolicy.__post_init__` refuses any unratified
`episode_schema_version`, `lifecycle_version` or `policy_version`, so both paths
resolve to the same ratified token by construction. A probe on one admissible
IGNITION claim produced the identical `OPEP:084327d4571400a96c1bbe79fd0de0e5` from
both paths with all twelve identity fields equal, and constructing a policy with
`opportunity-episode-v2` raises rather than forking. **No decision is requested.**
The agreement is asserted by test so it cannot silently reverse.

## 5. What this proposal is not

- It is not a request to activate anything. No mode, profile, bus, writer or
  scheduler is proposed for activation.
- It is not a request to widen trading authority. Nothing here touches F5 veto
  precedence, F6 calibrated authority, F7 selection authority or admission.
- It is not a migration. No store, projection, replica or event is rewritten,
  replayed or truncated by this mission, and no schema version changes.
- It is not a claim that any of these facts has production value. None is
  production-proven, and the matrix's `proof=` axis says `NOT_PROVEN` on every row.

## 6. Failure modes a future increment must handle

Recorded now so that an approved increment cannot quietly omit them: an unavailable
projection must withhold the value rather than report a zero; an empty population
must read as a real zero and not as an outage; a coverage gap must not become a
health score; a stale watermark must not be presented as current; an incompatible
release identity must be exposed rather than joined; and a missing family must
remain visibly missing rather than silently absent.

