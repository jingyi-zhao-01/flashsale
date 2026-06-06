# ADR 0004: Manage Flashsale Schema And Migrations With Python Prisma

- Status: Accepted
- Date: 2026-06-05

## Context

Flashsale currently creates and evolves PostgreSQL tables from three separate Python codepaths:

- `user-service/app/repositories.py`
- `product-service/app/repositories.py`
- `order-service/app/adapters/order_postgres_schema.py`

That bootstrap style got us moving quickly, but it now has real costs:

- schema changes are split across service runtime code instead of one migration history
- indexes and constraints are harder to review before deploy
- database drift is easy to introduce across environments
- CI and local setup do not have a single “apply schema” step

We also have an important repo constraint: flashsale is a Python workspace. We do not want the database workflow to depend on a separate npm project just to format schemas and apply migrations.

## Decision

We will manage flashsale database schemas with **Prisma schema files plus Prisma Migrate**, using the **Python `prisma` package** (`prisma-client-py`) as the tool entrypoint.

Because flashsale now shares one PostgreSQL database across three services, we will keep **one Prisma datasource** and map models into **three PostgreSQL schemas**:

- `user_service`
- `product_service`
- `order_service`

The Prisma schema is split into multiple files under one folder:

- `prisma/schema.prisma`
- `prisma/user.prisma`
- `prisma/product.prisma`
- `prisma/order.prisma`

All migrations live in one directory:

- `prisma/migrations/`

## Why this shape

### Why Prisma

Prisma gives us a better database control plane than inline DDL:

- declarative schema files that are easy to diff in code review
- migration directories that become the audit trail for table/index changes
- one repeatable command path for local, CI, and cluster bootstrap
- explicit ownership of indexes, relations, and column naming

### Why Python Prisma instead of a standalone npm toolchain

The services and dev workflow are already Python-first:

- dependencies are managed with `uv`
- tests and scripts are Python-based
- operators already enter the project through `uv run ...` and `make ...`

Using the Python `prisma` package keeps Prisma in the same toolchain family as the rest of flashsale while still using Prisma schema and migration primitives under the hood.

### Why one shared database with three PostgreSQL schemas

Flashsale still wants clear storage ownership boundaries, but we also want one database endpoint to provision, secure, and inject into runtime.

Keeping one shared PostgreSQL database with three schemas preserves service isolation without requiring three separate database URLs:

- `user-service` owns `users`
- `product-service` owns `products` and `reservations`
- `order-service` owns `orders`, `order_terminalization_tasks`, and `order_terminalization_task_events`

At runtime, each service gets the shared `DATABASE_URL` plus a service-specific `DB_SCHEMA`, and the connection search path is pinned to that schema.

## Migration strategy

The first Prisma migrations are **baseline migrations** that mirror the current runtime-created tables and indexes.

They are intentionally written as idempotent SQL:

- `CREATE TABLE IF NOT EXISTS ...`
- `CREATE INDEX IF NOT EXISTS ...`
- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`

This lets us adopt Prisma on top of existing databases without requiring a destructive reset.

## Compatibility boundary

For now, service runtime `init_db()` code remains in place as a compatibility fallback while environments are switching to Prisma-managed bootstrap.

The new preferred path is:

1. start databases
2. run Prisma migrations
3. start services

The eventual target state is that schema evolution happens through Prisma migrations first, with runtime bootstrap reduced to health or compatibility checks instead of being the primary schema author.

## Consequences

Benefits:

- schema and index changes move into reviewable migration history
- local and CI can share the same migration command surface
- one shared database URL is easier to provision and inject across environments
- three service-owned schemas stay clearly separated without losing consistency of tooling
- future schema work can be planned through migrations instead of ad hoc startup DDL

Tradeoffs:

- we rely on PostgreSQL schema boundaries instead of separate database instances
- Prisma migrate commands need to target `prisma/schema.prisma` while format/validate can target the schema folder
- some Postgres-specific constructs still need hand-edited migration SQL
- until runtime bootstrap is fully retired, there is a temporary dual-path compatibility period

## Related changes

- `application/flashsale/pyproject.toml`
- `application/flashsale/scripts/run_prisma.py`
- `application/flashsale/prisma/`
- `application/flashsale/shared/flashsale_shared/postgres_schema.py`
