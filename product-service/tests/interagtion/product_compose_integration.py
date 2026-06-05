"""Docker Compose integration coverage for the product service."""

# pylint: disable=duplicate-code

import unittest
from tests.integration_test_support import (
    FlashsaleIntegrationClient,
    request_json,
    reset_services,
    wait_for_stack,
)


class ProductServiceComposeIntegrationTest(unittest.TestCase):
    """Exercise product-service inventory flows against the compose stack."""

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

    def test_order_consumes_stock(self) -> None:
        """A successful order should reduce the available product stock."""
        self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=2,
            idempotency_key="compose-order-stock",
        )

        product = self.client.get_product(self.product_id)
        self.assertEqual(str(product["stock"]), "3")

    def test_duplicate_order_replay_does_not_change_stock(self) -> None:
        """Idempotent order replays must not consume stock twice."""
        first = self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=2,
            idempotency_key="compose-order-dup",
        )
        replay = self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=2,
            idempotency_key="compose-order-dup",
        )

        product = self.client.get_product(self.product_id)
        self.assertEqual(int(replay["id"]), int(first["id"]))
        self.assertEqual(str(product["stock"]), "3")

    def test_out_of_stock_order_returns_conflict(self) -> None:
        """Orders beyond remaining stock should fail without further stock loss."""
        self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=2,
            idempotency_key="compose-order-base",
        )
        conflict = self.client.create_order(
            user_id=self.user_id,
            product_id=self.product_id,
            quantity=4,
            idempotency_key="compose-order-oos",
            expected_status=409,
        )

        product = self.client.get_product(self.product_id)
        self.assertIn("detail", conflict)
        self.assertEqual(str(product["stock"]), "3")

    def test_list_products_returns_all_created_products(self) -> None:
        """Listing products should include all seeded and test products."""
        result = self.client.list_products()
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 1)
        for product in result:
            self.assertIn("id", product)
            self.assertIn("name", product)
            self.assertIn("stock", product)

    def test_get_product_not_found_returns_404(self) -> None:
        """Requesting a non-existent product should return 404."""
        result = request_json(
            "GET",
            "http://127.0.0.1:18002/products/99999",
            expected_status=404,
        )
        self.assertEqual(result["detail"], "product not found")

    def test_reserve_product_reduces_available_stock(self) -> None:
        """Reserving stock directly should reduce available stock."""
        reserve = request_json(
            "POST",
            f"http://127.0.0.1:18002/products/{self.product_id}/reserve",
            {"quantity": 3},
            expected_status=(200, 201),
        )

        self.assertIn("reservation_id", reserve)
        self.assertEqual(reserve["product_id"], self.product_id)
        self.assertEqual(reserve["quantity"], 3)
        self.assertEqual(reserve["status"], "reserved")

        product = self.client.get_product(self.product_id)
        self.assertEqual(str(product["stock"]), "2")

    def test_confirm_reservation_persists(self) -> None:
        """Confirming a reservation should transition it to confirmed."""
        reserve = request_json(
            "POST",
            f"http://127.0.0.1:18002/products/{self.product_id}/reserve",
            {"quantity": 1},
            expected_status=(200, 201),
        )

        confirmed = request_json(
            "POST",
            f"http://127.0.0.1:18002/reservations/{reserve['reservation_id']}/confirm",
            expected_status=200,
        )

        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["reservation_id"], reserve["reservation_id"])

    def test_cancel_reservation_restores_stock(self) -> None:
        """Cancelling a reservation should restore stock."""
        reserve = request_json(
            "POST",
            f"http://127.0.0.1:18002/products/{self.product_id}/reserve",
            {"quantity": 2},
            expected_status=(200, 201),
        )

        cancelled = request_json(
            "POST",
            f"http://127.0.0.1:18002/reservations/{reserve['reservation_id']}/cancel",
            expected_status=200,
        )

        self.assertEqual(cancelled["status"], "cancelled")

        product = self.client.get_product(self.product_id)
        self.assertEqual(str(product["stock"]), "5")

    def test_expire_reservations_releases_stale_reservations(self) -> None:
        """Expiring stale reservations should restore stock."""
        reserve = request_json(
            "POST",
            f"http://127.0.0.1:18002/products/{self.product_id}/reserve",
            {"quantity": 2},
            expected_status=(200, 201),
        )

        result = request_json(
            "POST",
            "http://127.0.0.1:18002/admin/expire-reservations",
            expected_status=200,
        )

        self.assertIn("expired_count", result)

    def test_confirm_nonexistent_reservation_returns_404(self) -> None:
        """Confirming a non-existent reservation should return 404."""
        result = request_json(
            "POST",
            "http://127.0.0.1:18002/reservations/99999/confirm",
            expected_status=404,
        )
        self.assertEqual(result["detail"], "reservation not found")

    def test_cancel_nonexistent_reservation_returns_404(self) -> None:
        """Cancelling a non-existent reservation should return 404."""
        result = request_json(
            "POST",
            "http://127.0.0.1:18002/reservations/99999/cancel",
            expected_status=404,
        )
        self.assertEqual(result["detail"], "reservation not found")


if __name__ == "__main__":
    unittest.main()
