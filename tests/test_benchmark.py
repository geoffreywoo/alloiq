from decimal import Decimal
import unittest

from invest.reports import build_portfolio_benchmark, serialize_return_windows


class PortfolioBenchmarkTests(unittest.TestCase):
    def test_return_window_serialization_skips_non_numeric_metadata(self):
        windows = {
            "NVDA": {"5d": Decimal("4.5"), "as_of": "2026-05-27", "last": Decimal("200")},
            "BAD": {"as_of": "2026-05-27"},
        }

        self.assertEqual(
            serialize_return_windows(windows),
            {"NVDA": {"5d": 4.5, "last": 200.0}},
        )

    def test_benchmark_compares_portfolio_to_market_and_peer_proxies(self):
        portfolio = {
            "by_symbol": [
                {"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.5},
                {"symbol": "AMZN", "bucket": "frontier_ai_platforms", "weight": 0.5},
            ]
        }
        cards = [
            {
                "symbol": "NVDA",
                "bucket": "semis_networking_hbm",
                "score": 50,
                "consensus_manager_count": 4,
                "signal_family_count": 3,
                "put_value": 100_000_000,
                "call_value": 0,
                "top_event_types": [],
            },
            {
                "symbol": "GOOGL",
                "bucket": "frontier_ai_platforms",
                "score": 45,
                "consensus_manager_count": 4,
                "signal_family_count": 2,
                "top_event_types": [],
            },
        ]
        manager_radar = {
            "focus_managers": [
                {
                    "status": "ok",
                    "manager_key": "focus",
                    "manager_name": "Focus Fund",
                    "default_portfolio_overlap_pct": 50,
                    "top_positions": [
                        {"symbol": "NVDA", "fund_weight": 0.2},
                        {"symbol": "GOOGL", "fund_weight": 0.1},
                    ],
                }
            ]
        }
        macro = {
            "scores": {"ai_momentum": 3.0},
            "tape": [
                {"symbol": "SPY", "five_day_pct": 1.0},
                {"symbol": "QQQ", "five_day_pct": 2.0},
                {"symbol": "SMH", "five_day_pct": 4.0},
                {"symbol": "IGV", "five_day_pct": 3.0},
            ],
        }
        prices = {
            "NVDA": {"five_day_pct": Decimal("5")},
            "AMZN": {"five_day_pct": Decimal("-1")},
            "GOOGL": {"five_day_pct": Decimal("2")},
        }
        return_windows = {
            "NVDA": {"5d": Decimal("5"), "1m": Decimal("12"), "3m": Decimal("30")},
            "AMZN": {"5d": Decimal("-1"), "1m": Decimal("4"), "3m": Decimal("9")},
            "GOOGL": {"5d": Decimal("2"), "1m": Decimal("6"), "3m": Decimal("15")},
            "SPY": {"5d": Decimal("1"), "1m": Decimal("2"), "3m": Decimal("6")},
            "QQQ": {"5d": Decimal("2"), "1m": Decimal("3"), "3m": Decimal("8")},
            "SMH": {"5d": Decimal("4"), "1m": Decimal("10"), "3m": Decimal("28")},
            "IGV": {"5d": Decimal("3"), "1m": Decimal("5"), "3m": Decimal("12")},
        }

        benchmark = build_portfolio_benchmark(portfolio, cards, manager_radar, macro, prices, return_windows)

        self.assertEqual(benchmark["portfolio_return_5d"], 2.0)
        self.assertEqual(benchmark["price_coverage_pct"], 100.0)
        self.assertEqual(benchmark["primary_horizon"], "3m")
        self.assertEqual(benchmark["primary_portfolio_return"], 19.5)
        self.assertFalse(benchmark["actual_return_available"])
        self.assertEqual(benchmark["horizon_returns"][0]["basis"], "current_weight_price_proxy_ex_cash")
        self.assertEqual(benchmark["performance_universe"]["portfolio_symbol_count"], 2)
        self.assertEqual(benchmark["performance_universe"]["priced_symbol_count"], 2)
        self.assertEqual([row["symbol"] for row in benchmark["performance_components"]], ["NVDA", "AMZN"])
        self.assertEqual(benchmark["peer_proxies"][0]["proxy_return"], 25.0)
        self.assertEqual(benchmark["benchmarks"][0]["name"], "S&P 500")
        self.assertEqual(benchmark["benchmarks"][0]["portfolio_vs_benchmark"], 13.5)
        self.assertEqual(benchmark["exposure_gaps"][0]["symbol"], "NVDA")
        self.assertEqual(benchmark["exposure_gaps"][0]["type"], "risk_review")
        self.assertTrue(any(row["symbol"] == "GOOGL" for row in benchmark["action_queue"]))
        nvda_action = next(row for row in benchmark["action_queue"] if row["symbol"] == "NVDA")
        self.assertEqual(nvda_action["trade_action"], "trim")
        self.assertEqual(nvda_action["recommended_delta_weight"], -0.03)
        self.assertEqual(nvda_action["post_action_weight"], 0.47)
        googl_action = next(row for row in benchmark["action_queue"] if row["symbol"] == "GOOGL")
        self.assertEqual(googl_action["trade_action"], "add")
        self.assertEqual(googl_action["recommended_delta_weight"], 0.02)
        self.assertEqual(googl_action["target_weight"], 0.02)

    def test_cash_reserve_is_excluded_from_return_comparisons(self):
        portfolio = {
            "cash_weight": 0.2,
            "equity_weight": 0.8,
            "by_symbol": [
                {"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.8},
                {"symbol": "CASH", "bucket": "cash_reserves", "asset_class": "cash", "is_cash": True, "weight": 0.2},
            ],
        }
        return_windows = {
            "NVDA": {"5d": Decimal("10"), "1m": Decimal("20"), "3m": Decimal("30")},
            "SPY": {"5d": Decimal("1"), "1m": Decimal("2"), "3m": Decimal("3")},
        }

        benchmark = build_portfolio_benchmark(portfolio, [], {"focus_managers": []}, {"tape": []}, {}, return_windows)

        self.assertEqual(benchmark["portfolio_return_5d"], 10.0)
        self.assertEqual(benchmark["total_portfolio_return_5d"], 8.0)
        self.assertEqual(benchmark["price_coverage_pct"], 100.0)
        self.assertEqual(benchmark["total_price_coverage_pct"], 100.0)
        self.assertEqual(benchmark["primary_portfolio_return"], 30.0)
        self.assertEqual(benchmark["primary_price_coverage_pct"], 100.0)
        self.assertEqual(benchmark["primary_equity_return"], 30.0)
        self.assertEqual(benchmark["total_horizon_returns"][0]["basis"], "current_weight_price_proxy_total_including_cash")
        self.assertEqual(benchmark["total_horizon_returns"][0]["portfolio_return"], 8.0)
        self.assertEqual(benchmark["equity_horizon_returns"][0]["basis"], "current_weight_price_proxy_ex_cash")
        self.assertEqual(benchmark["equity_horizon_returns"][0]["portfolio_return"], 10.0)
        self.assertEqual(benchmark["return_analytics"]["primary"]["invested_equity_return"], 30.0)
        self.assertEqual(benchmark["return_analytics"]["primary"]["total_portfolio_return"], 24.0)
        self.assertEqual(benchmark["return_analytics"]["primary"]["cash_effect_pct"], -6.0)
        self.assertEqual(benchmark["return_analytics"]["primary"]["ex_cash_uplift_pct"], 6.0)
        self.assertFalse(any(row["symbol"] == "CASH" for row in benchmark["top_detractors"]))

    def test_goog_and_googl_proxy_returns_and_weights(self):
        portfolio = {
            "by_symbol": [
                {"symbol": "GOOG", "bucket": "frontier_ai_platforms", "weight": 0.10},
                {"symbol": "CASH", "bucket": "cash_reserves", "asset_class": "cash", "is_cash": True, "weight": 0.90},
            ]
        }
        cards = [
            {
                "symbol": "GOOGL",
                "bucket": "frontier_ai_platforms",
                "score": 45,
                "consensus_manager_count": 4,
                "signal_family_count": 2,
                "top_event_types": [],
            }
        ]
        manager_radar = {
            "focus_managers": [
                {
                    "status": "ok",
                    "manager_key": "focus",
                    "manager_name": "Focus Fund",
                    "manager_tier": "tier_2",
                    "top_positions": [
                        {"symbol": "GOOGL", "fund_weight": 0.15},
                    ],
                }
            ]
        }
        return_windows = {
            "GOOG": {"5d": Decimal("2"), "1m": Decimal("4"), "3m": Decimal("12")},
            "SPY": {"5d": Decimal("1"), "1m": Decimal("2"), "3m": Decimal("6")},
        }

        benchmark = build_portfolio_benchmark(portfolio, cards, manager_radar, {"tape": []}, {}, return_windows)

        self.assertEqual(benchmark["primary_horizon"], "3m")
        self.assertEqual(benchmark["primary_portfolio_return"], 12.0)
        self.assertEqual(benchmark["peer_proxies"][0]["proxy_return"], 12.0)
        self.assertEqual(benchmark["exposure_gaps"], [])


if __name__ == "__main__":
    unittest.main()
