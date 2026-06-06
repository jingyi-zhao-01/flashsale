from collections.abc import Callable
from contextlib import contextmanager, nullcontext

import httpx
from fastapi import HTTPException
from opentelemetry.trace import SpanKind

from app.config import (
    PRODUCT_RELEASE_TIMEOUT_SECONDS,
    PRODUCT_RESERVE_TIMEOUT_SECONDS,
    PRODUCT_SERVICE_URL,
    PRODUCT_TERMINALIZE_TIMEOUT_SECONDS,
)
from app.domain.statuses import TerminalizationAction
from flashsale_shared.observability import inject_trace_headers, start_span


class ProductReservationHttpClient:
    def __init__(
        self,
        client_factory: Callable[[], httpx.Client],
        *,
        close_client_after_use: bool = True,
    ) -> None:
        self._client_factory = client_factory
        self._close_client_after_use = close_client_after_use

    @contextmanager
    def _client(self):
        client = self._client_factory()
        manager = client if self._close_client_after_use else nullcontext(client)
        with manager as managed_client:
            yield managed_client

    def reserve(self, product_id: int, quantity: int) -> tuple[float, int, int]:
        with start_span(
            "order-service",
            "product-service reserve",
            kind=SpanKind.CLIENT,
            attributes={
                "http.request.method": "POST",
                "server.address": PRODUCT_SERVICE_URL,
                "flashsale.product_id": product_id,
                "flashsale.quantity": quantity,
            },
        ):
            try:
                with self._client() as client:
                    response = client.post(
                        f"{PRODUCT_SERVICE_URL}/products/{product_id}/reserve",
                        json={"quantity": quantity},
                        headers=inject_trace_headers(),
                        timeout=PRODUCT_RESERVE_TIMEOUT_SECONDS,
                    )
            except httpx.TimeoutException as exc:
                raise HTTPException(
                    status_code=504,
                    detail="product-service reserve timed out",
                ) from exc
            except httpx.RequestError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="product-service unavailable",
                ) from exc
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"product {product_id} not found")
        if response.status_code == 409:
            raise HTTPException(
                status_code=409,
                detail=f"insufficient stock for product {product_id}",
            )
        if response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="product-service is busy, retry later",
            )
        if response.status_code == 408:
            raise HTTPException(
                status_code=504,
                detail="product-service reserve timed out",
            )
        if response.status_code >= 500:
            raise HTTPException(status_code=503, detail="product-service unavailable")
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"product-service returned unexpected status {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail="product-service returned invalid reservation payload",
            ) from exc
        if payload.get("unit_price") is None:
            raise HTTPException(status_code=502, detail="product reserve missing price")
        return (
            float(payload["unit_price"]),
            int(quantity),
            int(payload["reservation_id"]),
        )

    def release(self, reservation_ids: list[int]) -> None:
        with self._client() as client:
            for reservation_id in reversed(reservation_ids):
                try:
                    client.post(
                        f"{PRODUCT_SERVICE_URL}/reservations/{reservation_id}/cancel",
                        headers=inject_trace_headers(),
                        timeout=PRODUCT_RELEASE_TIMEOUT_SECONDS,
                    )
                except (httpx.TimeoutException, httpx.RequestError):
                    continue

    def terminalize(
        self,
        reservation_id: int,
        action: TerminalizationAction,
    ) -> tuple[bool, str | None]:
        try:
            with start_span(
                "order-service",
                f"product-service {action}",
                kind=SpanKind.CLIENT,
                attributes={
                    "http.request.method": "POST",
                    "server.address": PRODUCT_SERVICE_URL,
                    "flashsale.reservation_id": reservation_id,
                    "flashsale.action": action,
                },
            ):
                with self._client() as client:
                    response = client.post(
                        f"{PRODUCT_SERVICE_URL}/reservations/{reservation_id}/{action}",
                        headers=inject_trace_headers(),
                        timeout=PRODUCT_TERMINALIZE_TIMEOUT_SECONDS,
                    )
            if response.status_code >= 400:
                return False, f"status_code={response.status_code}"
            return True, None
        except httpx.TimeoutException:
            return False, "timeout"
        except httpx.RequestError:
            return False, "unavailable"
        except Exception as exc:
            return False, exc.__class__.__name__
