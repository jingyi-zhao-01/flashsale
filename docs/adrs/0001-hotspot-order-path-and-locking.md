# ADR 0001: Reduce Hotspot Order Round-Trips And Default To Pessimistic Inventory Locking

- Status: Accepted
- Date: 2026-06-03

## Context

The flashsales post-deploy concurrency smoke repeatedly failed under hotspot traffic.

Observed behavior:

- `order-service` requests stalled for tens of seconds during order creation
- readiness and liveness probes timed out
- `order-service` Pods restarted with `Exit Code 137`
- the GitHub Actions smoke lane then saw `connection refused` against the local `kubectl port-forward` endpoint

The hotspot path was doing more synchronous work than necessary:

1. `order-service` called `product-service GET /products/{id}` to fetch price
2. `order-service` then called `product-service POST /products/{id}/reserve`
3. `order-service` persisted the order
4. `order-service` called `confirm` or `cancel`

Under hotspot contention, that extra product read increased synchronous cross-service traffic on the critical path without adding correctness value. The same workflow was also still using `inventoryLockMode=optimistic` by default, which is a poor default for a single-product hotspot profile because retries and conflict churn become part of the latency path.

## Decision

We changed the flashsales default hotspot behavior in two ways.

### 1. Use the reservation response as the price source

`product-service reserve` now returns the price snapshot as `unit_price`.

`order-service` no longer performs a separate `GET /products/{id}` before reservation. It now:

1. validates the user
2. calls `reserve`
3. uses `unit_price` from the reservation response
4. persists the order
5. confirms or cancels the reservation

This removes one synchronous network hop from the hottest order path.

### 2. Default inventory locking to `pessimistic`

The Helm default for `productService.env.inventoryLockMode` is now `pessimistic`.

Reason:

- the perf smoke and hotspot suites are specifically contention-heavy
- pessimistic locking is the safer default for preserving stability under single-item contention
- optimistic mode remains available for explicit experiments, but should not be the default operating mode for this workload

## Consequences

Expected benefits:

- lower critical-path latency under hotspot traffic
- fewer avoidable calls from `order-service` to `product-service`
- less retry/conflict amplification in the default deployment
- lower chance that probe failures cascade into Pod restarts and `connection refused` in the perf lane

Trade-offs:

- reservation responses now carry a price snapshot and become part of the order pricing contract
- pessimistic locking may reduce peak throughput in low-conflict scenarios compared with a well-behaved optimistic path
- this change does not by itself solve every latency problem in the synchronous order flow

## Follow-up Notes

This ADR does not change the broader architectural shape:

- `order-service` still synchronously depends on `user-service` and `product-service`
- reservation `confirm` and `cancel` are still in the request path
- probe behavior and resource sizing still matter during perf runs

If hotspot instability continues, the next areas to evaluate are:

- shortening the synchronous order path further
- moving parts of confirm/cancel handling off the request path
- tuning resources and probe thresholds based on real cluster behavior

## Related Changes

- `application/flashsale/order-service/app/service.py`
- `application/flashsale/product-service/app/models.py`
- `application/flashsale/product-service/app/repositories.py`
- `application/flashsale/product-service/app/in_memory_repository.py`
- `charts/flashsales/values.yaml`
