import json
import os
import sys
import tempfile
import unittest
from http.client import RemoteDisconnected
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from ai_planner import (
    AIPlannerError,
    INSTRUCTIONS,
    OpenAIShadowPlanner,
    build_request_payload,
    load_local_environment,
    parse_planner_response,
)
from sales_agent import Bounds


BOUNDS = Bounds(
    earliest="2026-06-29",
    latest_timestamp="2026-09-20 13:08:06",
    latest_date="2026-09-20",
    latest_complete_date="2026-09-19",
)


class AIPlannerTests(unittest.TestCase):
    def test_request_is_stateless_and_requires_one_tool(self):
        payload = build_request_payload("最近30天销售额", "test-model", BOUNDS)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["tool_choice"], "required")
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertEqual(len(payload["tools"]), 9)
        self.assertIn("2026-09-19", payload["input"])

    def test_conversation_context_is_sent_without_query_results(self):
        context = {
            "last_user_question": "鸡蛋最近怎么样",
            "last_plan": {
                "tool": "query_metrics",
                "arguments": {"filters": {"product_query": "鸡蛋"}},
            },
        }
        payload = build_request_payload("改成最近7天", "test-model", BOUNDS, context)
        decoded = json.loads(payload["input"])
        self.assertEqual(decoded["conversation_context"], context)
        self.assertNotIn("rows", payload["input"])

    def test_comparison_policy_keeps_latest_complete_week_and_clarifies_generic_request(self):
        self.assertIn("执行前硬门禁", INSTRUCTIONS)
        self.assertIn("普通的“卖得怎么样”", INSTRUCTIONS)
        self.assertIn("不要整体向前移动一周", INSTRUCTIONS)
        self.assertIn("同时缺少比较指标和期间的问题必须澄清", INSTRUCTIONS)

    def test_execute_decision_is_parsed(self):
        response = {
            "id": "resp_1",
            "model": "test-model",
            "output": [{
                "type": "function_call",
                "name": "query_sales_summary",
                "arguments": json.dumps({
                    "start_date": "2026-08-21",
                    "end_date": "2026-09-19",
                    "allow_partial_end_date": False,
                }),
            }],
        }
        plan = parse_planner_response(response)
        self.assertEqual(plan["decision"], "execute")
        self.assertEqual(plan["tool"], "query_sales_summary")

    def test_clarification_decision_is_parsed(self):
        response = {
            "output": [{
                "type": "function_call",
                "name": "request_clarification",
                "arguments": json.dumps({"question": "想和哪个期间比较？", "reason": "期间不明确"}),
            }],
        }
        self.assertEqual(parse_planner_response(response)["decision"], "clarify")

    def test_unknown_tool_is_rejected(self):
        response = {
            "output": [{"type": "function_call", "name": "run_sql", "arguments": "{}"}],
        }
        with self.assertRaisesRegex(AIPlannerError, "未知工具"):
            parse_planner_response(response)

    def test_missing_configuration_is_rejected_before_network(self):
        with self.assertRaisesRegex(AIPlannerError, "OPENAI_API_KEY"):
            OpenAIShadowPlanner("", "test-model")

    def test_local_env_loader_only_reads_whitelisted_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "OPENAI_API_KEY='local-test-key'\nUNSAFE_KEY=ignored\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_local_environment(path)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "local-test-key")
                self.assertNotIn("UNSAFE_KEY", os.environ)

    @patch("ai_planner.time.sleep")
    @patch("ai_planner.urlopen")
    def test_transient_disconnect_is_retried(self, mock_urlopen, mock_sleep):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "output": [{
                "type": "function_call",
                "name": "request_clarification",
                "arguments": json.dumps({"question": "比较什么？", "reason": "缺少指标"}),
            }],
        }).encode("utf-8")
        mock_urlopen.side_effect = [RemoteDisconnected(), response]
        plan = OpenAIShadowPlanner("test-key", "test-model").plan("对比一下", BOUNDS)
        self.assertEqual(plan["decision"], "clarify")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
