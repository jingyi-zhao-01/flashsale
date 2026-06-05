import sys
import types
import unittest
from inspect import iscoroutinefunction
from unittest.mock import patch

# Force-replace psycopg in sys.modules with a stub that includes errors submodule support.
_psycopg_stub = types.ModuleType("psycopg")
_psycopg_rows_stub = types.ModuleType("psycopg.rows")
_psycopg_errors_stub = types.ModuleType("psycopg.errors")
_psycopg_stub.connect = None
_psycopg_rows_stub.dict_row = object()


class FakeUniqueViolation(Exception):
    pass


_psycopg_errors_stub.UniqueViolation = FakeUniqueViolation
_psycopg_errors_stub.IntegrityError = FakeUniqueViolation

sys.modules["psycopg"] = _psycopg_stub
sys.modules["psycopg.rows"] = _psycopg_rows_stub
sys.modules["psycopg.errors"] = _psycopg_errors_stub
_psycopg_stub.errors = _psycopg_errors_stub

email_validator_stub = types.ModuleType("email_validator")


class EmailNotValidError(ValueError):
    pass


def validate_email(value: str, *args: object, **kwargs: object) -> object:
    return types.SimpleNamespace(email=value, normalized=value, local_part=value.split("@")[0] if "@" in value else value)


email_validator_stub.EmailNotValidError = EmailNotValidError
email_validator_stub.validate_email = validate_email
email_validator_stub.__version__ = "2.0.0"

import importlib.metadata as _metadata

_original_distribution = _metadata.distribution


def _patched_distribution(distribution_name: str) -> object:
    if distribution_name == "email-validator":
        return types.SimpleNamespace(version="2.0.0")
    return _original_distribution(distribution_name)


sys.modules["email_validator"] = email_validator_stub
_metadata.distribution = _patched_distribution

from fastapi.testclient import TestClient

from app.main import app


class UserServiceApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def setUp(self) -> None:
        self.client.post("/admin/reset")

    def test_create_user_returns_201_with_id_and_email(self) -> None:
        response = self.client.post(
            "/users",
            json={"name": "Alice", "email": "alice@example.com"},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("id", body)
        self.assertIsInstance(body["id"], int)
        self.assertEqual(body["name"], "Alice")
        self.assertEqual(body["email"], "alice@example.com")

    def test_get_user_returns_existing_user(self) -> None:
        created = self.client.post(
            "/users",
            json={"name": "Bob", "email": "bob@example.com"},
        ).json()

        response = self.client.get(f"/users/{created['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created)

    def test_get_user_not_found_returns_404(self) -> None:
        response = self.client.get("/users/99999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "user not found")

    def test_create_duplicate_email_returns_409(self) -> None:
        self.client.post(
            "/users",
            json={"name": "Alice", "email": "dup@example.com"},
        )
        response = self.client.post(
            "/users",
            json={"name": "Alice Again", "email": "dup@example.com"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("already exists", response.json()["detail"])

    def test_list_users_returns_all_created_users(self) -> None:
        self.client.post("/users", json={"name": "A", "email": "a@example.com"})
        self.client.post("/users", json={"name": "B", "email": "b@example.com"})
        self.client.post("/users", json={"name": "C", "email": "c@example.com"})

        response = self.client.get("/users")
        self.assertEqual(response.status_code, 200)
        users = response.json()
        self.assertIsInstance(users, list)
        self.assertEqual(len(users), 3)
        emails = {u["email"] for u in users}
        self.assertEqual(emails, {"a@example.com", "b@example.com", "c@example.com"})

    def test_list_users_empty_returns_empty_list(self) -> None:
        response = self.client.get("/users")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_persistence_failure_maps_to_503(self) -> None:
        with patch.object(
            app.state.repository,
            "create_user",
            side_effect=RuntimeError("persistence failed"),
        ):
            response = self.client.post(
                "/users",
                json={"name": "X", "email": "x@example.com"},
            )
        self.assertEqual(response.status_code, 503)

    def test_db_unavailable_maps_to_503_on_get(self) -> None:
        with patch.object(
            app.state.repository,
            "get_user",
            side_effect=ConnectionError("db down"),
        ):
            response = self.client.get("/users/1")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "database unavailable")

    def test_db_unavailable_maps_to_503_on_list(self) -> None:
        with patch.object(
            app.state.repository,
            "list_users",
            side_effect=ConnectionError("db down"),
        ):
            response = self.client.get("/users")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "database unavailable")

    def test_user_routes_are_sync(self) -> None:
        create_route = next(r for r in app.routes if r.path == "/users" and r.methods == {"POST"})
        get_route = next(r for r in app.routes if r.path == "/users/{user_id}" and r.methods == {"GET"})
        list_route = next(r for r in app.routes if r.path == "/users" and r.methods == {"GET"})

        self.assertFalse(iscoroutinefunction(create_route.endpoint))
        self.assertFalse(iscoroutinefunction(get_route.endpoint))
        self.assertFalse(iscoroutinefunction(list_route.endpoint))

    def test_reset_clears_all_users(self) -> None:
        self.client.post("/users", json={"name": "X", "email": "x@example.com"})
        self.client.post("/users", json={"name": "Y", "email": "y@example.com"})

        response = self.client.post("/admin/reset")
        self.assertEqual(response.status_code, 204)

        list_resp = self.client.get("/users")
        self.assertEqual(list_resp.json(), [])


if __name__ == "__main__":
    unittest.main()
