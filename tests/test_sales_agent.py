import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from sales_agent import classify_intent, run_sql


class SalesAgentRoutingTests(unittest.TestCase):
    def test_sales_summary(self):
        self.assertEqual(classify_intent("今天、昨天和本周销售额是多少"), "sales_summary")

    def test_top_products(self):
        self.assertEqual(classify_intent("最近哪些商品卖得最好"), "top_products")

    def test_product_change(self):
        self.assertEqual(classify_intent("哪些商品销量上升或下降"), "product_change")

    def test_category_contribution(self):
        self.assertEqual(classify_intent("哪些类别贡献最多"), "category_contribution")

    def test_kpi_change(self):
        self.assertEqual(classify_intent("客单价和订单量如何变化"), "kpi_change")

    @patch("sales_agent.subprocess.run")
    def test_sql_variables_use_psql_stdin_not_command_text(self, subprocess_run):
        subprocess_run.return_value = SimpleNamespace(stdout="ok\n")
        rows = run_sql("db", "SELECT :'value';", {"value": "饮料' OR 1=1 --"})
        command = subprocess_run.call_args.args[0]
        kwargs = subprocess_run.call_args.kwargs
        self.assertNotIn("-c", command)
        self.assertIn("value=饮料' OR 1=1 --", command)
        self.assertEqual(kwargs["input"], "SELECT :'value';")
        self.assertEqual(rows, [["ok"]])


if __name__ == "__main__":
    unittest.main()
