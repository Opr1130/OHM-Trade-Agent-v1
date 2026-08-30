# O'Pip Sequence 5 — BUILD 5.2A

BUILD 5.2A adds the sustained shadow-equivalence evidence layer required before
any Decision Engine admission cutover can even be proposed.

It is measurement and governance only. It does not make the O'Pip Decision
Engine authoritative, does not alter a qualification threshold, does not route
orders, does not change Telegram behavior, and does not deploy itself.

## Scope

### Paired equivalence observation

Each eligible comparison is represented as an immutable
`EquivalenceObservation` joining a PRODUCTION_REFERENCE Decision V2 and a
SHADOW_ENGINE Decision V2.

A pair is comparable only when it agrees on the sealed candidate/runtime
identity:

- candidate ID
- evidence hash
- gate-policy fingerprint
- application-code fingerprint
- pair
- direction
- market type

Missing sides are INCOMPLETE. Identity/runtime mismatches are INVALID. Neither
state may count as a successful comparison.

For a COMPLETE pair, exact equivalence requires all of:

- same outcome
- same terminal gate
- same terminal reason code/class
- byte-equivalent ordered gate history after canonical serialization

The observation identity is an `EQO:` SHA-256 over canonical structured join
keys. Duplicate observations are idempotent; an ID collision with different
content fails closed.

## Durable ledger

The optional ledger is dark by default behind:

`OPIP_EQUIVALENCE_LEDGER_ENABLED=false`

When explicitly enabled it writes bounded HOT JSONL and reuses O'Pip's verified
WARM/COLD archive implementation. Older observations are archived rather than
discarded. Reads return an explicit coverage-completeness bit and warnings;
archive corruption, malformed HOT data or observation-ID conflict can therefore
block promotion evaluation instead of silently shrinking the denominator.

No database is introduced.

## Sustained promotion evaluator

`PromotionCriteria` requires the caller to explicitly choose minimum:

- comparable observations
- distinct scans
- distinct UTC days
- instrumentation coverage

BUILD 5.2A deliberately does not invent a production promotion duration or
sample threshold.

Exact engine-equivalence itself is non-negotiable:

- zero complete-pair divergences
- one homogeneous gate-policy fingerprint
- one homogeneous application-code fingerprint
- sufficient instrumentation coverage
- complete ledger coverage

The strongest result is:

`READY_FOR_HUMAN_REVIEW`

There is intentionally no PROMOTED result. The evaluator exposes
`AUTHORITATIVE=False`, `CAN_PROMOTE=False`, and `CAN_CHANGE_POLICY=False`.

BUILD 5.2B remains the separately approved reversible admission-cutover build.

## BUILD 5.1 debt handling

The policy snapshot freeze/thaw representation is hardened in 5.2A with
explicit type tags so empty lists and list-of-pairs round-trip without being
misread as dictionaries. The calculated gate-policy fingerprint remains over
the same thawed policy values; no threshold changes.

`EvidenceCompleteness.UNUSABLE` remains a reserved compatibility enum value.
A successfully constructed `OPipDecisionEvidence` is syntactically usable by
definition, so the dead internal branch is removed/documented rather than
manufacturing a fake unusable evidence object. Failed capture belongs outside
the valid Decision V2 evidence contract.

## Out of scope

- Decision Engine admission cutover
- legacy-path demotion
- threshold changes
- challenger policy promotion
- Outcome Registry
- counterfactual routing
- ML
- futures
- autonomous trading
- production deployment
