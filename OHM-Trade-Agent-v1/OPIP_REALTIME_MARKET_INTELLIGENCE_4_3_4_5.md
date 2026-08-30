# O'Pip Sequence 4 — BUILD 4.3 through BUILD 4.5

Sequence 4 completes the production-shadow real-time market intelligence
pipeline while preserving a strict evidence-only boundary.

## BUILD 4.3 — Binance public derivatives

The Binance adapter uses the current USDⓈ-M public combined WebSocket endpoint:

- wss://fstream.binance.com/public/stream

Initial reviewed instruments:

- BTCUSDT → Bitcoin
- ETHUSDT → Ethereum
- SOLUSDT → Solana

Subscriptions:

- aggregate trades
- force-order liquidation evidence

Aggregate-trade aggressor side is derived from Binance's buyer-maker flag.
Liquidation side is derived from the liquidation order direction.
Numeric evidence must be finite and positive. Unknown instruments retain
UNKNOWN identity and cannot participate in asset-specific features.

## BUILD 4.4 — Bybit + cross-venue intelligence

The Bybit adapter uses:

- wss://stream.bybit.com/v5/public/linear

Subscriptions:

- publicTrade.{symbol}
- allLiquidation.{symbol}

A Bybit public-trade message may contain many trade records. The adapter
splits the message into bounded single observations. Repeated sequence values
are valid; trade IDs, not the sequence value, are used as bounded dedupe
identity.

The cross-venue accumulator uses USD notional rather than naïve raw-volume
addition. It calculates:

- per-venue signed CVD notional
- combined signed CVD notional
- venue agreement/disagreement
- liquidation notional imbalance
- observed liquidation synchronization

Liquidation streams do not carry continuity sequences that can establish
complete evidence. Therefore liquidation synchronization is explicitly marked
non-confirming and DEGRADED when present. It remains useful measurement
evidence but cannot independently confirm a future decision.

## BUILD 4.5 — Production shadow

The production worker is a physically separate Compose service:

- opip-stream-worker

It has:

- no ports
- no main application .env
- no private exchange credentials
- no Telegram credentials
- no OpenAI credentials
- no Kraken access
- no order/trading imports
- 0.40 CPU ceiling
- 256 MiB memory ceiling
- 128 PID ceiling
- all Linux capabilities dropped
- read-only root filesystem
- read-only general data mount
- write access only to data/opip/streaming

The application receives the streaming directory as a read-only nested mount.

## Backpressure and PIT integrity

The raw ingress queue is bounded at 5000 and uses DROP_RAW_NEWEST.
Previously accepted evidence is never evicted.

Any queue drop is attributed conservatively to matching windows and removes
COMPLETE quality. Any late observation after seal does not enter the feature
accumulator. Provider timestamps too far in the future fail closed. A missing
historical window is never recreated after its seal boundary.

Window quality is finalized only after the sealed-window retention interval so
late evidence can degrade the immutable aggregate before publication.

## Persistence

Raw trades remain memory-only.

Only aggregate shadow evidence is persisted:

- hourly features-YYYYMMDDTHH.jsonl
- latest_features.json
- telemetry.json
- health.json

Feature JSONL retention is 72 hours.

## Liveness

Provider connections are supervised with bounded exponential reconnect backoff.

A heartbeat is considered successful only after provider acknowledgement.
Data consumed while waiting for a Bybit pong is buffered and delivered through
the normal receive path. A quiet but responsive connection remains connected.

Reconnect epochs are explicit sequence boundaries and never silently bridge
continuity across sockets.

## Deployment

The existing production deployment wrapper executes the target checkout's
scheduler reconciliation hook. BUILD 4.5 chains a stream-worker reconciler
through that trusted hook, allowing the first worker deployment to be atomic
even when the installed wrapper predates BUILD 4.5.

The deployment succeeds only when:

1. exact approved SHA equals current main;
2. canonical CI for that SHA is successful;
3. main O'Pip health is OK;
4. opip-stream-worker builds and starts;
5. Docker reports the stream worker healthy;
6. the worker's own healthcheck succeeds.

Failure triggers the existing outer rollback. Resetting to the previous Compose
definition with --remove-orphans removes the new worker.

## Soak gate

Production activation starts the shadow soak; it does not promote streaming
into Decision Engine or Risk Shield policy.

Required soak before any later influence:

- 24+ hours
- no impact to unified_cycle
- no memory growth trend
- p95 CPU below the worker ceiling
- aggregate persistence healthy
- raw frame drop ideally 0%
- <0.1% raw drop considered healthy
- >1% raw drop for three consecutive hours is a scale-out trigger
- no asset expansion before the initial BTC/ETH/SOL soak succeeds

No Sequence 4 evidence can place, modify, cancel, or close an order.
