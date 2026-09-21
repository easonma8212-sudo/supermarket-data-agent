import unittest
import json
from unittest.mock import patch
import sys
from pathlib import Path
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from business_review import period_parameters, render_review, rate, run_business_review
from sales_agent import Bounds
from sales_tools import ToolInputError

B = Bounds('2026-06-29','2026-09-20 13:08:06','2026-09-20','2026-09-19')

class BusinessReviewTests(unittest.TestCase):
    def test_visual_report_preserves_evidence_and_guards_invalid_data(self):
        for invalid in (0, 1):
            summary = [dict(period='current', amount=Decimal('120.10'), receipts=10, days=6, invalid=invalid),
                       dict(period='previous', amount=Decimal('100.00'), receipts=10, days=7, invalid=0)]
            rows = [dict(id='1', name='<商品>', current=Decimal('120.10'), previous=Decimal('100.00'))]
            with patch('business_review.get_bounds', return_value=B), patch('business_review._query', side_effect=[summary, rows, rows]):
                result = run_business_review('test', start_date='2026-09-13', end_date='2026-09-19')
            json.dumps(result)
            if invalid:
                self.assertIsNone(result['report'])
            else:
                self.assertEqual(result['report']['summary'][0]['amount'], '120.10')
                self.assertEqual(result['report']['breakdowns']['商品'][0]['name'], '<商品>')
                self.assertIn('6/7', result['report']['warnings'][0])

    def test_period_boundary(self):
        p = period_parameters('2026-09-13','2026-09-19',B)
        self.assertEqual(p['previous_start'],'2026-09-06')
        self.assertEqual(p['previous_end'],'2026-09-12')
        with self.assertRaises(ToolInputError):
            period_parameters('2026-09-14','2026-09-20',B)
        with self.assertRaises(ToolInputError):
            period_parameters('2026-06-29','2026-07-05',B)

    def test_zero_baseline(self):
        self.assertIsNone(rate(Decimal(5),Decimal(0)))

    def test_reconcile_and_missing_records(self):
        p = period_parameters('2026-09-13','2026-09-19',B)
        summary = [{'period':'current','amount':120,'receipts':10,'days':6,'invalid':0},
                   {'period':'previous','amount':100,'receipts':10,'days':7,'invalid':0}]
        breakdown = {k:[{'id':'1','name':'测试','current':120,'previous':100}] for k in ('类别','商品')}
        report = render_review(summary,breakdown,p,B)
        self.assertIn('6/7',report)
        self.assertIn('+20.0%',report)
        breakdown['商品'][0]['current'] = 119
        with self.assertRaises(ToolInputError):
            render_review(summary,breakdown,p,B)
        summary[0]['invalid'] = 1
        self.assertIn('数据检查未通过',render_review(summary,breakdown,p,B))
