import logging

import psycopg
import psycopg.errors  # noqa: F401  # ensure errors submodule is loaded
from fastapi import HTTPException

from .config import DATABASE_UNAVAILABLE_MESSAGE
from .models import UserCreate, UserOut
from .repositories import UserRepository


class UserService:
    def __init__(
        self, repository: UserRepository, logger: logging.Logger, storage: str
    ) -> None:
        self._repository = repository
        self._logger = logger
        self._storage = storage

    def init_db(self) -> None:
        try:
            self._repository.init_db()
        except Exception:
            self._logger.exception("event=user_init_db_error storage=%s", self._storage)
            raise

    def create_user(self, payload: UserCreate) -> UserOut:
        try:
            user = self._repository.create_user(payload)
            self._logger.info(
                "event=user_created user_id=%s email=%s storage=%s",
                user.id,
                user.email,
                self._storage,
            )
            return user
        except psycopg.errors.UniqueViolation as exc:
            self._logger.warning("event=user_create_conflict email=%s", payload.email)
            raise HTTPException(
                status_code=409, detail="user email already exists"
            ) from exc
        except HTTPException:
            raise
        except RuntimeError:
            self._logger.error(
                "event=user_create_failed reason=persistence_empty email=%s storage=%s",
                payload.email,
                self._storage,
            )
            raise HTTPException(status_code=503, detail="user persistence failed")
        except Exception as exc:
            self._logger.exception("event=user_create_error storage=%s", self._storage)
            raise HTTPException(
                status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE
            ) from exc

    def get_user(self, user_id: int) -> UserOut:
        try:
            user = self._repository.get_user(user_id)
            if not user:
                self._logger.info(
                    "event=user_not_found user_id=%s storage=%s", user_id, self._storage
                )
                raise HTTPException(status_code=404, detail="user not found")
            return user
        except HTTPException:
            raise
        except Exception as exc:
            self._logger.exception(
                "event=user_get_error storage=%s user_id=%s", self._storage, user_id
            )
            raise HTTPException(
                status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE
            ) from exc

    def list_users(self) -> list[UserOut]:
        try:
            users = self._repository.list_users()
            self._logger.info(
                "event=user_list count=%s storage=%s", len(users), self._storage
            )
            return users
        except Exception as exc:
            self._logger.exception("event=user_list_error storage=%s", self._storage)
            raise HTTPException(
                status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE
            ) from exc
