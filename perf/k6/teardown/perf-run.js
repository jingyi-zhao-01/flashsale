import http from "k6/http";
import { sleep } from "k6";

import { resetAllServices } from "../lib/k6-common.js";

export function drainTerminalizations({
  orderUrl,
  timeout,
  maxAttempts = 20,
  pauseSeconds = 0.25,
  tags = { phase: "teardown", service_class: "maintenance" },
}) {
  // 对异步终态化路径，teardown 前先尽量把队列里的任务跑完，
  // 这样既方便看 Grafana 的时序，也能避免旧任务泄漏到下一轮压测。
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const res = http.post(`${orderUrl}/admin/process-terminalizations`, null, {
      name: "teardown/process-terminalizations",
      tags: {
        ...tags,
        operation: "process_terminalizations",
      },
      timeout,
    });
    if (res.status !== 200) {
      console.log(
        `[k6-maintenance] terminalization_drain_failed endpoint=${orderUrl}/admin/process-terminalizations attempt=${attempt} status=${res.status}`,
      );
      return false;
    }

    const body = res.json();
    const claimedCount = Number(body.claimed_count || 0);
    const retryingCount = Number(body.retrying_count || 0);
    if (claimedCount === 0 && retryingCount === 0) {
      console.log(
        `[k6-maintenance] terminalization_drain_complete endpoint=${orderUrl}/admin/process-terminalizations attempts=${attempt} claimed_count=${claimedCount} retrying_count=${retryingCount}`,
      );
      return true;
    }

    sleep(pauseSeconds);
  }

  console.log(
    "[k6-maintenance] terminalization_drain_exhausted endpoint=/admin/process-terminalizations",
  );
  return false;
}

export function cleanupPerfRun({
  orderUrl,
  userUrl,
  productUrl,
  timeout,
  postCleanup = true,
  drainTerminalizationsFirst = false,
  drainOptions = {},
}) {
  // 只有显式要求时才先 drain，因为并不是所有场景都会走异步终态化链路。
  if (drainTerminalizationsFirst) {
    drainTerminalizations({
      orderUrl,
      timeout,
      ...drainOptions,
    });
  }

  if (!postCleanup) {
    console.log("[k6-post-cleanup] skipped (POST_CLEANUP=false)");
    return {
      order: false,
      user: false,
      product: false,
      allOk: false,
    };
  }

  // 统一收尾，保证下一次 perf run 从干净状态启动。
  const results = resetAllServices({
    orderUrl,
    userUrl,
    productUrl,
    timeout,
    tags: {
      phase: "teardown",
      service_class: "maintenance",
    },
    orderOptions: { wait: false },
    userOptions: { wait: false },
  });
  const allOk =
    (results.order.status === 202 || results.order.status === 204) &&
    (results.user.status === 202 || results.user.status === 204) &&
    results.product.status === 204;

  console.log(
    `[k6-post-cleanup] completed order_reset=${results.order.status === 202 || results.order.status === 204} user_reset=${results.user.status === 202 || results.user.status === 204} product_reset=${results.product.status === 204} all_ok=${allOk}`,
  );

  return {
    order: results.order.status === 202 || results.order.status === 204,
    user: results.user.status === 202 || results.user.status === 204,
    product: results.product.status === 204,
    allOk,
  };
}
