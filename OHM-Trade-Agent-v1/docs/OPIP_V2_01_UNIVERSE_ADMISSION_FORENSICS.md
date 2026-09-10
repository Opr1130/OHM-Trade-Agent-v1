# O'Pip Profit Discovery V2-01 — Universe Outcome & Admission Forensics

**MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY**

This build records, with point-in-time evidence, how many future high-quality
opportunities the current discovery/admission boundary never admits into the
production shortlist. It does not rank, alert, paper-enroll, or trade.

## Objective

Preserve the production control arm:

`broad universe → directional technical score → threshold → capped shortlist → downstream intelligence/gates`

and add reconstructable evidence for every eligible instrument the Broad Search
scan evaluated.

## Architecture

V2-01 extends existing Stage-0 / screening machinery rather than adding a
parallel record type.

| Plane | What V2-01 writes | Authority |
| --- | --- | --- |
| Production scan (fail-open) | Enriched `ScreeningEvaluation` rows + scan `compute_envelope` | None. Selector, thresholds, alerts unchanged. |
| Learning worker (offline) | Joinable forward outcomes, `DISCOVERY_OUTCOME_V1` labels, exclusive Stage-0 attribution, earliness metrics | None. Labels cannot enter decision metadata. |

Production capture is gated by the existing `OPIP_FUNNEL_TELEMETRY_ENABLED`
flag. A telemetry/storage exception is logged and **cannot** reject or reorder
a production candidate.

Forward outcomes are matured by `app.jobs.build_discovery_forward_outcomes`
inside the existing learning-worker opportunity-intelligence cycle. They are
not scheduled on the trading host.

## Schemas

### Decision-time (screening)

- `ScreeningEvaluation.schema_version` remains 2.
- Additive metadata: `v2_01_admission_schema_version = 1`.
- Reuses `Stage0DecisionFeatures`, `CoarseRankContext`, `ScoreComponents`.
- Join key: `metadata.observation_id` =
  `OBS:` + SHA-256(`scan_id|scanner_type|venue_instrument_id|observed_at`)[:24]
  plus the existing `identity_tuple`.

`production_admission_result` is one of:

- `BELOW_THRESHOLD`
- `RANKED_OUTSIDE_BUDGET` (persisted screening outcome `COARSE_RANK_LIMIT`)
- `ADMITTED` (persisted screening outcome `ADVANCED`)
- `DATA_UNAVAILABLE`
- `EXCLUDED_MARKET`

Threshold rejection and rank/cap rejection are never collapsed.

### Offline labels

- `opip/discovery/forward_outcomes.jsonl` — schema 1
- `opip/discovery/attributions.jsonl` — taxonomy `DISCOVERY_ATTRIBUTION_V1`

These files are **not** written into `ScreeningEvaluation.metadata`.

## Join identities

Use existing O'Pip identity (`ResolvedInstrumentIdentity`). Do not invent a
second symbol scheme.

Join decision rows to labels by `observation_id` (preferred) or
`(observed_at, scan_id, scanner_type, venue_instrument_id)`.

## Outcome definition — `DISCOVERY_OUTCOME_V1`

Evaluation label only. Zero trade authority.

Horizons: 1h, 4h, 12h. Primary label uses 12h.

Directional MFE/MAE:

- LONG favorable = price up; SHORT favorable = price down.
- Incomplete horizons stay `None`, never a fabricated 0% return.

Barriers (constants in `app/opip/discovery/constants.py`):

- favorable = `max(3.0%, 1.5 × ATR%)`
- adverse = `-max(2.0%, 1.0 × ATR%)`
- if ATR% is unavailable, the percent floors are used (`PERCENT_FLOOR`)

`WINNER` if the favorable barrier is reached at or before the adverse barrier.
`NON_WINNER` if the horizon is complete (or already invalidated).
`INCOMPLETE` otherwise.

Raw MFE/MAE/barrier timestamps are stored so a later definition can be
recomputed without destroying evidence.

## Attribution rules

Exclusive, deterministic, replayable, versioned. One Stage-0 category per
observation:

| Category | Meaning |
| --- | --- |
| `NOT_OBSERVED` | No screening row exists for the demanded instrument |
| `DATA_UNAVAILABLE` | Observed but analysis/data failed |
| `EXCLUDED_MARKET` | Non-spot / excluded instrument class |
| `BELOW_THRESHOLD` | Neither directional technical score cleared `MIN_TECHNICAL_SCORE` (80) |
| `RANKED_OUTSIDE_BUDGET` | Threshold cleared; mixed cap of 8 and/or per-direction cap of 5 excluded it |
| `ADMITTED` | Present in the production shortlist |

A later economic, AI, execution, or Telegram rejection is **not** a discovery
miss. Admitted stays `ADMITTED`. V2-02+ will extend this taxonomy.

## Point-in-time safeguards

- Decision metadata is passed through `assert_point_in_time_safe`.
- Forward markers (`mfe`, `mae`, `outcome`, `future`, …) are forbidden on
  decision-time payloads.
- Outcome math uses `SymbolTimeline` windows `(t, t+H]` and
  `has_forward_observation` / `has_complete_window`.
- Unavailable features remain `None` and are listed in `unavailable_features`.
  Movement-radar defaults (for example ATR percentile 100) are not copied when
  `movement_data_status == UNAVAILABLE`.

Timestamp roles are explicit: `observed_at` / `decision_at` on screening rows;
`reference_at` and horizon timestamps on labels; `labeled_at` is compute time.

## Storage and retention

- Screening continues to use bounded `screening_evaluations.jsonl` (HOT +
  checksummed archive). p95 row size raised to 2 KB for the richer payload.
- Discovery labels use bounded JSONL under `/app/data/opip/discovery/` on the
  learning worker (100k-line / 64 MB HOT cap, archive-before-delete).
- Production files remain the WAL. No Kafka, Redis, or new database.

### Storage estimate (200–300 instruments every 5 minutes)

- 288 scans/day × 250 instruments × ~2 KB ≈ **140 MB/day** screening.
- 14-day recovery window ≈ **2.0 GB** screening before compression, plus
  existing funnel/summary streams.
- Forward-outcome rows are similar in size but written only on the learning
  worker for matured observations (~same order of magnitude once 12h windows
  complete).

## Compute overhead

Per scan, fail-open:

- one observational callback copy per analyzed instrument
- rank/threshold reclassification after the selector returns
- two cheap RSS/CPU samples (`/proc` or `resource`; unavailable on some
  Windows hosts and recorded as `UNAVAILABLE`)

This must not add a monitoring dependency and must not block the scan.

## Feature flags

- Capture: existing `OPIP_FUNNEL_TELEMETRY_ENABLED` (dark by default).
- No new production flag. No live-funded authority.

## Tests

`tests/test_opip_v2_01_universe_admission_forensics.py` covers the V2-01
contract: unchanged `MIN_TECHNICAL_SCORE` / `MAX_CANDIDATES`, shortlist parity,
fail-open telemetry, threshold vs rank-cap, LONG/SHORT MFE/MAE, incomplete
horizons, exclusive attribution, and a 197-market synthetic replay.

## Known limitations

- Universe construction still drops stablecoin/fiat/suffix markets before the
  Broad Search callback. Those names are `NOT_OBSERVED` unless a later winner
  join asks for them; V2-01 does not persist the entire Kraken catalog.
- Cross-scan earliness (first observation vs first admission vs favorable
  print) requires historical screening rows. A single isolated scan records
  those metrics as unavailable rather than inventing prior state.
- Forward prices join on `venue_instrument_id` / observation symbol. Alias
  mismatches between `full_market_observations.jsonl` and screening identity
  leave that observation unlabeled (`NO_FORWARD_DATA`).
- Offline maturation reads observations through `decision_time + 13h`
  (`DISCOVERY_FORWARD_READ_GRACE`). Decision time itself is not the window end.
- `DISCOVERY_OUTCOME_V1` is an evaluation prior, not a calibrated probability
  model.

## How V2-02 will consume this evidence

V2-02 should read `observation_id` joins only:

1. Decision-time features + admission result (no future columns).
2. Offline `DISCOVERY_OUTCOME_V1` / raw MFE-MAE as labels.
3. Exclusive Stage-0 attribution as the recall denominator.

It must not feed those labels back into `MIN_TECHNICAL_SCORE`, shortlist caps,
Chief routing, or alerts without a separate promotion-gated contract.

**Do not begin V2-02 in this PR.**
