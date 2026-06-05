import unittest
from types import SimpleNamespace

from fastapi import Request

from flashsale_shared.observability import request_path_label


class ProductServiceObservabilityTest(unittest.TestCase):
    def test_uses_route_template_for_dynamic_paths(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/reservations/1277/confirm",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "query_string": b"",
            }
        )
        request.scope["route"] = SimpleNamespace(
            path="/reservations/{reservation_id}/confirm"
        )

        self.assertEqual(
            request_path_label(request),
            "/reservations/{reservation_id}/confirm",
        )


if __name__ == "__main__":
    unittest.main()
