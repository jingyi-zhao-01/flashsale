import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.adapters.product_reservation_http_client import (
    ProductReservationHttpClient,
)
from app.adapters.user_http_client import UserHttpClient


class _FakeResponse:
    def __init__(
        self, status_code: int, payload: dict[str, object] | None = None
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append(("get", url, kwargs))
        return self.response

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append(("post", url, kwargs))
        return self.response


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def __enter__(self) -> "_RaisingClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append(("get", url, kwargs))
        raise self.exc

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append(("post", url, kwargs))
        raise self.exc


class OrderServiceHttpClientTimeoutTest(unittest.TestCase):
    def test_user_lookup_uses_dedicated_timeout(self) -> None:
        client = _FakeClient(_FakeResponse(200))
        http_client = UserHttpClient(client_factory=lambda: client)

        with patch(
            "app.adapters.user_http_client.USER_SERVICE_TIMEOUT_SECONDS", 4.5
        ), patch("app.adapters.user_http_client.USER_SERVICE_URL", "http://user-service"):
            http_client.ensure_user_exists(42)

        self.assertEqual(client.calls[0][2]["timeout"], 4.5)

    def test_user_lookup_timeout_maps_to_504(self) -> None:
        request = httpx.Request("GET", "http://user-service/users/42")
        client = _RaisingClient(httpx.ReadTimeout("timeout", request=request))
        http_client = UserHttpClient(client_factory=lambda: client)

        with patch(
            "app.adapters.user_http_client.USER_SERVICE_TIMEOUT_SECONDS", 4.5
        ), patch("app.adapters.user_http_client.USER_SERVICE_URL", "http://user-service"):
            with self.assertRaises(HTTPException) as exc_info:
                http_client.ensure_user_exists(42)

        self.assertEqual(exc_info.exception.status_code, 504)
        self.assertEqual(exc_info.exception.detail, "user-service request timed out")

    def test_product_reserve_uses_dedicated_timeout(self) -> None:
        client = _FakeClient(
            _FakeResponse(200, {"unit_price": 9.99, "reservation_id": 7})
        )
        http_client = ProductReservationHttpClient(client_factory=lambda: client)

        with patch(
            "app.adapters.product_reservation_http_client.PRODUCT_RESERVE_TIMEOUT_SECONDS",
            9.0,
        ), patch(
            "app.adapters.product_reservation_http_client.PRODUCT_SERVICE_URL",
            "http://product-service",
        ):
            http_client.reserve(1, 2)

        self.assertEqual(client.calls[0][2]["timeout"], 9.0)

    def test_product_reserve_timeout_maps_to_504(self) -> None:
        request = httpx.Request("POST", "http://product-service/products/1/reserve")
        client = _RaisingClient(httpx.ReadTimeout("timeout", request=request))
        http_client = ProductReservationHttpClient(client_factory=lambda: client)

        with patch(
            "app.adapters.product_reservation_http_client.PRODUCT_RESERVE_TIMEOUT_SECONDS",
            9.0,
        ), patch(
            "app.adapters.product_reservation_http_client.PRODUCT_SERVICE_URL",
            "http://product-service",
        ):
            with self.assertRaises(HTTPException) as exc_info:
                http_client.reserve(1, 2)

        self.assertEqual(exc_info.exception.status_code, 504)
        self.assertEqual(
            exc_info.exception.detail, "product-service reserve timed out"
        )

    def test_product_reserve_connection_error_maps_to_503(self) -> None:
        request = httpx.Request("POST", "http://product-service/products/1/reserve")
        client = _RaisingClient(httpx.ConnectError("connect", request=request))
        http_client = ProductReservationHttpClient(client_factory=lambda: client)

        with patch(
            "app.adapters.product_reservation_http_client.PRODUCT_RESERVE_TIMEOUT_SECONDS",
            9.0,
        ), patch(
            "app.adapters.product_reservation_http_client.PRODUCT_SERVICE_URL",
            "http://product-service",
        ):
            with self.assertRaises(HTTPException) as exc_info:
                http_client.reserve(1, 2)

        self.assertEqual(exc_info.exception.status_code, 503)
        self.assertEqual(exc_info.exception.detail, "product-service unavailable")

    def test_product_reserve_busy_maps_to_429(self) -> None:
        client = _FakeClient(_FakeResponse(429))
        http_client = ProductReservationHttpClient(client_factory=lambda: client)

        with patch(
            "app.adapters.product_reservation_http_client.PRODUCT_SERVICE_URL",
            "http://product-service",
        ):
            with self.assertRaises(HTTPException) as exc_info:
                http_client.reserve(1, 2)

        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "product-service is busy, retry later")

    def test_product_release_uses_dedicated_timeout(self) -> None:
        client = _FakeClient(_FakeResponse(200))
        http_client = ProductReservationHttpClient(client_factory=lambda: client)

        with patch(
            "app.adapters.product_reservation_http_client.PRODUCT_RELEASE_TIMEOUT_SECONDS",
            5.5,
        ), patch(
            "app.adapters.product_reservation_http_client.PRODUCT_SERVICE_URL",
            "http://product-service",
        ):
            http_client.release([11, 12])

        self.assertEqual(client.calls[0][2]["timeout"], 5.5)

    def test_product_terminalize_uses_dedicated_timeout(self) -> None:
        client = _FakeClient(_FakeResponse(200))
        http_client = ProductReservationHttpClient(client_factory=lambda: client)

        with patch(
            "app.adapters.product_reservation_http_client.PRODUCT_TERMINALIZE_TIMEOUT_SECONDS",
            7.25,
        ), patch(
            "app.adapters.product_reservation_http_client.PRODUCT_SERVICE_URL",
            "http://product-service",
        ):
            http_client.terminalize(99, "confirm")

        self.assertEqual(client.calls[0][2]["timeout"], 7.25)


if __name__ == "__main__":
    unittest.main()
