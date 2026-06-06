import unittest

from tests.stubs import install_stubs

install_stubs()

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
