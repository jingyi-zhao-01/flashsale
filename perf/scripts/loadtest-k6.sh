#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${KUBECONFIG_PATH:-}" ]]; then
  KUBECONFIG_PATH="$KUBECONFIG_PATH"
elif [[ -f .kube-config ]]; then
  KUBECONFIG_PATH=".kube-config"
else
  KUBECONFIG_PATH="$HOME/.kube/config"
fi

NAMESPACE="${NAMESPACE:-flashsales}"
LOADTEST_SCRIPT="${LOADTEST_SCRIPT:-$SCRIPT_DIR/../k6/scenarios/loadtest.js}"
WAIT_HTTP_RETRIES="${WAIT_HTTP_RETRIES:-40}"
WAIT_HTTP_SLEEP_SEC="${WAIT_HTTP_SLEEP_SEC:-1}"
CURL_CONNECT_TIMEOUT_SEC="${CURL_CONNECT_TIMEOUT_SEC:-2}"
CURL_MAX_TIME_SEC="${CURL_MAX_TIME_SEC:-3}"
USE_K8S_PORT_FORWARD="${USE_K8S_PORT_FORWARD:-false}"
USER_LOCAL_PORT="${USER_LOCAL_PORT:-18080}"
PRODUCT_LOCAL_PORT="${PRODUCT_LOCAL_PORT:-18081}"
ORDER_LOCAL_PORT="${ORDER_LOCAL_PORT:-18082}"
PERF_RESULTS_DIR="${PERF_RESULTS_DIR:-}"

PORT_FORWARD_PIDS=()

if [[ ! -r "$KUBECONFIG_PATH" ]]; then
  echo "Kubeconfig not found or not readable: $KUBECONFIG_PATH" >&2
  echo "Set KUBECONFIG_PATH explicitly or provide .kube-config / $HOME/.kube/config" >&2
  exit 1
fi

if [[ ! -r "$LOADTEST_SCRIPT" ]]; then
  echo "Loadtest script not found or not readable: $LOADTEST_SCRIPT" >&2
  exit 1
fi

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1"
    exit 1
  fi
}

ensure_node24() {
  local required_major="${NODE_MAJOR_REQUIRED:-24}"
  local current=""

  if command -v node >/dev/null 2>&1; then
    current="$(node -v 2>/dev/null || true)"
    if [[ "$current" =~ ^v${required_major}\. ]]; then
      echo "Using Node.js ${current}"
      return 0
    fi
  fi

  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    # shellcheck disable=SC1090
    . "$NVM_DIR/nvm.sh"
    nvm install "$required_major" >/dev/null
    nvm use "$required_major" >/dev/null
    current="$(node -v 2>/dev/null || true)"
    if [[ "$current" =~ ^v${required_major}\. ]]; then
      echo "Using Node.js ${current} via nvm"
      return 0
    fi
  fi

  echo "Node.js ${required_major}.x is required by this load test wrapper."
  echo "k6 runtime itself does not need Node.js, but this project standardizes on Node ${required_major}."
  exit 1
}

wait_http() {
  local url="$1"
  local name="$2"
  local i
  echo "Waiting for ${name} at ${url}"
  for i in $(seq 1 "$WAIT_HTTP_RETRIES"); do
    if curl --connect-timeout "$CURL_CONNECT_TIMEOUT_SEC" --max-time "$CURL_MAX_TIME_SEC" -fsS "$url/health" >/dev/null 2>&1 || \
      curl --connect-timeout "$CURL_CONNECT_TIMEOUT_SEC" --max-time "$CURL_MAX_TIME_SEC" -fsS "$url/docs" >/dev/null 2>&1; then
      echo "Service ready: ${name} at ${url}"
      return 0
    fi
    echo "Still waiting for ${name} (${i}/${WAIT_HTTP_RETRIES}): ${url}"
    sleep "$WAIT_HTTP_SLEEP_SEC"
  done
  echo "Timed out waiting for ${name} on ${url} after ${WAIT_HTTP_RETRIES} attempts"
  exit 1
}

cleanup() {
  if [[ "${#PORT_FORWARD_PIDS[@]}" -gt 0 ]]; then
    for pid in "${PORT_FORWARD_PIDS[@]}"; do
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
    done
  fi
}
trap cleanup EXIT

require_cmd kubectl
require_cmd k6
require_cmd curl
ensure_node24

start_port_forward() {
  local service_name="$1"
  local local_port="$2"
  local remote_port="$3"
  local log_file
  local pid

  log_file="$(mktemp)"
  echo "Starting port-forward for ${service_name}: 127.0.0.1:${local_port} -> ${remote_port}"
  kubectl -n "$NAMESPACE" port-forward "svc/${service_name}" "${local_port}:${remote_port}" \
    >"$log_file" 2>&1 &
  pid=$!
  PORT_FORWARD_PIDS+=("$pid")

  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "Port-forward for ${service_name} exited early:" >&2
      cat "$log_file" >&2
      exit 1
    fi

    if grep -q "Forwarding from" "$log_file"; then
      return 0
    fi
    sleep 1
  done

  echo "Timed out waiting for port-forward on ${service_name}" >&2
  cat "$log_file" >&2
  exit 1
}

if [[ "$USE_K8S_PORT_FORWARD" == "true" ]]; then
  start_port_forward "flashsales-user-service" "$USER_LOCAL_PORT" "8001"
  start_port_forward "flashsales-product-service" "$PRODUCT_LOCAL_PORT" "8002"
  start_port_forward "flashsales-order-service" "$ORDER_LOCAL_PORT" "8003"

  USER_URL="${USER_URL:-http://127.0.0.1:${USER_LOCAL_PORT}}"
  PRODUCT_URL="${PRODUCT_URL:-http://127.0.0.1:${PRODUCT_LOCAL_PORT}}"
  BASE_URL="${BASE_URL:-http://127.0.0.1:${ORDER_LOCAL_PORT}}"
else
  USER_HOST="${USER_HOST:-homelab-user-service.jzhao62.com}"
  PRODUCT_HOST="${PRODUCT_HOST:-homelab-product-service.jzhao62.com}"
  ORDER_HOST="${ORDER_HOST:-homelab-order-service.jzhao62.com}"

  USER_URL="${USER_URL:-http://$USER_HOST}"
  PRODUCT_URL="${PRODUCT_URL:-http://$PRODUCT_HOST}"
  BASE_URL="${BASE_URL:-http://$ORDER_HOST}"
fi

wait_http "$USER_URL" "user-service"
wait_http "$PRODUCT_URL" "product-service"
wait_http "$BASE_URL" "order-service"

K6_ARGS=()
if [[ -n "$PERF_RESULTS_DIR" ]]; then
  mkdir -p "$PERF_RESULTS_DIR"
  K6_SUMMARY_EXPORT="${K6_SUMMARY_EXPORT:-$PERF_RESULTS_DIR/k6-summary.json}"
  K6_ARGS+=(--summary-export "$K6_SUMMARY_EXPORT")
fi

k6 run \
  "${K6_ARGS[@]}" \
  -e USER_URL="$USER_URL" \
  -e PRODUCT_URL="$PRODUCT_URL" \
  -e BASE_URL="$BASE_URL" \
  "$@" \
  "$LOADTEST_SCRIPT"
