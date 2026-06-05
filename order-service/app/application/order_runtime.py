from app.application.create_order_use_case import CreateOrderUseCase
from app.application.order_queries import OrderQueries
from app.application.process_terminalization_task_use_case import (
    ProcessTerminalizationTaskUseCase,
)
from app.ports.unit_of_work import UnitOfWork
from app.ports.product_reservation_client import ProductReservationClient
from app.ports.user_directory_client import UserDirectoryClient


class OrderRuntime:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserDirectoryClient,
        products: ProductReservationClient,
    ) -> None:
        self.uow = uow
        self.create_orders = CreateOrderUseCase(uow=uow, users=users, products=products)
        self.process_tasks = ProcessTerminalizationTaskUseCase(
            uow=uow,
            products=products,
        )
        self.queries = OrderQueries(uow=uow)
