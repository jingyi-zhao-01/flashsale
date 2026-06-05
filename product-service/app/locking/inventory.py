import logging
import time

import psycopg
from psycopg.rows import dict_row
from opentelemetry.trace import SpanKind

try:
    from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests
except ImportError:  # pragma: no cover - exercised only when pool dep is absent
    PoolClosed = PoolTimeout = TooManyRequests = None

from app.config import DB_POOL_MAX_SIZE, DB_POOL_MIN_SIZE, DB_POOL_TIMEOUT_SECONDS
from flashsale_shared.db_pool import DatabasePool
from flashsale_shared.observability import start_span

lock_logger = logging.getLogger("product-service.locking")
POOL_ERRORS = tuple(
    exc
    for exc in (PoolTimeout, PoolClosed, TooManyRequests)
    if isinstance(exc, type)
)


class InventoryReserveEngine:
    def __init__(
        self,
        database_url: str,
        lock_mode: str,
        retry_limit: int,
        slow_ms_threshold: float = 200,
        pool: DatabasePool | None = None,
    ) -> None:
        self._database_url = database_url
        self._lock_mode = lock_mode
        self._retry_limit = retry_limit
        self._slow_ms_threshold = slow_ms_threshold
        self._pool = pool or DatabasePool(
            database_url=database_url,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout_seconds=DB_POOL_TIMEOUT_SECONDS,
        )

    def reserve(self, product_id: int, quantity: int) -> dict[str, object] | None:
        with start_span(
            "product-service",
            "inventory reserve",
            kind=SpanKind.INTERNAL,
            attributes={
                "flashsale.product_id": product_id,
                "flashsale.quantity": quantity,
                "flashsale.lock_mode": self._lock_mode,
            },
        ):
            if self._lock_mode == "optimistic":
                return self._reserve_optimistic(product_id, quantity)
            if self._lock_mode == "pessimistic":
                return self._reserve_pessimistic(product_id, quantity)
            lock_logger.error(
                "event=invalid_lock_mode product_id=%s quantity=%s lock_mode=%s",
                product_id,
                quantity,
                self._lock_mode,
            )
            raise ValueError(f"Invalid lock mode: {self._lock_mode}")

    def reserve_with_reservation(
        self, product_id: int, quantity: int, reservation_ttl_seconds: int
    ) -> dict[str, object] | None:
        with start_span(
            "product-service",
            "inventory reserve with reservation",
            kind=SpanKind.INTERNAL,
            attributes={
                "flashsale.product_id": product_id,
                "flashsale.quantity": quantity,
                "flashsale.lock_mode": self._lock_mode,
                "flashsale.reservation_ttl_seconds": reservation_ttl_seconds,
            },
        ):
            if self._lock_mode == "optimistic":
                return self._reserve_optimistic_with_reservation(
                    product_id, quantity, reservation_ttl_seconds
                )
            if self._lock_mode == "pessimistic":
                return self._reserve_pessimistic_with_reservation(
                    product_id, quantity, reservation_ttl_seconds
                )
            lock_logger.error(
                "event=invalid_lock_mode product_id=%s quantity=%s lock_mode=%s",
                product_id,
                quantity,
                self._lock_mode,
            )
            raise ValueError(f"Invalid lock mode: {self._lock_mode}")

    def _insert_reservation(
        self,
        cur: object,
        product_id: int,
        quantity: int,
        unit_price: object,
        reservation_ttl_seconds: int,
    ) -> dict[str, object]:
        cur.execute(
            """
            INSERT INTO reservations (product_id, quantity, unit_price, status, expires_at)
            VALUES (%s, %s, %s, %s, NOW() + (%s || ' seconds')::interval)
            RETURNING reservation_id, product_id, quantity, unit_price, status, expires_at
            """,
            (
                product_id,
                quantity,
                unit_price,
                "reserved",
                reservation_ttl_seconds,
            ),
        )
        reservation = cur.fetchone()
        if not reservation:
            raise RuntimeError("reservation persistence failed")
        return reservation

    def _reserve_pessimistic(
        self, product_id: int, quantity: int
    ) -> dict[str, object] | None:
        tx_start = time.perf_counter()
        try:
            with self._pool.connection(autocommit=False, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = '1s'")
                    cur.execute("SET LOCAL statement_timeout = '3s'")
                    lock_start = time.perf_counter()
                    cur.execute(
                        """
                        SELECT id, name, price, stock
                        FROM products
                        WHERE id = %s
                        FOR UPDATE
                        """,
                        (product_id,),
                    )
                    row = cur.fetchone()
                    lock_wait_ms = (time.perf_counter() - lock_start) * 1000
                    lock_logger.info(
                        "event=product_service_lock_wait lock_mode=pessimistic product_id=%s quantity=%s wait_ms=%.2f threshold_ms=%.2f lock_timeout_ms=1000 statement_timeout_ms=3000",
                        product_id,
                        quantity,
                        lock_wait_ms,
                        self._slow_ms_threshold,
                    )
                    if lock_wait_ms >= self._slow_ms_threshold:
                        lock_logger.warning(
                            "event=lock_wait_slow lock_mode=pessimistic product_id=%s quantity=%s wait_ms=%.2f threshold_ms=%.2f",
                            product_id,
                            quantity,
                            lock_wait_ms,
                            self._slow_ms_threshold,
                        )

                    if not row:
                        return None

                    current_stock = int(row["stock"])
                    if current_stock < quantity:
                        lock_logger.warning(
                            "event=insufficient_stock lock_mode=pessimistic product_id=%s quantity=%s current_stock=%s",
                            product_id,
                            quantity,
                            current_stock,
                        )
                        raise ValueError("insufficient stock")

                    cur.execute(
                        """
                        UPDATE products
                        SET stock = stock - %s
                        WHERE id = %s AND stock >= %s
                        RETURNING id, name, price, stock
                        """,
                        (quantity, product_id, quantity),
                    )
                    updated = cur.fetchone()
                    if not updated:
                        lock_logger.error(
                            "event=pessimistic_reserve_failed lock_mode=pessimistic product_id=%s quantity=%s",
                            product_id,
                            quantity,
                        )
                        raise RuntimeError("pessimistic reserve failed")

                    conn.commit()
                    return updated
        except Exception as exc:
            if POOL_ERRORS and isinstance(exc, POOL_ERRORS):
                lock_logger.exception(
                    "event=inventory_pool_error lock_mode=pessimistic product_id=%s quantity=%s",
                    product_id,
                    quantity,
                )
                raise
            if isinstance(exc, psycopg.Error):
                lock_logger.exception(
                    "event=inventory_db_error lock_mode=pessimistic product_id=%s quantity=%s",
                    product_id,
                    quantity,
                )
                raise
            raise
        finally:
            tx_elapsed_ms = (time.perf_counter() - tx_start) * 1000
            lock_logger.info(
                "event=product_service_reserve_transaction lock_mode=pessimistic product_id=%s quantity=%s elapsed_ms=%.2f threshold_ms=%.2f lock_timeout_ms=1000 statement_timeout_ms=3000",
                product_id,
                quantity,
                tx_elapsed_ms,
                self._slow_ms_threshold,
            )
            if tx_elapsed_ms >= self._slow_ms_threshold:
                lock_logger.warning(
                    "event=transaction_slow lock_mode=pessimistic product_id=%s quantity=%s elapsed_ms=%.2f threshold_ms=%.2f",
                    product_id,
                    quantity,
                    tx_elapsed_ms,
                    self._slow_ms_threshold,
                )

    def _reserve_optimistic(
        self, product_id: int, quantity: int
    ) -> dict[str, object] | None:
        reserve_start = time.perf_counter()
        try:
            for retry_index in range(self._retry_limit):
                with self._pool.connection(
                    autocommit=True, row_factory=dict_row
                ) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE products
                            SET stock = stock - %s
                            WHERE id = %s AND stock >= %s
                            RETURNING id, name, price, stock
                            """,
                            (quantity, product_id, quantity),
                        )
                        updated = cur.fetchone()
                        if updated:
                            if retry_index > 0:
                                elapsed_ms = (
                                    time.perf_counter() - reserve_start
                                ) * 1000
                                lock_logger.info(
                                    "event=optimistic_retry_succeeded lock_mode=optimistic product_id=%s quantity=%s retries_used=%s elapsed_ms=%.2f",
                                    product_id,
                                    quantity,
                                    retry_index,
                                    elapsed_ms,
                                )
                            return updated

                        cur.execute(
                            """
                            SELECT stock
                            FROM products
                            WHERE id = %s
                            """,
                            (product_id,),
                        )
                        row = cur.fetchone()
                        if not row:
                            return None

                        current_stock = int(row["stock"])
                        if current_stock < quantity:
                            lock_logger.warning(
                                "event=insufficient_stock lock_mode=optimistic product_id=%s quantity=%s current_stock=%s",
                                product_id,
                                quantity,
                                current_stock,
                            )
                            raise ValueError("insufficient stock")

                        lock_logger.warning(
                            "event=optimistic_conflict_retry lock_mode=optimistic product_id=%s quantity=%s retry_index=%s retry_limit=%s",
                            product_id,
                            quantity,
                            retry_index + 1,
                            self._retry_limit,
                        )

            lock_logger.warning(
                "event=optimistic_retry_exhausted lock_mode=optimistic product_id=%s quantity=%s retry_limit=%s",
                product_id,
                quantity,
                self._retry_limit,
            )
            raise RuntimeError("optimistic reserve retry limit exceeded")
        except Exception as exc:
            if POOL_ERRORS and isinstance(exc, POOL_ERRORS):
                lock_logger.exception(
                    "event=inventory_pool_error lock_mode=optimistic product_id=%s quantity=%s",
                    product_id,
                    quantity,
                )
                raise
            if isinstance(exc, psycopg.Error):
                lock_logger.exception(
                    "event=inventory_db_error lock_mode=optimistic product_id=%s quantity=%s",
                    product_id,
                    quantity,
                )
                raise
            raise
        finally:
            elapsed_ms = (time.perf_counter() - reserve_start) * 1000
            lock_logger.info(
                "event=product_service_reserve_transaction lock_mode=optimistic product_id=%s quantity=%s elapsed_ms=%.2f threshold_ms=%.2f",
                product_id,
                quantity,
                elapsed_ms,
                self._slow_ms_threshold,
            )

    def _reserve_pessimistic_with_reservation(
        self,
        product_id: int,
        quantity: int,
        reservation_ttl_seconds: int,
    ) -> dict[str, object] | None:
        tx_start = time.perf_counter()
        try:
            with self._pool.connection(autocommit=False, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = '1s'")
                    cur.execute("SET LOCAL statement_timeout = '3s'")
                    lock_start = time.perf_counter()
                    cur.execute(
                        """
                        SELECT id, name, price, stock
                        FROM products
                        WHERE id = %s
                        FOR UPDATE
                        """,
                        (product_id,),
                    )
                    row = cur.fetchone()
                    lock_wait_ms = (time.perf_counter() - lock_start) * 1000
                    lock_logger.info(
                        "event=product_service_lock_wait lock_mode=pessimistic product_id=%s quantity=%s wait_ms=%.2f threshold_ms=%.2f lock_timeout_ms=1000 statement_timeout_ms=3000",
                        product_id,
                        quantity,
                        lock_wait_ms,
                        self._slow_ms_threshold,
                    )
                    if lock_wait_ms >= self._slow_ms_threshold:
                        lock_logger.warning(
                            "event=lock_wait_slow lock_mode=pessimistic product_id=%s quantity=%s wait_ms=%.2f threshold_ms=%.2f",
                            product_id,
                            quantity,
                            lock_wait_ms,
                            self._slow_ms_threshold,
                        )
                    if not row:
                        return None

                    current_stock = int(row["stock"])
                    if current_stock < quantity:
                        lock_logger.warning(
                            "event=insufficient_stock lock_mode=pessimistic product_id=%s quantity=%s current_stock=%s",
                            product_id,
                            quantity,
                            current_stock,
                        )
                        raise ValueError("insufficient stock")

                    cur.execute(
                        """
                        UPDATE products
                        SET stock = stock - %s
                        WHERE id = %s AND stock >= %s
                        RETURNING price
                        """,
                        (quantity, product_id, quantity),
                    )
                    updated = cur.fetchone()
                    if not updated:
                        lock_logger.error(
                            "event=pessimistic_reserve_failed lock_mode=pessimistic product_id=%s quantity=%s",
                            product_id,
                            quantity,
                        )
                        raise RuntimeError("pessimistic reserve failed")

                    reservation = self._insert_reservation(
                        cur,
                        product_id,
                        quantity,
                        updated["price"],
                        reservation_ttl_seconds,
                    )
                    conn.commit()
                    return reservation
        except Exception as exc:
            if POOL_ERRORS and isinstance(exc, POOL_ERRORS):
                lock_logger.exception(
                    "event=inventory_pool_error lock_mode=pessimistic product_id=%s quantity=%s",
                    product_id,
                    quantity,
                )
                raise
            if isinstance(exc, psycopg.Error):
                lock_logger.exception(
                    "event=inventory_db_error lock_mode=pessimistic product_id=%s quantity=%s",
                    product_id,
                    quantity,
                )
                raise
            raise
        finally:
            tx_elapsed_ms = (time.perf_counter() - tx_start) * 1000
            lock_logger.info(
                "event=product_service_reserve_transaction lock_mode=pessimistic product_id=%s quantity=%s elapsed_ms=%.2f threshold_ms=%.2f lock_timeout_ms=1000 statement_timeout_ms=3000",
                product_id,
                quantity,
                tx_elapsed_ms,
                self._slow_ms_threshold,
            )
            if tx_elapsed_ms >= self._slow_ms_threshold:
                lock_logger.warning(
                    "event=transaction_slow lock_mode=pessimistic product_id=%s quantity=%s elapsed_ms=%.2f threshold_ms=%.2f",
                    product_id,
                    quantity,
                    tx_elapsed_ms,
                    self._slow_ms_threshold,
                )

    def _reserve_optimistic_with_reservation(
        self,
        product_id: int,
        quantity: int,
        reservation_ttl_seconds: int,
    ) -> dict[str, object] | None:
        reserve_start = time.perf_counter()
        try:
            for retry_index in range(self._retry_limit):
                with self._pool.connection(
                    autocommit=False, row_factory=dict_row
                ) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL lock_timeout = '1s'")
                        cur.execute("SET LOCAL statement_timeout = '3s'")
                        cur.execute(
                            """
                            UPDATE products
                            SET stock = stock - %s
                            WHERE id = %s AND stock >= %s
                            RETURNING price
                            """,
                            (quantity, product_id, quantity),
                        )
                        updated = cur.fetchone()
                        if updated:
                            reservation = self._insert_reservation(
                                cur,
                                product_id,
                                quantity,
                                updated["price"],
                                reservation_ttl_seconds,
                            )
                            conn.commit()
                            if retry_index > 0:
                                elapsed_ms = (
                                    time.perf_counter() - reserve_start
                                ) * 1000
                                lock_logger.info(
                                    "event=optimistic_retry_succeeded lock_mode=optimistic product_id=%s quantity=%s retries_used=%s elapsed_ms=%.2f",
                                    product_id,
                                    quantity,
                                    retry_index,
                                    elapsed_ms,
                                )
                            return reservation

                        cur.execute(
                            """
                            SELECT stock
                            FROM products
                            WHERE id = %s
                            """,
                            (product_id,),
                        )
                        row = cur.fetchone()
                        if not row:
                            return None

                        current_stock = int(row["stock"])
                        if current_stock < quantity:
                            lock_logger.warning(
                                "event=insufficient_stock lock_mode=optimistic product_id=%s quantity=%s current_stock=%s",
                                product_id,
                                quantity,
                                current_stock,
                            )
                            raise ValueError("insufficient stock")

                        lock_logger.warning(
                            "event=optimistic_conflict_retry lock_mode=optimistic product_id=%s quantity=%s retry_index=%s retry_limit=%s",
                            product_id,
                            quantity,
                            retry_index + 1,
                            self._retry_limit,
                        )
            lock_logger.warning(
                "event=optimistic_retry_exhausted lock_mode=optimistic product_id=%s quantity=%s retry_limit=%s",
                product_id,
                quantity,
                self._retry_limit,
            )
            raise RuntimeError("optimistic reserve retry limit exceeded")
        except Exception as exc:
            if POOL_ERRORS and isinstance(exc, POOL_ERRORS):
                lock_logger.exception(
                    "event=inventory_pool_error lock_mode=optimistic product_id=%s quantity=%s",
                    product_id,
                    quantity,
                )
                raise
            if isinstance(exc, psycopg.Error):
                lock_logger.exception(
                    "event=inventory_db_error lock_mode=optimistic product_id=%s quantity=%s",
                    product_id,
                    quantity,
                )
                raise
            raise
        finally:
            elapsed_ms = (time.perf_counter() - reserve_start) * 1000
            lock_logger.info(
                "event=product_service_reserve_transaction lock_mode=optimistic product_id=%s quantity=%s elapsed_ms=%.2f threshold_ms=%.2f lock_timeout_ms=1000 statement_timeout_ms=3000",
                product_id,
                quantity,
                elapsed_ms,
                self._slow_ms_threshold,
            )
