# AGENT.md

## Purpose

This file is the workload-specific context contract for `application/flashsale/`.

Use it when work clearly depends on flashsale application behavior, database layout,
test harnesses, release contracts, or app-owned documentation. Keep platform-wide
deploy, Terraform, and Helm rules in the root [AGENT.md](/home/jingyi/PycharmProjects/homelab-cloud/AGENT.md).

## Scope

This sub-repo owns:

* flashsale application code
* app-owned CI in `application/flashsale/.github/`
* local Docker Compose integration environment
* Prisma schema and migration workflow
* workload docs, ADRs, release manifest, and quality contract

This sub-repo does not own:

* cluster-wide deploy orchestration in `homelab-cloud/.github/workflows/`
* Helm release wiring in `charts/flashsales/`
* Terraform infrastructure in `terraform/`

## Current Architecture

Default mental model:

* three FastAPI services: `user-service`, `product-service`, `order-service`
* one shared PostgreSQL database endpoint
* three PostgreSQL schemas:
  * `user_service`
  * `product_service`
  * `order_service`
* one shared `DATABASE_URL`
* one per-service `DB_SCHEMA`
* runtime connections rely on `search_path=<service_schema>,public`
* Prisma manages one datasource with multi-schema models under `prisma/`

Do not assume the old 3-database model is still valid. If you see `user-db`,
`product-db`, or `order-db` references, treat them as drift to fix unless the file is
historical by design.

## Source Of Truth

Use the closest flashsale source of truth first:

* onboarding and local workflow: [README.md](/home/jingyi/PycharmProjects/homelab-cloud/application/flashsale/README.md)
* workload overview and deploy semantics: [docs/flashsales.md](/home/jingyi/PycharmProjects/homelab-cloud/application/flashsale/docs/flashsales.md)
* test catalog and integration entrypoints: [docs/testing.md](/home/jingyi/PycharmProjects/homelab-cloud/application/flashsale/docs/testing.md)
* architecture diagrams: `docs/diagram/`
* architectural decisions: `docs/adrs/`
* app release intent: [release/flashsale-release.yaml](/home/jingyi/PycharmProjects/homelab-cloud/application/flashsale/release/flashsale-release.yaml)
* post-deploy quality lanes: [release/flashsale-quality-contract.yaml](/home/jingyi/PycharmProjects/homelab-cloud/application/flashsale/release/flashsale-quality-contract.yaml)

## Documentation Rules

Any flashsale behavior change should update the nearest workload doc, not just code.

Required defaults:

* if you change architecture or runtime shape, update `README.md` and/or `docs/flashsales.md`
* if you change test entrypoints, contracts, or expectations, update `docs/testing.md`
* if you make a durable architectural decision, add or update an ADR in `docs/adrs/`
* every flashsale ADR should be mirrored to `wiki/adrs/` in the parent repo
* meaningful flashsale investigation or behavior changes should also be reflected in `wiki/`

Do not leave workload docs and wiki mirrors knowingly divergent.

## Database Rules

When touching persistence:

* preserve the shared `DATABASE_URL` + per-service `DB_SCHEMA` model
* keep service table ownership separated by PostgreSQL schema
* check both Prisma migrations and runtime fallback bootstrap paths
* prefer fixing schema drift in Prisma and compatibility bootstrap together
* if migrations change, verify `scripts/run_prisma.py` still matches Prisma CLI expectations

Important nuance:

* `validate`, `format`, and `generate` can target the Prisma schema folder
* migrate commands currently need the root `prisma/schema.prisma` entrypoint

## Validation Order

Prefer the cheapest relevant flashsale check first:

* schema changes: `uv run python3 ./scripts/run_prisma.py validate`
* Prisma generation path: `uv run python3 ./scripts/run_prisma.py generate`
* unit compatibility checks for touched service
* local Compose migration/bootstrap check
* targeted integration suite
* full integration suite only when cross-service behavior is affected

Common entrypoints:

* `uv sync --extra dev`
* `make db-format`
* `make db-validate`
* `make db-generate`
* `make db-migrate-all`
* `make test-unit`
* `python3 scripts/flashsale_compose_integration.py --suite all`

## Editing Rules

* prefer focused diffs over broad cleanup
* preserve existing service names, ports, release contract fields, and test entrypoints unless the task requires otherwise
* keep architecture diagrams in sync with actual runtime behavior
* if you touch app-owned release or quality contracts, consider whether parent repo workflow wiring also needs updating
* if you change ADRs or workload docs, remember the wiki mirror requirement

## Debugging Order

For flashsale issues, investigate in this order:

1. the smallest affected service or script
2. config/env mismatches such as `DATABASE_URL`, `DB_SCHEMA`, timeouts, or service URLs
3. schema/bootstrap/migration drift
4. cross-service HTTP behavior and idempotency path
5. integration harness behavior
6. cluster/platform wiring only after workload assumptions are verified

## Working Assumptions

Unless evidence shows otherwise, assume:

* order creation is the main orchestration path
* product reservation correctness and idempotency matter more than micro-optimizing code shape
* worker and sync API paths may both need updates when order terminalization semantics change
* docs, ADRs, and wiki mirrors are part of the deliverable, not optional follow-up
