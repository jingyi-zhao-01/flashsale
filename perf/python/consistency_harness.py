#!/usr/bin/env python3
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request


USER_URL = os.getenv("USER_URL", "http://homelab-user-service.jzhao62.com")
PRODUCT_URL = os.getenv("PRODUCT_URL", "http://homelab-product-service.jzhao62.com")
ORDER_URL = os.getenv("ORDER_URL", "http://homelab-order-service.jzhao62.com")
TOXIPROXY_URL = os.getenv("TOXIPROXY_URL", "http://127.0.0.1:8474")
PROXY_NAME = os.getenv("DB_PROXY_NAME", "flashsales-neon-db")

ORDER_TIMEOUT_SECONDS = float(os.getenv("ORDER_TIMEOUT_SECONDS", "20"))
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "0.2"))
STOCK_DROP_TIMEOUT_SECONDS = float(os.getenv("STOCK_DROP_TIMEOUT_SECONDS", "15"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
LATENCY_MS = int(os.getenv("DB_PROXY_LATENCY_MS", "2500"))


def request_json(method: str, url: str, payload: dict | None = None, timeout: float = 10.0):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            data = json.loads(raw) if raw else None
            return res.status, data
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        try:
            data = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            data = {"raw": raw}
        return err.code, data


def reset_service_state() -> None:
    for base in (ORDER_URL, USER_URL, PRODUCT_URL):
        status, _ = request_json("POST", f"{base}/admin/reset", timeout=10)
        if status != 204:
            raise RuntimeError(f"reset failed for {base}: status={status}")


def create_user(attempt: int) -> int:
    status, data = request_json(
        "POST",
        f"{USER_URL}/users",
        {"email": f"consistency-{attempt}-{time.time_ns()}@example.com", "name": "consistency"},
        timeout=10,
    )
    if status not in (200, 201) or not data:
        raise RuntimeError(f"user create failed: status={status} body={data}")
    return int(data["id"])


def create_product(stock: int = 5) -> int:
    status, data = request_json(
        "POST",
        f"{PRODUCT_URL}/products",
        {"name": f"consistency-{time.time_ns()}", "price": 9.99, "stock": stock},
        timeout=10,
    )
    if status not in (200, 201) or not data:
        raise RuntimeError(f"product create failed: status={status} body={data}")
    return int(data["id"])


def get_product(product_id: int) -> dict:
    status, data = request_json("GET", f"{PRODUCT_URL}/products/{product_id}", timeout=10)
    if status != 200 or not data:
        raise RuntimeError(f"product get failed: status={status} body={data}")
    return data


def list_orders() -> list:
    status, data = request_json("GET", f"{ORDER_URL}/orders", timeout=10)
    if status != 200 or data is None:
        raise RuntimeError(f"orders list failed: status={status} body={data}")
    return data


def proxy_request(method: str, path: str, payload: dict | None = None):
    return request_json(method, f"{TOXIPROXY_URL}{path}", payload=payload, timeout=10)


def ensure_proxy_available() -> None:
    status, data = proxy_request("GET", f"/proxies/{PROXY_NAME}")
    if status != 200:
        raise RuntimeError(f"db proxy unavailable: status={status} body={data}")


def set_proxy_enabled(enabled: bool) -> None:
    status, data = proxy_request("POST", f"/proxies/{PROXY_NAME}", {"enabled": enabled})
    if status != 200:
        raise RuntimeError(f"proxy toggle failed: enabled={enabled} status={status} body={data}")


def list_toxics() -> list[dict]:
    status, data = proxy_request("GET", f"/proxies/{PROXY_NAME}/toxics")
    if status != 200:
        raise RuntimeError(f"list toxics failed: status={status} body={data}")
    return data or []


def clear_toxics() -> None:
    for toxic in list_toxics():
        name = toxic["name"]
        status, data = proxy_request("DELETE", f"/proxies/{PROXY_NAME}/toxics/{name}")
        if status not in (200, 204):
            raise RuntimeError(f"delete toxic failed: name={name} status={status} body={data}")


def add_latency_toxic() -> None:
    payload = {
        "name": "db-write-latency",
        "type": "latency",
        "stream": "downstream",
        "attributes": {"latency": LATENCY_MS, "jitter": 0},
    }
    status, data = proxy_request("POST", f"/proxies/{PROXY_NAME}/toxics", payload)
    if status not in (200, 201):
        raise RuntimeError(f"add latency toxic failed: status={status} body={data}")


def submit_order(user_id: int, product_id: int):
    return request_json(
        "POST",
        f"{ORDER_URL}/orders",
        {"user_id": user_id, "items": [{"product_id": product_id, "quantity": 1}]},
        timeout=ORDER_TIMEOUT_SECONDS,
    )


def attempt_inventory_leak(attempt: int) -> None:
    print(f"[consistency] attempt={attempt} starting")
    reset_service_state()
    ensure_proxy_available()
    set_proxy_enabled(True)
    clear_toxics()
    add_latency_toxic()

    user_id = create_user(attempt)
    product_id = create_product(stock=5)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(submit_order, user_id, product_id)

        stock_dropped = False
        deadline = time.time() + STOCK_DROP_TIMEOUT_SECONDS
        while time.time() < deadline:
            product = get_product(product_id)
            stock = int(product["stock"])
            if stock == 4:
                stock_dropped = True
                print(f"[consistency] attempt={attempt} reserve_observed stock={stock}")
                set_proxy_enabled(False)
                break
            if future.done():
                break
            time.sleep(POLL_INTERVAL_SECONDS)

        try:
            order_status, order_body = future.result(timeout=ORDER_TIMEOUT_SECONDS)
        finally:
            set_proxy_enabled(True)
            clear_toxics()

    orders = list_orders()
    product_after = get_product(product_id)
    remaining_stock = int(product_after["stock"])

    print(
        "[consistency] attempt=%s order_status=%s stock_dropped=%s order_count=%s remaining_stock=%s"
        % (attempt, order_status, stock_dropped, len(orders), remaining_stock)
    )

    if not stock_dropped:
        raise RuntimeError(f"attempt={attempt} never observed reserve before order finished")

    if order_status >= 500 and len(orders) == 0 and remaining_stock == 4:
        raise AssertionError(
            "inventory leaked after persistence failure: order failed, no order persisted, stock was still consumed"
        )

    if order_status in (200, 201):
        raise RuntimeError(
            f"attempt={attempt} order unexpectedly succeeded before DB fault injection landed"
        )

    raise RuntimeError(
        f"attempt={attempt} did not reproduce expected leak signature; "
        f"status={order_status} orders={len(orders)} stock={remaining_stock} body={order_body}"
    )


def main() -> int:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            attempt_inventory_leak(attempt)
        except AssertionError as exc:
            print(f"[consistency] failure_detected: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"[consistency] attempt={attempt} inconclusive: {exc}")

    print("[consistency] unable to reproduce persistence failure leak after retries")
    return 2


if __name__ == "__main__":
    sys.exit(main())
