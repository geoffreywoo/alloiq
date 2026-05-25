from datetime import date
from decimal import Decimal
import json
import unittest

from invest.external_signals import (
    parse_alpha_vantage_news,
    parse_cftc_cot,
    parse_finra_short_interest,
    fetch_sec_company_signals,
    budget_exhausted,
    provider_timeout,
    runtime_budget_seconds,
)
from invest.features import build_feature_matrix


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ExternalSignalTests(unittest.TestCase):
    def test_alpha_vantage_news_sentiment_parses_symbol_scores(self):
        payload = {
            "feed": [
                {
                    "title": "NVIDIA raises AI data center guidance",
                    "url": "https://example.com/nvda",
                    "source": "Example Wire",
                    "time_published": "20260524T130000",
                    "overall_sentiment_score": "0.31",
                    "topics": [{"topic": "Technology"}],
                    "ticker_sentiment": [
                        {"ticker": "NVDA", "relevance_score": "0.92", "ticker_sentiment_score": "0.44"},
                        {"ticker": "MSFT", "relevance_score": "0.10", "ticker_sentiment_score": "0.02"},
                    ],
                }
            ]
        }

        items, signals = parse_alpha_vantage_news(payload, {"NVDA"}, date(2026, 5, 24))

        self.assertEqual(len(items), 1)
        self.assertEqual(signals[0]["symbol"], "NVDA")
        self.assertEqual(signals[0]["source"], "alpha_vantage_news")
        self.assertGreater(signals[0]["score"], 0)

    def test_sec_companyfacts_and_submissions_create_fundamental_and_form4_signals(self):
        submissions = {
            "filings": {
                "recent": {
                    "form": ["4", "10-Q", "8-K"],
                    "filingDate": ["2026-05-20", "2026-05-01", "2026-04-28"],
                    "accessionNumber": ["a1", "a2", "a3"],
                    "primaryDocument": ["form4.xml", "q.htm", "8k.htm"],
                }
            }
        }
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"val": 220, "filed": "2026-05-01", "end": "2026-03-31", "form": "10-Q", "fy": 2026, "fp": "Q1"},
                                {"val": 100, "filed": "2025-05-01", "end": "2025-03-31", "form": "10-Q", "fy": 2025, "fp": "Q1"},
                            ]
                        }
                    }
                }
            }
        }

        def fake_urlopen(req, timeout=20):
            url = req.full_url
            if "submissions" in url:
                return FakeResponse(submissions)
            if "companyfacts" in url:
                return FakeResponse(facts)
            raise AssertionError(url)

        item, signals = fetch_sec_company_signals("NVDA", "0001045810", date(2026, 5, 24), fake_urlopen)
        signal_types = {row["signal_type"] for row in signals}

        self.assertEqual(item["form4_trailing_45d"], 1)
        self.assertEqual(item["latest_result_form"], "10-Q")
        self.assertEqual(item["revenue_yoy_pct"], 120.0)
        self.assertIn("sec_form4_activity", signal_types)
        self.assertIn("sec_fundamental_trend", signal_types)

    def test_finra_short_interest_parser_outputs_risk_score_without_position_counts(self):
        rows = [
            {
                "symbolCode": "NVDA",
                "settlementDate": "2026-05-15",
                "daysToCoverQuantity": "2.5",
                "shortInterestPercentFloat": "3.2",
                "shortInterest": "1000000",
            }
        ]

        items, signals = parse_finra_short_interest(rows, {"NVDA"}, date(2026, 5, 24))

        self.assertEqual(items[0]["symbol"], "NVDA")
        self.assertLess(signals[0]["score"], 0)
        self.assertNotIn("shortInterest", json.dumps(items))
        self.assertNotIn("shortInterest", json.dumps(signals))

    def test_cftc_cot_parser_creates_global_positioning_signal(self):
        rows = [
            {
                "market_and_exchange_names": "NASDAQ-100 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
                "report_date_as_yyyy_mm_dd": "2026-05-19",
                "open_interest_all": "1000",
                "noncomm_positions_long_all": "600",
                "noncomm_positions_short_all": "200",
            }
        ]

        items, signals = parse_cftc_cot(rows, date(2026, 5, 24))

        self.assertEqual(items[0]["label"], "Nasdaq")
        self.assertEqual(signals[0]["scope"], "global")
        self.assertGreater(signals[0]["score"], 0)

    def test_provider_timeout_is_bounded_for_live_source_checks(self):
        self.assertEqual(provider_timeout({}), 8)
        self.assertEqual(provider_timeout({"timeout_seconds": 1}), 2)
        self.assertEqual(provider_timeout({"timeout_seconds": 99}), 30)
        self.assertEqual(runtime_budget_seconds({}), 45)
        self.assertEqual(runtime_budget_seconds({"max_runtime_seconds": 5}), 10)
        self.assertFalse(budget_exhausted({"_deadline_monotonic": 9999999999.0}))
        self.assertTrue(budget_exhausted({"_deadline_monotonic": 1.0}))

    def test_feature_matrix_includes_external_provider_features(self):
        external = {
            "by_symbol": {
                "NVDA": {
                    "external_signal_score": 8.0,
                    "alpha_news_sentiment": 4.0,
                    "sec_fundamental_score": 3.0,
                    "sec_form4_activity_score": 1.0,
                    "gdelt_event_score": 2.0,
                    "short_interest_risk_score": -1.0,
                    "source_count": 3,
                    "signal_count": 4,
                }
            },
            "global": {"global_signal_score": 2.0, "eia_power_pressure_score": 1.5, "cftc_positioning_score": 0.5},
        }

        features = build_feature_matrix(
            date(2026, 5, 24),
            [
                {
                    "symbol": "NVDA",
                    "bucket": "semis_networking_hbm",
                    "score": 40,
                    "signal_family_count": 1,
                    "signal_families": ["manager"],
                    "source_tiers": ["market_news"],
                }
            ],
            {"by_symbol": [], "by_bucket": []},
            {"focus_managers": []},
            {"regime": "mixed macro tape", "scores": {}},
            {"NVDA": {"5d": Decimal("1"), "1m": Decimal("2"), "3m": Decimal("4")}},
            [],
            external,
        )

        nvda = features["rows"][0]
        self.assertEqual(nvda["external_signal_score"], 8.0)
        self.assertEqual(nvda["external_signal_count"], 4)
        self.assertIn("external_feeds", nvda["signal_families"])


if __name__ == "__main__":
    unittest.main()
