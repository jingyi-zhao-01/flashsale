from itertools import count
from threading import Lock
from typing import Protocol

import psycopg
from psycopg.rows import dict_row

from .config import DB_POOL_MAX_SIZE, DB_POOL_MIN_SIZE, DB_POOL_TIMEOUT_SECONDS
from flashsale_shared.db_pool import DatabasePool
from .models import UserCreate, UserOut


class UserRepository(Protocol):
    def init_db(self) -> None: ...

    def is_healthy(self) -> bool: ...

    def reset_db(self) -> None: ...

    def create_user(self, payload: UserCreate) -> UserOut: ...

    def get_user(self, user_id: int) -> UserOut | None: ...

    def list_users(self) -> list[UserOut]: ...


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._users: dict[int, UserOut] = {}
        self._counter = count(1)
        self._lock = Lock()

    def init_db(self) -> None:
        return

    def is_healthy(self) -> bool:
        return True

    def reset_db(self) -> None:
        with self._lock:
            self._users.clear()
            self._counter = count(1)

    def create_user(self, payload: UserCreate) -> UserOut:
        with self._lock:
            user_id = next(self._counter)
            user = UserOut(id=user_id, name=payload.name, email=payload.email)
            self._users[user_id] = user
            return user

    def get_user(self, user_id: int) -> UserOut | None:
        return self._users.get(user_id)

    def list_users(self) -> list[UserOut]:
        return list(self._users.values())


class PostgresUserRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool = DatabasePool(
            database_url=database_url,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout_seconds=DB_POOL_TIMEOUT_SECONDS,
        )

    def init_db(self) -> None:
        with psycopg.connect(self._database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id BIGSERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        email TEXT NOT NULL UNIQUE
                    )
                    """)

    def is_healthy(self) -> bool:
        with psycopg.connect(self._database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True

    def reset_db(self) -> None:
        with psycopg.connect(self._database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE")

    def create_user(self, payload: UserCreate) -> UserOut:
        with self._pool.connection(autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (name, email)
                    VALUES (%s, %s)
                    RETURNING id, name, email
                    """,
                    (payload.name, payload.email),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("user persistence failed")
                return UserOut(
                    id=int(row["id"]),
                    name=str(row["name"]),
                    email=str(row["email"]),
                )

    def get_user(self, user_id: int) -> UserOut | None:
        with self._pool.connection(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, email
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return UserOut(
                    id=int(row["id"]),
                    name=str(row["name"]),
                    email=str(row["email"]),
                )

    def list_users(self) -> list[UserOut]:
        with self._pool.connection(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, email
                    FROM users
                    ORDER BY id DESC
                    """)
                rows = cur.fetchall()
                return [
                    UserOut(
                        id=int(row["id"]),
                        name=str(row["name"]),
                        email=str(row["email"]),
                    )
                    for row in rows
                ]
