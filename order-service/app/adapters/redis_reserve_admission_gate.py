from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError

from app.adapters.reserve_admission_metrics import ReserveAdmissionMetrics
from app.config import (
    RESERVE_ADMISSION_MAX_INFLIGHT,
    RESERVE_ADMISSION_PERMIT_TTL_SECONDS,
)

if TYPE_CHECKING:
    from app.ports.reserve_admission_gate import ReserveAdmissionGate

_KEY_PREFIX = "flashsale:reserve:admission"

_ACQUIRE_PERMIT_SCRIPT = """
-- repair_negative_counter_and_incr
local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local current = redis.call("GET", key)
if current and tonumber(current) < 0 then
    redis.call("DEL", key)
end
local counter = redis.call("INCR", key)
if counter == 1 then
    redis.call("EXPIRE", key, ttl)
end
return counter
"""

_RELEASE_PERMIT_SCRIPT = """
-- safe_release_counter
local key = KEYS[1]
local current = redis.call("GET", key)
if not current then
    return 0
end
local inflight = tonumber(current)
if inflight <= 1 then
    redis.call("DEL", key)
    return 0
end
return redis.call("DECR", key)
"""


def _key(product_id: int) -> str:
    return f"{_KEY_PREFIX}:{product_id}"


class RedisReserveAdmissionGate:
    """Redis-based product-level admission control.

    Limits concurrent inventory reservation attempts per product_id by
    maintaining an integer counter in Redis with a TTL-based safety net.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        max_inflight: int = RESERVE_ADMISSION_MAX_INFLIGHT,
        permit_ttl_seconds: int = RESERVE_ADMISSION_PERMIT_TTL_SECONDS,
        metrics: ReserveAdmissionMetrics | None = None,
    ) -> None:
        self._redis = redis
        self._max_inflight = max_inflight
        self._permit_ttl = permit_ttl_seconds
        self._metrics = metrics or ReserveAdmissionMetrics()

    # -- ReserveAdmissionGate ------------------------------------------

    def acquire(self, product_ids: list[int]) -> list[int]:
        """Acquire admission permits for *product_ids*.

        Permits are acquired atomically via Redis INCR.  If any product is
        over the limit, all previously acquired permits are released and an
        HTTP 429 is raised.
        """
        if not product_ids:
            return []

        acquired: list[int] = []
        for pid in product_ids:
            t0 = time.perf_counter()
            try:
                counter = self._acquire_counter(pid)
            except RedisError as exc:
                self._metrics.record_error(pid, "redis_incr_error")
                self._release(acquired)
                raise HTTPException(
                    status_code=503,
                    detail="admission control unavailable",
                ) from exc

            wait_ms = (time.perf_counter() - t0) * 1000
            inflight = int(counter)

            if inflight > self._max_inflight:
                try:
                    self._release_counter(pid)
                except RedisError:
                    pass
                self._metrics.record_rejected(pid, inflight, self._max_inflight)
                self._metrics.trace_gate(
                    pid, "rejected", inflight, self._max_inflight
                )
                self._release(acquired)
                raise HTTPException(
                    status_code=429,
                    detail="product reservation busy, retry later",
                )

            acquired.append(pid)
            self._metrics.record_allowed(
                pid, inflight, self._max_inflight, wait_ms
            )
            self._metrics.trace_gate(
                pid, "allowed", inflight, self._max_inflight, wait_ms
            )

        return acquired

    def release(self, product_ids: list[int]) -> None:
        self._release(product_ids)

    def _acquire_counter(self, product_id: int) -> int:
        return int(
            self._redis.eval(
                _ACQUIRE_PERMIT_SCRIPT,
                1,
                _key(product_id),
                self._permit_ttl,
            )
        )

    def _release_counter(self, product_id: int) -> int:
        return int(
            self._redis.eval(
                _RELEASE_PERMIT_SCRIPT,
                1,
                _key(product_id),
            )
        )

    def _release(self, product_ids: list[int]) -> None:
        for pid in product_ids:
            try:
                inflight = self._release_counter(pid)
            except RedisError:
                self._metrics.record_error(pid, "redis_decr_error")
            else:
                self._metrics.record_inflight(pid, inflight)
