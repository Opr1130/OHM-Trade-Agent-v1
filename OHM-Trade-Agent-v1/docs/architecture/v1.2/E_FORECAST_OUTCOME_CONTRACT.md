# E. Forecast / outcome contract

Execution outcomes and post-fill path outcomes are separate.

## Entry execution outcomes

- `NO_FILL`
- `PARTIAL_FILL`
- `FULL_FILL`

## Post-fill path outcomes

- `TARGET`
- `STOP`
- `TIMEOUT`
- `RISK_EXIT` (only when independently triggered)

## Other required states

| State | Meaning |
| --- | --- |
| Unresolved / open | Horizon still live; not a label |
| `INCOMPLETE_COVERAGE` | Coverage gap before resolution; not a timeout; not dropped |
| `INSUFFICIENT_EVIDENCE` | Forecast abstention |

## Horizon and residuals

- Policy horizon is anchored to **first fill**.
- Additional fills do not silently restart the horizon.
- Residual entry orders expire on their own deadline.

## Timeout vs incomplete coverage

- A fully observed horizon expiry is `TIMEOUT`.
- Its economic result uses the declared executable exit policy, not an assumed midpoint.
- A coverage gap before resolution is `INCOMPLETE_COVERAGE`.

For a binary target-within-H label, a fully observed timeout is `target=0`. That does **not** imply a negative return.

## MFE / MAE and within-bar ambiguity

- MFE and MAE are path diagnostics, not achievable-profit claims.
- Ambiguous within-bar target/stop order cannot receive a clean exact label.
- Current native paper “stop wins if both touched” is a conservative Grade B reconstruction, not a Grade A exact label.

## Simulation-fidelity grades

| Grade | Meaning | Evidence use |
| --- | --- | --- |
| A | Complete required feed/book coverage and supported execution simulation | Primary paper evaluation, subject to model limits |
| B | Conservative aggregate reconstruction with declared ambiguity | Research and sensitivity |
| C | Material feed/protection gap or unknown execution path | Incident / incomplete-outcome reporting; not clean calibration |

Promotion reports disclose the full intent population, including Grade C and missingness. Clean-sample filtering must not conceal adverse coverage periods.

See [fixtures/paper_outcomes.example.json](fixtures/paper_outcomes.example.json).
