# Flashsales Harness Engineering

This record covers:

- `flashsale/user-service`
- `flashsale/product-service`
- `flashsale/order-service`
- `charts/flashsales`
- `flashsale/perf/k6/scenarios/concurrency-test.js`
- `flashsale/perf/scripts/loadtest-k6.sh`
- `flashsale/perf/docs/CONCURRENCY_TEST_PLAN.md`

## Current Architecture Summary

| Component | Current role | Notes for harness interpretation |
| --- | --- | --- |
| `user-service` | User persistence and lookup | Order creation synchronously depends on it |
| `product-service` | Product read, reservation lifecycle, and stock management | Reservation state machine now owns `reserve/confirm/cancel/expire` |
| `order-service` | User validation, reserve orchestration, and order persistence | Confirms or cancels reservations after persistence outcome |
| `charts/flashsales` | Deployment source of truth | Ingress and HPA behavior directly affect perf conclusions |
| `flashsale/perf/k6/scenarios/concurrency-test.js` | Main concurrency harness | Measures business outcomes and latency, but has correctness blind spots |

## Confirmed Findings

### 1. The current k6 harness can miss consistency failures

The current concurrency harness is still request-level and outcome-level. It measures latency, HTTP status, and high-level success rate, but it does not directly validate whether inventory and orders remain consistent after fault windows.

Implication:

- it cannot detect "reserved stock but failed to persist order"
- a run can pass while inventory and orders are already inconsistent

Primary files:

- `flashsale/perf/k6/scenarios/concurrency-test.js`

## What The Harness Currently Proves

It is useful for:

- catching obvious 5xx spikes
- comparing broad latency bands across profiles
- observing how contention changes tail latency

It is not sufficient for:

- proving stock and order correctness under failure
- validating timeout-race behavior
- validating order persistence failure windows

### 2. The harness needs a separate correctness lane

The runtime consistency harness exists because the perf harness alone is not enough.

Primary files:

- `.github/workflows/flashsales-consistency.yml`
- `flashsale/perf/python/consistency_harness.py`

### 3. The hotspot profile overstates post-sellout behavior

The `hotspot` profile in `flashsale/perf/k6/scenarios/concurrency-test.js` uses one product with `initialStock=1` while driving `100 TPS` for three minutes.

Implication:

- after the first successful reservation, most requests become fast business rejection
- the profile mostly measures the post-sellout path
- it is not a trustworthy benchmark for comparing optimistic and pessimistic lock behavior under sustained contention

Primary files:

- `flashsale/perf/k6/scenarios/concurrency-test.js`
- `flashsale/perf/docs/CONCURRENCY_TEST_PLAN.md`

### 5. Order-service HPA is unlikely to scale meaningfully as configured

The chart enables CPU-based HPA for `order-service`, but the default values only set a CPU limit and omit a CPU request.

Implication:

- CPU utilization based scaling may not behave as intended

### 6. Reservation terminalization is still too close to request latency

Even after removing the extra price lookup, `confirm` and `cancel` still sit in the `order-service` request path.

Implication:

- client timeout does not mean the backend stopped working
- late `confirm/cancel` can race with teardown, reset, or expiry flows
- hotspot investigations need worker and backlog metrics once terminalization moves async

## Gates

- deploy workflow: `.github/workflows/flashsales-deploy.yml`
- post-deploy workflow: `.github/workflows/flashsales-deploy-post.yml`
- reusable runtime consistency workflow: `.github/workflows/flashsales-consistency.yml`
- reusable perf workflow: `.github/workflows/flashsales-perf-concurrency-suite.yml`
- runtime script: `flashsale/perf/python/consistency_harness.py`
- cluster component: optional `dbProxy` in `charts/flashsales`

The two gates have different roles:

- consistency gate: correctness and persistence-failure behavior
- perf gate: contention, throughput, and latency trends

## Suggested Verification After Future Changes

When changing flashsales correctness or perf semantics, verify at least:

```bash
bash ./flashsale/scripts/e2e-smoke.sh
make concurrency-smoke
make concurrency-hotspot-10tps
make concurrency-baseline
make concurrency-hotspot
```

For correctness-sensitive changes, also run:

```bash
python3 ./flashsale/perf/python/consistency_harness.py
```

## Related Pages

- [Flashsales workload](flashsales.md)
- [ADR 0001: Reduce hotspot order round-trips and default to pessimistic inventory locking](adrs/0001-hotspot-order-path-and-locking.md)
- [ADR 0002: Move reservation confirm and cancel off the synchronous order path](adrs/0002-async-reservation-terminalization.md)
- [ADR 0002-1: Move order confirmation off the synchronous create-order path](adrs/0002-1-order-confirmation-off-synchronous-path.md)
- [Operations and tooling](../../docs/operations.md)
- [Repository overview](../../docs/overview.md)

Back to [README](../../README.md).
