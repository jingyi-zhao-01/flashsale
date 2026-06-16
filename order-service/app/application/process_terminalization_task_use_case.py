import logging
import time

from app.application.results import ProcessTerminalizationTasksResult
from app.config import KAFKA_TERMINALIZATION_MAX_ATTEMPTS
from app.domain.statuses import PaymentStatus, OrderStatus, TerminalizationAction
from app.domain.terminalization_command import TerminalizationCommand
from flashsale_shared.observability import start_span
from app.ports.product_reservation_client import ProductReservationClient
from app.ports.terminalization_command_publisher import (
    NoOpTerminalizationCommandPublisher,
    TerminalizationCommandPublisher,
)
from app.ports.unit_of_work import UnitOfWork

terminalization_logger = logging.getLogger("order-service.terminalization")


class ProcessTerminalizationTaskUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        products: ProductReservationClient,
        terminalization: TerminalizationCommandPublisher | None = None,
    ) -> None:
        self._uow = uow
        self._products = products
        self._terminalization = (
            terminalization
            if terminalization is not None
            else NoOpTerminalizationCommandPublisher()
        )

    def set_terminalization_publisher(
        self,
        terminalization: TerminalizationCommandPublisher,
    ) -> None:
        self._terminalization = terminalization

    def process(self, limit: int = 32) -> ProcessTerminalizationTasksResult:
        terminalization_logger.info(
            "event=order_service_worker_poll result=disabled backend=kafka claimed_count=0"
        )
        return ProcessTerminalizationTasksResult(0, 0, 0)

    def process_kafka_command(self, command: TerminalizationCommand) -> None:
        with start_span(
            "order-service",
            "terminalize reservation",
            attributes={
                "flashsale.order_id": command.order_id,
                "flashsale.reservation_id": command.reservation_id,
                "flashsale.action": command.action,
            },
        ):
            started_at = time.perf_counter()
            ok, error = self._products.terminalize(
                command.reservation_id,
                command.action,
            )
            if ok and command.action == "confirm":
                ok, error = self._update_order_state(command.order_id, command.action)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
        result = "success" if ok else "retry"
        terminalization_logger.info(
            "event=order_service_terminalization_call order_id=%s reservation_id=%s action=%s elapsed_ms=%.2f confirm_cancel_ms=%.2f result=%s attempt_count=%s",
            command.order_id,
            command.reservation_id,
            command.action,
            elapsed_ms,
            elapsed_ms,
            result,
            command.attempt,
        )
        if ok:
            return
        last_error = error or "terminalization_failed"
        if command.attempt >= KAFKA_TERMINALIZATION_MAX_ATTEMPTS:
            self._terminalization.publish_dead_letter(command, last_error)
            terminalization_logger.warning(
                "event=order_service_terminalization_dead_lettered order_id=%s reservation_id=%s action=%s attempt_count=%s error=%s",
                command.order_id,
                command.reservation_id,
                command.action,
                command.attempt,
                last_error,
            )
            return
        self._terminalization.publish_retry(command, last_error)

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
