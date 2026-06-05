import { createHotspotOrderScenario } from "../lib/k6-hotspot-order-scenario.js";

// Default medium profile for the shared hotspot-order scenario factory.

const scenario = createHotspotOrderScenario({
  defaultBaseUrl: "http://127.0.0.1:18082",
  defaultUserUrl: "http://127.0.0.1:18080",
  defaultProductUrl: "http://127.0.0.1:18081",
  defaultRampUp: "20s",
  defaultSteady: "60s",
  defaultRampDown: "20s",
  defaultTargetVus: 20,
  defaultReportIntervalMs: 5000,
  defaultHttpTimeout: "15s",
  defaultP50ThresholdMs: 800,
  defaultP90ThresholdMs: 1200,
  defaultP95ThresholdMs: 1500,
  defaultDescription:
    "Hotspot order test: all VUs repeatedly order the same product for the same user",
});

export const options = scenario.options;
export const setup = scenario.setup;
export const teardown = scenario.teardown;
export default scenario.default;
