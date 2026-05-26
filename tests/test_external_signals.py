from datetime import date
from decimal import Decimal
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from invest.config import AppConfig
from invest.external_signals import (
    alpha_vantage_news_provider,
    build_external_signal_snapshot,
    external_provider_health_detail,
    gdelt_provider,
    parse_alpha_vantage_news,
    parse_cftc_cot,
    parse_finra_short_interest,
    fetch_sec_company_signals,
    budget_exhausted,
    overall_status,
    provider_timeout,
    parse_eia_signals,
    runtime_budget_seconds,
    sec_company_provider,
    signal_payload,
    tail_provider_reserve_seconds,
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
    def test_provider_health_detail_names_degraded_feeds(self):
        detail = external_provider_health_detail(
            {
                "signal_count": 12,
                "provider_count": 3,
                "source_statuses": [
                    {"label": "SEC company facts", "status": "ok", "detail": "12 parsed."},
                    {"label": "Alpha Vantage news", "status": "limited", "detail": "Optional API key env ALPHA_VANTAGE_API_KEY is not set."},
                    {"label": "FINRA short interest", "status": "limited", "detail": "Runtime budget exhausted."},
                ],
            }
        )

        self.assertIn("12 normalized signals; 1/3 providers ok, 2 limited", detail)
        self.assertIn("Alpha Vantage news: Optional API key env ALPHA_VANTAGE_API_KEY is not set", detail)
        self.assertIn("FINRA short interest: Runtime budget exhausted", detail)

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

    def test_alpha_vantage_news_provider_uses_as_of_bounded_date_window(self):
        urls: list[str] = []

        def fake_urlopen(req, timeout=20):
            urls.append(req.full_url)
            return FakeResponse({"feed": []})

        with patch.dict("os.environ", {"ALPHA_VANTAGE_API_KEY": "demo"}):
            provider = alpha_vantage_news_provider(
                {"alpha_vantage_news_timespan_days": 3},
                date(2026, 5, 23),
                ["NVDA"],
                fake_urlopen,
            )

        self.assertEqual(provider["status"], "limited")
        self.assertIn("time_from=20260521T0000", urls[0])
        self.assertIn("time_to=20260523T2359", urls[0])

    def test_gdelt_provider_caps_repeated_query_failures(self):
        timeouts: list[int] = []

        def timeout_urlopen(req, timeout=20):
            timeouts.append(timeout)
            raise TimeoutError("handshake timed out")

        provider = gdelt_provider(
            {
                "gdelt_queries": ["ai query one", "ai query two", "ai query three"],
                "gdelt_timeout_seconds": 2,
                "gdelt_max_failures": 1,
            },
            date(2026, 5, 24),
            ["NVDA"],
            timeout_urlopen,
        )

        self.assertEqual(timeouts, [2])
        self.assertEqual(provider["status"], "limited")
        self.assertIn("1 query windows failed", provider["detail"])
        self.assertIn("TimeoutError: handshake timed out", provider["detail"])
        self.assertIn("2 remaining query windows skipped after failure cap", provider["detail"])

    def test_gdelt_provider_uses_as_of_bounded_date_window(self):
        urls: list[str] = []

        def fake_urlopen(req, timeout=20):
            urls.append(req.full_url)
            return FakeResponse({"articles": []})

        provider = gdelt_provider(
            {
                "gdelt_queries": ["AI data center"],
                "gdelt_timespan_days": 3,
            },
            date(2026, 5, 23),
            ["NVDA"],
            fake_urlopen,
        )

        self.assertEqual(provider["status"], "limited")
        self.assertIn("startdatetime=20260521000000", urls[0])
        self.assertIn("enddatetime=20260523235959", urls[0])
        self.assertNotIn("timespan=", urls[0])
        self.assertIn("20260521000000..20260523235959", provider["detail"])

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

    def test_sec_company_signals_ignore_filing_data_after_as_of(self):
        submissions = {
            "filings": {
                "recent": {
                    "form": ["10-Q", "4", "8-K", "4"],
                    "filingDate": ["2026-05-25", "2026-05-25", "2026-05-20", "2026-05-19"],
                    "accessionNumber": ["future-q", "future-4", "current-8k", "current-4"],
                    "primaryDocument": ["future.htm", "future4.xml", "current.htm", "current4.xml"],
                }
            }
        }
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {
                                    "val": 500,
                                    "filed": "2026-05-25",
                                    "end": "2026-03-31",
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q1",
                                },
                                {
                                    "val": 220,
                                    "filed": "2026-05-01",
                                    "end": "2026-03-31",
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q1",
                                },
                                {
                                    "val": 100,
                                    "filed": "2025-05-01",
                                    "end": "2025-03-31",
                                    "form": "10-Q",
                                    "fy": 2025,
                                    "fp": "Q1",
                                },
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

        item, signals = fetch_sec_company_signals("NVDA", "0001045810", date(2026, 5, 23), fake_urlopen)
        revenue_signal = next(row for row in signals if row["signal_type"] == "sec_fundamental_trend")

        self.assertEqual(item["latest_result_form"], "8-K")
        self.assertEqual(item["latest_result_filed_at"], "2026-05-20")
        self.assertEqual(item["form4_trailing_45d"], 1)
        self.assertEqual(item["revenue_yoy_pct"], 120.0)
        self.assertEqual(revenue_signal["event_date"], "2026-05-01")

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

    def test_finra_short_interest_parser_ignores_stale_and_undated_rows(self):
        rows = [
            {
                "symbolCode": "NVDA",
                "settlementDate": "2020-04-15",
                "daysToCoverQuantity": "9",
                "shortInterestPercentFloat": "20",
            },
            {
                "symbolCode": "NVDA",
                "daysToCoverQuantity": "4",
                "shortInterestPercentFloat": "10",
            },
        ]

        items, signals = parse_finra_short_interest(rows, {"NVDA"}, date(2026, 5, 24), max_age_days=75)

        self.assertEqual(items, [])
        self.assertEqual(signals, [])

    def test_finra_short_interest_parser_keeps_latest_fresh_settlement(self):
        rows = [
            {
                "symbolCode": "NVDA",
                "settlementDate": "2026-04-15",
                "daysToCoverQuantity": "1",
                "shortInterestPercentFloat": "1",
            },
            {
                "symbolCode": "NVDA",
                "settlementDate": "2026-05-15",
                "daysToCoverQuantity": "5",
                "shortInterestPercentFloat": "12",
            },
        ]

        items, signals = parse_finra_short_interest(rows, {"NVDA"}, date(2026, 5, 24), max_age_days=75)

        self.assertEqual(items[0]["settlement_date"], "2026-05-15")
        self.assertEqual(signals[0]["event_date"], "2026-05-15")
        self.assertLess(signals[0]["score"], -5)

    def test_finra_short_interest_parser_ignores_future_settlements(self):
        rows = [
            {
                "symbolCode": "NVDA",
                "settlementDate": "2026-05-29",
                "daysToCoverQuantity": "9",
                "shortInterestPercentFloat": "20",
            },
            {
                "symbolCode": "NVDA",
                "settlementDate": "2026-05-15",
                "daysToCoverQuantity": "2",
                "shortInterestPercentFloat": "4",
            },
        ]

        items, signals = parse_finra_short_interest(rows, {"NVDA"}, date(2026, 5, 24), max_age_days=75)

        self.assertEqual(items[0]["settlement_date"], "2026-05-15")
        self.assertEqual(signals[0]["event_date"], "2026-05-15")

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

    def test_cftc_cot_parser_ignores_future_reports(self):
        rows = [
            {
                "market_and_exchange_names": "NASDAQ-100 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
                "report_date_as_yyyy_mm_dd": "2026-05-26",
                "open_interest_all": "1000",
                "noncomm_positions_long_all": "900",
                "noncomm_positions_short_all": "100",
            },
            {
                "market_and_exchange_names": "NASDAQ-100 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
                "report_date_as_yyyy_mm_dd": "2026-05-19",
                "open_interest_all": "1000",
                "noncomm_positions_long_all": "600",
                "noncomm_positions_short_all": "200",
            },
        ]

        items, signals = parse_cftc_cot(rows, date(2026, 5, 24))

        self.assertEqual(items[0]["report_date"], "2026-05-19")
        self.assertEqual(signals[0]["event_date"], "2026-05-19")
        self.assertEqual(signals[0]["score"], 9.6)

    def test_cftc_cot_parser_ignores_stale_reports(self):
        rows = [
            {
                "market_and_exchange_names": "NASDAQ-100 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
                "report_date_as_yyyy_mm_dd": "2026-03-01",
                "open_interest_all": "1000",
                "noncomm_positions_long_all": "900",
                "noncomm_positions_short_all": "100",
            },
            {
                "market_and_exchange_names": "NASDAQ-100 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
                "report_date_as_yyyy_mm_dd": "2026-05-19",
                "open_interest_all": "1000",
                "noncomm_positions_long_all": "600",
                "noncomm_positions_short_all": "200",
            },
        ]

        items, signals = parse_cftc_cot(rows, date(2026, 5, 24), max_age_days=45)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["report_date"], "2026-05-19")
        self.assertEqual(signals[0]["event_date"], "2026-05-19")

    def test_eia_parser_ignores_future_periods(self):
        rows = [
            {"period": "2026-06", "generation": "1000"},
            {"period": "2026-05", "generation": "120"},
            {"period": "2026-04", "generation": "100"},
            {"period": "2026-03", "generation": "100"},
            {"period": "2026-02", "generation": "100"},
        ]

        signals = parse_eia_signals(rows, date(2026, 5, 24))

        self.assertEqual(signals[0]["event_date"], "2026-05")
        self.assertLess(signals[0]["score"], 10)

    def test_provider_timeout_is_bounded_for_live_source_checks(self):
        self.assertEqual(provider_timeout({}), 8)
        self.assertEqual(provider_timeout({"timeout_seconds": 1}), 2)
        self.assertEqual(provider_timeout({"timeout_seconds": 99}), 30)
        self.assertEqual(runtime_budget_seconds({}), 30)
        self.assertEqual(runtime_budget_seconds({"max_runtime_seconds": 5}), 10)
        self.assertEqual(tail_provider_reserve_seconds({}), 8)
        self.assertEqual(tail_provider_reserve_seconds({"tail_provider_reserve_seconds": 99}), 60)
        self.assertFalse(budget_exhausted({"_deadline_monotonic": 9999999999.0}))
        self.assertTrue(budget_exhausted({"_deadline_monotonic": 1.0}))
        with patch("invest.external_signals.time.monotonic", return_value=100.0):
            self.assertTrue(budget_exhausted({"_deadline_monotonic": 105.0}, reserve_seconds=8))

    def test_sec_company_provider_preserves_tail_provider_budget(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={"earnings": {"sec_companies": [{"symbol": "NVDA", "cik": "0001045810"}]}},
        )

        def fail_urlopen(*args, **kwargs):
            raise AssertionError("SEC provider should not spend reserved tail-provider budget")

        with patch("invest.external_signals.time.monotonic", return_value=100.0):
            provider = sec_company_provider(
                config,
                {
                    "_deadline_monotonic": 105.0,
                    "tail_provider_reserve_seconds": 8,
                    "sec_company_max_symbols": 1,
                },
                date(2026, 5, 24),
                ["NVDA"],
                fail_urlopen,
            )

        self.assertEqual(provider["status"], "limited")
        self.assertIn("preserve tail-provider runtime budget", provider["detail"])

    def test_external_feed_status_reflects_partial_provider_degradation(self):
        signals = [{"signal_id": "sec-1", "score": 3.0}]

        self.assertEqual(overall_status({"ok": 1, "limited": 5}, signals), "limited")
        self.assertEqual(overall_status({"ok": 2}, signals), "ok")
        self.assertEqual(overall_status({"limited": 2}, []), "limited")
        self.assertEqual(overall_status({"failed": 2}, []), "missing")

    def test_external_snapshot_dedupes_signals_before_features_and_counts(self):
        duplicate = signal_payload(
            "alpha_vantage_news",
            date(2026, 5, 24),
            "news_sentiment",
            5.0,
            "NVDA AI data center guidance",
            "NVDA",
            0.9,
            "duplicate source article",
            "2026-05-24",
            "https://example.com/nvda",
            "news",
        )
        provider = {
            "source": "alpha_vantage_news",
            "label": "Alpha Vantage news sentiment",
            "status": "ok",
            "detail": "two duplicate signals",
            "item_count": 2,
            "signal_count": 2,
            "items": [],
            "signals": [duplicate, dict(duplicate)],
        }
        disabled = {
            "source": "disabled",
            "label": "Disabled",
            "status": "disabled",
            "detail": "disabled",
            "item_count": 0,
            "signal_count": 0,
            "items": [],
            "signals": [],
        }
        config = AppConfig(path=Path("config/invest.toml"), data={})

        with (
            patch("invest.external_signals.alpha_vantage_news_provider", return_value=provider),
            patch("invest.external_signals.gdelt_provider", return_value=disabled),
            patch("invest.external_signals.sec_company_provider", return_value=disabled),
            patch("invest.external_signals.eia_provider", return_value=disabled),
            patch("invest.external_signals.finra_short_interest_provider", return_value=disabled),
            patch("invest.external_signals.cftc_cot_provider", return_value=disabled),
        ):
            snapshot = build_external_signal_snapshot(config, date(2026, 5, 24), ["NVDA"])

        self.assertEqual(snapshot["signal_count"], 1)
        self.assertEqual(snapshot["symbols"], ["NVDA"])
        self.assertEqual(snapshot["symbol_count"], 1)
        self.assertEqual(snapshot["duplicate_signal_count"], 1)
        self.assertEqual(len(snapshot["top_signals"]), 1)
        self.assertEqual(snapshot["by_symbol"]["NVDA"]["signal_count"], 1)
        self.assertEqual(snapshot["by_symbol"]["NVDA"]["external_signal_score"], 5.0)

    def test_external_snapshot_runs_distinct_feeds_before_gdelt_sweep(self):
        calls: list[str] = []

        def provider(source: str):
            def _provider(*args, **kwargs):
                calls.append(source)
                return {
                    "source": source,
                    "label": source,
                    "status": "limited",
                    "detail": "test provider",
                    "item_count": 0,
                    "signal_count": 0,
                    "items": [],
                    "signals": [],
                }

            return _provider

        config = AppConfig(path=Path("config/invest.toml"), data={})

        with (
            patch("invest.external_signals.alpha_vantage_news_provider", provider("alpha_vantage_news")),
            patch("invest.external_signals.sec_company_provider", provider("sec_company_data")),
            patch("invest.external_signals.eia_provider", provider("eia_energy_power")),
            patch("invest.external_signals.finra_short_interest_provider", provider("finra_short_interest")),
            patch("invest.external_signals.cftc_cot_provider", provider("cftc_cot")),
            patch("invest.external_signals.gdelt_provider", provider("gdelt_global_news")),
        ):
            build_external_signal_snapshot(config, date(2026, 5, 24), ["NVDA"])

        self.assertEqual(
            calls,
            [
                "alpha_vantage_news",
                "sec_company_data",
                "eia_energy_power",
                "finra_short_interest",
                "cftc_cot",
                "gdelt_global_news",
            ],
        )

    def test_feature_matrix_includes_external_provider_features(self):
        external = {
            "status": "limited",
            "provider_count": 6,
            "provider_ok_count": 1,
            "provider_ok_ratio": 0.1667,
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
        self.assertEqual(nvda["coverage_adjusted_external_signal_score"], 2.0)
        self.assertEqual(nvda["external_signal_count"], 4)
        self.assertEqual(nvda["signal_family_count"], 2)
        self.assertEqual(nvda["coverage_adjusted_signal_family_count"], 1.25)
        self.assertEqual(nvda["external_provider_count"], 6)
        self.assertEqual(nvda["external_provider_ok_count"], 1)
        self.assertEqual(nvda["external_provider_ok_ratio"], 0.1667)
        self.assertEqual(nvda["external_coverage_multiplier"], 0.25)
        self.assertEqual(nvda["external_feed_status"], "limited")
        self.assertIn("external_feeds", nvda["signal_families"])

    def test_feature_evidence_shrinks_external_boost_when_provider_coverage_is_weak(self):
        base_external = {
            "by_symbol": {
                "NVDA": {
                    "external_signal_score": 20.0,
                    "source_count": 1,
                    "signal_count": 4,
                }
            },
            "global": {},
        }
        card = {
            "symbol": "NVDA",
            "bucket": "semis_networking_hbm",
            "score": 40,
            "signal_family_count": 1,
            "signal_families": ["manager"],
            "source_tiers": ["market_news"],
        }
        common_args = (
            date(2026, 5, 24),
            [card],
            {"by_symbol": [], "by_bucket": []},
            {"focus_managers": []},
            {"regime": "mixed macro tape", "scores": {}},
            {"NVDA": {"5d": Decimal("1"), "1m": Decimal("2"), "3m": Decimal("4")}},
            [],
        )
        full = build_feature_matrix(
            *common_args,
            {**base_external, "status": "ok", "provider_count": 6, "provider_ok_count": 6, "provider_ok_ratio": 1.0},
        )["rows"][0]
        weak = build_feature_matrix(
            *common_args,
            {**base_external, "status": "limited", "provider_count": 6, "provider_ok_count": 1, "provider_ok_ratio": 0.1667},
        )["rows"][0]

        self.assertEqual(full["external_coverage_multiplier"], 1.0)
        self.assertEqual(weak["external_coverage_multiplier"], 0.25)
        self.assertGreater(full["evidence_quality"], weak["evidence_quality"])
        self.assertEqual(full["coverage_adjusted_external_signal_score"], 20.0)
        self.assertEqual(weak["coverage_adjusted_external_signal_score"], 5.0)
        self.assertEqual(full["coverage_adjusted_signal_family_count"], 2.0)
        self.assertEqual(weak["coverage_adjusted_signal_family_count"], 1.25)
        self.assertGreater(full["timing_score"], weak["timing_score"])
        self.assertGreater(full["data_quality"], weak["data_quality"])


if __name__ == "__main__":
    unittest.main()
