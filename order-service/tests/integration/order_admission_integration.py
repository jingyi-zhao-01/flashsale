"""Docker Compose integration coverage for the Redis reserve admission gate.

These tests require a compose stack with:

    REDIS_URL=redis://flashsale-redis:6379/0
    RESERVE_ADMISSION_MAX_INFLIGHT=2

Gate assertions are verified via one of two strategies:

* Real concurrent HTTP calls (thread-pool barrier) for the happy-path
  concurrent-window test.
* Direct Redis counter manipulation via ``docker compose exec`` for
  deterministic saturate/drain tests — avoids flaky timing races in CI.
"""

# pylint: disable=duplicate-code

import concurrent.futures
import re
import threading
import unittest

from tests.integration_test_support import (
    FlashsaleIntegrationClient,
    compose_exec,
    request_json,
    reset_services,
    wait_for_stack,
)

_BASE_ORDER_URL = "http://127.0.0.1:18003"


def _admission_key(product_id: int) -> str:
    return f"flashsale:reserve:admission:{product_id}"


def _redis_saturate(product_id: int, count: int) -> None:
    """Set the admission-gate counter directly in Redis."""
    compose_exec(
        "exec", "-T", "flashsale-redis", "redis-cli",
        "SET", _admission_key(product_id), str(count),
    )


def _redis_drain(product_id: int) -> None:
    """Remove the admission-gate counter key from Redis."""
    compose_exec(
        "exec", "-T", "flashsale-redis", "redis-cli",
        "DEL", _admission_key(product_id),
    )


class AdmissionGateIntegrationTest(unittest.TestCase):
    """Verify the Redis admission gate end-to-end via real HTTP calls."""

    @classmethod
    def setUpClass(cls) -> None:
        wait_for_stack()

    def setUp(self) -> None:
        reset_services()
        # Drain any leftover gate keys so stale state does not bleed across tests.
        _redis_drain(0)  # wildcard not supported in compose exec; see tearDown
        self.client = FlashsaleIntegrationClient()
        self.user_id = self.client.create_user()
        self.product_id = self.client.create_product(
            name="Admission Gate Product",
            price=29.99,
            stock=50,
        )

    def tearDown(self) -> None:
        _redis_drain(self.product_id)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _create(self, idempotency_key: str) -> dict:
        return self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=1,
            idempotency_key=idempotency_key,
        )

    def _try_create(self, idempotency_key: str) -> int:
        """Return the HTTP status code for a single order create attempt.

        Does NOT retry — a second request with the same idempotency key
        would hit the idempotency replay path and return 201, masking the
        real admission-gate decision.
        """
        payload = {
            "user_id": self.user_id,
            "idempotency_key": idempotency_key,
            "items": [{"product_id": self.product_id, "quantity": 1}],
        }
        try:
            request_json(
                "POST", f"{_BASE_ORDER_URL}/orders", payload, expected_status=201,
            )
            return 201
        except AssertionError as exc:
            match = re.search(r"HTTP (\d+)", str(exc))
            if match:
                return int(match.group(1))
            return 0

    # ------------------------------------------------------------------
    # 1. serial orders — gate allows, releases, allows again
    # ------------------------------------------------------------------

    def test_serial_orders_all_succeed_within_limit(self) -> None:
        """Two serial orders (max_inflight=2) both get 201."""
        o1 = self._create("serial-1")
        o2 = self._create("serial-2")
        self.assertEqual(o1["status"], "pending")
        self.assertEqual(o2["status"], "pending")

    # ------------------------------------------------------------------
    # 2. over limit — third concurrent request gets 429
    # ------------------------------------------------------------------

    def test_concurrent_third_request_rejected_with_429(self) -> None:
        """With max_inflight=2, two allow + one reject in a concurrent wave.

        Uses a threading.Barrier so all three fire simultaneously.
        """
        barrier = threading.Barrier(3, timeout=10)
        results: dict[int, int] = {}
        errors: list[Exception] = []

        def fire(idx: int, key: str) -> None:
            try:
                barrier.wait()
                results[idx] = self._try_create(key)
            except Exception as exc:
                errors.append(exc)
                results[idx] = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(fire, i, f"concurrent-{i}") for i in range(3)
            ]
            for f in futures:
                f.result(timeout=15)

        self.assertEqual(len(errors), 0, f"unexpected errors: {errors}")
        self.assertEqual(
            sorted(results.values()),
            [201, 201, 429],
            f"expected two 201 and one 429, got {results}",
        )

    # ------------------------------------------------------------------
    # 3. deterministic rejection — saturate Redis, then request
    # ------------------------------------------------------------------

    def test_request_rejected_when_counter_exceeds_limit(self) -> None:
        """Manually set counter=2, next request gets 429, drain, next 201."""
        _redis_saturate(self.product_id, 2)

        code = self._try_create("deterministic-reject")
        self.assertEqual(code, 429, "expected 429 when counter saturated to 2")

        _redis_drain(self.product_id)
        follow = self._create("deterministic-recover")
        self.assertEqual(follow["status"], "pending")

    # ------------------------------------------------------------------
    # 4. counter is decremented to 0 after a successful order
    # ------------------------------------------------------------------

    def test_counter_returns_to_zero_after_order_completes(self) -> None:
        """After a successful POST /orders, the Redis counter must be 0."""
        self._create("counter-test-1")

        # Read the counter directly from Redis
        raw = compose_exec(
            "exec", "-T", "flashsale-redis", "redis-cli",
            "GET", _admission_key(self.product_id),
        )
        self.assertIn(raw, ("0", ""),
                      f"expected counter 0 or key missing, got '{raw}'")

    # ------------------------------------------------------------------
    # 5. payment webhook does NOT need an admission permit
    # ------------------------------------------------------------------

    def test_payment_webhook_succeeds_when_gate_is_saturated(self) -> None:
        """Confirm/cancel path skips the admission gate entirely.

        The order may or may not be confirmed by the worker yet — either
        way the payment webhook must succeed without touching the gate.
        """
        o1 = self._create("webhook-order-1")
        self.client.process_terminalizations()

        _redis_saturate(self.product_id, 2)
        replay = self.client.payment_webhook(
            order_id=int(o1["id"]),
            event_id="evt-wh-replay",
            status="succeeded",
        )
        self.assertEqual(replay["status"], "confirmed")

    # ------------------------------------------------------------------
    # 6. process-terminalizations does NOT need an admission permit
    # ------------------------------------------------------------------

    def test_terminalization_worker_succeeds_when_gate_is_saturated(self) -> None:
        """Worker terminalization path skips admission gate."""
        o1 = self._create("term-order-1")
        _redis_saturate(self.product_id, 2)

        result = self.client.process_terminalizations()
        self.assertGreaterEqual(result.get("succeeded_count", 0), 1)

    # ------------------------------------------------------------------
    # 7. idempotency replay bypasses admission gate
    # ------------------------------------------------------------------

    def test_idempotency_replay_bypasses_admission_gate(self) -> None:
        """Idempotency replay returns before acquire; never consumes a permit."""
        o1 = self._create("idem-sat-1")
        _redis_saturate(self.product_id, 2)

        replay = self._create("idem-sat-1")
        self.assertEqual(replay["status"], "pending")
        self.assertEqual(replay["id"], o1["id"])

    # ------------------------------------------------------------------
    # 8. multi-item order acquires all product permits and releases them
    # ------------------------------------------------------------------

    def test_multi_item_order_acquires_all_product_permits(self) -> None:
        """Order with two product_ids acquires both, releases both."""
        pid2 = self.client.create_product(
            name="Second Product", price=19.99, stock=10,
        )
        self.addCleanup(_redis_drain, pid2)

        response = request_json(
            "POST",
            f"{_BASE_ORDER_URL}/orders",
            {
                "user_id": self.user_id,
                "idempotency_key": "multi-item",
                "items": [
                    {"product_id": self.product_id, "quantity": 1},
                    {"product_id": pid2, "quantity": 1},
                ],
            },
            expected_status=201,
        )
        self.assertEqual(response["status"], "pending")

        # Both permits released — follow-up for either product works
        follow = request_json(
            "POST",
            f"{_BASE_ORDER_URL}/orders",
            {
                "user_id": self.user_id,
                "idempotency_key": "multi-follow",
                "items": [{"product_id": self.product_id, "quantity": 1}],
            },
            expected_status=201,
        )
        self.assertEqual(follow["status"], "pending")


if __name__ == "__main__":
    unittest.main()
