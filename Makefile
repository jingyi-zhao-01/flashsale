.PHONY: db-format db-validate db-generate db-migrate-status db-migrate-all concurrency-seed-low concurrency-smoke concurrency-idempotency-lite concurrency-hotspot-100tps concurrency-baseline concurrency-stress100 concurrency-stress200 concurrency-hotspot

LOADTEST_WRAPPER := bash ./perf/scripts/loadtest-k6.sh
CONCURRENCY_SCENARIO := ./perf/k6/scenarios/concurrency-test.js
IDEMPOTENCY_SCENARIO := ./perf/k6/scenarios/loadtest-lite-idempotency.js
PRISMA_WRAPPER := uv run python3 ./scripts/run_prisma.py

db-format:
	$(PRISMA_WRAPPER) format

db-validate:
	$(PRISMA_WRAPPER) validate

db-generate:
	$(PRISMA_WRAPPER) generate

db-migrate-status:
	$(PRISMA_WRAPPER) migrate-status

db-migrate-all:
	$(PRISMA_WRAPPER) migrate-deploy

concurrency-seed-low:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=seedlow -e K6_P50_THRESHOLD_MS=2000 -e K6_P90_THRESHOLD_MS=3000 -e K6_P99_THRESHOLD_MS=5000 -e MAX_5XX_RATE=0

concurrency-smoke:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=smoke -e K6_P50_THRESHOLD_MS=2000 -e K6_P90_THRESHOLD_MS=3000 -e K6_P99_THRESHOLD_MS=5000 -e MAX_5XX_RATE=0

concurrency-idempotency-lite:
	LOADTEST_SCRIPT=$(IDEMPOTENCY_SCENARIO) $(LOADTEST_WRAPPER) -e K6_P50_THRESHOLD_MS=2000 -e K6_P90_THRESHOLD_MS=3000 -e K6_P95_THRESHOLD_MS=2000

concurrency-hotspot-100tps:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=hotspot100 -e K6_P50_THRESHOLD_MS=2000 -e K6_P90_THRESHOLD_MS=3000 -e K6_P99_THRESHOLD_MS=5000 -e MAX_5XX_RATE=0

concurrency-baseline:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=baseline -e K6_P50_THRESHOLD_MS=2000 -e K6_P90_THRESHOLD_MS=3000 -e K6_P99_THRESHOLD_MS=5000 -e MAX_5XX_RATE=0.01

concurrency-stress100:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=stress100 -e K6_P50_THRESHOLD_MS=2000 -e K6_P90_THRESHOLD_MS=3000 -e K6_P99_THRESHOLD_MS=5000 -e MAX_5XX_RATE=0.02

concurrency-stress200:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=stress200 -e K6_P50_THRESHOLD_MS=2000 -e K6_P90_THRESHOLD_MS=3000 -e K6_P99_THRESHOLD_MS=5000 -e MAX_5XX_RATE=0.02

concurrency-hotspot:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=hotspot -e K6_P50_THRESHOLD_MS=2000 -e K6_P90_THRESHOLD_MS=3000 -e K6_P99_THRESHOLD_MS=5000 -e MAX_5XX_RATE=0.01
