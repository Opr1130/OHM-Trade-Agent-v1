# O'Pip Decision Engine — Build 1 (shadow / read-only comparison mode)

Build 1 adds observation, traceability and identity safety. It deliberately does
**not** try to increase the number of trades, and it changes no production
admission.

---

## 1. Architecture

### Before

```
scan → candidates → margin → execution → cross-pair → reference → intelligence
     → review_candidates(...)          # prefilter + Chief, reasons discarded
     → qualified_alerts(...)           # confidence bar, drops are silent
     → target gate → economic gate → rank → Telegram → paper bridge
```

Rejections were scattered across `capture_snapshot_decision`, `candidate_trace`
and `chief_learning_capture`, each with its own vocabulary. Candidates dropped by
the Chief prefilter had **no terminal attribution at all**. The operator saw
`AI top candidates: 0` for five different causes.

### After

The production path is untouched. An observer runs beside it.

```
                 production path (authoritative, unchanged)
scan → ... → review_candidates → qualified_alerts → gates → rank → Telegram
   │      │          │                  │              │        │
   └──────┴──────────┴──────────────────┴──────────────┴────────┘
                              │ records what production decided
                              ▼
                     QualificationFunnel  ──► AdmissionDecision per candidate
                              │
                              ├──► O'Pip Decision Engine (shadow re-derivation)
                              │         │
                              │         ▼
                              │    comparison telemetry (divergence, gate match)
                              ▼
                    scan summary  ──►  stdout + JSONL  ──►  zero-trade read model
```

### Module layout — `app/opip/decision/`

| Module | Responsibility |
| --- | --- |
| `versioning.py` | version stamps + `gate_policy_fingerprint()` derived from the live thresholds |
| `thresholds.py` | read-only **aliases** of production constants; defines no new number |
| `models.py` | `GateResult`, `AdmissionDecision`, the enums, `terminal_attribution()` |
| `identity.py` | direction-scoped, timestamp-free candidate/scan identity |
| `gates.py` | thin adapters around the evaluators production already uses |
| `engine.py` | `OPipDecisionEngine` — walks the gate sequence, stops at the first failure |
| `funnel.py` | per-scan recorder + the terminal-attribution invariant |
| `comparison.py` | legacy vs shadow, on both verdict and terminal gate |
| `summary.py` | machine-readable summary + the operator text block |
| `store.py` | append-only JSONL with locking, tail repair, dead-letter, retention |
| `explanations.py` | `build_zero_trade_explanation()` read model |
| `observer.py` | the fail-soft façade the scan drives |

### Funnel lifecycle

`CANDIDATE_CREATED → DIRECTION_SELECTED → MARGIN_ELIGIBILITY →
EXECUTION_VALIDATION → CROSS_MARKET_CONFIRMATION → REFERENCE_VALIDATION →
MARKET_INTELLIGENCE → DETERMINISTIC_QUALITY → AI_ELIGIBILITY → AI_INVOCATION →
AI_RESULT → AI_CONFIDENCE → RECOMMENDATION_GATE → TARGET_QUALITY →
ECONOMIC_QUALITY → FINAL_QUALIFICATION → PAPER_ADMISSION_ELIGIBILITY`

Every registered candidate ends in exactly one terminal state, enforced by:

```
entered == qualified + rejected_by_policy + operationally_unresolved
```

A candidate the scan never terminated is `INCOMPLETE`, which counts as
operationally unresolved — an admission that instrumentation lost it, never a
rejection it did not receive.

---

## 2. Four causes that are no longer the same event

| Class | Example reason code | Meaning |
| --- | --- | --- |
| `POLICY` | `TARGET_ATTAINABILITY_FAILED`, `ECONOMIC_GATE_FAILED`, `DETERMINISTIC_VIABILITY_FAILED` | the system worked and said no |
| `OPERATIONAL` | `AI_SERVICE_UNAVAILABLE`, `SNAPSHOT_MISSING` | the system could not answer |
| `BUDGET` | `AI_BUDGET_LIMIT` | the system declined to ask |
| `MODEL` | `AI_CONFIDENCE_BELOW_THRESHOLD`, `AI_RETURNED_NO_CANDIDATES`, `AI_DECISION_WATCH` | the model answered, below the bar |

An unmapped reason code defaults to `OPERATIONAL`: an unattributed stop is by
definition not a decision the system can claim it made.

---

## 3. Identity

`opip_candidate_id = sha256(episode_id | pair | market_type | direction)`

No microsecond timestamp — the canonical episode already encodes the decision
boundary, so recomputing a candidate's id later in the same scan is stable.

`build_signal_id` and `_paper_id` gained a `direction` parameter defaulting to
`"LONG"`, interpolated exactly where the literal `LONG` used to sit. Every id
previously issued for a LONG signal is reproduced byte-for-byte
(`tests/test_opip_direction_identity_v1.py` recomputes the historical formula
independently), while `BTCUSD LONG` and `BTCUSD SHORT` can no longer collide.

---

## 4. Versioning

`STRATEGY_VERSION`, `INTELLIGENCE_VERSION`, `GATE_POLICY_VERSION` are named,
deliberately non-semantic build identifiers. `FEATURE_SCHEMA_VERSION` and
`MODEL_VERSION` are declared as explicit `null` — prepared, not required.

`gate_policy_fingerprint()` hashes the **live threshold values**, so a threshold
change is detectable in the evidence even if nobody bumps a label.

---

## 5. Storage

`/app/data/opip/qualification/` — `funnel_events.jsonl`, `scan_summaries.jsonl`,
`funnel_dead_letter.jsonl`. Dark by default behind
`OPIP_FUNNEL_TELEMETRY_ENABLED`, exactly like `P1_SHADOW_OUTBOX_ENABLED`.

Durability follows the P1 outbox conventions: exclusive writer lock, truncated
tail repaired before appending, `fsync` on close, per-row dead-lettering so one
unserialisable row cannot cost the rest of the cohort, and bounded retention.

---

## 6. ML preparation (no models built)

Every funnel row carries `episode_id`, `cohort_id`, `candidate_id`, `signal_id`,
`pair`, `market_type`, `direction` and the version block, so
`episode → candidate → decision → paper position → outcome → prediction` joins
without a migration.

Point-in-time discipline: every gate result records `evaluated_at`, and gates
read only evidence the live scan had already gathered at decision time. No
outcome-derived value enters decision-time evidence. No ML dependency is added.

---

## 7. What Build 1 explicitly does not do

- does not make the O'Pip Decision Engine authoritative
- does not change any production threshold
- does not route a counterfactual trade (eligibility is recorded only)
- does not activate futures, paper cohorts, or ML
- does not delete or disable any legacy capture system
- does not change environment configuration
