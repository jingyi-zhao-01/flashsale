.PHONY: concurrency-smoke concurrency-idempotency-lite concurrency-hotspot-10tps concurrency-baseline concurrency-stress100 concurrency-stress200 concurrency-hotspot

# Shared wrapper and scenario entrypoints used by the perf targets below.
LOADTEST_WRAPPER := bash ./scripts/loadtest-k6.sh
CONCURRENCY_SCENARIO := ./k6/scenarios/concurrency-test.js
IDEMPOTENCY_SCENARIO := ./k6/scenarios/loadtest-lite-idempotency.js

# Default thresholds can still be overridden from the CLI or CI environment.
K6_P50_THRESHOLD_MS ?= 800
K6_P90_THRESHOLD_MS ?= 1200
K6_P95_THRESHOLD_MS ?= 1500
K6_P99_THRESHOLD_MS ?= 4000
MAX_5XX_RATE ?= 0

# Profile-specific perf lanes used by CI and manual runs.
concurrency-smoke:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=smoke -e K6_P50_THRESHOLD_MS=1500 -e K6_P90_THRESHOLD_MS=1600 -e K6_P99_THRESHOLD_MS=4000 -e MAX_5XX_RATE=0

concurrency-idempotency-lite:
	LOADTEST_SCRIPT=$(IDEMPOTENCY_SCENARIO) $(LOADTEST_WRAPPER) -e K6_P50_THRESHOLD_MS=1000 -e K6_P90_THRESHOLD_MS=1500 -e K6_P95_THRESHOLD_MS=2000

concurrency-hotspot-10tps:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=hotspot10 -e K6_P50_THRESHOLD_MS=100 -e K6_P90_THRESHOLD_MS=200 -e K6_P99_THRESHOLD_MS=4000 -e MAX_5XX_RATE=0

concurrency-baseline:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=baseline -e K6_P50_THRESHOLD_MS=100 -e K6_P90_THRESHOLD_MS=300 -e K6_P99_THRESHOLD_MS=4000 -e MAX_5XX_RATE=0.01

concurrency-stress100:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=stress100 -e K6_P50_THRESHOLD_MS=150 -e K6_P90_THRESHOLD_MS=500 -e K6_P99_THRESHOLD_MS=4000 -e MAX_5XX_RATE=0.02

concurrency-stress200:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=stress200 -e K6_P50_THRESHOLD_MS=150 -e K6_P90_THRESHOLD_MS=500 -e K6_P99_THRESHOLD_MS=4000 -e MAX_5XX_RATE=0.02

concurrency-hotspot:
	LOADTEST_SCRIPT=$(CONCURRENCY_SCENARIO) $(LOADTEST_WRAPPER) -e PROFILE=hotspot -e K6_P50_THRESHOLD_MS=150 -e K6_P90_THRESHOLD_MS=500 -e K6_P99_THRESHOLD_MS=4000 -e MAX_5XX_RATE=0.01
