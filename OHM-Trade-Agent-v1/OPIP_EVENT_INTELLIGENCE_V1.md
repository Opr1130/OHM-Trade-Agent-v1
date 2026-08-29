# O'Pip Event Intelligence Foundation v1

## Status

Sequence 2 foundation. Shadow / evidence-only. Dark by default with
`OPIP_EVENT_STORE_ENABLED=false`.

This layer cannot qualify, rank, notify, admit paper trades, or execute orders.
The existing production decision path does not read the event store in v1.

## Source roles

| Source | Sequence 2 role |
|---|---|
| CryptoPanic | Discrete NEWS evidence |
| CoinMarketCal | Discrete CATALYST evidence |
| CoinGecko | External identity backbone; not an event source |
| Coinalyze | Continuous market-state evidence; deliberately not forced into the discrete event store |

Coinalyze remains on its existing market-intelligence seam. Turning snapshots
into events would require inventing change thresholds or manufacturing rows on
every scan, both of which are outside this foundation.

## Runtime architecture

```text
Existing unified cycle
        |
        +--> exchange reconciliation / real risk protection
        +--> pending protection
        +--> Early Watch
        +--> paper monitor
        |
        +--> O'Pip Event Intelligence (shadow, own bounded cadence)
                |
                +--> point-in-time-safe known identity catalog
                |
                +--> CryptoPanic / CoinMarketCal clients
                |
                +--> provider normalizers
                |
                +--> canonical OPipEvent
                |
                +--> durable EventStore
                         |
                         +--> bounded HOT JSONL
                         +--> verified immutable gzip archives
                         +--> point-in-time query / deterministic replay
        |
        +--> existing broad discovery path (unchanged)
```

Event capture therefore does not depend on the current trading finalist list.
It is also scheduled only through the existing unified cycle: Sequence 2 adds
no second scheduler, queue, service, Redis, Kafka, managed database, or host.

The event observer runs only after existing real lifecycle protection. Provider
latency or storage failure cannot delay active-position protection.

## Temporal contract

O'Pip separates the provider/world clock from the knowledge clock:

- `source_event_time_utc`: time claimed by the provider for the event.
- `ingest_time_utc`: conservative time O'Pip completed receipt of the
  provider payload.
- `normalized_at_utc`: time canonical normalization completed.
- `persisted_at_utc`: timestamp stamped only on a row that is being durably
  appended to the canonical store.
- `decision_visible_at_utc`: canonical visibility boundary.

For a persisted event:

```text
decision_visible_at_utc =
    max(ingest_time_utc, normalized_at_utc, persisted_at_utc)
```

A row that failed persistence does not exist in the canonical store and cannot
be returned by a point-in-time query. Knowledge timestamps are UTC and
monotonic: normalization cannot precede ingestion and persistence cannot
precede normalization.

Historical queries use `decision_visible_at_utc <= decision_at`. Provider
publication/event time alone never makes evidence historically visible.

## Identity contract

Ticker text is never sufficient evidence of identity.

Canonical mapping states are:

- `UNIQUE`
- `AMBIGUOUS`
- `UNKNOWN`

The existing externally verified identity registry remains the identity
backbone. Sequence 2 adds `learned_at_utc` when a verified identity is learned
or safely revalidated.

An identity mapping may be used for event normalization only when its knowledge
timestamp proves:

```text
identity_learned_at_utc <= event ingest/capture time
```

Legacy identity rows with no trustworthy learning timestamp are not
retroactively treated as historically known. They become eligible only after a
current identity-safe validation relearns them with a timestamp.

AMBIGUOUS and UNKNOWN events are retained as evidence, but canonical asset
queries refuse to attach them merely by matching ticker text.

CoinMarketCal's existing mapping cache already records `resolved_at`; only
mappings resolved by the capture time are eligible.

## Event identity and revisions

No fuzzy/text-similarity deduplication is used.

The canonical model uses:

- provider
- provider event ID when available
- event class
- asset identity key
- deterministic dedupe key
- canonical sanitized payload hash

An exact repeated payload has the same `event_id` and is a duplicate.

The same dedupe key with changed content is a new append-only revision and
records `revision_of`. Previous evidence is never silently overwritten.

Logical deduplication/revision lineage includes verified archives as well as
HOT storage.

## Canonical event record

The v1 record includes:

- `event_id`, `dedupe_key`, `schema_version`, `normalizer_version`
- provider and provider event ID
- event class (NEWS or CATALYST)
- payload hash
- the complete temporal contract
- source identity and canonical identity state
- identity knowledge timestamp and provenance
- headline / summary
- safe source reference
- sanitized provider-specific metadata
- bounded numeric evidence
- warnings
- expiry
- revision lineage

No provider credential or API secret is stored in canonical events or dead
letters.

## Storage lifecycle

HOT:

`/app/data/opip/events/events.jsonl`

Dead letter:

`/app/data/opip/events/event_dead_letter.jsonl`

Archive:

`/app/data/opip/events/archive/events-*.jsonl.gz`

HOT is bounded at 32 MiB / 100,000 lines. When a bound is crossed, retention
is archive-before-compact:

1. select oldest HOT rows,
2. write a temporary gzip archive,
3. compute checksum,
4. reopen and parse every archived canonical row,
5. verify row count,
6. atomically finalize archive and checksum,
7. only then atomically compact HOT.

If archive creation, checksum, verification, or HOT replacement fails, HOT
evidence is not intentionally deleted. Replay de-duplicates by `event_id` so
a crash after archive finalization but before HOT compaction remains safe.

Archives are included in deterministic replay and point-in-time reads. Local
archive lifecycle beyond this foundation is a later storage-maturity concern;
canonical history is not silently purged in Sequence 2.

## Failure isolation and observability

Expected provider/API failures, malformed timestamps, normalization failures
and storage failures are measured and fail soft.

Per-capture telemetry includes:

- received / normalized / persisted
- duplicates / revisions
- malformed
- UNIQUE / AMBIGUOUS / UNKNOWN mapping counts
- stale
- provider / normalization / storage errors
- provider request counts
- min / mean / max NEWS ingestion lag

Malformed evidence is dead-lettered without storing raw secrets.

## Production compatibility

The existing finalist news/catalyst pipeline remains unchanged in Sequence 2.
The event store is not a source for current ranking or signal admission.

This intentionally permits duplicate provider requests while the new path is
shadow-only. Removing that duplication is a later promotion step after
equivalence, coverage and latency evidence prove that canonical event evidence
can safely replace the current finalist-oriented fetch path.

## Promotion prerequisites

Before the event layer is allowed to influence production decisions:

1. sustained shadow ingestion evidence,
2. provider coverage and failure-rate measurement,
3. point-in-time replay verification,
4. identity ambiguity review,
5. latency/freshness analysis,
6. storage growth and archive restore verification,
7. explicit O'Pip Decision Engine integration design,
8. independent review and human production approval.

Sequence 2 itself grants no decision or execution authority.
