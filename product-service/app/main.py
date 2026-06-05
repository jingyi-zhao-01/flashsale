from fastapi import FastAPI, Response

from .config import SEED_PRODUCT_COUNT, SEED_PRODUCT_QUANTITY, db_url, use_postgres
from .models import (
    ErrorResponse,
    ExpireReservationsResult,
    HealthResponse,
    ProductCreate,
    ProductOut,
    ReservationOut,
    ReserveRequest,
)
from flashsale_shared.observability import (
    configure_service_logger,
    create_request_logging_middleware,
    initialize_tracing,
)
from .repositories import InMemoryProductRepository, PostgresProductRepository
from .service import ProductService

SERVICE_NAME = "product-service"
initialize_tracing(SERVICE_NAME)
logger = configure_service_logger(SERVICE_NAME)

app = FastAPI(title=SERVICE_NAME, version="0.1.0")
app.middleware("http")(create_request_logging_middleware(logger, SERVICE_NAME))

if use_postgres():
    repository = PostgresProductRepository(db_url())
    storage = "postgres"
else:
    repository = InMemoryProductRepository()
    storage = "memory"

product_service = ProductService(repository=repository, logger=logger, storage=storage)
app.state.repository = repository


@app.on_event("startup")
def startup() -> None:
    logger.info("event=startup service=%s", SERVICE_NAME)
    try:
        product_service.startup()
    except Exception:
        # Keep service available even if DB startup checks fail.
        pass


@app.get(
    "/health",
    summary="Service health check",
    description="Returns basic process health for product-service without touching dependencies.",
    tags=["system"],
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=SERVICE_NAME)


@app.get(
    "/ready",
    summary="Service readiness check",
    description="Returns dependency readiness for product-service, including database reachability.",
    tags=["system"],
)
async def ready() -> HealthResponse:
    return HealthResponse(status="ok", service=SERVICE_NAME)


@app.get(
    "/live",
    summary="Service liveness check",
    description="Returns basic process liveness for product-service without touching dependencies.",
    tags=["system"],
)
async def live() -> HealthResponse:
    return HealthResponse(status="ok", service=SERVICE_NAME)


@app.post(
    "/products",
    status_code=201,
    summary="Create product",
    description="Creates a product record that can later be reserved by orders.",
    tags=["products"],
    responses={503: {"model": ErrorResponse, "description": "Database unavailable"}},
)
def create_product(payload: ProductCreate) -> ProductOut:
    return product_service.create_product(payload)


@app.get(
    "/products/{product_id}",
    summary="Get product",
    description="Fetches a single product by identifier.",
    tags=["products"],
    responses={
        404: {"model": ErrorResponse, "description": "Product not found"},
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)
def get_product(product_id: int) -> ProductOut:
    return product_service.get_product(product_id)


@app.post(
    "/products/{product_id}/reserve",
    summary="Reserve product stock",
    description="Reserves available stock for a product before order persistence is finalized.",
    tags=["reservations"],
    responses={
        404: {"model": ErrorResponse, "description": "Product not found"},
        409: {"model": ErrorResponse, "description": "Insufficient stock"},
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)
def reserve_product(product_id: int, payload: ReserveRequest) -> ReservationOut:
    return product_service.reserve_product(product_id, payload)


@app.post(
    "/reservations/{reservation_id}/confirm",
    summary="Confirm reservation",
    description="Marks a reservation as confirmed after order persistence succeeds.",
    tags=["reservations"],
    responses={
        404: {"model": ErrorResponse, "description": "Reservation not found"},
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)
def confirm_reservation(reservation_id: int) -> ReservationOut:
    return product_service.confirm_reservation(reservation_id)


@app.post(
    "/reservations/{reservation_id}/cancel",
    summary="Cancel reservation",
    description="Cancels a reservation and restores stock when order persistence fails.",
    tags=["reservations"],
    responses={
        404: {"model": ErrorResponse, "description": "Reservation not found"},
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)
def cancel_reservation(reservation_id: int) -> ReservationOut:
    return product_service.cancel_reservation(reservation_id)


@app.post(
    "/admin/expire-reservations",
    summary="Expire reservations",
    description="Expires stale reservations and restores stock for the associated products.",
    tags=["admin"],
    responses={503: {"model": ErrorResponse, "description": "Database unavailable"}},
)
def expire_reservations() -> ExpireReservationsResult:
    return product_service.expire_reservations()


@app.get(
    "/products",
    summary="List products",
    description="Returns all products visible to the service storage backend.",
    tags=["products"],
    responses={503: {"model": ErrorResponse, "description": "Database unavailable"}},
)
def list_products() -> list[ProductOut]:
    return product_service.list_products()


@app.post(
    "/admin/reset",
    status_code=204,
    summary="Reset product storage",
    description="Clears product-service backing storage for local and integration test workflows.",
    tags=["admin"],
    responses={503: {"model": ErrorResponse, "description": "Database unavailable"}},
)
def admin_reset() -> Response:
    product_service._repository.reset_db()
    return Response(status_code=204)


@app.post(
    "/admin/seed",
    status_code=204,
    summary="Seed products",
    description="Seeds the repository with default products for local or smoke test workflows.",
    tags=["admin"],
    responses={503: {"model": ErrorResponse, "description": "Database unavailable"}},
)
def admin_seed() -> Response:
    product_service._repository.seed_with_quantity(
        SEED_PRODUCT_COUNT, SEED_PRODUCT_QUANTITY
    )
    return Response(status_code=204)
