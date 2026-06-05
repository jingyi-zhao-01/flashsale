import { sleep } from "k6";

// Shared factory for the three hotspot-order variants. The scenario keeps one
// seeded user/product pair hot so the differences come from ramp/threshold settings.

import {
  buildRampOptions,
  createPostJson,
  createRuntimeReporter,
  envNumber,
  envString,
  checkStatus,
} from "./k6-common.js";
import { setupSingleUserAndProductScenario } from "../setup/index.js";
import { cleanupPerfRun } from "../teardown/index.js";

export function createHotspotOrderScenario({
  defaultBaseUrl,
  defaultUserUrl,
  defaultProductUrl,
  defaultRampUp,
  defaultSteady,
  defaultRampDown,
  defaultTargetVus,
  defaultReportIntervalMs,
  defaultHttpTimeout,
  defaultP50ThresholdMs,
  defaultP90ThresholdMs,
  defaultP95ThresholdMs,
  defaultDescription,
  defaultProductStock = 100000,
}) {
  // 所有参数都允许被环境变量覆盖，方便本地、CI、不同压测强度复用同一个场景骨架。
  const BASE_URL = envString("BASE_URL", defaultBaseUrl);
  const USER_URL = envString("USER_URL", defaultUserUrl);
  const PRODUCT_URL = envString("PRODUCT_URL", defaultProductUrl);
  const RAMP_UP = envString("RAMP_UP", defaultRampUp);
  const STEADY = envString("STEADY", defaultSteady);
  const RAMP_DOWN = envString("RAMP_DOWN", defaultRampDown);
  const TARGET_VUS = envNumber("TARGET_VUS", defaultTargetVus);
  const REPORT_INTERVAL_MS = envNumber(
    "REPORT_INTERVAL_MS",
    defaultReportIntervalMs,
  );
  const K6_HTTP_TIMEOUT = envString("K6_HTTP_TIMEOUT", defaultHttpTimeout);
  const K6_P50_THRESHOLD_MS = envNumber(
    "K6_P50_THRESHOLD_MS",
    defaultP50ThresholdMs,
  );
  const K6_P90_THRESHOLD_MS = envNumber(
    "K6_P90_THRESHOLD_MS",
    defaultP90ThresholdMs,
  );
  const K6_P95_THRESHOLD_MS = envNumber(
    "K6_P95_THRESHOLD_MS",
    defaultP95ThresholdMs,
  );
  const TEST_DESCRIPTION = envString("TEST_DESCRIPTION", defaultDescription);
  const POST_CLEANUP =
    (envString("POST_CLEANUP", "true") || "true").toLowerCase() === "true";

  const postJson = createPostJson(K6_HTTP_TIMEOUT);
  const reportRuntime = createRuntimeReporter(
    REPORT_INTERVAL_MS,
    ({ activeVus, tps }) =>
      `[k6-runtime] active_vu=${activeVus} approx_tps=${tps.toFixed(2)}`,
  );

  return {
    options: buildRampOptions({
      rampUp: RAMP_UP,
      steady: STEADY,
      rampDown: RAMP_DOWN,
      targetVus: TARGET_VUS,
      thresholds: {
        http_req_failed: ["rate<0.05"],
        http_req_duration: [
          `p(50)<${K6_P50_THRESHOLD_MS}`,
          `p(90)<${K6_P90_THRESHOLD_MS}`,
          `p(95)<${K6_P95_THRESHOLD_MS}`,
        ],
      },
    }),

    setup() {
      return setupSingleUserAndProductScenario({
        description: TEST_DESCRIPTION,
        baseUrl: BASE_URL,
        userUrl: USER_URL,
        productUrl: PRODUCT_URL,
        timeout: K6_HTTP_TIMEOUT,
        emailPrefix: "k6",
        userName: "k6 user",
        productPrefix: "k6-product",
        productPrice: 9.99,
        productStock: defaultProductStock,
      });
    },

    default(data) {
      // 每次迭代都购买同一个商品，故意把竞争集中到同一条库存记录上。
      const orderRes = postJson(`${BASE_URL}/orders`, {
        user_id: data.user_id,
        items: [
          {
            product_id: data.product_id,
            quantity: 1,
          },
        ],
      });

      checkStatus(orderRes, "order accepted", [200, 201]);
      reportRuntime();
      sleep(1);
    },

    teardown() {
      cleanupPerfRun({
        orderUrl: BASE_URL,
        userUrl: USER_URL,
        productUrl: PRODUCT_URL,
        timeout: K6_HTTP_TIMEOUT,
        postCleanup: POST_CLEANUP,
      });
    },
  };
}
