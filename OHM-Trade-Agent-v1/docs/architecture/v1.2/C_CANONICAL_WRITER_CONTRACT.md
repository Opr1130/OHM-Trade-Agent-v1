# C. Canonical writer / concurrency contract

PR 1 does **not** create the database, writer process, or IPC.

## v1 physical model

- One local SQLite WAL database on the production node.
- One canonical writer process is the only authority that commits operational domain events and projections.
- Intelligence computation, position protection, and canonical writing may run in separate processes.
- Protection independence means protection can evaluate and alert when discovery fails. Durable state still depends on writer health.

## Intent interface

- The writer accepts bounded intents over **local IPC**.
- That transport is a work path, not another evidence authority.
- Priority classes: **protection/execution > opportunity/forecast > telemetry**.
- Protection/execution intents receive reserved queue capacity.

## Acknowledgement and retry

- Acknowledge success only after durable commit.
- Producers retry unacknowledged intents using **stable idempotency keys**.
- Idempotency keys identify the actual operation (interval revision, detector transition, intent, fill, export). Timestamp alone is insufficient.

## Write-transaction prohibitions

Forbidden inside a write transaction:

- market calls
- feature computation
- export transfer
- AI

Long transactions are forbidden by contract.

## Provisional performance targets (N8)

| Metric | Provisional target | Status |
| --- | --- | --- |
| Writer transaction duration | p99 ≤ 50 ms | Measure and ratify in PR 2 |
| Protection-intent queue age | p99 ≤ **1 second** | Measure and ratify in PR 2 |

## Dashboard / read policy

Dashboard and reporting use read-only connections or rebuildable projections. They are not writers.

Projection rebuild into a fresh read model is a supported, tested operation at a named watermark (implemented in later PRs).

## Failure

Writer failure halts new reservations and simulated fills. Protection may continue to detect and alert but **must not claim an uncommitted exit**.

## Stress gate for physical split

Physical database splitting is **not authorized in v1**. A split is considered only after repeatable protection-deadline failures at the ratified workload, after transaction shortening, query isolation, and low-priority backpressure.

## PR 2 first transaction

The first production intent wrapped by this writer is the [alert-governor capture boundary](PR2_CAPTURE_BOUNDARY.md).
