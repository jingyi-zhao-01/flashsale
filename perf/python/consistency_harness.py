#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.request


USER_URL = os.getenv("USER_URL", "http://homelab-user-service.jzhao62.com")
PRODUCT_URL = os.getenv("PRODUCT_URL", "http://homelab-product-service.jzhao62.com")
ORDER_URL = os.getenv("ORDER_URL", "http://homelab-order-service.jzhao62.com")

ORDER_CONFIRM_TIMEOUT_SECONDS = float(os.getenv("ORDER_CONFIRM_TIMEOUT_SECONDS", "15"))
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "0.2"))


def request_json(
    method: str, url: str, payload: dict | None = None, timeout: float = 10.0
):
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


def create_user(label: str) -> int:
    status, data = request_json(
        "POST",
        f"{USER_URL}/users",
        {"email": f"{label}-{time.time_ns()}@example.com", "name": label},
        timeout=10,
    )
    if status not in (200, 201) or not data:
        raise RuntimeError(f"user create failed: status={status} body={data}")
    return int(data["id"])


def create_product(stock: int) -> int:
    status, data = request_json(
        "POST",
        f"{PRODUCT_URL}/products",
        {"name": f"consistency-{time.time_ns()}", "price": 9.99, "stock": stock},
        timeout=10,
    )
    if status not in (200, 201) or not data:
        raise RuntimeError(f"product create failed: status={status} body={data}")
    return int(data["id"])


def submit_order(user_id: int, product_id: int, idempotency_key: str | None = None):
    payload = {
        "user_id": user_id,
        "items": [{"product_id": product_id, "quantity": 1}],
    }
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    return request_json("POST", f"{ORDER_URL}/orders", payload, timeout=15)


def get_order(order_id: int) -> dict:
    status, data = request_json("GET", f"{ORDER_URL}/orders/{order_id}", timeout=10)
    if status != 200 or not data:
        raise RuntimeError(f"order get failed: status={status} body={data}")
    return data


def list_orders() -> list[dict]:
    status, data = request_json("GET", f"{ORDER_URL}/orders", timeout=10)
    if status != 200 or data is None:
        raise RuntimeError(f"orders list failed: status={status} body={data}")
    return data


def get_product(product_id: int) -> dict:
    status, data = request_json("GET", f"{PRODUCT_URL}/products/{product_id}", timeout=10)
    if status != 200 or not data:
        raise RuntimeError(f"product get failed: status={status} body={data}")
    return data


def wait_for_order_status(order_id: int, expected_status: str) -> dict:
    deadline = time.time() + ORDER_CONFIRM_TIMEOUT_SECONDS
    last_order = None
    while time.time() < deadline:
        last_order = get_order(order_id)
        if last_order["status"] == expected_status:
            return last_order
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        f"order {order_id} did not reach status={expected_status}; last={last_order}"
    )


def assert_successful_order_flow() -> None:
    user_id = create_user("consistency-success")
    product_id = create_product(stock=2)

    status, order = submit_order(
        user_id, product_id, idempotency_key=f"success-{time.time_ns()}"
    )
    if status not in (200, 201) or not order:
        raise RuntimeError(f"order create failed: status={status} body={order}")

    persisted = wait_for_order_status(int(order["id"]), "confirmed")
    product = get_product(product_id)

    if int(product["stock"]) != 1:
        raise AssertionError(
            f"successful order should reduce stock to 1, got {product['stock']}"
        )

    if persisted["payment_status"] != "succeeded":
        raise AssertionError(
            f"successful order should have succeeded payment, got {persisted['payment_status']}"
        )


def assert_out_of_stock_consistency() -> None:
    first_user_id = create_user("consistency-stock-1")
    second_user_id = create_user("consistency-stock-2")
    product_id = create_product(stock=1)

    first_status, first_order = submit_order(
        first_user_id, product_id, idempotency_key=f"first-{time.time_ns()}"
    )
    if first_status not in (200, 201) or not first_order:
        raise RuntimeError(
            f"first order for stock=1 product failed: status={first_status} body={first_order}"
        )

    wait_for_order_status(int(first_order["id"]), "confirmed")

    second_status, second_body = submit_order(
        second_user_id, product_id, idempotency_key=f"second-{time.time_ns()}"
    )
    if second_status != 409:
        raise AssertionError(
            f"second order should be out-of-stock 409, got status={second_status} body={second_body}"
        )

    orders = list_orders()
    product = get_product(product_id)

    if len(orders) != 1:
        raise AssertionError(f"expected exactly one persisted order, got {len(orders)}")

    if int(product["stock"]) != 0:
        raise AssertionError(
            f"out-of-stock scenario should leave stock at 0, got {product['stock']}"
        )


def main() -> int:
    try:
        reset_service_state()
        assert_successful_order_flow()
        assert_out_of_stock_consistency()
    except AssertionError as exc:
        print(f"[consistency] failed: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[consistency] error: {exc}")
        return 2

    print("[consistency] passed: runtime order/product consistency checks succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
