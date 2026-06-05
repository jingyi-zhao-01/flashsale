"""Docker Compose integration coverage for the order service."""

# pylint: disable=duplicate-code

import unittest
from tests.integration_test_support import (
    FlashsaleIntegrationClient,
    request_json,
    reset_services,
    wait_for_stack,
)


class OrderServiceComposeIntegrationTest(unittest.TestCase):
    """Exercise order-service flows against the compose-backed stack."""

    @classmethod
    def setUpClass(cls) -> None:
        """Wait until the compose stack is ready for integration traffic."""
        wait_for_stack()

    def setUp(self) -> None:
        """Reset shared state and seed a user plus product for each test."""
        reset_services()
        self.client = FlashsaleIntegrationClient()
        self.user_id = self.client.create_user()
        self.product_id = self.client.create_product(
            name="Compose Product",
            price=19.99,
            stock=5,
        )

    def test_create_order_confirms_payment(self) -> None:
        """Creating an order through compose should confirm payment."""
        response = self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=2,
            idempotency_key="compose-order-1",
        )
        self.client.process_terminalizations()
        order = self.client.get_order(int(response["id"]))

        self.assertEqual(order["status"], "confirmed")
        self.assertEqual(order["payment_status"], "succeeded")

    def test_duplicate_payment_webhook_is_idempotent(self) -> None:
        """Replaying a succeeded payment webhook should not alter the outcome."""
        order = self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=2,
            idempotency_key="compose-order-webhook",
        )
        self.client.process_terminalizations()
        confirmed = self.client.get_order(int(order["id"]))

        replay = self.client.payment_webhook(
            order_id=int(order["id"]),
            event_id="evt-compose-1",
            status="succeeded",
        )

        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["payment_status"], "succeeded")
        self.assertEqual(replay["status"], "confirmed")
        self.assertEqual(replay["payment_status"], "succeeded")

    def test_expired_order_stays_expired_after_late_payment(self) -> None:
        """Late payment notifications must not revive an expired pending order."""
        pending_product_id = self.client.create_product(
            name="Pending Product",
            price=29.99,
            stock=5,
        )
        reserve = self.client.reserve_product(product_id=pending_product_id, quantity=1)
        pending_order_id = self.client.seed_pending_order(
            user_id=self.user_id,
            product_id=pending_product_id,
            reservation_id=int(reserve["reservation_id"]),
        )

        expire = self.client.expire_orders()
        replay = self.client.payment_webhook(
            order_id=pending_order_id,
            event_id="evt-compose-late",
            status="succeeded",
        )

        self.assertEqual(str(expire["expired_count"]), "1")
        self.assertEqual(replay["status"], "expired")
        self.assertEqual(replay["payment_status"], "cancelled")

    def test_list_orders_returns_all_created_orders(self) -> None:
        """Listing orders should return all orders across the stack."""
        self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=1,
            idempotency_key="compose-list-1",
        )
        self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=1,
            idempotency_key="compose-list-2",
        )

        result = self.client.list_orders()
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 2)
        for order in result:
            self.assertIn("id", order)
            self.assertIn("status", order)
            self.assertIn("payment_status", order)

    def test_get_order_not_found_returns_404(self) -> None:
        """Requesting a non-existent order should return 404."""
        result = request_json(
            "GET",
            "http://127.0.0.1:18003/orders/99999",
            expected_status=404,
        )
        self.assertEqual(result["detail"], "order not found")

    def test_payment_webhook_on_nonexistent_order_returns_404(self) -> None:
        """Sending a payment webhook for a non-existent order returns 404."""
        result = request_json(
            "POST",
            "http://127.0.0.1:18003/payments/webhook",
            {"order_id": 99999, "event_id": "evt-nonexistent", "status": "succeeded"},
            expected_status=404,
        )
        self.assertEqual(result["detail"], "order not found")

    def test_order_with_invalid_user_returns_error(self) -> None:
        """Creating an order for a non-existent user should fail."""
        result = self.client.create_order(
            user_id=99999,
            product_id=self.product_id,
            quantity=1,
            idempotency_key="compose-bad-user",
            expected_status=(404, 502),
        )
        self.assertIn("detail", result)

    def test_order_with_invalid_product_returns_404(self) -> None:
        """Creating an order for a non-existent product should fail."""
        result = request_json(
            "POST",
            "http://127.0.0.1:18003/orders",
            {
                "user_id": self.user_id,
                "idempotency_key": "compose-bad-product",
                "items": [{"product_id": 99999, "quantity": 1}],
            },
            expected_status=404,
        )
        self.assertIn("detail", result)

    def test_order_with_empty_items_returns_400(self) -> None:
        """Creating an order with no items should return 400."""
        result = request_json(
            "POST",
            "http://127.0.0.1:18003/orders",
            {
                "user_id": self.user_id,
                "idempotency_key": "compose-empty-items",
                "items": [],
            },
            expected_status=400,
        )
        self.assertEqual(result["detail"], "order items cannot be empty")


if __name__ == "__main__":
    unittest.main()
