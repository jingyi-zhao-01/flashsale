"""Docker Compose integration coverage for the user service."""

# pylint: disable=duplicate-code

import unittest
from tests.integration_test_support import (
    FlashsaleIntegrationClient,
    request_json,
    reset_services,
    wait_for_stack,
)


class UserServiceComposeIntegrationTest(unittest.TestCase):
    """Exercise user-service CRUD flows against the compose stack."""

    @classmethod
    def setUpClass(cls) -> None:
        """Wait until the compose stack is ready for integration traffic."""
        wait_for_stack()

    def setUp(self) -> None:
        """Reset shared state before each test."""
        reset_services()
        self.client = FlashsaleIntegrationClient()

    def test_create_and_get_user(self) -> None:
        """A created user should be retrievable by ID."""
        user_id = self.client.create_user()

        user = self.client.get_user(user_id)

        self.assertEqual(int(user["id"]), user_id)
        self.assertIn("name", user)
        self.assertIn("email", user)

    def test_get_user_not_found_returns_404(self) -> None:
        """Requesting a non-existent user should yield a 404."""
        result = request_json(
            "GET",
            "http://127.0.0.1:18001/users/99999",
            expected_status=404,
        )
        self.assertEqual(result["detail"], "user not found")

    def test_create_duplicate_email_returns_409(self) -> None:
        """Two users with the same email should yield a conflict."""
        import time

        email = f"duplicate.{int(time.time_ns())}@example.com"

        first = request_json(
            "POST",
            "http://127.0.0.1:18001/users",
            {"name": "First", "email": email},
            expected_status=(200, 201),
        )
        self.assertIn("id", first)

        second = request_json(
            "POST",
            "http://127.0.0.1:18001/users",
            {"name": "Second", "email": email},
            expected_status=409,
        )
        self.assertEqual(second["detail"], "user email already exists")

    def test_list_users_returns_all_created_users(self) -> None:
        """Listing users should return all users created during the test."""
        self.client.create_user()
        self.client.create_user()
        self.client.create_user()

        result = self.client.list_users()
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 3)
        for user in result:
            self.assertIn("id", user)
            self.assertIn("name", user)
            self.assertIn("email", user)

    def test_invalid_email_returns_422(self) -> None:
        """An invalid email format should be rejected by FastAPI validation."""
        result = request_json(
            "POST",
            "http://127.0.0.1:18001/users",
            {"name": "Bad Email", "email": "not-an-email"},
            expected_status=422,
        )
        self.assertIn("detail", result)

    def test_health_returns_ok(self) -> None:
        """The health endpoint should return ok status."""
        result = request_json("GET", "http://127.0.0.1:18001/health")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["service"], "user-service")

    def test_ready_returns_ok(self) -> None:
        """The ready endpoint should return ok status."""
        result = request_json("GET", "http://127.0.0.1:18001/ready")
        self.assertEqual(result["status"], "ok")

    def test_live_returns_ok(self) -> None:
        """The live endpoint should return ok status."""
        result = request_json("GET", "http://127.0.0.1:18001/live")
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
