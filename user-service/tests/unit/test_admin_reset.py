import sys
import time
import types
import unittest
from importlib import metadata
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
email_validator_stub = types.ModuleType("email_validator")
psycopg_stub.connect = None
psycopg_rows_stub.dict_row = object()


class EmailNotValidError(ValueError):
    pass


def validate_email(value: str, *args: object, **kwargs: object) -> object:
    return types.SimpleNamespace(email=value)


email_validator_stub.EmailNotValidError = EmailNotValidError
email_validator_stub.validate_email = validate_email
email_validator_stub.__version__ = "2.0.0"

original_distribution = metadata.distribution


def patched_distribution(distribution_name: str) -> object:
    if distribution_name == "email-validator":
        return types.SimpleNamespace(version="2.0.0")
    return original_distribution(distribution_name)


sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)
sys.modules.setdefault("email_validator", email_validator_stub)
metadata.distribution = patched_distribution

from app.main import app


class UserServiceAdminResetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def wait_for_reset_to_finish(self) -> None:
        deadline = time.time() + 1
        while app.state.reset_controller.running and time.time() < deadline:
            time.sleep(0.01)

    def test_admin_reset_wait_false_is_non_blocking_and_coalesced(self) -> None:
        started = Event()
        release = Event()
        calls = 0

        def slow_reset() -> None:
            nonlocal calls
            calls += 1
            started.set()
            release.wait(timeout=1)

        with patch.object(app.state.repository, "reset_db", side_effect=slow_reset):
            first = self.client.post("/admin/reset?wait=false")
            self.assertEqual(first.status_code, 202)
            self.assertTrue(started.wait(timeout=1))

            second = self.client.post("/admin/reset?wait=false")
            self.assertEqual(second.status_code, 202)

            release.set()
            self.wait_for_reset_to_finish()

        self.assertEqual(calls, 1)

    def test_admin_reset_wait_true_runs_synchronously(self) -> None:
        calls = 0

        def reset_once() -> None:
            nonlocal calls
            calls += 1

        with patch.object(app.state.repository, "reset_db", side_effect=reset_once):
            response = self.client.post("/admin/reset")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
