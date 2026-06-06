from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def ensure_schema(cur: Any, schema_name: str) -> None:
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{_validate_schema_name(schema_name)}"')


def set_search_path(cur: Any, schema_name: str) -> None:
    validated = _validate_schema_name(schema_name)
    cur.execute(f'SET search_path TO "{validated}", public')


def with_search_path(database_url: str, schema_name: str) -> str:
    if not database_url:
        return database_url

    validated = _validate_schema_name(schema_name)
    parts = urlsplit(database_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    options_value = f"-csearch_path={validated},public"
    updated: list[tuple[str, str]] = []
    options_seen = False

    for key, value in query:
        if key != "options":
            updated.append((key, value))
            continue
        options_seen = True
        extra = options_value if not value else f"{value} {options_value}"
        updated.append((key, extra))

    if not options_seen:
        updated.append(("options", options_value))

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(updated, doseq=True),
            parts.fragment,
        )
    )


def _validate_schema_name(schema_name: str) -> str:
    if not schema_name:
        raise ValueError("schema_name must not be empty")
    if not schema_name[0].isalpha() and schema_name[0] != "_":
        raise ValueError("schema_name must start with a letter or underscore")
    if not all(char.isalnum() or char == "_" for char in schema_name):
        raise ValueError("schema_name must contain only letters, digits, or underscores")
    return schema_name
