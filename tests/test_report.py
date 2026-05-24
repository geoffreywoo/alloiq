from pathlib import Path
import unittest

from invest.config import AppConfig
from invest.reports import alias_matches, build_weekly_research, render_markdown


class ReportTests(unittest.TestCase):
    def test_report_renders_cited_news_and_research_disclaimer(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={
                    "reports": {"directory": str(tmp)},
                    "vanguard": {"enabled": True},
                    "managers": [{"key": "situational-awareness", "name": "Situational Awareness LP", "primary": True}],
                },
            )
            payload = {
                "as_of": "2026-05-08",
                "session": "premarket",
                "stale_vanguard": {"is_stale": True, "last_import": None},
                "decision_cards": [
                    {
                        "symbol": "BE",
                        "candidate": "research add candidate",
                        "score": 42.0,
                        "bucket": "power_grid_gas_nuclear",
                        "last_price": 10.0,
                        "five_day_pct": 5.0,
                        "filing_value": 875505000.0,
                        "news_count": 2,
                        "counterargument": "Execution risk.",
                        "falsifier": "Demand breaks.",
                    }
                ],
                "transactions": [],
                "latest_filing": {
                    "form": "13F-HR",
                    "accession_number": "0002045724-26-000002",
                    "url": "https://www.sec.gov/example",
                    "filing_date": "2026-02-11",
                    "report_date": "2025-12-31",
                },
                "news": [
                    {
                        "title": "AI power demand headline",
                        "url": "https://example.com/news",
                        "source": "Example",
                        "published_at": "2026-05-08T10:00:00",
                    }
                ],
            }

            md = render_markdown(payload, config)

            self.assertIn("Research only", md)
            self.assertIn("[AI power demand headline](https://example.com/news)", md)
            self.assertIn("Vanguard import status: stale or missing", md)

    def test_report_omits_vanguard_warning_when_disabled(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "vanguard": {"enabled": False},
                "managers": [{"key": "situational-awareness", "name": "Situational Awareness LP", "primary": True}],
            },
        )
        payload = {
            "as_of": "2026-05-08",
            "session": "premarket",
            "stale_vanguard": None,
            "portfolio": {"position_count": 0},
            "macro": {},
            "manager_radar": {},
            "ideas": [],
            "decision_cards": [],
            "transactions": [],
            "latest_filing": None,
            "news": [],
        }

        md = render_markdown(payload, config)

        self.assertNotIn("Vanguard import status", md)
        self.assertIn("Import IBKR Flex positions first", md)

    def test_ticker_alias_matching_avoids_common_false_positives(self):
        self.assertFalse(alias_matches("GOOGLE NEWS MOMENTUM", "GOOGL"))
        self.assertFalse(alias_matches("AI MOMENTUM BUILDS", "MU"))
        self.assertTrue(alias_matches("MICRON RALLIES ON HBM DEMAND", "MICRON"))
        self.assertTrue(alias_matches("COREWEAVE SIGNS AI DEAL", "COREWEAVE"))

    def test_weekly_research_payload_contains_expanded_ideas(self):
        from datetime import date

        weekly = build_weekly_research(
            date(2026, 5, 24),
            [
                {
                    "symbol": "NVDA",
                    "type": "owned add/trim review",
                    "bucket": "semis_networking_hbm",
                    "score": 50,
                    "setup": "Owned leader needs review.",
                    "evidence": "score 50; 3 signal families.",
                    "trigger": "Backlog confirms.",
                    "risk": "Valuation.",
                    "falsifier": "Margins break.",
                    "signal_families": ["manager", "news", "price"],
                }
            ],
            [
                {
                    "symbol": "NVDA",
                    "bucket": "semis_networking_hbm",
                    "score": 50,
                    "signal_families": ["manager", "news", "price"],
                }
            ],
            {
                "action_queue": [
                    {
                        "symbol": "NVDA",
                        "action": "Add +1.0% on confirmation.",
                        "trade_action": "add",
                        "portfolio_weight": 0.05,
                        "recommended_delta_weight": 0.01,
                        "target_weight": 0.06,
                    }
                ]
            },
            {"regime": "mixed macro tape"},
        )

        self.assertEqual(weekly["title"], "Weekly Idea Research")
        self.assertEqual(weekly["ideas"][0]["symbol"], "NVDA")
        self.assertEqual(weekly["ideas"][0]["trade_action"], "add")
        self.assertTrue(weekly["ideas"][0]["research_questions"])


if __name__ == "__main__":
    unittest.main()
