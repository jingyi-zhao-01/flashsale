# Flashsale

A concurrency-practice microservice workload — three FastAPI services backed by PostgreSQL and Redis, orchestrated via Docker Compose.

## Architecture

![C4 Container Diagram](docs/diagram/architecture.svg)

All three services share `flashsale_shared/` (observability, caching, DB pool helpers), one PostgreSQL 16 database with isolated service-owned schemas, and one Redis 7 instance for product-level reserve admission control. See [`docs/diagram/`](docs/diagram/) for D2 source files.

### Reserve Admission Gate

![Reserve Admission Sequence](docs/adrs/0005-redis-admission-sequence.svg)

To reduce `POST /orders` p99 latency under hotspot traffic, the order-service gates concurrent inventory-reservation attempts per product via a Redis counter:

- **INCR** on entry, **DECR** on exit (finally block)
- **TTL** (15s) auto-recovers permits from crashed processes
- Default max **2** concurrent reserves per `product_id`
- Requests over limit → `HTTP 429` "product reservation busy, retry later"
- Confirm/cancel/payment paths **do not** hold the permit
- No `REDIS_URL` → gate silently disabled (no-op)

See [ADR 0005](docs/adrs/0005-redis-reserve-admission-control.md) for the full decision record with C4 and sequence diagrams.

### Cross-service call flow (create order)

![Order Creation Sequence](docs/diagram/create-order.svg)

1. Client POSTs order to **order-service**
2. order-service validates the user via **user-service** (404 → 422)
3. order-service acquires a Redis admission permit for each `product_id`
4. order-service reserves stock via **product-service** (409/404 → propagate)
5. On success, order is persisted to the shared PostgreSQL database under the **order_service** schema, the admission permits are released, and terminalization is enqueued
6. 201 Created returned to client

## Getting Started

```bash
# Install workspace + dev tooling (includes Python Prisma CLI)
uv sync --extra dev

# Start the full stack
docker compose up -d --build

# Apply Prisma migrations to the shared flashsale database
make db-migrate-all

# Run unit tests
make test-unit

# Run integration tests against live Compose stack
python3 scripts/flashsale_compose_integration.py --suite all
```

## Database Schema Management

Flashsale now keeps one Prisma datasource for the shared PostgreSQL database and maps each service's tables into its own PostgreSQL schema:

- `user_service`
- `product_service`
- `order_service`

Schema files are organized under `prisma/`:

- `prisma/schema.prisma`
- `prisma/user.prisma`
- `prisma/product.prisma`
- `prisma/order.prisma`

The repo uses the Python `prisma` package (`prisma-client-py`) as the CLI entrypoint, so
all Prisma commands stay inside the existing `uv` workflow.

Common commands:

```bash
# Format and validate all Prisma schemas
make db-format
make db-validate

# Generate the Python Prisma client into generated/
make db-generate

# Apply migrations to the shared DB
make db-migrate-all

# Show migration state
make db-migrate-status
```

Local Docker Compose now exposes the shared Postgres and Redis instances for host-side access:

- `flashsale-db`: `localhost:15432`
- `flashsale-redis`: `localhost:16379`

All three services share one `DATABASE_URL` and set `DB_SCHEMA` independently:

```bash
DATABASE_URL=postgresql://test:test@localhost:15432/flashsale make db-migrate-all
```

## Services

| Service     | Port   | Responsibilities                                  |
|-------------|--------|---------------------------------------------------|
| user-service | 18001  | User CRUD, email validation, health probes        |
| product-service | 18002 | Product catalog, inventory reservation (pessimistic/optimistic), confirm/cancel/expire |
| order-service | 18003 | Order creation (cross-service orchestration), Redis admission gate, terminalization, webhooks |
| flashsale-redis | 16379 | Reserve admission control counter per product_id (INCR/DECR with TTL) |

## Test Coverage

| Layer          | Files                                                       | Tests |
|----------------|-------------------------------------------------------------|------:|
| shared / unit  | `test_cache.py`, `test_observability.py`                    |    14 |
| user / unit    | `test_user_service.py`, `test_user_api_contract.py`, `test_health_probes.py` | 23 |
| product / unit | 8 test files                                                |    17 |
| order / unit   | 9 test files                                                |    51 |
| **unit total** |                                                             | **105** |
| user / integration   | `user_compose_integration.py`                          |     8 |
| product / integration | `product_compose_integration.py`                      |    10 |
| order / integration   | `order_compose_integration.py`, `order_admission_integration.py` | 16 |

See [`docs/testing.md`](docs/testing.md) for the full test catalog and run instructions.

## Documentation

- [Architecture & ADRs](docs/adrs/)
- [ADR 0004: Prisma schema and migration management](docs/adrs/0004-manage-flashsale-schema-and-migrations-with-python-prisma.md)
- [ADR 0005: Redis reserve admission control](docs/adrs/0005-redis-reserve-admission-control.md)
- [ADR 0007: Kafka terminalization queue](docs/adrs/0007-kafka-terminalization-queue.md)
- [Test Catalog](docs/testing.md)
- [Release Contract](release/flashsale-release.yaml)
- [Quality Contract](release/flashsale-quality-contract.yaml)
