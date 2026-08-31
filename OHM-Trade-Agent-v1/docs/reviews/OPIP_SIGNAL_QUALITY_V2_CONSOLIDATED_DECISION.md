# O'Pip Signal Quality v2 — Consolidated Multi-Model Decision

Status: FINAL DESIGN DECISION FOR IMPLEMENTATION PLANNING — architecture only; no production behavior change

## 1. Final verdict

APPROVE WITH REQUIRED CHANGES.

The final Opus pass confirmed the core v2 thesis and exposed several production defects that change implementation priority. The governing objective remains: monitor broadly, rank globally, alert narrowly, protect existing holdings continuously.

## 2. Authoritative design decisions

### A. One synchronous TradeQualityAssessor, two logical sub-assessments

Continuation and Entry remain semantically separate because a move can be likely to continue while the current entry is poor. They must not run as independent asynchronous engines.

One immutable point-in-time FeatureSnapshot drives one synchronous pass:

FeatureSnapshot -> ContinuationAssessment -> EntryAssessment -> TradePlanAssessment

If evidence changes, recompute a fresh full snapshot. Never carry a stale continuation assessment into a later entry decision.

For continuation=PASS and entry=WAIT, place the candidate in a small bounded fast entry-watch set. Re-evaluate on a fresh full snapshot at roughly 60–120s cadence, with strict TTL and capacity limits.

### B. Deterministic global ranking before probability calibration

No probabilistic expected-value ranking before proper out-of-time calibration.

Initial Opportunity Utility is deterministic and versioned. Reward must be expressed in risk-normalized units rather than raw upside percent, and an explicit liquidity-capacity ceiling must prevent thin assets from winning because of theoretical move size.

Inputs include continuation quality, entry quality, target/net reward-to-risk, liquidity/slippage, exhaustion, evidence quality, regime, concentration and opportunity cost.

### C. Regime-aware normalization, not ATR-only

Use ATR/ATR percentile, realized-volatility percentile, BTC/ETH regime, sector/breadth context, liquidity/depth and asset-relative history. Replace regime-blind thresholds where they affect signal or protection quality. Operational safety thresholds may remain explicit fixed/versioned gates where appropriate.

### D. Kraken-first ExposureResolver is the sole authority for exposure existence

Kraken read-only account truth is primary. The resolver must enumerate Kraken balances/open positions first and left-join local lifecycle context.

Required states include VERIFIED_MANAGED, VERIFIED_UNMANAGED, ABSENT, UNKNOWN and DEGRADED.

Exactly one component owns terminalization after verified absence. Monitoring consumers do not independently close or infer exposure using conflicting rules. Kraken unavailable means UNKNOWN/DEGRADED, never flat.

### E. Position protection suppression is materiality-aware

Delete the earlier statement that a stable DETERIORATING position remains silent.

Correct rule:

Suppress only when (state unchanged) AND (no monitored deterioration measure crossed a versioned materiality threshold since the last delivered action).

Protection must detect slow deterioration through MFE giveback, time-under-water, capital holding time, relative-strength decay, regime change, liquidity degradation and structural deterioration. No fixed-frequency WARNING spam.

### F. Silent discovery remains; informational watch delivery disappears

Keep useful Early Watch/Broad Watch/price-movement detectors as internal evidence. Remove direct user-facing EARLY WATCH / READY / DEVELOPING / BROAD WATCH delivery at cutover.

All detectors feed the same bounded candidate queue and unified assessment/ranker.

### G. ActionDecision distinguishes rejected from undelivered

Target flow:

Decision -> ActionDecision -> NotificationPolicy -> Telegram transport

REJECTED/INVALIDATED is a decision outcome.

UNDELIVERED is a transport outcome. A qualified signal that fails tracking registration or Telegram delivery must remain live/retryable until invalidated/expired; it must not be terminalized merely because delivery failed.

Every drop path must produce telemetry.

Notification reservation must be atomic: reserve -> send -> confirm, or reserve -> send failure -> release.

### H. Capital/portfolio gating occurs before ActionDecision

Capital allocation and portfolio veto must be upstream of notification and downstream of global ranking.

If rank #1 is vetoed, evaluate/promote the next-best eligible candidate rather than leaving capital idle.

One canonical identity must join candidate decision, dedup, delivery and outcome records.

### I. Events/news/whales/order flow are contextual evidence

Positive news/events cannot independently create a trade.

Market confirmation must be directional and temporal, using evidence available after ingest time: price, volume, structure, order flow and liquidity.

Cross-venue evidence strengthens a setup when available but is not a universal hard gate.

FeatureSnapshot must distinguish NO_RELEVANT_EVENT from EVENT_PROVIDER_UNAVAILABLE, and more generally carry availability/quality flags for every evidence family. Missing data is not zero or neutral.

Historical replay must use event ingest time, not event timestamp. Revisions supersede going forward only; backtests must see the version actually known at each decision time.

### J. Bounded monitoring queue

The silent queue requires canonical identity, dedupe, TTL, priority, freshness, bounded retention, idempotent updates and explicit re-evaluation triggers. Alert-era cooldowns must not be reused as queue-retention logic.

### K. Multi-dimensional outcome labels

T1-before-stop is useful but insufficient. Persist T1/T2/T3-before-stop, MFE, MAE, realized fee/slippage-adjusted return, time-to-entry, time-to-target, time-under-water, MFE giveback, capital holding time, opportunity rank and discovery lead time.

Outcome classes may include EXPLOSIVE_WINNER, EFFICIENT_WINNER, SLOW_WINNER, FAILED_CONTINUATION, STOPPED_LOSER and EXPIRED_NO_ENTRY.

No probability language is allowed until point-in-time leakage controls, purged walk-forward validation and calibration pass. Label K/S/H parameters must be frozen by cohort before outcomes are generated.

## 3. P0 blockers

1. Kraken-first ExposureResolver and exactly one verified-absence terminalizer.
2. Materiality-aware deterioration protection; same state alone cannot suppress worsening risk.
3. ActionDecision must separate REJECTED from UNDELIVERED; transient tracking/Telegram failure cannot destroy a qualified opportunity.
4. Capital/portfolio gate moves upstream of ActionDecision with next-best promotion.
5. One synchronous same-snapshot TradeQualityAssessor; no stale continuation carry-forward.
6. Deterministic pre-calibration global ranking with risk-normalized reward and liquidity-capacity ceiling.
7. Regime-aware normalization where signal/protection thresholds are currently crude.
8. Evidence-family availability/quality flags; missing evidence is never treated as neutral/zero.
9. Positive event/news promotion requires market confirmation using point-in-time evidence.
10. Remove uncalibrated probability/confidence percentage rendering and enforce structurally.

## 4. P1 before broad cutover

1. Bounded silent candidate queue with TTL/priority/freshness/idempotency.
2. Discovery initially uses rank velocity and volume/participation anomaly; add other channels only after measured benefit.
3. Fast bounded entry-watch reevaluation for continuation=PASS, entry=WAIT.
4. Existing-position degraded-history fallback so assets with insufficient candles still receive price-vs-stop/target protection.
5. Remove monotonic max-merge behavior from price_movement_radar when reused; scores must be allowed to decay.
6. Make protection thresholds regime-aware, including relative-strength and volume deterioration.
7. Establish explicit alert priority so protection warnings do not accidentally consume the same noncritical budget as new trade actions.
8. Atomic notification reservation and materiality-bucketed fingerprints.
9. Event revisions/version validity and purged walk-forward embargo >= maximum outcome horizon.

## 5. P2 defer

- historical analogue scoring
- probabilistic EV
- full covariance optimizer
- advanced ML continuation probability
- broad module cleanup not tied to measurable outcomes
- premature calibration/probability module
- separate thesis subsystem until complexity earns it

## 6. Revised implementation waves

### Wave 0 — Production audit

Verify live KRAKEN_RECONCILIATION_ENABLED and KRAKEN_RECONCILIATION_MODE. Count pending setups terminalized as tracking_failed/send_failed. Enumerate Kraken balances/open positions and diff against the active registry. Verify actual deployed SHA and feature-flag inventory.

### Wave 1 — Position Protection

Kraken-primary ExposureResolver; VERIFIED_UNMANAGED holdings; one terminalizer; MFE giveback, time-under-water and relative-strength decay; materiality-aware suppression; insufficient-history fallback. Dual-run against current active monitor.

### Wave 2 — Action/Alert Plumbing

ActionDecision; rejected-vs-undelivered; retryable delivery; atomic NotificationPolicy; canonical identity; move capital/portfolio gate upstream; next-best promotion.

### Wave 3 — Silent Queue + FeatureSnapshot

Bounded queue, canonical immutable point-in-time FeatureSnapshot, per-family availability/quality flags. Start discovery channels with rank velocity and volume/participation anomaly only. No Telegram.

### Wave 4 — TradeQualityAssessor + Entry Watch

Continuation + Entry over one snapshot; bounded 60–120s fresh-snapshot reevaluation for PASS+WAIT candidates.

### Wave 5 — Deterministic Global Ranker

Risk-normalized Opportunity Utility plus liquidity-capacity ceiling, concentration/correlation proxy and opportunity cost. Shadow only; measure selection regret.

### Wave 6 — Alert Cutover

Only NEW TRADE SIGNAL and EXISTING TRADE ACTION remain user-facing. Direct informational watch transport is disabled.

### Wave 7 — Calibration

Purged out-of-time calibration and probability activation only after evidence gates pass.

## 7. Production acceptance gates

No V2 alert cutover until all are true:

- every verified Kraken holding is protected or explicitly UNKNOWN/DEGRADED
- a Kraken holding absent from the local registry is discovered
- slow deterioration and MFE giveback can trigger review without a sharp shock
- informational watch alerts cannot reach Telegram
- transient Telegram failure does not terminalize a qualified signal
- portfolio veto of rank #1 permits evaluation/promotion of rank #2
- one canonical identity joins decision, delivery and outcome
- continuation and entry use the same point-in-time snapshot
- high-ATR/thin-book candidates cannot dominate ranking purely because of raw expected upside
- positive news alone cannot promote a candidate
- missing provider evidence is distinguishable from measured neutral evidence
- no uncalibrated probability/confidence percentage can render
- new listings with insufficient indicator history still receive minimum protection
- shadow V2 improves actionable precision without unacceptable major-mover recall loss
- rollback does not strand candidate/position lifecycle state

## 8. Implementation principle

Do not optimize for architectural elegance. Every change must measurably improve at least one of: signal precision, major-mover recall, entry timing, capital efficiency, existing-position protection, calibration integrity or operational resilience. Otherwise defer it.