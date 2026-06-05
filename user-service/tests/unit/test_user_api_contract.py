import sys
import types
import unittest

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_errors_stub = types.ModuleType("psycopg.errors")
email_validator_stub = types.ModuleType("email_validator")
psycopg_stub.connect = None


class FakeUniqueViolation(Exception):
    pass


psycopg_errors_stub.UniqueViolation = FakeUniqueViolation
psycopg_errors_stub.IntegrityError = FakeUniqueViolation
psycopg_rows_stub.dict_row = object()


class EmailNotValidError(ValueError):
    pass


def validate_email(value: str, *args: object, **kwargs: object) -> object:
    return types.SimpleNamespace(email=value, normalized=value, local_part=value.split("@")[0] if "@" in value else value)


email_validator_stub.EmailNotValidError = EmailNotValidError
email_validator_stub.validate_email = validate_email
email_validator_stub.__version__ = "2.0.0"

import importlib.metadata as _metadata

_original_distribution = _metadata.distribution


def _patched_distribution(distribution_name: str) -> object:
    if distribution_name == "email-validator":
        return types.SimpleNamespace(version="2.0.0")
    return _original_distribution(distribution_name)


sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)
sys.modules.setdefault("psycopg.errors", psycopg_errors_stub)
psycopg_stub.errors = psycopg_errors_stub
sys.modules.setdefault("email_validator", email_validator_stub)
_metadata.distribution = _patched_distribution

from app.main import app


class UserServiceApiContractTest(unittest.TestCase):
    def test_openapi_exposes_user_endpoints(self) -> None:
        spec = app.openapi()

        self.assertIn("/users", spec["paths"])
        self.assertIn("/users/{user_id}", spec["paths"])
        self.assertIn("/health", spec["paths"])
        self.assertIn("/ready", spec["paths"])
        self.assertIn("/live", spec["paths"])
        self.assertIn("/admin/reset", spec["paths"])

    def test_user_create_schema_requires_name_and_email(self) -> None:
        spec = app.openapi()
        create_schema = spec["components"]["schemas"]["UserCreate"]

        self.assertIn("name", create_schema["properties"])
        self.assertIn("email", create_schema["properties"])
        self.assertEqual(create_schema["required"], ["name", "email"])

    def test_user_out_schema_includes_id_name_email(self) -> None:
        spec = app.openapi()
        out_schema = spec["components"]["schemas"]["UserOut"]

        self.assertIn("id", out_schema["properties"])
        self.assertIn("name", out_schema["properties"])
        self.assertIn("email", out_schema["properties"])
        self.assertEqual(
            out_schema["properties"]["id"]["type"],
            "integer",
        )

    def test_health_response_schema(self) -> None:
        spec = app.openapi()
        health_schema = spec["components"]["schemas"]["HealthResponse"]

        self.assertIn("status", health_schema["properties"])
        self.assertIn("service", health_schema["properties"])

    def test_health_is_get_with_ok_response(self) -> None:
        spec = app.openapi()
        health_path = spec["paths"]["/health"]["get"]

        self.assertIn("200", health_path["responses"])

    def test_create_user_responses_document_409_and_503(self) -> None:
        spec = app.openapi()
        create_user = spec["paths"]["/users"]["post"]

        self.assertIn("409", create_user["responses"])
        self.assertEqual(
            create_user["responses"]["409"]["description"],
            "User email already exists",
        )
        self.assertIn("503", create_user["responses"])

    def test_get_user_responses_document_404_and_503(self) -> None:
        spec = app.openapi()
        get_user = spec["paths"]["/users/{user_id}"]["get"]

        self.assertIn("404", get_user["responses"])
        self.assertEqual(
            get_user["responses"]["404"]["description"],
            "User not found",
        )
        self.assertIn("503", get_user["responses"])


if __name__ == "__main__":
    unittest.main()
