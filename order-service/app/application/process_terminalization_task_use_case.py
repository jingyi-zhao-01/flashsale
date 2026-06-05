from datetime import datetime, timedelta, timezone
import logging
import time

from app.application.results import ProcessTerminalizationTasksResult
from app.domain.statuses import PaymentStatus, OrderStatus, TerminalizationAction
from flashsale_shared.observability import start_span
from app.ports.product_reservation_client import ProductReservationClient
from app.ports.unit_of_work import UnitOfWork

terminalization_logger = logging.getLogger("order-service.terminalization")


class ProcessTerminalizationTaskUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        products: ProductReservationClient,
    ) -> None:
        self._uow = uow
        self._products = products

    def process(self, limit: int = 32) -> ProcessTerminalizationTasksResult:
        poll_start = time.perf_counter()
        with start_span(
            "order-service",
            "process terminalization batch",
            attributes={"flashsale.batch_limit": limit},
        ):
            tasks = self._uow.tasks.claim_ready(
                limit=limit,
                available_before=datetime.now(timezone.utc),
            )
        if not tasks:
            terminalization_logger.info(
                "event=order_service_worker_poll claimed_count=0 succeeded_count=0 retrying_count=0 total_poll_ms=%.2f result=empty",
                (time.perf_counter() - poll_start) * 1000,
            )
            return ProcessTerminalizationTasksResult(0, 0, 0)

        succeeded_count = 0
        retrying_count = 0
        for task in tasks:
            with start_span(
                "order-service",
                "terminalize reservation",
                attributes={
                    "flashsale.order_id": task.order_id,
                    "flashsale.reservation_id": task.reservation_id,
                    "flashsale.action": task.action,
                },
            ):
                started_at = time.perf_counter()
                ok, error = self._products.terminalize(
                    task.reservation_id, task.action
                )
                if ok and task.action == "confirm":
                    ok, error = self._update_order_state(task.order_id, task.action)
                elapsed_ms = (time.perf_counter() - started_at) * 1000
            terminalization_logger.info(
                "event=order_service_terminalization_call order_id=%s reservation_id=%s action=%s elapsed_ms=%.2f confirm_cancel_ms=%.2f result=%s attempt_count=%s",
                task.order_id,
                task.reservation_id,
                task.action,
                elapsed_ms,
                elapsed_ms,
                "success" if ok else "retry",
                task.attempt_count,
            )
            if ok:
                self._uow.tasks.mark_succeeded(task.task_id)
                succeeded_count += 1
                continue
            retrying_count += 1
            self._uow.tasks.record_event(
                task_id=task.task_id,
                order_id=task.order_id,
                reservation_id=task.reservation_id,
                action=task.action,
                event_type="error",
                attempt_count=task.attempt_count,
                last_error=error,
            )
            self._uow.tasks.mark_retrying(
                task_id=task.task_id,
                available_at=datetime.now(timezone.utc)
                + timedelta(seconds=min(task.attempt_count, 10)),
                last_error=error or "terminalization_failed",
            )

        terminalization_logger.info(
            "event=order_service_worker_poll claimed_count=%s succeeded_count=%s retrying_count=%s total_poll_ms=%.2f result=processed",
            len(tasks),
            succeeded_count,
            retrying_count,
            (time.perf_counter() - poll_start) * 1000,
        )
        return ProcessTerminalizationTasksResult(
            claimed_count=len(tasks),
            succeeded_count=succeeded_count,
            retrying_count=retrying_count,
        )

    def _update_order_state(
        self, order_id: int, action: TerminalizationAction
    ) -> tuple[bool, str | None]:
        if action != "confirm":
            return True, None
        target_status: OrderStatus
        target_payment_status: PaymentStatus
        target_status = "confirmed"
        target_payment_status = "succeeded"
        try:
            updated = self._uow.orders.update_state(
                order_id,
                status=target_status,
                payment_status=target_payment_status,
            )
        except Exception as exc:  # pragma: no cover - defensive retry path
            return False, exc.__class__.__name__
        if not updated:
            return False, "order_not_found"
        return True, None
