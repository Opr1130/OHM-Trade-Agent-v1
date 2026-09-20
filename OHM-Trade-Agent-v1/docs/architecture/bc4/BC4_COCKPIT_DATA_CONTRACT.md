# B/C-4 Cockpit — frozen semantic and data contract (B/C-4A)

Status: **frozen for implementation** (owner-authorised overnight session).
Scope: read-only analytical projection and presentation for O'Pip Paper v2.

This document is the **B/C-4A** deliverable required by the implementation
program: a field-level inventory, the authoritative source map, metric semantics,
trust semantics, the exact missing data, and the projection boundaries. Nothing in
B/C-4B..D may invent a semantic that is not settled here.

It **complements** and does not replace the frozen v1.2 contracts:

| Contract | Relevance to B/C-4 |
| --- | --- |
| `docs/architecture/v1.2/F_ECONOMIC_PORTFOLIO_CONTRACT.md` | primary objective, trading vs operating economics, diagnostics-vs-objective, forbidden techniques |
| `docs/architecture/v1.2/G_STATISTICAL_PROTOCOL.md` | **forbids** promoting N/profit-factor/ECE/bps thresholds to architecture constants; preregistration; `INSUFFICIENT_EVIDENCE` |
| `docs/architecture/v1.2/C_CANONICAL_WRITER_CONTRACT.md` | canonical evidence remains the single source of truth |
| `deploy/analytics/README.md` | analytics plane is derived and non-authoritative; production files remain the write-ahead log |
| `deploy/grafana/README.md` | Grafana is the presentation plane; must consume `ops.dashboard_freshness_v` |
| `app/opip/contracts/paper_metrics.py` | **the** versioned metric registry (`paper-metrics-v1`) |

---

## 1. Non-negotiable invariants (inherited, not re-decided)

1. **PAPER ONLY.** No funded/live authority. No private Kraken order capability.
2. **One canonical source of truth.** `CanonicalWriter` owns ancestry, conservation
   and economics. The cockpit derives; it never adjudicates.
3. **Read-only.** No dashboard write path, no promotion control, no risk override.
4. **AI is advisory only** and must never override a deterministic gate.
5. **Point-in-time correctness.** Retained decision evidence is never rewritten.
6. **No duplicate analytics truth.** One metric registry, one freshness authority.
7. **No alternate P&L.** The frontend/panel layer computes no P&L formula.
8. **USD and USDT are distinct portfolios** (`QUOTE_CURRENCIES = {"USD", "USDT"}`)
   and must never be summed together.
9. **UNKNOWN ≠ ZERO.**
10. **No new infrastructure.** No new database, scheduler, broker, or frontend
    framework. Reuse PostgreSQL 17 + Grafana + `app/opip/data_platform`.

---

## 2. Authoritative source map

| Domain | Authority | Notes |
| --- | --- | --- |
| Paper-v2 trade lifecycle | `app/opip/canonical/writer.py` + canonical SQLite store | The only authority for fills, exposure, economics |
| Paper-v2 trade event schema | `app/opip/contracts/paper_execution_events.py` | 9 frozen contracts (see §3) |
| Paper-v2 vocabulary | `app/opip/contracts/paper_execution.py` | enums: disposition, execution state, position state, protection state, populations |
| Fill economics | `app/opip/contracts/paper_economics.py` | `PAPER_ECONOMIC_MODEL_VERSION = opip-paper-economics-v2` |
| Metric semantics | `app/opip/contracts/paper_metrics.py` | `paper-metrics-v1`, 13 metrics |
| Freshness / trust (Python) | `app/opip/data_platform/freshness.py` | thresholds + reason vocabulary |
| Freshness / trust (SQL) | `deploy/analytics/migrations/005` + `006` | parity-tested mirror; `ops.dashboard_freshness_v` |
| Read path | `app/opip/data_platform/read_model.py` | `SET TRANSACTION READ ONLY`, 1500 ms timeout, fail-soft |
| Analytics ingest | `app/opip/data_platform/shipper.py` | append-only, idempotent, dead-letters |
| Opportunity accountability | `app/services/opportunity_accountability.py` + migration `004` | retrospective diagnostics |
| Statistical protocol | `docs/architecture/v1.2/G_STATISTICAL_PROTOCOL.md` | preregistration; **no architecture thresholds** |

---

## 3. Canonical Paper-v2 event inventory (AVAILABLE)

Every field below is **AVAILABLE** and contract-validated. Source:
`paper_execution_events.py`.

### 3.1 `paper_execution.opportunity_disposition.recorded`
Required: `disposition_id`, `decision_context_id`, `disposition_seq`,
`disposition`, `evaluation_population`, `quote_currency`, `requested_capital`,
`disposition_time`, `reason_code`.
Optional: `paper_trade_id`, `reservation_id`, `expected_portfolio_version`,
`capital_policy_version`, `portfolio_equity_limit`, `portfolio_position_limit`,
`requested_reservation_amount`.

### 3.2 `paper_execution.order_intent.recorded`
Required: `order_intent_id`, `paper_trade_id`, `decision_context_id`,
`intent_seq`, `intent_role`, `side`, `order_type`, `requested_quantity`,
`requested_notional`, `reason_code`, `intent_time`, `execution_model_version`,
`reservation_id`. Optional: `limit_price`.

### 3.3 `paper_execution.attempt.recorded`
Required: `execution_attempt_id`, `order_intent_id`, `paper_trade_id`,
`attempt_seq`, `execution_state`, `attempt_time`, `execution_model_version`.
Optional: `market_evidence_ref`, `accepted_quantity`, `rejection_reason`.

### 3.4 `paper_execution.fill.recorded`
Required: `fill_id`, `execution_attempt_id`, `order_intent_id`, `paper_trade_id`,
`fill_seq`, `side`, `quantity`, `price`, `fee_cost`, `spread_cost`,
`slippage_cost`, `other_supported_cost`, `fill_time`, `execution_model_version`,
`economic_model_version`. Optional: `market_evidence_ref`.

### 3.5 `paper_protection.plan.recorded`
Required: `protection_plan_id`, `paper_trade_id`, `plan_seq`, `stop_price`,
`targets` (`target_id`, `price`, `fraction`), `max_hold_seconds`, `plan_time`,
`protection_model_version`.

### 3.6 `paper_protection.state.recorded`
Required: `protection_event_id`, `protection_plan_id`, `paper_trade_id`,
`state_seq`, `from_state`, `to_state`, `reason_code`, `state_time`,
`protection_model_version`.

### 3.7 `paper_protection.trigger.recorded`
Required: `protection_trigger_id`, `protection_plan_id`, `paper_trade_id`,
`trigger_seq`, `trigger_type` (`STOP|TARGET|TIME`), `reference_price`,
`trigger_time`, `protection_model_version`. Optional: `market_evidence_ref`.

### 3.8 `paper_execution.reconciliation.recorded`
Required: `reconciliation_id`, `paper_trade_id`, `reconciliation_seq`,
`position_state`, `terminal_reconciliation_state`, `filled_entry_quantity`,
`filled_exit_quantity`, `remaining_quantity`, `reserved_capital`,
`realized_gross_pnl`, `recorded_execution_costs`, `realized_net_pnl`,
`reconciled_time`, `economic_model_version`. Optional: `unresolved_reason`.

### 3.9 Temporal evidence (all event timestamps)
`{precision: EXACT|BOUNDED|UNKNOWN, basis: SOURCE_REPORTED|LOCALLY_OBSERVED|MODEL_ASSIGNED, occurred_at | window_start/window_end, reason}`.
Serialization must equal the canonical `TemporalEvidence.as_dict()`.

---

## 4. Field classification for the Cockpit

Legend: **A** = AVAILABLE · **D** = DERIVABLE · **M** = MISSING (not available) ·
**NA** = NOT_AUTHORITATIVE (exists for a different plane) · **DEF** = DEFER.

### 4.1 Identity
| Field | Class | Evidence |
| --- | --- | --- |
| trade id | **A** | `paper_trade_id` on every event (`PTV2:`) |
| opportunity id | **A** | `candidate_id` / `episode_id` on decision context |
| disposition / reservation id | **A** | disposition event |
| pair / native symbol | **A** | resolved from registered `InstrumentVersion.venue_instrument_id` |
| quote currency | **A** | `quote_currency` (USD \| USDT) |
| direction | **D** | derivable from ENTRY intent `side` (BUY ⇒ long-only) |
| cohort | **A** | `cohort_id` on snapshot payload |
| **strategy + version** | **D** | **`policy_version` + `policy_fingerprint` on the decision context**; no separate `strategy_name` exists |
| engine | **A** | `engine = OPIP_PAPER_V2` |
| execution / economic / protection model version | **A** | explicit on each event |

`strategy_name` as a human label does **not** exist canonically. The strategy/version
axis is `policy_version` (+ fingerprint). The cockpit must label it as such rather
than inventing a name.

### 4.2 Lifecycle (all DERIVABLE from committed events)
Detected/qualified (episode + snapshot + context) · admitted (disposition) · order
intent · execution attempt · fills · protection plan/state · trigger · exit fills ·
reconciliation · `FINAL_VERIFIED`. **A** as events; **D** as an ordered timeline.

### 4.3 Economic reconciliation
| Field | Class | Evidence |
| --- | --- | --- |
| quantity (entry/exit/remaining) | **A** | reconciliation + fills |
| entry notional | **D** | Σ(entry fill quantity × price) |
| gross paper P/L | **A** | `realized_gross_pnl` |
| modeled fees / spread / slippage | **A** | per-fill cost components |
| net paper P/L | **A** | `realized_net_pnl` |
| cost-model version | **A** | `economic_model_version` |
| operating-cost estimate | **M/DEF** | F-contract defines operating economics, but **no canonical operating-cost evidence exists**; DEFER |

### 4.4 Planned vs observed
| Field | Class | Evidence |
| --- | --- | --- |
| planned stop / targets / fractions | **A** | protection plan |
| planned entry geometry | **A** | entry bounds on the opportunity (not on canonical events) → **D** via context/snapshot |
| planned vs actual exit | **D** | plan targets vs exit fills |
| target fractions vs actual | **D** | eligible-target ordering + fills |
| **expected/scenario net** | **M** | the platform has **no** canonical expected-return expectation; scenario targets are **not** expectations. Must never be described as expected value. |
| holding policy vs duration | **D** | `max_hold_seconds` vs fill span |
| forecast/calibration | **M** | no forecast/outcome labels exist for Paper-v2 |

### 4.5 Price path
| Field | Class | Evidence |
| --- | --- | --- |
| fill points | **A** | fills |
| frozen plan levels | **A** | plan |
| protection/exit milestones | **A** | trigger/state events |
| retained observed path | **NA/DEF** | `market.observation` exists on the analytics plane but is **not** canonically bound to a trade's holding interval on the production side; any in-position MFE/MAE requires that binding. **DEFER** unless bound. |

### 4.6 Signal / decision-time context
| Field | Class | Notes |
| --- | --- | --- |
| decision-time facts | **A** | decision context + snapshot |
| policy version/fingerprint | **A** | decision context |
| news / whale / event context | **M** | if absent at decision time the UI must show `UNAVAILABLE_AT_DECISION`; **never backfill** |

### 4.7 Portfolio / risk
| Field | Class | Evidence |
| --- | --- | --- |
| realized equity / realized drawdown | **A** | registered metric `paper.realized_equity_drawdown_pct` |
| current / max **marked-equity** drawdown | **M** | **no canonical current-valuation (mark) evidence exists for an OPEN Paper-v2 position.** The only `unrealized_pct` implementations are in the **legacy Paper-v1** `trade_monitor.py`. Reported as `UNKNOWN / INCOMPLETE`. |
| open positions | **A** | `GET_PAPER_V2_ACTIVE_EXPOSURES` |
| reservations / capacity / slots | **A** | `get_paper_portfolio_state` |
| time underwater / recovery duration | **D** | derivable **from the realized series only**, not from marks |
| operating-cost-adjusted return | **DEF** | needs operating-cost evidence (§4.3) |

> **Consequence for §9.** Because marks are MISSING, **current marked-equity
> drawdown must render as `UNKNOWN / INCOMPLETE`, never `0`.** The authoritative
> risk series in this slice is **realized-equity drawdown**, which F-contract
> explicitly permits to be shown separately. Adding marks would require new
> upstream capture that binds a current price to each open position; that is a
> frozen-upstream change and is **out of scope** (STOP condition B).

### 4.8 Execution quality
| Field | Class | Notes |
| --- | --- | --- |
| no-fill / partial / full fill rates | **A/D** | attempt `execution_state` + fills; registered `paper.fill_ratio` |
| admission → first fill latency | **D** | registered `paper.entry_latency` (SHOW_INTERVAL) |
| trigger → exit latency | **D** | registered `paper.exit_latency` (SHOW_INTERVAL) |
| fill degradation vs reference | **D** | registered `paper.entry_price_drift_bps` / `exit_price_drift_bps` |
| residual / cancel / expiry disposition | **A** | `ReasonCode` + disposition vocabulary |
| protection ack latency / time w/o verified protection | **D** | state + fill timestamps |
| reconciliation failures | **A** | reconciliation states incl. `UNRESOLVED_EVIDENCE`; registered `paper.unresolved_count` |
| simulation fidelity / coverage | **M/DEF** | no fidelity-grade vocabulary bound to Paper-v2 trades; DEFER |

### 4.9 Detection quality
| Field | Class | Notes |
| --- | --- | --- |
| detection → qualification latency | **NA/DEF** | episode/stage data exists on the analytics plane; binding to the paper trade is indirect |
| qualification → first fill latency | **D** | decision time → entry fill |
| post-detection / post-qualification fixed-horizon outcome | **NA/DEF** | retrospective; belongs to accountability MV |
| false-watch burden | **NA/DEF** | analytics-plane episode data |
| eligible-universe miss rate | **NA/DEF** | requires universe evidence not bound to trades |
| **MFE / MAE / capture ratio** | **DEF** | registered as metrics, but must be labelled **RETROSPECTIVE / HINDSIGHT DIAGNOSTIC** and require path coverage binding that does not yet exist |

### 4.10 Learning
| Field | Class | Notes |
| --- | --- | --- |
| ML model registry (lifecycle, health, minimum sample) | **A** (separate plane) | `app/opip/ml/registry.py`; `mark_statistical_degradation` uses an explicit `minimum_sample_count` |
| registered experiment ledger (preregistered endpoint/stopping rule) | **M** | G-contract *requires* per-experiment declaration; **no canonical registry of registered paper experiments exists**. Learning UI therefore renders `INSUFFICIENT_EVIDENCE` / `DEFERRED` rather than a fabricated ledger. |
| consumption disposition | **A** (separate plane) | learning-worker dispositions |

### 4.11 Opportunity accountability
| Field | Class | Notes |
| --- | --- | --- |
| directional evaluations, capturable/market-winner candidates, captured winners, executable false negatives, threshold/ranking-cap/operational misses, estimated missed move, decision-latency samples | **NA** | computed on the analytics plane by `learning.opportunity_accountability_daily_mv` (migration `004`) |
| counterfactual evaluation window | **PREREGISTRATION REQUIRED** | must come from a registered strategy/version horizon; must never be chosen after seeing the outcome. **Not implemented → DEFER** (§12 makes counterfactuals NEXT anyway). |

---

## 5. Trust model (frozen semantics)

Four **independent** dimensions; never collapsed into one score:

1. `freshness` — from `ops.dashboard_freshness_v` only (`LIVE|DEGRADED|STALE|UNAVAILABLE` + `reason` + `age_seconds`).
2. `completeness` / reconciliation — `ops.reconciliation_run` + per-stream `last_reconciliation_status`.
3. `simulation fidelity / coverage` — DEFERRED for Paper-v2 (no bound vocabulary).
4. `statistical uncertainty` — from the registry's `uncertainty_requirement` (`NONE|SHOW_SUPPORT|SHOW_INTERVAL`) plus `INSUFFICIENT_EVIDENCE`.

Rules:
- Every response carries `snapshot as_of`, `watermark`, `freshness`, `completeness`,
  metric-registry version, projection version, cost-model version, currency,
  timezone, coverage, population/filters, and correction state.
- Panels **inherit** their dependency's trust state and must visibly show
  `STALE` / `UNKNOWN` / `INCOMPLETE`. **`UNKNOWN ≠ 0`.**
- A reconciliation mismatch withholds the affected economics as definitively
  reconciled. A conflicting correction quarantines the affected result.

---

## 6. Statistical posture (do **not** invent thresholds)

Per `G_STATISTICAL_PROTOCOL.md`:

- Minimum meaningful effect is **experiment-derived and owner-approved per
  registered family** — **not** an architecture constant.
- **Do not** promote N_eff, profit-factor, ECE, or bps thresholds to constants.
- Thin cohorts ⇒ shrinkage and/or abstention: **`INSUFFICIENT_EVIDENCE`**.
- Primary endpoint is **net portfolio dollars over a common window**.
- Hit rate, profit factor and ECE are **diagnostics**, never the primary endpoint
  and never a "winner ranking".
- Consequence for the cockpit: strategy contribution shows **counts, realized net
  P/L, expectancy and realized drawdown with explicit support/interval labels** —
  **no pass/fail gate and no invented "N ≥ 30"**.

---

## 7. Projection boundaries (minimal, additive)

1. **Analytical trade summary** (one row per canonical Paper-v2 trade) — the single
   reconciled read model for Trade Detail and the ledger. Carries identity,
   lifecycle milestones, economic reconciliation, plan binding, cost-model binding,
   reconciliation state, and a trust envelope.
2. **Portfolio series** — realized equity series + realized drawdown + reservation
   and slot occupancy, per quote currency.
3. **Accountability / composition** — funnel counts and strategy(version)
   contribution, per quote currency.
4. **Presentation** — the existing Grafana plane plus the existing read-only
   `GET /api/analytics/*` surface. **No new framework.**

Corrections are **append-only and versioned**; a superseding row never mutates the
row it supersedes. Rebuilds are deterministic from canonical evidence.

---

## 8. Explicitly out of scope

- Marked-equity valuation / mark capture (requires frozen-upstream change).
- Canonical forecast/calibration labels.
- A registered experiment ledger for paper strategies.
- Counterfactual windows (§12 defers; preregistration not implemented).
- Operating-cost evidence.
- Any composite "Intelligence Score".
- Any dashboard-controlled promotion, risk or execution authority.
- New infrastructure of any kind.

---

## 9. Project Brain reconciliation (B/C-4A sign-off)

```text
PROJECT_BRAIN_RECONCILED_VIA_OWNER_ARB_SUPPLIED_CANONICAL_CONTEXT
```

The owner/ARB supplied the canonical "O'Pip Project Brain" extract for this run.
Repository governance and the frozen checked-in contracts remain primary technical
evidence; the supplied context governs architectural intent, sequencing and scope.

### 9.1 Contradictions found: none at the frozen-contract level

The supplied context and this inventory agree on every frozen point: read-only
authority, one versioned metric registry, the four independent trust dimensions,
`UNKNOWN ≠ 0`, point-in-time correctness, no historical rewriting, no composite
Intelligence Score, no funded/live authority, no new infrastructure, no invented
statistical constants, and the B/C sequencing.

### 9.2 Two divergences in *emphasis*, resolved in favour of repository evidence

**(a) Marked-equity drawdown is listed as a primary owner metric, but its coverage
does not exist in first release.**

The supplied context lists *Current* and *Maximum Marked-Equity Drawdown* among the
primary metrics **and** simultaneously states the governing rule: *"where
defensible valuation coverage exists"*, *"If required valuation evidence is
unavailable: drawdown = UNKNOWN / INCOMPLETE"*, *"Never silently substitute
realized-balance drawdown"*, and it requires us to *"explicitly verify before
promising first-release support: … marked-equity valuation-history coverage"*.

Repository evidence (§4.7) shows there is **no canonical mark bound to an open
Paper-v2 position**; the only `unrealized_pct` implementation belongs to the legacy
Paper-v1 monitor. The verification the context asks for has therefore been
performed, and its own stated fallback applies:

- marked-equity drawdown ⇒ **`UNKNOWN / INCOMPLETE`** (never `0`);
- **realized-equity drawdown** (`paper.realized_equity_drawdown_pct`) is exposed as
  a **separate, explicitly named** metric, which the context expressly permits.

This is not a contradiction: the context anticipated exactly this outcome. It does
mean the primary risk tile renders `UNKNOWN` in first release, which is the correct
truthful behaviour rather than a gap to paper over.

**(b) Strategy identity has no canonical name.**

The context's Trade Detail requires `strategy/version`. Repository evidence shows no
`strategy_name` field exists; the axis is the decision context's `policy_version`
(plus `policy_fingerprint`). The cockpit therefore labels the axis
**"policy version"** and displays the fingerprint, rather than inventing a
human-readable strategy name. This satisfies the requirement truthfully.

### 9.3 Additional verified gap: canonical Paper-v2 evidence is absent from the analytics plane

The context says *"Grafana remains the default where sufficient"* and B/C-4D is a
read-only presentation/API contract. Verification shows a boundary that materially
constrains the presentation choice:

- Canonical Paper-v2 evidence lives in a **SQLite store on the trading host**
  (`app/opip/canonical/paths.py`: `DB_PATH`, served by the `opip-canonical-writer`
  process over `writer.sock`).
- The analytics plane's ingest is a **JSONL file export**. Its stream list
  (`app/opip/data_platform/streams.py`) contains
  `paper_trade_events → paper_trading/events.jsonl`, which is **legacy Paper-v1**,
  and contains **no canonical Paper-v2 stream**.

Therefore PostgreSQL/Grafana currently **cannot** see canonical Paper-v2 economics.
Shipping them there would require a new canonical→analytics stream: new data-plane
work touching the learning/analytics ownership boundary.

Per the context's own rule — *"Existing infrastructure must be reused unless
measured evidence proves it cannot satisfy the requirement"* — and the B/C-4
programme's "no new infrastructure", the first release reuses the **existing
read-only production surface** (`app/api/dashboard.py` → `/api/analytics/*`,
already `SET TRANSACTION READ ONLY`) reading the canonical store directly, and
leaves Grafana responsible for the evidence that already lands in PostgreSQL
(screening, funnel, attrition, accountability, legacy paper).

**This gap is recorded, not worked around.** Adding a canonical Paper-v2 analytics
stream is a candidate follow-up requiring explicit owner architecture approval.

### 9.4 Deferred items confirmed against the context

The context's own deferrals match §8 exactly: no composite Intelligence Score, no
model-voting/committee UI, no unrestricted strategy leaderboard, no raw
news/whale/market-mover terminal, no custom dashboard builder, no new warehouse or
time-series platform, no second scheduler, no per-trade infrastructure-cost
allocation, and no execution/risk/promotion controls.

The context's *"primary likely genuinely new analytical evidence"* —
preregistered post-rejection opportunity outcome tracking — is confirmed by §4.11
as **not yet existing** (no registered experiment ledger, no preregistered
horizon). It must **not** be created speculatively in B/C-4B, and is therefore
listed as DEFER.

### 9.5 Governing test applied

Every surface in the B/C-4B..D scope answers *yes* to both governing questions:
it materially helps understand/protect sustainable realized net profit, and every
number traces to canonical evidence. Anything that fails either test is listed in
§8 as out of scope.

---

## 10. Inventory outcome

**AVAILABLE** → identity, canonical lifecycle, economic reconciliation, plan
binding, execution-quality derivations, portfolio reservations, realized drawdown,
freshness/reconciliation trust, opportunity accountability (analytics plane).

**DERIVABLE** → ordered lifecycle timeline, latencies, drift, planned-vs-observed,
strategy version axis (`policy_version`), time-underwater from the realized series.

**MISSING** → marks / marked-equity drawdown, expected-return expectations,
forecast/calibration labels, registered paper-experiment ledger, fidelity grade,
decision-time news context.

**NOT_AUTHORITATIVE (separate plane)** → opportunity-accountability MVs, market
observation path series, ML model registry.

**DEFERRED** → counterfactuals, MFE/MAE/capture (needs path binding), detection
miss rates, operating costs.
