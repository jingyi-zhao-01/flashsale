import unittest

from flashsale_shared.postgres_schema import with_search_path


class WithSearchPathTest(unittest.TestCase):
    def test_appends_options_for_empty_query(self) -> None:
        url = "postgresql://user:pass@localhost:5432/flashsale"
        rendered = with_search_path(url, "user_service")

        self.assertIn("options=-csearch_path%3Duser_service%2Cpublic", rendered)

    def test_preserves_existing_query_params(self) -> None:
        url = "postgresql://user:pass@localhost:5432/flashsale?sslmode=require"
        rendered = with_search_path(url, "product_service")

        self.assertIn("sslmode=require", rendered)
        self.assertIn("options=-csearch_path%3Dproduct_service%2Cpublic", rendered)

    def test_appends_to_existing_options(self) -> None:
        url = "postgresql://user:pass@localhost:5432/flashsale?options=-cstatement_timeout%3D5000"
        rendered = with_search_path(url, "order_service")

        self.assertIn("statement_timeout", rendered)
        self.assertIn("search_path%3Dorder_service%2Cpublic", rendered)


if __name__ == "__main__":
    unittest.main()
