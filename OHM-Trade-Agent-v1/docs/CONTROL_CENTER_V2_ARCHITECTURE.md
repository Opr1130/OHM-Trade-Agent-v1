# O'Pip Operational Control Center V2 — analytical projection, configuration and retention

Increment: `ATDD-OPIP-CONTROL-CENTER-V2-M02`.

This document records what the Control Center reads, from where, and what it is
allowed to claim. It is deliberately explicit about the fields that do **not**
exist yet, because a missing field rendered as a plausible number is the defect
this increment exists to remove.

## 1. Boundaries

| Boundary | Rule |
| --- | --- |
| Canonical JSONL | Authoritative, append-only, never written by this increment. |
| `opip_analytics.db` | Derived, reconstructable SQLite projection. Disposable, not evidence. |
| Reader connections | `mode=ro` + `PRAGMA query_only=ON`, used by every dashboard read. |
| Writer connections | Only ingestion, reconciliation, retention and configuration staging. |
| Trading authority | Unchanged. No funded path, no F11 override, no scanner admission change. |
| Secrets | Never stored, returned, logged or rendered. `ai.api_key` is a reference only. |
| Retention deletion | Disabled by default; requires `OPIP_ENABLE_RETENTION_DELETE`. |

If the projection and canonical evidence disagree, the projection is reported
`DRIFT_DETECTED` or `UNVERIFIED`. SQL is never preferred silently.

## 2. Registered sources

Only these sources are read. An undeclared file is not evidence the console may
show.

| `source_id` | Canonical file | Kind | Written by |
| --- | --- | --- | --- |
| `scan_attempt_ledger` | `<OPIP_SCAN_STATE_DIR>/scan_attempts.jsonl` | JSONL event stream | core (`app/services/scan_attempt.py`) |
| `scoring_comparison_ledger` | `/app/data/opip/qualification/scoring_comparison.jsonl` | JSONL rows | promotion path (`app/services/technical_scoring_comparison.py`) |
| `scan_snapshots:<attempt>` | `<state_dir>/staging/<attempt>/snapshots.jsonl` | JSONL rows (manifest-verified) | isolated scan worker |
| `worker_status:<attempt>` | `<state_dir>/staging/<attempt>/worker_status.json` | JSON document | isolated scan worker |

A snapshot batch is ingested only after `manifest.json` exists **and** its
`snapshots.sha256` matches the file. Until then the source reports
`DEFERRED / ATTEMPT_IN_PROGRESS`.

## 3. Field mapping

### 3.1 Scan attempt events

| Canonical field | Parser | SQLite column | API property | UI widget |
| --- | --- | --- | --- | --- |
| `attempt_id` | `_project_attempt_event` | `scanner_attempt_events.attempt_id` | `attempts[].attempt_id` | Funnel card title |
| `seq` | `_project_attempt_event` | `event_seq` | `attempts[].events` | Funnel stage count |
| `event` | `_project_attempt_event` | `event_type` | `attempt_events[].event_type` | Forensics drawer |
| `state` | `_project_attempt_event` | `state` | `attempts[].state` | Funnel "attempt admitted" |
| `outcome` | `_project_attempt_event` | `outcome` | `attempts[].acquisition_outcome` | Funnel "acquisition outcome" |
| `reason_code` | `_project_attempt_event` | `reason_code` | `attempts[].reason_code` | Funnel detail |
| `fence` | `_project_attempt_event` | `fence` | provenance only | — |
| `at` / `created_at` | `_project_attempt_event` | `observed_at_utc` | `last_event_at_utc` | Funnel timestamp |
| record bytes | `sha256_bytes` | `record_sha256` | `record_sha256` | Forensics "Source record hash" |
| file offset | `ingest_source` | `byte_offset` | `byte_offset` | Forensics "Byte offset" |
| line ordinal | `ingest_source` | `line_number` | `line_number` | Forensics "Line number" |

### 3.2 Scanner snapshots (`MarketSnapshot`)

| Canonical field | SQLite column | API property | UI widget |
| --- | --- | --- | --- |
| `symbol` | `scanner_snapshots.symbol` | `rows[].symbol` | Signals table |
| `underlying_asset` | `underlying_asset` | `rows[].underlying_asset` | Forensics |
| `trade_direction` | `trade_direction` | `rows[].trade_direction` | Signals table |
| `movement_timeframe` | `timeframe` | `rows[].timeframe` | Signals table |
| `technical_score` | `technical_score` | `rows[].technical_score` | "V1 score" |
| `technical_score_version` | `technical_score_version` | `rows[].technical_score_version` | Forensics |
| `directional_long_score` | `directional_long_score` | `rows[].directional_long_score` | Forensics |
| `directional_short_score` | `directional_short_score` | `rows[].directional_short_score` | Forensics |
| `v2_long_score` | `v2_long_score` | `rows[].v2_long_score` | "V2 LONG" |
| `v2_short_score` | `v2_short_score` | `rows[].v2_short_score` | "V2 SHORT" |
| `v2_feature_status` | `v2_feature_status` | `rows[].v2_feature_status` | "V2 feature status" |
| `v2_profile_version` | `v2_profile_version` | `rows[].v2_profile_version` | "V2 profile" |
| `v2_profile_digest` | `v2_profile_digest` | `rows[].v2_profile_digest` | Forensics |
| `v2_long_components` | `scoring_components.points` (`LONG`) | `components[]` | Forensics rubric |
| `v2_short_components` | `scoring_components.points` (`SHORT`) | `components[]` | Forensics rubric |
| *(no per-symbol timestamp exists)* | `observed_at_utc` = manifest `started_at`, else file mtime | `rows[].observed_at_utc` | "Attempt time (UTC)" |

A score that is absent stays `NULL` end to end. `classify_signal` produces
`divergence` and `v2_score_delta` only when both scores exist.

### 3.3 Paired V1/V2 comparisons

| Canonical field | SQLite column | API property | UI widget |
| --- | --- | --- | --- |
| `scan_id` | `scoring_comparisons.scan_id` | `scan_id` | Overview comparison table |
| `model_authority` | `model_authority` | `model_authority` | "V2 production · V1 observation" |
| `v1_threshold` / `v2_threshold` | same | same | Divergence classification thresholds |
| `scanned` / `v2_ready` | same | same | KPI "V2 features ready" |
| `v1_top` / `v2_top` | `*_json` | `v1_top` / `v2_top` | KPI "V1 shortlist" / "V2 shortlist" |
| `v1_only` / `v2_only` | `*_json` | `v1_only` / `v2_only` | Attribution card (counts only) |
| `outcome_status` | `outcome_status` | `outcome_status` | "Outcome not matured" |

`v1_only` is a directional disagreement, **not** an avoided loss.

### 3.4 Worker scoring provenance

`worker_status.json` is projected into `operational_telemetry` with
`section='worker_status'`, keyed by attempt and document hash, so
`production_scoring_version`, `v2_evaluation_enabled`,
`v2_fields_in_snapshot_schema` and `v2_status_counts` are readable without
re-reading the staging tree. Every distinct document version is appended; an
older version is never overwritten.

## 4. Ingestion, reconciliation and retention invariants

| Invariant | Where it is enforced |
| --- | --- |
| A partial trailing line is never ingested and never advances the offset | `ingest.ingest_source` |
| Record + checkpoint commit in one `BEGIN IMMEDIATE` transaction | `ingest.ingest_source` / `store.write_transaction` |
| Replay is a duplicate, not a second row | `ingested_records` primary key `(source, generation, byte_offset)` |
| An existing verified row is never overwritten | `INSERT OR IGNORE` in `ingest.insert_row` |
| A replaced or truncated file starts a new generation | `ingest.ingest_source` (`SOURCE_IDENTITY_CHANGED` / `SOURCE_TRUNCATED`) |
| A malformed record never yields a clean source | `quarantined_records` + `reconcile` → `UNVERIFIED` |
| An authorized purge is coverage, not drift | `retention_coverage` added to the expected count in `reconcile` |
| The newest record per source is retained | `retention._TIP_GUARD` |
| Deletion without authority is refused | `retention.housekeeping` (`allow_delete=False`) |
| Absence of a run is `UNVERIFIED` | `reconcile.latest_status` |

## 5. Configuration model

| Layer | Source | Mutable from the console |
| --- | --- | --- |
| Definitions | `app/opip/config/registry.py` (code), mirrored to `config_definitions` | No |
| Staged versions | `config_versions` (`STAGED` / `SUPERSEDED`) | Yes (staging only) |
| Effective values | `app/core/strategy_policy.json` or `active.json`, plus deployment env | No |
| Activation / rollback | `/api/opip/strategy-admin/activate` and `/rollback` (dual credential) | Existing authority only |

Staging is inert: the API returns `live_effect: false`, and `/api/opip/control-center/config`
reads effective values from their live source, never from `config_versions`.

## 6. What is intentionally unavailable

| Metric | Status | Reason |
| --- | --- | --- |
| Qualified / rejected candidates | `UNAVAILABLE` | No qualification-event source is registered. |
| Promotion result | `UNAVAILABLE` | No promotion-event source is registered. |
| Avoided losses / missed winners / agreement rate | `OUTCOME NOT MATURED` | Requires matured, comparable outcome evidence. |
| Win rate / expectancy / average win-loss | `INSUFFICIENT DATA` | Same. |
| AI win rate, false-signal rate, calibration | `INSUFFICIENT DATA` | No inference source; ordinal confidence is not a probability. |
| F11 protection state | `UNVERIFIED` | No persisted protection artifact (`OPIP_F11_STATE_PATH`). |
| Learning Droplet CPU/RAM | `UNAVAILABLE` | No cross-host telemetry channel. |
| Deployed revision | `UNAVAILABLE` unless `OPIP_DEPLOYED_SHA` is set | No release marker read on this host. |
| Reclaimed bytes | `null` | Freed WAL pages are not returned to the filesystem. |

Each of these is rendered as a labelled state, never as `0` or a plausible
default.

## 7. Deployment status and blockers

| Item | Status |
| --- | --- |
| Python modules, schema, registry, API | Implemented and unit/integration tested. |
| UI (header, ribbon, workspaces, drawer, admin console) | Implemented; `npm run build` passes. |
| Edge allowlist for the new routes | Updated in `deploy/control-center/nginx/v2.conf`; **not deployed**. |
| Edge deployment | Blocked on the owner-gated UI-only release controller (exact `main` SHA). |
| Projection location | Configurable; defaults to the trading-host data volume. Moving it to the approved Learning Droplet needs that host's inventory and permissions verified first. |
| Maintenance scheduling | Not scheduled in Compose (changes the trading-host runtime shape); CLI entrypoint exists: `python -m app.jobs.opip_analytics_maintenance`. |
| Retention deletion | Disabled. Remains dry-run until separately authorized. |
| Approved logo asset | **Installed.** `control-center-v2/public/assets/opip-control-center-logo-transparent.png` — 1120×1635 RGBA PNG, transparent exterior, SHA-256 `08db86b0…6595`. Served at `/dashboard/v2/assets/…`; a missing file still degrades to the explicit missing-asset mark, never a substitute logo. |
| Deployment sequencing | The new API and projection live in the **core** application, so a UI-only edge release alone is insufficient: a core release must land first, then the edge overlay. See §8. |

## 8. Deployment sequencing (confirmed, not authorized here)

| Layer | Artifact | How it becomes live |
| --- | --- | --- |
| Analytics + config API | `app/api/control_center.py`, `app/opip/analytics/*`, `app/opip/config/*`, `app/core/config.py`, `app/main.py` | **Core release** — owner-gated `deploy-production` at one exact `main` SHA (pytest gate, lock, health, rollback). |
| Edge routes | `deploy/control-center/nginx/v2.conf` | **UI-only release controller** — builds the Vite bundle and recreates only `ohm-dashboard-edge`. |
| Static UI | `control-center-v2/**` | Same UI-only controller. |

Consequence: this increment is **not** a UI-only change. Shipping the UI before the
core API exists would render every new workspace `UNAVAILABLE`. The correct order is
core → edge. Neither is authorized by this work.

## 9. Missing sources — explicit inventory

No metric below is computed, approximated or inferred. Each row names the source
that would have to exist before the metric could become `MEASURED`.

| Metric / surface | Current state | Missing source | Path it would register as |
| --- | --- | --- | --- |
| AI inference rows, latency, agreement, calibration | `NO_EVIDENCE` | an append-only AI inference/dead-letter event stream written by the advisory stage | `<data>/opip/ai/inferences.jsonl` (proposed; parser + tests required) |
| Qualified / rejected candidates | `UNAVAILABLE` | the qualification decision stream from the screening stage | `/app/data/opip/qualification/screening_evaluations.jsonl` (already written by other stages) |
| Promotion result per attempt | `UNAVAILABLE` | a promotion decision event (accepted / rejected + reason) | evidence directory promotion claim (`scan_attempt` evidence tree) |
| Outcome maturity, avoided losses, missed winners, win rate, expectancy | `OUTCOME NOT MATURED` / `INSUFFICIENT DATA` | matured outcome observations joined to a signal identity | forward-outcome artifact from the outcome/maturation job |
| AI confidence as a probability | `INSUFFICIENT DATA` | a documented probabilistic calibration contract plus labelled outcomes | — (no contract exists) |
| Learning Droplet CPU / RAM | `UNAVAILABLE` | a cross-host telemetry publisher | `OPIP_LEARNING_TELEMETRY_PATH` |
| F11 protection state | `UNVERIFIED` | a persisted protection verdict | `OPIP_F11_STATE_PATH` |

Registering any of these is a separate reviewed change: a parser, an ingestion
source entry, a reconciliation rule and tests. Until then the corresponding widget
keeps stating the limitation, and the derived KPIs stay unverified.


