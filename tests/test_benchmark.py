from decimal import Decimal
import unittest

from invest.reports import build_portfolio_benchmark


class PortfolioBenchmarkTests(unittest.TestCase):
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
        self.assertEqual(benchmark["horizon_returns"][0]["basis"], "current_weight_price_proxy")
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


if __name__ == "__main__":
    unittest.main()
