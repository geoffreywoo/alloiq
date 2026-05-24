from datetime import date
from pathlib import Path
import unittest

from invest.config import AppConfig
from invest.earnings import build_earnings_events


class EarningsTests(unittest.TestCase):
    def test_manual_and_news_events_are_normalized(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "earnings": {
                    "events": [
                        {
                            "symbol": "NVDA",
                            "event_date": "2026-05-27",
                            "fiscal_period": "FY2027 Q1",
                            "status": "confirmed",
                            "source": "manual",
                        }
                    ]
                }
            },
        )

        events = build_earnings_events(
            config,
            ["NVDA", "GOOGL"],
            date(2026, 5, 24),
            {"GOOGL": {"event_types": ["earnings_revision", "capex_signal"]}},
        )

        nvda = next(row for row in events if row["symbol"] == "NVDA")
        googl = next(row for row in events if row["symbol"] == "GOOGL")
        self.assertEqual(nvda["days_until"], 3)
        self.assertEqual(nvda["event_type"], "earnings")
        self.assertEqual(googl["event_type"], "earnings_catalyst")
        self.assertIn("earnings_revision", googl["catalyst_types"])


if __name__ == "__main__":
    unittest.main()
