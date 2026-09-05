import unittest
from institutional_stock_report import build_report, fundamental_summary

class InstitutionalReportTests(unittest.TestCase):
    def test_missing_fundamentals_are_explicit(self):
        f = fundamental_summary(None)
        self.assertIsNone(f["score"])
        self.assertEqual(f["status"], "VERI YOK")

    def test_strong_technical_does_not_fake_fundamental_verdict(self):
        c = {"symbol":"TEST","price":10,"tier":"A","priority":"YUKSEK","reason":"iki model", "models":{}, "structure":{"trend":"YUKSELEN YAPI","bos":"YUKARI BOS","last_swing_high":11,"last_swing_low":9}, "elliott":{"phase":"ITKI ADAYI","confidence":"ORTA"}}
        r = build_report(c, None)
        self.assertIn("TEMEL VERI BEKLENIYOR", r["executive_view"]["title"])
        self.assertIsNone(r["fundamental"]["score"])

if __name__ == "__main__": unittest.main()
