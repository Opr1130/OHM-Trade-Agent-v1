# O'Pip Sequence 4 — BUILD 4.2 Runtime Foundation

BUILD 4.2 adds the isolated asynchronous runtime foundation for future
real-time public market-data adapters.

It does not add a live provider implementation and does not activate a
production streaming service.

## Runtime topology

StreamingRuntime owns:

- one bounded FIFO ingress queue
- one consumer task that owns mutable PIT window state
- one supervisor task per configured provider adapter
- an optional lightweight resource-monitor task
- immutable telemetry snapshots

Provider adapters own only provider-specific transport and normalization
semantics: connection URL, subscription payload, heartbeat wire format, frame
parsing and provider-specific sequencing.

## Backpressure

The canonical queue policy is DROP_RAW_NEWEST.

Previously accepted frames are never evicted to make room for a new frame.
When capacity is exhausted, the incoming frame is rejected and telemetry is
incremented. This preserves ordering and PIT meaning for accepted evidence.

Default queue capacity: 5000.

## Lifecycle and reconnect

Each provider supervisor owns a reconnect epoch. Every reconnect increments the
epoch and creates a deterministic connection ID. Frames from an older epoch are
rejected before normalization so a stale connection cannot contaminate the
replacement session.

Reconnect delay uses bounded exponential backoff with injected jitter semantics.
Tests can provide deterministic jitter.

## Liveness

The provider-neutral liveness framework waits for normal frames. On the
heartbeat interval timeout, the runtime invokes the adapter heartbeat hook and
waits for one heartbeat timeout interval. Failure to receive data then ends the
connection and enters supervised reconnect/backoff.

BUILD 4.3/4.4 will provide the actual provider heartbeat protocol.

## Point-in-time windows

BUILD 4.2 reuses BUILD 4.1 WindowBounds, WindowAccumulator and evidence-quality
contracts.

Provider timestamp determines semantic window membership.
Runtime UTC time determines physical seal eligibility.

The runtime holds bounded current/recent windows only, never raw trade history.
Sealed windows are retained briefly for late-frame classification and then
evicted.

Default windows: 1 second and 15 seconds.

## Resource discipline

The runtime is designed for the current small production host.

Soft telemetry guards:

- memory target: 150 MiB
- CPU fraction: 0.40
- event-loop lag: 250 ms
- queue utilization: 90%

Soft limits degrade telemetry only. They do not create a trading action or
terminate the process based on one sample.

The future container ceiling remains 256 MiB and belongs to the production
shadow activation build.

## Shutdown

Shutdown stops accepting new frames, cancels provider supervisors, gives the
state-owning consumer a bounded opportunity to drain accepted frames, records
any remaining shutdown drops, then cancels remaining owned tasks.

No long-lived task is intentionally created without runtime ownership.

## Dependencies

BUILD 4.2 declares:

- orjson
- websockets

No live network client imports are present in BUILD 4.2 runtime modules.
These dependencies prepare the provider adapters planned for later builds.

## Safety boundary

BUILD 4.2:

- does not alter the unified cycle
- does not add a production compose service
- does not consume or influence the Decision Engine
- does not consume or influence Sequence 3 protection policy
- does not send outbound trading actions
- does not add private exchange access
- does not place, modify, cancel or close positions
- does not add ML
- does not deploy itself

BUILD 4.3 remains the first provider-adapter build.
