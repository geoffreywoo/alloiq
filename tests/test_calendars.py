from datetime import date
from pathlib import Path
import unittest

from invest.calendars import build_13f_calendar, build_calendar_snapshot, quarter_payload
from invest.config import AppConfig


class CalendarTests(unittest.TestCase):
    def test_13f_deadline_handles_weekends_and_holidays(self):
        q2 = quarter_payload(date(2026, 6, 30))
        q4_2025 = quarter_payload(date(2025, 12, 31))

        self.assertEqual(q2["deadline"], "2026-08-14")
        self.assertEqual(q4_2025["deadline"], "2026-02-17")

    def test_manager_filing_calendar_marks_pending_and_late(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={"managers": [{"key": "m1", "name": "Manager One", "cik": "1"}]},
        )

        calendar = build_13f_calendar(config, date(2026, 8, 20), {"manager_status": []})

        self.assertEqual(calendar["current_cycle"]["quarter_end"], "2026-06-30")
        self.assertEqual(calendar["managers"][0]["status"], "late")

    def test_calendar_snapshot_enriches_earnings_fields(self):
        config = AppConfig(path=Path("config/invest.toml"), data={"managers": []})
        snapshot = build_calendar_snapshot(
            config,
            date(2026, 5, 24),
            {},
            [{"symbol": "NVDA", "event_date": "2026-05-27", "source": "manual", "days_until": 3}],
        )

        event = snapshot["earnings"]["events"][0]
        self.assertEqual(event["confidence"], 1.0)
        self.assertEqual(event["risk_window"], "risk_window")
        self.assertEqual(snapshot["filings_13f"]["rule_source"], "https://www.sec.gov/divisions/investment/13ffaq.htm")


if __name__ == "__main__":
    unittest.main()
