# K6 Concurrency Test Plan (No Microservice Changes)

This plan uses only K6 and existing admin/test endpoints.

## Goals

- Compare optimistic vs pessimistic lock trade-offs with controlled concurrency/TPS.
- Keep correctness first: no oversell.
- Separate business rejection from system errors.

## Profiles Implemented

All profiles run via `application/flashsale/perf/k6/scenarios/concurrency-test.js` and are exposed in Make targets.

1. `concurrency-smoke`

- TPS: 10
- Duration: 3m
- Non-hotspot mode: traffic is spread across multiple products.
- P50 < 100ms
- P90 < 200ms
- P99 < 500ms
- 5xx rate = 0
- Oversell = 0

1. `concurrency-hotspot-10tps`

- TPS: 10
- Duration: 3m
- Hotspot mode: many users contend for one product.
- P50 < 100ms
- P90 < 200ms
- P99 < 500ms
- 5xx rate = 0
- Oversell = 0

1. `concurrency-baseline`

- TPS: 50
- Duration: 8m
- P50 < 100ms
- P90 < 300ms
- P99 < 800ms
- 5xx rate < 1%
- Oversell = 0

1. `concurrency-stress100`

- TPS: 100
- Duration: 5m
- P50 < 150ms
- P90 < 500ms
- P99 < 1.2s
- 5xx rate < 2%
- Oversell = 0

1. `concurrency-stress200`

- TPS: 200
- Duration: 3m
- P50 < 150ms
- P90 < 500ms
- P99 < 2s
- 5xx rate < 2%
- Oversell = 0

1. `concurrency-hotspot`

- High conflict mode: many users contend for one product.
- TPS: 100 (default, override if needed)
- Duration: 3m
- P99 target relaxed to 2s
- Oversell = 0 (must pass)

## How to Run

From repo root:

- `make concurrency-smoke`
- `make concurrency-hotspot-10tps`
- `make concurrency-baseline`
- `make concurrency-stress100`
- `make concurrency-stress200`
- `make concurrency-hotspot`

## Step-by-step Progression

Run in this order:

1. Smoke
2. Idempotency Lite
3. Hotspot 10 TPS
4. Baseline
5. Stress100
6. Stress200
7. Hotspot

Repeat each profile 3 times and compare medians (especially P99 and 5xx).

## Correctness and Error Semantics

- `409` and `404` are treated as business outcomes in the K6 script (not system failure).
- System reliability is tracked by a dedicated metric: `http_5xx_rate`.
- Oversell is checked in teardown by counting ordered quantity per product and validating it does not exceed initial stock.

## Lock-mode Comparison Protocol

For each profile:

1. Deploy with `inventoryLockMode=pessimistic` and run 3 times.
2. Deploy with `inventoryLockMode=optimistic` and run 3 times.
3. Compare:

- throughput/TPS stability
- P50/P90/P99
- 5xx rate
- oversell violations
- lock/retry logs

Use the same image, DB size, and cluster resources for both modes.
