import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from import_pos_exports import datetime_value, decimal_value, optional_date


class PosImportTests(unittest.TestCase):
    def test_datetime_accepts_export_and_screen_formats(self):
        self.assertEqual(datetime_value("2026/9/20 13:08:06"), "2026-09-20 13:08:06")
        self.assertEqual(datetime_value("2026-09-20 13:08:06"), "2026-09-20 13:08:06")

    def test_date_accepts_slash_and_dash(self):
        self.assertEqual(optional_date("2026/9/20"), "2026-09-20")
        self.assertEqual(optional_date("2026-09-20"), "2026-09-20")

    def test_percent_accepts_ascii_and_full_width_symbol(self):
        self.assertEqual(decimal_value("100.00%", percent=True), "1.00")
        self.assertEqual(decimal_value("100.00％", percent=True), "1.00")


if __name__ == "__main__":
    unittest.main()
