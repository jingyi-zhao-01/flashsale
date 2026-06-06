# Flashsale Testing Documentation

## Overview

The flashsale project uses two levels of testing:

1. **Unit tests** — `unittest`-based, run with `python -m unittest discover`. No external
   dependencies required (no databases, no network). Tests use fake/stub implementations
   of repository interfaces, in-memory data stores, and `FastAPI TestClient` for HTTP
   endpoint testing.

2. **Integration tests** — `unittest`-based, run against a live Docker Compose stack with
   one real PostgreSQL database, three service-owned PostgreSQL schemas, and all three microservices. Tests exercise cross-service
   HTTP calls end-to-end.

---

## Unit Test Coverage

### Shared Library (`shared/`)

| Test File | What It Covers |
|---|---|
| `tests/unit/test_cache.py` | `NoOpCache` protocol compliance: `get()` always returns `None`, `set()`/`delete()` never raise, set-then-get returns `None` |
| `tests/unit/test_observability.py` | `TraceContextFilter` adds trace_id/span_id to log records; `request_path_label` returns route template when available, falls back to raw path; `inject_trace_headers` returns dict with `traceparent` and preserves existing headers |

### User Service

| Test File | What It Covers |
|---|---|
| `tests/unit/test_user_service.py` | Full CRUD lifecycle via `TestClient`: create user (201), get user (200), get user not found (404), duplicate email conflict (409), list all users, empty list, persistence failure → 503, DB unavailable → 503, sync route verification, admin reset clears state |
| `tests/unit/test_user_api_contract.py` | OpenAPI schema shape: all endpoints present, `UserCreate` requires name+email, `UserOut` has id/name/email, error response documentation (409, 404, 503), health response schema |
| `tests/unit/test_health_probes.py` | `/health`, `/ready`, `/live` ignore repository failures and return 200; all probe routes are async |
| `tests/unit/test_observability.py` | `request_path_label` uses route template `/users/{user_id}` for dynamic paths |

### Product Service

| Test File | What It Covers |
|---|---|
| `tests/unit/test_reservation_lifecycle.py` | Reserve reduces stock, confirm is idempotent, out-of-stock returns 409, cancel restores stock and is idempotent, expire releases only elapsed reservations |
| `tests/unit/test_api_contract.py` | OpenAPI endpoints present, reservation schema shape, product schema shape |
| `tests/unit/test_db_migration_compatibility.py` | DDL backward compatibility: products table, reservations table, indexes |
| `tests/unit/test_health_probes.py` | Probe endpoints ignore DB failures; async route check |
| `tests/unit/test_inventory_pool_errors.py` | Pool timeout propagates correctly through `reserve_product` |
| `tests/unit/test_observability.py` | `request_path_label` uses route templates |
| `tests/unit/test_service_error_mapping.py` | Pool timeout → 503, lock contention → 409, query timeout → 504 |

### Order Service

| Test File | What It Covers |
|---|---|
| `tests/unit/test_order_lifecycle.py` | State machine transitions (pending→confirmed, invalid transitions rejected); full order lifecycle: create, confirm, cancel, expire, idempotency keys, duplicate payment webhooks, timeout races (expired order stays expired after late payment), out-of-stock rejection, confirm failure retries in background |
| `tests/unit/test_api_contract.py` | OpenAPI endpoints, schemas include idempotency_key, payment_status, ErrorResponse |
| `tests/unit/test_db_migration_compatibility.py` | DDL: orders table with payment_status and idempotency_key columns, indexes, terminalization tasks/events tables |
| `tests/unit/test_db_pool.py` | `DatabasePool`: row_factory handling, autocommit behavior |
| `tests/unit/test_health_probes.py` | Probe endpoints ignore DB failures; async route check |
| `tests/unit/test_http_client_timeouts.py` | Dedicated timeouts per client (user lookup, product reserve, release, terminalize); error→HTTP mapping (504, 503, 429) |
| `tests/unit/test_observability.py` | `request_path_label` uses route template, falls back to raw path |
| `tests/unit/test_persistence_failure_consistency.py` | When DB persistence fails, inventory is NOT consumed (stock restored) |
| `tests/unit/test_worker_loop.py` | Worker continues after single-iteration failure |

---

## Integration Test Coverage

Integration tests run against a live Docker Compose stack with all three services
(`user-service`, `product-service`, `order-service`) backed by a shared PostgreSQL
database. The stack is started via `docker compose up -d --build` and tests wait for
all services to report healthy before executing.

Each test class resets all service state between tests via the `/admin/reset` endpoints.

### User Service Integration (`user_compose_integration.py`)

| Test | Scenario | Services Exercised |
|---|---|---|
| `test_create_and_get_user` | Create a user via POST `/users`, then GET `/users/{id}` to verify persistence | user-service → flashsale-db (`user_service`) |
| `test_get_user_not_found_returns_404` | GET `/users/99999` returns 404 with "user not found" detail | user-service → flashsale-db (`user_service`) |
| `test_create_duplicate_email_returns_409` | Two POST `/users` with same email; second returns 409 "user email already exists" | user-service → flashsale-db (`user_service`) |
| `test_list_users_returns_all_created_users` | Create 3 users, GET `/users` returns all 3 with id, name, email fields | user-service → flashsale-db (`user_service`) |
| `test_invalid_email_returns_422` | POST `/users` with invalid email "not-an-email" returns 422 (FastAPI validation) | user-service |
| `test_health_returns_ok` | GET `/health` returns `{"status": "ok", "service": "user-service"}` | user-service |
| `test_ready_returns_ok` | GET `/ready` returns `{"status": "ok"}` | user-service |
| `test_live_returns_ok` | GET `/live` returns `{"status": "ok"}` | user-service |

### Product Service Integration (`product_compose_integration.py`)

| Test | Scenario | Services Exercised |
|---|---|---|
| `test_order_consumes_stock` | Create order for quantity 2, verify product stock reduced from 5 to 3 | product-service, order-service, user-service |
| `test_duplicate_order_replay_does_not_change_stock` | Create order twice with same idempotency key, verify stock only consumed once | product-service, order-service, user-service |
| `test_out_of_stock_order_returns_conflict` | Consume 2 stock, then order 4 (more than remaining 3), returns 409 conflict | product-service, order-service, user-service |
| `test_list_products_returns_all_created_products` | GET `/products` returns list with id, name, stock fields for each product | product-service → flashsale-db (`product_service`) |
| `test_get_product_not_found_returns_404` | GET `/products/99999` returns 404 "product not found" | product-service → flashsale-db (`product_service`) |
| `test_reserve_product_reduces_available_stock` | POST `/products/{id}/reserve` for quantity 3 verifies stock goes from 5 to 2 | product-service → flashsale-db (`product_service`) |
| `test_confirm_reservation_persists` | Reserve → confirm transitions reservation to "confirmed" status | product-service → flashsale-db (`product_service`) |
| `test_cancel_reservation_restores_stock` | Reserve 2 → cancel → stock returns to 5 | product-service → flashsale-db (`product_service`) |
| `test_expire_reservations_releases_stale_reservations` | Reserve → expire endpoint returns expired_count in response | product-service → flashsale-db (`product_service`) |
| `test_confirm_nonexistent_reservation_returns_404` | POST `/reservations/99999/confirm` returns 404 | product-service → flashsale-db (`product_service`) |
| `test_cancel_nonexistent_reservation_returns_404` | POST `/reservations/99999/cancel` returns 404 | product-service → flashsale-db (`product_service`) |

### Order Service Integration (`order_compose_integration.py`)

| Test | Scenario | Services Exercised |
|---|---|---|
| `test_create_order_confirms_payment` | Create order → process terminalizations → order status "confirmed", payment "succeeded" | order-service, product-service, user-service |
| `test_duplicate_payment_webhook_is_idempotent` | Confirm order → replay payment webhook → status unchanged, still "confirmed"/"succeeded" | order-service, product-service, user-service |
| `test_expired_order_stays_expired_after_late_payment` | Seed pending order → expire → late payment webhook → order stays "expired"/"cancelled" | order-service, product-service, user-service |
| `test_list_orders_returns_all_created_orders` | Create 2 orders, GET `/orders` returns both with id, status, payment_status | order-service → flashsale-db (`order_service`) |
| `test_get_order_not_found_returns_404` | GET `/orders/99999` returns 404 "order not found" | order-service → flashsale-db (`order_service`) |
| `test_payment_webhook_on_nonexistent_order_returns_404` | POST `/payments/webhook` for order 99999 returns 404 | order-service → flashsale-db (`order_service`) |
| `test_order_with_invalid_user_returns_error` | Create order with user_id=99999 returns error (404 or 502) | order-service → user-service |
| `test_order_with_invalid_product_returns_404` | Create order with product_id=99999 returns 404 | order-service → product-service |
| `test_order_with_empty_items_returns_400` | Create order with empty items array returns 400 "order items cannot be empty" | order-service |

---

## Cross-Service Integration Scenarios

The integration tests collectively verify these cross-service workflows:

### Happy Path: Order Creation
1. Client calls `POST /users` (user-service) → user created
2. Client calls `POST /products` (product-service) → product created with stock
3. Client calls `POST /orders` (order-service) → order-service calls user-service to validate user,
   calls product-service to reserve stock, persists pending order, enqueues terminalization
4. Background worker processes terminalization → calls product-service to confirm reservation
5. Order transitions to "confirmed" / payment "succeeded"

### Idempotency
- Duplicate order requests (same `idempotency_key`) return the existing order without consuming additional stock
- Duplicate payment webhooks do not alter already-confirmed orders

### Error Handling
- Out-of-stock: product-service returns 409, order-service propagates it
- Invalid user: user-service returns 404, order-service returns error
- Invalid product: product-service returns 404, order-service propagates it
- Empty items: order-service returns 400 before calling any downstream service
- Late payments on expired orders: order remains expired, stock already restored

### Reservation Lifecycle
- Reserve → stock reduces
- Confirm → reservation finalized
- Cancel → stock restored
- Expire → stale reservations released, stock restored
- Idempotent confirm/cancel: replaying does not error or double-count

---

## Running Tests

### Unit Tests

```bash
# Shared library
cd shared
python -m unittest discover -s tests/unit -t . -p 'test_*.py'

# User service
cd user-service
python -m unittest discover -s tests/unit -t . -p 'test_*.py'

# Product service
cd product-service
python -m unittest discover -s tests/unit -t . -p 'test_*.py'

# Order service
cd order-service
python -m unittest discover -s tests/unit -t . -p 'test_*.py'
```

### Integration Tests

All suites (requires Docker):
```bash
python3 scripts/flashsale_compose_integration.py --suite all
```

Individual suites:
```bash
python3 scripts/flashsale_compose_integration.py --suite user
python3 scripts/flashsale_compose_integration.py --suite product
python3 scripts/flashsale_compose_integration.py --suite order
```

The compose integration runner:
1. Starts `docker compose up -d --build`
2. Waits for all services to report healthy (up to 60 attempts)
3. Resets all services via `/admin/reset`
4. Discovers and runs `*_integration.py` tests in the appropriate `integration/` directory
5. Cleans up (`docker compose down -v`) on exit

---

## CI Pipeline

The GitHub Actions workflow (`.github/workflows/flashsales-deploy-pre.yml`) gates
deployment on all test suites:

```
shared-unit-gate ─────────────────┐
product-service-unit-gate ────────┤
order-service-unit-gate ──────────┼── integration gates ── build-and-push ── deploy
user-service-unit-gate ───────────┤
                                  │
product-service-compose ──────────┤
order-service-compose ────────────┤
user-service-compose ─────────────┘
```

All unit gates run in parallel. Integration gates require all unit gates to pass.
Build-and-push and deployment require all unit and integration gates to pass.
