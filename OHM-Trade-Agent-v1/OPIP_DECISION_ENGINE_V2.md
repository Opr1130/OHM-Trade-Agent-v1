# O'Pip Decision Engine V2 — Sequence 5 BUILD 5.1

BUILD 5.1 is additive. It adds immutable point-in-time decision evidence,
deterministic cryptographic identities, policy provenance, and a bounded
exact-replay contract. It does not promote the O'Pip Decision Engine or change
production admission, Telegram, paper trading, Kraken, Risk Shield, or any
threshold.

## Reuse

V2 reuses the existing V1 gate evaluators, GateResult, AdmissionDecision,
GATE_ORDER, reason taxonomy, threshold-distance semantics, direction-safe
candidate identity, and gate-policy fingerprint. There is no parallel decision
engine.

## Evidence

OPipDecisionEvidence seals candidate, AI, context, identity provenance and
policy evidence at T0. Mutable objects are canonicalized to JSON when sealed.
The evidence identity is the full SHA-256 EVH digest; there is no truncated
secondary identifier.

Completeness is relative to the frozen policy's required_evidence_refs.
Sequence 4 streaming is optional unless that policy explicitly requires it.

## Policy snapshot

GatePolicySnapshot freezes the exact deterministic threshold inputs observed at
T0 and verifies them against the existing GPF fingerprint. The snapshot is
evidence only; BUILD 5.1 never writes frozen values into runtime policy.

## Decision V2

AdmissionDecisionV2 is built from V1 output and preserves gate_results as an
ordered immutable tuple. Decision identity is:

DEC:SHA256(candidate_id | decision_role | engine_version |
           gate_policy_fingerprint | evidence_hash)

Roles are PRODUCTION_REFERENCE, SHADOW_ENGINE, CHAMPION and CHALLENGER. Merely
recording a role never changes trading authority.

## Replay boundary

BUILD 5.1 claims exact re-evaluation only when both the current deterministic
policy fingerprint and the compatible engine version match the frozen record.
A mismatch raises explicitly. It does not silently run today's thresholds over
old evidence and call that historical replay.

Replay consumes only the sealed evidence bundle; it does not query current
provider health, EventStore, Risk Shield, asset aliases, market data, an
exchange, or a network service.

## Deferred

BUILD 5.1 does not implement the sustained promotion ledger/cutover, Outcome
Registry, counterfactual routing, learning governance, generalized
champion/challenger promotion, futures, ML, merge, or deployment.
