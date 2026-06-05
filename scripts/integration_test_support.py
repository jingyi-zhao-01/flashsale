from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = Path(
    os.environ.get(
        "COMPOSE_FILE",
        str(REPO_ROOT / "application" / "flashsale" / "docker-compose.yaml"),
    )
)
BASE_USER_URL = os.environ.get("BASE_USER_URL", "http://127.0.0.1:18001")
BASE_PRODUCT_URL = os.environ.get("BASE_PRODUCT_URL", "http://127.0.0.1:18002")
BASE_ORDER_URL = os.environ.get("BASE_ORDER_URL", "http://127.0.0.1:18003")
RUN_ID = os.environ.get("RUN_ID", str(int(time.time())))


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    expected_status: int | Iterable[int] = 200,
) -> dict[str, Any]:
    allowed_statuses = (
        {expected_status}
        if isinstance(expected_status, int)
        else {status for status in expected_status}
    )
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read().decode("utf-8")
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        if exc.code not in allowed_statuses:
            raise AssertionError(
                f"Unexpected HTTP {exc.code} for {method} {url}: {body}"
            ) from exc
        return json.loads(body) if body else {}
    except (urllib.error.URLError, OSError) as exc:
        # Docker Compose can report a container as started slightly before the
        # HTTP server is ready to answer health probes. Treat those socket-level
        # resets/refusals as transient so the readiness poll can retry.
        raise AssertionError(f"Request failed for {method} {url}: {exc}") from exc

    if status not in allowed_statuses:
        raise AssertionError(f"Unexpected HTTP {status} for {method} {url}: {body}")
    return json.loads(body) if body else {}


def wait_for_http(url: str, attempts: int = 60) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            request_json("GET", url)
            return
        except AssertionError as exc:
            last_error = exc
            time.sleep(1)
    if last_error is not None:
        raise AssertionError(f"Timeout waiting for {url}: {last_error}") from last_error
    raise AssertionError(f"Timeout waiting for {url}")


def wait_for_stack() -> None:
    wait_for_http(f"{BASE_USER_URL}/health")
    wait_for_http(f"{BASE_PRODUCT_URL}/health")
    wait_for_http(f"{BASE_ORDER_URL}/health")


def reset_services() -> None:
    request_json("POST", f"{BASE_USER_URL}/admin/reset", expected_status=204)
    request_json("POST", f"{BASE_PRODUCT_URL}/admin/reset", expected_status=204)
    request_json("POST", f"{BASE_ORDER_URL}/admin/reset", expected_status=204)


def compose_exec(*args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip().replace("\r", "")


class FlashsaleIntegrationClient:
    def create_user(self) -> int:
        response = request_json(
            "POST",
            f"{BASE_USER_URL}/users",
            {
                "name": "Compose User",
                "email": f"compose.user.{RUN_ID}.{time.time_ns()}@example.com",
            },
            expected_status=(200, 201),
        )
        return int(response["id"])

    def get_user(self, user_id: int) -> dict[str, Any]:
        return request_json("GET", f"{BASE_USER_URL}/users/{user_id}")

    def list_users(self) -> list[dict[str, Any]]:
        return request_json("GET", f"{BASE_USER_URL}/users")  # type: ignore[return-value]

    def create_product(self, *, name: str, price: float, stock: int) -> int:
        response = request_json(
            "POST",
            f"{BASE_PRODUCT_URL}/products",
            {"name": name, "price": price, "stock": stock},
            expected_status=(200, 201),
        )
        return int(response["id"])

    def get_product(self, product_id: int) -> dict[str, Any]:
        return request_json("GET", f"{BASE_PRODUCT_URL}/products/{product_id}")

    def list_products(self) -> list[dict[str, Any]]:
        return request_json("GET", f"{BASE_PRODUCT_URL}/products")  # type: ignore[return-value]

    def create_order(
        self,
        *,
        user_id: int,
        product_id: int,
        quantity: int,
        idempotency_key: str,
        expected_status: int | Iterable[int] = (200, 201),
    ) -> dict[str, Any]:
        return request_json(
            "POST",
            f"{BASE_ORDER_URL}/orders",
            {
                "user_id": user_id,
                "idempotency_key": idempotency_key,
                "items": [{"product_id": product_id, "quantity": quantity}],
            },
            expected_status=expected_status,
        )

    def get_order(self, order_id: int) -> dict[str, Any]:
        return request_json("GET", f"{BASE_ORDER_URL}/orders/{order_id}")

    def list_orders(self) -> list[dict[str, Any]]:
        return request_json("GET", f"{BASE_ORDER_URL}/orders")  # type: ignore[return-value]

    def payment_webhook(
        self, *, order_id: int, event_id: str, status: str
    ) -> dict[str, Any]:
        return request_json(
            "POST",
            f"{BASE_ORDER_URL}/payments/webhook",
            {"order_id": order_id, "event_id": event_id, "status": status},
        )

    def reserve_product(self, *, product_id: int, quantity: int) -> dict[str, Any]:
        return request_json(
            "POST",
            f"{BASE_PRODUCT_URL}/products/{product_id}/reserve",
            {"quantity": quantity},
        )

    def expire_orders(self) -> dict[str, Any]:
        return request_json("POST", f"{BASE_ORDER_URL}/admin/expire-orders")

    def process_terminalizations(self) -> dict[str, Any]:
        return request_json("POST", f"{BASE_ORDER_URL}/admin/process-terminalizations")

    def seed_pending_order(
        self, *, user_id: int, product_id: int, reservation_id: int
    ) -> int:
        env = os.environ.copy()
        env.update(
            {
                "SEED_USER_ID": str(user_id),
                "SEED_PRODUCT_ID": str(product_id),
                "SEED_RESERVATION_ID": str(reservation_id),
            }
        )
        output = compose_exec(
            "exec",
            "-T",
            "-e",
            "SEED_USER_ID",
            "-e",
            "SEED_PRODUCT_ID",
            "-e",
            "SEED_RESERVATION_ID",
            "order-service",
            "python",
            "-c",
            (
                "import json\n"
                "import os\n"
                "import psycopg\n"
                "\n"
                "database_url = os.environ['DATABASE_URL']\n"
                "user_id = int(os.environ['SEED_USER_ID'])\n"
                "product_id = int(os.environ['SEED_PRODUCT_ID'])\n"
                "reservation_id = int(os.environ['SEED_RESERVATION_ID'])\n"
                "\n"
                "with psycopg.connect(database_url, autocommit=True) as conn:\n"
                "    with conn.cursor() as cur:\n"
                "        cur.execute(\n"
                "            '''\n"
                "            INSERT INTO orders (\n"
                "                user_id,\n"
                "                total_amount,\n"
                "                status,\n"
                "                payment_status,\n"
                "                idempotency_key,\n"
                "                reservation_ids_json,\n"
                "                items_json,\n"
                "                created_at\n"
                "            )\n"
                "            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NOW() - interval '5 minutes')\n"
                "            RETURNING id\n"
                "            ''',\n"
                "            (\n"
                "                user_id,\n"
                "                29.99,\n"
                "                'pending',\n"
                "                'pending',\n"
                "                'compose-pending-order',\n"
                "                json.dumps([reservation_id]),\n"
                "                json.dumps([{\n"
                "                    'product_id': product_id,\n"
                "                    'quantity': 1,\n"
                "                    'unit_price': 29.99,\n"
                "                    'line_total': 29.99,\n"
                "                }]),\n"
                "            ),\n"
                "        )\n"
                "        row = cur.fetchone()\n"
                "        print(json.dumps({'id': row[0]}))\n"
            ),
            env=env,
        )
        return int(json.loads(output)["id"])
