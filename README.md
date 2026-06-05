# Flashsale

A concurrency-practice microservice workload — three FastAPI services backed by PostgreSQL, orchestrated via Docker Compose.

## Architecture

![C4 Container Diagram](docs/diagram/architecture.svg)

All three services share `flashsale_shared/` (observability, caching, DB pool helpers) and each owns its own PostgreSQL 16 database. See [`docs/diagram/`](docs/diagram/) for D2 source files.

### Cross-service call flow (create order)

![Order Creation Sequence](docs/diagram/create-order.svg)

1. Client POSTs order to **order-service**
2. order-service validates the user via **user-service** (404 → 422)
3. order-service reserves stock via **product-service** (409/404 → propagate)
4. On success, order is persisted to **order-db** and terminalization is enqueued
5. 201 Created returned to client

## Getting Started

```bash
# Start the full stack
docker compose up -d --build

# Run unit tests
make test-unit

# Run integration tests against live Compose stack
python3 scripts/flashsale_compose_integration.py --suite all
```

## Services

| Service     | Port   | Responsibilities                                  |
|-------------|--------|---------------------------------------------------|
| user-service | 18001  | User CRUD, email validation, health probes        |
| product-service | 18002 | Product catalog, inventory reservation (pessimistic/optimistic), confirm/cancel/expire |
| order-service | 18000 | Order creation (cross-service orchestration), terminalization, webhooks |

## Test Coverage

| Layer          | Files                                                       | Tests |
|----------------|-------------------------------------------------------------|------:|
| shared / unit  | `test_cache.py`, `test_observability.py`                    |    11 |
| user / unit    | `test_user_service.py`, `test_user_api_contract.py`, `test_health_probes.py` | 23 |
| product / unit | 8 test files                                                |    17 |
| order / unit   | 8 test files                                                |    30 |
| **unit total** |                                                             | **81** |
| user / integration   | `user_compose_integration.py`                          |     8 |
| product / integration | `product_compose_integration.py`                      |    10 |
| order / integration   | `order_compose_integration.py`                        |     8 |

See [`docs/testing.md`](docs/testing.md) for the full test catalog and run instructions.

## Documentation

- [Architecture & ADRs](docs/adrs/)
- [Test Catalog](docs/testing.md)
- [Release Contract](release/flashsale-release.yaml)
- [Quality Contract](release/flashsale-quality-contract.yaml)
