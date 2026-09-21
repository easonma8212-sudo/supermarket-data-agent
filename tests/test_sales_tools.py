import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from sales_agent import Bounds
from sales_tools import (
    TOOL_SCHEMAS,
    ToolInputError,
    invoke_tool,
    query_product_sales_change,
    query_top_products,
)


BOUNDS = Bounds(
    earliest="2026-06-29",
    latest_timestamp="2026-09-20 13:08:06",
    latest_date="2026-09-20",
    latest_complete_date="2026-09-19",
)


class SalesToolsTests(unittest.TestCase):
    def test_all_tool_schemas_are_strict_closed_objects(self):
        self.assertEqual(len(TOOL_SCHEMAS), 5)
        for schema in TOOL_SCHEMAS:
            with self.subTest(tool=schema["name"]):
                self.assertTrue(schema["strict"])
                parameters = schema["parameters"]
                self.assertFalse(parameters["additionalProperties"])
                self.assertEqual(
                    set(parameters["required"]), set(parameters["properties"])
                )

    @patch("sales_tools.run_sql")
    @patch("sales_tools.get_bounds", return_value=BOUNDS)
    def test_category_is_passed_as_psql_variable(self, _get_bounds, run_sql):
        run_sql.return_value = [["1", "测试商品", "饮料", "2", "10", "1"]]
        category = "饮料' OR 1=1 --"
        result = query_top_products(
            "db",
            start_date="2026-09-13",
            end_date="2026-09-19",
            limit=5,
            sort_by="sales_amount",
            category=category,
        )
        sql = run_sql.call_args.args[1]
        variables = run_sql.call_args.args[2]
        self.assertNotIn(category, sql)
        self.assertEqual(variables["category"], category)
        self.assertEqual(result["parameters"]["category"], category)

    @patch("sales_tools.get_bounds", return_value=BOUNDS)
    def test_future_complete_date_is_rejected(self, _get_bounds):
        with self.assertRaisesRegex(ToolInputError, "最新完整日"):
            query_top_products(
                "db",
                start_date="2026-09-13",
                end_date="2026-09-20",
            )

    @patch("sales_tools.get_bounds", return_value=BOUNDS)
    def test_invalid_limit_is_rejected(self, _get_bounds):
        with self.assertRaisesRegex(ToolInputError, "1 至 50"):
            query_top_products(
                "db",
                start_date="2026-09-13",
                end_date="2026-09-19",
                limit=100,
            )

    @patch("sales_tools.get_bounds", return_value=BOUNDS)
    def test_overlapping_comparison_periods_are_rejected(self, _get_bounds):
        with self.assertRaisesRegex(ToolInputError, "必须早于"):
            query_product_sales_change(
                "db",
                current_start_date="2026-09-13",
                current_end_date="2026-09-19",
                previous_start_date="2026-09-10",
                previous_end_date="2026-09-13",
            )

    def test_unknown_tool_is_rejected(self):
        with self.assertRaisesRegex(ToolInputError, "未知工具"):
            invoke_tool("run_sql", {})


if __name__ == "__main__":
    unittest.main()
