from datetime import date
from pathlib import Path
import unittest
from unittest.mock import patch

from invest.config import AppConfig
from invest.earnings import (
    build_earnings_events,
    dedupe_events,
    event_payload,
    ir_feed_item_event,
    parse_alpha_vantage_calendar,
    parse_ir_feed_items,
    parse_nasdaq_calendar_rows,
    nasdaq_earnings_events,
)


class EarningsTests(unittest.TestCase):
    def test_manual_and_news_events_are_normalized(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "earnings": {
                    "providers": {"nasdaq_enabled": False, "alpha_vantage_enabled": False},
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

    def test_alpha_vantage_calendar_csv_creates_estimated_dates(self):
        text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "NVDA,NVIDIA Corp,2026-05-27,2026-04-30,0.88,USD,post-market\n"
            "IBM,International Business Machines,2026-07-20,2026-06-30,2.01,USD,post-market\n"
        )

        events = parse_alpha_vantage_calendar(text, date(2026, 5, 24), {"NVDA"})

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "alpha_vantage_earnings_calendar")
        self.assertEqual(events[0]["confirmed_or_estimated"], "estimated")
        self.assertEqual(events[0]["days_until"], 3)

    def test_nasdaq_calendar_rows_are_estimated_forward_dates(self):
        events = parse_nasdaq_calendar_rows(
            [
                {
                    "symbol": "MRVL",
                    "name": "Marvell Technology, Inc.",
                    "time": "time-after-hours",
                    "fiscalQuarterEnding": "Apr/2026",
                    "lastYearRptDt": "5/29/2025",
                },
                {"symbol": "PDD", "name": "PDD Holdings Inc."},
            ],
            date(2026, 5, 24),
            date(2026, 5, 27),
            {"MRVL"},
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["symbol"], "MRVL")
        self.assertEqual(events[0]["source"], "nasdaq_earnings_calendar")
        self.assertEqual(events[0]["confidence"], 0.7)

    def test_nasdaq_scan_continues_after_temporary_failures(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "earnings": {
                    "providers": {
                        "nasdaq_enabled": True,
                        "nasdaq_lookahead_days": 5,
                        "nasdaq_max_requests": 5,
                        "nasdaq_timeout_seconds": 1,
                        "nasdaq_retries": 1,
                        "alpha_vantage_enabled": False,
                    }
                }
            },
        )
        calls = []

        def fake_fetch(event_date, timeout=5):
            calls.append(event_date)
            if len(calls) <= 2:
                raise TimeoutError("temporary upstream timeout")
            return [{"symbol": "MRVL", "name": "Marvell Technology, Inc.", "fiscalQuarterEnding": "Apr/2026"}]

        with patch("invest.earnings.fetch_nasdaq_earnings_rows", side_effect=fake_fetch):
            events = nasdaq_earnings_events(config, date(2026, 5, 24), {"MRVL"})

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "nasdaq_earnings_calendar")
        self.assertGreaterEqual(len(calls), 3)

    def test_company_ir_feed_parses_confirmed_earnings_date(self):
        feed = """<?xml version="1.0"?>
        <rss><channel><item>
          <title>NVIDIA to Announce Fiscal Results on May 27, 2026</title>
          <link>https://investor.example.com/nvda</link>
          <pubDate>Mon, 20 Apr 2026 12:00:00 GMT</pubDate>
          <description>Company will host an earnings conference call.</description>
        </item></channel></rss>
        """

        items = parse_ir_feed_items(feed)
        event = ir_feed_item_event("NVDA", "https://investor.example.com/rss", items[0], date(2026, 5, 24))

        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "earnings")
        self.assertEqual(event["source"], "company_ir_feed")
        self.assertEqual(event["event_date"], "2026-05-27")
        self.assertEqual(event["confirmed_or_estimated"], "confirmed")

    def test_manual_date_beats_provider_estimate_for_same_symbol_date(self):
        as_of = date(2026, 5, 24)
        events = dedupe_events(
            [
                event_payload(
                    "NVDA",
                    as_of,
                    date(2026, 5, 27),
                    "earnings",
                    "nasdaq_earnings_calendar",
                    "NVDA expected earnings",
                    "estimated",
                    ["earnings"],
                    {},
                ),
                event_payload(
                    "NVDA",
                    as_of,
                    date(2026, 5, 27),
                    "earnings",
                    "manual",
                    "NVDA confirmed earnings",
                    "confirmed",
                    ["earnings"],
                    {},
                ),
            ]
        )

        self.assertEqual(len([row for row in events if row["symbol"] == "NVDA" and row["event_type"] == "earnings"]), 1)
        self.assertEqual(events[0]["source"], "manual")


if __name__ == "__main__":
    unittest.main()
