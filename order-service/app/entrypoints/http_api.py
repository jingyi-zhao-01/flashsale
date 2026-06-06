import anyio
import httpx
from fastapi import Depends, FastAPI, HTTPException, Response, status
from redis import Redis
from redis.exceptions import RedisError

from app.adapters.order_mapping import to_api_order
from app.adapters.order_postgres_unit_of_work import OrderPostgresUnitOfWork
from app.adapters.product_reservation_http_client import ProductReservationHttpClient
from app.adapters.redis_reserve_admission_gate import RedisReserveAdmissionGate
from app.adapters.user_http_client import UserHttpClient
from app.application.commands import CreateOrderCommand, PaymentWebhookCommand
from app.application.order_runtime import OrderRuntime
from app.config import db_url
from app.config import ORDER_CREATE_MAX_IN_FLIGHT, REDIS_URL, REDIS_TOKEN
from app.ports.reserve_admission_gate import ReserveAdmissionGate
from app.entrypoints.worker_loop import TerminalizationWorkerLoop
from app.models import (
    ErrorResponse,
    ExpireOrdersResult,
    HealthResponse,
    OrderCreateRequest,
    OrderOut,
    PaymentWebhookRequest,
    ProcessTerminalizationTasksResult,
)
from flashsale_shared.observability import (
    configure_service_logger,
    create_request_logging_middleware,
    initialize_tracing,
)
from flashsale_shared.reset_control import ResetController

SERVICE_NAME = "order-service"


def _redact_url(url: str) -> str:
    """Return a safe-for-logging version of a Redis URL."""
    if "@" in url:
        prefix, rest = url.split("@", 1)
        scheme = prefix.split("://")[0] if "://" in prefix else ""
        return f"{scheme}://***@{rest}" if scheme else f"***@{rest}"
    return url


def build_http_api(
    run_background_worker: bool = True,
) -> tuple[FastAPI, object, OrderRuntime]:
    initialize_tracing(SERVICE_NAME)
    logger = configure_service_logger(SERVICE_NAME)
    app = FastAPI(title=SERVICE_NAME, version="0.1.0")
    app.middleware("http")(create_request_logging_middleware(logger, SERVICE_NAME))

    uow = OrderPostgresUnitOfWork(db_url())
    admission: ReserveAdmissionGate | None = None
    if REDIS_URL:
        try:
            redis_client = Redis.from_url(REDIS_URL, password=REDIS_TOKEN or None)
            redis_client.ping()
            admission = RedisReserveAdmissionGate(redis_client)
            logger.info(
                "event=redis_admission_gate_initialized redis_url=%s max_inflight=%s",
                _redact_url(REDIS_URL),
                admission._max_inflight,
            )
        except (RedisError, OSError) as exc:
            logger.warning(
                "event=redis_admission_gate_unavailable redis_url=%s error=%s",
                _redact_url(REDIS_URL),
                exc,
            )
    else:
        logger.info("event=redis_admission_gate_disabled reason=no_REDIS_URL")

    runtime = OrderRuntime(
        uow=uow,
        users=UserHttpClient(lambda: httpx.Client()),
        products=ProductReservationHttpClient(lambda: httpx.Client()),
        admission=admission,
    )
    worker = TerminalizationWorkerLoop(runtime.process_tasks.process)
    orders_limiter = anyio.CapacityLimiter(ORDER_CREATE_MAX_IN_FLIGHT)
    repository = uow
    app.state.repository = repository
    app.state.reset_target = uow
    app.state.reset_controller = ResetController()

    @app.on_event("startup")
    def startup() -> None:
        try:
            uow.init_db()
            if run_background_worker:
                worker.start()
        except Exception:
            pass

    @app.on_event("shutdown")
    def shutdown() -> None:
        if run_background_worker:
            worker.stop()

    async def order_capacity_guard() -> None:
        try:
            orders_limiter.acquire_nowait()
        except anyio.WouldBlock as exc:
            logger.warning(
                "event=order_service_admission_rejected path=/orders max_in_flight=%s result=busy",
                ORDER_CREATE_MAX_IN_FLIGHT,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="order-service overloaded, retry later",
            ) from exc
        try:
            yield
        finally:
            orders_limiter.release()

    @app.get(
        "/health",
        summary="Service health check",
        description="Returns basic process health for order-service without touching dependencies.",
        tags=["system"],
    )
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service=SERVICE_NAME)

    @app.get(
        "/ready",
        summary="Service readiness check",
        description="Returns process readiness for order-service without touching dependencies.",
        tags=["system"],
    )
    async def ready() -> HealthResponse:
        return HealthResponse(status="ok", service=SERVICE_NAME)

    @app.get(
        "/live",
        summary="Service liveness check",
        description="Returns basic process liveness for order-service without touching dependencies.",
        tags=["system"],
    )
    async def live() -> HealthResponse:
        return HealthResponse(status="ok", service=SERVICE_NAME)

    @app.post(
        "/orders",
        status_code=201,
        summary="Create order",
        description=(
            "Validates the user, reserves product stock, persists a pending order, "
            "and enqueues terminalization work."
        ),
        tags=["orders"],
        responses={
            400: {"model": ErrorResponse, "description": "Order items cannot be empty"},
            404: {"model": ErrorResponse, "description": "User or product not found"},
            409: {"model": ErrorResponse, "description": "Insufficient product stock"},
            429: {"model": ErrorResponse, "description": "Order service is busy"},
            502: {
                "model": ErrorResponse,
                "description": "Dependent service unavailable",
            },
            504: {
                "model": ErrorResponse,
                "description": "Dependent service timed out",
            },
            503: {"model": ErrorResponse, "description": "Database unavailable"},
        },
        dependencies=[Depends(order_capacity_guard)],
    )
    def create_order(payload: OrderCreateRequest) -> OrderOut:
        order = runtime.create_orders.create_order(
            CreateOrderCommand(
                user_id=payload.user_id,
                items=tuple((item.product_id, item.quantity) for item in payload.items),
                idempotency_key=payload.idempotency_key,
            )
        )
        return to_api_order(order)

    @app.get(
        "/orders/{order_id}",
        summary="Get order",
        description="Fetches a single order by identifier.",
        tags=["orders"],
        responses={
            404: {"model": ErrorResponse, "description": "Order not found"},
            503: {"model": ErrorResponse, "description": "Database unavailable"},
        },
    )
    def get_order(order_id: int) -> OrderOut:
        return to_api_order(runtime.queries.get_order(order_id))

    @app.get(
        "/orders",
        summary="List orders",
        description="Returns all persisted orders visible to the service storage backend.",
        tags=["orders"],
        responses={
            503: {"model": ErrorResponse, "description": "Database unavailable"}
        },
    )
    def list_orders() -> list[OrderOut]:
        return [to_api_order(order) for order in runtime.queries.list_orders()]

    @app.post(
        "/payments/webhook",
        summary="Process payment webhook",
        description="Applies a successful payment event to the referenced order.",
        tags=["payments"],
        responses={
            404: {"model": ErrorResponse, "description": "Order not found"},
            503: {"model": ErrorResponse, "description": "Database unavailable"},
        },
    )
    def payment_webhook(payload: PaymentWebhookRequest) -> OrderOut:
        order = runtime.create_orders.process_payment_webhook(
            PaymentWebhookCommand(
                order_id=payload.order_id,
                event_id=payload.event_id,
            )
        )
        return to_api_order(order)

    @app.post(
        "/admin/reset",
        status_code=204,
        summary="Reset order storage",
        description=(
            "Clears order-service backing storage for local and integration test workflows. "
            "Pass wait=false to trigger the reset asynchronously."
        ),
        tags=["admin"],
        responses={
            202: {"description": "Reset accepted and running in background"},
            503: {"model": ErrorResponse, "description": "Database unavailable"},
        },
    )
    def admin_reset(wait: bool = True) -> Response:
        if wait:
            app.state.reset_controller.run(app.state.reset_target.reset)
            return Response(status_code=204)
        app.state.reset_controller.trigger(app.state.reset_target.reset)
        return Response(status_code=202)

    @app.post(
        "/admin/expire-orders",
        summary="Expire pending orders",
        description="Marks elapsed pending orders as expired and releases their reservations.",
        tags=["admin"],
        responses={
            503: {"model": ErrorResponse, "description": "Database unavailable"}
        },
    )
    def admin_expire_orders() -> ExpireOrdersResult:
        result = runtime.create_orders.expire_orders()
        return ExpireOrdersResult(expired_count=result.expired_count)

    @app.post(
        "/admin/process-terminalizations",
        summary="Process terminalization tasks",
        description="Runs one pass of the reservation confirm/cancel worker for diagnostics and tests.",
        tags=["admin"],
        responses={
            503: {"model": ErrorResponse, "description": "Database unavailable"}
        },
    )
    def admin_process_terminalizations() -> ProcessTerminalizationTasksResult:
        result = runtime.process_tasks.process()
        return ProcessTerminalizationTasksResult(**result.__dict__)

    return app, repository, runtime
