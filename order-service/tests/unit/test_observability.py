import unittest
from types import SimpleNamespace

from fastapi import Request

from flashsale_shared.observability import request_path_label


class OrderServiceObservabilityTest(unittest.TestCase):
    def test_uses_route_template_for_dynamic_paths(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/orders/42",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "query_string": b"",
            }
        )
        request.scope["route"] = SimpleNamespace(path="/orders/{order_id}")

        self.assertEqual(request_path_label(request), "/orders/{order_id}")

    def test_falls_back_to_raw_path_when_route_is_missing(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/unknown/42",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "query_string": b"",
            }
        )

        self.assertEqual(request_path_label(request), "/unknown/42")


if __name__ == "__main__":
    unittest.main()
