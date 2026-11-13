# O'Pip Real-Time Market Intelligence — BUILD 4.1

## Purpose

Sequence 4 gives O'Pip cross-venue market-evidence infrastructure: streaming
trade and liquidation observations from public futures market data, reduced
into deterministic per-window aggregates (CVD, liquidation pressure, venue
agreement) with explicit, machine-readable evidence-quality metadata.

BUILD 4.1 is the contracts and pure-math layer only. It has no network
access, no persistent process, and produces nothing that any other O'Pip
system currently reads.

## Boundaries — what BUILD 4.1 is and is not

**Is:** deterministic dataclasses, enums, and pure functions under
`app/opip/streaming/`, plus their tests.

**Is not:** a live Binance/Bybit connection, a worker process, a Docker
service, a Telegram integration, a Sequence 3 Risk Shield input, or a Decision
Engine input. Nothing in `app/jobs/`, `app/services/`, or the existing
Sequence 2/3 modules was modified. Nothing here is scheduled or imported by
production code.

O'Pip's real-trade safety model is unchanged: no order placement, no order
modification, no order cancellation, no Kraken execution authority, no
automatic entry or exit, no real-position mutation. Sequence 4 evidence has
no authority over any of that, now or by design later — a future build would
have to *add* a consumer; nothing here reaches out to become one.

## Module layout

```
app/opip/streaming/
    __init__.py
    contract.py     canonical enums (provider, stream type, transport state,
                     sequence status, evidence-quality state, trade/liquidation
                     side, venue-agreement state, arrival decision)
    envelope.py      StreamEnvelope — the canonical, versioned, immutable
                     evidence record
    sequencing.py    provider-neutral SequenceTracker abstraction + 3 policies
    windows.py       grid-aligned WindowBounds + bounded WindowAccumulator +
                     closure/routing policy
    quality.py       EvidenceQuality model + fail-closed confirmation rule
    features.py      trade-side normalization, venue/cross-venue CVD,
                     liquidation aggregation and cross-venue synchronization
```

Six modules, each with one job. `contract.py` holds every enum so the other
five modules never need to agree on a type by convention — they import it.

## Reused rather than reinvented

- **Identity**: `MappingStatus` and its UNIQUE/AMBIGUOUS/UNKNOWN semantics
  come from `app.opip.events.contract`. Sequence 4 does not define a second
  asset-identity model. An envelope or trade/liquidation observation may only
  carry a `canonical_asset_id` when `identity_status == MappingStatus.UNIQUE`
  — enforced at construction, not by convention.
- **UTC discipline**: `require_utc` / `parse_utc` / `utc_iso` are the same
  helpers Sequence 2/3 use. Every timestamp field in this package is
  validated the same way.
- **Deterministic serialization**: `StreamEnvelope.canonical_bytes()` uses
  `app.opip.storage.bounded_jsonl.encode_row` — the same sorted-key, no-NaN
  JSON encoder Sequence 2/3 use for durable rows — so a future BUILD 4.2
  storage layer gets byte-identical determinism guarantees for free, without
  this build persisting anything itself.
- **Provider health**: deliberately *not* reused for transport lifecycle.
  `StreamTransportState` (DISCONNECTED/CONNECTING/CONNECTED/GAPPED/BACKOFF/
  OVERFLOW/STOPPED) is a new, separate enum. `ProviderHealthState` in
  `app.opip.events.provider_health` is untouched — a future build may derive
  one from the other plus evidence freshness, but they are never merged.

## The envelope

`StreamEnvelope` is a frozen dataclass covering provider, stream type,
provider symbol, both timestamps (provider and ingest, kept explicitly
distinct), connection id, reconnect epoch, sequence status and value, the
three derived boolean flags, aggregation flag, fail-closed identity, and a
free-form payload/quality dict pair (using the same `field(default_factory=
dict)` convention as `OPipEvent.source_metadata`, so no instance shares a
mutable default). Construction enforces that `gap_before`/`out_of_order`/
`duplicate` are the single fixed combination implied by `sequence_status` —
an envelope cannot claim `sequence_status=DUPLICATE` while `duplicate=False`.

## Sequencing design

A single global rule ("current ≠ previous + 1 ⇒ gap") is unsafe across
venues. Three trackers implement three distinct, testable policies behind one
`SequenceTracker` interface:

- **StrictIncrementingSequenceTracker** — for streams meant to increment by
  exactly one. Reports GAP with an explicit `gap_size`, DUPLICATE on exact
  repeat, OUT_OF_ORDER on anything smaller that isn't a repeat.
- **NonDecreasingSequenceTracker** — for streams where non-contiguous IDs are
  normal. Any increase is CONTIGUOUS regardless of size; this tracker never
  reports GAP, because gap size has no meaning under this policy.
- **NoSequenceTracker** — for streams with no usable sequence value at all.
  Always UNSUPPORTED.

Reconnect-epoch handling is shared: an epoch bump always produces
RESET_NEW_EPOCH and clears tracker memory, **never** GAP or OUT_OF_ORDER by
itself. A reconnect is not, on its own, evidence a message was lost.

## PIT / window design

`WindowBounds.for_timestamp` grid-aligns to the UTC epoch
(`floor(epoch_seconds / window_seconds) * window_seconds`), so the same
instant always maps to the same window regardless of arrival order — two
independently-started series agree on boundaries without coordination.

`WindowAccumulator` is a bounded, O(1)-per-window aggregate (counts and
first/last timestamps), never a raw event list — enforced by a dedicated
safety test checking the dataclass has no `raw_events`/`events`/
`observations` field. `.record()` is pure (returns a new instance) and raises
if the window is already sealed or the observation falls outside its bounds.
`.seal()` is idempotent. A late arrival after sealing is recorded via
`.record_late_frame()`, which bumps a counter without touching the sealed
aggregate's history — evidence, never mutation.

Closure is deliberately split into two independent decisions: `WindowBounds.
is_sealable(now_utc, grace_seconds)` uses the caller's local clock, never the
provider clock, so a provider clock anomaly cannot block closure; and
`route_observation()` decides only where an observation *belongs* (current
open window / a new window / late-against-a-sealed-window). Nothing in this
module reads the wall clock — every function takes time as an argument.

## Evidence-quality model

Four states — COMPLETE / DEGRADED / INCOMPLETE / UNUSABLE — with a frozen,
non-empty `degradations` reason set for anything short of COMPLETE (enforced
at construction: COMPLETE cannot carry reasons, and non-COMPLETE cannot lack
one). A sequence gap or heavy dropped-frame ratio marks the window INCOMPLETE
(evidence is missing); everything else measured against a threshold
(excessive out-of-order, late events) is DEGRADED (evidence is present but
imperfect). `combine_quality()` folds several verdicts to the worst one and
unions their reasons — used to combine per-venue quality into one cross-venue
verdict — and explicitly treats zero inputs as UNUSABLE, never COMPLETE.

`can_independently_confirm(quality)` is the one fail-closed gate later
components should call: true only for COMPLETE. This build does not wire that
gate into anything; it only makes the rule machine-readable.

Thresholds (`DEFAULT_MAX_OUT_OF_ORDER_RATIO`, `DEFAULT_MAX_LATE_FRAME_RATIO`,
`DEFAULT_MAX_DROPPED_FRAME_RATIO`, `DEFAULT_NEUTRAL_NOTIONAL_RATIO`) are
structural configuration defaults, not statistically calibrated values — no
historical Sequence 4 data exists yet to calibrate against.

## CVD design

`TradeObservation` carries a normalized `TradeSide` (BUY_AGGRESSOR /
SELL_AGGRESSOR / UNKNOWN). `normalize_trade_side()` maps only the literal
tokens `"BUY"`/`"SELL"` (case-insensitive); anything else — `None`, empty,
malformed — resolves to UNKNOWN. UNKNOWN is never inferred into a direction.

`VenueCvdState` tracks `signed_base_volume` and `signed_notional_usd`
separately, plus `gross_notional_usd` (for later ratio-based polarity checks)
and unknown-side volume/notional/count *excluded from the directional delta
but never discarded*.

Cross-venue combination (`combine_cross_venue`) is explicitly **not**
`binance_raw_volume + bybit_raw_volume`: combination happens in USD-notional
terms, because base-quantity conventions differ per venue/contract. The
result carries the combined notional, each venue's own contribution, and a
five-state `VenueAgreementState` (ALIGNED_POSITIVE / ALIGNED_NEGATIVE /
DISAGREEMENT / MIXED_NEUTRAL / INSUFFICIENT_EVIDENCE) computed only from
venues whose quality independently confirms (`can_independently_confirm`);
non-confirming venues still contribute to the combined notional figure (they
are evidence, just imperfect) but are named in `excluded_venues` and excluded
from the agreement verdict. Exactly one confirmable venue is
INSUFFICIENT_EVIDENCE for *agreement* by design — agreement is inherently a
multi-source concept; a single source's own reading is available directly
from `per_venue_signed_notional_usd` without an agreement claim being
manufactured for it.

## Liquidation design

`LiquidationObservation`/`LiquidationAggregate` mirror the trade-CVD shape:
long/short notional and base volume tracked separately, unknown-side
liquidations tracked and never discarded, `imbalance_notional_usd` derived
(long − short), and `venue_participation` as a bounded per-venue count.

`assess_liquidation_synchronization()` is deterministic and provider-neutral:
given a list of observations (already filtered by the caller for identity/
quality — this function reasons only about what it's given), it computes each
venue's earliest observation and classifies SYNCHRONIZED if at least
`min_venues` (default 2) distinct venues' earliest observations fall within
`window_seconds` of one another, NOT_SYNCHRONIZED otherwise, and
INSUFFICIENT_EVIDENCE if fewer than `min_venues` venues are represented at
all (including zero). No WebSocket topic name (`publicTrade.{symbol}`,
`allLiquidation.{symbol}`, or any Binance-specific stream name) appears
anywhere in this module — those are a future provider-adapter's concern.

## Identity safety across venues

`combinable_identity()` is the single gate: two venues combine only when both
sides report `MappingStatus.UNIQUE` and the same `canonical_asset_id` string.
Matching raw symbols, matching tickers, or "looks like the same asset" is
never sufficient — a dedicated test constructs two UNKNOWN-status
observations that happen to share the literal symbol `"BTC"` and asserts they
still cannot combine.

## Safety invariants (tested, not just asserted)

- No import of `app.exchanges`, any order-placement/cancellation/
  modification function, or any lifecycle-registry writer, anywhere under
  `app/opip/streaming/`.
- No ML library import (`pandas`, `numpy`, `sklearn`, `torch`, `tensorflow`,
  and the two LLM SDKs).
- No networking import (`websockets`, `aiohttp`, `socket`, `requests`,
  `httpx`).
- No Telegram/notification identifier anywhere in the package.
- No import of `app.opip.risk.observer`, `app.opip.risk.notifier`,
  `app.opip.risk.alert_state`, or a Decision Engine module — Sequence 4
  cannot become a hidden input to Sequence 3 or the Decision Engine by
  accident.
- No wall-clock read (`datetime.now(`, `utcnow(`, `time.time(`) in any of the
  six deterministic modules.
- `WindowAccumulator` carries no raw-event-list field, guarding the O(1)
  memory design against a future edit reintroducing unbounded buffering.
- No new networking dependency in `requirements.txt`.
- The repository-wide, pre-existing `test_opip_modules_reference_no_futures_
  surface` / `test_no_ml_dependency_is_introduced` safety tests (scoped to
  all of `app/opip`, predating Sequence 4) pass unmodified. `StreamProvider`
  is named `BINANCE`, not `BINANCE_FUTURES` as the architecture prompt's
  example JSON literally used — this was flagged as an open naming/scope
  question during implementation and explicitly reviewed and confirmed by
  the project owner (2026-08-29): keep `BINANCE`, make no change to the
  pre-existing safety test. This is settled, not an open item for BUILD 4.2.

## What BUILD 4.1 intentionally does NOT do

No live Binance/Bybit WebSocket client. No persistent worker or scheduler
integration. No Docker service. No `app/jobs/run_cycle.py` change (verified
byte-identical). No Sequence 3 or Decision Engine consumption. No Telegram
behavior. No production configuration or activation. No calibrated
thresholds — every numeric default here is a structural placeholder pending
real evidence.

## Next steps for BUILD 4.2

BUILD 4.2 adds the asynchronous worker: a real WebSocket client per venue
producing `StreamEnvelope` instances, driving `SequenceTracker`/
`WindowAccumulator`/`quality.assess_window_quality` on a real clock via
`route_observation` + `WindowBounds.is_sealable`, and persisting sealed
windows (a natural fit for the same `BoundedJsonlArchive` primitive Sequence
2/3 already use). BUILD 4.2 should not need to change any BUILD 4.1 contract
signature — only construct instances of them from live data. Backpressure
policy (when to call `record_dropped_frame()` under queue overflow) and the
transport-state → provider-health derivation mentioned above are BUILD 4.2
concerns, not resolved here.


## BUILD 4.1 independent-review remediation

Independent review before merge identified five deterministic-hardening issues
and they were remediated in BUILD 4.1 rather than deferred to the async worker:

- StreamEnvelope payload/quality are now recursively frozen at construction;
  canonical bytes cannot change if the caller mutates its original dict/list.
- NonDecreasingSequenceTracker treats repeated sequence values as valid
  CONTIGUOUS evidence, explicitly separating sequence continuity from message
  deduplication for Bybit-style repeated-sequence semantics.
- Trade/liquidation/CVD numeric inputs fail closed on NaN/Infinity.
- Cross-venue state/quality key mismatch degrades to INCOMPLETE evidence
  without accidental KeyError or false agreement.
- PIT grid alignment uses integer microseconds from the UTC epoch, eliminating
  float-boundary ambiguity.
- Liquidation synchronization is identity-safe and searches for an actual
  cross-venue time cluster rather than comparing only each venue's earliest
  event.

Reconnect boundaries and unsupported sequence capability remain conservative
DEGRADED evidence in V1 by design; BUILD 4.2 may add provider-capability-aware
metadata, but neither state may independently confirm evidence today.
