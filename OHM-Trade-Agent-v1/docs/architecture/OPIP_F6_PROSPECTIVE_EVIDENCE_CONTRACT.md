# O'Pip — Prospective F6 Forecast Evidence Contract (frozen)

Status: **FROZEN, PREPARED, NOT ACTIVATED.** This document freezes the smallest
architecture-consistent contract under which prospective evidence for the F6
Forecast Engine may be collected and later evaluated. It activates nothing,
trains nothing, promotes nothing, writes no production mode, and grants no
trading authority.

Authority references:

- `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`
  (owner authority; SHA256
  `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`).
- `docs/architecture/v1.4.3/ARCHITECTURE.md` (paragraph extraction).
- `docs/atdd/scope-contracts/ATDD-R3-F6-forecast-engine.md` (frozen F6 behaviour).
- `docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md`
  (this increment, Slice 3B).
- `docs/architecture/v1.2/E_FORECAST_OUTCOME_CONTRACT.md` and
  `docs/architecture/v1.2/G_STATISTICAL_PROTOCOL.md`.

The label projection that realises this contract in code is
`app/opip/forecast_labels.py`; the projection is a read-only re-expression of the
canonical writer's committed evidence and is **not** a second outcome authority.

## 1. What this contract is

One frozen statement of the prospective population, the two label families and
the policies that turn committed canonical paper evidence into F6 forecast
evidence. It is written **before** any outcome is observed, so the vocabulary,
population and policies cannot be chosen after seeing the future.

## 2. Full population (nothing is dropped)

Every eligible prospective evaluation unit is retained, including:

- **selected** (admitted) opportunities;
- **rejected / not-qualified** opportunities (`COUNTERFACTUAL_REJECTED`);
- **abstained** opportunities (F5 `INSUFFICIENT_EVIDENCE` / F6 abstention);
- **no-fill** entries (`NO_FILL`);
- **incomplete** outcomes (`INCOMPLETE_COVERAGE`);
- **unresolved** outcomes;
- **cash / no-trade** decisions (a qualified opportunity deliberately not admitted).

The absence of a trade is evidence and remains countable.

## 3. Calibration-eligible subset

A unit is calibration-eligible for a family **only** when that family's label is
cleanly `RESOLVED` under the frozen label/fidelity policy:

- **entry-execution family**: `NO_FILL`, `PARTIAL_FILL` or `FULL_FILL`, resolved;
- **post-fill path family**: `TARGET`, `STOP`, `TIMEOUT` or `RISK_EXIT`, resolved.

Nothing else is scored. Provisional market-derived labels (for example the
phase-3C `PROVISIONAL_MARKET` evidence) are **never** a calibration-eligible
label for a primary supervised row.

## 4. Unresolved / incomplete population (retained, never negative)

`UNRESOLVED` and `INCOMPLETE_COVERAGE` labels are retained and reported. They are
never silently recoded as a negative, never dropped, and never repaired into a
favorable value. Missing evidence is never favorable evidence.

## 5. LONG / SHORT populations

LONG and SHORT results are reported separately whenever the evidence differs.
A direction the route does not authorise carries an explicit disposition rather
than being silently mapped onto another direction. Current capture emits LONG
only; SHORT remains contractually supported and is separately reported.

## 6. Cash / no-trade population

Deliberately unadmitted opportunities are retained as the cash/no-trade
population with a realized net return of exactly zero, for matched comparison
against the selected population.

## 7. Two label families (never mixed)

| Family | Tokens | Notes |
| --- | --- | --- |
| Entry-execution | `NO_FILL`, `PARTIAL_FILL`, `FULL_FILL` | `NO_FILL` deploys no capital: zero return, **no** path label |
| Post-fill path | `TARGET`, `STOP`, `TIMEOUT`, `RISK_EXIT` | conditional on fill exposure only |

- `NO_FILL` has **no** post-fill path label.
- `TIMEOUT` is an observed horizon expiry scored from its **recorded** realized
  return; it is never automatically a negative return.
- `INCOMPLETE_COVERAGE` is not `TIMEOUT` and is not a negative.
- An ambiguous within-bar target/stop ordering (`OHLC_GAP`) is not a clean exact
  path label.
- The two families are never collapsed into a single win probability.

## 8. Frozen policies (chosen before outcomes)

| Policy | Frozen choice |
| --- | --- |
| Population inclusion | Section 2 (full population, all classes retained) |
| Feature / input schema | the canonical `FeatureSnapshot` contract, sealed per §1 of the ML foundation |
| Evaluation instant | the snapshot's own `evaluation_cutoff` |
| Evidence cutoff | the close of the latest completed source candle; never backdated |
| Availability rule | evidence eligible only if available by the decision boundary (per-feature `AvailabilityStamp`) |
| Forecast horizon | explicit entry-deadline, post-fill path and forecast-validity durations; no numeric default |
| Entry deadline | explicit, per decision; no default |
| Post-fill path anchor | `FIRST_FILL` (additional fills do not restart it) |
| Label policy | the two families of §7, via the frozen canonical→F6 mapping in `app/opip/forecast_labels.py` |
| Missingness policy | missingness recorded as missingness, never as zero or a favorable value |
| Fidelity policy | grade `B` for native paper; grade `C` for an ambiguous path or incomplete lineage; grade `A` is never inferred |
| Fee policy | the committed canonical realized net figure (net over committed capital, dimensionless) |
| Correction policy | append / supersede only; historical evidence is never rewritten |
| Maturity policy | a unit matures only from its own committed terminal evidence, never because a model predicted it |
| Sealing policy | sealed evaluation populations; a label available at or before the prediction cutoff is future-label leakage and is refused |
| Training cutoff | declared per dataset manifest; a model trained after the evaluation time fails closed |
| Dataset manifest identity | explicit dataset manifest with cutoff, embargo, missingness and exclusion policy |

**These are never chosen after viewing future outcomes.** Any material change to
population, feature schema, horizon, label policy, fidelity or fee policy
requires a **new contract version** and a **new / segmented prospective cohort;
cohorts are never silently mixed.**

## 9. Statistical rules (frozen)

- No qualified calibrated model ⇒ `INSUFFICIENT_EVIDENCE` ⇒ `NO_CALIBRATED_MODEL`.
- `RESEARCH_ONLY` remains non-authoritative.
- No probability is ever generated from a technical score, opportunity score,
  ranking score, tradeability score, alert/AI/Committee confidence, the Committee
  0-100 rubric, an F5 disposition, or `score / 100`, or an invented sigmoid.
- The v1.4.3 AI Model Registry (Committee advisory routing) is **not** the F6
  statistical model registry; the two authorities stay separate.

## 10. Maturation gate (dependence-aware)

Required evidence is derived from the predeclared endpoint, the desired precision
/ effect size, the dependence structure (temporal and cross-sectional),
sample composition, missingness and fidelity, LONG/SHORT coverage, and
calibration reliability requirements. Observations that are temporally or
cross-sectionally dependent are **not** treated as IID; evaluation resamples
contiguous time blocks. No universal sample-count, Brier, log-loss, ECE or
`N_eff` threshold is asserted here as an architecture constant, and no automatic
statistical promotion exists.

When the required prospective evidence has not matured, the disposition is:

```
REAL_EVIDENCE_MATURATION_REQUIRED
```

with the exact missing evidence and the prospective collection plan — never a
fabricated corpus and never a model promoted to unblock a later phase.

## 11. Evidence-readiness census at freeze time

| Component | Class |
| --- | --- |
| PIT feature snapshots for training | `PARTIAL` |
| Entry-execution labels (`NO_FILL`/`PARTIAL_FILL`/`FULL_FILL`) | `MISSING` as a durable produced stream; derivable via this contract's projection |
| Post-fill path labels (`TARGET`/`STOP`/`TIMEOUT`/`RISK_EXIT`) | `MISSING` as a durable produced stream; derivable via this contract's projection |
| Realized returns incl. fees | `PARTIAL` (fee-aware canonical net; no slippage/latency/partial-fill model) |
| Execution fidelity grades | `PARTIAL` (enum exists; no record carries a grade) |
| Sealed evaluation population / training cutoff | `PARTIAL` (contracts exist; no instance) |
| Resolved-vs-unresolved / missingness accounting | `AVAILABLE` |
| LONG coverage | `PARTIAL` |
| SHORT coverage | `MISSING` (capture emits LONG only) |

## 12. What this contract is not

It is not an activation, not a model, not a promotion, not a second outcome
engine, not a parallel calibration spine, and not authority. It changes no F3-F7
behaviour and no production mode. `FUNDED TRADING = OUT OF SCOPE`.
