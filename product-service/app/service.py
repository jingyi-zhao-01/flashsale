import logging
import time

from fastapi import HTTPException
import psycopg

from .config import DATABASE_UNAVAILABLE_MESSAGE, PRODUCT_NOT_FOUND_MESSAGE
from .models import (
    ExpireReservationsResult,
    ProductCreate,
    ProductOut,
    ReservationOut,
    ReserveRequest,
)
from flashsale_shared.observability import start_span
from .repositories import ProductRepository


def _exception_name(exc: Exception) -> str:
    return exc.__class__.__name__


def _is_pool_error(exc: Exception) -> bool:
    return _exception_name(exc) in {"PoolTimeout", "PoolClosed", "TooManyRequests"}


def _is_psycopg_error(exc: Exception, *names: str) -> bool:
    errors = getattr(psycopg, "errors", None)
    if errors is not None:
        for name in names:
            exc_type = getattr(errors, name, None)
            if exc_type is not None and isinstance(exc, exc_type):
                return True
    return _exception_name(exc) in set(names)


def _map_inventory_error(exc: Exception) -> HTTPException:
    if _is_pool_error(exc):
        return HTTPException(
            status_code=503,
            detail="inventory database pool exhausted",
        )
    if _is_psycopg_error(exc, "LockNotAvailable", "DeadlockDetected"):
        return HTTPException(
            status_code=409,
            detail="inventory is busy, retry later",
        )
    if _is_psycopg_error(exc, "QueryCanceled"):
        return HTTPException(
            status_code=504,
            detail="inventory request timed out",
        )
    psycopg_error = getattr(psycopg, "Error", None)
    if psycopg_error is not None and isinstance(exc, psycopg_error):
        return HTTPException(
            status_code=503,
            detail=DATABASE_UNAVAILABLE_MESSAGE,
        )
    return HTTPException(
        status_code=503,
        detail=DATABASE_UNAVAILABLE_MESSAGE,
    )

class ProductService:
    def __init__(
        self, repository: ProductRepository, logger: logging.Logger, storage: str
    ) -> None:
        self._repository = repository
        self._logger = logger
        self._storage = storage

    def startup(self) -> None:
        self._repository.init_db()
        self._repository.seed_if_empty()

    def create_product(self, payload: ProductCreate) -> ProductOut:
        try:
            product = self._repository.create_product(payload)
            self._logger.info(
                "event=product_created product_id=%s name=%s stock=%s storage=%s",
                product.id,
                product.name,
                product.stock,
                self._storage,
            )
            return product
        except RuntimeError:
            self._logger.error(
                "event=product_create_failed reason=persistence_empty name=%s storage=%s",
                payload.name,
                self._storage,
            )
            raise HTTPException(status_code=503, detail="product persistence failed")
        except HTTPException:
            raise
        except Exception as exc:
            self._logger.exception(
                "event=product_create_error storage=%s", self._storage
            )
            raise _map_inventory_error(exc) from exc
    def get_product(self, product_id: int) -> ProductOut:
        try:
            product = self._repository.get_product(product_id)
            if not product:
                self._logger.info(
                    "event=product_not_found product_id=%s storage=%s",
                    product_id,
                    self._storage,
                )
                raise HTTPException(status_code=404, detail=PRODUCT_NOT_FOUND_MESSAGE)
            return product
        except HTTPException:
            raise
        except Exception as exc:
            self._logger.exception(
                "event=product_get_error storage=%s product_id=%s",
                self._storage,
                product_id,
            )
            raise _map_inventory_error(exc) from exc

    def reserve_product(
        self, product_id: int, payload: ReserveRequest
    ) -> ReservationOut:
        start = time.perf_counter()
        result = "unknown"
        try:
            with start_span(
                "product-service",
                "reserve product",
                attributes={
                    "flashsale.product_id": product_id,
                    "flashsale.quantity": payload.quantity,
                },
            ):
                reservation = self._repository.reserve_product(
                    product_id,
                    payload.quantity,
                )
            if not reservation:
                result = "missing"
                self._logger.info(
                    "event=product_not_found product_id=%s storage=%s",
                    product_id,
                    self._storage,
                )
                raise HTTPException(status_code=404, detail=PRODUCT_NOT_FOUND_MESSAGE)
            result = "reserved"
            self._logger.info(
                "event=product_reserved product_id=%s quantity=%s storage=%s",
                product_id,
                payload.quantity,
                self._storage,
            )
            return reservation
        except ValueError as exc:
            result = "insufficient_stock"
            self._logger.warning(
                "event=product_reserve_conflict product_id=%s quantity=%s storage=%s",
                product_id,
                payload.quantity,
                self._storage,
            )
            raise HTTPException(status_code=409, detail="insufficient stock") from exc
        except RuntimeError as exc:
            if str(exc) == "retry_exhausted":
                result = "busy"
                self._logger.warning(
                    "event=product_reserve_busy product_id=%s quantity=%s storage=%s",
                    product_id,
                    payload.quantity,
                    self._storage,
                )
                raise HTTPException(
                    status_code=409,
                    detail="inventory is busy, retry later",
                ) from exc
            result = "error"
            self._logger.exception(
                "event=product_reserve_error storage=%s product_id=%s quantity=%s",
                self._storage,
                product_id,
                payload.quantity,
            )
            raise _map_inventory_error(exc) from exc
        except HTTPException:
            raise
        except Exception as exc:
            result = "error"
            self._logger.exception(
                "event=product_reserve_error storage=%s product_id=%s quantity=%s",
                self._storage,
                product_id,
                payload.quantity,
            )
            raise _map_inventory_error(exc) from exc
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._logger.info(
                "event=product_service_reserve_finished product_id=%s quantity=%s elapsed_ms=%.2f result=%s storage=%s",
                product_id,
                payload.quantity,
                elapsed_ms,
                result,
                self._storage,
            )

    def confirm_reservation(self, reservation_id: int) -> ReservationOut:
        start = time.perf_counter()
        result = "unknown"
        try:
            with start_span(
                "product-service",
                "confirm reservation",
                attributes={"flashsale.reservation_id": reservation_id},
            ):
                reservation = self._repository.confirm_reservation(reservation_id)
            if not reservation:
                result = "missing"
                self._logger.info(
                    "event=reservation_not_found reservation_id=%s storage=%s",
                    reservation_id,
                    self._storage,
                )
                raise HTTPException(status_code=404, detail="reservation not found")
            result = reservation.status
            self._logger.info(
                "event=reservation_confirmed reservation_id=%s storage=%s",
                reservation_id,
                self._storage,
            )
            return reservation
        except HTTPException:
            raise
        except Exception as exc:
            result = "error"
            self._logger.exception(
                "event=reservation_confirm_error storage=%s reservation_id=%s",
                self._storage,
                reservation_id,
            )
            raise _map_inventory_error(exc) from exc
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._logger.info(
                "event=product_service_confirm_cancel_finished action=confirm reservation_id=%s elapsed_ms=%.2f confirm_cancel_ms=%.2f result=%s storage=%s",
                reservation_id,
                elapsed_ms,
                elapsed_ms,
                result,
                self._storage,
            )

    def cancel_reservation(self, reservation_id: int) -> ReservationOut:
        start = time.perf_counter()
        result = "unknown"
        try:
            with start_span(
                "product-service",
                "cancel reservation",
                attributes={"flashsale.reservation_id": reservation_id},
            ):
                reservation = self._repository.cancel_reservation(reservation_id)
            if not reservation:
                result = "missing"
                self._logger.info(
                    "event=reservation_not_found reservation_id=%s storage=%s",
                    reservation_id,
                    self._storage,
                )
                raise HTTPException(status_code=404, detail="reservation not found")
            result = reservation.status
            self._logger.info(
                "event=reservation_cancelled reservation_id=%s storage=%s",
                reservation_id,
                self._storage,
            )
            return reservation
        except HTTPException:
            raise
        except Exception as exc:
            result = "error"
            self._logger.exception(
                "event=reservation_cancel_error storage=%s reservation_id=%s",
                self._storage,
                reservation_id,
            )
            raise _map_inventory_error(exc) from exc
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._logger.info(
                "event=product_service_confirm_cancel_finished action=cancel reservation_id=%s elapsed_ms=%.2f confirm_cancel_ms=%.2f result=%s storage=%s",
                reservation_id,
                elapsed_ms,
                elapsed_ms,
                result,
                self._storage,
            )

    def expire_reservations(self) -> ExpireReservationsResult:
        try:
            expired_count = self._repository.expire_reservations()
            self._logger.info(
                "event=reservation_expired expired_count=%s storage=%s",
                expired_count,
                self._storage,
            )
            return ExpireReservationsResult(expired_count=expired_count)
        except Exception as exc:
            self._logger.exception(
                "event=reservation_expire_error storage=%s",
                self._storage,
            )
            raise _map_inventory_error(exc) from exc

    def list_products(self) -> list[ProductOut]:
        try:
            products = self._repository.list_products()
            self._logger.info(
                "event=product_list count=%s storage=%s", len(products), self._storage
            )
            return products
        except Exception as exc:
            self._logger.exception("event=product_list_error storage=%s", self._storage)
            raise _map_inventory_error(exc) from exc
