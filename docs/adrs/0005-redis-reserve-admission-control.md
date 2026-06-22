# ADR 0005: Redis Product-Level Admission Control Before DB Inventory Reservation

- Status: Accepted
- Date: 2026-06-06

## Context

Production and load-test traces show that the `POST /orders` tail latency is dominated by
inventory reservation latency inside `product-service`. Under hotspot traffic, many
concurrent `reserve` requests contend for the same product row lock in PostgreSQL.
Requests that queue behind a long-running `UPDATE products SET stock = stock - $1`
experience multi-second spans, which drive up p99 even when the average is acceptable.

Observed trace pattern:

- `reserve inventory` span regularly exceeds 1 s under hotspot contention
- `pg_stat_statements` shows `UPDATE products ... RETURNING` as the dominant wait source
- individual requests spend seconds waiting on Postgres row locks that are held by other
  in-flight reserve attempts for the same `product_id`

This is a known hotspot-row behavior: pessimistic locking serialises concurrent
`UPDATE` statements. The database is correct, but too many concurrent reserve attempts
for the same product create a queue that inflates tail latency.

## Decision

We will add a **Redis-based product-level admission-control middleware** before
`reserveStock()` in `order-service`. The gate limits how many concurrent reserve
attempts can be in flight for each `product_id`.

### Current runtime behavior

```
POST /orders
  → early idempotency replay check
  → validate user
  → for each product_id: REDIS ADMISSION GATE (acquire permit)
  → reserve(product_id, quantity) via product-service
  → persist order
  → return 201
  → finally: REDIS ADMISSION GATE (release permit)
```

The **confirm/cancel terminalization path** (worker, payment webhook) does **not**
hold the product reserve permit. The permit is held only during the synchronous
`POST /orders` reserve window.

Important current details from the implementation:

- if `REDIS_URL` is not configured, the service uses `NoOpReserveAdmissionGate`
  and skips Redis admission entirely
- if `idempotency_key` hits an existing order, replay returns **before**
  admission acquire and does not consume a permit
- if Redis is configured but the gate fails during request processing,
  the request returns `503 admission control unavailable`
- if one product in a multi-item order is rejected, the gate releases any
  already-acquired permits for earlier product ids in the same request

### Redis key format

```
Key:   flashsale:reserve:admission:{product_id}
Type:  string (integer counter)
Ops:   INCR on entry, DECR on exit, EXPIRE on first INCR
```

A Redis key TTL (default 15 s) prevents leaked permits from crashed or
hung processes.

### Default configuration

| Env Var | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `""` | Redis connection URL |
| `REDIS_TOKEN` | `""` | Redis auth token (Upstash) |
| `RESERVE_ADMISSION_MAX_INFLIGHT` | `2` | Max concurrent reserve attempts per product_id |
| `RESERVE_ADMISSION_PERMIT_TTL_SECONDS` | `15` | TTL on counter key to auto-recover leaked permits |

### Admission algorithm (per product_id)

Current implementation is Lua-script based:

1. `acquire(product_id)` calls `acquire_permit.lua`
2. script repairs any stale negative counter by deleting it
3. script runs `INCR flashsale:reserve:admission:{product_id}`
4. if counter == 1: script sets `EXPIRE ... {ttl}`
5. Python checks the returned counter
6. if counter > `max_inflight`, Python calls `release_permit.lua`, rejects with 429,
   and releases any previously acquired permits for the same request
7. on request exit, `release(product_id)` calls `release_permit.lua`
8. `release_permit.lua` returns `0` for missing or non-positive keys and deletes
   the key when the counter would otherwise drop to zero

### 2026-06-20 repair: prevent negative counters on release

Load-test traces and structured logs later exposed a gap in the original algorithm:
real Redis semantics do **not** clamp `DECR` on a missing key to `0`.
If the permit key had already expired, or had been cleared before the `finally`
release ran, a bare `DECR` would recreate the key as `-1`. Repeated releases
or follow-up requests could then drift the admission counter to `-2`, `-3`, and
other invalid inflight states.

The fix keeps the Redis admission model, but hardens both edges with Lua scripts:

1. `release_permit.lua` returns `0` when the key is missing or already non-positive,
   instead of writing a negative counter
2. `acquire_permit.lua` repairs any stale negative key by deleting it before `INCR`
3. the Python gate wrapper now calls these scripts atomically instead of issuing
   raw `INCR` / `DECR` commands from the client

This preserves the original intent of ADR 0005 while repairing an implementation
detail that only showed up under real Upstash/Redis behavior.

### Rejection behavior

When a product is over its admission limit, `order-service` returns:

```
HTTP 429 Too Many Requests
{ "detail": "product reservation busy, retry later" }
```

### Why Redis and not the existing anyio capacity limiter

The existing `ORDER_CREATE_MAX_IN_FLIGHT` (`anyio.CapacityLimiter`) limits total
in-flight order creates across all products. That helps with overall service
overload but does not prevent 32 concurrent requests from all targeting the same
hot product and queuing on its Postgres row lock.

The Redis gate is scoped **per product_id**, which is the right granularity for
reducing hotspot row-lock queuing.

### What this does NOT change

- Database schema is unchanged
- Redis is never used as the inventory source of truth
- Product reservation correctness (pessimistic locking, stock decrement) is untouched
- Confirm/cancel/payment terminalization does not hold the permit
- Theoretical write throughput of a single product row is not increased

## Observability

### OpenTelemetry spans

Each gate check emits a span named `reserve admission gate` under `order-service`
with attributes:

- `flashsale.product_id`: product identifier
- `flashsale.admission.decision`: `"allowed"` | `"rejected"`
- `flashsale.admission.inflight`: current counter value after INCR
- `flashsale.admission.wait_ms`: time spent in gate acquisition (for allowed requests)
- `flashsale.admission.max_inflight`: configured limit

### Structured logging

```
event=reserve_admission_gate product_id=42 decision=allowed inflight=1 max_inflight=2 wait_ms=1.23
event=reserve_admission_gate product_id=42 decision=rejected inflight=3 max_inflight=2
```

### Prometheus metrics

| Metric | Type | Labels |
|---|---|---|
| `flashsale_reserve_admission_allowed_total` | Counter | `product_id` |
| `flashsale_reserve_admission_rejected_total` | Counter | `product_id` |
| `flashsale_reserve_admission_inflight` | Gauge | `product_id` |
| `flashsale_reserve_admission_wait_seconds` | Histogram | `product_id` |
| `flashsale_reserve_admission_errors_total` | Counter | `product_id`, `error_type` |

## Consequences

Expected benefits:

- Lower `POST /orders` p99 latency under hotspot traffic
- Fewer requests queued behind Postgres row locks
- `reserve inventory` span no longer shows multi-second durations under
  configured concurrency
- 500/504 caused by DB lock-wait timeouts reduced
- Self-healing via Redis key TTL (leaked permits expire)

Trade-offs:

- Redis is now a runtime dependency on the critical order path
- If `REDIS_URL` is unset, the gate is disabled and the service falls back to
  `NoOpReserveAdmissionGate`
- If Redis is configured but unavailable during a request, the current code
  fails closed with `503 admission control unavailable`
- This does not increase theoretical write throughput for a single hot product;
  it is a stability improvement, not a scaling model
- Multi-item orders acquire permits for all product_ids atomically
- Release is now safe against expired or already-cleared permit keys
- Follow-up acquires self-heal stale negative counters instead of inheriting bad state

## Validation plan

Run the same load test before and after the change and compare:

| Metric | Before | After |
|---|---|---|
| POST /orders p99 latency | > 2 s under hotspot | < 1 s under hotspot |
| reserve inventory span > 1 s | common under hotspot | rare |
| 429 rate | 0 (only anyio overload) | non-zero (early rejection) |
| 500/504 from DB lock wait | present | reduced |

## Related changes

- `application/flashsale/order-service/app/ports/reserve_admission_gate.py`
- `application/flashsale/order-service/app/adapters/redis_reserve_admission_gate.py`
- `application/flashsale/order-service/app/adapters/lua/acquire_permit.lua`
- `application/flashsale/order-service/app/adapters/lua/release_permit.lua`
- `application/flashsale/order-service/app/adapters/reserve_admission_metrics.py`
- `application/flashsale/order-service/app/application/create_order_use_case.py`
- `application/flashsale/order-service/app/application/order_runtime.py`
- `application/flashsale/order-service/app/entrypoints/http_api.py`
- `application/flashsale/order-service/app/config.py`
- `application/flashsale/order-service/pyproject.toml`

## Diagrams

### C4 Container Diagram (with Redis)

![C4 container diagram with Redis admission gate](diagrams/0005-c4-redis-admission.svg)

### Sequence Diagram

![Redis admission gate sequence](diagrams/0005-redis-admission-sequence.svg)

### Current Runtime Sequence

This version reflects the code as it exists now, including:

- early idempotency replay bypass
- Lua-script acquire/release
- `503` on Redis runtime failure
- request-scoped rollback of previously acquired permits in multi-item flows

![Redis admission gate current runtime](diagrams/0005-redis-admission-current-runtime.svg)

### Counter Underflow Repair

This diagram shows the exact bug that appeared in production-like traffic and
what the Lua-script repair changed.

- Red box: the old release path could `DECR` a missing key and create `-1`
- Green box: the new release script returns `0` instead of underflowing
- Blue box: the new acquire script repairs any stale negative key before `INCR`

![Redis admission counter underflow repair](diagrams/0005-redis-admission-underflow-repair.svg)
