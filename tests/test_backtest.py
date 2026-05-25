from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from invest.backtest import build_backtest_summary, outcome_history_from_backtest


def price_history(start: date, prices: list[float]) -> list[dict]:
    return [
        {"date": start + timedelta(days=index), "close": price}
        for index, price in enumerate(prices)
    ]


class BacktestTests(unittest.TestCase):
    def test_labels_adds_and_trims_by_forward_return_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            payload = {
                "as_of": "2026-01-02",
                "session": "premarket",
                "recommendation_training_examples": [
                    {
                        "example_id": "add-nvda",
                        "as_of": "2026-01-02",
                        "session": "premarket",
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "trade_action": "add",
                        "recommended_delta_weight": 0.02,
                        "target_weight": 0.12,
                        "risk_adjusted_expected_return": 25,
                        "evidence_quality": 90,
                        "signal_families": ["manager", "catalyst"],
                    },
                    {
                        "example_id": "trim-amd",
                        "as_of": "2026-01-02",
                        "session": "premarket",
                        "symbol": "AMD",
                        "bucket": "semis_networking_hbm",
                        "trade_action": "trim",
                        "recommended_delta_weight": -0.01,
                        "target_weight": 0.02,
                        "risk_adjusted_expected_return": 12,
                        "evidence_quality": 60,
                        "signal_families": ["price_action"],
                    },
                ],
            }
            (reports / "2026-01-02-premarket.json").write_text(json.dumps(payload), encoding="utf-8")

            summary = build_backtest_summary(
                reports,
                as_of=date(2026, 2, 10),
                price_history={
                    "NVDA": price_history(date(2026, 1, 2), [100 + index for index in range(80)]),
                    "AMD": price_history(date(2026, 1, 2), [100 - index for index in range(80)]),
                },
            )

        self.assertEqual(summary["trial_count"], 2)
        self.assertEqual(summary["completed_outcome_count"], 4)
        self.assertEqual(summary["pending_outcome_count"], 6)
        five_day = next(row for row in summary["horizons"] if row["horizon"] == "5d")
        self.assertEqual(five_day["hit_rate"], 1.0)
        one_month = next(row for row in summary["horizons"] if row["horizon"] == "1m")
        self.assertEqual(one_month["hit_rate"], 1.0)
        self.assertGreater(one_month["average_decision_return"], 0)
        history = outcome_history_from_backtest(summary)
        self.assertEqual(len(history), 4)
        self.assertTrue(all(row["forward_return_pct"] > 0 for row in history))

    def test_reconstructs_trials_from_public_action_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            payload = {
                "as_of": "2026-01-02",
                "session": "postmarket",
                "portfolio_benchmark": {
                    "action_queue": [
                        {
                            "symbol": "CRWV",
                            "bucket": "neocloud_datacenters",
                            "trade_action": "add",
                            "recommended_delta_weight": 0.03,
                            "target_weight": 0.08,
                            "risk_adjusted_expected_return": 30,
                        }
                    ]
                },
                "feature_matrix": {
                    "rows": [{"symbol": "CRWV", "signal_families": ["manager"], "event_types": ["contract_win"]}]
                },
            }
            (reports / "2026-01-02-postmarket.json").write_text(json.dumps(payload), encoding="utf-8")

            summary = build_backtest_summary(
                reports,
                as_of=date(2026, 2, 10),
                price_history={"CRWV": price_history(date(2026, 1, 2), [100 + index for index in range(80)])},
        )

        self.assertEqual(summary["trial_count"], 1)
        self.assertEqual(summary["completed_outcome_count"], 2)
        self.assertEqual(summary["outcomes"][0]["symbol"], "CRWV")

    def test_legacy_action_queue_uses_decision_card_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            payload = {
                "as_of": "2026-01-02",
                "session": "postmarket",
                "decision_cards": [
                    {
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "signal_families": ["manager", "catalyst"],
                        "top_event_types": ["capex_signal"],
                    }
                ],
                "portfolio_benchmark": {
                    "action_queue": [
                        {
                            "symbol": "NVDA",
                            "action": "Re-underwrite sizing, hedge need, and falsifier before adding exposure.",
                            "why": "Owned position has a risk catalyst or put-heavy 13F signal.",
                            "portfolio_weight": 0.08,
                            "score": 50,
                        }
                    ]
                },
            }
            (reports / "2026-01-02-postmarket.json").write_text(json.dumps(payload), encoding="utf-8")

            summary = build_backtest_summary(
                reports,
                as_of=date(2026, 1, 20),
                price_history={"NVDA": price_history(date(2026, 1, 2), [100 + index for index in range(20)])},
            )

        five_day = next(row for row in summary["outcomes"] if row["horizon"] == "5d")
        self.assertEqual(five_day["bucket"], "semis_networking_hbm")
        self.assertEqual(five_day["trade_action"], "risk_review")
        self.assertEqual(five_day["current_weight"], 0.08)
        self.assertEqual(five_day["target_weight"], 0.08)
        self.assertEqual(five_day["signal_families"], ["manager", "catalyst"])
        self.assertEqual(five_day["event_types"], ["capex_signal"])


if __name__ == "__main__":
    unittest.main()
