from datetime import date
import unittest

from invest.engine import build_engine_snapshot, build_learning_state


class EngineTests(unittest.TestCase):
    def test_engine_feature_generation_is_deterministic_and_ranked(self):
        engine = build_engine_snapshot(
            date(2026, 5, 24),
            "premarket",
            [
                {
                    "symbol": "NVDA",
                    "bucket": "semis_networking_hbm",
                    "score": 50,
                    "signal_family_count": 3,
                    "signal_families": ["manager", "catalyst", "price_action"],
                    "consensus_manager_count": 4,
                    "event_score": 6,
                    "five_day_pct": 2,
                },
                {
                    "symbol": "AMD",
                    "bucket": "semis_networking_hbm",
                    "score": 30,
                    "signal_family_count": 2,
                    "signal_families": ["manager", "price_action"],
                    "consensus_manager_count": 2,
                    "event_score": 1,
                    "five_day_pct": 12,
                },
            ],
            {"by_symbol": [{"symbol": "NVDA", "weight": 0.08}]},
            {"exposure_gaps": [{"symbol": "AMD", "peer_avg_weight": 0.04}]},
            [{"ticket_id": "t1", "symbol": "NVDA", "trade_action": "add", "recommended_delta_weight": 0.01, "target_weight": 0.09}],
            {"max_single_name_weight": 0.15},
        )

        self.assertEqual(engine["version"], "2026-05-equity-max-return-v1")
        self.assertEqual(engine["ranked_candidates"][0]["symbol"], "NVDA")
        self.assertEqual(engine["optimizer"]["allocations"][0]["recommended_delta_weight"], 0.01)
        self.assertEqual(engine["live_order_execution"], "disabled")

    def test_learning_falls_back_without_enough_outcomes(self):
        learning = build_learning_state([{"forward_return_pct": 10, "expected_return_score": 40}])

        self.assertEqual(learning["status"], "baseline_fallback")
        self.assertEqual(learning["minimum_required"], 20)


if __name__ == "__main__":
    unittest.main()
