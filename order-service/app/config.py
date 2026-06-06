import os

from flashsale_shared.postgres_schema import with_search_path

DATABASE_UNAVAILABLE_MESSAGE = "database unavailable"

USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://localhost:8001")
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL", "http://localhost:8002")
DEPENDENCY_TIMEOUT_SECONDS = float(os.getenv("DEPENDENCY_TIMEOUT_SECONDS", "5"))


def _float_env(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


USER_SERVICE_TIMEOUT_SECONDS = _float_env(
    "USER_SERVICE_TIMEOUT_SECONDS", DEPENDENCY_TIMEOUT_SECONDS
)
PRODUCT_RESERVE_TIMEOUT_SECONDS = _float_env(
    "PRODUCT_RESERVE_TIMEOUT_SECONDS", DEPENDENCY_TIMEOUT_SECONDS
)
PRODUCT_RELEASE_TIMEOUT_SECONDS = _float_env(
    "PRODUCT_RELEASE_TIMEOUT_SECONDS", DEPENDENCY_TIMEOUT_SECONDS
)
PRODUCT_TERMINALIZE_TIMEOUT_SECONDS = _float_env(
    "PRODUCT_TERMINALIZE_TIMEOUT_SECONDS", DEPENDENCY_TIMEOUT_SECONDS
)
ORDER_PENDING_TTL_SECONDS = int(os.getenv("ORDER_PENDING_TTL_SECONDS", "300"))
ORDER_CREATE_MAX_IN_FLIGHT = int(os.getenv("ORDER_CREATE_MAX_IN_FLIGHT", "32"))
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "4"))
DB_POOL_TIMEOUT_SECONDS = float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "5"))
DB_HOST = os.getenv("DB_HOST", "")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_SCHEMA = os.getenv("DB_SCHEMA", "order_service")


def db_url() -> str:
    if DATABASE_URL:
        return with_search_path(DATABASE_URL, DB_SCHEMA)

    if DB_HOST and DB_NAME and DB_USER and DB_PASSWORD:
        return with_search_path(
            f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
            DB_SCHEMA,
        )

    return ""


def use_postgres() -> bool:
    return bool(db_url())
