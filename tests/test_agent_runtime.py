import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from agent_runtime import answer_with_ai, render_tool_result
from sales_agent import Bounds


BOUNDS = Bounds(
    earliest="2026-06-29",
    latest_timestamp="2026-09-20 13:08:06",
    latest_date="2026-09-20",
    latest_complete_date="2026-09-19",
)


class FakePlanner:
    def __init__(self, plan):
        self.plan_result = plan

    def plan(self, question, bounds, conversation_context=None):
        return self.plan_result


class AgentRuntimeTests(unittest.TestCase):
    @patch("agent_runtime.invoke_tool")
    @patch("agent_runtime.get_bounds", return_value=BOUNDS)
    def test_execute_plan_is_validated_and_rendered_locally(self, _, mock_invoke):
        arguments = {
            "start_date": "2026-09-01",
            "end_date": "2026-09-07",
            "allow_partial_end_date": False,
        }
        mock_invoke.return_value = {
            "tool": "query_sales_summary",
            "parameters": arguments,
            "rows": [{
                "sales_amount": "123.40",
                "sales_quantity": "20",
                "receipt_count": 8,
                "average_receipt_amount": "15.43",
            }],
        }
        planner = FakePlanner({
            "decision": "execute",
            "tool": "query_sales_summary",
            "arguments": arguments,
        })
        reply = answer_with_ai("查销售额", planner=planner)
        self.assertTrue(reply.tool_executed)
        self.assertIn("123.40 元", reply.answer)
        self.assertIn("8 张", reply.answer)
        mock_invoke.assert_called_once_with(
            "query_sales_summary", arguments, "supermarket_agent"
        )

    @patch("agent_runtime.invoke_tool")
    @patch("agent_runtime.get_bounds", return_value=BOUNDS)
    def test_clarification_never_executes_a_tool(self, _, mock_invoke):
        planner = FakePlanner({
            "decision": "clarify",
            "tool": "request_clarification",
            "arguments": {"question": "想比较哪个指标？", "reason": "缺少指标"},
        })
        reply = answer_with_ai("和以前比怎么样", planner=planner)
        self.assertFalse(reply.tool_executed)
        self.assertEqual(reply.answer, "想比较哪个指标？")
        mock_invoke.assert_not_called()

    @patch("agent_runtime.invoke_tool")
    @patch("agent_runtime.get_bounds", return_value=BOUNDS)
    def test_refusal_never_executes_a_tool(self, _, mock_invoke):
        planner = FakePlanner({
            "decision": "refuse",
            "tool": "refuse_request",
            "arguments": {"message": "不支持利润", "reason": "没有利润数据"},
        })
        reply = answer_with_ai("利润是多少", planner=planner)
        self.assertFalse(reply.tool_executed)
        self.assertIn("不会执行查询", reply.answer)
        mock_invoke.assert_not_called()

    def test_top_products_are_rendered_without_model_rewrite(self):
        answer = render_tool_result({
            "tool": "query_top_products",
            "parameters": {
                "start_date": "2026-09-13",
                "end_date": "2026-09-19",
                "limit": 1,
                "sort_by": "sales_quantity",
                "category": None,
            },
            "rows": [{
                "item_no": "00110",
                "product_name": "鸡蛋",
                "category": "鸡鱼肉蛋",
                "sales_quantity": "90.45",
                "sales_amount": "527.10",
                "receipt_count": 54,
            }],
        }, BOUNDS)
        self.assertIn("按销量", answer)
        self.assertIn("鸡蛋", answer)
        self.assertIn("527.10 元", answer)

    def test_metric_result_is_dispatched_before_legacy_rows_shape(self):
        answer = render_tool_result({
            "tool": "query_metrics",
            "parameters": {
                "metrics": ["sales_amount"],
                "dimension": None,
                "filters": {"product_query": "鸡蛋", "category": None},
                "resolved_product": {
                    "item_no": "00110",
                    "product_name": "鸡蛋",
                    "category": "鸡鱼肉蛋",
                },
            },
            "metric_labels": {"sales_amount": "销售额"},
            "periods": [{
                "label": "current",
                "start_date": "2026-09-13",
                "end_date": "2026-09-19",
                "rows": [{"sales_amount": "527.10"}],
            }],
        }, BOUNDS)
        self.assertIn("NL2Metrics 查询结果", answer)
        self.assertIn("鸡蛋（00110）", answer)
        self.assertIn("527.10 元", answer)

    @patch("agent_runtime.execute_metric_query")
    @patch("agent_runtime.get_bounds", return_value=BOUNDS)
    def test_first_candidate_continues_pending_metric_plan_without_llm(
        self, _, execute_metric_query
    ):
        arguments = {
            "metrics": ["sales_amount"],
            "dimension": None,
            "filters": {"product_query": "牛奶", "category": None},
            "start_date": "2026-09-13",
            "end_date": "2026-09-19",
            "allow_partial_end_date": False,
            "comparison": "none",
            "order_by": None,
            "order_direction": "desc",
            "limit": 1,
        }
        execute_metric_query.return_value = {
            "tool": "query_metrics",
            "parameters": {
                **arguments,
                "filters": {"product_query": "1001", "category": None},
                "resolved_product": {
                    "item_no": "1001", "product_name": "测试牛奶", "category": "饮料"
                },
            },
            "metric_labels": {"sales_amount": "销售额"},
            "periods": [{
                "label": "current",
                "start_date": "2026-09-13",
                "end_date": "2026-09-19",
                "rows": [{"sales_amount": "88.00"}],
            }],
        }
        context = {
            "pending_entity": {
                "tool": "query_metrics",
                "arguments": arguments,
                "candidates": [
                    {"item_no": "1001", "product_name": "测试牛奶", "category": "饮料"},
                    {"item_no": "1002", "product_name": "另一牛奶", "category": "饮料"},
                ],
            }
        }
        reply = answer_with_ai("第一个", conversation_context=context)
        self.assertTrue(reply.tool_executed)
        actual_arguments = execute_metric_query.call_args.kwargs
        self.assertEqual(actual_arguments["filters"]["product_query"], "1001")
        self.assertIn("测试牛奶（1001）", reply.answer)


if __name__ == "__main__":
    unittest.main()
