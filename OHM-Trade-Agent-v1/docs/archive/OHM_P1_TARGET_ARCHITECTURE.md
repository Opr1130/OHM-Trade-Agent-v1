# OHM P1 Intelligence Program — Reconciled Target Architecture

Status: design baseline for P1 research/shadow work.
Production baseline: `ee1fcc8fe4fd21bc3e447319958af3d33639f645`.
Umbrella issue: #88.

## 1. Architectural decision

P1 is implemented as three physically and logically separated paths:

1. **Live production path** — existing Phase 1 scoring, mover discovery, Telegram, PendingSetup, and execution-safety semantics. No P1 network calls or P1 ranking logic may execute before alert-critical work completes.
2. **Shadow evidence path** — receives an immutable `LiveScanSnapshot` after alert-critical work. External OHLC/catalyst enrichment, Phase 3B structure/chase, Phase 3D shadow composition, trade-setup research, and cross-coin research run here only.
3. **Offline validation path** — Phase 3C reads immutable evidence, joins forward outcomes point-in-time, deduplicates episodes, and produces evidence reports. It has no live write path back into ranking or execution.

This preserves spot-only, advisory-only, human-confirmed operation and ensures research failures cannot change production decisions.

## 2. Durable outbox instead of an in-process event bus

P1 v1 will **not** introduce Kafka, Redis, a paid queue, or an in-process background thread in the live scanner.

After alert-critical work completes, the live scan may append one small `LiveScanSnapshot` record to a local durable outbox using the existing locked append-only storage pattern. This operation contains no external API calls and no P1 computation.

A separate shadow worker consumes the outbox asynchronously. The worker is not a second market scanner: it never discovers markets independently and may process only snapshots emitted by the live scanner. If it is down or slow, production alerts continue unchanged and backlog remains observable.

Future migration to a queue is allowed only if throughput or durability measurements justify it.

## 3. Canonical contracts

### LiveScanSnapshot

Immutable decision-time record emitted after Phase 1 completes.

Required fields:
- `schema_version`
- `snapshot_id` — deterministic unique identifier, e.g. hash of scan identity + decision timestamp
- `decision_at_utc`
- `symbol`
- `reference_price`
- Phase 1 stage/pattern/scores/components
- liquidity, persistence, exhaustion, candidate rank, universe size
- `suppressed`
- source/exchange identity for the reference observation

Rules:
- `decision_at_utc` is required and timezone-aware.
- No P1 evaluator may substitute `datetime.now()` for decision time.
- Existing Phase 1 output is not mutated when the snapshot is emitted.

### MarketDataQuery

Exchange-agnostic read contract:
- canonical symbol
- interval
- `end_at_utc` (required; normally equal to snapshot decision time)
- bounded lookback

Adapters may translate Kraken aliases internally. Intelligence layers must not contain Kraken/XBT/XDG pair-format logic.

### MarketDataSlice

Returns:
- exchange/source
- canonical symbol
- interval
- requested end time
- observed/fetched time
- completed bars only
- source quality/status

Every bar used by an evaluator must satisfy its completion time `<= end_at_utc`.

### CatalystContext

Context-only record:
- `catalyst_id`
- canonical symbol/entity IDs
- source
- source URL/reference where available
- event time when known
- publication time
- first observed/ingested time
- classification/category
- text/headline fingerprint
- source/classification confidence metadata

Point-in-time rule for a live snapshot join:
- publication/event information must have been publicly available no later than `decision_at_utc`.
- a story ingested after the decision must not be retroactively attached as information known at the decision.

### P1EvidenceEvent

Append-only immutable evidence keyed by `snapshot_id` and evaluator version. Contains Phase 3B structure/chase, Phase 3D shadow result, setup result, catalyst context references, and cross-coin research fields. Missing components are explicit statuses, never fabricated defaults.

### ShadowRankEvent

Research-only rank result:
- snapshot cohort ID / decision time
- production rank
- shadow rank
- ordinal shadow score
- component-family contributions
- evaluator version
- `advisory_only=true`
- `affects_production_rank=false`
- `affects_telegram=false`
- `affects_execution=false`

No shadow score is a probability.

### AdvisoryTradeSetup

Spot-long research/advisory geometry only:
- preferred entry zone
- trigger/retest preference
- invalidation
- target levels
- R multiples
- expected timeframe bucket
- setup status and reasons
- source structure levels and decision timestamp

No short, leverage, perpetual, order, cancel, confirm, or position-modification authority.

## 4. Timestamp and no-lookahead invariants

All P1 evaluation APIs require an explicit `decision_at`/`as_of`; production evaluators do not provide a default wall clock.

- Market bars: close time `<= decision_at`.
- Confirmed pivots: all left/right confirmation bars close `<= decision_at`.
- Catalysts: public availability time `<= decision_at` for any point-in-time join.
- Forward returns/MFE/MAE: available only in Phase 3C/offline outcome tables and prohibited from live/shadow feature inputs.
- Fetch/ingest time is lineage metadata, not decision time.

Tests must prove delayed fetching cannot change the point-in-time feature set.

## 5. Service boundaries

Recommended modules (names may be adjusted to existing repository conventions):

- `p1/contracts.py` — immutable schemas only.
- `p1/outbox.py` — locked append-only snapshot emission/acknowledgement; no market APIs.
- `p1/shadow_worker.py` — consumes snapshots after alerts; fail-soft orchestration.
- `market_data/base.py` — exchange-agnostic interface.
- `market_data/kraken_adapter.py` — Kraken REST/pair aliases/time-bounded OHLC.
- `market_data/cache.py` — bounded completed-bar cache, keyed by source/symbol/interval/end bucket.
- `catalyst/contracts.py`, `catalyst/adapters/*`, `catalyst/dedup.py` — context tier only.
- `phase3c/*` — episode join, outcome metrics, chronological split, bootstrap, ablations, reports.
- `phase3d/*` — shadow composition using only features approved by 3C gates.
- `trade_setup/*` — deterministic point-in-time spot-long geometry.
- `cross_coin/*` — within-scan research ranking and rank diagnostics.

## 6. Catalyst isolation firewall

Catalyst data is structurally excluded from Phase 3D v1 score input. The v1 ranker accepts a typed `ValidatedFeatureSet` that contains only feature IDs approved by Phase 3C governance.

Catalyst records may be displayed in research output and evaluated offline. They cannot be converted to a rank contribution until a later catalyst-specific validation review explicitly promotes a named catalyst feature/version.

This is stronger than relying on a runtime numeric-weight check: the ranker input schema simply does not contain catalyst fields until approved.

## 7. Phase 3C as promotion authority

Phase 3C produces signed evidence artifacts, not production configuration. It evaluates:
- MoveEpisode-level independent samples
- first detection / predeclared episode anchor
- strict chronological train/validation/test or walk-forward with untouched final holdout
- 15m/30m/60m/4h returns
- 24h MFE/MAE and time-to-extreme
- rank/liquidity/regime/structure/chase strata
- component ablations and correlation matrix
- uncertainty intervals
- comparison with Phase 1 baseline

No component becomes eligible for Phase 3D merely because it looks useful in exploratory data.

## 8. Phase 3D composition rules

- validated components only
- simple/interpretable v1 composition; no complex ML initially
- normalize using predeclared robust/percentile transformations
- group correlated inputs into conceptual families (momentum, run-up/chase, structure, liquidity/quality)
- cap family contribution to reduce double counting
- shadow score remains ordinal, never probability
- production rank remains the baseline reference and is not replaced

Rolling smoothing, PCA/factor models, and ML are experiments only and are not part of v1 unless evidence later justifies them.

## 9. Cross-coin ranking rules

V1 runs in research/shadow only.

- compare candidates from the same decision cohort
- use robust within-cohort percentiles/normalization where appropriate
- preserve hard liquidity safety rules rather than using normalization to rescue illiquid names
- report precision@k, NDCG, top-k overlap, Spearman rank correlation, rank stability, and forward-return comparisons against Phase 1
- do not smooth ranks by default; evaluate smoothing as a separate experiment because smoothing can delay fast-moving signals

## 10. Trade setup rules

All levels must be reproducible from data available at the decision time. Confirmed swings/breakout/retest state may be used; future MFE/MAE/returns may never set entry, invalidation, target, or timeframe.

Every setup result records its input version and source levels so replay can prove identical historical computation.

## 11. Failure isolation

- live alert path never waits for catalyst/OHLC/3C/3D/setup/cross-coin work
- outbox append failure is logged and fail-soft; it cannot suppress alerts
- shadow worker may skip/mark unavailable individual enrichments and continue
- all external clients have bounded timeouts/retries in the shadow worker
- cache failure falls back to bounded source fetch or explicit unavailable status
- telemetry/storage failures never propagate into PendingSetup or execution

## 12. Observability

Track at minimum:
- outbox depth/oldest age/write failures
- shadow worker latency and failure counts
- OHLC fetch p50/p90/p99 and timeout rate
- OHLC/history coverage and insufficient-history rate
- catalyst ingestion lag/dedup rate/source coverage
- Phase 3B top-8 cohort coverage
- evidence join success/missingness
- feature correlation matrix
- Phase 1 vs shadow rank Spearman/top-k overlap
- forward outcome metrics by rank/liquidity/regime

## 13. Exchange extensibility

Core intelligence consumes canonical symbols and `MarketDataSlice`; it cannot call Kraken-specific pair formatting. Kraken remains the first and only required market-data adapter for P1 v1. Coinbase support is deferred until Kraken-first evidence and operational stability justify a second adapter.

## 14. What is deliberately not built yet

- no Kafka/Redis/event-bus infrastructure
- no paid catalyst/news API requirement
- no real-time catalyst score in Phase 3D
- no complex ML model
- no automatic threshold tuning
- no autonomous orders
- no shorts/leverage/perpetuals
- no replacement of production Phase 1 ranking
- no Telegram semantics change
- no PendingSetup behavior change

## 15. Integration order

1. canonical contracts + durable outbox + evidence ledger
2. Phase 3C validation harness and governance gates
3. market-data adapter/cache refinement and catalyst context adapters in shadow
4. Phase 3D shadow composition using only approved feature versions
5. deterministic trade setup evaluation
6. cross-coin shadow comparison
7. separate evidence review before any limited live advisory overlay

Every later live-influence step requires a separate reviewed exact SHA and explicit production approval.