# O'Pip Event Intelligence — Design and Architecture

Target architecture and staged build plan for the O'Pip event/market-intelligence
layer.

This document is **edge-led, not source-led**. Every stage exists to answer a
question the previous stage could not, and the programme is designed so that
"the evidence does not help, stop here" is a legitimate and cheap outcome.

Nothing in this programme grants trading authority. Every stage is measurement
only until an explicitly approved, separate build says otherwise.

---

## 0. Starting position (verified against the code, not assumed)

| Capability | Actual state |
| --- | --- |
| Kraken market data | working — execution and universe authority |
| CoinGecko / CryptoPanic / CoinMarketCal | working, with real identity-safety logic |
| Coinalyze (OI, funding, liquidations) | working — the only live derivatives provider |
| `market_intelligence_registry` | 12 provider specs, **1 implementation** |
| WebSocket / streaming | **none** — no `websockets`, `aiohttp`, or asyncio anywhere |
| Runtime model | cron `* * * * *` → `docker compose exec` → one-shot process → exit |
| Decision funnel | Build 1 — terminal attribution, versioning, shadow comparison |
| Outcome data | `mfe_pct` / `mae_pct` per trade; evidence state gated at 30 samples |

Two consequences shape everything below.

**There is no process that can hold a WebSocket.** Streaming is not a library
choice, it is a new compute class. It is therefore deferred to Stage 6, behind
evidence that it is worth having.

**The binding constraint today is arithmetic, not information.** The Build 1
funnel attributes zero-trade scans to `DETERMINISTIC_QUALITY` — target and
economic gates, on move size, cost and reward/risk. Evidence that arrives above
that gate changes nothing. Stage 8 is the first stage permitted to argue that a
new source would have flipped a decision, and it must argue it with the funnel's
own replay, not with intuition.

---

## 1. Architecture

### Layering

```
                        EXTERNAL SOURCES
   (existing REST)        (Stage 6 stream)      (Stage 7 reference)
   CryptoPanic             one news WS          Binance / Bybit REST
   CoinMarketCal
   Coinalyze / Kraken
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 ▼
                    O'Pip Event Contract  (Stage 1)
             normalize → two clocks → dedupe key → digest
                                 ▼
                    O'Pip Identity Resolver  (Stage 2)
              exchange symbol → verified anchor → learned
              registry → structured instrument → REFUSE
                                 ▼
                    O'Pip Event Store  (Stage 3)
          events.jsonl · unresolved.jsonl · dead_letter.jsonl
                                 ▼
              ┌──────────────────┴──────────────────┐
              ▼                                     ▼
   O'Pip Event Risk Shadow (Stage 4)     Outcome Attribution (Stage 8)
   records what it WOULD have said       event → episode → candidate
   no output path, ever                  → decision → outcome (MFE/MAE)
              │                                     │
              └──────────────────┬──────────────────┘
                                 ▼
                    Efficacy Gate  (Stage 9)
              lead time · discrimination · precision · flips
                                 ▼
              expand providers (10) → ML preparation (11)
```

The existing production path is untouched at every stage. This is the same seam
Build 1 established: observe beside the decision, never inside it.

### Where the code lives

```
app/opip/
  events/
    contract.py      OPipEvent, EventClass, EventIdentity, deterministic ids
    adapters/        one module per source; pure normalisation, no I/O policy
    store.py         thin wrapper over app/opip/storage.py
    ingest.py        batch ingestion driven by the existing cycle
    stream.py        Stage 6 only — the single long-lived consumer
  identity/
    resolver.py      the resolution ladder + refusal rules
    ladder.py        rung implementations, each independently testable
  risk/
    shadow.py        Event Risk Shadow — evaluates, records, returns nothing
  attribution/
    efficacy.py      event → outcome joins and efficacy statistics
  storage.py         generalised from app/opip/decision/store.py
```

`app/opip/decision/store.py` is **generalised into `app/opip/storage.py`**, not
copied. It already implements the writer lock, truncated-tail repair, `fsync`,
per-row dead-lettering and bounded retention that these streams need. A second
copy would drift.

### What this architecture deliberately does not add

No Kafka, NATS or Redis. No managed database. No second scheduler. No additional
droplet. The append-only JSONL + `registry_lock` + atomic-write + dead-letter
pattern already in the repository **is** the event bus, and it has already
survived the failure modes a broker would be bought to solve.

One new container is introduced, at Stage 6 only, inside the existing Compose
file.

---

## 2. Stage 0 — Infrastructure seam

**Goal:** make it possible to add sources without touching the decision path.

Two pieces, both pure refactors with no behavioural change:

1. **`app/opip/storage.py`** — lift the durable-JSONL helpers out of
   `app/opip/decision/store.py` (append-with-lock, tail repair, dead-letter,
   retention, tolerant read). `decision/store.py` becomes a thin caller. Proven
   by the existing Build 1 store tests continuing to pass unchanged.

2. **`EvidenceSource` protocol** — one shape for "something that produces
   events", modelled on the existing `MarketIntelligenceProvider` and
   `FlowEvidenceProvider` protocols rather than a new idiom:

   ```python
   class EvidenceSource(Protocol):
       name: str
       trust_tier: str
       def poll(self, *, since: datetime) -> Sequence[RawEvent]: ...
   ```

   Implementations fail closed into an empty sequence. Orchestration catches
   provider exceptions, exactly as `collect_market_intelligence` already does.

**Exit criterion:** Build 1 tests green with no edits; no new dependency in
`requirements.txt`.

---

## 3. Stage 1 — Canonical Event Contract

The load-bearing decision of the whole programme.

```python
SCHEMA_VERSION = 1

class EventClass(str, Enum):
    NEWS         # editorial / headline
    CATALYST     # scheduled, known in advance
    MACRO        # rate decision, print, policy
    LIQUIDATION  # forced-flow cluster
    FUNDING      # perp funding / OI regime
    FLOW         # on-chain or exchange flow
    GAP          # synthetic: ingestion lost coverage

class TrustTier(str, Enum):
    PRIMARY      # the issuing venue or agency itself
    AGGREGATOR   # a redistributor (CryptoPanic, Tree News)
    UNVERIFIED   # social, unattributed

@dataclass(frozen=True)
class OPipEvent:
    schema_version: int
    event_id: str                      # deterministic content hash
    dedupe_key: str                    # stable across reconnects and replays
    source: str
    source_event_id: str | None
    event_class: EventClass
    trust_tier: TrustTier

    source_event_time_utc: str         # when the world says it happened
    ingest_time_utc: str               # when O'Pip durably observed it
    observation_lag_seconds: float | None

    identity: EventIdentity            # see Stage 2
    headline: str | None               # short, attributable
    body_digest: str | None            # sha256 of body — never the body
    numeric: dict[str, float]          # normalised numeric payload
    warnings: tuple[str, ...]
```

### Four decisions worth defending

**Two clocks, always.** `source_event_time_utc` is what the world claims;
`ingest_time_utc` is when this system could actually have known. Every
downstream read filters on `ingest_time_utc <= decision_at`, which extends the
Build 1 invariant `max_feature_input_timestamp <= decision_timestamp` to a
streaming world. Using the source clock for that filter is the single easiest
way to build a backtest that cannot be traded.

**`observation_lag_seconds` is a first-class field, not diagnostics.** It is the
number that decides whether Stage 6 was worth building. A "breaking news" feed
that reaches us after the price has already moved is an expensive RSS reader,
and this field is how we find that out in weeks rather than never.

**Digest, not body.** Storing `sha256(body)` instead of the body keeps rows
small, keeps redistribution-restricted text out of a durable store, and still
supports exact dedupe. Headlines are retained because attribution requires them;
full articles are not.

**`GAP` is an event.** When the Stage 6 consumer reconnects, it writes a `GAP`
event covering the blind window. Analysis that silently treats a hole as "no
news" produces confidently wrong efficacy numbers.

`event_id` is a deterministic hash of `(source, source_event_id or dedupe basis,
source_event_time)`, following the existing `canonical_episode_id` /
`opip_candidate_id` idiom, so ingestion is idempotent and replay-safe.

---

## 4. Stage 2 — Identity Resolver

The place this programme most plausibly fails. It is a **correctness gate above
the feature layer**, not a feature.

```python
class IdentityStatus(str, Enum):
    RESOLVED_UNIQUE   # exactly one Kraken-tradeable asset, verified
    RESOLVED_MULTI    # several assets, each verified
    MARKET_WIDE       # macro / breadth, no single asset
    AMBIGUOUS         # candidates collide — REFUSED
    UNMAPPED          # outside the Kraken universe
```

### The ladder — first match wins, every rung records its evidence

1. **Exchange-native symbol** — `kraken_identity.canonicalize_pair` /
   `canonicalize_asset`. Handles the legacy codes (`XXBT`→`BTC`, `XXDG`→`DOGE`)
   and maps venue symbols (`BTCUSDT` perp) onto the Kraken spot universe.
2. **Verified reference anchor** — `ReferenceMarketValidation.mapping_status` in
   `{UNIQUE, PRICE_DISAMBIGUATED}` with a CoinGecko id *and* name.
3. **Learned display registry** — `asset_display_identity`, already populated by
   verified production identities.
4. **Structured instrument match** — the source's own instrument objects
   (CryptoPanic `instruments`), matched on id/slug/title, never on ticker.
5. **Refuse.**

### Rules that are not negotiable

- **Ticker-only attribution is refused.** This rule already exists in
  `news_context.py`; Stage 2 promotes it from one module to a shared service.
  Fuzzy and name-similarity matching are not rungs on this ladder.
- **`AMBIGUOUS` is terminal, not a fallback to a guess.** A misattributed
  headline does not raise; it produces a confident, plausible, wrong signal.
  That is strictly worse than no signal.
- **`MARKET_WIDE` is a real answer.** "SEC sues a major exchange" is not a BTC
  event; forcing it onto one asset manufactures precision.
- **Resolution is pure and versioned.** `resolver_version` is recorded on every
  event so the whole store can be re-resolved when the registry improves.
  Re-resolution is a batch job over stored events, never a silent mutation.

Unresolved events are appended to `identity_unresolved.jsonl`. That file is a
deliverable, not an error log: it is the ranked backlog of mappings worth adding.

---

## 5. Stage 3 — Event storage

```
/app/data/opip/events/
    events.jsonl              canonical resolved events
    identity_unresolved.jsonl AMBIGUOUS / UNMAPPED, for backlog work
    event_dead_letter.jsonl   rows that could not be serialised
    ingest_checkpoint.json    per-source high-water mark (atomic write)
```

Same durability contract as the Build 1 funnel: exclusive writer lock, truncated
tail repaired before appending, `fsync` on close, per-row dead-lettering so one
bad row cannot cost the batch, bounded retention via `compact_jsonl_recent`.

Sizing: an event row is roughly 0.8–1.2 KB (headline + digest + numerics, no
body). At a realistic few hundred events a day this is single-digit MiB a month.
Caps: 32 MiB / 100,000 lines for `events.jsonl`, 8 MiB / 20,000 for the
unresolved stream.

Dark by default behind `OPIP_EVENT_INGEST_ENABLED`, following the
`P1_SHADOW_OUTBOX_ENABLED` and `OPIP_FUNNEL_TELEMETRY_ENABLED` precedent.

---

## 6. Stage 4 — Event Risk Shadow

The direct answer to the `PROTECT` / `EXIT-REVIEW` authority question raised in
review: **measure the signal before granting it any power.**

For every resolved event the shadow evaluates, against open advisory/paper
positions and the current scan candidates, what it *would* have recommended:

```python
class EventRiskAction(str, Enum):
    NONE
    WOULD_AVOID          # would have suppressed a new entry
    WOULD_PROTECT        # would have tightened risk on an open position
    WOULD_EXIT_REVIEW    # would have raised a human exit review
```

It emits an `EventRiskAssessment` reusing the Build 1 `GateResult` shape —
reason code, reason class, measured value, threshold, `evaluated_at` — so event
reasoning is expressed in the same vocabulary as gate reasoning and joins the
same way.

**It has no output path.** Not to Telegram, not to the funnel's decision, not to
paper admission, not to any registry the live path reads. Structural tests
enforce this exactly as Build 1's decision-isolation tests do:

- the shadow module imports no notifier, no exchange module, no trade registry
  writer;
- a shadow forced to return `WOULD_EXIT_REVIEW` for every event changes no
  production output;
- disabling the whole event layer changes no deterministic gate result — the
  fail-closed requirement holds because nothing depends on it.

Its assessments are what Stage 8 scores. If they turn out to be noise, nothing
was ever at risk.

---

## 7. Stage 5 — Retrofit existing sources

`CryptoPanicEventAdapter` and `CoinMarketCalEventAdapter` wrap the output the
scan **already fetches** into `OPipEvent`. Coinalyze funding/OI/liquidation
snapshots become `FUNDING` / `LIQUIDATION` events the same way.

Zero new API calls, zero new credentials, zero new failure modes. This is the
cheapest possible proof that the contract, resolver and store hold on real,
messy production data — and it produces a backfillable event history before a
single new integration is bought.

**Exit criterion:** a week of production events with an identity-resolution rate
and an audited sample of `RESOLVED_UNIQUE` decisions. If precision on that
sample is poor, Stage 6 does not start; the resolver does.

---

## 8. Stage 6 — One streaming news source

The only stage that introduces long-lived compute, and the only one that needs
an explicit architecture approval.

**Shape:** one container, `opip-event-ingestor`, in the **existing** Compose
file, alongside the pattern already set by the Freqtrade paper sidecar
(`mem_limit`, `no-new-privileges`, no ports, read-only mounts where possible).
It is not a scheduler: it holds a socket and appends rows. The existing cron
cycle remains the only thing that schedules work.

**Responsibilities, deliberately minimal:**
- hold the WebSocket; normalise frames to `RawEvent`; resolve identity; append.
- bounded in-memory queue with drop-oldest and a dropped-count metric. Never
  block the socket; never grow without limit.
- reconnect with exponential backoff and jitter; **write a `GAP` event** for the
  blind window on every reconnect.
- write a heartbeat file. The cycle reads it; a stale heartbeat marks subsequent
  event evidence `DEGRADED` rather than pretending coverage was complete.

**New dependency:** one WebSocket client library, pinned. This is the first
addition to `requirements.txt` in the programme and should be reviewed as such.

**The measurement that justifies it:** the distribution of
`observation_lag_seconds`, and whether `source_event_time` leads the price
reaction the scan would have seen anyway. If the stream is not measurably ahead,
the correct outcome is to delete this container and keep the REST adapters. That
result is a success of the programme, not a failure of it.

---

## 9. Stage 7 — Binance / Bybit microstructure

REST-polled first, inside the existing cycle. No streaming until Stage 6 has
proven streaming pays.

Content: liquidation clusters, funding regime, open-interest acceleration,
aggressor imbalance — mapped to `LIQUIDATION` / `FUNDING` / `FLOW` classes. These
complement `native_flow_evidence`, which already derives CVD-style aggressor
imbalance and large-print concentration from Kraken alone.

**Hard invariant, enforced by test:** these venues are *reference evidence only*.
The trading venue is Kraken US retail (Bitnomial margin). No module under
`app/opip/events/adapters/` may be imported by any execution, order, or paper
admission path, and symbol mapping goes through the Identity Resolver's
exchange-native rung — never string manipulation at the call site.

---

## 10. Stage 8 — Outcome attribution

Where Build 1 pays off. The join key chain already exists:

```
event_id → identity.base_asset → episode_id → candidate_id
        → signal_id → paper_trade_id → outcome (mfe_pct / mae_pct)
```

Two distinct measurements, and the second is the important one:

**Traded attribution** — for events on assets that produced a decision, join
through the funnel and compare outcomes with and without the event present.
Accurate, but sample-starved: it inherits the < 30-outcome problem.

**Universe attribution** — for **every** resolved event, compute forward MFE/MAE
over fixed windows (15m / 1h / 4h / 24h) from `ingest_time_utc`, on the whole
observed universe, whether or not a trade happened.

Universe attribution is what makes this programme viable on the current data
volume. Event efficacy does not need trades: it needs prices, and there are
thousands of asset-hours a day. It also avoids the selection bias of scoring
only the events that happened to pass every gate.

`trade_outcome_registry` already computes MFE/MAE and its direction handling is
reused rather than reimplemented.

---

## 11. Stage 9 — The efficacy gate

An explicit promotion gate. **Failing it is a legitimate result and stops the
programme.** Four questions, each answerable from stored evidence:

| Question | Measure | Meaning if it fails |
| --- | --- | --- |
| Lead time | `source_event_time` → price reaction, vs. scan observation → reaction | the feed is not faster than what we already see |
| Discrimination | forward MFE/MAE distributions with vs. without an event | the signal carries no information |
| Identity precision | audited error rate on a `RESOLVED_UNIQUE` sample | the mapping is wrong; features are built on noise |
| Decision impact | replay the Build 1 funnel with event features; count terminal-state flips | evidence sits above the binding gate and changes nothing |

The fourth is the one this programme exists to answer, and it is the one the
original source-led design could not have asked. The Build 1 funnel already
records every candidate's terminal gate and distance from threshold, so the
replay is a query, not a new system.

Only a passing gate authorises Stage 10.

---

## 12. Stages 10–11 — Provider expansion and ML preparation

Providers are added in measured-efficacy order, filling the slots
`market_intelligence_registry` already declares (`fred`, `deribit_dvol`,
`whale_alert`, `glassnode`, `cryptoquant`, `santiment`, `lunarcrush`, `coinglass`,
`kaiko`, `amberdata`). `fred` and `deribit_dvol` are free and REST and are the
natural first two — they also prove the `EvidenceSource` protocol at zero cost
and could reasonably be pulled forward to Stage 5.

Cost note for the reviewer: `kaiko`, `amberdata`, `glassnode`, `cryptoquant` and
the Arkham/Nansen class are enterprise-tier commitments. They should be bought
against a measured efficacy number, never against a diagram.

ML remains out of scope until a validated feature set exists. The preparation is
already in place: `feature_schema_version` and `model_version` are declared and
null in the Build 1 version stamp, the join chain is complete, and the
point-in-time rule extends to events through `ingest_time_utc`. No ML dependency
enters the production image in this programme.

---

## 13. Cross-cutting requirements

**Safety.** Every stage is measurement-only. No module in `app/opip/events/`,
`identity/`, `risk/` or `attribution/` may import an exchange client, a notifier,
or a trade-registry writer — enforced by AST tests in the style of
`tests/test_opip_decision_safety_v1.py`. Deterministic gates must produce
identical results with the entire event layer disabled; external AI and external
evidence remain advisory and fail closed.

**Flags.** `OPIP_EVENT_INGEST_ENABLED`, `OPIP_EVENT_RISK_SHADOW_ENABLED`,
`OPIP_EVENT_STREAM_ENABLED` — each dark by default, each measurement-only,
following the established precedent.

**Cost.** Any AI escalation over events reuses `chief_runtime_guard`'s
fingerprint cache and daily call/token budget. Event-driven triggers scale with
volatility, which is exactly when they are most expensive; the existing guard
already solves this and must not be reinvented.

**Versioning.** Events carry `schema_version` and `resolver_version`;
assessments carry the Build 1 version stamp including `gate_policy_fingerprint`.
Any stored evidence can be partitioned by the exact policy that produced it.

**Performance.** Batch ingestion runs inside the existing cycle and must stay
within the same order as the Build 1 funnel's ~29 ms. The Stage 6 consumer is
bounded by `mem_limit` and a fixed queue.

---

## 14. Stage summary

| # | Stage | New infra | New dependency | Authority |
| --- | --- | --- | --- | --- |
| 0 | Infrastructure seam | none | none | none |
| 1 | Event contract | none | none | none |
| 2 | Identity resolver | none | none | none |
| 3 | Event storage | none | none | none |
| 4 | Event risk shadow | none | none | none |
| 5 | Retrofit existing sources | none | none | none |
| 6 | One streaming source | **1 container** | **1 WS client** | none |
| 7 | Binance / Bybit reference | none | none | none |
| 8 | Outcome attribution | none | none | none |
| 9 | Efficacy gate | none | none | none |
| 10 | Provider expansion | none | per provider | none |
| 11 | ML preparation | none | none | none |

Stages 0–5 add no infrastructure and no dependencies, and end with a real
identity-resolution measurement on production data. That is the cheapest point
at which this programme can be honestly cancelled.

## 15. Open questions for the production reviewer

1. Approval for one long-lived container at Stage 6, and for the WebSocket
   dependency it requires.
2. Choice of the single streaming news source, and its redistribution terms —
   headlines are retained in durable storage.
3. Whether `fred` and `deribit_dvol` should be pulled forward into Stage 5, since
   both are free, REST, and fit the current polling model.
4. Retention sizing for `events.jsonl` against the actual droplet volume.
5. Confirmation that Binance/Bybit reference data raises no jurisdictional issue
   given Kraken US retail execution — the design treats them as evidence-only,
   never as venues.
