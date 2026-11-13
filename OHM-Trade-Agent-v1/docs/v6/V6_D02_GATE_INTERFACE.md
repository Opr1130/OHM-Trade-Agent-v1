# V6-D02 — Shared, pure, versioned gate interface (ADR)

Status: **Approved by owner for design and isolated implementation. NOT integrated.**

## Decision
Add `app/opip/gates/` exposing `evaluate_gate(GateInput, GatePolicy) -> GateResult`
(`interface_version = opip.gate.v1`). It mirrors the production economic gate's
numeric semantics (`evaluate_economic_quality`) in a dependency-free module so the
scanner, simulator and Chief can later share one definition.

## Properties
- Pure: only `hashlib/json/math/dataclasses/enum/typing` imports (AST-tested).
- Fail closed: `None`, strings, bools, NaN/inf, bad policy or direction reject with
  reason codes; the function never raises on bad data. Missing is never zero.
- Versioned and attributable: result carries interface version, `policy_id` and a
  SHA-256 policy digest.
- Ordered reasons: all failed checks are reported; `primary_reason` equals the
  production gate's single rejection (RR, then move, then net profit, then account risk).
- Strict-positive after-cost net profit (cent-exact half-up) even with all filters off.

## Evidence
`tests/test_opip_gate_interface_v1.py` (27 tests): contract, fail-closed matrix,
purity, "not imported outside the package", equivalence with the production gate on
3,000 seeded random cases plus a boundary grid. Four hand mutations were each killed.

## Known differences from production (deliberate, to be reviewed before integration)
1. Production raises `ValueError` for an invalid direction; the interface rejects.
2. Production's `quantize_usd_cents` is re-implemented, not imported, to stay pure;
   equivalence is test-enforced rather than shared code.
3. Production's formatted rejection strings are replaced by stable reason codes.
4. Only Target-2 economics are gated, as in production; Target-1 metrics are omitted.

## Gating / non-goals
No scanner, Chief, S1-dependent reconciliation, lineage or S4 persistence changes.
Integration requires S1 evidence and a compatibility review, then a separate owner approval.

## Hardening addendum (owner review, 2026-11-13)
- `evaluate_gate` is wrapped: any arithmetic/type failure becomes `INTERNAL_ERROR`;
  non-finite derived values become `NUMERIC_OVERFLOW`. Stress tests cover 1e308 capital,
  leverage, costs, geometry and policy floors.
- **Production finding:** `evaluate_economic_quality(plan, 1e308)` raises `OverflowError`
  (verified). The interface is stricter by design; production callers are unchanged.
- Direction convention verified: production accepts only LONG/SHORT (case-insensitive,
  raises otherwise); the interface is identical except it rejects instead of raising.
- Added: exact decimal net-profit boundary ($75.00 / $75.01) vs production, golden
  policy digest, stable result serialization. 37 tests.
