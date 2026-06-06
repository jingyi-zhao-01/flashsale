from collections.abc import Callable
from contextlib import contextmanager, nullcontext

import httpx
from fastapi import HTTPException
from opentelemetry.trace import SpanKind

from app.config import USER_SERVICE_TIMEOUT_SECONDS, USER_SERVICE_URL
from flashsale_shared.observability import inject_trace_headers, start_span


class UserHttpClient:
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

    def ensure_user_exists(self, user_id: int) -> None:
        with start_span(
            "order-service",
            "user-service lookup",
            kind=SpanKind.CLIENT,
            attributes={
                "http.request.method": "GET",
                "server.address": USER_SERVICE_URL,
                "flashsale.user_id": user_id,
            },
        ):
            try:
                with self._client() as client:
                    response = client.get(
                        f"{USER_SERVICE_URL}/users/{user_id}",
                        headers=inject_trace_headers(),
                        timeout=USER_SERVICE_TIMEOUT_SECONDS,
                    )
            except httpx.TimeoutException as exc:
                raise HTTPException(
                    status_code=504,
                    detail="user-service request timed out",
                ) from exc
            except httpx.RequestError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="user-service unavailable",
                ) from exc
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="user not found")
        if response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="user-service is busy, retry later",
            )
        if response.status_code == 408:
            raise HTTPException(
                status_code=504,
                detail="user-service request timed out",
            )
        if response.status_code >= 500:
            raise HTTPException(status_code=503, detail="user-service unavailable")
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"user-service returned unexpected status {response.status_code}",
            )
