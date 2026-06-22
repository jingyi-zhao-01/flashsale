# ADR 0008: Reintroduce a durable idempotency and outbox boundary for order creation

- Status: Proposed
- Date: 2026-06-21
- Related:
  - ADR 0005: Redis admission control before reserve
  - ADR 0006: replay-safe order semantics
  - ADR 0007: Kafka terminalization queue

## Context

The current flashsale runtime has two correctness gaps on the `POST /orders`
path:

1. `idempotency_key` only protects the final order row, not the earlier side
   effects
2. order persistence and Kafka publish are still a raw dual write

In practice, that means we can hit both of these failure modes:

- two concurrent requests with the same `idempotency_key` both reserve
  inventory, but only one order row wins
- the order row commits, but the terminalization command never reaches Kafka

The current architecture is close, but the durable boundary is in the wrong
place. Redis admission helps hotspot latency. Kafka is a good queue backend.
Neither solves the moment where we need to say:

> this logical purchase intent is now durably claimed, and its async follow-up
> is durably recorded

That boundary needs to live in Postgres, before we depend on Kafka delivery.

## Options considered

### Option A: DB-first + outbox

`order-service` persists all of these in one Postgres transaction:

- idempotency claim
- pending order row
- outbox command row

Kafka publish moves out of the synchronous request path. A dedicated outbox
publisher drains unpublished rows and publishes them to Kafka. `order-worker`
continues to consume Kafka and drive reservation terminalization.

Redis admission stays where it is today: it is still a latency and overload
control mechanism, not the source of truth.

### Option B: Kafka-first intake

The API accepts a create-order command first, publishes it to Kafka, and
returns a pending-style response. A worker later validates the user, reserves
inventory, and writes the order into Postgres.

In this model, Kafka is the first durable intake boundary, and Postgres becomes
the later materialized order state.

This is a real architecture, but it changes the semantics of `POST /orders`
from "create an order now" to "accept a command and maybe create an order
later."

## Decision

Choose **Option A: DB-first + outbox**.

We are explicitly **not** choosing Kafka-first intake for flashsale order
creation at this time.

## Why Option A wins here

### 1. It matches the API semantics we already want

Flashsale order creation behaves like a synchronous commerce API, not a generic
command-ingestion endpoint.

What we want from `POST /orders` is:

- a real durable order row
- a stable `order_id`
- replay-safe retries
- no ambiguity about whether the order exists

Option B changes the surface contract too much. Returning "pending command
accepted" is workable, but it is a different product behavior.

### 2. It fixes the actual bug we have

The current correctness bug is not "Kafka exists." The bug is:

- Postgres commit happens
- Kafka publish is separate
- the two are not covered by one durable boundary

Option A fixes exactly that by moving the correctness anchor back into
Postgres. Option B also avoids the same dual-write hole, but at the cost of
changing intake semantics and pushing more business work into the async path.

### 3. It keeps the hard business steps closer to the caller

Inventory reserve, order creation, and idempotency claim are the parts we care
most about reasoning about clearly.

Option B pushes more of that work into background workers:

- user validation
- inventory reservation
- order creation
- result materialization

That can be the right trade-off in a queue-first platform, but it is heavier
than what flashsale needs right now.

### 4. Redis stays in its proper place

With Option A, Redis remains an admission / latency control layer.

With Option B, Redis often becomes part of the pending-status story. That is
fine, but it adds another user-visible state surface. We do not need that extra
moving part to solve the current correctness issue.

## Chosen architecture

### Request path

New `POST /orders` flow:

1. validate user
2. pass Redis admission gate
3. reserve inventory in `product-service`
4. open one Postgres transaction in `order_service`
5. claim the `idempotency_key`
6. write the pending order row
7. write the outbox row for terminalization
8. commit
9. return `201` without waiting for Kafka

### Async path after commit

1. outbox publisher scans unpublished rows
2. publisher sends command to Kafka
3. publisher marks the outbox row as published
4. `order-worker` consumes Kafka
5. worker confirms or cancels the reservation
6. worker updates durable order state

## Why we are not choosing Kafka-first intake

Kafka-first intake is attractive when these are the primary goals:

- absorb very large spikes at the intake boundary
- return `202 accepted` instead of a fully created order
- tolerate fully asynchronous read-after-write
- model order creation as queued command processing first and business record
  creation second

That is not the trade-off we want for this system right now.

For flashsale, the simpler and safer stance is:

> Postgres should be the first durable source of truth for order creation, and
> Kafka should carry the async follow-up after the order boundary already
> exists.

## Why this is better than the current runtime

### 1. The idempotency fence moves earlier

Right now, the duplicate request can still do real work before the database
conflict is discovered. With a durable idempotency claim inside the order
transaction, the system gets a real ownership record for that purchase intent.

That gives us a stable answer to:

- who owns this `idempotency_key`
- whether a retry should create new side effects
- whether a request is replaying an existing result or racing a live one

### 2. The dual-write gap goes away

Without an outbox, this is still possible:

- order commit succeeds
- Kafka publish fails
- request returns an error
- retry sees the old pending order
- no safe recovery path recreates the lost command

With an outbox, Kafka being temporarily unavailable becomes a backlog problem,
not a correctness problem.

### 3. Failure handling becomes boring

That is the goal.

If Kafka is down:

- order rows still commit
- outbox rows still commit
- publisher retries later

If the client retries:

- the request replays the existing logical order
- it does not reserve again
- it does not need to publish directly

If the publisher crashes:

- unpublished rows are still in Postgres

## Container responsibilities in the chosen design

### `order-service`

Owns:

- synchronous create-order orchestration
- durable idempotency claim
- pending order persistence
- outbox row creation

Does **not** publish directly to Kafka in the request path.

### `outbox-publisher`

Owns:

- scanning unpublished outbox rows
- publishing Kafka commands
- marking rows published

This can run as a small sidecar-style worker or a separate process. It is a
control-plane component, not a public API.

### `order-worker`

Owns:

- Kafka consumption
- replay-safe terminalization execution
- final order state updates

### `Postgres`

Owns the durable truth for:

- orders
- idempotency claims
- outbox rows
- publish status

### `Kafka`

Owns delivery, partitioning, replay, retry, and consumer lag visibility.

It does **not** own business correctness by itself.

## Data model sketch

We do not need a huge schema change to get the benefit.

Minimal durable control-plane tables in `order_service`:

- `order_idempotency_claims`
  - `idempotency_key`
  - `request_hash`
  - `order_id`
  - `status`
  - timestamps
- `order_terminalization_outbox`
  - `event_id`
  - `order_id`
  - `reservation_id`
  - `action`
  - `published_at`
  - `attempt_count`
  - `last_error`
  - timestamps

This can reuse parts of the older task-table shape if that is easier than
inventing something totally new.

## Comparison summary

| Topic | Option A: DB-first + outbox | Option B: Kafka-first intake |
|---|---|---|
| First durable source of truth | Postgres | Kafka |
| API response shape | real order result | pending / accepted |
| Read-after-write | straightforward | asynchronous |
| Dual-write safety | strong, via outbox | strong, via queue-first intake |
| Idempotency anchor | Postgres claim + unique write | consumer-side idempotent materialization |
| Operational shape | one extra publisher | heavier async intake workflow |
| Best fit for flashsale today | yes | not yet |

## Migration notes

Suggested path:

1. add the new outbox table and idempotency claim table
2. move request-path Kafka publish behind a feature flag
3. implement the outbox publisher
4. shadow-publish and compare outbox rows vs Kafka messages
5. switch the request path to outbox-only
6. delete the old direct publish path

Do not try to rewrite Redis admission, Kafka consumption, and order state
machine logic in the same change. The main fix here is moving the durable
boundary.

## Consequences

Benefits:

- fixes the raw DB/Kafka dual-write hole
- gives `idempotency_key` a real durable ownership record
- turns broker outages into backlog instead of lost follow-up work
- makes retries much easier to reason about

Costs:

- adds one more control-plane worker
- adds one more table family in `order_service`
- requires publisher metrics and backlog monitoring

Still required after this ADR:

- `product-service` confirm/cancel must stay idempotent
- `order-worker` must stay replay-safe
- order state transitions still need explicit guards

## Diagrams

### Option A: DB-first + outbox

D2 source: [0008-c4-option-a-db-first-outbox.d2](diagrams/0008-c4-option-a-db-first-outbox.d2)

Rendered diagram: [0008-c4-option-a-db-first-outbox.svg](diagrams/0008-c4-option-a-db-first-outbox.svg)

### Option B: Kafka-first intake

D2 source: [0008-c4-option-b-kafka-first-intake.d2](diagrams/0008-c4-option-b-kafka-first-intake.d2)

Rendered diagram: [0008-c4-option-b-kafka-first-intake.svg](diagrams/0008-c4-option-b-kafka-first-intake.svg)
