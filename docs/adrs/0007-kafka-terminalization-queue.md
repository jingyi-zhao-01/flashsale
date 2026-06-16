# ADR 0007: Use Kafka for Reservation Terminalization Queue

- Status: Proposed
- Date: 2026-06-16
- Related issues:
  - [#6 Prove queue/worker safety under retry, crash, and duplicate delivery](https://github.com/jingyi-zhao-01/flashsale/issues/6)
  - [#8 Build a measured hotspot capacity model with TPS and pool sweeps](https://github.com/jingyi-zhao-01/flashsale/issues/8)
  - [#10 Build observability and runbooks for hotspot and failure diagnosis](https://github.com/jingyi-zhao-01/flashsale/issues/10)

## Context

ADR 0002 moved reservation terminalization out of the synchronous order path. The
current implementation uses a Postgres-backed durable task table:

- `order_service.order_terminalization_tasks`
- `order_service.order_terminalization_task_events`
- `FOR UPDATE SKIP LOCKED` for worker claims
- `queued`, `processing`, `retrying`, and `succeeded` task states

That implementation is useful as a correctness baseline because it keeps order
creation and task enqueue inside one database transaction. It also makes queue
state queryable with SQL.

However, the current milestone work is explicitly about queue semantics under
retry, crash, duplicate delivery, and observability. Kafka is a better fit for
that next phase because it gives us a real broker boundary with explicit
consumer groups, offsets, replay, topic lag, and dead-letter topics.

The goal is not to make Kafka provide exactly-once business execution. Kafka
will provide durable at-least-once command delivery. Business correctness still
comes from idempotent state transitions and terminalization handlers.

## Decision

Move the terminalization queue backend from the Postgres task table to Kafka.

The target architecture keeps Postgres as the source of truth for orders and
uses Kafka as the durable command stream for reservation terminalization.

### Topics

Use three initial topics:

| Topic | Purpose |
|---|---|
| `flashsale.order.terminalization.v1` | Primary command topic for `confirm` and `cancel` reservation terminalization |
| `flashsale.order.terminalization.retry.v1` | Delayed retry topic for retryable failures |
| `flashsale.order.terminalization.dlq.v1` | Dead-letter topic for poison tasks after max attempts |

### Message Key

Use `reservation_id` as the Kafka message key.

This preserves per-reservation ordering while still allowing parallelism across
different reservations. It also keeps duplicate `confirm` or `cancel` commands
for the same reservation on the same partition.

### Message Payload

Each message should include:

```json
{
  "event_id": "uuid",
  "order_id": 123,
  "reservation_id": 456,
  "action": "confirm",
  "attempt": 1,
  "created_at": "2026-06-16T00:00:00Z",
  "idempotency_key": "terminalize:456:confirm"
}
```

`event_id` deduplicates producer-side retries. `idempotency_key` expresses the
business operation. Consumers must treat both as advisory fences, not as a
replacement for durable state checks.

## Producer Boundary

Order creation must not publish to Kafka before the order row is durably
committed.

Use an outbox pattern:

1. `order-service` creates the `orders` row and a terminalization outbox row in
   one Postgres transaction.
2. A Kafka publisher process reads unpublished outbox rows.
3. The publisher writes terminalization commands to Kafka.
4. The publisher marks the outbox row as published after Kafka acknowledges the
   send.

This avoids the classic split-brain cases:

- order committed but Kafka publish lost
- Kafka publish succeeds but order transaction rolls back

The existing Postgres task table can be evolved into this outbox during the
migration, or a dedicated `order_terminalization_outbox` table can be added.

## Consumer Semantics

Kafka gives at-least-once delivery. The worker must assume that any message can
be delivered more than once.

The consumer flow is:

1. Read a terminalization command from Kafka.
2. Start a database transaction.
3. Check whether the order and reservation operation is still valid.
4. If already terminalized, commit the Kafka offset and return.
5. Call `product-service` `confirm` or `cancel`.
6. Update the order/task state in Postgres.
7. Commit the database transaction.
8. Commit the Kafka offset after the business state is durably updated.

Offset commit timing matters:

- Commit too early, and a worker crash can lose the command.
- Commit too late, and the command can be replayed.

The design accepts replay and makes replay safe through idempotent state
transitions.

## Retry And DLQ

Retryable failures move to `flashsale.order.terminalization.retry.v1` with:

- incremented attempt count
- last error class
- next visible time or retry delay metadata

After `max_attempts`, publish the command to
`flashsale.order.terminalization.dlq.v1` and mark the related terminalization
record as failed/dead-lettered in Postgres.

DLQ is required because infinite retry hides poison tasks and makes queue lag
hard to reason about.

## Idempotency Requirements

Kafka does not remove the need for application idempotency.

The worker must remain safe when:

- the same Kafka message is delivered twice
- producer retry creates two messages with the same `event_id`
- `product-service` terminalization succeeds but the worker crashes before
  committing its offset
- the worker succeeds in Postgres but crashes before committing the offset
- `confirm` and `cancel` commands race for the same reservation

The product reservation API should keep `confirm` and `cancel` idempotent.
The order state machine should reject illegal terminal transitions.

## Observability

Kafka adds broker-level signals that should be added to Grafana:

- consumer group lag by topic and partition
- oldest unprocessed message age
- messages produced/sec and consumed/sec
- retry topic depth
- DLQ message count
- consumer rebalance count
- consumer processing latency
- product terminalization latency
- duplicate message discard count
- illegal transition rejection count

Queue health should be diagnosed by comparing:

- enqueue rate
- consume rate
- terminalization service time
- consumer lag
- retry/DLQ rate

## Migration Plan

1. Keep the Postgres-backed queue as the baseline.
2. Add Kafka topics and producer/consumer configuration.
3. Introduce a terminalization outbox table or adapt the current task table as
   the outbox source.
4. Add a Kafka publisher that drains unpublished outbox rows.
5. Add a Kafka consumer worker behind a feature flag.
6. Run both paths in shadow mode and compare:
   - produced command count
   - consumed command count
   - terminalized reservation count
   - duplicate discard count
   - final order status distribution
7. Switch the active terminalization worker to Kafka after correctness and lag
   dashboards are stable.
8. Retire the Postgres task claim path after the Kafka path passes issue #6
   crash/retry/duplicate-delivery experiments.

## Consequences

Benefits:

- clearer queue semantics for retry, replay, and consumer lag
- better fit for multi-worker fanout and event replay experiments
- broker-native observability for backlog and consumer health
- cleaner path to partitioned processing by reservation or tenant key

Trade-offs:

- more infrastructure to operate
- order creation now needs an outbox publisher to avoid dual-write loss
- exactly-once business behavior still requires idempotent state transitions
- tests and runbooks must cover Kafka outage, consumer lag, retry, and DLQ

## Diagram

D2 source: [0007-kafka-terminalization-queue.d2](diagrams/0007-kafka-terminalization-queue.d2)

Rendered diagram: [0007-kafka-terminalization-queue.svg](diagrams/0007-kafka-terminalization-queue.svg)

