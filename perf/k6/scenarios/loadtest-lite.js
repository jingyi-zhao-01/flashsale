import { createHotspotOrderScenario } from "../lib/k6-hotspot-order-scenario.js";

// Short, lower-concurrency smoke profile for quick post-deploy validation.

const scenario = createHotspotOrderScenario({
  defaultBaseUrl: "http://127.0.0.1:18082",
  defaultUserUrl: "http://127.0.0.1:18080",
  defaultProductUrl: "http://127.0.0.1:18081",
  defaultRampUp: "10s",
  defaultSteady: "20s",
  defaultRampDown: "10s",
  defaultTargetVus: 10,
  defaultReportIntervalMs: 5000,
  defaultHttpTimeout: "20s",
  defaultP50ThresholdMs: 800,
  defaultP90ThresholdMs: 1200,
  defaultP95ThresholdMs: 1500,
  defaultDescription:
    "Hotspot buy lite: lower concurrency with short duration for post-deploy quick verification",
});

export const options = scenario.options;
export const setup = scenario.setup;
export const teardown = scenario.teardown;
export default scenario.default;
