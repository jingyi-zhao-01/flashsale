from fastapi import FastAPI

from .config import db_url, use_postgres
from .models import HealthResponse, UserCreate, UserOut
from flashsale_shared.observability import (
    configure_service_logger,
    create_request_logging_middleware,
    initialize_tracing,
)
from .repositories import InMemoryUserRepository, PostgresUserRepository
from .service import UserService

SERVICE_NAME = "user-service"
initialize_tracing(SERVICE_NAME)
logger = configure_service_logger(SERVICE_NAME)

app = FastAPI(title=SERVICE_NAME, version="0.1.0")
app.middleware("http")(create_request_logging_middleware(logger, SERVICE_NAME))

if use_postgres():
    repository = PostgresUserRepository(db_url())
    storage = "postgres"
else:
    repository = InMemoryUserRepository()
    storage = "memory"

user_service = UserService(repository=repository, logger=logger, storage=storage)
app.state.repository = repository


@app.on_event("startup")
def startup() -> None:
    logger.info("event=startup service=%s", SERVICE_NAME)
    try:
        user_service.init_db()
    except Exception:
        # Keep service available even if DB startup checks fail.
        pass


@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=SERVICE_NAME)


@app.get("/ready")
async def ready() -> HealthResponse:
    return HealthResponse(status="ok", service=SERVICE_NAME)


@app.get("/live")
async def live() -> HealthResponse:
    return HealthResponse(status="ok", service=SERVICE_NAME)


@app.post(
    "/users",
    status_code=201,
    responses={
        409: {"description": "User email already exists"},
        503: {"description": "Database unavailable"},
    },
)
def create_user(payload: UserCreate) -> UserOut:
    return user_service.create_user(payload)


@app.get(
    "/users/{user_id}",
    responses={
        404: {"description": "User not found"},
        503: {"description": "Database unavailable"},
    },
)
def get_user(user_id: int) -> UserOut:
    return user_service.get_user(user_id)


@app.get(
    "/users",
    responses={503: {"description": "Database unavailable"}},
)
def list_users() -> list[UserOut]:
    return user_service.list_users()


@app.post(
    "/admin/reset",
    status_code=204,
    responses={503: {"description": "Database unavailable"}},
)
def admin_reset() -> None:
    user_service._repository.reset_db()
