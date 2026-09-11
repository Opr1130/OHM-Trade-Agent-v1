# I. Safety / monitoring contract

## Independence

Safety and protection code cannot depend on detectors, forecast, economics, or AI.

Position protection already runs independently of discovery scheduling inside `run_cycle` (active-trade monitor after Kraken reconcile, before paper monitor).

Durable protection state will use the prioritized writer intent path once the writer exists. Until then, existing registries remain the as-built store. PR 1 does not change that.

## Fail-closed vs open positions

- New entries fail closed on feed or data gaps.
- Open positions preserve state, mark uncertainty, and escalate rather than invent fills.
- Existing positions retain approved protection during new-entry suspension.

## Incident lifecycle

```
OPEN → CHANGED / ESCALATED → RECOVERED
```

Reminders are bounded. Per-cycle duplicate alerts are forbidden.

See [fixtures/incident_lifecycle.example.json](fixtures/incident_lifecycle.example.json) and [fixtures/coverage_incident.example.json](fixtures/coverage_incident.example.json).

## Alert governor adaptation plan

The as-built governor (`CREATE` / `EDIT` / `SUPPRESS`) is the production attention valve and the [PR 2 capture boundary](PR2_CAPTURE_BOUNDARY.md).

Later adaptation (not in PR 1):

- Map `CREATE` of a protection/coverage incident to `OPEN`.
- Map `EDIT` / `MEANINGFUL_TRANSITION` to `CHANGED` or `ESCALATED`.
- Map recovery evidence to `RECOVERED`.
- Keep cooldown and 8-card/24h budget as the bounded-reminder policy until a later ratified incident policy replaces them.

## External heartbeat

External heartbeat evidence must exist outside a failed production process and is imported canonically after recovery. Current as-built heartbeats (unified-cycle lock, Early Watch scheduler state, Freqtrade 60s heartbeat, export age) are provenance inputs, not a second truth system.

## Protection coverage as fidelity evidence

Protection coverage gaps are evidence and affect simulation-fidelity grade (see contract E).

## Event Risk Shield

`AlertStateManager` exists at the pin and is **not scheduled** in `run_cycle`. PR 1 documents it as an unused incident-model implementation. PR 2 does not require scheduling it.
