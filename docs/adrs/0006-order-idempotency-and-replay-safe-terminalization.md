# ADR 0006: Make Order Creation, Confirmation, and Terminalization Replay-Safe

- Status: Accepted
- Date: 2026-06-06

## Context

Milestone 1 in [flashsale issue #5](https://github.com/jingyi-zhao-01/flashsale/issues/5)
asks us to make order state transitions and idempotency behavior explicit, testable,
and safe under replay.

The current flashsale architecture already has the right building blocks:

- `orders.idempotency_key` exists
- `orders_idempotency_key_idx` is unique when the key is not null
- `CreateOrderUseCase` checks for an existing order before reserving stock
- `OrderPostgresRepository` and `OrderPostgresUnitOfWork` also fence duplicates at
  insert time with `INSERT ... ON CONFLICT DO NOTHING`
- order status and payment status transitions are guarded in
  `order-service/app/domain/state_machines.py`
- reservation terminalization is already off the synchronous `/orders` path and runs
  through durable `order_terminalization_tasks`

What is still easy to lose in code review, tests, or incident response is the exact
answer to these questions:

- Why does replaying the same `POST /orders` not create a second logical order?
- Why does replaying the same payment webhook not corrupt the final order state?
- What final state do we expect if reserve succeeds but order persistence fails?
- Which transitions are legal and which must remain impossible?

This ADR makes those guarantees explicit and sets the design contract for the
hardening work tracked in issue #5.

## Decision

We will treat idempotency in flashsale as a **state convergence guarantee**, not just
as a cache optimization.

The rule is:

> A single logical purchase intent may be retried, replayed, or re-observed many
> times, but it must converge to one durable order outcome and one inventory
> outcome.

That rule applies to three layers:

1. `POST /orders`
2. payment confirmation / webhook handling
3. reservation terminalization retries

## Design

### 1. Order creation is keyed by `idempotency_key`

`POST /orders` uses `idempotency_key` as the external replay fence for one logical
purchase intent.

The create path has two guards:

1. **read-before-work guard**
   - `CreateOrderUseCase.create_order()` checks `get_by_idempotency_key()` before user
     validation, admission control, and inventory reservation
   - if an order already exists, the existing order is returned immediately
2. **write-time uniqueness guard**
   - the database keeps a unique partial index on `orders.idempotency_key`
   - insert uses `ON CONFLICT (idempotency_key) ... DO NOTHING`
   - if a concurrent writer wins first, the loser reads back the existing order and
     returns it

This means a replayed `POST /orders` must not:

- reserve stock a second time
- enqueue a second logical order
- create a second order row

It may only:

- return the existing order row

### 2. Reservation success without order persistence must compensate immediately

Inventory reservation is not allowed to become an orphaned side effect.

If `product-service reserve` succeeds but the order row cannot be persisted, the
system must compensate by releasing all reservation ids collected during that create
attempt.

The final state for this failure mode is:

- no new durable order row
- reservation released or cancelled in `product-service`
- inventory returned
- caller receives a failure response

This boundary is more important than whether the failure came from:

- order insert failure
- task enqueue failure
- a transient database exception after reserve

For this milestone, the compensation rule is:

> no durable order means no durable inventory hold may remain.

### 3. Order state transitions are explicit and closed

Legal order transitions:

| Current | Allowed target |
|---|---|
| `pending` | `confirmed`, `failed`, `cancelled`, `expired` |
| `confirmed` | `confirmed` |
| `failed` | `failed` |
| `cancelled` | `cancelled` |
| `expired` | `expired` |

Legal payment status transitions:

| Current | Allowed target |
|---|---|
| `pending` | `succeeded`, `cancelled` |
| `succeeded` | `succeeded` |
| `cancelled` | `cancelled` |

Anything else is an illegal transition and must fail fast.

Examples that must stay illegal:

- `confirmed -> cancelled`
- `expired -> confirmed`
- `cancelled -> confirmed`
- `succeeded -> cancelled`

Self-transitions on terminal states are allowed because replay after success must be a
no-op, not a new mutation.

### 4. Payment confirmation is replay-safe by current order state

`POST /payments/webhook` must be safe when the same logical payment success is replayed.

Decision rules:

- if the order is already `confirmed / succeeded`, return the existing order and do
  nothing else
- if the order is already terminal in a non-success direction
  (`expired`, `failed`, `cancelled`), return the existing order and do not revive it
- if the order is still `pending`, transition it once toward success and enqueue the
  required reservation confirmation work

For this milestone, replay safety on payment success is anchored on the current order
state rather than on a separate persisted webhook-event ledger.

That is sufficient for the current local and compose-backed system because:

- the payment path is modeled as an internal success signal, not a third-party at-least-once queue
- terminal states are explicit and immutable except for self-replay
- duplicate success webhooks after the first success collapse into a read-only no-op

If flashsale later integrates with a real external payment provider, we should add a
persisted inbound event ledger keyed by provider event id. That is a likely follow-on,
but it is not required to complete the guarantees in issue #5.

### 5. Terminalization retries must be outcome-idempotent

`order_terminalization_tasks` are the durable retry boundary for confirm/cancel work.

The worker guarantee is:

- retrying the same logical confirm must not double-confirm inventory
- retrying the same logical cancel must not double-release inventory
- replaying work after the order has already converged must leave the final order
  state unchanged

For this milestone, we treat replay safety here as a combination of:

- durable task rows
- task attempt history in `order_terminalization_task_events`
- idempotent downstream terminalization semantics
- no-op order-state updates on already terminal orders

That means the worker may observe the same business intent more than once, but only
the first successful pass is allowed to change the durable outcome.

## End-to-end invariants

These are the invariants this ADR establishes.

### Invariant A: one idempotency key maps to one logical order

For a given `idempotency_key`, the system may return the same order many times, but it
may not create two order ids.

### Invariant B: inventory consumption is single-effect

For a given logical purchase intent:

- stock may be reserved once
- stock may be confirmed once
- stock may be released once

Replay may re-check those operations, but not consume or release inventory a second
time.

### Invariant C: terminal states do not revive

Once an order is terminal, late or duplicate success signals must not resurrect it
into another terminal state.

### Invariant D: background retries cannot corrupt the already-visible result

If a caller already observes a converged order, worker replay must preserve that
visible outcome.

## Final states for the important failure paths

| Scenario | Final order state | Final inventory state |
|---|---|---|
| create succeeds, confirm later succeeds | `confirmed / succeeded` | reservation `confirmed` |
| create succeeds, order expires before payment | `expired / cancelled` | reservation `cancelled` |
| late payment arrives after expiry | stays `expired / cancelled` | stays `cancelled` |
| reserve succeeds, order persist fails | no durable order | reservation released / cancelled |
| duplicate `POST /orders` with same key | same order returned | no second reserve |
| duplicate payment success after confirmation | same order returned | no second confirm |
| worker retry after confirm already succeeded | same order remains terminal | no double confirm |

## Consequences

Benefits:

- issue #5 now has a concrete architectural contract instead of only test intent
- order replay behavior is explainable during incidents
- the legal transition boundary is shared across API, worker, and repository code
- compensation after persistence failure is documented as a correctness requirement

Trade-offs:

- `idempotency_key` becomes part of the public correctness contract for safe client replay
- payment replay safety currently relies on state convergence, not yet on a separate
  provider-event dedupe table
- worker replay safety depends on downstream confirm/cancel semantics staying
  idempotent as integrations evolve

## Validation plan

The milestone is complete only when the following are covered by tests or harnesses:

- replay the same `idempotency_key` three times and observe one logical order outcome
- replay the same payment success three times and observe no state or inventory drift
- replay terminalization processing three times and observe no double confirm/release
- inject `reserve succeeded but order persist failed` and verify compensation
- assert that illegal transitions fail:
  - `confirmed -> cancelled`
  - `expired -> confirmed`
  - `cancelled -> confirmed`

## Related code

- `application/flashsale/order-service/app/application/create_order_use_case.py`
- `application/flashsale/order-service/app/application/process_terminalization_task_use_case.py`
- `application/flashsale/order-service/app/domain/state_machines.py`
- `application/flashsale/order-service/app/adapters/order_postgres_repository.py`
- `application/flashsale/order-service/app/adapters/order_postgres_unit_of_work.py`
- `application/flashsale/prisma/order.prisma`
- `application/flashsale/order-service/tests/unit/test_order_lifecycle.py`
- `application/flashsale/order-service/tests/integration/order_compose_integration.py`

## Diagram

The most useful visualization for this ADR is a **sequence diagram**, because issue #5
is about replay on behavior boundaries rather than only static structure.

The diagram below shows:

- first create request
- duplicate create replay short-circuit
- first payment confirmation
- duplicate confirmation replay short-circuit

![Order idempotency and replay-safe terminalization](diagrams/0006-order-idempotency-sequence.svg)
