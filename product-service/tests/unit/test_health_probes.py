import sys
import types
import unittest
from inspect import iscoroutinefunction
from unittest.mock import patch
from fastapi.testclient import TestClient

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_stub.connect = None
psycopg_rows_stub.dict_row = object()
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

from app.main import app


class ProductServiceHealthProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_live_ignores_repository_health(self) -> None:
        with patch.object(
            app.state.repository, "is_healthy", side_effect=RuntimeError("db down")
        ):
            response = self.client.get("/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_health_ignores_repository_health(self) -> None:
        with patch.object(
            app.state.repository, "is_healthy", side_effect=RuntimeError("db down")
        ):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_ready_ignores_repository_health(self) -> None:
        with patch.object(
            app.state.repository, "is_healthy", side_effect=RuntimeError("db down")
        ):
            response = self.client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_probe_routes_are_async(self) -> None:
        health_route = next(route for route in app.routes if route.path == "/health")
        ready_route = next(route for route in app.routes if route.path == "/ready")
        live_route = next(route for route in app.routes if route.path == "/live")

        self.assertTrue(iscoroutinefunction(health_route.endpoint))
        self.assertTrue(iscoroutinefunction(ready_route.endpoint))
        self.assertTrue(iscoroutinefunction(live_route.endpoint))


if __name__ == "__main__":
    unittest.main()
