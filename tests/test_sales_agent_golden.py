import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from sales_agent import answer, classify_intent


class SalesAgentGoldenSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "evals" / "sales_agent_golden_set.json"
        cls.spec = json.loads(path.read_text(encoding="utf-8"))

    def test_golden_set_has_thirty_unique_cases(self):
        cases = self.spec["cases"]
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertEqual(len({case["question"] for case in cases}), len(cases))

    def test_every_question_routes_to_expected_intent(self):
        for case in self.spec["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    classify_intent(case["question"]), case["expected_intent"]
                )

    def test_unsupported_questions_do_not_query_database(self):
        response = answer("今天利润是多少？", database="database_that_does_not_exist")
        self.assertIn("超出首版经营问答范围", response)
        self.assertIn("我目前可以回答", response)


if __name__ == "__main__":
    unittest.main()
