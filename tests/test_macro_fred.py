from datetime import date
import unittest

from invest.macro import build_macro_dashboard
from invest.macro_fred import build_fred_macro_snapshot, parse_fred_csv


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.text.encode("utf-8")


def fake_fred_urlopen(req, timeout=5):
    url = req.full_url
    series_id = url.split("id=")[1].split("&")[0]
    fixtures = {
        "DGS10": "observation_date,DGS10\n2026-02-20,4.10\n2026-04-20,4.20\n2026-05-20,4.50\n",
        "DGS2": "observation_date,DGS2\n2026-02-20,4.40\n2026-04-20,4.55\n2026-05-20,4.70\n",
        "BAMLH0A0HYM2": "observation_date,BAMLH0A0HYM2\n2026-02-20,3.20\n2026-04-20,3.80\n2026-05-20,4.60\n",
        "NFCI": "observation_date,NFCI\n2026-02-20,-0.25\n2026-04-20,0.10\n2026-05-20,0.60\n",
        "WALCL": "observation_date,WALCL\n2026-02-20,6900000\n2026-04-20,6800000\n2026-05-20,6700000\n",
        "DCOILWTICO": "observation_date,DCOILWTICO\n2026-02-20,70\n2026-04-20,80\n2026-05-20,90\n",
        "DHHNGSP": "observation_date,DHHNGSP\n2026-02-20,3\n2026-04-20,4\n2026-05-20,5\n",
    }
    return FakeResponse(fixtures[series_id])


class FredMacroTests(unittest.TestCase):
    def test_parse_fred_csv_ignores_missing_values(self):
        rows = parse_fred_csv("observation_date,DGS10\n2026-05-19,.\n2026-05-20,4.50\n", "DGS10")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], date(2026, 5, 20))
        self.assertEqual(float(rows[0]["value"]), 4.5)

    def test_build_fred_macro_snapshot_scores_stress(self):
        snapshot = build_fred_macro_snapshot(as_of=date(2026, 5, 24), urlopen=fake_fred_urlopen)

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["series_count"], 7)
        self.assertLess(snapshot["scores"]["yield_curve_10y2y"], 0)
        self.assertGreater(snapshot["scores"]["credit_stress_score"], 5)
        self.assertIn("credit_spreads_widening", snapshot["regime_flags"])

    def test_macro_dashboard_uses_fred_stress_regime(self):
        snapshot = build_fred_macro_snapshot(as_of=date(2026, 5, 24), urlopen=fake_fred_urlopen)
        macro = build_macro_dashboard({}, snapshot)

        self.assertEqual(macro["regime"], "credit/liquidity stress")
        self.assertIn("credit_stress_score", macro["scores"])
        self.assertEqual(macro["fred_macro"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
