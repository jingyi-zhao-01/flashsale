import http from "k6/http";
import exec from "k6/execution";
import { Counter, Rate } from "k6/metrics";

// Main throughput harness used by the named concurrency profiles in Makefile and CI.
// It spreads traffic across seeded users/products, then checks for oversell in teardown.

import {
  buildConstantArrivalRateOptions,
  createPostJson,
  createRuntimeReporter,
} from "../lib/k6-common.js";
import { initializePerfRun } from "../setup/index.js";
import { cleanupPerfRun } from "../teardown/index.js";

const BASE_URL = __ENV.BASE_URL || "http://homelab-order-service.jzhao62.com";
const USER_URL = __ENV.USER_URL || "http://homelab-user-service.jzhao62.com";
const PRODUCT_URL = __ENV.PRODUCT_URL || "http://homelab-product-service.jzhao62.com";
const K6_HTTP_TIMEOUT = __ENV.K6_HTTP_TIMEOUT || "20s";
const PROFILE = __ENV.PROFILE || "smoke";
const REPORT_INTERVAL_MS = Number(__ENV.REPORT_INTERVAL_MS || 5000);

// 这些 profile 默认值定义了不同压测档位的目标工作点。
const PROFILE_DEFAULTS = {
  seedlow: {
    tps: 2,
    duration: "2m",
    p50: 2000,
    p90: 3000,
    p99: 5000,
    max5xxRate: 0,
    productCount: 3,
    userCount: 12,
    initialStock: 20,
  },
  smoke: {
    tps: 10,
    duration: "3m",
    p50: 1500,
    p90: 1600,
    p99: 4000,
    max5xxRate: 0,
    productCount: 10,
    userCount: 50,
    initialStock: 300,
  },
  hotspot100: {
    tps: 100,
    duration: "3m",
    p50: 100,
    p90: 200,
    p99: 4000,
    max5xxRate: 0,
    productCount: 1,
    userCount: 50,
    initialStock: 3000,
  },
  baseline: {
    tps: 50,
    duration: "8m",
    p50: 100,
    p90: 300,
    p99: 4000,
    max5xxRate: 0.01,
    productCount: 20,
    userCount: 1000,
    initialStock: 2000,
  },
  stress100: {
    tps: 100,
    duration: "5m",
    p50: 150,
    p90: 500,
    p99: 4000,
    max5xxRate: 0.02,
    productCount: 30,
    userCount: 2000,
    initialStock: 1500,
  },
  stress200: {
    tps: 200,
    duration: "3m",
    p50: 150,
    p90: 500,
    p99: 4000,
    max5xxRate: 0.02,
    productCount: 40,
    userCount: 3000,
    initialStock: 1200,
  },
  hotspot: {
    tps: 100,
    duration: "3m",
    p50: 150,
    p90: 500,
    p99: 4000,
    max5xxRate: 0.01,
    productCount: 1,
    userCount: 1000,
    initialStock: 1,
  },
};

const selectedProfile = PROFILE_DEFAULTS[PROFILE] || PROFILE_DEFAULTS.smoke;
const TARGET_TPS = Number(__ENV.TARGET_TPS || selectedProfile.tps);
const TEST_DURATION = __ENV.TEST_DURATION || selectedProfile.duration;
const P50_MS = Number(__ENV.K6_P50_THRESHOLD_MS || selectedProfile.p50);
const P90_MS = Number(__ENV.K6_P90_THRESHOLD_MS || selectedProfile.p90);
const P99_MS = Number(__ENV.K6_P99_THRESHOLD_MS || selectedProfile.p99);
const MAX_5XX_RATE = Number(__ENV.MAX_5XX_RATE || selectedProfile.max5xxRate);
const PRODUCT_COUNT = Number(__ENV.PRODUCT_COUNT || selectedProfile.productCount);
const USER_COUNT = Number(__ENV.USER_COUNT || selectedProfile.userCount);
const INITIAL_STOCK = Number(__ENV.INITIAL_STOCK || selectedProfile.initialStock);
const SETUP_TIMEOUT = __ENV.K6_SETUP_TIMEOUT || "3m";
const TEARDOWN_TIMEOUT = __ENV.K6_TEARDOWN_TIMEOUT || "3m";
const PRE_ALLOCATED_VUS = Number(
  __ENV.PRE_ALLOCATED_VUS || Math.max(20, TARGET_TPS * 2),
);
const MAX_VUS = Number(__ENV.MAX_VUS || Math.max(200, TARGET_TPS * 10));

const TEST_DESCRIPTION =
  __ENV.TEST_DESCRIPTION ||
  `Concurrency profile=${PROFILE} tps=${TARGET_TPS} duration=${TEST_DURATION}`;
const POST_CLEANUP = (__ENV.POST_CLEANUP || "true").toLowerCase() === "true";
const DRAIN_TERMINALIZATIONS_FIRST = (
  __ENV.DRAIN_TERMINALIZATIONS_FIRST || "false"
).toLowerCase() === "true";
const TERMINALIZATION_DRAIN_ATTEMPTS = Number(
  __ENV.TERMINALIZATION_DRAIN_ATTEMPTS || 20,
);
const TERMINALIZATION_DRAIN_PAUSE_SECONDS = Number(
  __ENV.TERMINALIZATION_DRAIN_PAUSE_SECONDS || 0.25,
);

const http5xxRate = new Rate("http_5xx_rate");
const oversellViolations = new Counter("oversell_violations");
const orderSuccessTotal = new Counter("order_success_total");
const businessRejectTotal = new Counter("business_reject_total");

const setupPostJson = createPostJson(K6_HTTP_TIMEOUT, {
  tags: { phase: "setup" },
});
const trafficPostJson = createPostJson(K6_HTTP_TIMEOUT, {
  tags: { phase: "traffic" },
});
const reportRuntime = createRuntimeReporter(
  REPORT_INTERVAL_MS,
  ({ tps }) => `[k6-runtime] profile=${PROFILE} approx_tps=${tps.toFixed(2)}`,
);

http.setResponseCallback(http.expectedStatuses({ min: 200, max: 204 }, 409, 404));

export const options = buildConstantArrivalRateOptions({
  scenarioName: "concurrency_test",
  rate: TARGET_TPS,
  duration: TEST_DURATION,
  preAllocatedVUs: PRE_ALLOCATED_VUS,
  maxVUs: MAX_VUS,
  setupTimeout: SETUP_TIMEOUT,
  teardownTimeout: TEARDOWN_TIMEOUT,
  thresholds: {
    "http_req_duration{phase:traffic}": [
      `p(50)<${P50_MS}`,
      `p(90)<${P90_MS}`,
      `p(99)<${P99_MS}`,
    ],
    http_5xx_rate: [`rate<=${MAX_5XX_RATE}`],
    oversell_violations: ["count==0"],
  },
});

export function setup() {
  const timestamp = Date.now();

  // 每轮压测都用新的批次数据初始化，避免复用旧用户/旧商品导致结果不可比。
  const initialized = initializePerfRun({
    description: TEST_DESCRIPTION,
    orderUrl: BASE_URL,
    userUrl: USER_URL,
    productUrl: PRODUCT_URL,
    timeout: K6_HTTP_TIMEOUT,
    postJson: setupPostJson,
    userBatch: {
      emailPrefix: `k6-${PROFILE}-u`,
      namePrefix: "k6 user",
      count: USER_COUNT,
      timestamp,
    },
    productBatch: {
      namePrefix: `k6-${PROFILE}-p`,
      price: 9.99,
      stock: INITIAL_STOCK,
      count: PRODUCT_COUNT,
      timestamp,
    },
  });

  return {
    users: initialized.users.map((user) => user.id),
    products: initialized.products.map((product) => product.id),
    initialStock: INITIAL_STOCK,
  };
}

export default function concurrencyTest(data) {
  const userId = data.users[exec.scenario.iterationInTest % data.users.length];
  const productId =
    // hotspot 档位故意把所有流量集中到一个商品上，用来观察热点路径行为。
    PROFILE === "hotspot"
      ? data.products[0]
      : data.products[exec.scenario.iterationInTest % data.products.length];

  const orderRes = trafficPostJson(`${BASE_URL}/orders`, {
    user_id: userId,
    items: [{ product_id: productId, quantity: 1 }],
  });

  const is5xx = orderRes.status >= 500;
  http5xxRate.add(is5xx);

  if (orderRes.status === 200 || orderRes.status === 201) {
    orderSuccessTotal.add(1);
  } else if (orderRes.status === 409 || orderRes.status === 404) {
    businessRejectTotal.add(1);
  }

  reportRuntime();
}

export function teardown(data) {
  const listOrdersRes = http.get(`${BASE_URL}/orders`, {
    tags: { phase: "teardown" },
    timeout: K6_HTTP_TIMEOUT,
  });
  let violations = 0;

  if (listOrdersRes.status === 200) {
    // oversell 校验放在末尾统一做，因为 k6 VU 之间不适合高频共享业务状态。
    const orders = listOrdersRes.json();
    const soldByProduct = {};
    for (const order of orders) {
      for (const item of order.items || []) {
        const productId = Number(item.product_id);
        const quantity = Number(item.quantity || 0);
        soldByProduct[productId] = (soldByProduct[productId] || 0) + quantity;
      }
    }

    for (const productId of data.products) {
      const sold = soldByProduct[Number(productId)] || 0;
      if (sold > data.initialStock) {
        violations += 1;
        console.log(
          `[k6-correctness] oversell_detected product_id=${productId} sold=${sold} initial_stock=${data.initialStock}`,
        );
      }
    }
  } else {
    violations += 1;
    console.log("[k6-correctness] unable to list orders for oversell check");
  }

  if (violations > 0) {
    oversellViolations.add(violations);
  }

  cleanupPerfRun({
    orderUrl: BASE_URL,
    userUrl: USER_URL,
    productUrl: PRODUCT_URL,
    timeout: K6_HTTP_TIMEOUT,
    postCleanup: POST_CLEANUP,
    drainTerminalizationsFirst: DRAIN_TERMINALIZATIONS_FIRST,
    drainOptions: {
      maxAttempts: TERMINALIZATION_DRAIN_ATTEMPTS,
      pauseSeconds: TERMINALIZATION_DRAIN_PAUSE_SECONDS,
    },
  });
}
