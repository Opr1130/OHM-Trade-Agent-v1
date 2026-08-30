# O'Pip Sequence 5 — BUILD 5.2A

BUILD 5.2A adds the sustained shadow-equivalence evidence layer required before
any Decision Engine admission cutover can even be proposed.

It is measurement and governance only. It does not make the O'Pip Decision
Engine authoritative, alter a qualification threshold, route orders, change
Telegram behavior, or deploy itself.

## Exact paired evidence

Each `EquivalenceObservation` joins a PRODUCTION_REFERENCE Decision V2 and a
SHADOW_ENGINE Decision V2. A pair is comparable only when candidate identity,
EVH evidence, policy fingerprint, application-code fingerprint, pair, direction
and market type agree.

The durable row carries canonical hashes of each full Decision V2 and each full
gate history. Match flags and divergence classification are revalidated when a
row is loaded. The `EQO:` identity hashes the complete comparison content
(excluding wall-clock observation time), so accidental mutation cannot silently
turn a divergence into an exact match.

Missing sides are INCOMPLETE. Identity/runtime mismatches are INVALID. Neither
can count as equivalence.

## Independent denominator

Promotion evidence cannot derive its denominator from whichever comparison rows
happen to exist. `ScanCoverageExpectation` supplies an independent expected
candidate set per canonical scan.

The evaluator blocks when:

- no expected denominator is supplied
- an expected comparison is missing
- an unexpected comparison appears
- more than one distinct observation exists for one expected scan/candidate
- either comparison side is missing
- a pair is invalid

Instrumentation coverage is calculated as COMPLETE expected comparisons divided
by expected candidate slots, not by rows that survived into the ledger. This
prevents omitted or selectively recorded candidates from improving readiness.

Future production wiring must derive the expectations from canonical eligible
episode/candidate capture, not from the equivalence ledger itself.

## Durable ledger

The optional ledger is dark by default behind:

`OPIP_EQUIVALENCE_LEDGER_ENABLED=false`

When explicitly enabled it uses bounded HOT JSONL and the existing verified
WARM/COLD archive implementation. Reads reconcile archive files with the durable
manifest. A manifest-declared missing segment, invalid manifest, verification
failure, unmanifested segment, malformed HOT row or observation-ID conflict sets
ledger coverage incomplete. Incomplete ledger coverage blocks readiness.

No database is introduced.

## Sustained evaluator

`PromotionCriteria` requires explicit governance inputs for minimum comparable
observations, distinct scans, distinct UTC days and instrumentation coverage.
BUILD 5.2A invents no production promotion threshold.

Exact engine-equivalence is non-negotiable:

- zero complete-pair divergences
- one homogeneous gate-policy fingerprint
- one homogeneous application-code fingerprint
- independent expected denominator satisfied
- sufficient instrumentation coverage
- complete ledger coverage

The strongest result is `READY_FOR_HUMAN_REVIEW`. There is no PROMOTED result.
The evaluator exposes `AUTHORITATIVE=False`, `CAN_PROMOTE=False`, and
`CAN_CHANGE_POLICY=False`.

BUILD 5.2B remains a separately approved reversible admission-cutover build.

## BUILD 5.1 debt handling

Policy snapshot freeze/thaw uses explicit container tags so empty lists,
list-of-pairs, empty dictionaries and nested combinations round-trip without
container ambiguity. Fingerprinting remains over the same restored policy
values; no threshold changes.

`EvidenceCompleteness.UNUSABLE` remains a reserved compatibility enum value
for capture failures before a valid evidence bundle exists. A successfully
constructed Decision V2 evidence bundle is syntactically usable by definition.

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
- production activation of the equivalence ledger
