import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from metric_query import (
    EntityClarification,
    METRIC_TOOL_SCHEMA,
    MetricPlanError,
    execute_metric_query,
    resolve_product,
)
from sales_agent import Bounds


BOUNDS = Bounds(
    earliest="2026-06-29",
    latest_timestamp="2026-09-20 13:08:06",
    latest_date="2026-09-20",
    latest_complete_date="2026-09-19",
)


def egg_plan(**overrides):
    plan = {
        "metrics": ["sales_amount", "sales_quantity", "receipt_count"],
        "dimension": None,
        "filters": {"product_query": "鸡蛋", "category": None},
        "start_date": "2026-08-21",
        "end_date": "2026-09-19",
        "allow_partial_end_date": False,
        "comparison": "none",
        "order_by": None,
        "order_direction": "desc",
        "limit": 1,
    }
    plan.update(overrides)
    return plan


class MetricQueryTests(unittest.TestCase):
    def test_schema_is_strict_including_filters(self):
        parameters = METRIC_TOOL_SCHEMA["parameters"]
        self.assertTrue(METRIC_TOOL_SCHEMA["strict"])
        self.assertFalse(parameters["additionalProperties"])
        self.assertFalse(parameters["properties"]["filters"]["additionalProperties"])
        self.assertEqual(set(parameters["required"]), set(parameters["properties"]))

    @patch("metric_query.get_bounds", return_value=BOUNDS)
    @patch("metric_query.run_sql")
    def test_specific_product_is_resolved_then_queried_by_item_number(self, run_sql, _):
        run_sql.side_effect = [
            [["00110", "鸡蛋", "鸡鱼肉蛋"]],
            [["1645.90", "290.99", "127"]],
        ]
        result = execute_metric_query("db", **egg_plan())
        self.assertEqual(result["parameters"]["resolved_product"]["item_no"], "00110")
        self.assertEqual(result["periods"][0]["rows"][0]["receipt_count"], 127)
        metric_sql = run_sql.call_args_list[1].args[1]
        metric_variables = run_sql.call_args_list[1].args[2]
        self.assertNotIn("鸡蛋", metric_sql)
        self.assertEqual(metric_variables["item_no"], "00110")

    @patch("metric_query.run_sql")
    def test_ambiguous_product_requests_clarification(self, run_sql):
        run_sql.return_value = [
            ["1", "旺仔牛奶125ml", "饮料"],
            ["2", "旺仔牛奶245ml", "饮料"],
        ]
        with self.assertRaisesRegex(EntityClarification, "匹配到多个商品"):
            resolve_product("db", "旺仔牛奶")

    @patch("metric_query.get_bounds", return_value=BOUNDS)
    def test_unknown_metric_is_rejected_before_sql(self, _):
        with self.assertRaisesRegex(MetricPlanError, "未认证指标"):
            execute_metric_query("db", **egg_plan(metrics=["profit"]))

    @patch("metric_query.get_bounds", return_value=BOUNDS)
    @patch("metric_query.run_sql")
    def test_daily_series_expands_limit_to_complete_period(self, run_sql, _):
        run_sql.side_effect = [
            [["00110", "鸡蛋", "鸡鱼肉蛋"]],
            [["2026-09-13", "15.73"], ["2026-09-14", "12.00"]],
        ]
        result = execute_metric_query(
            "db",
            **egg_plan(
                metrics=["sales_quantity"],
                dimension="day",
                start_date="2026-09-13",
                end_date="2026-09-19",
                limit=1,
            ),
        )
        self.assertEqual(result["parameters"]["limit"], 7)
        self.assertEqual(run_sql.call_args_list[1].args[2]["limit"], 7)

    @patch("metric_query.get_bounds", return_value=BOUNDS)
    @patch("metric_query.run_sql")
    def test_previous_period_is_adjacent_and_equal_length(self, run_sql, _):
        run_sql.side_effect = [
            [["00110", "鸡蛋", "鸡鱼肉蛋"]],
            [["100", "20", "10"]],
            [["80", "16", "8"]],
        ]
        result = execute_metric_query(
            "db",
            **egg_plan(
                start_date="2026-09-13",
                end_date="2026-09-19",
                comparison="previous_period",
            ),
        )
        self.assertEqual(result["periods"][1]["start_date"], "2026-09-06")
        self.assertEqual(result["periods"][1]["end_date"], "2026-09-12")


if __name__ == "__main__":
    unittest.main()
