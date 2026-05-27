from datetime import date
from decimal import Decimal
import unittest

from invest.market import build_price_audit, return_windows_for_history


class MarketTests(unittest.TestCase):
    def test_return_windows_include_last_date_for_price_audits(self):
        rows = [
            {"date": date(2026, 5, 18), "close": Decimal("100")},
            {"date": date(2026, 5, 19), "close": Decimal("110")},
            {"date": date(2026, 5, 20), "close": Decimal("120")},
            {"date": date(2026, 5, 21), "close": Decimal("130")},
            {"date": date(2026, 5, 22), "close": Decimal("140")},
            {"date": date(2026, 5, 26), "close": Decimal("150")},
        ]

        windows = return_windows_for_history(rows)

        self.assertEqual(windows["last"], Decimal("150"))
        self.assertEqual(windows["last_date"], "2026-05-26")
        self.assertEqual(round(float(windows["5d"]), 2), 50.0)

    def test_price_audit_accepts_consistent_live_quote_and_return_window(self):
        audit = build_price_audit(
            ["MU"],
            {"MU": {"last": Decimal("150"), "change_pct": Decimal("20")}},
            {"MU": {"last": Decimal("150"), "last_date": "2026-05-26", "1d": Decimal("20"), "5d": Decimal("50")}},
            focus_symbols=["MU"],
        )

        self.assertEqual(audit["status"], "ok")
        self.assertEqual(audit["checked_count"], 1)
        self.assertIn("MU last $150.00", audit["detail"])
        self.assertEqual(audit["rows"][0]["return_window_5d_pct"], 50.0)

    def test_price_audit_flags_stale_return_window_last_price(self):
        audit = build_price_audit(
            ["MU"],
            {"MU": {"last": Decimal("165"), "change_pct": Decimal("32")}},
            {"MU": {"last": Decimal("150"), "last_date": "2026-05-26", "1d": Decimal("20"), "5d": Decimal("50")}},
            focus_symbols=["MU"],
        )

        self.assertEqual(audit["status"], "stale")
        self.assertEqual(audit["stale_count"], 1)
        self.assertEqual(audit["issue_count"], 1)
        self.assertIn("live quote and return-window last differ", audit["issues"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
