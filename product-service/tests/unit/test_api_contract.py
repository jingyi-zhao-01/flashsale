import sys
import types
import unittest

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_stub.connect = None
psycopg_rows_stub.dict_row = object()
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

from app.main import app


class ProductServiceApiContractTest(unittest.TestCase):
    def test_openapi_exposes_reservation_lifecycle_endpoints(self) -> None:
        spec = app.openapi()

        self.assertIn("/products/{product_id}/reserve", spec["paths"])
        self.assertIn("/reservations/{reservation_id}/confirm", spec["paths"])
        self.assertIn("/reservations/{reservation_id}/cancel", spec["paths"])
        self.assertIn("/admin/expire-reservations", spec["paths"])

        reserve_post = spec["paths"]["/products/{product_id}/reserve"]["post"]
        reserve_response = reserve_post["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(
            reserve_response["$ref"], "#/components/schemas/ReservationOut"
        )

        reserve_post = spec["paths"]["/products/{product_id}/reserve"]["post"]
        self.assertEqual(reserve_post["summary"], "Reserve product stock")
        self.assertIn("reservations", reserve_post["tags"])
        self.assertEqual(
            reserve_post["responses"]["409"]["content"]["application/json"]["schema"][
                "$ref"
            ],
            "#/components/schemas/ErrorResponse",
        )

    def test_product_create_schema_stays_backward_compatible(self) -> None:
        spec = app.openapi()
        create_schema = spec["components"]["schemas"]["ProductCreate"]

        self.assertEqual(
            set(create_schema["required"]),
            {"name", "price", "stock"},
        )
        self.assertIn("stock", create_schema["properties"])
        self.assertIn("description", create_schema["properties"]["stock"])
        self.assertIn(
            "description",
            spec["components"]["schemas"]["ReservationOut"]["properties"]["status"],
        )
        self.assertIn("ErrorResponse", spec["components"]["schemas"])
        self.assertIn("HealthResponse", spec["components"]["schemas"])


if __name__ == "__main__":
    unittest.main()
