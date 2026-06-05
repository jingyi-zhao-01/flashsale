import http from "k6/http";
import { check } from "k6";
import exec from "k6/execution";

export function envString(name, fallback) {
  return __ENV[name] || fallback;
}

export function envNumber(name, fallback) {
  return Number(__ENV[name] || fallback);
}

export function buildRampOptions({
  rampUp,
  steady,
  rampDown,
  targetVus,
  thresholds,
}) {
  return {
    stages: [
      { duration: rampUp, target: targetVus },
      { duration: steady, target: targetVus },
      { duration: rampDown, target: 0 },
    ],
    thresholds,
  };
}

export function buildConstantArrivalRateOptions({
  scenarioName,
  rate,
  duration,
  preAllocatedVUs,
  maxVUs,
  setupTimeout,
  thresholds,
}) {
  return {
    scenarios: {
      [scenarioName]: {
        executor: "constant-arrival-rate",
        rate,
        timeUnit: "1s",
        duration,
        preAllocatedVUs,
        maxVUs,
      },
    },
    ...(setupTimeout ? { setupTimeout } : {}),
    thresholds,
  };
}

export function createPostJson(timeout, defaultOptions = {}) {
  return function postJson(url, body, requestOptions = {}) {
    const defaultHeaders = defaultOptions.headers || {};
    const requestHeaders = requestOptions.headers || {};
    const defaultTags = defaultOptions.tags || {};
    const requestTags = requestOptions.tags || {};

    return http.post(url, JSON.stringify(body), {
      ...defaultOptions,
      ...requestOptions,
      headers: {
        ...defaultHeaders,
        ...requestHeaders,
        "Content-Type": "application/json",
      },
      tags: {
        ...defaultTags,
        ...requestTags,
      },
      timeout,
    });
  };
}

export function checkStatus(res, label, acceptedStatuses) {
  const allowed = Array.isArray(acceptedStatuses)
    ? acceptedStatuses
    : [acceptedStatuses];
  return check(res, {
    [label]: (response) => allowed.includes(response.status),
  });
}

export function resetService(url, serviceName, timeout, tags = {}) {
  const res = http.post(`${url}/admin/reset`, null, { timeout, tags });
  checkStatus(res, `${serviceName} database reset`, 204);
  return res;
}

export function resetAllServices({
  orderUrl,
  userUrl,
  productUrl,
  timeout,
  tags = {},
}) {
  const orderRes = resetService(orderUrl, "order", timeout, tags);
  const userRes = resetService(userUrl, "user", timeout, tags);
  const productRes = resetService(productUrl, "product", timeout, tags);
  return {
    order: orderRes,
    user: userRes,
    product: productRes,
  };
}

export function seedProducts(productUrl, timeout) {
  const res = http.post(`${productUrl}/admin/seed`, null, { timeout });
  checkStatus(res, "products seeded", 204);
  return res;
}

export function createUser({ userUrl, timeout, email, name, postJson }) {
  const res = postJson(`${userUrl}/users`, { email, name });
  checkStatus(res, "setup user created", [200, 201]);
  return res.json();
}

export function createProduct({
  productUrl,
  name,
  price,
  stock,
  postJson,
  label = "setup product created",
}) {
  const res = postJson(`${productUrl}/products`, { name, price, stock });
  checkStatus(res, label, [200, 201]);
  return res.json();
}

export function createRuntimeReporter(reportIntervalMs, formatMessage) {
  let lastReportAt = Date.now();
  let lastCompletedIterations = 0;

  return function reportRuntime() {
    if (__VU !== 1) {
      return;
    }

    const now = Date.now();
    if (now - lastReportAt < reportIntervalMs) {
      return;
    }

    const completedIterations = exec.instance.iterationsCompleted;
    const elapsedSec = (now - lastReportAt) / 1000;
    const tps =
      elapsedSec > 0
        ? (completedIterations - lastCompletedIterations) / elapsedSec
        : 0;
    const activeVus = exec.instance.vusActive;

    console.log(formatMessage({ activeVus, tps }));
    lastReportAt = now;
    lastCompletedIterations = completedIterations;
  };
}

export function createUsersBatch({
  userUrl,
  postJson,
  emailPrefix,
  namePrefix,
  count,
  timestamp = Date.now(),
}) {
  const users = [];
  for (let i = 0; i < count; i += 1) {
    const user = createUser({
      userUrl,
      email: `${emailPrefix}-${i}-${timestamp}@example.com`,
      name: `${namePrefix} ${i}`,
      postJson,
    });
    users.push(user);
  }
  return users;
}

export function createProductsBatch({
  productUrl,
  postJson,
  namePrefix,
  price,
  stock,
  count,
  timestamp = Date.now(),
  labelPrefix = "setup product created",
}) {
  const products = [];
  for (let i = 0; i < count; i += 1) {
    const product = createProduct({
      productUrl,
      name: `${namePrefix}-${i}-${timestamp}`,
      price,
      stock,
      postJson,
      label: labelPrefix,
    });
    products.push(product);
  }
  return products;
}
