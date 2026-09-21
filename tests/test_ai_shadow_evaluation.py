import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_ai_shadow import compare_plan, load_golden_set


class AIShadowEvaluationTests(unittest.TestCase):
    def test_golden_set_has_ten_unique_cases(self):
        spec = load_golden_set(ROOT / "evals" / "ai_shadow_golden_set.json")
        ids = [case["id"] for case in spec["cases"]]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(ids), len(set(ids)))

    def test_exact_execute_plan_passes(self):
        expected = {
            "decision": "execute",
            "tool": "query_top_products",
            "arguments": {"limit": 5},
        }
        actual = json.loads(json.dumps(expected))
        actual["model"] = "test-model"
        self.assertEqual(compare_plan(expected, actual), [])

    def test_argument_difference_fails(self):
        failures = compare_plan(
            {
                "decision": "execute",
                "tool": "query_top_products",
                "arguments": {"limit": 5},
            },
            {
                "decision": "execute",
                "tool": "query_top_products",
                "arguments": {"limit": 10},
            },
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("arguments expected", failures[0])


if __name__ == "__main__":
    unittest.main()
