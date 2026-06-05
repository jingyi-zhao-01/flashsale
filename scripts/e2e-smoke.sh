#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-flashsales}"
USER_PORT="${USER_PORT:-18001}"
PRODUCT_PORT="${PRODUCT_PORT:-18002}"
ORDER_PORT="${ORDER_PORT:-18003}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_KUBECONFIG="$REPO_ROOT/.kube-config"
if [[ -f "$REPO_KUBECONFIG" ]]; then
  DEFAULT_KUBECONFIG_PATH="$REPO_KUBECONFIG"
else
  DEFAULT_KUBECONFIG_PATH="${KUBECONFIG:-$HOME/.kube/config}"
fi
KUBECONFIG_PATH="${KUBECONFIG_PATH:-$DEFAULT_KUBECONFIG_PATH}"
RUN_ID="${RUN_ID:-$(date +%s)}"

log() {
  echo "[e2e] $*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

wait_for_http() {
  local url="$1"
  for _ in $(seq 1 40); do
    if curl --max-time 1 -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timeout waiting for $url" >&2
  exit 1
}

find_free_ports() {
  local user_start="$1"
  local product_start="$2"
  local order_start="$3"
  python3 - "$user_start" "$product_start" "$order_start" <<'PY'
import socket
import sys

starts = [int(v) for v in sys.argv[1:4]]
allocated = []

def pick_port(start, used):
  for port in range(start, start + 200):
    if port in used:
      continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      try:
        sock.bind(("127.0.0.1", port))
        return port
      except OSError:
        continue
  raise RuntimeError(f"No free port found from {start} to {start + 199}")

used = set()
for s in starts:
  p = pick_port(s, used)
  used.add(p)
  allocated.append(p)

print(" ".join(str(p) for p in allocated))
PY
}

cleanup() {
  if [[ -n "${PF_USER_PID:-}" ]]; then kill "$PF_USER_PID" >/dev/null 2>&1 || true; fi
  if [[ -n "${PF_PRODUCT_PID:-}" ]]; then kill "$PF_PRODUCT_PID" >/dev/null 2>&1 || true; fi
  if [[ -n "${PF_ORDER_PID:-}" ]]; then kill "$PF_ORDER_PID" >/dev/null 2>&1 || true; fi
}

trap cleanup EXIT

require_cmd kubectl
require_cmd curl
require_cmd python3

json_get_field() {
  local json_payload="$1"
  local field_name="$2"
  local label="$3"
  python3 - "$json_payload" "$field_name" "$label" <<'PY'
import json
import sys

payload, field, label = sys.argv[1], sys.argv[2], sys.argv[3]
try:
  obj = json.loads(payload)
except Exception as exc:
  print(f"[{label}] invalid JSON response: {payload}", file=sys.stderr)
  raise SystemExit(1) from exc

if field not in obj:
  print(f"[{label}] missing field '{field}', response: {payload}", file=sys.stderr)
  raise SystemExit(1)

print(obj[field])
PY
}

kctl() {
  kubectl --kubeconfig "$KUBECONFIG_PATH" "$@"
}

API_SERVER=$(kctl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
if [[ ! "$API_SERVER" =~ localhost|127\.0\.0\.1 ]]; then
  echo "Refusing to run against non-local cluster: $API_SERVER" >&2
  echo "This project is local-only. Set KUBECONFIG_PATH to a local k3s config." >&2
  exit 1
fi

log "Checking pods in namespace: $NAMESPACE"
kctl get pods -n "$NAMESPACE" >/dev/null

read -r USER_PORT PRODUCT_PORT ORDER_PORT <<< "$(find_free_ports "$USER_PORT" "$PRODUCT_PORT" "$ORDER_PORT")"
log "Using local ports user=${USER_PORT} product=${PRODUCT_PORT} order=${ORDER_PORT}"

log "Starting port-forward sessions"
kctl port-forward -n "$NAMESPACE" svc/flashsales-user-service "$USER_PORT":8001 >/tmp/flashsales-pf-user.log 2>&1 &
PF_USER_PID=$!
kctl port-forward -n "$NAMESPACE" svc/flashsales-product-service "$PRODUCT_PORT":8002 >/tmp/flashsales-pf-product.log 2>&1 &
PF_PRODUCT_PID=$!
kctl port-forward -n "$NAMESPACE" svc/flashsales-order-service "$ORDER_PORT":8003 >/tmp/flashsales-pf-order.log 2>&1 &
PF_ORDER_PID=$!

wait_for_http "http://127.0.0.1:${USER_PORT}/health"
wait_for_http "http://127.0.0.1:${PRODUCT_PORT}/health"
wait_for_http "http://127.0.0.1:${ORDER_PORT}/health"

log "Creating user"
USER_EMAIL="e2e.user.${RUN_ID}@example.com"
USER_RESP=$(curl -sS -X POST "http://127.0.0.1:${USER_PORT}/users" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"E2E User\",\"email\":\"${USER_EMAIL}\"}")
USER_ID=$(json_get_field "$USER_RESP" "id" "create-user")

log "Creating product"
PRODUCT_RESP=$(curl -sS -X POST "http://127.0.0.1:${PRODUCT_PORT}/products" \
  -H 'Content-Type: application/json' \
  -d '{"name":"E2E Product","price":99.9,"stock":5}')
PRODUCT_ID=$(json_get_field "$PRODUCT_RESP" "id" "create-product")

log "Creating order"
ORDER_RESP=$(curl -sS -X POST "http://127.0.0.1:${ORDER_PORT}/orders" \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\":${USER_ID},\"items\":[{\"product_id\":${PRODUCT_ID},\"quantity\":2}]}")

ORDER_ID=$(json_get_field "$ORDER_RESP" "id" "create-order")
TOTAL_AMOUNT=$(json_get_field "$ORDER_RESP" "total_amount" "create-order")

log "Verifying product stock reduced"
PRODUCT_AFTER=$(curl -sS "http://127.0.0.1:${PRODUCT_PORT}/products/${PRODUCT_ID}")
STOCK_AFTER=$(json_get_field "$PRODUCT_AFTER" "stock" "verify-stock")
if [[ "$STOCK_AFTER" != "3" ]]; then
  echo "Unexpected stock after order: $STOCK_AFTER (expected 3)" >&2
  exit 1
fi

log "E2E PASS"
echo "order_id=${ORDER_ID} total_amount=${TOTAL_AMOUNT} user_id=${USER_ID} product_id=${PRODUCT_ID}"
