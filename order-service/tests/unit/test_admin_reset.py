import time
import unittest
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.unit import app_import_stubs as _app_import_stubs

from app.main import app


class OrderServiceAdminResetTest(unittest.TestCase):
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

        with patch.object(app.state.reset_target, "reset", side_effect=slow_reset):
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

        with patch.object(app.state.reset_target, "reset", side_effect=reset_once):
            response = self.client.post("/admin/reset")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
