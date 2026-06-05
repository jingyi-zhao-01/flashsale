import unittest
from types import SimpleNamespace

from fastapi import Request

from flashsale_shared.observability import request_path_label


class UserServiceObservabilityTest(unittest.TestCase):
    def test_uses_route_template_for_dynamic_paths(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/users/49",
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


if __name__ == "__main__":
    unittest.main()
