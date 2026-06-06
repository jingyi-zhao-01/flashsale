import os

from flashsale_shared.postgres_schema import with_search_path

DATABASE_UNAVAILABLE_MESSAGE = "database unavailable"
PRODUCT_NOT_FOUND_MESSAGE = "product not found"
DEFAULT_SEED_PRODUCT_COUNT = 100

# Perf test seed config
SEED_PRODUCT_COUNT = int(os.getenv("SEED_PRODUCT_COUNT", "1000"))
SEED_PRODUCT_QUANTITY = int(os.getenv("SEED_PRODUCT_QUANTITY", "10000"))
RESERVE_SQL_LOG_SLOW_MS = float(os.getenv("RESERVE_SQL_LOG_SLOW_MS", "200"))
INVENTORY_LOCK_MODE = os.getenv("INVENTORY_LOCK_MODE", "pessimistic").lower()
OPTIMISTIC_RETRY_LIMIT = int(os.getenv("OPTIMISTIC_RETRY_LIMIT", "5"))
RESERVATION_TTL_SECONDS = int(os.getenv("RESERVATION_TTL_SECONDS", "300"))
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "4"))
DB_POOL_TIMEOUT_SECONDS = float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "5"))

DB_HOST = os.getenv("DB_HOST", "")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_SCHEMA = os.getenv("DB_SCHEMA", "product_service")


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
