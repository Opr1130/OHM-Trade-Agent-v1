# Shared gate interface: design (V6-D02, approved for design and isolated work)

## Finding (evidence: app/opip/decision at main 88c335b6)
A shared, pure, versioned gate layer **already exists**:
- `thresholds.py` imports every threshold from the module that owns it (no duplicates) and exposes `gate_policy_constants()`.
- `gates.py` holds thin adapters over the production evaluators (margin, execution, cross-market, reference, market intelligence, deterministic quality, recommendation, target and economic quality), with no network I/O.
- `policy_snapshot.py` freezes thresholds with a fingerprint; `replay.py` and `equivalence.py` replay and compare decisions.

The offline simulator (PR #561) is the only parallel rule set: it applies parametric thresholds to flat candidate dicts, not to scan snapshots.

## Design
Two tiers, one source of truth:
1. **Tier 1, real-snapshot evaluation** = existing decision engine + gate adapters. Authoritative. What-if policies would need a threshold-injection parameter on the gate functions, which are module-constant driven today. That edit touches the live qualification path and stays **gated** until S1 evidence and compatibility verification are done.
2. **Tier 2, parametric simulator** = approximation for sweeps. It must never be mistaken for Tier 1. `app/opip/simulator/parity.py` (this increment) builds a baseline from the live constants, reports exact, converted and approximate fields, lists gates it does not reproduce, and gives a digest so threshold drift in production is detectable.

## Not done (gated)
- No change to the scanner, gates or thresholds.
- Gate-level threshold injection (D2 follow-up). Compatibility proof required: for the current constants the injected path must produce byte-identical decisions to today's path, shown with the existing equivalence observation on stored snapshots.

## Caveats
- The reference profile (`strategy_policy.json`) is the v2 *target* (threshold 75, advisory economics 0); live production constants are v1 threshold 80, AI confidence 85, spread 30 bps. The simulator now offers both baselines (`baseline_from_strategy_policy` for the target profile, `production_baseline` for live).

## Reconciliation with #566 (merged) and review corrections
- `app/opip/gates/interface.py` (#566, `opip.gate.v1`) is a pure, dependency-free mirror of the production *economic* gate. It does not replace the decision-engine gate adapters (Tier 1); it is the intended shared economic core. A later step should have the simulator call `evaluate_gate` for the economic checks instead of its own arithmetic. That step is not taken here, to avoid a third rule implementation and to keep this PR isolated.
- The parity baseline is labelled `CODE_CONSTANTS_V1_REFERENCE`: module constants are not proof of the deployed scorer or effective runtime policy. Reports expose `full_policy_fingerprint` (all constants, including unmapped gates) separately from `simulator_digest` (mapped fields only).
