"""Shared stubs for user-service unit tests.

Import this module before importing any app code that depends on psycopg
or email_validator to avoid pulling in the real database driver.

Usage::

    from tests.stubs import install_stubs
    install_stubs()

    from app.main import app  # safe — stubs are in place
"""

from __future__ import annotations

import sys
import types


class FakeUniqueViolation(Exception):
    """Stand-in for psycopg.errors.UniqueViolation."""


def _validate_email(value: str, *_args: object, **_kwargs: object) -> object:
    return types.SimpleNamespace(
        email=value,
        normalized=value,
        local_part=value.split("@")[0] if "@" in value else value,
    )


class EmailNotValidError(ValueError):
    """Stand-in for email_validator.EmailNotValidError."""


def install_stubs() -> None:
    """Install stubs for psycopg and email_validator into sys.modules."""

    _psycopg = types.ModuleType("psycopg")
    _psycopg_rows = types.ModuleType("psycopg.rows")
    _psycopg_errors = types.ModuleType("psycopg.errors")
    _psycopg.connect = None
    _psycopg_rows.dict_row = object()

    _psycopg_errors.UniqueViolation = FakeUniqueViolation  # type: ignore[attr-defined]
    _psycopg_errors.IntegrityError = FakeUniqueViolation  # type: ignore[attr-defined]

    sys.modules["psycopg"] = _psycopg
    sys.modules["psycopg.rows"] = _psycopg_rows
    sys.modules["psycopg.errors"] = _psycopg_errors
    _psycopg.errors = _psycopg_errors  # type: ignore[attr-defined]

    _email_validator = types.ModuleType("email_validator")
    _email_validator.EmailNotValidError = EmailNotValidError  # type: ignore[attr-defined]
    _email_validator.validate_email = _validate_email  # type: ignore[attr-defined]
    _email_validator.__version__ = "2.0.0"  # type: ignore[attr-defined]

    sys.modules["email_validator"] = _email_validator

    import importlib.metadata as _md

    _original = _md.distribution

    def _patch(name: str) -> object:
        if name == "email-validator":
            return types.SimpleNamespace(version="2.0.0")
        return _original(name)

    _md.distribution = _patch  # type: ignore[assignment]
