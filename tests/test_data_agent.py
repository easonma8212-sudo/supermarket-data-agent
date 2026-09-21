import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))
from data_agent import AgentError, Principal, decompose_change, query_metric, run_demo, synthetic_orders


class DataAgentTests(unittest.TestCase):
    def setUp(self):
        self.orders = synthetic_orders()
        self.manager = Principal("manager", frozenset({"DE", "US"}))

    def test_certified_metric_and_reconciliation(self):
        result = run_demo()
        self.assertEqual(result["previous"]["value"], 300.0)
        self.assertEqual(result["current"]["value"], 290.0)
        self.assertEqual(result["decomposition"]["delta_usd"], -10.0)
        self.assertEqual(result["decomposition"]["contributions_usd"], {"DE": -20.0, "US": 10.0})
        self.assertEqual(sum(result["decomposition"]["contributions_usd"].values()), -10.0)

    def test_market_permission_is_enforced_before_query(self):
        with self.assertRaises(PermissionError):
            query_metric(self.orders, Principal("de_only", frozenset({"DE"})),
                         "gmv_demo_usd", date(2026, 9, 15), {"DE", "US"}, date(2026, 9, 16))

    def test_unknown_metric_is_rejected(self):
        with self.assertRaises(AgentError):
            query_metric(self.orders, self.manager, "raw_sql", date(2026, 9, 15),
                         {"DE"}, date(2026, 9, 16))

    def test_unfinished_day_is_rejected(self):
        with self.assertRaises(AgentError):
            query_metric(self.orders, self.manager, "gmv_demo_usd", date(2026, 9, 16),
                         {"DE"}, date(2026, 9, 16))

    def test_different_filters_cannot_be_compared(self):
        a = query_metric(self.orders, self.manager, "gmv_demo_usd", date(2026, 9, 14),
                         {"DE"}, date(2026, 9, 16))
        b = query_metric(self.orders, self.manager, "gmv_demo_usd", date(2026, 9, 15),
                         {"DE", "US"}, date(2026, 9, 16))
        with self.assertRaises(AgentError):
            decompose_change(a, b)


if __name__ == "__main__":
    unittest.main()
