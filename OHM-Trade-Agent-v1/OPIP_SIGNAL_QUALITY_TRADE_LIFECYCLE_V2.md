# O'Pip Signal Quality & Trade Lifecycle Architecture v2

Status: DESIGN FOR REVIEW — not production-authoritative
Primary objective: maximize actionable signal quality and capital efficiency while preserving advisory-only operation.

## 1. Product objective

O'Pip should not be an alert generator. It should be an opportunity-selection and position-protection system.

The user-facing contract is intentionally narrow:

1. NEW TRADE SIGNAL — emitted only when a candidate is actionable now or within a tightly defined entry zone and has passed every required quality gate.
2. EXISTING TRADE ACTION — emitted only when a verified Kraken holding materially changes in a way that requires attention or action.

Everything else stays internal in monitoring queues, telemetry, learning stores, dashboards, and shadow models.

Core principle: Monitor broadly. Rank globally. Alert narrowly. Protect holdings continuously.

No EARLY WATCH / READY / DEVELOPING Telegram noise is part of the target architecture.

## 2. Non-negotiable invariants

- Kraken remains read-only for real holdings and reconciliation.
- No automatic order placement, modification, cancellation, or exchange execution authority.
- Every real Kraken holding must be independently detectable by the protection engine.
- Discovery and existing-position protection are separate runtime concerns.
- A local registry is lifecycle context, not proof of live exposure.
- No probability label may be shown unless calibrated against an explicit historical event definition.
- Unknown / incomplete evidence must not be converted into positive confirmation.
- Events/news/whale/order-flow/technical evidence are inputs to one decision process, not independent trade triggers.
- A single data source can veto for safety, but should not create a trade by itself.
- Signal quality is measured by trade outcomes, not alert volume.

## 3. Target architecture

MARKET INTELLIGENCE PATH

Kraken spot universe
  -> multi-channel discovery
     -> leaderboard / rank velocity
     -> pre-breakout / compression
     -> relative-volume anomalies
     -> order-flow / whale accumulation
     -> catalyst / event-confirmed setups
     -> pullback / continuation structures
     -> cross-market / venue anomalies
  -> silent monitoring queue
  -> intelligence fusion
  -> continuation / explosion engine
  -> entry engine
  -> global opportunity ranker
  -> capital utility / portfolio gate
  -> NEW TRADE SIGNAL

EXISTING POSITION PROTECTION PATH

Kraken balance/position truth
  -> canonical exposure resolver
  -> attach O'Pip lifecycle context when available
  -> continuous market/event/order-flow intelligence
  -> HEALTHY / DETERIORATING / RECOVERING / PROFIT_PROTECT / EXIT_REVIEW / INVALIDATED
  -> alert only on material or actionable change

Both paths feed outcome capture -> learning -> calibration.

## 4. Discovery is broad; Top-15 is only one funnel

The Kraken leaderboard is a high-value discovery source, not the trading universe.

### 4.1 Eligible universe

Build a canonical Kraken spot universe after excluding unsuitable markets using explicit policy:

- supported quote currencies
- minimum 24h notional liquidity
- maximum spread / minimum depth
- no inactive/delisted pairs
- configurable exclusion list
- stablecoin-to-stablecoin and structurally unsuitable pairs excluded
- identity must be canonical and unambiguous

### 4.2 Discovery channels

Each channel emits CandidateObservation records into one silent queue.

A. Leaderboard / rank-velocity
- current market rank
- rank change over 5m / 15m / 30m / 60m
- relative strength vs universe
- acceleration of percentile rank
- Top-15 entry and rapid upward rank migration

B. Pre-breakout / compression
- Bollinger bandwidth percentile
- ATR percentile
- compression duration
- volume accumulation
- distance to structural breakout

C. Volume / participation anomaly
- 5m / 15m / 1h relative volume
- acceleration of relative volume
- quote-volume quality
- cross-pair confirmation

D. Order-flow / whale anomaly
- aggressive buy/sell imbalance
- CVD / cross-venue CVD
- order-book imbalance and depth change
- large trade concentration
- liquidation/funding/OI context where available

E. Catalyst / event
- verified event relevance
- directionality
- freshness
- source quality
- market confirmation after event
- event revision / contradiction handling

F. Pullback / continuation
- prior impulse quality
- retracement depth
- support/retest quality
- volume contraction on pullback
- continuation re-acceleration

No channel may directly emit a user-facing trade alert.

## 5. Monitoring queue

A candidate enters the monitoring queue when any discovery channel identifies enough evidence to justify continued observation. The queue is intentionally silent.

CandidateState fields:

- candidate_id / episode_id / asset_id / pair
- discovery_sources
- first_seen_at / last_updated_at
- rank_history
- price / volume / order_flow / event / market_regime / liquidity / structure / exhaustion features
- evidence_quality
- continuation_assessment
- entry_assessment
- opportunity_assessment
- terminal_reason

Internal states only: OBSERVED, MONITORING, QUALIFYING, ACTIONABLE, REJECTED, EXPIRED.

These states must not map 1:1 to Telegram messages.

## 6. Intelligence fusion

V2 creates one canonical point-in-time feature snapshot used by downstream decision models.

Feature families:

- Market-relative: universe percentile return, leaderboard rank, rank velocity/acceleration, sector strength, BTC/ETH beta-adjusted relative strength.
- Momentum: 5m/15m/1h/3h/6h return, acceleration, trend persistence, breakout distance, realized-volatility-normalized movement.
- Participation: relative volume by horizon, volume acceleration, buy/sell participation, cross-market confirmation.
- Order flow / whale: CVD, cross-venue agreement, large-trade imbalance, order-book imbalance, depth changes, liquidation/OI context, evidence quality.
- Events/news: relevance, direction, freshness, provider reliability, corroboration, revisions, post-event market confirmation.
- Tradeability: spread, depth, slippage estimate, 24h notional, execution fragility.
- Structure: breakout/retest quality, support/resistance, ATR-normalized stop distance, pullback depth, wick rejection, trend regime.
- Exhaustion: ATR extension, declining volume, blow-off/wicks, rank saturation, crowded derivatives, momentum divergence.
- Market context: BTC/ETH regime, breadth, sector breadth, volatility regime, risk-on/risk-off state.
- Historical analogues: empirical target-before-stop rates, MFE/MAE, time-to-target, sample size, calibration quality.

## 7. Continuation / explosion engine

Purpose: decide whether the observed move is likely to materially continue from the current state.

This is separate from entry quality.

Initial output contract:

- decision = FAIL | MONITOR | PASS
- score = 0..100 (score, not probability)
- evidence_quality
- supporting_factors
- vetoes
- exhaustion_state
- expected_move_band_atr
- expected_horizon

Later, after calibration, the engine may expose P(continuation event within horizon | current point-in-time state).

Candidate training label:

CONTINUATION_SUCCESS = `MFE >= K * ATR before MAE <= -S * ATR within H` minutes.

K, S, and H must be strategy-versioned and validated historically.

Hard vetoes:

- unusable evidence quality
- inadequate liquidity
- excessive spread/slippage
- ambiguous identity
- severe exhaustion
- major contradictory event evidence
- structural invalidation
- stale data

## 8. Entry engine

Purpose: even if continuation is likely, determine whether there is a high-quality entry now.

Outputs:

- decision = WAIT | PASS | VETO
- quality_score
- entry_low / entry_high / do_not_chase
- stop / invalidation
- target_1 / target_2 / target_3
- expected_holding_window
- expected_net_reward / expected_loss
- reward_to_risk
- target_before_stop_score
- exhaustion_risk

Primary calibration event:

SUCCESS = T1 reached before STOP within forecast horizon after a fill inside the approved entry zone.

Also retain T2/T3-before-stop, MFE, MAE, time-to-target, fee/slippage-adjusted outcome, and whether the entry remained available before the chase limit.

## 9. Global opportunity ranker

Passing a local setup is not enough. Every actionable candidate competes with every other actionable candidate across the eligible universe.

Rank by expected capital utility, not raw momentum.

Conceptual utility:

Opportunity Utility = success likelihood * expected net reward - failure likelihood * expected net loss - fees/slippage - liquidity penalty - exhaustion penalty - portfolio concentration penalty - opportunity-cost penalty.

Before calibrated probabilities exist, use a deterministic versioned score with the same feature families and preserve all raw evidence for later calibration.

Required outputs:

- global opportunity rank
- percentile
- expected capital efficiency
- alternatives outranking this candidate
- portfolio conflict flags

## 10. Capital allocation

Capital should work on the highest-quality available opportunity subject to risk constraints.

The allocator consumes opportunity quality, stop distance, expected return, liquidity/slippage, evidence confidence, calibration confidence, existing exposure, correlation, available capital, and opportunity rank.

The allocator must not treat an uncalibrated AI confidence percentage as win probability.

If a materially better opportunity appears while capital is occupied, O'Pip may issue an opportunity-cost advisory only when that creates a real decision. It must not churn positions because of small rank changes.

## 11. User-facing new-trade alert

Only emit after all of the following pass:

1. eligible-universe policy
2. evidence freshness/quality
3. continuation
4. entry
5. liquidity/slippage
6. exhaustion
7. target quality
8. economic quality
9. portfolio/capital
10. global opportunity threshold

Target payload:

🚀 O'PIP TRADE SIGNAL — XYZ/USD

Action: REVIEW ENTRY NOW
Entry Zone: ...
Do Not Chase Above: ...
Stop / Invalidation: ...
T1 / T2 / T3: ...
Expected Hold: ...
Expected Net R:R: ...
Opportunity Rank: #... / ...
Continuation: PASS
Entry Quality: HIGH
Liquidity: PASS
Exhaustion: LOW
Catalyst / Event: SUPPORTIVE | NONE | MIXED
Market Context: SUPPORTIVE | NEUTRAL | ADVERSE
Why: top supporting reasons
No order was placed or changed.

Until calibrated, use Continuation Score, Entry Quality Score, and Opportunity Score — never an unlabeled probability.

Once calibrated, a probability must name the event, horizon, sample size, and cohort, e.g. P(T1 before stop within 6h): 71% (calibrated, n=842, cohort=v3).

## 12. Existing Position Protection Engine

This is independent of discovery.

### 12.1 Source of truth

Kraken balances/open positions -> canonical exposure resolver -> attach lifecycle context when available -> monitor every verified exposure.

Required cases:

- Kraken holding + active registry: normal managed exposure.
- Kraken holding + no registry: UNMANAGED_VERIFIED_HOLDING; protection starts immediately with limited context.
- registry active + Kraken absent: lifecycle reconciliation / close or degraded state.
- Kraken unavailable: monitoring-degraded operational alert.

### 12.2 Position intelligence

Evaluate current P/L, fee-adjusted P/L, distance to stop/targets, momentum health, volume health, order-flow/whale changes, event/news changes, market regime changes, support/resistance, ATR-normalized deterioration, exhaustion/profit-protection conditions, and thesis delta from entry state.

Internal states:

- HEALTHY
- DETERIORATING
- RECOVERING
- PROFIT_PROTECT
- EXIT_REVIEW
- INVALIDATED
- UNMANAGED_VERIFIED_HOLDING
- MONITORING_DEGRADED

### 12.3 Alert semantics

No timer-based WARNING spam.

Alert when state or thesis changes materially, stop/structure risk escalates, profit-protection becomes actionable, target is reached, invalidation occurs, a protection dependency degrades materially, or a previously unmanaged Kraken holding is discovered.

Repeated urgent alerts are allowed only for unresolved actionable conditions such as INVALIDATED/EXIT_REVIEW, using bounded retry semantics.

Suppress an alert only when the state is unchanged and no monitored deterioration measure has crossed a versioned materiality threshold since the last delivered action. An unchanged DETERIORATING state must still alert when risk worsens materially.

## 13. Events and news integration

Events/news are first-class evidence, not a separate trade engine.

Each event carries canonical asset identity, source/provider, event timestamp, ingest timestamp, relevance, direction, source quality, revision/version, freshness, corroboration, contradiction flags, and market-confirmation state.

Rules:

- positive news alone cannot create a trade
- negative high-confidence event can veto or escalate risk
- event impact decays with age unless market response persists
- contradictory revisions supersede earlier assumptions
- event evidence must remain point-in-time for backtests
- the same event store feeds new-trade qualification and existing-position protection

## 14. Learning and calibration

Every observation should produce a canonical episode:

episode -> candidate -> feature snapshot -> continuation assessment -> entry assessment -> opportunity rank -> alert decision -> paper/real observed entry -> position path -> outcome -> calibration record.

Required metrics:

Discovery: recall of future Top-5/Top-10 movers, lead time, false discovery rate.
Signal: actionable alert precision, T1/T2-before-stop, expected vs realized R:R, MFE/MAE, time-to-target, false positives, missed opportunities.
Ranking: top-1/top-3/top-5 outcome quality, selection regret, capital utilization, opportunity-cost loss.
Protection: loss avoided, profit protection captured, false action alerts, missed deterioration, detection latency.
Calibration: Brier score, reliability curve, expected calibration error, sample count by cohort/regime.

## 15. Implementation mapping to current repository

Reuse where sound:

- app/opip/decision: identity, gate attribution, evidence/version discipline.
- app/opip/events: canonical event contracts and provider health.
- app/opip/streaming: evidence quality, CVD, liquidation, cross-venue primitives.
- app/services/price_movement_radar.py: useful compression/participation/derivative features; remove user-facing READY semantics.
- app/services/kraken_reconciliation.py: read-only exchange truth and lifecycle reconciliation.
- trade outcome registry / canonical episode capture: outcome joins.
- paper-trade and Freqtrade result ingest: learning evidence.
- Telegram delivery ledger / dedup infrastructure: transport observability.

Refactor or replace:

- local candidate confidence presented as pseudo-probability
- READY / EARLY WATCH user-facing lifecycle
- symbol-local qualification without global cross-sectional ranking
- active-trade protection that starts only from the local active registry
- fixed percentage material-change notification logic as final architecture
- notification state acting as decision state

Proposed new modules:

app/opip/opportunity/
  universe.py
  discovery.py
  queue.py
  features.py
  continuation.py
  entry.py
  ranker.py
  capital_utility.py
  models.py
  observer.py

app/opip/protection/
  exposure_resolver.py
  position_features.py
  thesis.py
  state_machine.py
  advisor.py
  models.py
  observer.py

app/opip/calibration/
  labels.py
  cohorts.py
  metrics.py
  probability.py

Do not create duplicate identity/event/streaming contracts; import the existing canonical models.

## 16. Migration plan

Phase 0 — architecture freeze and evidence baseline
- keep PR #169 unmerged
- capture current production metrics
- freeze current signal semantics for comparison
- define canonical success labels and cohort versioning

Phase 1 — unified monitoring queue in shadow
- broad universe discovery
- Top-15/rank velocity as one channel
- pre-breakout/volume/order-flow/event channels
- no Telegram
- persist point-in-time candidate episodes

Phase 2 — intelligence fusion + continuation model in shadow
- canonical feature snapshots
- deterministic continuation score
- exhaustion and evidence-quality vetoes
- evaluate recall/precision against future paths

Phase 3 — entry + target/stop model in shadow
- entry timing and chase veto
- ATR/structure-driven stops
- T1/T2/T3
- target-before-stop labels
- paper-trade every passing candidate

Phase 4 — global ranker + capital utility in shadow
- all candidates compete
- quantify selection regret
- compare old production alerts vs V2 top-ranked candidates

Phase 5 — Position Protection Engine
- Kraken-first exposure resolver
- attach lifecycle context
- action-driven state machine
- dual-run against existing active-trade monitor
- verify zero silent real holdings

Phase 6 — alert cutover
- disable EARLY WATCH Telegram
- emit only actionable NEW TRADE
- emit only actionable EXISTING TRADE ACTION
- preserve delivery observability and operational degradation alerts

Phase 7 — calibration
- convert scores to probabilities only after adequate sample size
- publish calibration cohort/version in alert payload
- continuously monitor calibration drift

## 17. Acceptance criteria

No V2 production cutover until:

- 100% of verified Kraken holdings are represented in protection or explicitly marked degraded
- no user-facing EARLY WATCH / READY informational alerts
- every NEW TRADE alert contains valid entry, stop, targets, and global rank
- every signal passes tradeability + exhaustion + continuation + entry + economic + portfolio gates
- every score/probability is correctly labeled
- candidate evaluation is point-in-time reproducible
- all alert decisions have terminal attribution
- Telegram failure is observable
- registry failure cannot silently disable protection
- discovery failure cannot disable position protection
- shadow comparison demonstrates materially better signal quality than current production baseline

## 18. Review questions

Reviewers must challenge, not merely approve:

1. Does this maximize actionable signal quality rather than alert count?
2. Is broad-universe discovery sufficient to avoid Top-15 tunnel vision?
3. Are continuation and entry correctly separated?
4. Can news/events/whale/order-flow evidence influence decisions without becoming fragile single-source triggers?
5. Is Kraken-first protection robust to registry drift?
6. Are labels free of look-ahead leakage?
7. Are fixed thresholds replaced by volatility/structure-aware rules where appropriate?
8. Can global ranking compare candidates with different volatility/liquidity?
9. Is capital allocation tied to calibrated expected utility rather than raw confidence?
10. What failure could still cause a high-quality trade to be missed or a poor trade to be alerted?
11. What is over-engineered and should be removed?
12. What is the smallest safe migration path from current production?