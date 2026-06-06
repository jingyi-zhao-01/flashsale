import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request

from flashsale_shared.observability import (
    TraceContextFilter,
    initialize_tracing,
    inject_trace_headers,
    request_path_label,
)


class TraceContextFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        initialize_tracing("test-service")

    def test_filter_adds_trace_id_and_span_id_on_record(self) -> None:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0, msg="test", args=(), exc_info=None
        )
        filter_ = TraceContextFilter()
        result = filter_.filter(record)

        self.assertTrue(result)
        self.assertTrue(hasattr(record, "trace_id"))
        self.assertTrue(hasattr(record, "span_id"))
        self.assertGreaterEqual(len(record.trace_id), 1)


class RequestPathLabelTest(unittest.TestCase):
    def test_returns_route_template_when_available(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/users/42",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "query_string": b"",
            }
        )
        request.scope["route"] = SimpleNamespace(path="/users/{user_id}")
        self.assertEqual(request_path_label(request), "/users/{user_id}")

    def test_falls_back_to_raw_path_when_no_route(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/health",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "query_string": b"",
            }
        )
        self.assertEqual(request_path_label(request), "/health")

    def test_uses_raw_path_when_route_has_no_path_attr(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/some/path",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "query_string": b"",
            }
        )
        request.scope["route"] = SimpleNamespace()
        self.assertEqual(request_path_label(request), "/some/path")

    def test_ignores_empty_string_route_path(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/fallback",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "query_string": b"",
            }
        )
        request.scope["route"] = SimpleNamespace(path="")
        self.assertEqual(request_path_label(request), "/fallback")


class InjectTraceHeadersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        initialize_tracing("test-service")

    def test_returns_dict_type(self) -> None:
        headers = inject_trace_headers()
        self.assertIsInstance(headers, dict)

    def test_preserves_existing_headers(self) -> None:
        existing = {"x-custom": "value", "authorization": "Bearer token"}
        headers = inject_trace_headers(existing)
        self.assertIn("x-custom", headers)
        self.assertIn("authorization", headers)
        self.assertEqual(headers["x-custom"], "value")


if __name__ == "__main__":
    unittest.main()
