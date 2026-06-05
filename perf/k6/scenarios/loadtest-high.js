import { createHotspotOrderScenario } from "../lib/k6-hotspot-order-scenario.js";

// Higher-concurrency profile that trades strict latency for bounded stress coverage.

const scenario = createHotspotOrderScenario({
  defaultBaseUrl: "http://127.0.0.1:18082",
  defaultUserUrl: "http://127.0.0.1:18080",
  defaultProductUrl: "http://127.0.0.1:18081",
  defaultRampUp: "20s",
  defaultSteady: "45s",
  defaultRampDown: "20s",
  defaultTargetVus: 40,
  defaultReportIntervalMs: 5000,
  defaultHttpTimeout: "30s",
  defaultP50ThresholdMs: 1200,
  defaultP90ThresholdMs: 2200,
  defaultP95ThresholdMs: 3000,
  defaultDescription:
    "Hotspot buy high: higher concurrency and bounded stress to expose lock contention tail latency",
});

export const options = scenario.options;
export const setup = scenario.setup;
export const teardown = scenario.teardown;
export default scenario.default;
