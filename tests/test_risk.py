import unittest

from invest.risk import apply_risk_controls


class RiskControlTests(unittest.TestCase):
    def test_caps_adds_by_single_name_and_ticket_limits(self):
        actions = [
            {
                "symbol": "NVDA",
                "trade_action": "add",
                "portfolio_weight": 0.14,
                "recommended_delta_weight": 0.05,
                "target_weight": 0.19,
                "signal_family_count": 3,
            }
        ]
        controlled = apply_risk_controls(
            actions,
            {"by_bucket": [{"bucket": "semis_networking_hbm", "weight": 0.2}]},
            [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "score": 50}],
            limits={"max_single_name_weight": 0.15, "max_one_ticket_delta": 0.03},
        )

        self.assertEqual(controlled[0]["recommended_delta_weight"], 0.01)
        self.assertIn("single_name_cap", controlled[0]["risk_flags"])
        self.assertTrue(controlled[0]["approval_required"])
        self.assertEqual(controlled[0]["order_execution"], "none")

    def test_earnings_blackout_blocks_adds(self):
        actions = [
            {
                "symbol": "GOOGL",
                "trade_action": "add",
                "portfolio_weight": 0.01,
                "recommended_delta_weight": 0.02,
                "target_weight": 0.03,
                "signal_family_count": 3,
            }
        ]
        controlled = apply_risk_controls(
            actions,
            {"by_bucket": [{"bucket": "frontier_ai_platforms", "weight": 0.2}]},
            [{"symbol": "GOOGL", "bucket": "frontier_ai_platforms", "score": 45}],
            earnings_events=[{"symbol": "GOOGL", "days_until": 1}],
            limits={"earnings_blackout_days": 2},
        )

        self.assertEqual(controlled[0]["trade_action"], "watch")
        self.assertEqual(controlled[0]["recommended_delta_weight"], 0.0)
        self.assertIn("earnings_blackout", controlled[0]["risk_flags"])


if __name__ == "__main__":
    unittest.main()
