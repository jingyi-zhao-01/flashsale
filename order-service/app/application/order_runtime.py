from app.application.create_order_use_case import CreateOrderUseCase
from app.application.order_queries import OrderQueries
from app.application.process_terminalization_task_use_case import (
    ProcessTerminalizationTaskUseCase,
)
from app.ports.reserve_admission_gate import ReserveAdmissionGate
from app.ports.terminalization_command_publisher import TerminalizationCommandPublisher
from app.ports.unit_of_work import UnitOfWork
from app.ports.product_reservation_client import ProductReservationClient
from app.ports.user_directory_client import UserDirectoryClient


class OrderRuntime:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserDirectoryClient,
        products: ProductReservationClient,
        admission: ReserveAdmissionGate | None = None,
        terminalization: TerminalizationCommandPublisher | None = None,
    ) -> None:
        self.uow = uow
        self.create_orders = CreateOrderUseCase(
            uow=uow,
            users=users,
            products=products,
            admission=admission,
            terminalization=terminalization,
        )
        self.process_tasks = ProcessTerminalizationTaskUseCase(
            uow=uow,
            products=products,
            terminalization=terminalization,
        )
        self.queries = OrderQueries(uow=uow)
